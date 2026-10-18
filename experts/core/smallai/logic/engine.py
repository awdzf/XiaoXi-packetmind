"""逻辑层:流水线的第五跳,插在**理解层与校验层之间**。

为什么插在这儿:
    理解层刚把话拆成结构化帧(事实/表达式),校验层正要判定"能不能答"。
    逻辑推理既需要前者的产物,又必须发生在后者之前 —— 这样"推不出来"
    就能像"缺槽位"一样被校验层拦下、被生成层解释,而不是在生成阶段瞎猜。

这一层的产物 LogicResult 有三种结局,都是**正常数据**:
    answer   推出了一个明确结论(真/假/数值关系)
    unknown  依据不足,如实说不知道(而不是默认"假")
    invalid  话没解析成事实/表达式,交给校验层报错
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.context import Context, Intent, Stage
from ..packs import pack_data
from .compare import CompareResult, Quantity, compare_quantities, parse_comparison
from .expr import Fact, to_text
from .inference import read_capabilities, read_chain
from .nlp import (
    _parse_class_question,
    ask_across_predicates,
    parse_conditional,
    parse_fact,
    parse_logic,
    parse_property,
)
from .reasoner import (
    FactStore,
    QueryResult,
    decide,
    default_store,
    forward_chain,
    query as query_fact,
)


@dataclass
class LogicResult:
    """逻辑层的产物。"""

    kind: str = "none"                  # compare / logic / infer / assert / none
    outcome: str = "unknown"            # answer / unknown / invalid
    answer: Any = None                  # True/False / 比较结论 / 事实元组
    text: str = ""                      # 一句话结论(生成层会引用)
    basis: list[str] = field(default_factory=list)   # 依据(逐条,人话)
    store_size: int = 0
    derived: list[str] = field(default_factory=list)  # 本次新推出的事实
    error: str = ""

    @property
    def is_answer(self) -> bool:
        return self.outcome == "answer"

    def described(self) -> str:
        head = f"[{self.kind}/{self.outcome}] {self.text}"
        if self.basis:
            head += " | 依据:" + " / ".join(self.basis)
        return head


def default_kb_path() -> Path:
    """事实库:数据包里有 kb.json 就用它,否则用核心自带的那份。

    于是"逻辑知识"可以按**数据包**换(一个包里放 kb.json 就覆盖全局),
    而推理骨架和词表都不动 —— 这是单核 + 多数据包的一部分。
    """
    return pack_data("kb.json", Path(__file__).resolve().parents[1] / "kb.json")


class LogicEngine:
    """逻辑引擎:事实库 + 前向推理 + 布尔求值 + 比较。

    事实库来源优先级(先找到的胜出):
        1. 显式传入的 store
        2. kb_paths 里第一个存在的文件
        3. 数据包的 kb.json(有就覆盖)/ 核心自带的 kb.json
        4. 纯内置默认库(上面都没有时的兜底)
    """

    name = "logic.engine"

    def __init__(self, store: FactStore | None = None, kb_paths: list[str | Path] | None = None) -> None:
        if store is not None:
            self.store = store
        else:
            candidates: list[str | Path] = list(kb_paths or [])
            candidates.append(default_kb_path())
            self.store = self._load_first(candidates)
        self._merge_structured()
        self._dirty = True
        self.last_derived: list[str] = []

    def _merge_structured(self) -> None:
        """把**当前数据包** pack.json 里的 facts/rules/relations 并进事实库。

        结构化知识仍然是数据包的一部分:拆掉包,事实和规则一起消失;
        包里的 kb.json 先加载,pack.json 的 facts/rules 再叠加(后者可覆盖同名规则)。
        """
        try:
            from ..domain import PACK

            raw = PACK.raw
        except Exception:                              # noqa: BLE001
            return
        if isinstance(raw, dict):
            self.store.extend(raw)

    @staticmethod
    def _load_first(paths: list[str | Path]) -> FactStore:
        for raw in paths:
            path = Path(raw)
            if path.exists():
                return FactStore.load(path)
        return default_store()

    # ---- 事实库维护 ------------------------------------------------------
    def refresh(self) -> int:
        """跑一次前向推理(只在库被改动后才真的算)。返回新增事实数。"""
        if not self._dirty:
            return 0
        # 快照"这一轮之前已有的派生记录",这样 last_derived 只会是**本轮**新增的 ——
        # 否则每次回答都会把整个派生历史倒一遍,又长又没信息。
        before_index = len(self.store.derivations)
        added = forward_chain(self.store)
        self._dirty = False
        fresh = self.store.derivations[before_index:]
        self.last_derived = [d.described() for d in fresh]
        return added

    def learn(self, predicate: str, *args: str) -> bool:
        """学一条事实。学会了就标脏,下次问话会重新推理。"""
        learned = self.store.add_fact(predicate, *args)
        if learned:
            self._dirty = True
        return learned

    def save(self, path: str | Path) -> Path:
        return self.store.save(path)

    # ---- 主入口 ----------------------------------------------------------
    def think(self, ctx: Context) -> LogicResult:
        started = time.perf_counter()
        frame = ctx.frame
        intent = frame.intent if frame else Intent.UNKNOWN

        # 先吃进这句话里断言的事实(『北京属于华北』这种),再推理。
        # 例外:INFER 断言由 _think_infer 负责入库并报告 —— 两边都写会导致
        # 第一次就说"这条我已经知道"(实测踩到)。
        learned: list[str] = []
        if frame is not None and frame.derived_fact and frame.intent is not Intent.INFER:
            predicate, args = frame.derived_fact
            if self.learn(predicate, *args):
                learned.append(f"{Fact(predicate, args)}")

        added = self.refresh()

        try:
            # 条件句优先于意图分派:『如果 P 那么 Q』的可能被路由到 LOGIC 或 INFER,
            # 但它的处理方式只有一种 —— 先看前提能不能站住。
            if parse_conditional(ctx.raw_text) is not None:
                result = self._think_conditional(parse_conditional(ctx.raw_text), ctx)
            elif intent is Intent.COMPARE:
                result = self._think_compare(ctx)
            elif intent is Intent.LOGIC:
                result = self._think_logic(ctx)
            elif intent is Intent.INFER:
                result = self._think_infer(ctx)
            else:
                result = LogicResult(kind="none", outcome="unknown")
        except Exception as exc:                                  # noqa: BLE001
            result = LogicResult(kind="logic", outcome="invalid", error=f"{type(exc).__name__}: {exc}")

        result.store_size = self.store.count()
        if added:
            result.derived = list(self.last_derived)
        if learned:
            result.basis = [f"新学到事实:{'、'.join(learned)}", *result.basis]

        ctx.logic = result
        ctx.record(
            Stage.LOGIC,
            self.name,
            ok=result.outcome != "invalid",
            detail=(
                f"kind={result.kind} outcome={result.outcome} "
                f"facts={result.store_size} derived={added} {result.text or result.error}"
            ),
            elapsed_ms=(time.perf_counter() - started) * 1000,
            basis=result.basis,
        )
        return result

    # ---- 三种推理 --------------------------------------------------------
    def _think_compare(self, ctx: Context) -> LogicResult:
        parsed = parse_comparison(ctx.raw_text)
        if not parsed.ok or parsed.left is None or parsed.right is None:
            return LogicResult(
                kind="compare", outcome="invalid",
                error=parsed.error or "没解析出两个可比较的量",
            )
        outcome = compare_quantities(parsed.left, parsed.right, parsed.relation or "gt")
        if not outcome.ok:
            return LogicResult(kind="compare", outcome="invalid", error=outcome.error)
        symbol = {"gt": ">", "lt": "<", "ge": "≥", "le": "≤", "eq": "=", "ne": "≠"}[outcome.relation or "gt"]
        text = (
            f"{parsed.left.describe()} {symbol} {parsed.right.describe()} "
            f"-> {'成立' if outcome.holds else '不成立'}"
        )
        return LogicResult(
            kind="compare",
            outcome="answer",
            answer=outcome.holds,
            text=text,
            basis=[outcome.basis],
        )

    def _think_logic(self, ctx: Context) -> LogicResult:
        parsed = parse_logic(ctx.raw_text)
        if not parsed.ok or parsed.expr is None:
            return LogicResult(
                kind="logic", outcome="invalid",
                error=parsed.reason or "没解析出可判真假的命题",
                basis=[f"没解析出来的子句:{'、'.join(parsed.unknown_words)}"] if parsed.unknown_words else [],
            )

        ctx.frame.logic_expr = parsed.expr          # 存下来,生成层/追踪都能看
        verdict, detail, unknown = decide(self.store, parsed.expr)
        basis = [f"{d.expr} => {'真' if d.value else '假'}({d.reason})" for d in detail.details]

        if verdict is None:
            unknown_text = "、".join(to_text(f) for f in unknown)
            return LogicResult(
                kind="logic", outcome="unknown",
                answer=None,
                text=f"依据不足:事实库里没有这几个命题 —— {unknown_text}",
                basis=basis,
            )
        return LogicResult(
            kind="logic",
            outcome="answer",
            answer=verdict,
            text=f"{to_text(parsed.expr)} => {'真' if verdict else '假'}",
            basis=basis,
        )

    def _think_infer(self, ctx: Context) -> LogicResult:
        frame = ctx.frame
        # 条件句:如果 P 那么 Q
        conditional = parse_conditional(ctx.raw_text)
        if conditional is not None:
            return self._think_conditional(conditional, ctx)

        target = frame.slot("target") if frame else None
        left = frame.slot("left") if frame else None
        right = frame.slot("right") if frame else None
        why = bool(frame.slot("why")) if frame else False
        single_subject = frame.slot("single_subject") if frame else None
        single_text = frame.slot("single_text") if frame else None
        capability_of = frame.slot("capability_of") if frame else None

        # "苹果能做什么" —— 列举它的能力与性质,并指出每一项从哪个上位类别继承来
        if capability_of:
            return self._answer_capabilities(capability_of)

        # "为什么苹果可以吃" —— 给出推导链(解释),而不是重答一遍
        if why and left and right:
            return self._answer_why(target or "能", left, right)

        # 只有一个参数的归属问句:"北京是城市吗" —— 类别从原句里再抽一次
        if single_subject and single_text:
            parsed = _parse_class_question(single_text, single_subject)
            if parsed is None:
                return LogicResult(kind="infer", outcome="invalid",
                                   error=f"没能从『{single_text}』里看出要问的类别")
            predicate, category = parsed
            return self._answer_membership(predicate, single_subject, category)

        if target and left and right:
            subject, predicate, obj = left, target, right
            return self._answer_membership(predicate, subject, obj)

        if target:
            # 这句是"记一条事实"而不是"问一条事实"
            learned = parse_fact(target) or parse_property(target) or parse_fact(ctx.raw_text)
            if learned is None:
                return LogicResult(kind="infer", outcome="invalid", error=f"没解析出事实:{target}")
            is_new = self.learn(learned[0], *learned[1])
            self.refresh()
            verb = "记下了" if is_new else "这条我已经知道"
            return LogicResult(
                kind="assert", outcome="answer", answer=learned,
                text=f"{verb}:{Fact(learned[0], learned[1])}",
                basis=[f"事实库现有 {self.store.count()} 条事实"],
            )

        return LogicResult(kind="infer", outcome="invalid", error="没解析出要问的事实")

    # ---- 归属查询(两种句式共用一个出口) --------------------------------
    def _answer_membership(self, predicate: str, subject: str, obj: str) -> LogicResult:
        """回答『X <谓词> Y 吗』。跨谓词查询 + 依据链。

        为什么叫"跨谓词":同一个"属于"在中文里跨三种粒度
        (城市->地区 / 地区->大区 / 个体->类别),事实库里分别落在
        城市在 / 地区属于 / 是 上。提问者不该知道内部谓词名。
        """
        result = ask_across_predicates(self.store, predicate, subject, obj)
        basis = [f"查询:{predicate}({subject}, {obj})"]
        basis.extend(result.path)
        if not result.holds:
            basis.append("事实库里没有这条,规则也推不出来")
        return LogicResult(
            kind="infer",
            outcome="answer",
            answer=result.holds,
            text=f"{Fact(predicate, (subject, obj))} => {'成立' if result.holds else '不成立'}",
            basis=basis,
        )

    def _answer_capabilities(self, subject: str) -> LogicResult:
        """『X 能做什么』:列举能力与性质,每项标明从哪个上位类别继承来。

        这一条正是用户要的"进一步推理"的**列举形态**:
            苹果 -> 是水果 -> 水果是食物 -> 食物能吃 => 苹果能吃
        报告里每一项都带 from_category,所以人能核对"它是从哪儿继承的"。
        """
        report = read_capabilities(self.store, subject)
        if report.missing:
            return LogicResult(
                kind="infer", outcome="unknown",
                text=f"我不知道『{subject}』属于什么类别,所以推不出它能做什么",
                basis=[report.note],
            )
        basis = [f"上位类别:{'、'.join(report.categories) or '(无)'}"]
        for link in report.abilities:
            basis.append(f"能 {link.value}  <- 来自『{link.from_category}』")
        for link in report.properties:
            basis.append(f"具有 {link.value}  <- 来自『{link.from_category}』")

        parts: list[str] = []
        if report.abilities:
            parts.append("能做:" + "、".join(f"{link.value}(来自{link.from_category})" for link in report.abilities))
        if report.properties:
            parts.append("具有:" + "、".join(f"{link.value}(来自{link.from_category})" for link in report.properties))
        text = f"『{subject}』" + (";".join(parts) if parts else "我没推出它有什么能力或性质")
        return LogicResult(
            kind="infer",
            outcome="answer" if parts else "unknown",
            answer=None,
            text=text,
            basis=basis,
        )

    def _answer_why(self, predicate: str, subject: str, obj: str) -> LogicResult:
        """『为什么 X 能 Y』:摆出推导链,而不是重答一遍"成立"。"""
        query = ask_across_predicates(self.store, predicate, subject, obj)
        if not query.holds:
            return LogicResult(
                kind="infer", outcome="answer", answer=False,
                text=f"『{subject} {predicate} {obj}』并不成立,所以没有推导链可给",
                basis=["事实库里没有这条,规则也推不出来"],
            )
        fact = query.fact
        links = read_chain(self.store, fact)
        basis = [link.described() for link in links]
        if not basis:
            basis = [f"{Fact(*fact)}(事实库里直接有,没有用到规则)"]
        return LogicResult(
            kind="infer", outcome="answer", answer=True,
            text=f"『{subject} {predicate} {obj}』成立,推导链见下",
            basis=basis,
        )

    def _think_conditional(self, conditional, ctx: Context) -> LogicResult:
        premise_text = conditional.premise_text
        conclusion_text = conditional.conclusion_text
        basis = [f"前提:{premise_text}", f"结论:{conclusion_text}"]

        # 前提取值:可能是个事实,也可能本身就是个布尔式
        premise_fact = parse_fact(premise_text)
        premise_holds: bool | None = None
        if premise_fact is not None:
            holds = self.store.has(premise_fact[0], *premise_fact[1])
            premise_holds = holds
            basis.append(f"前提 {Fact(premise_fact[0], premise_fact[1])} => {'真' if holds else '假'}")
        else:
            parsed = parse_logic(premise_text)
            if parsed.ok and parsed.expr is not None:
                verdict, detail, unknown = decide(self.store, parsed.expr)
                premise_holds = verdict
                basis.extend(f"{d.expr} => {'真' if d.value else '假'}" for d in detail.details)
                if unknown:
                    basis.append("未知:" + "、".join(to_text(f) for f in unknown))

        if premise_holds is None:
            return LogicResult(kind="logic", outcome="unknown",
                               text=f"前提我判断不了:{premise_text}", basis=basis)
        if premise_holds is False:
            return LogicResult(
                kind="logic", outcome="answer", answer=None,
                text=f"前提不成立({premise_text}),所以不能推出「{conclusion_text}」",
                basis=basis,
            )
        if conditional.conclusion is None:
            return LogicResult(kind="logic", outcome="invalid",
                               error=f"结论没解析成事实:{conclusion_text}", basis=basis)

        # 前提成立:先把结论记下来(这是"条件推理"的实质动作),再报告
        is_new = self.learn(conditional.conclusion[0], *conditional.conclusion[1])
        self.refresh()
        conclusion = Fact(conditional.conclusion[0], conditional.conclusion[1])
        return LogicResult(
            kind="infer",
            outcome="answer",
            answer=conditional.conclusion,
            text=(f"前提成立,因此「{conclusion}」"
                  + ("(已加入事实库)" if is_new else "(此前已在事实库)")),
            basis=basis,
        )

    # ---- 便捷查询(给 CLI / 测试用) ------------------------------------
    def ask_fact(self, subject: str, predicate: str, obj: str) -> QueryResult:
        self.refresh()
        return query_fact(self.store, predicate, subject, obj)


__all__ = ["LogicEngine", "LogicResult", "FactStore", "Quantity", "CompareResult"]

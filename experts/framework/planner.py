"""有界规划器:只做"最多几步、每步可核对"的结构化推理。

它不是通用推理机,只覆盖三类问题:

    · 归属/能力:**香蕉是水果吗 / 苹果可以吃吗 / 香蕉需要冷藏吗**
      —— 交给核心的事实库 + 前向推理(含继承规则),把推导链带回来;
    · 关系:**A 和 B 是什么关系 / 有什么区别**
      —— 只查包里显式写下的 facts / relations,不猜;
    · 属性:**香蕉的储存方式是什么**
      —— 只查包里显式写下的 `谓词(主体, 值)`。

三条纪律:

    1. **有界**:最多展示 4 步推导;没有依据就返回 None,交回普通检索;
    2. **不生成**:所有结论都来自事实/规则/关系,文字只是把结构翻成人话;
    3. **可拆装**:只用当前数据包里的结构化知识;包删了,推理能力一起消失。

它不直接依赖核心的 Python 对象:通过 `ask(card, question)` 回调拿核心的 JSON,
这样单进程 / 子进程两种模式都能用。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from .catalog import ExpertCard
from .datalog import DatalogEngine, QueryResult

MAX_STEPS = 4

#: "X 是 Y 吗 / X 可以 Y 吗 / X 需要 Y 吗"（排除"是什么"这种定义题）
_MEMBERSHIP_RE = re.compile(
    r"^(?!.*(?:是什么|是啥|什么意思|什么叫|什么是|为什么|怎么|如何))"
    r"(?P<subject>.{1,24}?)"
    r"(?P<predicate>属于|是否属于|是|是否|能不能|能|可以|会|需要)"
    r"(?P<object>.{1,24}?)(?:吗|么|呢)?[?？。]?$"
)
#: "A 和 B 是什么关系 / 有什么区别"
_RELATION_RE = re.compile(
    r"^(?P<a>.{1,20}?)(?:和|与|跟)(?P<b>.{1,20}?)(?:的)?"
    r"(?:是什么|有哪些|是啥)?"
    r"(?P<kind>关系|区别|不同|联系|异同)"
    r"(?:是什么|有哪些|在哪)?[?？。]?$"
)
#: "X 的 Y 是什么 / 是多少 / 有哪些"
_ATTRIBUTE_RE = re.compile(
    r"^(?P<subject>.{1,24}?)的(?P<attribute>.{1,16}?)"
    r"(?:是什么|是多少|有哪些|是啥|怎么|如何)[?？。]?$"
)
#: 事实文本 `谓词(参数, 参数)` 或带空格
_FACT_TEXT_RE = re.compile(r"^(?P<predicate>[^()\s]+)\s*\((?P<args>[^()]*)\)$")


@dataclass(frozen=True)
class PlanResult:
    """一次有界推理的结果：结论文字 + 依据 + 来自哪个包。"""

    folder: str
    text: str
    steps: tuple[str, ...]
    kind: str


def _clean(text: str) -> str:
    return re.sub(r"[\s\u3000]+", "", (text or "").strip())


def _strip_article(text: str) -> str:
    for suffix in ("的", "了"):
        if text.endswith(suffix) and len(text) > len(suffix):
            text = text[:-len(suffix)]
    return text.strip()


def _fact_text(predicate: str, args: list[str] | tuple[str, ...]) -> str:
    """把内部事实翻成人话；不认识的谓词就原样列出，不硬编。"""
    values = [str(arg) for arg in args if str(arg).strip()]
    if not values:
        return predicate
    if predicate == "是" and len(values) == 2:
        return f"{values[0]}是{values[1]}"
    if predicate in ("属于", "地区属于") and len(values) == 2:
        return f"{values[0]}属于{values[1]}"
    if predicate == "城市在" and len(values) == 2:
        return f"{values[0]}位于{values[1]}"
    if predicate in ("能", "可以") and len(values) == 2:
        return f"{values[0]}可以{values[1]}"
    if predicate == "具有" and len(values) == 2:
        return f"{values[0]}具有{values[1]}"
    if predicate == "需要" and len(values) == 2:
        return f"{values[0]}需要{values[1]}"
    if len(values) == 1:
        return f"{values[0]}满足「{predicate}」"
    return f"{values[0]} 的「{predicate}」是 " + "、".join(values[1:])


def _human_fact(text: str) -> str:
    """`能(苹果, 吃)` -> `苹果可以吃`；解析失败原样返回。"""
    cleaned = (text or "").strip().rstrip("。")
    match = _FACT_TEXT_RE.match(cleaned)
    if not match:
        return cleaned
    predicate = match.group("predicate").strip()
    args = [part.strip() for part in re.split(r"[,，、]", match.group("args")) if part.strip()]
    return _fact_text(predicate, args)


def _split_facts(text: str) -> list[str]:
    """按顶层 `、` 拆多个事实；事实参数里的 `、` 不拆。"""
    parts: list[str] = []
    buffer = ""
    depth = 0
    for char in text:
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        if char in "、,，" and depth == 0:
            if buffer.strip():
                parts.append(buffer.strip())
            buffer = ""
        else:
            buffer += char
    if buffer.strip():
        parts.append(buffer.strip())
    return parts


def _proof_steps(basis: list[str], limit: int = MAX_STEPS) -> list[str]:
    """把核心的推导账本变成人话步骤（去重、截断）。"""
    steps: list[str] = []
    for line in basis:
        line = str(line).strip()
        if not line or line.startswith(("查询:", "事实库里没有", "新学到事实")):
            continue
        if "<-" in line:
            parts = line.split("<-")
            if len(parts) >= 3:
                conclusion = _human_fact(parts[0])
                rule = parts[1].strip().strip("() ")
                premises = [
                    _human_fact(item)
                    for item in _split_facts(parts[2].strip())
                    if item.strip()
                ]
                step = f"由「{'、'.join(premises)}」按规则「{rule}」推出「{conclusion}」"
            else:
                step = _human_fact(line)
        else:
            step = _human_fact(line)
        if step and step not in steps:
            steps.append(step)
    if len(steps) > limit:
        hidden = len(steps) - limit
        steps = steps[:limit] + [f"……还有 {hidden} 步推导已省略"]
    return steps


class BoundedPlanner:
    """结构化问题 → 有界推理。没有结构化知识时什么都不做。"""

    def __init__(self, cards: tuple[ExpertCard, ...] = ()) -> None:
        self.cards = tuple(cards)
        # 每个包的 Datalog 子集只在装配时读一次；包删了，能力一起消失
        self.datalog: dict[str, DatalogEngine] = {}
        for card in self.cards:
            spec = getattr(card, "datalog", None)
            if not spec:
                continue
            try:
                self.datalog[card.folder] = DatalogEngine.from_spec(spec)
            except (TypeError, ValueError) as exc:
                self.datalog[card.folder] = DatalogEngine()
                self.datalog[card.folder].errors.append(f"datalog 读取失败:{exc}")

    # ---- 对外 ------------------------------------------------------------
    def plan(
        self,
        text: str,
        ask: Callable[[ExpertCard, str], dict[str, Any] | None],
    ) -> PlanResult | None:
        cleaned = _clean(text)
        if not cleaned:
            return None

        relation = _RELATION_RE.match(cleaned)
        if relation:
            return self._plan_relation(relation, ask)

        attribute = _ATTRIBUTE_RE.match(cleaned)
        if attribute:
            return self._plan_attribute(attribute)

        membership = _MEMBERSHIP_RE.match(cleaned)
        if membership:
            return self._plan_membership(membership, ask)
        return None

    # ---- 归属 / 能力 -----------------------------------------------------
    def _plan_membership(self, match: re.Match[str],
                         ask: Callable[[ExpertCard, str], dict[str, Any] | None]) -> PlanResult | None:
        subject = _strip_article(match.group("subject"))
        obj = _strip_article(match.group("object"))
        if not subject or not obj:
            return None

        # 先试 Datalog：递归的“祖先/可达/传递闭包”是 facts/rules 表达不了的。
        # 只认真正推出的事实；推不出就落回核心事实库（开放世界，不判假）。
        derived = self._plan_datalog_membership(subject, obj, match.group("predicate"))
        if derived is not None:
            return derived

        # 主体必须出现在这个包自己的结构化知识里。只按宾语匹配会让
        # "香蕉是水果吗" 借到别的包里"水果"的事实,再用核心内置事实作答 ——
        # 结论可能对,但来源是假的,所以这里要求主体有本地依据。
        for card in self._cards_with_subject(subject):
            payload = ask(card, match.string)
            logic = (payload or {}).get("logic") or {}
            if logic.get("outcome") != "answer" or logic.get("answer") is not True:
                # 核心说"不成立"往往只是事实库里没有这条（开放世界），
                # 不能当成"假"回答给用户；交给普通检索/澄清。
                continue
            steps = _proof_steps(list(logic.get("basis") or ()))
            if not steps:
                steps = [f"数据包 {card.folder} 里有这条事实(见 pack.json 的 facts/relations)"]
            conclusion = _human_fact(str(logic.get("text") or "").split("=>")[0])
            lines = [f"结论:{conclusion}。"]
            if steps:
                lines.append("依据:")
                lines.extend("  · " + step for step in steps)
            else:
                lines.append("依据:数据包里的事实与规则。")
            lines.append("")
            lines.append(
                f"—— 来自数据包 {card.folder} 的结构化知识(含核心继承规则);"
                "离线快照,未联网核对最新版本"
            )
            return PlanResult(
                folder=card.folder,
                text="\n".join(lines),
                steps=tuple(steps),
                kind="membership",
            )
        return None

    def _plan_datalog_membership(
        self, subject: str, obj: str, word: str
    ) -> PlanResult | None:
        word = word or ""
        if any(token in word for token in ("能", "可以", "会")):
            predicates = ("可以", "能", "会")
        elif "需要" in word:
            predicates = ("需要",)
        elif any(token in word for token in ("属于", "是", "是否")):
            predicates = ("是", "属于")
        else:
            return None

        for card in self.cards:
            engine = self.datalog.get(card.folder)
            if engine is None:
                continue
            for predicate in predicates:
                result = engine.query_first([predicate, subject, obj])
                if result is None:
                    continue
                steps = engine.explain(result.proof)
                conclusion = _fact_text(predicate, (subject, obj))
                lines = [f"结论:{conclusion}。"]
                if steps:
                    lines.append("依据:")
                    lines.extend("  · " + step for step in steps)
                lines.append("")
                lines.append(
                    f"—— 来自数据包 {card.folder} 的 Datalog 规则"
                    "(递归/传递闭包);离线快照,未联网核对最新版本"
                )
                return PlanResult(
                    folder=card.folder,
                    text="\n".join(lines),
                    steps=tuple(steps),
                    kind="datalog-membership",
                )
        return None

    # ---- 关系 ------------------------------------------------------------
    def _plan_relation(self, match: re.Match[str],
                       ask: Callable[[ExpertCard, str], dict[str, Any] | None]) -> PlanResult | None:
        left = _strip_article(match.group("a"))
        right = _strip_article(match.group("b"))
        if not left or not right or left == right:
            return None

        lines: list[str] = []
        folders: list[str] = []
        for card in self.cards:
            added_here = False
            for relation, subject, obj in self._relations(card):
                if _same(subject, left) and _same(obj, right):
                    lines.append(_fact_text(relation, (left, right)))
                    added_here = True
                elif _same(subject, right) and _same(obj, left):
                    lines.append(_fact_text(relation, (right, left)))
                    added_here = True
            # 直接事实:是/属于
            for predicate, args in self._facts(card):
                if len(args) != 2:
                    continue
                if _same(args[0], left) and _same(args[1], right):
                    lines.append(_fact_text(predicate, args))
                    added_here = True
                elif _same(args[0], right) and _same(args[1], left):
                    lines.append(_fact_text(predicate, args))
                    added_here = True
            # 共同上位类别: 是(A,C) 且 是(B,C)
            left_parents = {args[1] for predicate, args in self._facts(card)
                            if predicate == "是" and len(args) == 2 and _same(args[0], left)}
            right_parents = {args[1] for predicate, args in self._facts(card)
                             if predicate == "是" and len(args) == 2 and _same(args[0], right)}
            for parent in sorted(left_parents & right_parents):
                lines.append(f"{left} 和 {right} 都属于 {parent}")
                added_here = True
            if added_here and card.folder not in folders:
                folders.append(card.folder)

        # 属性对比:只列同一个谓词下两边都有值的事实,不硬编"区别"
        for card in self.cards:
            added_here = False
            common: dict[str, list[str]] = {}
            for predicate, args in self._facts(card):
                if len(args) != 2:
                    continue
                if _same(args[0], left):
                    common.setdefault(predicate, ["", ""])[0] = args[1]
                elif _same(args[0], right):
                    common.setdefault(predicate, ["", ""])[1] = args[1]
            for predicate, (left_value, right_value) in common.items():
                if predicate in ("是", "属于", "地区属于", "城市在"):
                    continue
                if left_value and right_value:
                    lines.append(
                        f"{_fact_text(predicate, (left, left_value))};"
                        f"{_fact_text(predicate, (right, right_value))}"
                    )
                    added_here = True
            if added_here and card.folder not in folders:
                folders.append(card.folder)

        # Datalog：如果显式关系没写全，让递归规则把关系推出来（祖先/可达等）
        for card in self.cards:
            engine = self.datalog.get(card.folder)
            if engine is None:
                continue
            engine.derive()
            added_here = False
            for predicate, bucket in engine.facts.items():
                if not bucket or len(next(iter(bucket))) != 2:
                    continue
                for subject, obj in bucket:
                    if _same(subject, left) and _same(obj, right):
                        line = f"{left} 和 {right} 满足「{predicate}」"
                    elif _same(subject, right) and _same(obj, left):
                        line = f"{right} 和 {left} 满足「{predicate}」"
                    else:
                        continue
                    lines.append(line)
                    added_here = True
            if added_here and card.folder not in folders:
                folders.append(card.folder)

        lines = list(dict.fromkeys(lines))
        if not lines:
            return None
        shown = lines[:MAX_STEPS]
        if len(lines) > len(shown):
            shown.append(f"……还有 {len(lines) - len(shown)} 条关系已省略")
        text = "从现有结构化知识看:\n" + "\n".join("  · " + line for line in shown)
        text += "\n\n—— 来自数据包 " + "、".join(folders or ["(未知)"]) + \
                " 的事实/关系;离线快照,未联网核对最新版本"
        return PlanResult(folder="、".join(folders or ["(未知)"]),
                          text=text, steps=tuple(shown), kind="relation")

    # ---- 属性 ------------------------------------------------------------
    def _plan_attribute(self, match: re.Match[str]) -> PlanResult | None:
        subject = _strip_article(match.group("subject"))
        attribute = _strip_article(match.group("attribute"))
        if not subject or not attribute:
            return None

        lines: list[str] = []
        folders: list[str] = []
        for card in self.cards:
            added_here = False
            for predicate, args in self._facts(card):
                if len(args) < 2 or not _same(args[0], subject):
                    continue
                # "储存" 命中 "储存方式";反向也可以
                if not (predicate in attribute or attribute in predicate):
                    continue
                values = "、".join(args[1:])
                lines.append(f"{subject} 的「{predicate}」是 {values}")
                added_here = True
            if added_here and card.folder not in folders:
                folders.append(card.folder)

        # Datalog：属性也可以是推导出来的（例如“祖先链上的某条”）
        for card in self.cards:
            engine = self.datalog.get(card.folder)
            if engine is None:
                continue
            engine.derive()
            added_here = False
            for predicate, bucket in engine.facts.items():
                if not (predicate in attribute or attribute in predicate):
                    continue
                if not bucket or len(next(iter(bucket))) != 2:
                    continue
                for subject_value, value in bucket:
                    if _same(subject_value, subject):
                        lines.append(f"{subject} 的「{predicate}」是 {value}")
                        added_here = True
            if added_here and card.folder not in folders:
                folders.append(card.folder)

        lines = list(dict.fromkeys(lines))
        if not lines:
            return None
        shown = lines[:MAX_STEPS]
        text = "从现有结构化知识看:\n" + "\n".join("  · " + line for line in shown)
        text += "\n\n—— 来自数据包 " + "、".join(folders or ["(未知)"]) + \
                " 的事实;离线快照,未联网核对最新版本"
        return PlanResult(folder="、".join(folders or ["(未知)"]),
                          text=text, steps=tuple(shown), kind="attribute")

    # ---- 数据 ------------------------------------------------------------
    def _cards_with_subject(self, subject: str) -> list[ExpertCard]:
        """只挑"主体真的写在这个包的结构化知识里"的包（来源可信）。"""
        out: list[ExpertCard] = []
        for card in self.cards:
            subjects = {args[0] for _predicate, args in self._facts(card) if args}
            subjects.update({head for _rel, head, _obj in self._relations(card)})
            if _mentioned(subject, subjects):
                out.append(card)
        return out

    @staticmethod
    def _facts(card: ExpertCard) -> list[tuple[str, tuple[str, ...]]]:
        out: list[tuple[str, tuple[str, ...]]] = []
        for item in card.facts:
            predicate = str(item.get("predicate") or "").strip()
            args = item.get("args") or ()
            if predicate and isinstance(args, (list, tuple)) and args:
                out.append((predicate, tuple(str(arg) for arg in args)))
        for item in card.relations:
            predicate = str(item.get("type") or item.get("predicate") or "").strip()
            subject = str(item.get("from") or item.get("subject") or "").strip()
            obj = str(item.get("to") or item.get("object") or "").strip()
            if predicate and subject and obj:
                out.append((predicate, (subject, obj)))
        return out

    @staticmethod
    def _relations(card: ExpertCard) -> list[tuple[str, str, str]]:
        out: list[tuple[str, str, str]] = []
        for item in card.relations:
            predicate = str(item.get("type") or item.get("predicate") or "").strip()
            subject = str(item.get("from") or item.get("subject") or "").strip()
            obj = str(item.get("to") or item.get("object") or "").strip()
            if predicate and subject and obj:
                out.append((predicate, subject, obj))
        return out


def _same(left: str, right: str) -> bool:
    return bool(left) and bool(right) and (left == right or left in right or right in left)


def _mentioned(term: str, names: set[str]) -> bool:
    if not term:
        return False
    return any(term == name or term in name or name in term for name in names if name)


__all__ = ["MAX_STEPS", "PlanResult", "BoundedPlanner"]

"""GoChat 管线:安全闸 → 分解 → 查找数据包 → 取答案 → 总结。

分工与边界:
  · **分解**(decomposer):切词 + 造候选(排列 / 谐音 / 错字);
  · **查找**(router):候选逐条尝试,哪个包里有对上的就查哪个(可多个);
  · **知识**:**同一份核心代码**(`experts/core/smallai/`)+ 各自的数据包。
    默认走**单进程**(`corebridge.py`):核心只 import 一次,每个包按需装配流水线并
    缓存,文件没变就复用;单进程失败才退回子进程(`--subprocess` 可强制旧路径);
  · **总结**(summarizer):只引用答案原句,点明数据包与条目 id,不生成新知识。

人格在这条链路之前:问"你是谁 / 你能做什么"由 `persona.py` 直接答,
不查任何数据包(删光数据包,小析依然知道自己是谁)。

知识是**可插拔**的:
  · 目录自动发现 `experts/packs/*/pack.json`,代码里没有名单;
  · 移出某个数据包 → 少一块知识,框架照常启动;
  · `general` 角色的包是兜底:只有领域包一个都没命中时才轮到它;
  · 安全底线由 framework/safety.py 自带(不依赖 ethics 包是否还在),
    若 ethics 包在,则它的细则与框架底线合并判定。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Callable

from . import budget as budget_mod
from . import conflicts
from . import corebridge
from . import lessons
from . import safety as safety_rules
from . import settings as settings_mod
from .trust import TrustMap
from .catalog import (
    CATALOG,
    CORE_RUN,
    ExpertCard,
    card_for,
    discover,
    general_card,
    persona,
)
from .decomposer import Decomposer, Decomposition
from .memory import ConversationMemory, is_followup
from .planner import MAX_STEPS as MAX_PLAN_STEPS, BoundedPlanner, PlanResult
from .relations import PackGraph
from .router import RouteTarget, Router, Routing
from .summarizer import ExpertAnswer, summarize

GOOD_INTENTS = frozenset({
    "domain", "help", "greeting", "calc", "convert", "compare", "logic",
    "infer", "semantic", "time", "weather",
})

# 危机场景:先分清"是用户自己"还是"用户在说别人"。
# 说错对象的代价很大 —— 对正在难受的人念"怎么帮助当事人"的操作手册,既没接住人,
# 也把求助变成了任务。这里只做非常粗的文本判断,判不清时按"身边人"处理。
_SELF_CRISIS = re.compile(
    r"(?:我|自己|本人)"
    r"(?:最近|现在|真的|有点|总是|一直|也|快|已经|今天|这两天|想|好想)?"
    r"(?:不想活|活不下去|撑不下去|想死|自杀|自残)"
)
_OTHER_CRISIS = re.compile(
    r"(?:朋友|家人|亲人|同事|同学|孩子|父母|身边|别人|有人|他|她)"
    r".{0,8}(?:不想活|活不下去|撑不下去|想死|自杀|自残|轻生)"
)
_NAKED_CRISIS = re.compile(r"(?:不想活|活不下去|撑不下去)")


@dataclass
class ChatResult:
    user_text: str
    decomposition: Decomposition
    routing: Routing
    answers: list[ExpertAnswer]
    summary: str
    safety: dict = field(default_factory=dict)
    elapsed_ms: float = 0.0
    budget_note: str = ""

    @property
    def answered(self) -> list[ExpertAnswer]:
        return [a for a in self.answers if a.ok]


class GoChat:
    """一次完整问答(也是 GoChat.py 背后的对象)。

    三个可调的东西:

      · `max_packs` —— 一次最多查几个数据包(默认取 config.json,命令行可覆盖);
      · `memory`    —— 对话记忆(记住上一轮的关键词,接着问时再用一次);
      · `context`   —— 想整个关掉记忆时传 False。
    """

    def __init__(
        self,
        cards: tuple[ExpertCard, ...] | None = None,
        memory: ConversationMemory | None = None,
        max_packs: int | None = None,
        context: bool | None = None,
        config: settings_mod.Settings | None = None,
        packs_root=None,
        rehit_rounds: int | None = None,
        engine: str = "inprocess",
    ) -> None:
        self.packs_root = packs_root          # 测试用:从别处发现数据包(教完才 reload)
        if cards is None:
            self.cards = tuple(discover(packs_root)) if packs_root else CATALOG
        else:
            self.cards = tuple(cards)
        self.config = config or settings_mod.load()
        self.max_packs = (
            settings_mod.clamp_max_packs(max_packs)
            if max_packs is not None else self.config.max_packs
        )
        self.rehit_rounds = (
            settings_mod.clamp_rehit_rounds(rehit_rounds)
            if rehit_rounds is not None else self.config.rehit_rounds
        )
        self.context_enabled = self.config.context_enabled if context is None else bool(context)
        self.memory = memory if memory is not None else ConversationMemory(
            max_turns=self.config.max_turns,
            max_keywords=self.config.max_keywords,
        )
        self.trust = TrustMap.from_settings(self.config.trust)
        self.decomposer = Decomposer(self.cards)
        self.router = Router(
            self.cards,
            retrieval=self.config.retrieval,
            trust=self.trust,
        )
        self.planner = BoundedPlanner(self.cards)
        #: "inprocess"(默认:核心常驻,按包缓存流水线)或 "subprocess"(旧路径,隔离优先)
        self.engine = "subprocess" if str(engine).lower().startswith("sub") else "inprocess"
        #: 单进程回退到子进程时留下的说明(不静默;`:stat` 会显示)
        self.engine_notes: list[str] = []
        self.last_elapsed_ms: float = 0.0

    # ---- 对外 ------------------------------------------------------------
    def ask(
        self,
        text: str,
        detail: bool = True,
        trace: bool = False,
        plain: bool = True,
        ask_user: Callable[[str], str] | None = None,
    ) -> ChatResult:
        started = time.perf_counter()
        result = self._ask(text, detail=detail, trace=trace, plain=plain, ask_user=ask_user)
        result.elapsed_ms = (time.perf_counter() - started) * 1000
        self.last_elapsed_ms = result.elapsed_ms
        return result

    def _ask(
        self,
        text: str,
        detail: bool = True,
        trace: bool = False,
        plain: bool = True,
        ask_user: Callable[[str], str] | None = None,
    ) -> ChatResult:
        text = (text or "").strip()
        safety = self.guard(text)

        if safety.get("level") == "refuse":
            ethics = self._card("ethics")
            answer = self._ask_expert_card(ethics, text) if ethics else None
            if answer is None or not answer.text.strip():
                advice = " ".join(str(a) for a in safety.get("advice") or ())
                text_out = (
                    persona.refusal_head(str(safety.get("labels") or "命中安全红线"))
                    + "\n" + advice
                ).strip()
                answer = ExpertAnswer(
                    folder="ethics", name=persona.source("ethics"), question=text, text=text_out,
                    source="refusal", safety_level="refuse", ok=False,
                    attempts=(text,), role="domain",
                )
            answer.attempts = (text,)
            routing = Routing(targets=(), tried=0, notes="安全闸拒答,未进入查找")
            decomposition = Decomposition(original=text, tokens=(),
                                          candidates=(), notes=("安全闸拒答",))
            summary = summarize(text, decomposition, routing, [answer], safety,
                                detail=detail, trace=trace, plain=plain)
            return ChatResult(text, decomposition, routing, [answer], summary, safety)

        # 人格自述:问"你是谁 / 你能做什么"由人格直接答,不查数据包。
        # 放在安全闸之后:该拒的先拒;放在分解之前:这两句没有可查的关键词。
        self_intro = persona.answers(
            text,
            packs=tuple(card.name for card in self.cards),
            brief=plain and not trace,
        )
        if self_intro:
            decomposition = Decomposition(
                original=text, tokens=(), candidates=(),
                notes=("人格自述:不查数据包",),
            )
            routing = Routing(targets=(), tried=0, notes="人格自述,不查数据包")
            answer = ExpertAnswer(
                folder=persona.ROLE, name=persona.NAME, question=text, text=self_intro,
                intent="help", source="persona", ok=True, role=persona.ROLE,
            )
            summary = summarize(text, decomposition, routing, [answer], safety,
                                detail=detail, trace=trace, plain=plain)
            return ChatResult(text, decomposition, routing, [answer], summary, safety)

        # 有界规划器:结构化知识(事实/规则/关系)能给出可核对结论时,直接走它。
        # 它只覆盖"归属/能力/关系/属性"几类问题;拿不准就返回 None,落回普通检索。
        plan = self._plan(text)
        if plan is not None:
            decomposition = self.decomposer.decompose(text)
            routing = Routing(
                targets=(), tried=0,
                notes=f"有界规划器:{plan.kind}(最多 {MAX_PLAN_STEPS} 步)",
            )
            answer = ExpertAnswer(
                folder=plan.folder, name=persona.source(plan.folder), question=text,
                text=plan.text, intent="infer", source="planner", ok=True,
                attempts=(text,), role="domain",
            )
            summary = plan.text if not trace else f"[规划] {plan.kind} · {plan.folder}\n" + plan.text
            if self.context_enabled:
                self.memory.remember(
                    text, decomposition.keywords,
                    packs=(plan.folder,), domains=(plan.folder,),
                )
            return ChatResult(text, decomposition, routing, [answer], summary, safety)

        decomposition = self.decomposer.decompose(text)
        routing = self.router.route(decomposition, max_targets=self.max_packs,
                                    rehit_rounds=self.rehit_rounds)
        context_used: tuple[str, ...] = ()

        # 上下文第二遍:这一句**一个领域包都没命中**、又是在"接着上一句问"时,
        # 才拿记忆里"当前话题"的关键词再查一次(判据见 memory.is_followup)。
        # 有直接命中就不动用记忆 —— 新话题不会被上一轮的词劫持。
        if self.context_enabled and not any(not t.card.is_general for t in routing.targets):
            topic = self.memory.topic()
            if topic and is_followup(text, decomposition.keywords):
                retry = self.router.route_focus(topic, max_targets=self.max_packs)
                if any(not t.card.is_general for t in retry.targets):
                    routing = retry
                    context_used = topic

        # 上限之外的达标包:问用户要不要补查(同意只影响这一次;config 里的上限不变)
        routing = self._maybe_expand(routing, ask_user)

        targets = self._select_targets(routing, safety, text)
        if tuple(targets) != routing.targets:
            note = routing.notes + ";按角色筛选后:"
            note += "、".join(t.card.folder for t in targets) or "无"
            routing = Routing(targets=tuple(targets), tried=routing.tried, notes=note)

        answers = []
        for target in routing.targets:
            answer = self._ask_target(target, text)
            answers.append(answer)
            if target.card.is_memory:
                answers.extend(self._memory_extras(target, text, answer))
        # 冲突：同一件事作者包与用户记忆都有说法时，按 trust.conflict_priority
        # 排序（默认作者包在前）；prefer 策略仍把低优先级列出来并注明。
        report = conflicts.resolve(answers, self.cards, self.trust)
        answers = report.answers
        ambiguity = report.ambiguity
        if report.notes:
            routing = Routing(
                targets=routing.targets,
                tried=routing.tried,
                notes=routing.notes + ";冲突:" + "；".join(report.notes),
                dropped=routing.dropped,
                rehits=routing.rehits,
            )

        # World Info 式预算：条数 / 正文字符。只裁“能答的”，失败账保留。
        usable = [answer for answer in answers if answer.ok]
        failed = [answer for answer in answers if not answer.ok]
        trimmed = budget_mod.trim_answers(
            usable,
            max_entries=self.config.retrieval.max_entries,
            text_budget=self.config.retrieval.text_budget,
        )
        answers = trimmed.answers + failed
        budget_note = trimmed.note

        summary = summarize(text, decomposition, routing, answers, safety,
                            detail=detail, trace=trace, context=context_used,
                            ambiguity=ambiguity, plain=plain,
                            budget_note=budget_note)

        # 记住这一轮:自己的关键词优先;整句都是虚词时,把借来的上下文续上去,
        # 这样"引力波是什么 → 它的频率呢 → 还有吗"能一路接住。
        if self.context_enabled:
            answered = tuple(dict.fromkeys(a.folder for a in answers if a.ok))
            domains = tuple(dict.fromkeys(
                a.folder for a in answers if a.ok and a.role != "general"
            ))
            self.memory.remember(
                text,
                decomposition.keywords or context_used,
                packs=answered,
                domains=domains,
            )
        return ChatResult(
            text, decomposition, routing, answers, summary, safety,
            budget_note=budget_note,
        )

    def decompose(self, text: str) -> Decomposition:
        return self.decomposer.decompose(text)

    def route(self, text: str) -> tuple[Decomposition, Routing]:
        decomposition = self.decomposer.decompose(text)
        return decomposition, self.router.route(decomposition, max_targets=self.max_packs,
                                                rehit_rounds=self.rehit_rounds)

    # ---- 可调开关(命令行 / 对话内都能改)--------------------------------
    def set_max_packs(self, value: int) -> int:
        """改"一次最多查几个包"。返回真正生效的值(越界会收敛)。"""
        self.max_packs = settings_mod.clamp_max_packs(value)
        return self.max_packs

    def set_rehit_rounds(self, value: int) -> int:
        """改"再命中轮数"(0 = 关闭)。返回真正生效的值。"""
        self.rehit_rounds = settings_mod.clamp_rehit_rounds(value)
        return self.rehit_rounds

    # ---- 信任 / 冲突优先级（写回 config.json）----------------------------
    def _reload_config(self) -> None:
        self.config = settings_mod.load()
        self.trust = TrustMap.from_settings(self.config.trust)
        self.router.set_trust(self.trust)

    def trust_lines(self) -> list[str]:
        return self.trust.describe_lines(self.cards)

    def set_trust_ref(self, ref: str, level: str = "high") -> str:
        """把「包/条目」标成 high / low / normal；写回 config.json。"""
        settings_mod.set_trust_ref(ref, level)
        self._reload_config()
        return level

    def set_author_entries_high(self, enabled: bool) -> bool:
        settings_mod.update_trust(author_entries_high=bool(enabled))
        self._reload_config()
        return bool(enabled)

    def set_conflict_priority(self, tokens: tuple[str, ...]) -> tuple[str, ...]:
        settings_mod.set_priority(tuple(tokens))
        self._reload_config()
        return self.trust.conflict_priority

    def retrieval_info(self) -> dict:
        return self.router.stats()

    def relations(self) -> "PackGraph":
        return PackGraph.from_cards(self.cards)

    def remember(self, text: str, keywords: tuple[str, ...] | list[str] = ()) -> None:
        """手动往上下文里塞一句(测试与 :remember 用)。"""
        self.memory.remember(text, keywords)

    def forget(self) -> None:
        self.memory.clear()

    def remembered(self) -> tuple[str, ...]:
        return self.memory.keywords()

    def context_line(self) -> str:
        return self.memory.snapshot() if self.context_enabled else "(上下文已关闭)"

    # ---- 记忆数据包 ------------------------------------------------------
    def reload(self) -> tuple[ExpertCard, ...]:
        """重新扫描 packs —— 教完新知识后,让那条知识立刻能命中。"""
        cards = discover(self.packs_root) if self.packs_root else discover()
        self.cards = tuple(cards)
        self.decomposer = Decomposer(self.cards)
        # 增量：Router 只重建真正变过的包；教完一条记忆不会把所有倒排表推倒
        self.router.refresh(self.cards)
        self.router.set_trust(self.trust)
        self.planner = BoundedPlanner(self.cards)
        return self.cards

    def memory_card(self) -> ExpertCard | None:
        for card in self.cards:
            if card.is_memory:
                return card
        return None

    def teach(self, knowledge: str, keywords: tuple[str, ...] | list[str] = (),
              raw: str | None = None,
              secondary: tuple[str, ...] | list[str] = (),
              answer: str | None = None) -> tuple[dict, bool]:
        """把一条知识写进记忆数据包(不做确认;确认在终端里问)。

        `answer` 给了就是 Q&A 对（问题与答案分开存）；不给就沿用旧写法，
        问题与答案都是同一句知识。`raw` 是用户当时输入的整行(含 `#`),
        `#?` 回放的就是它。`secondary` 是次要关键词(权重低)。
        返回 (条目, 是否新建):同一道题再教一次会更新答案/关键词，不重复追加。
        """
        card = self.memory_card()
        if card is None:
            raise RuntimeError("没有记忆数据包(experts/packs/memory)")
        entry, created = lessons.teach(
            card, knowledge, keywords, raw=raw, secondary=secondary, answer=answer,
        )
        self._invalidate_core(card)
        self.reload()
        return entry, created

    def revise(self, question: str, answer: str,
               keywords: tuple[str, ...] | list[str] = (),
               raw: str | None = None,
               secondary: tuple[str, ...] | list[str] = ()) -> tuple[dict, str | None]:
        """CBR 修正：同一道题改写答案；返回 (条目, 旧答案)。"""
        card = self.memory_card()
        if card is None:
            raise RuntimeError("没有记忆数据包(experts/packs/memory)")
        entry, previous = lessons.revise(
            card, question, answer, keywords, raw=raw, secondary=secondary,
        )
        self._invalidate_core(card)
        self.reload()
        return entry, previous

    def taught(self, query: str = "") -> list[dict]:
        """现在记着哪些教过的知识(空查询 = 全部;`query` 按原话/关键词过滤)。"""
        card = self.memory_card()
        if card is None:
            return []
        return lessons.find_taught(card, query)

    def forget_taught(self, query: str) -> list[dict]:
        """忘掉和 `query` 对得上的教过的知识;返回真正删掉的条目(空 = 没删)。"""
        card = self.memory_card()
        if card is None:
            raise RuntimeError("没有记忆数据包(experts/packs/memory)")
        removed = lessons.forget(card, query)
        if removed:
            self._invalidate_core(card)
            self.reload()
        return removed

    def taught_text(self, query: str = "") -> str:
        """`#?` 的输出:按用户当时的原话逐行列出(`lessons.render_taught`)。"""
        card = self.memory_card()
        if card is None:
            return "没有记忆数据包(experts/packs/memory),现在什么都记不住。"
        return lessons.render_taught(card, query)

    def _invalidate_core(self, card: ExpertCard) -> None:
        """写盘之后让核心的缓存跟上(单进程常驻时尤其重要)。"""
        try:
            corebridge.invalidate(card.pack_dir)
        except Exception:                                  # noqa: BLE001
            pass

    @staticmethod
    def _entry_keys(entry: dict) -> set[str]:
        """一个条目"声称自己答什么":题目 / 别名 / 关键词。"""
        keys: set[str] = set()
        for item in [entry.get("question")] + list(entry.get("aliases") or []) + list(
            entry.get("keywords") or []
        ):
            text = str(item or "").strip()
            if text:
                keys.add(text)
        return keys

    def _ambiguity_folders(self, answers: list[ExpertAnswer]) -> tuple[str, ...]:
        """兼容旧调用：现在统一由 `conflicts.resolve` 计算冲突。"""
        return conflicts.resolve(answers, self.cards, self.trust).ambiguity

    def guard(self, text: str) -> dict:
        """框架底线 + ethics 数据包的细则(若它还装着)。"""
        reports: list[dict] = [safety_rules.assess(text)]

        ethics = self._card("ethics")
        if ethics is not None:
            report = self._guard_card(ethics, text)
            if report is None:
                reports.append({
                    "level": "ok", "labels": "无", "rule_ids": [], "advice": [],
                    "error": "数据包 ethics 的安全闸调用失败(框架底线仍然生效)",
                })
            else:
                reports.append(report)
        return safety_rules.merge(reports)

    def _guard_card(self, card: ExpertCard, text: str) -> dict | None:
        """这一包的安全判定:默认单进程;失败就退回子进程并记一条说明。"""
        if self.engine == "inprocess":
            try:
                return corebridge.guard(card, text)
            except Exception as exc:                   # noqa: BLE001
                self._note_engine_fallback(card, exc)
        proc = self._run(card, ("--guard", text, "--json"))
        if proc is None:
            return None
        try:
            parsed = json.loads(proc.stdout)
        except json.JSONDecodeError:
            self.engine_notes.append(
                f"{card.folder}:安全闸输出无法解析:{proc.stdout[:60]!r}"
            )
            return None
        return parsed if isinstance(parsed, dict) else None

    # ---- 选包 ------------------------------------------------------------
    def _maybe_expand(
        self,
        routing: Routing,
        ask_user: Callable[[str], str] | None,
    ) -> Routing:
        """关键词命中了超过 max_packs 的包:问用户要不要把剩下的也查了。

        同意**只影响这一次**;`config.json` 里的上限不会被改动(始终锁死),
        下一次提问照旧按上限截断。没有提供 `ask_user`(纯 API 调用)就不提问,
        行为与原来完全一样。
        """
        if not routing.dropped or ask_user is None:
            return routing
        names = "、".join(t.card.folder for t in routing.dropped)
        try:
            answer = ask_user(f"能否再调用数据包[y/n]:{names}")
        except (EOFError, KeyboardInterrupt):
            answer = "n"
        if not lessons.is_yes(str(answer)):
            return Routing(
                targets=routing.targets,
                tried=routing.tried,
                notes=routing.notes + ";用户没有再调用",
                dropped=routing.dropped,
            )
        merged = list(routing.targets) + list(routing.dropped)
        merged.sort(key=lambda t: -t.score)
        notes = routing.notes + f";用户同意补查 {len(routing.dropped)} 个:{names}"
        return Routing(targets=tuple(merged), tried=routing.tried, notes=notes)

    def _select_targets(
        self, routing: Routing, safety: dict, text: str = ""
    ) -> list[RouteTarget]:
        # 危机场景优先：先分清"我"还是"身边人"，再决定用哪一条。
        # 必须在普通路由之前 —— 否则"朋友不想活了"会因为关键词命中 crisis-self，
        # 把帮助者的说明念给当事人（或反过来）。
        if safety.get("level") == "caution":
            crisis = self._crisis_kind(text)
            if crisis:
                emotion = self._card("emotion")
                entry = emotion.entry(crisis) if emotion else None
                if emotion is not None and entry is not None:
                    return [
                        RouteTarget(
                            card=emotion,
                            score=0,
                            reasons=("安全灰区:按对象选择危机回应",),
                            entry_ids=(str(entry.get("id") or crisis),),
                            fallback_question=str(entry.get("question") or "") or None,
                            prefer_question=str(entry.get("question") or "") or None,
                        )
                    ]

        domain = [t for t in routing.targets if not t.card.is_general]
        if domain:
            return domain

        general_matched = [t for t in routing.targets if t.card.is_general]
        if general_matched:
            return general_matched

        # 一个领域包都没命中,又没有命中寒暄条目 → 也让兜底包接一次;
        # 它若也接不住,总结层才会弹提示。
        general = self._general()
        if general is not None:
            return [
                RouteTarget(
                    card=general,
                    score=0,
                    reasons=("兜底:general 包接住",),
                    entry_ids=(),
                    fallback_question=None,
                )
            ]
        return []

    def _crisis_kind(self, text: str) -> str | None:
        """这段话说的是"我"还是"我身边的人"?不是危机话题就返回 None。"""
        self_hit = bool(_SELF_CRISIS.search(text))
        other_hit = bool(_OTHER_CRISIS.search(text))
        if other_hit and not self_hit:
            return "crisis-support"
        if self_hit or _NAKED_CRISIS.search(text):
            return "crisis-self"
        return "crisis-support" if other_hit else None

    @staticmethod
    def _entry_mentions(entry: dict, text: str) -> bool:
        """这条记忆的题目/别名/关键词/次要关键词，有没有出现在用户这句里。"""
        lowered = (text or "").lower()
        parts: list[str] = [str(entry.get("question") or "")]
        parts.extend(str(item) for item in entry.get("aliases") or ())
        parts.extend(str(item) for item in entry.get("keywords") or ())
        parts.extend(str(item) for item in entry.get("secondary_keywords") or ())
        for part in parts:
            part = part.strip().lower()
            if len(part) >= 2 and part in lowered:
                return True
        return False

    def _memory_extras(
        self, target: RouteTarget, user_text: str, primary: ExpertAnswer
    ) -> list[ExpertAnswer]:
        """记忆包内的"再命中":同一条记忆之外，还有别的记忆也提到了这句里的词。

        只在**不是是非问**（不带"吗/是否"）时补，避免"What 是英语单词吗"还要
        把"What 是什么意思"也拖出来；补的条目必须**自己**命中用户这句话，
        不能只靠上一轮上下文（防止一直重复上文）。
        """
        if not primary.ok or not target.entry_ids:
            return []
        if any(word in user_text for word in ("吗", "是否", "是不是")):
            return []

        seen = {primary.entry}
        extras: list[ExpertAnswer] = []
        for entry_id in target.entry_ids[:4]:
            if entry_id in seen:
                continue
            entry = target.card.entry(entry_id)
            if entry is None or not self._entry_mentions(entry, user_text):
                continue
            question = str(entry.get("question") or "").strip()
            if not question:
                continue
            answer = self._ask_expert_card(target.card, question, attempts=(question,))
            if answer is None or not answer.ok or answer.entry in seen:
                continue
            seen.add(answer.entry)
            extras.append(answer)
            if len(extras) >= 2:
                break
        return extras

    # ---- 查数据包 --------------------------------------------------------
    def _ask_target(self, target: RouteTarget, user_text: str) -> ExpertAnswer:
        """这一包的追问顺序:用户原话 → 危机指定问法 → 聚合出的前几条条目原题。

        借鉴 RAGless 的 Q-Q 聚合:同一个答案有多条问法时,不只看"最像的那一条",
        而是把条目得分按条目累加(见 Router._score_targets 的 entry_scores),
        再拿前几条条目的**标准问法**依次试。这样一条别名/关键词只沾一点边的
        问题,仍有机会被它对应的那条知识接住;文档/导入包尤其受益。
        多试有上限(3 条),不会把关一次提问拖成很多次调用。
        """
        questions: list[str] = []
        if target.prefer_question:
            questions.append(target.prefer_question)
        if user_text not in questions:
            questions.append(user_text)

        for entry_id in target.entry_ids[:3]:
            entry = target.card.entry(entry_id)
            if not entry:
                continue
            question = str(entry.get("question") or "").strip()
            if question and question not in questions:
                questions.append(question)

        if target.fallback_question and target.fallback_question not in questions:
            questions.append(target.fallback_question)

        attempts: list[str] = []
        best: ExpertAnswer | None = None
        for question in questions:
            attempts.append(question)
            answer = self._ask_expert_card(target.card, question, attempts=tuple(attempts))
            if answer is None:
                continue
            if answer.source == "refusal":
                answer.attempts = tuple(attempts)
                return answer
            if answer.ok:
                answer.attempts = tuple(attempts)
                return answer
            best = answer
        if best is None:
            return ExpertAnswer(
                folder=target.card.folder, name=persona.source(target.card.folder),
                question=questions[-1], text="", ok=False,
                error=f"数据包 {target.card.folder} 调用失败", attempts=tuple(attempts),
                role=target.card.role,
            )
        best.attempts = tuple(attempts)
        return best

    def _ask_expert_card(
        self,
        card: ExpertCard,
        question: str,
        attempts: tuple[str, ...] = (),
    ) -> ExpertAnswer | None:
        payload, error = self._json_for(card, question)
        if payload is None:
            return ExpertAnswer(
                folder=card.folder, name=persona.source(card.folder), question=question, text="",
                ok=False, error=error or f"数据包 {card.folder} 调用失败",
                attempts=attempts, role=card.role,
            )

        text = str(payload.get("text") or "")
        intent = str(payload.get("intent") or "")
        source = str(payload.get("source") or "")
        domain = payload.get("domain") or {}
        entry = domain.get("entry") if isinstance(domain, dict) else None
        safety = payload.get("safety") or {}
        level = str(safety.get("level") or "ok") if isinstance(safety, dict) else "ok"
        semantic = payload.get("semantic") or {}
        raw_confidence = payload.get("confidence")
        try:
            confidence = float(raw_confidence) if raw_confidence is not None else None
        except (TypeError, ValueError):
            confidence = None
        trust = self.trust.level(card, str(entry)) if entry else "normal"

        if source == "refusal":
            ok = False
        elif card.is_general:
            # 兜底包只在"条目作答"或确实算出来的通用能力上算命中;
            # 否则它可能用一句"这一条我处理不了"把兜底伪装成回答(实测踩到)。
            if intent == "domain":
                ok = entry is not None
            elif intent == "semantic":
                ok = isinstance(semantic, dict) and semantic.get("outcome") == "answer"
            else:
                ok = intent in GOOD_INTENTS - {"semantic"} and bool(text.strip())
        elif intent == "domain":
            ok = entry is not None
        else:
            ok = intent in GOOD_INTENTS and bool(text.strip())

        return ExpertAnswer(
            folder=card.folder, name=persona.source(card.folder), question=question, text=text,
            intent=intent, entry=entry, source=source, safety_level=level,
            ok=ok, attempts=attempts, role=card.role,
            confidence=confidence, trust=trust,
        )

    # ---- 工具 ------------------------------------------------------------
    def _card(self, folder: str) -> ExpertCard | None:
        return card_for(folder, self.cards)

    def _general(self) -> ExpertCard | None:
        return general_card(self.cards)

    def _plan(self, text: str) -> PlanResult | None:
        """跑一次有界规划;任何异常都只让这一轮落回普通检索,不拖垮对话。"""
        try:
            return self.planner.plan(text, self._plan_ask)
        except Exception as exc:                              # noqa: BLE001
            note = f"有界规划器失败({type(exc).__name__}: {exc}),已落回普通检索"
            if note not in self.engine_notes:
                self.engine_notes.append(note)
            return None

    def _plan_ask(self, card: ExpertCard, question: str) -> dict | None:
        payload, error = self._json_for(card, question)
        if payload is None or error:
            return None
        return payload

    def _json_for(self, card: ExpertCard, question: str) -> tuple[dict | None, str | None]:
        """这一包这一次的 `--json` 结果:默认单进程,失败回退子进程(并记说明)。"""
        if self.engine == "inprocess":
            try:
                payload = corebridge.ask(card, question)
                if isinstance(payload, dict):
                    return payload, None
                return None, f"数据包 {card.folder} 的单进程输出不是 JSON 对象"
            except Exception as exc:                   # noqa: BLE001
                self._note_engine_fallback(card, exc)
        proc = self._run(card, ("--json", question))
        if proc is None:
            return None, f"数据包 {card.folder} 调用失败"
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return None, f"数据包 {card.folder} 的输出无法解析:{proc.stdout[:80]!r}"
        if not isinstance(payload, dict):
            return None, f"数据包 {card.folder} 的输出不是 JSON 对象:{proc.stdout[:80]!r}"
        return payload, None

    def _note_engine_fallback(self, card: ExpertCard, exc: Exception) -> None:
        note = (
            f"{card.folder}:单进程内核失败({type(exc).__name__}: {exc}),"
            "已改用子进程重试"
        )
        if note not in self.engine_notes:
            self.engine_notes.append(note)

    def engine_info(self) -> dict:
        """这一轮用的是什么进程形态、缓存命中多少(给 `:stat` 与测试看)。"""
        info: dict = {
            "mode": self.engine,
            "last_ms": round(self.last_elapsed_ms, 2),
            "notes": list(self.engine_notes),
            "retrieval": self.router.stats(),
        }
        if self.engine == "inprocess":
            info.update(corebridge.stats())
        return info

    def _run(self, card: ExpertCard, args: tuple[str, ...]) -> subprocess.CompletedProcess | None:
        """用**同一个核心**跑一个数据包:核心不变,只换 --pack 指的目录。

        这是 `--subprocess` 的路径,也是单进程失败后的退路:隔离优先时用它。
        子进程的好处是某个数据包把自己搞崩了,只是"这一包调用失败",不连累其它包;
        代价是每次都要重新解释器 + 重新读 kb/词向量。
        """
        env = dict(os.environ)
        env["SMALLAI_PACK"] = str(card.pack_dir)
        try:
            return subprocess.run(
                [sys.executable, str(CORE_RUN), "--pack", str(card.pack_dir), *args],
                cwd=str(card.pack_dir),
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
            )
        except (OSError, subprocess.SubprocessError):
            return None


__all__ = ["ChatResult", "GoChat"]

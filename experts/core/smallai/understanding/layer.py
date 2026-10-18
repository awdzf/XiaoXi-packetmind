"""理解层:把路由结果落成结构化语义帧(SemanticFrame)。

职责边界刻意划清:
  - 路由层只决定"这句话归哪一类";
  - 理解层负责"这一类所要求的槽位,到底填得上没有"。

填槽只有一条路:**规则抽取**(免费、确定、可解释)。
模型端口已拆除:填不上就进 missing,由校验层走澄清路径 —— 不猜、不借模型。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any

from ..core import (
    Context,
    Intent,
    SemanticFrame,
    Stage,
    normalize,
)
from ..rules.router import extract_city
from ..rules.extract import extract_expression, extract_target_unit, extract_value_unit
from ..rules.lexicon import ALIASES_BY_LENGTH, UNIT_ALIASES, UNIT_TO_KIND  # noqa: F401  (对外沿用旧导出位置)

# --------------------------------------------------------------------------
# 槽位 schema:每个意图需要什么、什么是必填。
# 校验层直接读这份 schema 判断"缺槽",避免两处各写一套要求。
# --------------------------------------------------------------------------
@dataclass
class SlotSpec:
    name: str
    required: bool = True
    desc: str = ""
    kind: str = "text"          # text / number / expression / unit / city / logic


SLOT_SCHEMA: dict[Intent, list[SlotSpec]] = {
    Intent.GREETING: [],
    Intent.HELP: [],
    Intent.SAFETY: [
        SlotSpec("topic", False, "触发安全判定的原文(只用于交代,可为空)", "text"),
    ],
    Intent.WEATHER: [
        SlotSpec("city", True, "要查天气的城市", "city"),
        SlotSpec("day", False, "日期口径(今天/明天/未来)", "text"),
    ],
    Intent.TIME: [
        SlotSpec("scope", True, "now=时刻 / date=日期", "text"),
    ],
    Intent.CALC: [
        SlotSpec("expression", True, "算术表达式", "expression"),
    ],
    Intent.CONVERT: [
        SlotSpec("value", True, "待换算数值", "number"),
        SlotSpec("from_unit", True, "源单位", "unit"),
        SlotSpec("to_unit", True, "目标单位", "unit"),
    ],
    # ---- 新增三个意图的槽位 ------------------------------------------
    Intent.COMPARE: [
        # kind="quantity":值本身是个**可读描述**("3 kg"),不是纯数字,
        # 所以不能套 number 的类型检查(套了会报"不是数值",实测踩到)。
        SlotSpec("left", True, "左边的量(值+单位)", "quantity"),
        SlotSpec("right", True, "右边的量(值+单位)", "quantity"),
        SlotSpec("relation", True, "gt/lt/ge/le/eq/ne", "text"),
    ],
    Intent.LOGIC: [
        SlotSpec("expression", True, "布尔式(且/或/非 组合的原子命题)", "logic"),
    ],
    Intent.INFER: [
        SlotSpec("target", True, "要问/要断言的谓词", "text"),
        SlotSpec("left", False, "主语(问事实时给)", "text"),
        SlotSpec("right", False, "宾语(问事实时给)", "text"),
        SlotSpec("why", False, "问『为什么』,要的是推导链", "text"),
        SlotSpec("capability_of", False, "列举谁的能力/性质", "text"),
    ],
    Intent.SEMANTIC: [
        SlotSpec("query", True, "向量算式或要找邻居的词", "text"),
        SlotSpec("a", False, "算式第一项", "text"),
        SlotSpec("b", False, "算式里被减掉的项", "text"),
        SlotSpec("c", False, "算式里加上的项", "text"),
    ],
    Intent.DOMAIN: [
        SlotSpec("topic", False, "本领域问题原文(用于检索本地知识条目,可为空)", "text"),
    ],
    Intent.OFFTOPIC: [
        SlotSpec("topic", False, "问题原文(只用于说明它超出领域,可为空)", "text"),
    ],
    Intent.UNKNOWN: [],
}

# 换算句里夹在数值与单位之间的动词字,匹配单位时要先砍掉
_CONNECTORS = frozenset("换算转为成到是等于多少变作")


def _traceable(slots: dict[str, Any]) -> str:
    """把槽位渲染成可读且**一定可序列化**的字符串。

    槽位里现在会放表达式树(logic.Expr)这类对象 —— 它们没有 JSON 形态。
    追踪是给人看的,不该因为一个对象不可序列化就整条流水线崩掉;
    所以这里统一降级成 repr,而不是把它们塞进 json.dumps。
    """

    def render(value: Any) -> Any:
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        if isinstance(value, (list, tuple)):
            return [render(v) for v in value]
        if isinstance(value, dict):
            return {str(k): render(v) for k, v in value.items()}
        return repr(value)

    try:
        return json.dumps(render(slots), ensure_ascii=False)
    except (TypeError, ValueError):
        return repr(slots)


def match_unit(raw: str) -> str | None:
    """把一段'紧邻数值的字符片段'匹配到规范单位。

    例:'公斤换算' -> kg;'斤换算成' -> jin;'司斤' -> jin;'个苹果换' -> None

    做法:从**最长候选**开始试(整段 -> 砍掉尾部连接字 -> 再砍首字符),
    保证 '公斤' 不会被更短的 '斤' 抢先命中。
    """
    if not raw:
        return None
    text = raw.strip().lower()
    while text and text[-1] in _CONNECTORS:      # 先剥掉尾部的'换/算/成'这类连接字
        text = text[:-1]
    while text:
        if text in UNIT_ALIASES:
            return UNIT_ALIASES[text]
        text = text[1:]
    return None


@dataclass
class UnderstandingLayer:
    """理解层:纯规则填槽(这一版没有模型端口)。"""

    name = "understanding.slots"

    # ---- 主流程 ----------------------------------------------------------
    def understand(self, ctx: Context) -> SemanticFrame:
        started = time.perf_counter()
        text = normalize(ctx.raw_text)
        intent = ctx.route.intent if ctx.route else Intent.UNKNOWN

        slots: dict[str, Any] = {}
        unresolved: list[str] = []

        # 路由层已经抽到的线索先落进来(层间不重复劳动)
        if ctx.route and ctx.route.slots_hint:
            for k, v in ctx.route.slots_hint.items():
                if k.startswith("_"):
                    continue                      # 下划线开头是内部线索,不进帧
                if v is not None:
                    slots[k] = v

        slots.update(self._extract_by_rules(text, intent, unresolved))

        missing = self._missing_slots(intent, slots)

        frame = SemanticFrame(intent=intent, slots=slots, missing=missing, unresolved=unresolved)

        # 断言类句子:把读到的事实挂到帧上,由逻辑层入库(理解层不写事实库,保持单向)
        if intent is Intent.INFER and "asserted" in slots:
            fact = self._last_asserted_fact()
            if fact is not None:
                frame.derived_fact = fact

        ctx.frame = frame

        elapsed = (time.perf_counter() - started) * 1000
        ctx.record(
            Stage.UNDERSTAND,
            self.name,
            ok=not missing,
            detail=(
                f"slots={_traceable(slots)} "
                f"missing={missing or '[]'}"
            ),
            elapsed_ms=elapsed,
            unresolved=unresolved,
        )
        return frame

    # ---- 规则抽取 --------------------------------------------------------
    def _extract_by_rules(self, text: str, intent: Intent, unresolved: list[str]) -> dict[str, Any]:
        slots: dict[str, Any] = {}

        if intent is Intent.WEATHER:
            city = extract_city(text)
            if city:
                slots["city"] = city
            for word, label in (("明天", "明天"), ("后天", "后天"), ("未来", "未来几天"), ("这几天", "未来几天")):
                if word in text:
                    slots["day"] = label
                    break
            else:
                if "今天" in text or "现在" in text:
                    slots["day"] = "今天"

        elif intent is Intent.TIME:
            if any(w in text for w in ("几点", "现在", "当前")):
                slots["scope"] = "now"
            if any(w in text for w in ("日期", "几号", "星期", "周几", "礼拜")):
                slots["scope"] = "date"

        elif intent is Intent.CALC:
            expr = extract_expression(text)
            if expr:
                slots["expression"] = expr
            else:
                # 提到了计算但没抓到算式 —— 记成"未决",让校验层给出澄清话术
                unresolved.append("expression")

        elif intent is Intent.CONVERT:
            slots.update(self._parse_conversion(text, unresolved))

        elif intent is Intent.COMPARE:
            slots.update(self._parse_compare(text, unresolved))

        elif intent is Intent.LOGIC:
            slots.update(self._parse_logic_slots(text, unresolved))

        elif intent is Intent.INFER:
            slots.update(self._parse_infer(text, unresolved))

        elif intent is Intent.SEMANTIC:
            slots.update(self._parse_semantic(text, unresolved))

        elif intent in (Intent.DOMAIN, Intent.OFFTOPIC):
            topic = re.sub(r"^[请帮我想问一下,，\s]+", "", text).strip("?？。 ")
            if topic:
                slots["topic"] = topic

        return slots

    # ---- 新意图的槽位抽取 -------------------------------------------------
    def _parse_compare(self, text: str, unresolved: list[str]) -> dict[str, Any]:
        """比较句:两个量 + 一个关系。解析不出来就进 unresolved,不猜。

        槽位里只放**可序列化**的东西(值/单位/关系字符串)。
        真实比较由逻辑层做 —— 那边才是权威口径,这里不预算计一遍。
        """
        from ..logic.compare import parse_comparison

        parsed = parse_comparison(text)
        if not parsed.ok or parsed.left is None or parsed.right is None:
            unresolved.append("comparison")
            return {}
        return {
            "left": parsed.left.describe(),
            "right": parsed.right.describe(),
            "left_value": parsed.left.value,
            "right_value": parsed.right.value,
            "left_unit": parsed.left.unit,
            "right_unit": parsed.right.unit,
            "relation": parsed.relation or "gt",
        }

    def _parse_logic_slots(self, text: str, unresolved: list[str]) -> dict[str, Any]:
        """布尔式:交给 logic.nlp 解析成表达式树;解析不出来就报缺。"""
        from ..logic.expr import to_text
        from ..logic.nlp import parse_logic

        parsed = parse_logic(text)
        if not parsed.ok or parsed.expr is None:
            unresolved.extend(parsed.unknown_words or ["logic"])
            return {}
        return {"expression": parsed.expr, "expression_text": to_text(parsed.expr)}

    def _parse_infer(self, text: str, unresolved: list[str]) -> dict[str, Any]:
        """归属/类别句:既可当提问(北京属于华北吗),也可当断言(北京属于华北)。

        识别顺序:归属句(属于/是/在) -> 属性句(会飞/有翅膀/含维生素)。
        两套句式必须分开走,否则『麻雀会飞』会被切成 麻雀/会/飞。
        """
        from ..logic.nlp import parse_fact, parse_property

        cleaned = re.sub(r"[\s?？。!！]+$", "", text)

        # 形态甲:"X 能做什么 / 会干什么" -> 列举能力。
        # 必须排在归属句之前:否则 "苹果能做什么" 会被 parse_fact 解析成
        # ("能", ("苹果","做什么")) 而走成断言,把问句答成"这条我已经知道"(实测踩到)。
        capability = re.match(
            r"^(.{1,12}?)(?:能|会|可以)(?:做|干|吃|用来做)?(?:什么|啥|哪些事|哪些事情)$", cleaned
        )
        if capability:
            subject = capability.group(1).strip("的了")
            if subject:
                return {"capability_of": subject}

        # 形态乙:"为什么 X 能 Y" -> 给推导链。
        # 刻意收紧:光有"为什么"不算 —— 必须**剥掉问词之后能解析成事实**,
        # 否则"为什么天空是蓝色的"这类开放问答会被吸进事实推理(实测踩到)。
        why_match = re.match(r"^(?:为什么|为啥|为何|怎么做到)", cleaned)
        why = bool(why_match)
        if why:
            cleaned = re.sub(r"^(?:为什么|为啥|为何|怎么做到)", "", cleaned).strip()
            if parse_fact(cleaned) is None and parse_property(cleaned) is None:
                unresolved.append("why-not-a-fact")
                return {}

        # 问句判据要**在所有位置**找,不能只看结尾:"是不是"通常出现在句中
        # (苹果是不是食物)。早先只匹配结尾,于是这类问句被当成断言,
        # 被答成"这条我已经知道"(实测踩到)。
        asking = bool(
            re.search(r"(是不是|是否|吗|呢|成不成立|对不对)", cleaned)
        )
        core = re.sub(r"(成不成立|对不对|是不是|是否|吗|呢)$", "", cleaned).strip()
        fact = parse_fact(core)
        if fact is not None:
            predicate, args = fact
            if why and len(args) >= 2:
                return {"why": True, "target": predicate, "left": args[0], "right": args[-1]}
            if asking and len(args) >= 2:
                return {"target": predicate, "left": args[0], "right": args[-1]}
            if asking and len(args) == 1:
                # "北京是城市吗" 这种只有一个参数的归属问句:类别直接从原句抽,
                # 不靠谓词反推(反推容易得到 "北京 是 是" 这种胡话)。
                return {"single_subject": args[0], "single_text": core}
            self._asserted_fact = fact
            return {"target": core, "asserted": True}

        prop = parse_property(core)
        if prop is not None:
            predicate, args = prop
            if why and len(args) >= 2:
                return {"why": True, "target": predicate, "left": args[0], "right": args[-1]}
            if asking:
                return {"target": predicate, "left": args[0], "right": args[-1]}
            self._asserted_fact = prop
            return {"target": core, "asserted": True}

        unresolved.append("fact")
        return {}

    def _last_asserted_fact(self) -> tuple[str, tuple[str, ...]] | None:
        """取出本轮抽到的断言事实(每句话只可能有一条)。"""
        return getattr(self, "_asserted_fact", None)

    def _parse_semantic(self, text: str, unresolved: list[str]) -> dict[str, Any]:
        """词向量算式 / 找语义邻居。解析失败就报缺,不硬凑一个算式。"""
        from ..semantics.parse import parse_semantic

        query = parse_semantic(text)
        if not query.ok:
            unresolved.append("semantic")
            return {}
        if query.kind == "neighbors":
            return {"query": query.word or "", "kind": "neighbors"}
        return {
            "query": query.described(),
            "kind": "analogy",
            "a": query.a,
            "b": query.b,
            "c": query.c,
        }

    def _parse_conversion(self, text: str, unresolved: list[str]) -> dict[str, Any]:
        """解析『3公斤换算成斤』这类句子。失败的部分进 unresolved,不瞎填。

        三条纪律(都是踩过坑才写下来的):
          · 单位只认**紧跟在数值后面那个 token**,不接受"猜单位" ——
            猜单位是换算错误的第一大来源;
          · 单位别名必须**最长优先**匹配,否则 "3公斤" 会被 "斤" 抢走;
          · 量纲是否一致不在这里判,**交给校验层**(它才会把量纲不一致报成错误)。
        """
        out: dict[str, Any] = {}

        # ---- 源:数值 + 紧邻单位 --------------------------------------
        value, raw_unit = extract_value_unit(text)
        if value is None:
            unresolved.append("value")
        else:
            out["value"] = value
            unit = match_unit(raw_unit or "")
            if unit is None:
                unresolved.append("from_unit")
            else:
                out["from_unit"] = unit

        # ---- 目标:换算动词后面那个单位 -------------------------------
        raw_target = extract_target_unit(text)
        if raw_target is None:
            unresolved.append("to_unit")
        else:
            unit = match_unit(raw_target)
            if unit is None:
                unresolved.append("to_unit")
            else:
                out["to_unit"] = unit

        return out

    def _missing_slots(self, intent: Intent, slots: dict[str, Any]) -> list[str]:
        return [
            spec.name
            for spec in SLOT_SCHEMA.get(intent, [])
            if spec.required and slots.get(spec.name) in (None, "", [])
        ]

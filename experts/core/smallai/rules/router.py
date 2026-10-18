"""规则引擎 / 逻辑树路由层。

这是流水线的第一跳:把一句自由文本判成一条**意图路径**,并顺手抽出几个高层线索。

实现是"逻辑树 + 记分"的混合体,而不是一堆 if/elif:
  - 树负责结构:根 -> 候选意图分支 -> 证据叶子
  - 记分负责取舍:每条证据命中 +1,某分支得分 = 命中数 / 该分支证据总数
  - 置信度 = 冠军分支得分,差距 = 冠军分 - 亚军分(用于判断"要不要回头")

这样做的实用价值:路由结果自带**可解释性**(path 里能看到走了哪些节点)、
自带**置信度**(低置信可以转兜底),而且加新意图只需要加分支,不用改动判断主流程。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from ..core import Context, Intent, RouteDecision, Stage, normalize, truncate
from ..domain import PACK
from ..domain.safety import assess as assess_safety
from .extract import extract_expression
from .lexicon import (
    AND_WORDS,
    CALC_WORDS,
    CITIES,
    COMPARE_SIGNAL_WORDS,
    COMPARE_WORDS,
    CONVERT_WORDS,
    GREETING_WORDS,
    HELP_WORDS,
    INFER_SIGNAL_WORDS,
    KNOWN_CLASSES,
    LOGIC_SIGNAL_WORDS,
    OR_WORDS,
    PROVINCES,
    QUESTION_MARKS,
    QUESTION_WORDS,
    TIME_WORDS,
    UNIT_ALIASES,
    WEATHER_STATE_WORDS,
    WEATHER_WORDS,
    has_any,
)

# 词表已迁到 lexicon.py(单一口径);这里只留路由本身需要的正则
_NUM_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")
_CITY_SUFFIX_RE = re.compile(r"([\u4e00-\u9fff]{2,6}?)(?:市|的天气|天气)")

# 比较/逻辑/推理三个新意图的"门"要够严,否则会把普通问句吸走
COMPARE_GATE_RE = re.compile(
    r"(?:比|大于|小于|等于|多于|少于|超过|不低于|不超过|至少|最多|>=|<=|!=|[<>])"
)
def _connective_pattern() -> str:
    """把连接词编成正则:ASCII 词加词边界,中文词按子串。

    踩过的坑:OR_WORDS 里有 "or",没有词边界时 CORS / localStorage 会被
    当成「或」,一个 CSS 问题就被路由去走布尔逻辑(实测命中)。
    """
    parts: list[str] = []
    for word in AND_WORDS + OR_WORDS:
        if len(word) <= 1:
            continue
        escaped = re.escape(word)
        if word.isascii() and word.isalnum():
            escaped = rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])"
        parts.append(escaped)
    return "|".join(parts)


LOGIC_CONNECTIVE_RE = re.compile(_connective_pattern())
CONDITIONAL_RE = re.compile(r"(?:如果|假如|若是|要是|只要).{1,30}?(?:那么|则|就|便)")
INFER_RE = re.compile(
    r"^(.{1,12}?)(属于|是不是|是否|是|在|具有|有|包括|包含)(.{1,12})$"
    r"|^(.{1,12}?)(属于|是)(.{1,12}?)(吗|呢|\?|？)$"
)

# 向量算术的形态:A - B + C(符号或口语运算符)
SEMANTIC_ARITH_RE = re.compile(
    r"[\u4e00-\u9fffA-Za-z0-9]{1,12}\s*(?:-|−|减去)\s*[\u4e00-\u9fffA-Za-z0-9]{1,12}"
    r"\s*(?:\+|\+|加上)\s*[\u4e00-\u9fffA-Za-z0-9]{1,12}"
)
SEMANTIC_WORDS = ("近义词", "同义词", "近义", "相关词", "相似词", "最近的词", "最相似的词",
                  "语义邻居", "向量", "词向量", "相似度")

# 平票时的意图优先级:越靠前越优先。
# 为什么需要它:分支得分是命中比例,两条分支都拿到 1.0 时,
# 靠"列表顺序"决定胜负太脆弱 —— 实测「javac怎么用」同时命中 HELP 与 DOMAIN,
# 列表里 HELP 在前,DOMAIN 就被抢走了。这里把取舍写成显式的一张表。
INTENT_PRIORITY: tuple[str, ...] = (
    "safety", "calc", "convert", "compare", "logic", "infer", "domain",
    "semantic", "weather", "time", "help", "greeting", "offtopic",
)


def _intent_rank(intent_value: str) -> int:
    try:
        return INTENT_PRIORITY.index(intent_value)
    except ValueError:
        return len(INTENT_PRIORITY)


# 能力列举与"为什么"追问:都属于事实推理的下游玩法
CAPABILITY_ASK_RE = re.compile(
    r"^.{1,12}?(?:能|会|可以)(?:做|干|吃|用来做)?(?:什么|啥|哪些事|哪些事情)$"
)
WHY_RE = re.compile(r"^(?:为什么|为啥|为何|怎么做到)")


def _contains_any(text: str, words: tuple[str, ...]) -> bool:
    return has_any(text, words)


def _has_any_unit(text: str) -> bool:
    """文本里有没有任何已知单位(词表来自 lexicon,加单位不用改这里)。"""
    lowered = text.lower()
    return any(alias in lowered for alias in UNIT_ALIASES)


def _match_compare_word(text: str) -> str | None:
    """命中一个比较词就返回它(长词优先,免得『大于等于』被『大于』抢走)。"""
    for word, _relation in sorted(COMPARE_WORDS, key=lambda kv: len(kv[0]), reverse=True):
        if "..." in word:
            continue
        if word in text:
            return word
    return None


def _looks_like_infer(text: str) -> bool:
    """判断这是不是一句"归属/能力/类别"式的话(问或断言)。

    刻意收紧,而且要有**已知的落脚点**,否则大量普通问句都会被吸进来:
      · 长度 30 以内(长句是开放讨论,不是归属判断);
      · 形状必须是下面四种之一,而且宾语/能力词得是我们认识的。
    """
    if len(text) > 30:
        return False
    if _is_conditional(text):
        return True
    # 形态甲:X 能做什么 / 会干什么 -> 列举能力。
    # 例外:"你能做什么"是在问**我**的能力,那是 HELP 的活,不能吸到这里。
    if CAPABILITY_ASK_RE.match(text) and text not in HELP_WORDS:
        return True
    # 形态乙:为什么 X 能 Y -> 要推导链。必须**既解析成事实、又有已知落脚点**,
    # 否则"为什么天空是蓝色的"会被吸进事实推理(实测踩到)。
    if WHY_RE.match(text):
        from ..logic.nlp import parse_fact, parse_property

        remainder = WHY_RE.sub("", text, count=1).strip()
        fact = parse_fact(remainder)
        if fact is not None and _known_term(fact[1][-1]):
            return True
        prop = parse_property(remainder)
        if prop is not None and prop[1][-1] in KNOWN_CLASSES:
            return True
    match = INFER_RE.search(text)
    if match:
        obj = (match.group(3) or match.group(6) or "").strip("吗呢?？")
        return _known_term(obj)
    # 属性句式(会飞/有翅膀/可以吃):宾语必须是已知属性。
    # 局部 import:rules 是底层,不在模块级别依赖 logic,免得绕出循环。
    from ..logic.nlp import MODAL_ACTION_RE, parse_property

    if MODAL_ACTION_RE.match(re.sub(r"(吗|呢)$", "", text).strip()):
        return True
    prop = parse_property(text)
    if prop is not None:
        return prop[1][-1] in KNOWN_CLASSES
    return False


def _is_conditional(text: str) -> bool:
    return bool(CONDITIONAL_RE.search(text))


def _known_term(term: str) -> bool:
    """这个宾语是不是我们认识的东西(城市/省份/内置类别词)。"""
    if not term:
        return False
    if _contains_any(term, CITIES) or _contains_any(term, PROVINCES):
        return True
    return term in KNOWN_CLASSES


def _is_weather_fact(text: str) -> bool:
    """『北京下雨吗』这类:问的是事实,不是天气预报。

    判据:同时出现城市 + 天气状态词 + 疑问/真假词。
    所以『北京今天天气怎么样』(没有状态词)不会被吸走。
    """
    if not extract_city(text):
        return False
    if not _contains_any(text, WEATHER_STATE_WORDS):
        return False
    return _contains_any(text, ("吗", "呢", "?", "？", "真的", "是不是", "是否"))


# --------------------------------------------------------------------------
# 逻辑树
# --------------------------------------------------------------------------
@dataclass
class TreeNode:
    """逻辑树节点。

    predicate 是这条边的通过条件;leaf 为 False 的节点继续往下走。
    证据节点(leaf=True)在记分时算一票。
    """

    name: str
    predicate: Callable[[str], bool]
    children: list["TreeNode"] = field(default_factory=list)
    leaf: bool = False
    slot_extractor: Callable[[str], dict[str, Any]] | None = None


@dataclass
class Branch:
    """一条候选意图分支:树根的一个孩子。"""

    intent: Intent
    node: TreeNode
    evidence_texts: dict[str, str] = field(default_factory=dict)  # 证据名 -> 人话解释


def _walk(node: TreeNode, text: str, path: list[str]) -> tuple[int, int, dict[str, Any]]:
    """深度优先走树。返回 (命中证据数, 证据总数, 槽位)。"""
    if not node.predicate(text):
        return 0, 0, {}
    path.append(node.name)
    if node.leaf:
        slots = node.slot_extractor(text) if node.slot_extractor else {}
        return 1, 1, slots
    hits = total = 0
    slots: dict[str, Any] = {}
    for child in node.children:
        ch, ct, cs = _walk(child, text, path)
        hits += ch
        total += ct
        for k, v in cs.items():
            slots.setdefault(k, v)
    # 父节点自身算一票"门"证据:门都没过就不会走到这里
    return hits + 1, total + 1, slots


class RuleRouter:
    """规则引擎 / 逻辑树路由层。"""

    name = "rules.tree"

    def __init__(self, min_confidence: float = 0.5) -> None:
        self.min_confidence = min_confidence
        self.tree = self._build_tree()

    # ---- 建树 ------------------------------------------------------------
    def _build_tree(self) -> list[Branch]:
        def ctx_slot(text: str) -> dict[str, Any]:
            return {"_normalized": normalize(text)}

        def city_slot(text: str) -> dict[str, Any]:
            city = extract_city(text)
            return {"city": city} if city else {}

        def expr_slot(text: str) -> dict[str, Any]:
            expr = extract_expression(text)
            return {"expression": expr} if expr else {}

        def number_slot(text: str) -> dict[str, Any]:
            nums = _NUM_RE.findall(text)
            return {"numbers": nums} if nums else {}

        branches = [
            # ---- 安全闸:命中硬红线就判成拒答(排第一,别的分支不抢)--------
            Branch(
                intent=Intent.SAFETY,
                evidence_texts={
                    "门:命中安全红线": "文本命中 pack.json 里 safety.refuse 的规则",
                },
                node=TreeNode(
                    "safety",
                    lambda t: assess_safety(t).level == "refuse",
                    [TreeNode("hit", lambda t: True, leaf=True, slot_extractor=ctx_slot)],
                ),
            ),
            Branch(
                intent=Intent.WEATHER,
                evidence_texts={"门:出现天气词": "命中天气类关键词", "证据:抽到城市": "从文本里认出一个城市名"},
                node=TreeNode(
                    "weather",
                    lambda t: _contains_any(t, WEATHER_WORDS),
                    [
                        TreeNode("city-known", lambda t: extract_city(t) is not None,
                                 leaf=True, slot_extractor=city_slot),
                        TreeNode("forecast-word", lambda t: _contains_any(t, ("预报", "明天", "后天", "未来", "这几天")),
                                 leaf=True),
                    ],
                ),
            ),
            Branch(
                intent=Intent.CALC,
                evidence_texts={"门:出现计算词或表达式": "命中计算类特征", "证据:能抽出表达式": "抽到一个合法算术表达式"},
                node=TreeNode(
                    "calc",
                    lambda t: _contains_any(t, CALC_WORDS) or extract_expression(t) is not None,
                    [
                        TreeNode("expr-found", lambda t: extract_expression(t) is not None,
                                 leaf=True, slot_extractor=expr_slot),
                        TreeNode("explicit-ask", lambda t: _contains_any(t, ("计算", "算一下", "算算", "等于多少")),
                                 leaf=True),
                    ],
                ),
            ),
            Branch(
                intent=Intent.CONVERT,
                evidence_texts={"门:出现换算词": "命中换算类关键词", "证据:带数值": "文本里带数字"},
                node=TreeNode(
                    "convert",
                    lambda t: _contains_any(t, CONVERT_WORDS) and not _contains_any(t, ("天气",)),
                    [
                        TreeNode("has-number", lambda t: bool(_NUM_RE.search(t)), leaf=True, slot_extractor=number_slot),
                        TreeNode("has-unit", lambda t: _has_any_unit(t), leaf=True),
                    ],
                ),
            ),
            # ---- 新增:比较 -------------------------------------------------
            Branch(
                intent=Intent.COMPARE,
                evidence_texts={"门:出现比较词/符号": "命中比较类特征", "证据:有两个量": "文本里至少两个数值"},
                node=TreeNode(
                    "compare",
                    lambda t: bool(COMPARE_GATE_RE.search(t)) or _contains_any(t, COMPARE_SIGNAL_WORDS),
                    [
                        TreeNode("two-numbers", lambda t: len(_NUM_RE.findall(t)) >= 2, leaf=True),
                        TreeNode("compare-word", lambda t: _match_compare_word(t) is not None, leaf=True),
                    ],
                ),
            ),
            # ---- 新增:布尔式 -----------------------------------------------
            Branch(
                intent=Intent.LOGIC,
                evidence_texts={"门:出现逻辑连接词": "且/或/并非/如果 这类词", "证据:带真假询问": "在问『是不是真的』"},
                node=TreeNode(
                    "logic",
                    lambda t: (
                        _contains_any(t, LOGIC_SIGNAL_WORDS)
                        or bool(LOGIC_CONNECTIVE_RE.search(t))
                        or bool(CONDITIONAL_RE.search(t))
                        or _is_weather_fact(t)
                    ),
                    [
                        TreeNode("connective", lambda t: bool(LOGIC_CONNECTIVE_RE.search(t)), leaf=True),
                        TreeNode("truth-ask", lambda t: _contains_any(t, ("吗", "真的", "成立", "真假", "是不是", "是否")),
                                 leaf=True),
                    ],
                ),
            ),
            # ---- 新增:词向量算术 / 语义邻居 -------------------------------
            Branch(
                intent=Intent.SEMANTIC,
                evidence_texts={
                    "门:出现向量算式": "A - B + C / A减去B加上C 这种写法",
                    "证据:出现语义词": "近义词 / 最近的词 / 相似词",
                },
                node=TreeNode(
                    "semantic",
                    lambda t: bool(SEMANTIC_ARITH_RE.search(t)) or _contains_any(t, SEMANTIC_WORDS),
                    [
                        TreeNode("arith-shape", lambda t: bool(SEMANTIC_ARITH_RE.search(t)), leaf=True),
                        TreeNode("neighbor-ask", lambda t: _contains_any(t, SEMANTIC_WORDS), leaf=True),
                    ],
                ),
            ),
            # ---- 新增:事实推理 ---------------------------------------------
            Branch(
                intent=Intent.INFER,
                evidence_texts={"门:出现断言/归属结构": "A 属于/是/在 B 的句式", "证据:带上疑问或直接陈述": "问它成不成立,或者就是一句话断言"},
                node=TreeNode(
                    "infer",
                    lambda t: _looks_like_infer(t),
                    [
                        TreeNode("ask-shape", lambda t: _contains_any(t, ("吗", "呢", "?", "？", "成不成立", "对不对")),
                                 leaf=True),
                        TreeNode("assert-shape", lambda t: _contains_any(t, INFER_SIGNAL_WORDS) or _is_conditional(t),
                                 leaf=True),
                    ],
                ),
            ),
            Branch(
                intent=Intent.TIME,
                evidence_texts={"门:出现时间词": "命中时间类关键词", "证据:不是问天气的": "与天气意图不冲突"},
                node=TreeNode(
                    "time",
                    lambda t: _contains_any(t, TIME_WORDS),
                    [
                        TreeNode("nowish", lambda t: _contains_any(t, ("现在", "几点", "当前")), leaf=True),
                        TreeNode("dateish", lambda t: _contains_any(t, ("日期", "几号", "星期", "周几", "礼拜")), leaf=True),
                    ],
                ),
            ),
            Branch(
                intent=Intent.HELP,
                evidence_texts={"门:出现求助词": "命中能力询问特征"},
                node=TreeNode(
                    "help",
                    lambda t: _contains_any(t, HELP_WORDS),
                    [
                        TreeNode("self-intro", lambda t: _contains_any(t, ("你是谁", "你叫什么")), leaf=True),
                        TreeNode("capability", lambda t: _contains_any(t, ("能做", "功能", "帮助", "怎么用")), leaf=True, slot_extractor=ctx_slot),
                    ],
                ),
            ),
            Branch(
                intent=Intent.GREETING,
                evidence_texts={"门:出现寒暄词": "命中问候语", "证据:句子很短": "短句更像寒暄"},
                node=TreeNode(
                    "greeting",
                    lambda t: _contains_any(t.lower(), GREETING_WORDS),
                    [
                        TreeNode("short", lambda t: len(t) <= 12, leaf=True),
                        TreeNode("no-question", lambda t: not _contains_any(t, QUESTION_WORDS), leaf=True, slot_extractor=ctx_slot),
                    ],
                ),
            ),
        ]

        # ---- 领域问答:兜底的第一条路(查本地知识条目,不接模型)------------
        # 门用领域包的关键词/条目关键词;门一过,这条分支的得分天然最高,
        # 于是领域内的问题先被它接住 —— 而不是先被"越界"分支接住。
        domain = Branch(
            intent=Intent.DOMAIN,
            evidence_texts={
                "门:命中领域词/条目关键词": "这道题落在这个包的知识范围里",
                "证据:把题面带进检索": "问题原文交给知识条目检索",
            },
            node=TreeNode(
                "domain",
                lambda t: PACK.matches(t),
                [
                    TreeNode("topic", lambda t: bool(t), leaf=True, slot_extractor=ctx_slot),
                ],
            ),
        )
        # ---- 越界:兜底的第二条路(如实说不是我的领域)--------------------
        # 门开得很宽(是句自然语言),所以别的分支都不命中时它才胜出。
        offtopic = Branch(
            intent=Intent.OFFTOPIC,
            evidence_texts={"门:是自然语言": "输入长度足够", "证据:有问句特征": "含问号或疑问词"},
            node=TreeNode(
                "offtopic",
                lambda t: len(t) >= 4,
                [
                    TreeNode("question-shape", lambda t: _contains_any(t, QUESTION_MARKS) or _contains_any(t, QUESTION_WORDS),
                             leaf=True),
                    TreeNode("chinese", lambda t: bool(re.search(r"[\u4e00-\u9fff]", t)), leaf=True, slot_extractor=ctx_slot),
                ],
            ),
        )
        branches.append(domain)
        branches.append(offtopic)
        return branches

    # ---- 路由主流程 -------------------------------------------------------
    def route(self, ctx: Context) -> RouteDecision:
        started = time.perf_counter()
        text = normalize(ctx.raw_text)
        scores: dict[str, float] = {}
        matched: dict[str, tuple[float, list[str], dict[str, Any], str]] = {}

        for branch in self.tree:
            path: list[str] = []
            hits, total, slots = _walk(branch.node, text, path)
            # 只过了"门"、没有任何证据叶子命中 -> 不算这条分支匹配。
            # 否则「对比度」里的"比"会把 COMPARE 刷到 1.0(实测踩到)。
            score = (hits / total) if total >= 2 else 0.0
            scores[branch.intent.value] = round(score, 3)
            if score > 0:
                matched[branch.intent.value] = (score, path, slots, branch.intent.value)

        if matched:
            best_intent, (best_score, best_path, best_slots, _) = max(
                matched.items(),
                key=lambda kv: (kv[1][0], -_intent_rank(kv[0])),
            )
            ranked = sorted((v[0] for v in matched.values()), reverse=True)
            runner_up = ranked[1] if len(ranked) > 1 else 0.0
            # 置信度:绝对得分打底,与亚军的差距做加成(避免两条分支贴平时盲选)
            confidence = min(1.0, best_score * 0.75 + max(0.0, best_score - runner_up) * 0.5 + 0.1)
            intent = Intent(best_intent)
        else:
            best_score, best_path, best_slots = 0.0, [], {}
            confidence = 0.0
            intent = Intent.UNKNOWN

        if confidence < self.min_confidence and intent not in (Intent.UNKNOWN, Intent.DOMAIN, Intent.OFFTOPIC):
            # 低置信不硬猜:落回"领域问答 / 越界"这两条兜底
            if len(text) < 4:
                intent = Intent.UNKNOWN
            else:
                intent = Intent.DOMAIN if PACK.matches(text) else Intent.OFFTOPIC

        decision = RouteDecision(
            intent=intent,
            confidence=round(confidence, 3),
            matched_rule=".".join(best_path) if best_path else None,
            path=best_path,
            scores=scores,
            slots_hint=best_slots,
        )

        elapsed = (time.perf_counter() - started) * 1000
        ctx.route = decision
        ctx.record(
            Stage.ROUTE,
            self.name,
            ok=intent is not Intent.UNKNOWN,
            detail=(
                f"intent={intent.value} conf={decision.confidence} "
                f"rule={decision.matched_rule or '-'} text={truncate(text, 40)!r}"
            ),
            elapsed_ms=elapsed,
            scores=scores,
            path=best_path,
        )
        return decision


# --------------------------------------------------------------------------
# 城市抽取留在这里(它依赖词表);算式/数值抽取在 rules.extract。
# --------------------------------------------------------------------------
def extract_city(text: str) -> str | None:
    """从文本里认城市:先用词表,再用『XX市 / XX的天气』这种后缀模式兜。"""
    for city in CITIES:
        if city in text:
            return city
    m = _CITY_SUFFIX_RE.search(text)
    if m:
        candidate = m.group(1)
        if candidate and candidate not in WEATHER_WORDS and 2 <= len(candidate) <= 6:
            return candidate
    return None

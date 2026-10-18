"""自然语言 → 逻辑结构。

三件事,都在这里:
  1. parse_logic  :"北京属于华北 且 上海属于南方吗" -> And(Fact, Fact) 树
  2. parse_fact    :"北京属于华北" -> ("属于", ("北京","华北"))  —— 入库用
  3. split_clauses :按逻辑连接词把一句话拆成子句(带连接词信息)

解析原则:**宁可解析失败,不要解析出一个错的树**。
失败由上层转成"没听懂,请你换个说法",而不是拿错误的树去推理。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..rules.lexicon import AND_WORDS, OR_WORDS, ZERO_ARY_PREDICATES
from .expr import And, Expr, Fact, Not, Or, to_text

if TYPE_CHECKING:                                   # 只为类型标注,避免运行期循环 import
    from .reasoner import FactStore, QueryResult

# 结束用的语气词/标点,解析前剥掉
TRAILING_RE = re.compile(r"[\s?？。!！,，]+$")
# 逻辑式开头的"请问/我想知道"这类客套
LEADING_RE = re.compile(r"^(请问|我想知道|我想问|帮我看看|帮我判断|判断一下|验证一下|看看)+")

# 谓词动词:出现这些词就说明这是一句"事实断言"而不是随便一句话
PREDICATE_VERBS: tuple[tuple[str, str], ...] = (
    ("属于", "属于"), ("是", "是"), ("在", "城市在"),
    ("包括", "包括"), ("包含", "包括"), ("具有", "具有"), ("有", "有"),
    ("可以", "可以"), ("能", "可以"), ("会", "会"), ("需要", "需要"),
)


@dataclass
class Clause:
    """拆出来的一个子句:文本 + 它前面的连接词。"""

    text: str
    connective: str = ""       # and / or / not / ""(第一个子句)


@dataclass
class ParseOutcome:
    ok: bool
    expr: Expr | None = None
    reason: str = ""
    unknown_words: list[str] = field(default_factory=list)


def _clean(text: str) -> str:
    """剥掉客套开头与疑问尾巴。

    关键细节:**先把空格去掉再剥尾巴**。否则 "小明没伞 吗"(子句切分后
    经常带一个空格)会因为尾巴不是紧贴标点而剥不掉,表现成整句解析失败。
    """
    out = LEADING_RE.sub("", text.strip())
    out = out.replace(" ", "").replace("\t", "")
    out = TRAILING_RE.sub("", out)
    return out.strip()


def split_clauses(text: str) -> list[Clause]:
    """按且/与/或/非把一句中文拆成子句。

    用**最长连接词优先**切分("并且" 不能先被 "且" 切走)。
    """
    cleaned = _clean(text)
    tokens = sorted(set(AND_WORDS + OR_WORDS), key=len, reverse=True)
    pattern = "|".join(re.escape(t) for t in tokens)
    parts = re.split(f"({pattern})", cleaned)

    clauses: list[Clause] = []
    pending = ""
    for part in parts:
        if not part:
            continue
        if part in AND_WORDS:
            pending = "and"
            continue
        if part in OR_WORDS:
            pending = "or"
            continue
        clauses.append(Clause(text=part.strip(), connective=pending))
        pending = ""
    return [c for c in clauses if c.text]


def parse_fact(text: str) -> tuple[str, tuple[str, ...]] | None:
    """把一句断言解析成事实元组;解析不出来返回 None。

    已知形态(够用即止,不追求覆盖全部中文):
        北京属于华北          -> ("属于", ("北京", "华北"))
        北京是城市            -> ("是", ("北京", "城市"))
        苏格拉底是人类        -> ("是", ("苏格拉底", "人类"))
        鸟具有翅膀            -> ("具有", ("鸟", "翅膀"))
        北京下雨 / 北京 下雨   -> ("下雨", ("北京",))
        小明有伞 / 小明有伞吗  -> ("有", ("小明", "伞"))
        麻雀会飞吗            -> ("具有", ("麻雀", "会飞"))
    """
    cleaned = _clean(text)
    cleaned = re.sub(r"^(那|那么|则|就)", "", cleaned).strip()
    # "为什么/为啥/为何" 前置问法:剥掉之后才是真正的事实句
    cleaned = re.sub(r"^(?:为什么|为啥|为何|怎么做到)", "", cleaned).strip()
    # 事实句里常带疑问尾巴与语气词:统一去掉,否则 "北京下雨吗" 解析不出来
    cleaned = re.sub(r"(吗|呢)$", "", cleaned).strip()
    cleaned = cleaned.replace(" ", "")
    if not cleaned:
        return None

    # 属性句必须**先**试:否则 "麻雀会飞" 会被归属句的动词表切成 麻雀/会/飞。
    # 这是顺序问题,不是正则问题 —— 顺序错了,再精确的正则也救不回来。
    prop = parse_property(cleaned)
    if prop is not None:
        return prop

    # 形态 0:情态 + 动作 —— "苹果可以吃" / "食物能吃" / "麻雀会飞"
    # 必须排在归属句之前:否则 "可以吃" 会被切成 可以(苹果,吃) 而不是 能族。
    modal = MODAL_ACTION_RE.match(cleaned)
    if modal:
        return ("能", (modal.group(1).strip(), modal.group(2)))

    # 形态 1:A 属于/是/在 B(动词最长优先;刻意不含单字"会",免得抢走"会飞")
    # "是不是" 必须排在 "是" 前面,否则会被切成 是(A, 不是B)。
    m = re.match(
        r"^(.{1,12}?)(属于|是不是|是否|是|在|具有|有|包括|包含|需要|可以)(.{1,12})$", cleaned
    )
    if m:
        subject, verb, obj = m.group(1), m.group(2), m.group(3)
        subject = _strip_article(subject)
        if verb == "属于":
            return ("属于", (subject, obj))
        if verb in ("是", "是不是", "是否"):
            return ("是", (subject, _strip_article(obj)))
        if verb == "在":
            return ("城市在", (subject, obj))
        if verb in ("具有", "有"):
            return ("具有", (subject, obj))
        return (verb, (subject, obj))

    # 形态 2:X 的下雨/下雪 这类状态谓词(允许"的"字与空格)
    m2 = re.match(r"^(.{1,12}?)(下雨|下雪|晴天|阴天|刮风|打雷)$", cleaned)
    if m2:
        subject = _strip_article(m2.group(1))
        if subject:
            return (m2.group(2), (subject,))
        return (m2.group(2), ())

    # 形态 3:光杆状态 —— "下雨了" / "下雨"
    m3 = re.match(r"^(下雨|下雪|晴天|阴天|刮风|打雷)(了)?$", cleaned)
    if m3:
        return (m3.group(1), ())

    return None


def candidates_of(predicate: str) -> list[str]:
    """谓词族展开(对外薄封装,便于测试与复用)。"""
    from .inference import candidates_for

    return candidates_for(predicate)


def _predicate_candidates(predicate: str, subject: str, obj: str) -> list[tuple[str, tuple[str, ...]]]:
    """把『X <谓词> Y』展开成几个可以逐个试的事实。

    展开两个来源:
      1. **谓词族**(inference.GROUPS):『可以』『会』『需要』与『能』等价,
         所以问『苹果可以吃吗』能找到存储用的 ("能", ("苹果","吃"));
      2. **粒度**(区划专用):同一个"属于"在城市/地区/个体三种粒度上
         分别落在 城市在 / 地区属于 / 是 上,按具体到一般逐个试。

    事实库内部用规范谓词存,**提问者不该知道内部谓词名** ——
    这个函数就是那道翻译层。
    """
    from .inference import canonical

    group_forms = [canonical(p) for p in candidates_of(predicate)]

    if canonical(predicate) in ("属于", "是"):
        # 区划/类别的多粒度展开:具体 -> 一般
        multi = [("城市在", (subject, obj)), ("是", (subject, obj)),
                 ("直辖市属于", (subject, obj)), ("地区属于", (subject, obj)),
                 ("属于", (subject, obj))]
        return list(dict.fromkeys(multi + [(p, (subject, obj)) for p in group_forms]))

    ordered = [(p, (subject, obj)) for p in group_forms]
    ordered.append(("具有", (subject, obj)))          # 性质族的兜底
    ordered.append(("有", (subject, obj)))
    return list(dict.fromkeys(ordered))


def ask_across_predicates(
    store: "FactStore", predicate: str, subject: str, obj: str
) -> "QueryResult":
    """按候选谓词顺序查询,返回**第一个成立**的;都不成立则返回最后一个的说明。

    注意"成立的优先,其次是给出一条有依据的链":派生事实也要能被问到,
    所以这里用 query() 而不是直接查 store.facts。
    """
    from .reasoner import query as _query

    last: QueryResult | None = None
    for cand_predicate, args in _predicate_candidates(predicate, subject, obj):
        result = _query(store, cand_predicate, *args)
        if result.holds:
            return result
        last = result
    assert last is not None
    return last


PROPERTY_VERBS: tuple[tuple[str, str, str], ...] = (
    # (字面形态, 归一谓词, 值)
    # "会飞/会游泳"是**能力**,归一到 能 族;这样它与事实库里的 ("能", ("鸟","飞")) 对得上。
    # 早先一律归到「具有」,于是"麻雀会飞吗"会去查 具有(麻雀,会飞) 而答"不成立"(实测踩到)。
    ("会飞", "能", "飞"),
    ("会游泳", "能", "游泳"),
    ("会跑", "具有", "会跑"),
    ("有翅膀", "具有", "翅膀"),
    ("有伞", "有", "伞"),
    ("含维生素", "具有", "含维生素"),
    ("带伞", "具有", "带伞"),
)

# 情态动词 + 动作:"可以吃 / 能吃 / 会飞 / 需要带伞"
# 归一到 能 族(见 inference.CANONICAL),这样"苹果可以吃吗"能查到 能(苹果,吃)。
ACTION_VERBS = ("吃", "喝", "切开", "生长", "飞", "跑", "游泳", "说话", "看", "听",
                "出门", "带伞", "下蛋", "产奶", "打鸣", "哺乳", "游水", "蹦跳", "爬树")
MODAL_VERBS = ("可以", "能", "会", "需要")
MODAL_ACTION_RE = re.compile(
    r"^(.{1,12}?)(?:" + "|".join(MODAL_VERBS) + r")(" + "|".join(ACTION_VERBS) + r")$"
)


def parse_property(text: str) -> tuple[str, tuple[str, ...]] | None:
    """解析"A 会飞 / A 有翅膀 / A 含维生素"这类**属性**断言。

    与 parse_fact 分开,是因为这些句式里的动词不是"是/属于",
    硬塞进归属句的正则会互相冲突(试过,会把"麻雀会飞"切成 麻雀/会/飞)。
    """
    cleaned = _clean(text)
    cleaned = re.sub(r"(吗|呢)$", "", cleaned).strip().replace(" ", "")
    if not cleaned:
        return None
    for surface, predicate, prop in sorted(PROPERTY_VERBS, key=lambda kv: len(kv[0]), reverse=True):
        if cleaned.endswith(surface) and len(cleaned) > len(surface):
            subject = _strip_article(cleaned[: -len(surface)])
            if not subject:
                return None
            return (predicate, (subject, prop))
    return None


_ARTICLES = ("的", "了")


def _strip_article(text: str) -> str:
    for suffix in _ARTICLES:
        if text.endswith(suffix) and len(text) > len(suffix):
            text = text[: -len(suffix)]
    return text.strip()


def _looks_like_fact(text: str) -> bool:
    return parse_fact(text) is not None


def parse_logic(text: str) -> ParseOutcome:
    """解析布尔式。返回树或失败原因。"""
    clauses = split_clauses(text)
    clauses = [c for c in clauses if c.text]
    if not clauses:
        return ParseOutcome(ok=False, reason="这句里没有可判真假的原子")

    nodes: list[tuple[str, Expr]] = []
    unknown: list[str] = []
    for clause in clauses:
        node, negated, bad = _clause_to_expr(clause.text)
        if node is None:
            unknown.append(clause.text)
            continue
        if negated:
            node = Not(node)
        nodes.append((clause.connective, node))

    if not nodes:
        return ParseOutcome(ok=False, reason="子句里没有识别出可判真假的事实",
                            unknown_words=unknown)
    if unknown:
        return ParseOutcome(ok=False, reason="有子句没解析成事实,不猜",
                            unknown_words=unknown)

    expr = nodes[0][1]
    for connective, node in nodes[1:]:
        expr = And(expr, node) if connective != "or" else Or(expr, node)
    return ParseOutcome(ok=True, expr=expr)


def _clause_to_expr(text: str) -> tuple[Expr | None, bool, str]:
    """子句 -> (表达式, 是否取反, 失败原因)。

    先剥疑问尾巴(『小明没伞 吗』的"吗"在切分后常带空格),
    再判否定 —— 顺序反了会把"伞吗"当成宾语。
    """
    cleaned = re.sub(r"[\s\u3000]+", "", text).strip()
    cleaned = re.sub(r"(吗|呢)$", "", cleaned).strip()
    cleaned = cleaned.strip("?？。!！,，")
    if not cleaned:
        return None, False, "空子句"

    # 形态一:否定词在**中间** —— "小明没伞" / "小明没有伞"
    # 只认后面是一到三个汉字的情况,避免把 "没有钱" 这种动宾结构切错。
    m = re.match(r"^(.{1,12}?)(?:没有|没)([\u4e00-\u9fff]{1,3})$", cleaned)
    if m:
        subject, obj = m.group(1).strip(), m.group(2).strip()
        if obj not in ("了", "有", "是"):
            predicate = "有" if obj == "伞" else "具有"
            return Fact(predicate, (subject, obj)), True, ""

    negated = False
    # 形态二:否定词在**开头** —— "并非北京下雨" / "不是小明有伞"
    for word in ("并非", "不是", "没有", "不", "没"):
        if cleaned.startswith(word) and len(cleaned) > len(word):
            negated = True
            cleaned = cleaned[len(word):].strip()
            break

    # 属性断言(会飞 / 有翅膀 / 含维生素)优先于归属句,避免被切错
    prop = parse_property(cleaned)
    if prop is not None:
        return Fact(prop[0], prop[1]), negated, ""

    fact = parse_fact(cleaned)
    if fact is not None:
        return Fact(fact[0], fact[1]), negated, ""

    # 零元谓词只认白名单:否则 "今天心情不错" 这类普通句子会被当成一个
    # 永远查不到的原子,进而被判成"假" —— 那是把"没听懂"说成"我判它为假"。
    if cleaned in ZERO_ARY_PREDICATES:
        return Fact(cleaned, ()), negated, ""

    return None, negated, f"没解析出事实:{cleaned}"


# --------------------------------------------------------------------------
# 条件句:如果 P 那么 Q
# --------------------------------------------------------------------------
COND_RE = re.compile(r"^(?:如果|假如|若是|要是|若|只要)(.+?)(?:那么|则|就|便|的话)(.*)$")


@dataclass
class Conditional:
    premise_text: str
    conclusion_text: str
    premise: Expr | None = None
    conclusion: tuple[str, tuple[str, ...]] | None = None


def parse_conditional(text: str) -> Conditional | None:
    cleaned = _clean(text)
    m = COND_RE.match(cleaned)
    if not m:
        return None
    premise_text, conclusion_text = m.group(1).strip(), m.group(2).strip()
    cond = Conditional(premise_text=premise_text, conclusion_text=conclusion_text)
    outcome = parse_logic(premise_text)
    if outcome.ok:
        cond.premise = outcome.expr
    cond.conclusion = parse_fact(conclusion_text)
    return cond


def describe_expr(expr: Expr | None) -> str:
    return to_text(expr) if expr is not None else "(空)"


def _parse_class_question(text: str, subject: str) -> tuple[str, str] | None:
    """『北京是城市吗』这种只有一个参数的归属问句 -> (谓词, 类别)。

    支持两种语序:
        X 是/属于 C   -> 谓词按动词归一,类别是 C
        C 是 X        -> 反序:类别是 C(如"城市是北京吗"这种口语少,但兜一下)
    """
    cleaned = _clean(text)
    cleaned = re.sub(r"(吗|呢)$", "", cleaned).strip()
    for verb, predicate in (("属于", "属于"), ("是", "是"), ("在", "城市在")):
        for pattern in (
            rf"^{re.escape(subject)}{verb}(.+)$",
            rf"^(.+){verb}{re.escape(subject)}$",
        ):
            m = re.match(pattern, cleaned)
            if m:
                other = m.group(1).strip()
                if other and other != subject:
                    return predicate, other
    return None

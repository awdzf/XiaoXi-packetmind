"""分解:把一句话拆成关键词,并为错字准备候选。

它做两件事,都**不接模型**:

1. **切词**:ASCII 串(HTML / Java / 3.14)整块留下;中文按最长匹配切,
   匹配不到的字连成一块(所以「引立波」不会被切碎)。
   例:HTML是什么? -> HTML / 是 / 什么

2. **造候选**(给打错字的用户留活路):
   · 汉字顺序的排列组合(2–4 字,最多 24 个);
   · 内置谐音表把字换成同音/近音字(如 力→立、波→播、场→厂);
   · 谐音变体再做一次排列(3 字以内);
   · ASCII 词的相邻字母互换(HTML -> HMTL),路由器还会按编辑距离兜一层。

候选是**有限且标了来源的**:每条候选带 kind(base / perm / homophone / ascii_typo / full),
查找时逐条尝试,总结里能说清"是哪个候选命中的"。不假装穷尽所有错法 ——
排列组合与谐音表都有上限,写在 notes 里。
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass

from .catalog import CATALOG, ExpertCard

CJK = r"[\u4e00-\u9fff]"
_CJK_RE = re.compile(CJK)
_ASCII_RUN = re.compile(r"[A-Za-z0-9]+(?:[+#.\-][A-Za-z0-9]+)*")

# 功能词/疑问词:保留给"看得见的拆解",但不参与造候选(避免排列爆炸),
# 也不算关键词(上下文记忆只记内容词)。
PARTICLES: frozenset[str] = frozenset({
    "是", "的", "了", "吗", "呢", "和", "与", "或", "在", "有", "能", "会",
    "要", "把", "被", "对", "从", "到", "为", "以", "及", "也", "都", "就",
    "而", "但", "这", "那", "个", "些", "之", "于", "其", "吧", "啊", "呀",
    "还", "再", "又", "很", "太", "更", "最", "不", "没", "请", "帮", "该",
    "却", "才", "只", "已经", "一些", "有点", "真的", "到底", "究竟",
})

CORE_WORDS: frozenset[str] = frozenset({
    "什么", "怎么", "怎样", "如何", "为什么", "为何", "是否", "可以", "能否",
    "多少", "哪个", "哪些", "区别", "关系", "原理", "定义", "意思", "作用",
    "特点", "优点", "缺点", "例子", "基础", "概念", "包括", "属于", "介绍",
    "解释", "说明", "方法", "方式", "以及", "如果", "那么", "因为", "所以",
    "专家", "回答", "问题", "知识", "领域", "数据包",
})

#: 代词/指代:说明"接着上一句",本身不是内容 —— 不算关键词
PRONOUNS: frozenset[str] = frozenset({
    "它", "他", "她", "我", "你", "您", "我们", "你们", "他们", "她们", "它们",
    "这个", "那个", "这些", "那些", "这里", "那里", "这样", "那样",
    "上面", "下面", "刚才", "前面", "后面", "还是", "然后", "现在",
})

# 内置谐音表:字 -> 常见同音/近音字(只收与各包知识相关的字,不求全)
HOMOPHONES: dict[str, tuple[str, ...]] = {
    "力": ("立", "利", "历"),
    "场": ("厂", "长", "常"),
    "波": ("播", "拨", "博"),
    "熵": ("商", "伤", "汤"),
    "量": ("亮", "良", "两"),
    "矩": ("举", "句", "巨"),
    "阵": ("镇", "振", "真"),
    "微": ("威", "危", "违"),
    "积": ("机", "基", "击"),
    "分": ("芬", "纷", "份"),
    "概": ("盖", "钙", "溉"),
    "率": ("律", "绿", "虑"),
    "群": ("裙", "群", "郡"),
    "图": ("途", "涂", "徒"),
    "论": ("轮", "伦", "沦"),
    "函": ("含", "寒", "韩"),
    "数": ("树", "术", "束"),
    "学": ("雪", "穴", "血"),
    "共": ("公", "工", "功"),
    "情": ("晴", "清", "请"),
    "感": ("敢", "赶", "杆"),
    "焦": ("交", "教", "胶"),
    "虑": ("绿", "律", "旅"),
    "边": ("编", "变", "遍"),
    "界": ("介", "借", "届"),
    "依": ("医", "衣", "一"),
    "恋": ("连", "联", "练"),
    "态": ("太", "泰", "汰"),
    "绪": ("序", "续", "絮"),
    "伦": ("轮", "论", "沦"),
    "理": ("里", "礼", "离"),
    "道": ("到", "倒", "盗"),
    "德": ("得", "的", "灯"),
    "隐": ("引", "饮", "印"),
    "私": ("思", "丝", "司"),
    "偏": ("篇", "骗", "片"),
    "见": ("建", "件", "间"),
    "算": ("蒜", "酸", "栓"),
    "法": ("发", "罚", "乏"),
    "公": ("工", "功", "攻"),
    "平": ("评", "凭", "瓶"),
    "透": ("投", "头", "抖"),
    "明": ("名", "鸣", "铭"),
    "尊": ("遵", "蹲", "樽"),
    "严": ("言", "研", "盐"),
    "义": ("意", "议", "艺"),
    "诚": ("成", "城", "承"),
    "信": ("心", "新", "欣"),
    "关": ("观", "官", "管"),
    "系": ("细", "戏", "西"),
    "定": ("订", "顶", "丁"),
    "因": ("音", "阴", "引"),
    "果": ("国", "过", "裹"),
    "证": ("整", "正", "政"),
    "据": ("剧", "句", "巨"),
    "假": ("架", "价", "驾"),
    "设": ("社", "舍", "射"),
    "计": ("记", "机", "既"),
    "编": ("边", "变", "遍"),
    "程": ("成", "城", "乘"),
    "序": ("绪", "续", "需"),
    "语": ("雨", "与", "玉"),
    "文": ("闻", "问", "温"),
    "存": ("村", "寸", "蹲"),
    "储": ("出", "初", "除"),
    "缓": ("换", "环", "还"),
    "网": ("往", "望", "忘"),
    "络": ("落", "洛", "路"),
    "服": ("福", "浮", "符"),
    "务": ("物", "午", "武"),
    "器": ("气", "汽", "弃"),
    "协": ("写", "谢", "鞋"),
    "议": ("义", "意", "艺"),
    "密": ("秘", "蜜", "觅"),
    "码": ("马", "妈", "骂"),
    "病": ("并", "冰", "饼"),
    "毒": ("独", "读", "肚"),
    "炸": ("诈", "榨", "乍"),
    "弹": ("谈", "淡", "蛋"),
    "枪": ("强", "墙", "抢"),
    "杀": ("沙", "纱", "傻"),
    "残": ("参", "餐", "惭"),
    "轻": ("清", "青", "倾"),
    "生": ("声", "升", "胜"),
    "危": ("威", "微", "违"),
    "险": ("显", "先", "现"),
    "犯": ("饭", "范", "翻"),
    "罪": ("最", "醉", "嘴"),
    "欺": ("期", "七", "妻"),
    "骗": ("片", "偏", "篇"),
    "控": ("空", "恐", "孔"),
    "药": ("要", "摇", "咬"),
    "剂": ("计", "记", "季"),
    "诊": ("真", "针", "枕"),
    "断": ("段", "短", "锻"),
    "咨": ("姿", "资", "紫"),
    "询": ("寻", "巡", "旬"),
    "治": ("志", "至", "制"),
    "疗": ("聊", "料", "辽"),
    "压": ("鸭", "押", "涯"),
    "爱": ("艾", "碍", "矮"),
    "婚": ("昏", "浑", "魂"),
    "需": ("须", "徐", "许"),
    "要": ("药", "摇", "耀"),
    "求": ("球", "秋", "囚"),
    "助": ("住", "注", "柱"),
    "陪": ("培", "赔", "佩"),
    "伴": ("半", "办", "扮"),
}

MAX_PERM_LEN = 4
MAX_PERM_TOKENS = 24
MAX_HOMOPHONE_VARIANTS = 12
MAX_TOTAL_CANDIDATES = 240

# 反向谐音表:用户把"力"写成"立"时,也要能从"立"找回"力"
HOMOPHONE_REVERSE: dict[str, tuple[str, ...]] = {}
for _canonical, _alts in HOMOPHONES.items():
    for _alt in _alts:
        HOMOPHONE_REVERSE.setdefault(_alt, ())
        if _canonical not in HOMOPHONE_REVERSE[_alt]:
            HOMOPHONE_REVERSE[_alt] = HOMOPHONE_REVERSE[_alt] + (_canonical,)


def normalize(text: str) -> str:
    """全角转半角、压缩空白。框架不依赖核心包,所以自带一份。"""
    if not text:
        return ""
    out: list[str] = []
    for ch in text:
        code = ord(ch)
        if code == 0x3000:
            out.append(" ")
        elif 0xFF01 <= code <= 0xFF5E:
            out.append(chr(code - 0xFEE0))
        else:
            out.append(ch)
    return re.sub(r"\s+", " ", "".join(out)).strip()


@dataclass(frozen=True)
class Candidate:
    """一条交给查找尝试的候选。"""

    text: str
    kind: str          # base / perm / homophone / ascii_typo / full
    origin: str        # 从哪个 token 变来的(全句候选为空)

    def label(self) -> str:
        return f"{self.text}[{self.kind}]"


@dataclass(frozen=True)
class Decomposition:
    original: str
    tokens: tuple[str, ...]
    candidates: tuple[Candidate, ...]
    notes: tuple[str, ...] = ()

    @property
    def content_tokens(self) -> tuple[str, ...]:
        return tuple(t for t in self.tokens if t not in PARTICLES)

    @property
    def keywords(self) -> tuple[str, ...]:
        """这一步拆出来的**非常用词** —— 也是要记住的东西。

        判据只有一条:**不是功能词、不是疑问词**。
        「引力波是什么?」拆成 引力波 / 是 / 什么 —— 是 与 什么 是常用的,
        关键词只有「引力波」;「向量场和电场的关系是什么?」的关键词是
        向量场 / 电场(和、的、关系 都是常用词)。

        上下文记忆、以及"借上一轮的关键词再查一次"用的就是这个结果。
        """
        return keywords_of(self.tokens)


class Decomposer:
    """规则切词 + 有限候选生成。"""

    def __init__(self, cards: tuple[ExpertCard, ...] = CATALOG) -> None:
        vocab: set[str] = set(CORE_WORDS)
        for card in cards:
            vocab.update(t for t in card.scope if _CJK_RE.search(t) and len(t) >= 2)
            for entry in card.entries:
                for kw in entry.get("keywords") or ():
                    kw = str(kw)
                    if _CJK_RE.search(kw) and len(kw) >= 2:
                        vocab.add(kw)
        self.vocab = frozenset(vocab)
        self.max_word = max((len(w) for w in vocab), default=8)

    # ---- 切词 ------------------------------------------------------------
    def tokenize(self, text: str) -> list[str]:
        norm = normalize(text)
        tokens: list[str] = []
        cjk: list[str] = []
        ascii_buf: list[str] = []

        def flush_ascii() -> None:
            if ascii_buf:
                tokens.append("".join(ascii_buf))
                ascii_buf.clear()

        def flush_cjk() -> None:
            if cjk:
                tokens.extend(self._segment("".join(cjk)))
                cjk.clear()

        for ch in norm:
            if ch.isascii() and (ch.isalnum() or ch in "+#.-"):
                flush_cjk()
                ascii_buf.append(ch)
            elif _CJK_RE.match(ch):
                flush_ascii()
                cjk.append(ch)
            else:
                flush_ascii()
                flush_cjk()
        flush_ascii()
        flush_cjk()
        return [t for t in tokens if t]

    def _segment(self, run: str) -> list[str]:
        """最长匹配;匹配不到的字先单放,稍后合并成块。"""
        raw: list[str] = []
        i = 0
        while i < len(run):
            for size in range(min(self.max_word, len(run) - i), 1, -1):
                piece = run[i:i + size]
                if piece in self.vocab:
                    raw.append(piece)
                    i += size
                    break
            else:
                raw.append(run[i])
                i += 1

        merged: list[str] = []
        buf = ""
        for token in raw:
            if len(token) == 1 and token not in PARTICLES:
                buf += token
                if len(buf) >= 6:
                    merged.append(buf)
                    buf = ""
            else:
                if buf:
                    merged.append(buf)
                    buf = ""
                merged.append(token)
        if buf:
            merged.append(buf)
        return merged

    # ---- 候选 ------------------------------------------------------------
    def decompose(self, text: str) -> Decomposition:
        tokens = self.tokenize(text)
        candidates: list[Candidate] = []
        seen: set[str] = set()

        def add(value: str, kind: str, origin: str) -> None:
            value = value.strip()
            if not value or value in seen or len(candidates) >= MAX_TOTAL_CANDIDATES:
                return
            seen.add(value)
            candidates.append(Candidate(value, kind, origin))

        for token in tokens:
            add(token, "base", token)
        add(normalize(text), "full", "")

        for token in tokens:
            if len(candidates) >= MAX_TOTAL_CANDIDATES:
                break
            if token.isascii():
                for variant in _ascii_typos(token):
                    add(variant, "ascii_typo", token)
                continue
            if not (2 <= len(token) <= MAX_PERM_LEN) or token in PARTICLES:
                continue
            for variant in _permutations(token):
                add(variant, "perm", token)
            for variant in _homophones(token):
                add(variant, "homophone", token)
                if len(token) <= 3:
                    for permuted in _permutations(variant):
                        add(permuted, "homophone", token)

        notes = [
            "候选不是穷尽的:排列组合只对 2–4 字、谐音表是内置有限表、总量有上限。",
            f"本次切出 {len(tokens)} 段,生成 {len(candidates)} 条候选交给查找逐条尝试。",
        ]
        return Decomposition(original=text, tokens=tuple(tokens),
                             candidates=tuple(candidates), notes=tuple(notes))


def _permutations(token: str) -> list[str]:
    out: list[str] = []
    for item in itertools.permutations(token):
        value = "".join(item)
        if value != token and value not in out:
            out.append(value)
        if len(out) >= MAX_PERM_TOKENS:
            break
    return out


def _homophones(token: str) -> list[str]:
    out: list[str] = []
    for index, ch in enumerate(token):
        alts = HOMOPHONES.get(ch, ()) + HOMOPHONE_REVERSE.get(ch, ())
        for alt in dict.fromkeys(alts):
            value = token[:index] + alt + token[index + 1:]
            if value != token and value not in out:
                out.append(value)
        if len(out) >= MAX_HOMOPHONE_VARIANTS:
            break
    return out


def _ascii_typos(token: str) -> list[str]:
    """相邻字母互换:HTML -> HMTL;再叠一个大小写不敏感的原词。"""
    out: list[str] = []
    lowered = token.lower()
    for index in range(len(token) - 1):
        chars = list(token)
        chars[index], chars[index + 1] = chars[index + 1], chars[index]
        value = "".join(chars)
        if value not in out:
            out.append(value)
    if lowered != token:
        out.insert(0, lowered)
    return out[:12]


__all__ = [
    "Candidate",
    "Decomposition",
    "Decomposer",
    "normalize",
    "keywords_of",
    "HOMOPHONES",
    "PARTICLES",
    "PRONOUNS",
]


def keywords_of(tokens: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """从切词结果里挑出非常用词(关键词),去重保序。

    只做**一步**判断:这个词是不是功能词/疑问词(PARTICLES / CORE_WORDS)。
    是 -> 丢掉;不是 -> 留下(单字母 ASCII 词例外:太短,记不住也没用)。
    不做词性分析、不接模型 —— 这一步要的是"够用且可解释"。
    """
    picked: list[str] = []
    for token in tokens:
        item = (token or "").strip()
        if not item or item in PARTICLES or item in CORE_WORDS or item in PRONOUNS:
            continue
        if item.isascii() and len(item) < 2:
            continue
        if item not in picked:
            picked.append(item)
    return tuple(picked)


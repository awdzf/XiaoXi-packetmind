"""分解:把一句话拆成关键词,并为错字准备候选。

它做两件事,都**不接模型**:

1. **切词**:先做归一化(NFKC + 繁简 + 去不可见字符),再切 ——
   ASCII 串(HTML / Java / 3.14)整块留下;中文按**双向最长匹配**切,
   两个方向各切一遍,取"没认出来的孤字更少"的那种;匹配不到的字连成一块
   (所以「引立波」不会被切碎)。
   例:HTML是什么? -> HTML / 是 / 什么

2. **造候选**(给打错字的人留活路):
   · 汉字顺序的排列组合(2–4 字,最多 24 个);
   · 内置谐音表把字换成同音/近音字(如 力→立、波→播、场→厂);
   · 形近字替换(如 已↔己、未↔末、场↔厂);
   · 谐音变体再做一次排列(3 字以内);
   · ASCII 词的相邻字母互换(HTML -> HMTL);
   · **词典纠错**:没被词表认出来的词,在词表里找"唯一最像"的那个
     (编辑距离按长度给预算,见 `fuzzy.py`);不唯一就不纠。

候选是**有限且标了来源的**:每条候选带 kind(base / perm / homophone /
confusable / ascii_typo / correct / full),查找时逐条尝试,总结里能说清
"是哪个候选命中的"。不假装穷尽所有错法 —— 排列组合、谐音表、形近表、
纠错候选都有上限,写在 notes 里。
"""

from __future__ import annotations

import itertools
import re
import unicodedata
from dataclasses import dataclass

from .catalog import CATALOG, ExpertCard
from .fuzzy import SHAPES, correct_token, shape_like, typo_budget

CJK = r"[\u4e00-\u9fff]"
_CJK_RE = re.compile(CJK)
_ASCII_RUN = re.compile(r"[A-Za-z0-9]+(?:[+#.\-][A-Za-z0-9]+)*")
#: 不可见字符:零宽空格 / 零宽连接符 / BOM / 软连字符。复制粘贴常带它们,
#: 肉眼看不见却会让"引力波"和"引力波"匹配不上
_INVISIBLE = {ord(ch): None for ch in "\u200b\u200c\u200d\u2060\ufeff\u00ad"}

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
#: 换字类候选(谐音/形近)最长对几个字生效 —— 随长度线性增长,比排列便宜
MAX_SUB_LEN = 6
#: 每个词最多造几个形近变体
MAX_CONFUSABLE_VARIANTS = 8
#: 一次分解最多收几条词典纠错(多了说明这句本来就不是在问知识点)
MAX_CORRECTIONS = 6
MAX_TOTAL_CANDIDATES = 240
#: 超过这个长度的 ASCII 词不再造"相邻字母互换"候选:
#: 每个位置复制整串是 O(n²),粘贴一段日志就会卡住(见 `_ascii_typos`)。
MAX_TYPO_TOKEN_LEN = 40

# 反向谐音表:用户把"力"写成"立"时,也要能从"立"找回"力"
HOMOPHONE_REVERSE: dict[str, tuple[str, ...]] = {}
for _canonical, _alts in HOMOPHONES.items():
    for _alt in _alts:
        HOMOPHONE_REVERSE.setdefault(_alt, ())
        if _canonical not in HOMOPHONE_REVERSE[_alt]:
            HOMOPHONE_REVERSE[_alt] = HOMOPHONE_REVERSE[_alt] + (_canonical,)


def _sound_map() -> dict[str, frozenset[str]]:
    """同音关系拍平成 字 -> 同音字集合(谐音表双向合并,查的时候一次哈希)。

    短词纠错要靠它当"第二道证据":两个字的词只差一个字时,光看字面分不清
    "错字"还是"另一个词",得再有一条"读音相近"的证据才认。
    """
    index: dict[str, set[str]] = {}
    for left, rights in HOMOPHONES.items():
        for right in rights:
            if right == left:
                continue
            index.setdefault(left, set()).add(right)
            index.setdefault(right, set()).add(left)
    return {key: frozenset(value) for key, value in index.items()}


SAME_SOUND: dict[str, frozenset[str]] = _sound_map()


def related_char(left: str, right: str) -> bool:
    """两个字有没有"同音或形近"的关系 —— 短词纠错时的第二道证据。"""
    if left == right:
        return True
    return right in SAME_SOUND.get(left, ()) or shape_like(left, right)


#: 常见繁体字 -> 简体字。用户从浏览器/繁体输入法粘贴过来的问句很常见,
#: 不做这一步的话「電車難題」和「电车难题」会被当成两件事(实测漏命中)。
#: 表只收常见字,不是完整的繁简表:漏掉的繁体字仍然能匹配简体文里的同形字,
#: 只是少一次折叠。NFKC 管全角/兼容字符,这张表专管繁简。
TRADITIONAL: tuple[tuple[str, str], ...] = (
    ("場", "场"), ("學", "学"), ("論", "论"), ("電", "电"), ("難", "难"),
    ("題", "题"), ("車", "车"), ("萬", "万"), ("關", "关"), ("係", "系"),
    ("麼", "么"), ("這", "这"), ("個", "个"), ("來", "来"), ("說", "说"),
    ("時", "时"), ("間", "间"), ("對", "对"), ("開", "开"), ("發", "发"),
    ("網", "网"), ("頁", "页"), ("設", "设"), ("計", "计"), ("數", "数"),
    ("與", "与"), ("為", "为"), ("會", "会"), ("後", "后"), ("於", "于"),
    ("裡", "里"), ("內", "内"), ("將", "将"), ("實", "实"), ("際", "际"),
    ("應", "应"), ("該", "该"), ("樣", "样"), ("種", "种"), ("嗎", "吗"),
    ("問", "问"), ("點", "点"), ("總", "总"), ("結", "结"), ("東", "东"),
    ("頭", "头"), ("體", "体"), ("經", "经"), ("驗", "验"), ("測", "测"),
    ("試", "试"), ("認", "认"), ("識", "识"), ("記", "记"), ("憶", "忆"),
    ("讀", "读"), ("寫", "写"), ("練", "练"), ("習", "习"), ("過", "过"),
    ("達", "达"), ("運", "运"), ("轉", "转"), ("換", "换"), ("變", "变"),
    ("動", "动"), ("態", "态"), ("勢", "势"), ("無", "无"), ("線", "线"),
    ("標", "标"), ("準", "准"), ("確", "确"), ("級", "级"), ("層", "层"),
    ("構", "构"), ("機", "机"), ("語", "语"), ("團", "团"), ("隊", "队"),
    ("員", "员"), ("價", "价"), ("現", "现"), ("義", "义"), ("議", "议"),
    ("討", "讨"), ("評", "评"), ("證", "证"), ("據", "据"), ("資", "资"),
    ("庫", "库"), ("儲", "储"), ("緩", "缓"), ("衝", "冲"), ("錯", "错"),
    ("誤", "误"), ("區", "区"), ("塊", "块"), ("條", "条"), ("項", "项"),
    ("統", "统"), ("維", "维"), ("護", "护"), ("衛", "卫"), ("風", "风"),
    ("險", "险"), ("傷", "伤"), ("醫", "医"), ("藥", "药"), ("療", "疗"),
    ("緒", "绪"), ("慮", "虑"), ("憂", "忧"), ("鬱", "郁"), ("懼", "惧"),
    ("驚", "惊"), ("憤", "愤"), ("憐", "怜"), ("愛", "爱"), ("戀", "恋"),
    ("惱", "恼"), ("誠", "诚"), ("責", "责"), ("倫", "伦"), ("禮", "礼"),
    ("權", "权"), ("費", "费"), ("業", "业"), ("務", "务"), ("產", "产"),
    ("質", "质"), ("檢", "检"), ("瀏", "浏"), ("覽", "览"), ("顯", "显"),
    ("畫", "画"), ("視", "视"), ("覺", "觉"), ("頻", "频"), ("聲", "声"),
    ("響", "响"), ("樂", "乐"), ("聽", "听"), ("傳", "传"), ("遞", "递"),
    ("給", "给"), ("讓", "让"), ("從", "从"), ("爾", "尔"), ("導", "导"),
    ("報", "报"), ("壞", "坏"), ("堅", "坚"), ("強", "强"), ("鍵", "键"),
    ("盤", "盘"), ("樹", "树"), ("葉", "叶"), ("絡", "络"), ("碼", "码"),
    ("編", "编"), ("譯", "译"), ("陣", "阵"), ("組", "组"), ("織", "织"),
    ("張", "张"), ("們", "们"), ("誰", "谁"), ("幾", "几"), ("處", "处"),
    ("虛", "虚"), ("訊", "讯"), ("備", "备"), ("輸", "输"), ("選", "选"),
    ("擇", "择"), ("邏", "逻"), ("輯", "辑"), ("歸", "归"), ("環", "环"),
    ("約", "约"), ("擴", "扩"), ("優", "优"), ("簡", "简"), ("複", "复"),
    ("雜", "杂"), ("邊", "边"), ("舉", "举"), ("圖", "图"), ("書", "书"),
    ("檔", "档"), ("雲", "云"), ("熱", "热"), ("溫", "温"), ("氣", "气"),
    ("壓", "压"), ("單", "单"), ("釋", "释"), ("課", "课"),
)

_TRAD_TO_SIMP = str.maketrans({trad: simp for trad, simp in TRADITIONAL})


def normalize(text: str) -> str:
    """归一化用户输入,让同一条规则只写一遍。

    四步,顺序有意:

    1. **NFKC**:全角转半角、兼容字符折叠(`ＨＴＭＬ` -> `HTML`、`①` -> `1`、
       `㎏` -> `kg`)。标准库自带,不必手写字符区间;
    2. **去不可见字符**:零宽空格/BOM/软连字符,复制粘贴的常见附着物;
    3. **繁简折叠**:`電車難題` -> `电车难题`(见 `TRADITIONAL`);
    4. **压缩空白**:连续空白变一个空格,去首尾。

    框架不依赖核心包,所以自带一份(与 `core.smallai.core.context.normalize`
    同口径,但多了繁简与兼容字符)。
    """
    if not text:
        return ""
    folded = unicodedata.normalize("NFKC", text)
    folded = folded.translate(_INVISIBLE).translate(_TRAD_TO_SIMP)
    return re.sub(r"\s+", " ", folded).strip()


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
        # 纠错按长度取候选池:中文只认同长、ASCII 容 ±预算 —— 不必每次扫全词表
        by_length: dict[int, list[str]] = {}
        for term in self.vocab:
            by_length.setdefault(len(term), []).append(term)
        self._by_length: dict[int, tuple[str, ...]] = {
            length: tuple(sorted(terms)) for length, terms in by_length.items()
        }
        self._pools: dict[tuple[bool, int, int], tuple[str, ...]] = {}

    def _correction_pool(self, window: str) -> tuple[str, ...]:
        """纠错候选池:只取长度上可能对得上的那些词(一次查好,之后走缓存)。"""
        limit = typo_budget(window)
        if limit <= 0:
            return ()
        is_ascii = window.isascii()
        key = (is_ascii, len(window), limit if is_ascii else 0)
        pooled = self._pools.get(key)
        if pooled is None:
            if is_ascii:
                lengths = range(max(2, len(window) - limit), len(window) + limit + 1)
            else:
                lengths = (len(window),)
            pooled = tuple(
                term for length in lengths for term in self._by_length.get(length, ())
            )
            self._pools[key] = pooled
        return pooled

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
        """双向最长匹配:两个方向各切一遍,取"认出来的字更多、孤字更少"的那个。

        为什么不是单向:中文没有空格,从左往右贪最长会把「研究生命」切成
        「研究生 + 命」(认出一个词、剩一个孤字),从右往左则得到「研究 + 生命」
        (两个词)。两种切法都摆在台面上可核对 —— 这里选孤字更少的那种,
        打平时保留从左往右的结果(与旧行为一致,不制造无谓的差异)。

        匹配不到的字先单放,再按「连续单字连成一块」合并(最多 6 字),
        所以「引立波」不会被切碎成三个单字。
        """
        forward = self._merge(self._match(run))
        backward = self._merge(self._match(run, reverse=True))
        if self._segment_score(backward) < self._segment_score(forward):
            return backward
        return forward

    def _match(self, run: str, *, reverse: bool = False) -> list[str]:
        """单向最长匹配(返回的都是原方向的词,不反转)。"""
        pieces: list[str] = []
        if not reverse:
            index = 0
            while index < len(run):
                for size in range(min(self.max_word, len(run) - index), 1, -1):
                    piece = run[index:index + size]
                    if piece in self.vocab:
                        pieces.append(piece)
                        index += size
                        break
                else:
                    pieces.append(run[index])
                    index += 1
            return pieces

        index = len(run)
        while index > 0:
            for size in range(min(self.max_word, index), 1, -1):
                piece = run[index - size:index]
                if piece in self.vocab:
                    pieces.append(piece)
                    index -= size
                    break
            else:
                pieces.append(run[index - 1])
                index -= 1
        pieces.reverse()
        return pieces

    @staticmethod
    def _merge(pieces: list[str]) -> list[str]:
        """把连续的单字(非虚词)连成块 —— 没认出来的词至少保持完整。"""
        merged: list[str] = []
        buf = ""
        for token in pieces:
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

    def _segment_score(self, parts: list[str]) -> tuple[int, int, int]:
        """切法的好坏:孤字少 -> 词表认出的字多 -> 段数少。"""
        stray = sum(
            1 for part in parts
            if len(part) == 1 and part not in PARTICLES and part not in self.vocab
        )
        known = sum(len(part) for part in parts if part in self.vocab)
        return (stray, -known, len(parts))

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

        # 换字类变体(谐音/形近)是线性的,可以管到 6 个字;
        # 排列是阶乘,仍然只对 2–4 字(见上面的 MAX_PERM_LEN)。
        for token in tokens:
            if len(candidates) >= MAX_TOTAL_CANDIDATES:
                break
            if token.isascii() or not (2 <= len(token) <= MAX_SUB_LEN):
                continue
            if token in PARTICLES:
                continue
            for variant in _homophones(token):
                add(variant, "homophone", token)
            for variant in _confusables(token):
                add(variant, "confusable", token)

        # 词典纠错:只对**词表没认出来**的词做,唯一最佳才收(见 fuzzy.correct_token)。
        # 放在最后:前面的变体是"确定的换字法",纠错是"猜一个最像的",
        # 同一条候选文字两边都能造出来时,保留靠前的来源,便于解释。
        #
        # 为什么要连"相邻两三段拼起来"一起看:切词会先把认识的子词切开,
        # 「量子退相于」会被切成 量子/退相/于 —— 单看每一段都没有错字,
        # 拼起来才是「退相干」写错了一个字。窗口只在"这一段不认识"时试。
        corrections: list[str] = []
        seen_windows: set[str] = set()
        for start in range(len(tokens)):
            if len(corrections) >= MAX_CORRECTIONS:
                break
            if len(candidates) >= MAX_TOTAL_CANDIDATES:
                break
            for width in (1, 2, 3):
                if start + width > len(tokens):
                    break
                window = "".join(tokens[start:start + width])
                if not (2 <= len(window) <= MAX_SUB_LEN) or window in self.vocab:
                    continue                      # 这一窗口本来就是认识的词,试更宽的
                if window in seen_windows:
                    continue
                seen_windows.add(window)
                correction = correct_token(
                    window, self._correction_pool(window), related=related_char
                )
                if correction is None or not _diff_avoids_particles(window, correction.term):
                    continue
                if correction.term not in seen:
                    add(correction.term, "correct", window)
                    corrections.append(f"{window} → {correction.term}")
                break                             # 这一处已经纠好,不再往更宽的窗口找

        notes = [
            "候选不是穷尽的:排列组合只对 2–4 字、换字(谐音/形近)管到 6 字,"
            "纠错只收词表里唯一最像的那个,总量有上限。",
            f"本次切出 {len(tokens)} 段,生成 {len(candidates)} 条候选交给查找逐条尝试。",
        ]
        if corrections:
            notes.append("词典纠错:" + "、".join(corrections))
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


def _diff_avoids_particles(left: str, right: str) -> bool:
    """纠错改动的位置上不许有虚词。

    「引力是」把「是」纠成「波」就成了「引力波」—— 字面上确实只差一个字,
    但那是把一句完整的话改成了另一个词。虚词参与的差异不认,宁可漏纠。
    """
    if len(left) != len(right):
        return True
    for a, b in zip(left, right):
        if a == b:
            continue
        if a in PARTICLES or b in PARTICLES:
            return False
    return True


def _confusables(token: str) -> list[str]:
    """形近字替换:一份(same 已, 未->末 …)。每个位置最多试几个,避免组合爆炸。"""
    out: list[str] = []
    for index, ch in enumerate(token):
        for alt in SHAPES.get(ch, ()):
            value = token[:index] + alt + token[index + 1:]
            if value != token and value not in out:
                out.append(value)
        if len(out) >= MAX_CONFUSABLE_VARIANTS:
            break
    return out[:MAX_CONFUSABLE_VARIANTS]


def _ascii_typos(token: str) -> list[str]:
    """相邻字母互换:HTML -> HMTL;再叠一个大小写不敏感的原词。

    超长 token(粘贴进来的一整段日志/URL/英文正文)不做互换:每个位置都复制一遍
    整串是 O(n²),40k 字符就会让交互卡住几十秒。这里只给原词的小写形态 ——
    长串本身也不像"打错一个字母的单词",纠错对它没有意义。
    """
    out: list[str] = []
    lowered = token.lower()
    if lowered != token:
        out.insert(0, lowered)
    if len(token) > MAX_TYPO_TOKEN_LEN:
        return out[:12]
    for index in range(len(token) - 1):
        chars = list(token)
        chars[index], chars[index + 1] = chars[index + 1], chars[index]
        value = "".join(chars)
        if value not in out:
            out.append(value)
        if len(out) >= 12:
            break
    return out[:12]


__all__ = [
    "Candidate",
    "Decomposition",
    "Decomposer",
    "normalize",
    "keywords_of",
    "related_char",
    "HOMOPHONES",
    "SAME_SOUND",
    "TRADITIONAL",
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


"""证据闸门：回答之前先问一句"这条证据够不够"。

为什么要单独一层
----------------
"答不答"原先由 `calibration.py` 合成：z 分数 / margin / 覆盖 / 信任。其中两个分量
在**小语料**上是坏的：

· `margin`（第一名比第二名高多少）在**只有一条候选**时算出 1.0 —— 把"没有竞争"
  当成了"优势极大"，白送 0.25 的置信度；
· `coverage` 的分子分母是"试过几条候选 / 问题切出几个词"，不是"问题里的**实义词**
  命中了几个"；而"什么 / 是 / 怎么"这些功能词也被算进了分母。

后果是可复现的：问「什么是德摩根定律」，混合检索只补出一条 physics 候选，margin=1.0、
high 信任再 +0.08，合成出 0.90 的置信度，然后它把问题改写成「热力学定律是什么」并作答。
「C++ 的虚函数表怎么实现」同理 —— 词表把"虚函数"切出了"函数"，命中 math/function。

**候选生成**（模糊 / 同音 / 排列 / 扩展 / 改写）负责"想出候选"；这一层只负责
"判断证据够不够"。两者分开，才能各自调、各自测、各自被审计。

怎么判
------
只用条目自己的**头部字段**（question / aliases / keywords）算覆盖度，按 IDF 加权：

    覆盖度 = Σ IDF(命中的实义词) / Σ IDF(问题里的全部实义词)

用答案正文算覆盖度是错的 —— 正文里出现"定律"不能证明这条知识在回答"德摩根定律"。

词项用**字的 1+2 元组**（CJK）和整段 ASCII 小写词，不依赖任何词表，所以"虚函数"
不会被切成"函数"：查询的二元组是 虚函 / 函数 / 数表，条目里只有 函数，覆盖度自然低。

可审计
------
`assess()` 返回的 `Evidence` 带 `matched` / `missing` 两张表，`addends` 能直接印成

    命中 向量场(IDF 2.31) 是(IDF 0.02) = 2.33 / 全部 2.33 → 覆盖度 1.00 ≥ 0.30 → 作答

也就是**每一个数都能手算**。阈值在 `config.json` 的 `retrieval.min_coverage`。

零依赖、确定性：同一段文本、同一批条目，永远得到同一个结论。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Iterable

#: 归一化只有一份：全角/兼容字符、不可见字符、繁简折叠都在 `decomposer.normalize`。
#: 这里直接复用 —— 宁可多一个 import，也不让"同一条规则"出现第三份实现。
from .decomposer import normalize
from .fuzzy import correct_token

#: 默认阈值。定它的方法见 `tests/test_evidence.py` 与 `tools/eval_evidence.py`：
#: 在自带评测集 + 28 条越界探针上扫一遍，取"零错误引用"那一侧的第一个拐点。
#:
#: 1.0.3 从 0.30 抬到 0.40：分母不再含虚词二元组（`型的 / 是从 / 的偏` 这类）之后，
#: 同一条问题的覆盖度整体上移，0.30 变得过松 —— 实测「费马大定理怎么证明」在
#: 0.37 就能过。0.40 是"自带评测集错误引用归零"的最小值（0.35 仍有 1 条，
#: 命中数一样是 30/33，所以直接取 0.40）。扫法见 `tools/tune_evidence.py`。
DEFAULT_MIN_COVERAGE = 0.40

#: 切词元：空白与标点。只用它切段，不做任何语言学处理。
PUNCT = re.compile(
    r"[\s,，。！？、：；「」『』（）()\[\]{}<>《》\"'`~!@#$%^&*_+=|\\/—·…\-]+"
)

#: 连接词也当边界。`向量场和电场的关系` 这种问法里，跨连接词的二元组（"场和""和电"）
#: 两边都不属于，只会同时把两个条目的覆盖度压低 —— 而它问的本来就是"两个条目各答一半"。
#: 当成边界之后，每个条目只跟自己那一半比。
CONJUNCTIONS = re.compile(r"[和与跟]|以及|还有|或者")

#: 功能词。去掉它们，分母才等于"真正需要被证据支撑的词"。
#: 只收单字/双字的虚词与疑问词；**不收**可能构成实词的字（如"和"作"和平"时，
#: 会以二元组形式留下来）。
STOPWORDS = frozenset(
    """
    什么 是 怎么 怎样 如何 为什么 为何 多少 哪里 哪个 哪些 吗 呢 吧 啊 呀
    的 地 得 了 着 过 和 与 或 及 还有 有 没有 在 就 都 也 还 又 很 太 更 最
    我 你 他 她 它 我们 你们 他们 这 那 这个 那个 这些 那些
    介绍 一下 请问 请 帮 能 可以 会 要 想 说 讲 告诉 区别 关系 什么区别
    哪 几 种 哪几 几种 一些 到底 究竟 关于 对于 讲讲 说说 麻烦
    """.split()
)

#: **当边界用的**填充词：它们出现的地方把"实义词"切开，二元组不许跨过去。
#:
#: 为什么必须切：`能讲讲概率吗` 以前会产出 `能讲 / 讲讲 / 讲概` 三个**跨功能词**的
#: 二元组，它们在任何条目里都不可能命中，却被算成"漏掉的实义词"，把覆盖度稀释到
#: 阈值以下 —— 于是一条完全正常的问题（概率是什么）被闸门挡掉了。
#:
#: 只收**绝不承载知识**的单位：`讲` 在全部条目头部出现 0 次；`到底 / 究竟 / 关于 / 对于`
#: 都是提问语。**不收** `底 / 到 / 能 / 会 / 要 / 想 / 说 / 下 / 个 / 样 / 些` ——
#: 它们出现在 `底线`、`遇到`、`功能`、`说明`、`样式表` 里，当边界用会把真正的词拆坏。
#:
#: 单字虚词（的/是/在/有/…）是 1.0.3 补的：不切它们时，`AI 模型的偏见是从哪来的`
#: 会产出 `型的 / 的偏 / 是从 / 说的 / 的是` 这类**跨虚词**的二元组 —— 它们在任何
#: 条目里都不可能命中，却占掉了大半个分母，把一条完全对得上的问题判成"证据不足"。
#: 反例也要避免：`向`（向量）、`对`（对比/对象）、`能`（能量）、`会`（社会）、
#: `说`（说服）、`要`（重要）都是实词的一部分，**不收**。
FILLERS = (
    "介绍一下", "怎么理解", "如何理解", "是怎么回事",
    "什么", "怎么", "怎样", "如何", "为什么", "多少", "哪里", "哪个", "哪些",
    "到底", "究竟", "关于", "对于", "请问", "一下", "一些", "讲讲", "说说", "麻烦",
    # 提问框架名词：它们描述"问的是哪一面"，本身不携带知识
    "原理", "机制", "应用", "用途", "场景", "说法", "做法", "回事", "办法",
    "步骤", "过程", "历史", "方面", "东西", "时候", "地方", "样子", "意义", "目的",
    # 提问语与虚词
    "讲", "什", "么", "吗", "呢", "吧", "啊", "呀",
    "的", "地", "得", "是", "在", "有", "都", "也", "就", "还", "又",
    "很", "太", "更", "最", "和", "与", "或", "及", "把", "被", "让", "给",
    "着", "过", "了", "哪", "来", "去", "而", "且", "从",
)

#: 长的先匹配（"介绍一下" 要在 "一下" 之前吃掉）
_FILLER_RE = re.compile("|".join(sorted(FILLERS, key=len, reverse=True)))

#: 单个汉字的命中该打几折。
#:
#: 单字的证据力本来就弱于二元组："理""定""证"几乎哪个条目里都有。用 1.0 时
#: 「费马大定理怎么证明」靠"定理/证明"里几个单字就够过阈值，把"费马/马大/大定"
#: 这些真正的特征摊薄掉了。打折之后，**特征二元组**才主导覆盖度。
#: 取 0.5 而不是更低：中文短问句（"熵是什么"）本来就只剩单字可算。
UNIGRAM_WEIGHT = 0.5


def _is_single_cjk(term: str) -> bool:
    return len(term) == 1 and ord(term) >= 128


#: 头部字段：只有这些能当证据。答案正文不算。
HEAD_FIELDS = ("question", "aliases", "keywords", "secondary_keywords", "title")

#: 条目的**不适用词**：这些词一出现，问的就是另一个意思，这条知识不该拿来答。
#:
#: 为什么必须在语料里写：覆盖度是统计量，它只知道"接口"这两个字命中过，
#: 不知道这里的"接口"是 USB 接口。同形异义是零依赖关键词系统的**已知盲区**，
#: 唯一诚实的做法是把边界写成可审计的数据（见各包 `pack.json` 的 `not_about`），
#: 而不是让阈值去猜。没写的盲区照样会漂 —— README 的「边界」一节写明了这一点。
NOT_ABOUT_FIELD = "not_about"
#: 太短的判据词会把正常问题也误伤（"类"、"岛"），所以只认 ≥2 个字符的。
NOT_ABOUT_MIN_LEN = 2


def _segments(text: str) -> Iterable[tuple[str, str]]:
    """把一段文本切成"连续 ASCII"与"连续 CJK"两种段。

    必须分段：整段按 CJK 处理会把 `htlm` 拆成 h/t/l/m 四个一元字，
    整段按 ASCII 处理又会让中文变成一个词。两种都会污染覆盖度。
    """
    kind: str | None = None
    buffer: list[str] = []
    for ch in text:
        current = "ascii" if ord(ch) < 128 else "cjk"
        if current != kind and buffer:
            yield kind or "cjk", "".join(buffer)
            buffer = []
        kind = current
        buffer.append(ch)
    if buffer:
        yield kind or "cjk", "".join(buffer)


def content_terms(text: str) -> tuple[str, ...]:
    """把一段文本切成"需要被证据支撑"的词项（去重、保序）。

    CJK 段：一元字 + 二元组；ASCII 段：整段小写词（`HTML`）。功能词与标点丢掉。
    没有任何词表参与 —— 所以"虚函数"不会被切成"函数"：查询产出的是
    虚函 / 函数 / 数表 三个二元组，条目里只有"函数"一个，覆盖度自然低。

    **二元组不跨功能词**：先把填充词（见 `FILLERS`）换成空格，再对每一段独立成词。
    否则 `能讲讲概率` 会产出 `能讲 / 讲讲 / 讲概` 三个跨词的二元组，它们永远不可能
    命中，却被算成"漏掉的实义词"。
    """
    out: list[str] = []
    seen: set[str] = set()

    def push(term: str) -> None:
        if not term or term in seen or term in STOPWORDS:
            return
        seen.add(term)
        out.append(term)

    body = _FILLER_RE.sub(" ", CONJUNCTIONS.sub(" ", normalize(text or "").lower()))
    for run in PUNCT.split(body):
        if not run:
            continue
        for kind, segment in _segments(run):
            if kind == "ascii":
                push(segment)
                continue
            chars = list(segment)
            if len(chars) == 1:
                push(segment)
                continue
            for ch in chars:
                push(ch)
            for i in range(len(chars) - 1):
                push(chars[i] + chars[i + 1])
    return tuple(out)


def clauses(text: str) -> tuple[str, ...]:
    """按连接词把一句话切成若干分句（"向量场和电场的关系" → 向量场 / 电场的关系）。"""
    parts = [part.strip() for part in CONJUNCTIONS.split(normalize(text or "")) if part.strip()]
    return tuple(parts) or ((text or "").strip(),)


def head_text(entry: dict) -> str:
    """条目的头部字段拼成一段文本（question / aliases / keywords / …）。"""
    parts: list[str] = []
    for field_name in HEAD_FIELDS:
        value = entry.get(field_name)
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, (list, tuple)):
            parts.extend(str(item) for item in value if isinstance(item, (str, int, float)))
    return " ".join(parts)


@dataclass(frozen=True)
class Evidence:
    """一次"这条证据够不够"的判断结果。所有分量都能手算。"""

    ok: bool
    coverage: float
    threshold: float
    folder: str = ""
    entry_id: str = ""
    matched: tuple[tuple[str, float], ...] = ()
    missing: tuple[tuple[str, float], ...] = ()
    reason: str = ""
    skipped: bool = False
    #: 靠哪条纠错候选撑起来的（"" = 就是用户原话）
    via: str = ""

    @property
    def label(self) -> str:
        if self.skipped:
            return "闸门跳过"
        return f"覆盖度 {self.coverage:.2f}{'' if self.ok else '<'}" + (
            "" if self.ok else f"{self.threshold:.2f}"
        )

    def addends(self, limit: int = 6) -> tuple[str, ...]:
        """加数树：命中什么、漏了什么、各占多少权重。`--trace` 直接印这几行。"""
        hit = " ".join(f"{term}({weight:.2f})" for term, weight in self.matched[:limit])
        miss = " ".join(f"{term}({weight:.2f})" for term, weight in self.missing[:limit])
        more = len(self.missing) - limit
        if more > 0:
            miss += f" …另 {more} 个"
        lines = []
        if hit:
            lines.append(f"命中 {hit}")
        if miss:
            lines.append(f"漏掉 {miss}")
        if not lines:
            lines.append("（没有实义词）")
        return tuple(lines)


class EvidenceGate:
    """把"问题 + 条目"判成"够 / 不够"。IDF 从当前装着的全部条目头部算。"""

    def __init__(self, documents: Iterable[str], *, threshold: float = DEFAULT_MIN_COVERAGE,
                 stopwords: frozenset[str] | None = None) -> None:
        self.threshold = max(0.0, min(1.0, float(threshold)))
        self._stopwords = stopwords if stopwords is not None else STOPWORDS
        docs = [content_terms(text) for text in documents]
        df: dict[str, int] = {}
        for terms in docs:
            for term in set(terms):
                df[term] = df.get(term, 0) + 1
        self._n = len(docs)
        self._df = df
        self._cache: dict[str, float] = {}

    # ---- 构造 ----------------------------------------------------------
    @classmethod
    def from_cards(cls, cards: Iterable[object], *, threshold: float = DEFAULT_MIN_COVERAGE) -> "EvidenceGate":
        """从 catalog 的 ExpertCard 序列建闸门（头部字段取全部条目）。"""
        documents: list[str] = []
        for card in cards:
            for entry in getattr(card, "entries", ()) or ():
                if isinstance(entry, dict):
                    documents.append(head_text(entry))
        return cls(documents, threshold=threshold)

    @property
    def disabled(self) -> bool:
        """阈值 ≤ 0 = 关闭闸门（`retrieval.min_coverage: 0`）。"""
        return self.threshold <= 0.0

    # ---- 打分 ----------------------------------------------------------
    def idf(self, term: str) -> float:
        cached = self._cache.get(term)
        if cached is not None:
            return cached
        df = self._df.get(term, 0)
        n = max(self._n, 1)
        value = math.log(1.0 + (n - df + 0.5) / (df + 0.5))
        self._cache[term] = value
        return value

    def _weight(self, term: str) -> float:
        """一个词项在覆盖度里的分量：IDF ×（单字折扣）。"""
        weight = self.idf(term)
        return weight * UNIGRAM_WEIGHT if _is_single_cjk(term) else weight

    def assess(
        self,
        query: str,
        entry: dict | None,
        *,
        folder: str = "",
        entry_id: str = "",
        alternates: Iterable[str] = (),
        siblings: Iterable[tuple[str, str, dict]] = (),
    ) -> Evidence:
        """这条条目能不能当这问题的证据。

        `alternates` 只应传**纠错类**候选（ascii_typo / homophone / perm）：
        那些是"用户可能把词写错了，系统给出的一种纠正读法"。**不要传 base 分词片断**——
        "虚函数"被词表切成"函数"正是漂移的来源，把切口当证据会让错误重新生效。

        纠错候选的门槛比原话**更严**：它必须**原样**出现在条目头部字段里。
        只按"共有的字"算是不够的 —— 试过把"函数"的一对字颠倒成"数函"，
        二元组仍然命中，覆盖度被抬到 0.49，"虚函数表"那个错就回来了。
        原样出现是个可以一眼核对的条件：`"向量场" in "向量场是什么?"`。

        `siblings` 是这一轮**别的包会拿来作答的条目**（`(包, 条目 id, 条目)`）。
        一个问两样东西的问题（"向量场和电场的关系"）可以一半由这条答、另一半由别的
        包答；但**任何一半都没有证据时必须弃权** —— 否则「熵增和热寂是什么关系」
        会拿"熵是什么"作答，答的是一个问关系的问题（1.0.3 实测的漂移）。
        """
        if entry is None:
            return Evidence(
                ok=False, coverage=0.0, threshold=self.threshold,
                folder=folder, entry_id=entry_id, reason="没有可核对的条目",
            )
        if self.disabled:
            return Evidence(
                ok=True, coverage=1.0, threshold=0.0,
                folder=folder, entry_id=entry_id, reason="闸门已关闭(retrieval.min_coverage=0)",
                skipped=True,
            )
        # 语料自己写下的"不适用词"优先于任何统计：它是人核对过的边界，
        # 而覆盖度只是"字面命中多少"。USB 接口不该由「接口」这条来答。
        cue = self._not_about_hit(query, entry)
        if cue is not None:
            return Evidence(
                ok=False, coverage=0.0, threshold=self.threshold,
                folder=folder, entry_id=entry_id,
                reason=f"出现不适用词「{cue}」:这一条讲的是另一个意思",
            )

        head_raw = normalize(head_text(entry)).lower()
        query_raw = normalize(query).lower()
        for alt in alternates:
            text = (alt or "").strip()
            if len(text) < 2 or text == query:
                continue
            folded = normalize(text).lower()
            # 已经是用户原话的一部分（"HTTP" 的候选还是 "http"）：它不构成新的证据。
            if folded in query_raw:
                continue
            if folded in head_raw:
                return Evidence(
                    ok=True, coverage=1.0, threshold=self.threshold,
                    folder=folder, entry_id=entry_id, via=text,
                    reason=f"纠错候选「{text}」原样出现在条目头部",
                )
        verdicts = self._clause_verdicts(query, entry, folder=folder, entry_id=entry_id)
        verdict = self._combine(verdicts, folder=folder, entry_id=entry_id)
        if verdict.ok:
            return verdict
        rescue = self._sibling_rescue(verdicts, siblings)
        if rescue is not None:
            other, coverage = rescue
            return Evidence(
                ok=True, coverage=coverage, threshold=self.threshold,
                folder=folder, entry_id=entry_id,
                reason=f"本条目答一半,另一半由「{other}」那条答（各分句都有证据）",
                matched=verdict.matched, missing=verdict.missing,
            )
        # 最后一问：用户是不是把**这条知识自己的标准问法**写错了？
        # 只跟这一条的 question/aliases 比，而且要求"同长度 + 只差一处 + 唯一最优"
        # （`fuzzy.correct_token` 原有的三条自律）。所以「引立波是什么」能认回
        # 「引力波是什么」，而「什么是德摩根定律」认不到热力学 —— 后者差了三处。
        typo = self._as_typo_of_question(query, entry)
        if typo is not None:
            return Evidence(
                ok=True, coverage=verdict.coverage, threshold=self.threshold,
                folder=folder, entry_id=entry_id, matched=verdict.matched,
                missing=verdict.missing, via=typo,
                reason=f"看着像「{typo}」的错写（同长度、只差一处、唯一最优）",
            )
        return verdict

    @staticmethod
    def _not_about_hit(query: str, entry: dict) -> str | None:
        """这句话里有没有这条知识"明确不管"的词（`not_about`）。

        只做**原样子串**匹配：`usb 接口` 命中 `usb`，`微信群` 命中 `微信群`。
        不做纠正、不做分词 —— 一张判据表要能一眼看懂，就不能带推测。
        """
        cues = entry.get(NOT_ABOUT_FIELD)
        if not isinstance(cues, (list, tuple)):
            return None
        text = normalize(query or "").lower()
        if not text:
            return None
        for raw in cues:
            cue = normalize(str(raw)).lower().strip()
            if len(cue) < NOT_ABOUT_MIN_LEN:
                continue
            if cue in text:
                return str(raw)
        return None

    @staticmethod
    def _as_typo_of_question(query: str, entry: dict) -> str | None:
        """用户是不是把这条知识的标准问法写错了（同长度、只差一处、唯一最优）。"""
        phrases = [str(entry.get("question") or "")] + [
            str(item) for item in (entry.get("aliases") or ())
        ]
        vocabulary = [phrase for phrase in phrases if len(phrase) >= 2]
        if not vocabulary:
            return None
        # 标点先剥掉：`引立波是什么?` 与别名 `引力波是什么` 只差一处，带上问号就变成差两处。
        bare = "".join(PUNCT.split(normalize(query)))
        if len(bare) < 5:            # 太短的句子差一处可能就是另一个问题了，不猜
            return None
        correction = correct_token(bare, [p for p in (normalize(v) for v in vocabulary) if len(p) == len(bare)])
        return correction.term if correction is not None else None

    def _clause_verdicts(self, text: str, entry: dict, *, folder: str,
                         entry_id: str) -> list[tuple[str, Evidence]]:
        """按分句各判一次，返回 [(分句原文, 判定)]。

        分句太短（整句虚词、或只剩一个汉字）没有判据力，不参与定案：
        一个字在相关条目里几乎必然出现，覆盖度按定义就是 1.0 —— 那不是证据。
        """
        head = set(content_terms(head_text(entry)))
        parts = clauses(text)
        out: list[tuple[str, Evidence]] = []
        for clause in parts:
            terms = content_terms(clause)
            if not terms:
                continue
            # 多分句时，**只剩一个汉字**的分句不能参与定案：「熵和寿命有什么关系」
            # 会被"熵"那一半算成 1.0，于是拿"熵是什么"去答一个问关系的问题。
            # 只对单个汉字生效：`Cookie 和 localStorage …` 里 `cookie` 单独成一个分句，
            # 那是一个完整的英文词，本来就是这条知识最硬的证据。
            if len(parts) > 1 and len(terms) == 1 and _is_single_cjk(terms[0]):
                continue
            out.append((clause, self._coverage_of(
                clause, head, folder=folder, entry_id=entry_id)))
        return out

    def _combine(self, verdicts: list[tuple[str, Evidence]], *, folder: str,
                 entry_id: str) -> Evidence:
        """把几个分句的判定合成一条：**全过才算过**，`coverage` 取最差的那一半。"""
        if not verdicts:
            # 整句没有实义词（"还有吗"）：交给上下文/规则路径，闸门不参与。
            return Evidence(
                ok=True, coverage=1.0, threshold=self.threshold,
                folder=folder, entry_id=entry_id,
                reason="没有实义词,闸门不参与", skipped=True,
            )
        ok = all(verdict.ok for _clause, verdict in verdicts)
        worst_clause, worst = min(verdicts, key=lambda item: item[1].coverage)
        if ok and len(verdicts) == 1:
            return worst
        if not ok and worst.ok:          # 保证"不通过"的那一半被报出来
            worst_clause, worst = next(
                item for item in verdicts if not item[1].ok
            )
        reason = f"各分句取最差:{worst.reason}"
        if not ok:
            reason += "（一半有证据、一半没有,不算答上）"
        return Evidence(
            ok=ok,
            coverage=worst.coverage,
            threshold=self.threshold,
            folder=worst.folder,
            entry_id=worst.entry_id,
            matched=worst.matched,
            missing=worst.missing,
            reason=reason,
            via=worst.via,
        )

    def _sibling_rescue(
        self,
        verdicts: list[tuple[str, Evidence]],
        siblings: Iterable[tuple[str, str, dict]],
    ) -> tuple[str, float] | None:
        """没通过的那几个分句，是不是**别的包**会拿来作答？

        一个问两样东西的问题（"向量场和电场的关系"）本来就会查两个包，
        一半由这条答、另一半由那个包答，是正常的。只要有一半谁都没有证据，
        就返回 None —— 那是"只答一半"，不是答案。
        """
        failing = [(clause, verdict) for clause, verdict in verdicts if not verdict.ok]
        # 只有**问了两样东西**的问题才谈得上"一半由别人答"。整句一个分句、
        # 这条自己没覆盖就是没覆盖 —— 拿同包里另一条来顶，正是漂移本身。
        if len(verdicts) < 2 or not failing or not siblings:
            return None
        # 而且这条自己得真的答得上一半：两半都靠别人，就不是"这条被问到了"。
        if not any(verdict.ok for _clause, verdict in verdicts):
            return None
        pool = list(siblings)
        names: list[str] = []
        for clause, _verdict in failing:
            covered = None
            for folder, entry_id, entry in pool:
                if self._not_about_hit(clause, entry) is not None:
                    continue
                head = set(content_terms(head_text(entry)))
                if self._coverage_of(clause, head, folder=folder,
                                     entry_id=entry_id).ok:
                    covered = f"{folder}/{entry_id}"
                    break
            if covered is None:
                return None
            names.append(covered)
        return ("、".join(dict.fromkeys(names)),
                min(verdict.coverage for _clause, verdict in verdicts))

    def _coverage_of(self, text: str, head: set[str], *, folder: str, entry_id: str,
                     via: str = "") -> Evidence:
        terms = content_terms(text)
        if not terms:
            return Evidence(
                ok=True, coverage=1.0, threshold=self.threshold,
                folder=folder, entry_id=entry_id, reason="没有实义词,闸门不参与",
                skipped=True, via=via,
            )
        weights = {term: self._weight(term) for term in terms}
        total = sum(weights.values())
        if total <= 0:
            return Evidence(
                ok=True, coverage=1.0, threshold=self.threshold,
                folder=folder, entry_id=entry_id, reason="权重全为零,闸门不参与",
                skipped=True, via=via,
            )

        matched = tuple((term, weights[term]) for term in terms if term in head)
        missing = tuple(
            sorted(
                ((term, weights[term]) for term in terms if term not in head),
                key=lambda pair: -pair[1],
            )
        )
        coverage = sum(weight for _, weight in matched) / total
        ok = coverage >= self.threshold
        reason = (
            f"头部覆盖 {coverage:.2f} {'≥' if ok else '<'} {self.threshold:.2f}"
            f"（命中 {len(matched)} / 共 {len(terms)} 个实义词）"
        )
        return Evidence(
            ok=ok,
            coverage=round(coverage, 4),
            threshold=self.threshold,
            folder=folder,
            entry_id=entry_id,
            matched=matched,
            missing=missing,
            reason=reason,
            via=via,
        )


__all__ = [
    "DEFAULT_MIN_COVERAGE",
    "Evidence",
    "EvidenceGate",
    "HEAD_FIELDS",
    "NOT_ABOUT_FIELD",
    "STOPWORDS",
    "content_terms",
    "head_text",
]

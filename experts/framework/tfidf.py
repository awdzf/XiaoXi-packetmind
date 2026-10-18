"""轻量检索:纯 Python 的 TF-IDF + 余弦相似度（零依赖）。

思路借自 QnA-Chatbot 的最小实现：

    · 把每条知识的**问法**（题目 / 别名 / 关键词）当成一篇"问题文档"；
    · 用户问题也做同样的切词，算 TF-IDF 向量；
    · 用余弦相似度找最像的条目，**低于阈值就如实说没找到**，不硬答。

和小析原有规则检索的分工：

    · 规则检索（Router 的领域词/别名/关键词打分）优先，精确、可解释；
    · 只有**规则一个领域包都没命中**时，才用这里的相似度补一次；
    · 它只认识"已经写下来的问法"，不会生成答案；命中后仍然引用条目原句。

为什么不用 sklearn / nltk / jieba：

    · 小设备 + 无依赖是硬底线；
    · 中文没有空格，这里用**两字组合**当词项（经典的无分词索引；
      单字只用于整段就是单字的情况），ASCII 按词边界切；IDF 会把高频项压下去；
    · 语料就是几十到几百条问法，纯 Python 的稀疏点积足够快。

它不保存索引文件：每次装配 Router 时在内存里建一次，随数据包增删自动重建。
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from .catalog import ExpertCard
from .decomposer import CORE_WORDS, PARTICLES, PRONOUNS, normalize

#: 低于这个余弦相似度就当作"没找到"。
#: QnA-Chatbot 用的是 0.3；小析的"问题文档"多了别名/关键词，阈值抬到 0.35，
#: 宁可漏一点，也不拿不相关的条目硬答（没命中会如实说没有）。
MIN_COSINE = 0.35
#: 最多取几条相似条目
DEFAULT_LIMIT = 5

_ASCII_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_+#.-]{1,31}")
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")

#: ASCII 停用词（不参与向量）
_ASCII_STOP = frozenset({
    "a", "an", "the", "of", "to", "in", "on", "for", "and", "or", "is", "are",
    "was", "were", "be", "been", "by", "with", "as", "at", "from", "this",
    "that", "these", "those", "it", "its", "we", "you", "they", "he", "she",
    "i", "not", "no", "do", "does", "did", "can", "will", "would", "should",
    "could", "may", "might", "must", "has", "have", "had",
    "what", "who", "whom", "whose", "how", "why", "when", "where", "which",
    "if", "then", "than", "so", "very", "just", "much", "many", "some", "any",
    "all", "our", "your", "their", "my", "me", "him", "her", "us", "them",
    "also", "about", "into", "over", "after", "before", "between", "during",
    "without", "within", "because", "while", "although", "though", "even",
    "only", "own", "same", "other", "such", "more", "most", "less", "least",
    "one", "two", "three", "yes", "ok", "okay", "please",
})

#: 单字停用词（中文虚词）；它们出现频率高，IDF 也会压，但直接去掉更省
_STOP_CHARS = frozenset(PARTICLES) | frozenset({"这", "那", "个", "些", "之", "于", "其"})
#: 两字停用组合（泛问词/代词/功能词）
_STOP_BIGRAMS = frozenset(
    {word for word in set(CORE_WORDS) | set(PRONOUNS) | set(PARTICLES) if len(word) >= 2}
) | frozenset({
    "我们", "你们", "他们", "她们", "它们", "这个", "那个", "这些", "那些",
    "可以", "以及", "如果", "因为", "所以", "但是", "而且", "或者", "没有",
    "就是", "不是", "需要", "使用", "时候", "问题", "方法", "内容", "部分",
    "其中", "然后", "这样", "那样", "一个", "进行", "通过", "用于", "对于",
    "关于", "根据", "由于", "因此", "并且", "同时", "已经", "正在", "可能",
    "应该", "必须", "能够", "得到", "提供", "支持", "包括", "例如", "比如",
    "注意", "重要", "主要", "一般", "通常", "常见", "相关", "如下", "以上",
    "以下", "第一", "第二", "第三", "这里", "那里", "什么", "怎么", "如何",
    "为什么", "是否", "哪些", "哪个", "多少", "一种", "一些",
})


@dataclass(frozen=True)
class SimilarHit:
    """一条相似度命中：哪个包、哪条、得分多少、对应哪句标准问法。"""

    folder: str
    entry_id: str
    question: str
    score: float


def tokenize(text: str) -> list[str]:
    """切词项：ASCII 词 + 中文单字 + 中文两字组合（跳过停用项）。"""
    lowered = normalize(text or "").lower()
    tokens: list[str] = []

    for match in _ASCII_RE.finditer(lowered):
        word = match.group(0).strip(".-")
        if len(word) >= 2 and word not in _ASCII_STOP:
            tokens.append("w:" + word)

    for match in _CJK_RUN.finditer(lowered):
        run = match.group(0)
        if len(run) == 1:
            # 单字词（如"熵"）保留；两字以上不再拆单字，避免"场"把
            # "电场"和"向量场"轻易判成同一件事（实测会误命中）。
            if run not in _STOP_CHARS:
                tokens.append("u:" + run)
            continue
        for index in range(len(run) - 1):
            bigram = run[index:index + 2]
            # 跨"实词 + 虚词"的两字组合（如"场是"）不携带内容信息，丢掉；
            # 否则"电场是什么"会和"向量场是什么"共享一个"场是"，被误判成相似。
            if (bigram not in _STOP_BIGRAMS
                    and bigram[0] not in _STOP_CHARS
                    and bigram[1] not in _STOP_CHARS):
                tokens.append("b:" + bigram)
    return tokens


def entry_tokens(entry: dict) -> list[str]:
    """一条知识的"问题文档"：题目/别名权重高，关键词补一遍。

    公开名字给 `retrieval.py` 的 BM25 复用，保证两套索引看到的是同一组词项。
    """
    question = str(entry.get("question") or "")
    aliases = [str(item) for item in entry.get("aliases") or ()]
    keywords = [str(item) for item in entry.get("keywords") or ()]
    secondary = [str(item) for item in entry.get("secondary_keywords") or ()]

    tokens = tokenize(question) * 2
    for alias in aliases:
        tokens.extend(tokenize(alias))
    # 关键词本身就是该条的检索键,给一次额外权重(不重复太多次,避免压过题目)
    for keyword in keywords:
        tokens.extend(tokenize(keyword))
    # 次要关键词只轻量参与,不压过题目/主关键词
    for keyword in secondary:
        tokens.extend(tokenize(keyword))
    return tokens


def _weighted_vector(tokens: list[str], idf: dict[str, float]) -> dict[str, float]:
    """词频归一化 + IDF + L2 归一化（稀疏 dict）。"""
    if not tokens:
        return {}
    counts = Counter(tokens)
    total = sum(counts.values()) or 1
    vector = {
        term: (count / total) * idf[term]
        for term, count in counts.items()
        if term in idf
    }
    norm = math.sqrt(sum(value * value for value in vector.values()))
    if norm <= 0:
        return {}
    return {term: value / norm for term, value in vector.items()}


class TfidfIndex:
    """内存里的 Q-Q 相似度索引：每条知识一篇文档，查询时算余弦。"""

    def __init__(self, cards: tuple[ExpertCard, ...] = ()) -> None:
        self.cards = tuple(cards)
        self.documents: list[tuple[str, str, str, dict[str, float]]] = []
        # documents: (folder, entry_id, question, vector)
        raw: list[tuple[str, str, str, list[str]]] = []
        for card in self.cards:
            for entry in card.entries:
                entry_id = str(entry.get("id") or "")
                question = str(entry.get("question") or "").strip()
                if not entry_id or not question:
                    continue
                tokens = entry_tokens(entry)
                if tokens:
                    raw.append((card.folder, entry_id, question, tokens))

        document_frequency: Counter[str] = Counter()
        for _folder, _entry_id, _question, tokens in raw:
            document_frequency.update(set(tokens))

        total = len(raw)
        self.idf: dict[str, float] = {
            term: math.log((total + 1) / (count + 1)) + 1.0
            for term, count in document_frequency.items()
        }
        if total:
            # 查询里出现但文档里没有的词项不会贡献相似度;给 0 即可
            pass
        for folder, entry_id, question, tokens in raw:
            vector = _weighted_vector(tokens, self.idf)
            if vector:
                self.documents.append((folder, entry_id, question, vector))

    @property
    def size(self) -> int:
        return len(self.documents)

    def search(
        self,
        query: str,
        *,
        limit: int = DEFAULT_LIMIT,
        min_score: float = MIN_COSINE,
    ) -> list[SimilarHit]:
        """返回相似度 ≥ min_score 的前 limit 条（按分数降序）。"""
        if not query or not self.documents:
            return []
        query_tokens = tokenize(query)
        if not query_tokens:
            return []
        query_vector = _weighted_vector(query_tokens, self.idf)
        if not query_vector:
            return []

        scored: list[tuple[float, str, str, str]] = []
        for folder, entry_id, question, vector in self.documents:
            # 稀疏点积:只遍历查询里有的词
            score = 0.0
            for term, weight in query_vector.items():
                other = vector.get(term)
                if other:
                    score += weight * other
            if score >= min_score:
                scored.append((score, folder, entry_id, question))

        scored.sort(key=lambda item: (-item[0], item[1], item[2]))
        return [
            SimilarHit(folder=folder, entry_id=entry_id, question=question, score=round(score, 4))
            for score, folder, entry_id, question in scored[:limit]
        ]


__all__ = [
    "MIN_COSINE",
    "DEFAULT_LIMIT",
    "SimilarHit",
    "tokenize",
    "entry_tokens",
    "TfidfIndex",
]

"""BM25 + 倒排索引 + 分层索引 + 混合融合（纯 Python，零依赖）。

这一层只做**检索候选**，不生成答案：

    · `BM25Index`：真正的倒排表（term -> {doc_id: tf}），不是每次全量扫描；
      支持按包 `upsert` / `remove`，所以教完一条记忆只更新记忆包那几条文档，
      不必把所有包重建一遍；
    · `HierarchyIndex`：把导入笔记的 `source_file` / `source_heading` 变成一棵
      “文件 → 章节”的树，查询命中标题时给该子树里的条目一个小加成；
    · `HybridRetriever`：BM25 + 原 TF-IDF + 分层加成做分数融合；BM25 首轮
      证据不足时，从首轮高分文档里取几个词做一次**查询扩展**（RM3 的轻量版），
      再检索一次；两次结果按文档取较强者，避免扩展把原命中挤掉。

它和 `tfidf.py` 的分工：`tfidf.py` 保留为“最小余弦兜底”和旧接口；
新检索默认走这里，但两者可以同时存在（混合融合会同时看）。
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import Iterable

from .catalog import ExpertCard
from .tfidf import TfidfIndex, entry_tokens, tokenize

#: BM25 的常用参数（Robertson & Zaragoza 的默认量级）
DEFAULT_K1 = 1.5
DEFAULT_B = 0.75
#: 查询扩展最多加几个词；加太多会把原问题带偏
DEFAULT_EXPANSION = 4
#: 混合分数低于这个值就不算候选（还要再过校准弃权）
MIN_HYBRID_SCORE = 0.10
#: 只有首轮覆盖不足时才扩展（覆盖够了说明原词已经够用）
EXPANSION_COVERAGE = 0.75


def _doc_id(folder: str, entry_id: str) -> str:
    return f"{folder}:{entry_id}"


@dataclass(frozen=True)
class RetrievalDocument:
    """倒排表里的一篇“文档”：一条知识条目的检索键。"""

    doc_id: str
    folder: str
    entry_id: str
    question: str
    tokens: tuple[str, ...]
    heading: str = ""
    source_file: str = ""

    @property
    def counts(self) -> Counter[str]:
        return Counter(self.tokens)


@dataclass(frozen=True)
class BM25Hit:
    doc_id: str
    folder: str
    entry_id: str
    question: str
    score: float
    matched: tuple[str, ...] = ()


@dataclass(frozen=True)
class RetrievalHit:
    """混合检索的一条命中。分数都在 0–1 量级，便于和校准层对齐。"""

    folder: str
    entry_id: str
    question: str
    score: float
    bm25: float = 0.0
    tfidf: float = 0.0
    hierarchy: float = 0.0
    matched_terms: tuple[str, ...] = ()
    expanded: bool = False
    via: str = "hybrid"


class BM25Index:
    """带倒排表的 BM25；支持按数据包增量更新。"""

    def __init__(self, k1: float = DEFAULT_K1, b: float = DEFAULT_B) -> None:
        self.k1 = float(k1)
        self.b = float(b)
        self.documents: dict[str, RetrievalDocument] = {}
        # term -> {doc_id: term_frequency}
        self.postings: dict[str, dict[str, int]] = {}
        self._total_len = 0
        self.updates = 0
        self.removals = 0

    # ---- 索引维护 --------------------------------------------------------
    def add_document(
        self,
        folder: str,
        entry_id: str,
        question: str,
        tokens: Iterable[str],
        *,
        heading: str = "",
        source_file: str = "",
    ) -> str:
        doc_id = _doc_id(folder, entry_id)
        if doc_id in self.documents:
            self.remove_document(doc_id)
        token_list = tuple(str(token) for token in tokens if str(token))
        counts = Counter(token_list)
        self.documents[doc_id] = RetrievalDocument(
            doc_id=doc_id,
            folder=folder,
            entry_id=entry_id,
            question=question,
            tokens=token_list,
            heading=heading,
            source_file=source_file,
        )
        for term, frequency in counts.items():
            self.postings.setdefault(term, {})[doc_id] = frequency
        self._total_len += len(token_list)
        self.updates += 1
        return doc_id

    def remove_document(self, doc_id: str) -> bool:
        document = self.documents.pop(doc_id, None)
        if document is None:
            return False
        for term in set(document.tokens):
            bucket = self.postings.get(term)
            if bucket is None:
                continue
            bucket.pop(doc_id, None)
            if not bucket:
                self.postings.pop(term, None)
        self._total_len -= len(document.tokens)
        self.removals += 1
        return True

    def remove_pack(self, folder: str) -> int:
        stale = [doc_id for doc_id, doc in self.documents.items() if doc.folder == folder]
        for doc_id in stale:
            self.remove_document(doc_id)
        return len(stale)

    def upsert_pack(self, card: ExpertCard) -> int:
        """整包替换：先摘掉这个包旧文档，再按当前 pack.json 加回去。

        这是**增量**的关键：只碰这一个包；其它包的倒排表原样保留。
        """
        self.remove_pack(card.folder)
        count = 0
        for entry in card.entries:
            entry_id = str(entry.get("id") or "").strip()
            question = str(entry.get("question") or "").strip()
            if not entry_id or not question:
                continue
            tokens = entry_tokens(entry)
            if not tokens:
                continue
            self.add_document(
                card.folder,
                entry_id,
                question,
                tokens,
                heading=str(entry.get("source_heading") or ""),
                source_file=str(entry.get("source_file") or ""),
            )
            count += 1
        return count

    # ---- 查询 ------------------------------------------------------------
    @property
    def size(self) -> int:
        return len(self.documents)

    @property
    def avgdl(self) -> float:
        return (self._total_len / self.size) if self.size else 0.0

    def _idf(self, term: str) -> float:
        df = len(self.postings.get(term) or ())
        total = self.size
        if df <= 0 or total <= 0:
            return 0.0
        return math.log(1.0 + (total - df + 0.5) / (df + 0.5))

    def search(
        self,
        query_tokens: Iterable[str],
        *,
        limit: int = 5,
        allowed: set[str] | None = None,
    ) -> list[BM25Hit]:
        query = [str(token) for token in query_tokens if str(token)]
        if not query or not self.documents:
            return []
        # 查询词去重：BM25 的查询项通常按集合处理；重复只会放大长查询
        unique_terms = tuple(dict.fromkeys(query))
        scores: dict[str, float] = {}
        matched: dict[str, list[str]] = {}
        avgdl = self.avgdl or 1.0

        for term in unique_terms:
            bucket = self.postings.get(term)
            if not bucket:
                continue
            idf = self._idf(term)
            if idf <= 0:
                continue
            for doc_id, frequency in bucket.items():
                if allowed is not None and doc_id not in allowed:
                    continue
                document = self.documents.get(doc_id)
                if document is None:
                    continue
                length = len(document.tokens) or 1
                denominator = frequency + self.k1 * (
                    1.0 - self.b + self.b * length / avgdl
                )
                contribution = idf * frequency * (self.k1 + 1.0) / denominator
                scores[doc_id] = scores.get(doc_id, 0.0) + contribution
                matched.setdefault(doc_id, []).append(term)

        if not scores:
            return []
        ordered = sorted(
            scores.items(),
            key=lambda item: (-item[1], item[0]),
        )
        if limit > 0:
            ordered = ordered[:limit]
        return [
            BM25Hit(
                doc_id=doc_id,
                folder=self.documents[doc_id].folder,
                entry_id=self.documents[doc_id].entry_id,
                question=self.documents[doc_id].question,
                score=round(score, 6),
                matched=tuple(dict.fromkeys(matched.get(doc_id, ()))),
            )
            for doc_id, score in ordered
        ]

    def stats(self) -> dict:
        return {
            "documents": self.size,
            "terms": len(self.postings),
            "updates": self.updates,
            "removals": self.removals,
        }


@dataclass
class _HierarchyNode:
    path: tuple[str, ...]
    terms: frozenset[str]
    entries: set[str]
    depth: int


class HierarchyIndex:
    """按 `source_file` / `source_heading` 建树；标题命中时给子树加成。

    普通数据包没有标题字段，只会得到“包名 → 条目”的浅层节点，几乎不产生
    加成；导入笔记（有 `source_heading`）才会真正用上分层。
    """

    def __init__(self) -> None:
        self.nodes: dict[tuple[str, ...], _HierarchyNode] = {}
        self.entry_paths: dict[str, tuple[str, ...]] = {}
        self._folders: dict[str, set[str]] = {}

    @staticmethod
    def _parts(heading: str) -> tuple[str, ...]:
        text = (heading or "").replace("＞", ">").replace("→", ">")
        pieces: list[str] = []
        for chunk in text.replace("/", ">").split(">"):
            piece = chunk.strip()
            if piece:
                pieces.append(piece)
        return tuple(pieces)

    def remove_pack(self, folder: str) -> None:
        doc_ids = self._folders.pop(folder, set())
        for doc_id in doc_ids:
            path = self.entry_paths.pop(doc_id, None)
            if path is None:
                continue
            for end in range(1, len(path) + 1):
                node = self.nodes.get(path[:end])
                if node is not None:
                    node.entries.discard(doc_id)
                    if not node.entries:
                        self.nodes.pop(path[:end], None)

    def upsert_pack(self, card: ExpertCard) -> int:
        self.remove_pack(card.folder)
        added: set[str] = set()
        for entry in card.entries:
            entry_id = str(entry.get("id") or "").strip()
            question = str(entry.get("question") or "").strip()
            if not entry_id or not question:
                continue
            doc_id = _doc_id(card.folder, entry_id)
            source = str(entry.get("source_file") or "").strip() or card.folder
            parts = (source,) + self._parts(str(entry.get("source_heading") or ""))
            self.entry_paths[doc_id] = parts
            for end in range(1, len(parts) + 1):
                path = parts[:end]
                node = self.nodes.get(path)
                if node is None:
                    node = _HierarchyNode(
                        path=path,
                        terms=frozenset(tokenize(" ".join(path))),
                        entries=set(),
                        depth=end,
                    )
                    self.nodes[path] = node
                node.entries.add(doc_id)
            added.add(doc_id)
        self._folders[card.folder] = added
        return len(added)

    def boosts(self, query_tokens: Iterable[str]) -> dict[str, float]:
        query = set(str(token) for token in query_tokens if str(token))
        if not query or not self.nodes:
            return {}
        boosts: dict[str, float] = {}
        for node in self.nodes.values():
            if not node.terms:
                continue
            overlap = query & set(node.terms)
            if not overlap:
                continue
            coverage = len(overlap) / len(node.terms)
            score = coverage * (1.0 + 0.12 * max(0, node.depth - 1))
            for doc_id in node.entries:
                if score > boosts.get(doc_id, 0.0):
                    boosts[doc_id] = score
        if not boosts:
            return {}
        top = max(boosts.values()) or 1.0
        return {doc_id: round(value / top, 4) for doc_id, value in boosts.items()}

    def stats(self) -> dict:
        return {
            "nodes": len(self.nodes),
            "entries": len(self.entry_paths),
        }


class HybridRetriever:
    """BM25 + TF-IDF + 分层加成；按包增量更新。"""

    def __init__(
        self,
        cards: tuple[ExpertCard, ...] = (),
        *,
        k1: float = DEFAULT_K1,
        b: float = DEFAULT_B,
        expansion: int = DEFAULT_EXPANSION,
        hierarchy: bool = True,
    ) -> None:
        self.bm25 = BM25Index(k1=k1, b=b)
        self.hierarchy = HierarchyIndex()
        self.expansion = max(0, int(expansion))
        self.hierarchy_enabled = bool(hierarchy)
        self.cards: dict[str, ExpertCard] = {}
        self._tfidf = TfidfIndex(cards)
        for card in cards:
            self.upsert_pack(card, rebuild_tfidf=False)
        self._rebuild_tfidf()
        self.expansions = 0

    def _rebuild_tfidf(self) -> None:
        self._tfidf = TfidfIndex(tuple(self.cards.values()))

    def upsert_pack(self, card: ExpertCard, *, rebuild_tfidf: bool = True) -> None:
        self.cards[card.folder] = card
        self.bm25.upsert_pack(card)
        if self.hierarchy_enabled:
            self.hierarchy.upsert_pack(card)
        if rebuild_tfidf:
            self._rebuild_tfidf()

    def remove_pack(self, folder: str, *, rebuild_tfidf: bool = True) -> None:
        self.cards.pop(folder, None)
        self.bm25.remove_pack(folder)
        if self.hierarchy_enabled:
            self.hierarchy.remove_pack(folder)
        if rebuild_tfidf:
            self._rebuild_tfidf()

    @property
    def tfidf(self) -> TfidfIndex:
        return self._tfidf

    def _expansion_terms(
        self,
        first_hits: list[BM25Hit],
        query_terms: tuple[str, ...],
    ) -> list[str]:
        """从首轮高分文档里取“没出现在问题里”的内容词（RM3 的轻量替身）。"""
        if not first_hits:
            return []
        used = set(query_terms)
        scores: dict[str, float] = {}
        for rank, hit in enumerate(first_hits[:2]):
            document = self.bm25.documents.get(hit.doc_id)
            if document is None:
                continue
            weight = 1.0 / (rank + 1)
            for term, frequency in Counter(document.tokens).items():
                if term in used or len(term) <= 2:
                    continue
                scores[term] = scores.get(term, 0.0) + frequency * weight
        ordered = sorted(
            scores.items(),
            key=lambda item: (-item[1], item[0]),
        )
        out: list[str] = []
        for term, _score in ordered:
            if term not in used:
                out.append(term)
            if len(out) >= self.expansion:
                break
        return out

    @staticmethod
    def _coverage(hit: BM25Hit, query_terms: tuple[str, ...]) -> float:
        total = len(set(query_terms)) or 1
        return len(set(hit.matched) & set(query_terms)) / total

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
    ) -> list[RetrievalHit]:
        query_terms = tuple(tokenize(query))
        if not query_terms:
            return []
        first = self.bm25.search(query_terms, limit=max(limit, 8))
        expanded = False
        merged: dict[str, BM25Hit] = {hit.doc_id: hit for hit in first}

        # 首轮覆盖不足才扩展；扩展结果只“补”，不覆盖原有更高分
        if self.expansion and first and self._coverage(first[0], query_terms) < EXPANSION_COVERAGE:
            extra = self._expansion_terms(first, query_terms)
            if extra:
                self.expansions += 1
                expanded = True
                second = self.bm25.search(
                    query_terms + tuple(extra), limit=max(limit, 8)
                )
                for hit in second:
                    current = merged.get(hit.doc_id)
                    if current is None or hit.score > current.score:
                        merged[hit.doc_id] = hit

        bm25_hits = list(merged.values())
        tfidf_hits = self._tfidf.search(query, limit=max(limit, 8))
        hierarchy = (
            self.hierarchy.boosts(query_terms) if self.hierarchy_enabled else {}
        )
        bm25_top = max((hit.score for hit in bm25_hits), default=0.0) or 1.0
        tfidf_by_id = {
            _doc_id(hit.folder, hit.entry_id): hit for hit in tfidf_hits
        }
        bm25_by_id = {hit.doc_id: hit for hit in bm25_hits}
        doc_ids = set(bm25_by_id) | set(tfidf_by_id) | set(hierarchy)

        results: list[RetrievalHit] = []
        for doc_id in doc_ids:
            bm25_hit = bm25_by_id.get(doc_id)
            tfidf_hit = tfidf_by_id.get(doc_id)
            document = self.bm25.documents.get(doc_id)
            if document is None and tfidf_hit is not None:
                document = RetrievalDocument(
                    doc_id=doc_id,
                    folder=tfidf_hit.folder,
                    entry_id=tfidf_hit.entry_id,
                    question=tfidf_hit.question,
                    tokens=(),
                )
            if document is None:
                continue
            bm25_norm = (bm25_hit.score / bm25_top) if bm25_hit else 0.0
            tfidf_score = tfidf_hit.score if tfidf_hit else 0.0
            hierarchy_score = hierarchy.get(doc_id, 0.0)
            score = 0.60 * bm25_norm + 0.30 * tfidf_score + 0.10 * hierarchy_score
            if score < MIN_HYBRID_SCORE:
                continue
            # 只有 BM25 的弱单字命中（覆盖很低）时不要；有 TF-IDF/标题证据则放行
            if (
                bm25_hit is not None
                and tfidf_hit is None
                and hierarchy_score <= 0
                and self._coverage(bm25_hit, query_terms) < 0.25
            ):
                continue
            results.append(
                RetrievalHit(
                    folder=document.folder,
                    entry_id=document.entry_id,
                    question=document.question,
                    score=round(min(1.0, score), 4),
                    bm25=round(bm25_hit.score, 4) if bm25_hit else 0.0,
                    tfidf=round(tfidf_score, 4),
                    hierarchy=round(hierarchy_score, 4),
                    matched_terms=bm25_hit.matched if bm25_hit else (),
                    expanded=expanded,
                    via="hybrid",
                )
            )

        results.sort(key=lambda hit: (-hit.score, hit.folder, hit.entry_id))
        return results[:limit]

    def search_pack(
        self, folder: str, query: str, *, limit: int = 3
    ) -> list[RetrievalHit]:
        """只在一个包里查（给规则命中后的“BM25 佐证/补条目”用）。"""
        allowed = {
            doc_id
            for doc_id, document in self.bm25.documents.items()
            if document.folder == folder
        }
        if not allowed:
            return []
        query_terms = tuple(tokenize(query))
        if not query_terms:
            return []
        hits = self.bm25.search(query_terms, limit=limit, allowed=allowed)
        top = max((hit.score for hit in hits), default=0.0) or 1.0
        return [
            RetrievalHit(
                folder=hit.folder,
                entry_id=hit.entry_id,
                question=hit.question,
                score=round(hit.score / top, 4),
                bm25=round(hit.score, 4),
                matched_terms=hit.matched,
                via="bm25-pack",
            )
            for hit in hits
        ]

    def stats(self) -> dict:
        return {
            **self.bm25.stats(),
            "hierarchy": self.hierarchy.stats(),
            "expansions": self.expansions,
            "tfidf_documents": self._tfidf.size,
        }


__all__ = [
    "DEFAULT_K1",
    "DEFAULT_B",
    "DEFAULT_EXPANSION",
    "MIN_HYBRID_SCORE",
    "EXPANSION_COVERAGE",
    "RetrievalDocument",
    "BM25Hit",
    "RetrievalHit",
    "BM25Index",
    "HierarchyIndex",
    "HybridRetriever",
]

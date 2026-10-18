"""路由:把小析拆出来的候选逐条丢给各个数据包,哪个包里能对上就查哪个。

它不是"猜一个最像的",而是**逐条尝试 + 累计命中**:

  · 每条候选对每个数据包打分:领域词 +3、条目标题/别名 +4、条目关键词 +3;
  · ASCII 候选再按编辑距离兜一层(HMTL ≈ HTML,距离 1);
  · 分数 ≥ 阈值(3)的包都算命中 —— 所以**问得广就会查多个包**;
  · 每个包最多带一个"兜底问法"(命中的条目原题),给打错字的情况用;
  · 规则**一个领域包都没命中**时,再用 TF-IDF 余弦相似度补一次
    (轻量检索的最小实现,见 `tfidf.py`;低于阈值仍然不答)。

路由只读各包的 pack.json,不启动核心进程;真正答案由后续管线去取。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from . import budget as budget_mod
from .calibration import Calibrator
from .catalog import CATALOG, ExpertCard
from .decomposer import Candidate, Decomposition, normalize
from .relations import PackGraph
from .retrieval import HybridRetriever, RetrievalHit
from .settings import RetrievalSettings
from .tfidf import MIN_COSINE, SimilarHit, TfidfIndex, tokenize
from .trust import TrustMap

MIN_SCORE = 3
MAX_TARGETS = 3
#: 规则没命中时，相似度最多补几个包
MAX_SIMILAR_TARGETS = 3
#: 再命中最多层数(每层:反向关键词 → 查没查过的包 → 认出父包)
MAX_REHIT_ROUNDS = 3
#: 每轮最多取多少条反向关键词(避免一次铺太开)
MAX_REVERSE_KEYWORDS = 8

# 泛问词:它们出现在几乎每个问题里,不携带领域信息。
# 不排除的话,「向量场和电场的关系」会因「关系」二字命中伦理条目(实测踩到)。
GENERIC_TERMS: frozenset[str] = frozenset({
    "关系", "区别", "原理", "定义", "意思", "作用", "特点", "优点", "缺点",
    "例子", "基础", "概念", "介绍", "解释", "说明", "方法", "方式",
    "什么", "怎么", "怎样", "如何", "为什么", "为何", "是否", "可以", "能否",
    "多少", "哪个", "哪些", "包括", "属于", "以及", "如果", "那么",
})

_PUNCT_RE = re.compile(r"[，。！？、；:：,.!?;~～\"'“”‘’()（）\[\]【】《》<>]+")
_ASCII_RE_CACHE: dict[str, re.Pattern[str]] = {}


def _key(text: str) -> str:
    cleaned = _PUNCT_RE.sub(" ", normalize(text).lower())
    return " ".join(cleaned.split())


def _ascii_pattern(key: str) -> re.Pattern[str] | None:
    """ASCII 词按词边界匹配;提前把正则编好,别在每轮打分里重复编。"""
    if not (key.isascii() and key.isalnum()):
        return None
    pattern = _ASCII_RE_CACHE.get(key)
    if pattern is None:
        pattern = re.compile(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])")
        _ASCII_RE_CACHE[key] = pattern
    return pattern


@dataclass(frozen=True)
class _Term:
    """一个检索词的预计算结果:原文 + 归一化键 + (ASCII 词的)边界正则。"""

    raw: str
    key: str
    pattern: re.Pattern[str] | None = None

    def hit(self, key: str) -> bool:
        if not self.key:
            return False
        if self.pattern is not None:
            return self.pattern.search(key) is not None
        return self.key in key


def _term(raw: str) -> _Term:
    key = _key(str(raw))
    return _Term(raw=str(raw), key=key, pattern=_ascii_pattern(key))


@dataclass(frozen=True)
class _EntryIndex:
    """一个条目预计算好的检索键(查找时不再反复 normalize)。"""

    id: str
    aliases: tuple[str, ...]          # 题目 + 别名,已归一化
    keywords: tuple[_Term, ...]
    secondary_keywords: tuple[_Term, ...]
    tags: tuple[_Term, ...]


@dataclass(frozen=True)
class _CardIndex:
    scope: tuple[_Term, ...]
    entries: tuple[_EntryIndex, ...]
    ascii_scope: tuple[tuple[str, str], ...]        # (原文, 小写)
    ascii_keywords: tuple[tuple[str, str], ...]     # (条目 id, 小写)


def _index_card(card: ExpertCard) -> _CardIndex:
    """每个数据包只在装配时归一化一次;一包 20+ 条目 × 多条候选不再重复算。"""
    scope = tuple(term for term in (_term(t) for t in card.scope) if term.key)
    entries: list[_EntryIndex] = []
    ascii_scope: list[tuple[str, str]] = []
    ascii_keywords: list[tuple[str, str]] = []

    for term in scope:
        if term.key.isascii() and len(term.key) >= 3:
            ascii_scope.append((term.raw, term.key))

    for entry in card.entries:
        eid = str(entry.get("id") or "")
        aliases: list[str] = []
        for alias in (entry.get("question"),) + tuple(entry.get("aliases") or ()):
            alias_key = _key(str(alias))
            if alias_key:
                aliases.append(alias_key)
        keywords: list[_Term] = []
        for keyword in entry.get("keywords") or ():
            term = _term(str(keyword))
            if term.key:
                keywords.append(term)
            if term.key.isascii() and len(term.key) >= 3:
                ascii_keywords.append((eid, term.key))
        secondary: list[_Term] = []
        for keyword in entry.get("secondary_keywords") or ():
            term = _term(str(keyword))
            if term.key and all(term.key != primary.key for primary in keywords):
                secondary.append(term)
        tags = tuple(term for term in (_term(str(t)) for t in entry.get("tags") or ()) if term.key)
        entries.append(_EntryIndex(
            id=eid, aliases=tuple(dict.fromkeys(aliases)),
            keywords=tuple(keywords), secondary_keywords=tuple(secondary),
            tags=tags,
        ))

    return _CardIndex(
        scope=scope,
        entries=tuple(entries),
        ascii_scope=tuple(ascii_scope),
        ascii_keywords=tuple(ascii_keywords),
    )


def _damerau1(a: str, b: str) -> bool:
    """编辑距离 ≤ 1(替换 / 插入 / 删除 / 相邻互换)。"""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:
        diff = [i for i in range(la) if a[i] != b[i]]
        if len(diff) == 1:
            return True
        if (
            len(diff) == 2
            and diff[1] == diff[0] + 1
            and a[diff[0]] == b[diff[1]]
            and a[diff[1]] == b[diff[0]]
        ):
            return True
        return False
    if la > lb:
        a, b = b, a
        la, lb = lb, la
    i = j = 0
    skipped = False
    while i < la and j < lb:
        if a[i] == b[j]:
            i += 1
            j += 1
        elif skipped:
            return False
        else:
            skipped = True
            j += 1
    return True


@dataclass(frozen=True)
class RouteTarget:
    card: ExpertCard
    score: int
    reasons: tuple[str, ...]
    entry_ids: tuple[str, ...]
    fallback_question: str | None
    #: 指定"先用哪句话问"（危机场景用条目原题，避免原句把对象判反）
    prefer_question: str | None = None
    #: direct = 这句话自己命中;reverse = 反向关键词命中(第 wave 轮)
    via: str = "direct"
    wave: int = 0
    #: 校准后的置信度（0–1）与最佳条目的信任级别
    confidence: float = 0.0
    trust: str = "normal"
    #: 混合检索/BM25 是否给这个包补过条目
    hybrid: bool = False

    def label(self) -> str:
        return f"数据包 {self.card.folder}"


@dataclass(frozen=True)
class Routing:
    targets: tuple[RouteTarget, ...]
    tried: int
    notes: str
    #: 达到阈值、但因为 max_packs 上限没查的包(可以问用户要不要补查)
    dropped: tuple[RouteTarget, ...] = ()
    #: 再命中:反向关键词命中了"包含已命中包"的父包 (child, parent)
    rehits: tuple[tuple[str, str], ...] = ()

    def labels(self) -> list[str]:
        return [t.label() for t in self.targets]


def _card_stamp(card: ExpertCard) -> tuple[int, int]:
    try:
        stat = card.pack_path.stat()
    except OSError:
        return (0, 0)
    return (stat.st_mtime_ns, stat.st_size)


class Router:
    """候选 → 数据包。只读 pack.json,不启动核心。"""

    def __init__(
        self,
        cards: tuple[ExpertCard, ...] = CATALOG,
        *,
        retrieval: RetrievalSettings | None = None,
        trust: TrustMap | None = None,
    ) -> None:
        self.cards = tuple(cards)
        self.retrieval_settings = retrieval or RetrievalSettings()
        self.trust = trust or TrustMap()
        self.graph = PackGraph.from_cards(self.cards)
        # 每个包的领域词/条目键只归一化一次(查找的热路径不再重复 normalize)
        self._index: dict[str, _CardIndex] = {
            card.folder: _index_card(card) for card in self.cards
        }
        self._stamps: dict[str, tuple[int, int]] = {
            card.folder: _card_stamp(card) for card in self.cards
        }
        # 规则检索的补充：BM25 倒排 + TF-IDF + 分层；只在规则弱/没命中时补
        self.retrieval = HybridRetriever(
            self.cards,
            k1=self.retrieval_settings.k1,
            b=self.retrieval_settings.b,
            expansion=self.retrieval_settings.query_expansion
            if self.retrieval_settings.bm25 else 0,
            hierarchy=self.retrieval_settings.hierarchy,
        )
        self.calibrator = Calibrator(
            min_confidence=self.retrieval_settings.abstain_confidence,
        )
        # 兼容旧接口：有人可能直接读 router.tfidf
        self.tfidf = self.retrieval.tfidf

    # ---- 增量更新 --------------------------------------------------------
    def refresh(self, cards: tuple[ExpertCard, ...]) -> "Router":
        """只重建真正变过的包；其它包的倒排表/规则索引原样保留。

        这是“教完一条记忆只更新记忆包”的落点：`GoChat.reload()` 不再
        每次 new 一个 Router，而是把新 cards 交给这里。
        """
        incoming = {card.folder: card for card in cards}
        removed = set(self._index) - set(incoming)
        for folder in removed:
            self._index.pop(folder, None)
            self._stamps.pop(folder, None)
            self.retrieval.remove_pack(folder)

        changed: list[str] = []
        for folder, card in incoming.items():
            stamp = _card_stamp(card)
            if self._stamps.get(folder) != stamp:
                self._index[folder] = _index_card(card)
                self.retrieval.upsert_pack(card)
                self._stamps[folder] = stamp
                changed.append(folder)

        self.cards = tuple(incoming.values())
        self.graph = PackGraph.from_cards(self.cards)
        self.tfidf = self.retrieval.tfidf
        # 变更 + 移除都算“这次动过的包”，`:stat` 才说得清
        self.last_refresh = tuple(changed + sorted(removed))
        return self

    def set_trust(self, trust: TrustMap) -> None:
        self.trust = trust

    def stats(self) -> dict:
        stats = self.retrieval.stats()
        stats["packs"] = len(self.cards)
        stats["last_refresh"] = list(getattr(self, "last_refresh", ()))
        return stats

    # ---- 单条候选打分 ----------------------------------------------------
    def _score(self, card: ExpertCard, cand: Candidate,
               key: str | None = None) -> tuple[int, list[str], dict[str, int]]:
        """(这个包的总分, 命中理由, 每个条目的得分)。

        条目得分单独留下,是为了能选对"兜底问法":同一个候选可能既命中
        引力波条目的别名(4 分),又命中万有引力条目的关键词(3 分)——
        该用**分最高**的那条,而不是"先出现"的那条。

        `key` 是候选归一化后的检索键:由 `_score_targets` 每条候选只算一次,
        这样"候选 × 数据包"不会重复走同一条 normalize。
        """
        key = key if key is not None else _key(cand.text)
        if not key or key in GENERIC_TERMS:
            return 0, [], {}
        index = self._index[card.folder]
        best = 0
        reasons: list[str] = []
        entry_scores: dict[str, int] = {}

        def bump(eid: str, points: int) -> None:
            if points > entry_scores.get(eid, 0):
                entry_scores[eid] = points

        for term in index.scope:
            if term.hit(key):
                best = max(best, 3)
                reasons.append(f"领域词:{term.raw}")

        # 变体候选(排列/谐音/错字)只认领域词与"完全相同的条目名":
        # 否则「向量场」的重排+谐音能凑出「常量」,把 java 包也拉进来(实测踩到)。
        variant = cand.kind in ("perm", "homophone", "ascii_typo")
        for entry in index.entries:
            eid = entry.id
            if not variant:
                for term in entry.keywords:
                    if term.key in GENERIC_TERMS:
                        continue
                    if term.hit(key):
                        best = max(best, 3)
                        reasons.append(f"关键词:{term.raw}@{eid}")
                        bump(eid, 3)
                for term in entry.secondary_keywords:
                    if term.key in GENERIC_TERMS:
                        continue
                    if term.hit(key):
                        best = max(best, 2)
                        reasons.append(f"次要关键词:{term.raw}@{eid}")
                        bump(eid, 2)
            for alias_key in entry.aliases:
                if len(alias_key) < 2:
                    continue
                if variant:
                    strong = alias_key == key
                else:
                    strong = (
                        alias_key == key
                        or alias_key in key
                        or (len(key) >= 3 and key in alias_key and len(alias_key) - len(key) <= 3)
                    )
                if strong:
                    best = max(best, 4)
                    reasons.append(f"条目:{eid}")
                    bump(eid, 4)
                    break

        # ASCII 错字兜底:HMTL ≈ HTML
        text = cand.text.strip()
        if text.isascii() and len(text) >= 3:
            lowered = text.lower()
            for raw, term_key in index.ascii_scope:
                if _damerau1(lowered, term_key):
                    best = max(best, 3)
                    reasons.append(f"近似领域词:{raw}")
            for eid, term_key in index.ascii_keywords:
                if _damerau1(lowered, term_key):
                    best = max(best, 3)
                    reasons.append(f"近似关键词:{term_key}@{eid}")
                    bump(eid, 3)

        return best, list(dict.fromkeys(reasons)), entry_scores

    # ---- 主流程 ----------------------------------------------------------
    def route(self, decomposition: Decomposition,
              max_targets: int | None = None,
              rehit_rounds: int = 0) -> Routing:
        """候选 → 数据包(可选:反向关键词命中 → 再命中包含它的父包)。

        max_targets:这一次最多查几个包(用户可配 `max_packs`)。
                     上限只做"截断",不降低阈值 —— 精确性由 MIN_SCORE 守。

        rehit_rounds:**再命中**轮数(默认 0 = 关闭)。每一轮:
          1. 从上一轮命中的条目里取关键词(排除已经用过的);
          2. 用这些关键词去查**还没查过**的包(每轮最多 max_targets 个);
          3. 如果这轮命中的包**包含**已命中的包(父包),记一次"再命中"
             (child, parent)—— 回答时会说明"既有子包的性质,也有父包的性质"。
        后面的轮次不会再用已经用过的关键词,所以不会来回打转。
        """
        targets = self._score_targets(decomposition.candidates)
        targets = self._fuse(targets, decomposition.original)
        tried = len(decomposition.candidates)
        notes = (
            f"逐条尝试 {tried} 条候选,"
            f"{len(targets)} 个数据包达到阈值(≥{MIN_SCORE})"
        )

        # 规则一个领域包都没中(只剩兜底/为空)时,让混合检索补一次。
        # 这是"轻量检索的最小实现":命中仍要过阈值,过了才追加候选包。
        targets, similar_note = self._with_similarity(targets, decomposition.original)
        notes += similar_note

        # 数据包可选的"转交":pack.json 的 expert.routes 声明
        # {"keywords": [...], "to": "physics"} —— 命中关键词就把问题再交给那个包。
        existing = {t.card.folder for t in targets}
        text_key = _key(decomposition.original)
        extra: list[RouteTarget] = []
        for target in targets:
            for rule in target.card.routes:
                to = str(rule.get("to") or "").strip()
                if not to or to in existing:
                    continue
                keywords = tuple(str(k) for k in rule.get("keywords") or ())
                if keywords and not any(_term(k).hit(text_key) for k in keywords):
                    continue
                card = next((c for c in self.cards if c.folder == to), None)
                if card is None:
                    continue
                extra.append(
                    RouteTarget(
                        card=card,
                        score=0,
                        reasons=(f"转交:{target.card.folder}",),
                        entry_ids=(),
                        fallback_question=None,
                        via="handoff",
                    )
                )
                existing.add(to)
        if extra:
            targets.extend(extra)
            targets.sort(key=lambda t: (-t.score, self._rank(t.card.folder)))
            notes += f";经 routes 转交 {len(extra)} 个"

        limit = self._limit(max_targets)
        dropped = targets[limit:]
        kept = targets[:limit]
        if dropped:
            notes += f";按分数只留前 {limit} 个(max_packs 上限),另有 {len(dropped)} 个达标的没查"

        all_targets = list(kept)
        rehits: list[tuple[str, str]] = []
        rounds = self._rounds(rehit_rounds)
        used = {_key(cand.text) for cand in decomposition.candidates}
        wave_targets = list(kept)

        for wave in range(1, rounds + 1):
            keywords = self._reverse_keywords(wave_targets, used)
            if not keywords:
                notes += f";第 {wave} 轮再命中:没有可用的反向关键词"
                break
            used.update(_key(word) for word in keywords)
            hit_folders = {target.card.folder for target in all_targets}
            scored = self._score_targets(
                tuple(Candidate(word, "reverse", f"第{wave}轮") for word in keywords)
            )
            fresh = [t for t in scored if t.card.folder not in hit_folders][:limit]
            if not fresh:
                notes += f";第 {wave} 轮再命中:{len(keywords)} 条反向关键词没有命中新包"
                break
            fresh = [replace(t, via="reverse", wave=wave) for t in fresh]
            notes += (
                f";第 {wave} 轮再命中:{len(keywords)} 条反向关键词"
                f"命中 {len(fresh)} 个包(" + "、".join(t.card.folder for t in fresh) + ")"
            )
            for child in all_targets:
                for parent in fresh:
                    if self.graph.contains(parent.card.folder, child.card.folder):
                        rehits.append((child.card.folder, parent.card.folder))
            all_targets.extend(fresh)
            wave_targets = fresh

        return Routing(
            targets=tuple(all_targets),
            tried=tried,
            notes=notes,
            dropped=tuple(dropped),
            rehits=tuple(dict.fromkeys(rehits)),
        )

    def _fuse(
        self,
        targets: list[RouteTarget],
        query: str,
    ) -> list[RouteTarget]:
        """规则命中后，用 BM25 给同一个包补条目/佐证（混合融合的“重排”半边）。

        只加权**已有目标**，不凭空加新包 —— 新包由下面的混合兜底负责。
        这样规则的高精度不会被 BM25 的宽召回稀释，但“别名只沾一点边”的
        条目仍可能被 BM25 补进候选。
        """
        if (
            not targets
            or not self.retrieval_settings.bm25
            or not self.retrieval_settings.hybrid
        ):
            return targets
        try:
            hits = self.retrieval.search(query, limit=max(8, len(targets) * 4))
        except Exception:                                   # noqa: BLE001
            return targets
        by_folder: dict[str, list[RetrievalHit]] = {}
        for hit in hits:
            by_folder.setdefault(hit.folder, []).append(hit)

        fused: list[RouteTarget] = []
        for target in targets:
            folder_hits = by_folder.get(target.card.folder, ())
            if not folder_hits:
                fused.append(target)
                continue
            best = folder_hits[0]
            entry_ids = list(target.entry_ids)
            for hit in folder_hits[:3]:
                if hit.entry_id not in entry_ids:
                    entry_ids.append(hit.entry_id)
            entry_ids, omitted = budget_mod.select_entries(
                target.card, entry_ids, self.retrieval_settings.max_entries
            )
            bonus = 1 if best.score >= 0.55 else 0
            reasons = list(target.reasons)
            reasons.append(f"BM25 佐证:{best.entry_id}({best.score:.2f})")
            if omitted:
                reasons.append(f"World Info 预算省略 {omitted} 条")
            fused.append(
                replace(
                    target,
                    score=target.score + bonus,
                    reasons=tuple(dict.fromkeys(reasons))[:8],
                    entry_ids=tuple(entry_ids),
                    fallback_question=target.fallback_question or best.question,
                    hybrid=True,
                    trust=self.trust.level(target.card, best.entry_id),
                )
            )
        fused.sort(
            key=lambda target: (
                -target.score,
                -getattr(target.card, "priority", 100),
                self._rank(target.card.folder),
            )
        )
        return fused

    def _with_similarity(
        self,
        targets: list[RouteTarget],
        query: str,
    ) -> tuple[list[RouteTarget], str]:
        """规则没命中领域包时，用 BM25 + TF-IDF + 分层补候选。"""
        if any(not target.card.is_general for target in targets):
            return targets, ""

        if not self.retrieval_settings.hybrid or not self.retrieval_settings.bm25:
            hits = self.retrieval.tfidf.search(query, limit=MAX_SIMILAR_TARGETS * 2)
            if not hits:
                return targets, f";规则未命中领域包,TF-IDF 没找到≥{MIN_COSINE:.2f}的条目"
            return self._similar_targets_from_tfidf(targets, hits, query)

        hits = self.retrieval.search(query, limit=MAX_SIMILAR_TARGETS * 3)
        if not hits:
            return targets, (
                ";规则未命中领域包,混合检索(BM25+TF-IDF)没找到足够强的条目"
            )

        total_terms = len(set(tokenize(query))) or 1
        best_by_folder: dict[str, RetrievalHit] = {}
        for hit in hits:
            if hit.folder not in best_by_folder:
                best_by_folder[hit.folder] = hit

        existing = {target.card.folder for target in targets}
        similar: list[RouteTarget] = []
        for hit in best_by_folder.values():
            if hit.folder in existing:
                continue
            card = next((c for c in self.cards if c.folder == hit.folder), None)
            if card is None:
                continue
            folder_hits = [item for item in hits if item.folder == hit.folder]
            trust = self.trust.level(card, hit.entry_id)
            matched = len(hit.matched_terms) or max(
                1, int(round(hit.tfidf * total_terms))
            )
            calibration = self.calibrator.calibrate_hybrid(
                [item.score for item in folder_hits],
                matched=matched,
                total=total_terms,
                trust=trust,
            )
            # 校准弃权：证据太弱就不硬答；高置信只加一点分，不是免死金牌
            if calibration.abstain:
                continue
            similar.append(
                RouteTarget(
                    card=card,
                    score=max(MIN_SCORE, int(round(hit.score * 10))),
                    reasons=(
                        f"混合检索 {hit.score:.2f}"
                        f"(BM25 {hit.bm25:.2f} / TF-IDF {hit.tfidf:.2f}"
                        f" / 分层 {hit.hierarchy:.2f})",
                    ),
                    entry_ids=(hit.entry_id,),
                    fallback_question=hit.question,
                    via="similarity",
                    confidence=calibration.value,
                    trust=trust,
                    hybrid=True,
                )
            )
            if len(similar) >= MAX_SIMILAR_TARGETS:
                break

        if not similar:
            return targets, (
                ";规则未命中领域包,混合检索有候选但校准后选择弃权"
            )

        added = {target.card.folder for target in similar}
        combined = similar + [target for target in targets if target.card.folder not in added]
        combined.sort(
            key=lambda target: (
                -target.score,
                -getattr(target.card, "priority", 100),
                -target.confidence,
                self._rank(target.card.folder),
            )
        )
        return combined, (
            f";规则未命中领域包,混合检索(BM25+TF-IDF)补 {len(similar)} 个"
            f"(校准阈值 {self.calibrator.min_confidence:.2f})"
        )

    def _similar_targets_from_tfidf(
        self,
        targets: list[RouteTarget],
        hits: list[SimilarHit],
        query: str,
    ) -> tuple[list[RouteTarget], str]:
        """旧路径（retrieval.hybrid=false）：只看 TF-IDF。"""
        best_by_folder: dict[str, SimilarHit] = {}
        for hit in hits:
            if hit.folder not in best_by_folder:
                best_by_folder[hit.folder] = hit
        existing = {target.card.folder for target in targets}
        similar: list[RouteTarget] = []
        total_terms = len(set(tokenize(query))) or 1
        for hit in best_by_folder.values():
            if hit.folder in existing:
                continue
            card = next((c for c in self.cards if c.folder == hit.folder), None)
            if card is None:
                continue
            trust = self.trust.level(card, hit.entry_id)
            calibration = self.calibrator.calibrate_hybrid(
                [hit.score],
                matched=max(1, int(hit.score * total_terms)),
                total=total_terms,
                trust=trust,
            )
            if calibration.abstain:
                continue
            similar.append(
                RouteTarget(
                    card=card,
                    score=max(MIN_SCORE, int(round(hit.score * 10))),
                    reasons=(f"TF-IDF 相似度 {hit.score:.2f}",),
                    entry_ids=(hit.entry_id,),
                    fallback_question=hit.question,
                    via="similarity",
                    confidence=calibration.value,
                    trust=trust,
                )
            )
            if len(similar) >= MAX_SIMILAR_TARGETS:
                break
        if not similar:
            return targets, ";规则未命中领域包,TF-IDF 有候选但校准后选择弃权"
        added = {target.card.folder for target in similar}
        combined = similar + [target for target in targets if target.card.folder not in added]
        combined.sort(
            key=lambda target: (
                -target.score,
                -getattr(target.card, "priority", 100),
                -target.confidence,
                self._rank(target.card.folder),
            )
        )
        return combined, (
            f";规则未命中领域包,TF-IDF 相似度补 {len(similar)} 个"
            f"(阈值 {MIN_COSINE:.2f})"
        )

    def _reverse_keywords(
        self,
        targets: list[RouteTarget],
        used: set[str],
    ) -> tuple[str, ...]:
        """从刚命中的条目里取"该包还认识哪些词" —— 反向命中用。

        只取命中条目的 keywords(它们本来就是该包的检索键),排除本句已经试过的词,
        以及"关系/区别/什么"这类泛问词;每轮最多 8 条,避免一次铺太开。
        """
        out: list[str] = []
        for target in targets:
            if target.card.is_general:
                continue
            for eid in target.entry_ids[:2]:
                entry = target.card.entry(eid)
                if not entry:
                    continue
                for keyword in entry.get("keywords") or ():
                    item = str(keyword).strip()
                    key = _key(item)
                    if not key or len(key) < 2 or key in used or key in GENERIC_TERMS:
                        continue
                    if item not in out:
                        out.append(item)
        return tuple(out[:MAX_REVERSE_KEYWORDS])

    def route_focus(self, focus: tuple[str, ...], max_targets: int | None = None) -> Routing:
        """接着上一句问:只拿记忆里的关键词去找包。

        只在这个顺序里用:先按这句话自己查、**一个领域包都没命中**,才轮到这里。
        所以"上一轮是引力波,这一句问电场"不会被记忆劫持 —— 自己的话优先。
        """
        candidates = tuple(Candidate(term, "context", "上一轮") for term in focus)
        targets = self._score_targets(candidates)
        notes = (
            f"借上一轮的关键词 {'、'.join(focus)} 查 {len(candidates)} 条,"
            f"{len(targets)} 个数据包达到阈值(≥{MIN_SCORE})"
        )
        return self._finish(targets, len(candidates), notes, max_targets)

    def _finish(self, targets: list[RouteTarget], tried: int, notes: str,
                max_targets: int | None) -> Routing:
        limit = self._limit(max_targets)
        dropped: list[RouteTarget] = []
        if len(targets) > limit:
            dropped = targets[limit:]
            notes += f";按分数只留前 {limit} 个(max_packs 上限),另有 {len(dropped)} 个达标的没查"
            targets = targets[:limit]
        return Routing(targets=tuple(targets), tried=tried, notes=notes,
                       dropped=tuple(dropped))

    def _score_targets(self, candidates: tuple[Candidate, ...]) -> list[RouteTarget]:
        """给一批候选打分并挑出达标的包(按分数排序)。"""
        scores: dict[str, int] = {card.folder: 0 for card in self.cards}
        reasons: dict[str, list[str]] = {card.folder: [] for card in self.cards}
        entry_scores: dict[str, dict[str, int]] = {card.folder: {} for card in self.cards}

        for cand in candidates:
            key = _key(cand.text)          # 每条候选只归一化一次
            for card in self.cards:
                points, why, per_entry = self._score(card, cand, key=key)
                if points <= 0:
                    continue
                scores[card.folder] += points
                reasons[card.folder].extend(why)
                bucket = entry_scores[card.folder]
                for eid, entry_points in per_entry.items():
                    bucket[eid] = bucket.get(eid, 0) + entry_points

        targets: list[RouteTarget] = []
        for card in self.cards:
            total = scores[card.folder]
            if total < MIN_SCORE:
                continue
            bucket = entry_scores[card.folder]
            # 兜底问法取"条目分最高"的那条(同分按包里出现的先后)
            best_id = max(bucket, key=lambda k: bucket[k]) if bucket else None
            fallback = None
            if best_id:
                entry = card.entry(best_id)
                if entry:
                    fallback = str(entry.get("question") or "") or None
            ranked_entries = sorted(bucket, key=lambda k: -bucket[k])
            selected, omitted = budget_mod.select_entries(
                card, ranked_entries, self.retrieval_settings.max_entries
            )
            fallback_id = selected[0] if selected else best_id
            if fallback_id:
                entry = card.entry(fallback_id)
                if entry:
                    fallback = str(entry.get("question") or "") or None
            best_trust = (
                self.trust.level(card, fallback_id) if fallback_id else "normal"
            )
            calibrated = self.calibrator.calibrate(
                [float(value) for value in bucket.values()] or [float(total)],
                matched=len(bucket) or 1,
                total=max(1, len(bucket)),
                trust=best_trust,
            )
            why = list(reasons[card.folder])
            if omitted:
                why.append(f"World Info 预算省略 {omitted} 条")
            targets.append(
                RouteTarget(
                    card=card,
                    score=total,
                    reasons=tuple(dict.fromkeys(why))[:8],
                    entry_ids=tuple(selected),
                    fallback_question=fallback,
                    confidence=calibrated.value,
                    trust=best_trust,
                )
            )

        targets.sort(
            key=lambda t: (
                -t.score,
                -getattr(t.card, "priority", 100),
                -t.confidence,
                self._rank(t.card.folder),
            )
        )
        return targets

    def _limit(self, max_targets: int | None) -> int:
        """这一次最多查几个包:用户配置优先,但至少 1、至多包的总数。"""
        if max_targets is None:
            return MAX_TARGETS
        try:
            value = int(max_targets)
        except (TypeError, ValueError):
            return MAX_TARGETS
        return max(1, min(value, len(self.cards) or 1))

    @staticmethod
    def _rounds(rehit_rounds: object) -> int:
        """再命中轮数:0 = 关闭;上限 MAX_REHIT_ROUNDS。"""
        try:
            value = int(rehit_rounds)          # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0
        return max(0, min(value, MAX_REHIT_ROUNDS))

    def _rank(self, folder: str) -> int:
        for index, card in enumerate(self.cards):
            if card.folder == folder:
                return index
        return len(self.cards)

    def candidates_for(self, decomposition: Decomposition, folder: str) -> list[str]:
        """给调试用:列出让某个包命中的候选。"""
        out: list[str] = []
        for cand in decomposition.candidates:
            card = next((c for c in self.cards if c.folder == folder), None)
            if card and self._score(card, cand)[0] > 0:
                out.append(cand.label())
        return out


__all__ = [
    "RouteTarget",
    "Routing",
    "Router",
    "MIN_SCORE",
    "MAX_TARGETS",
    "MAX_REHIT_ROUNDS",
    "MAX_REVERSE_KEYWORDS",
    "GENERIC_TERMS",
]

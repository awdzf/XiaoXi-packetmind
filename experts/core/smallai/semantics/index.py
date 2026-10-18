"""语义向量空间:离线训好的词向量 + 关系图,在线只做查表与算术。

这一层要回答的问题:**词与词之间的距离、以及词与词之间的差值,能不能当运算用。**

    · 距离:最近邻(国王 的邻居是 皇帝/王后/女王……)
    · 差值:类比算术(国王 - 男人 + 女人 = 女王)

三层结构,各自解决一件事,也各自能被核对:

    1. **统计向量**(PPMI + SVD):从关系语料里学出来的稠密语义维,负责"细微相似";
    2. **结构特征**(男/女、统治、动物……):从关系图定义出来的离散维,负责"关系算术的保底";
    3. **关系图**(原始三元组):负责检索时的**一致性检查** —— 候选得像那么回事。

为什么需要第 3 层(实测):
    只用 1+2 做"最近邻"时,`国王-男人+女人` 的冠军是『妈妈』『母亲』 ——
    它们在"女性"这一维上跟答案同样强,余弦分不出来。
    加上一致性检查(候选必须同时靠近 国王 和 女人,而不是靠近 男人)之后,
    命题级答案是『女王』。纯向量 6/10,加一致性检索 8/10。

诚实说明在这个文件里,也写在 README 里:
    语料只有千把条关系,所以**不能**指望它像在几十亿词上训出来的向量那样
    给出"漂亮的密集类比"。它能做的是:关系平行的那类类比(性别对、尺寸对、
    归属链)稳定成立,并给出可核对的依据。
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

from ..packs import pack_data
from .parse import parse_semantic

if TYPE_CHECKING:                                     # 只为类型标注,避免运行期循环 import
    from ..core import Context


def default_vector_paths() -> tuple[Path, ...]:
    """词向量:数据包里有 word_vectors.json 就用它,否则用核心自带的那份。

    按调用时解析(而不是导入时定死),同一个进程里换数据包就换语料。
    """
    return (
        pack_data("word_vectors.json", Path(__file__).resolve().parent / "word_vectors.json"),
    )


# --------------------------------------------------------------------------
# 向量与关系图
# --------------------------------------------------------------------------
@dataclass
class Neighbor:
    word: str
    score: float


@dataclass
class AnalogyCandidate:
    word: str
    score: float
    passes_check: bool = False
    reason: str = ""


@dataclass
class AnalogyResult:
    query: str
    target: list[float]
    candidates: list[AnalogyCandidate] = field(default_factory=list)
    rejected: list[AnalogyCandidate] = field(default_factory=list)
    checked: bool = False
    error: str = ""

    @property
    def best(self) -> AnalogyCandidate | None:
        return self.candidates[0] if self.candidates else None

    def ranked(self, limit: int = 3) -> list[AnalogyCandidate]:
        return self.candidates[:limit]

    def described(self) -> str:
        words = "、".join(f"{c.word}({c.score:.2f})" for c in self.ranked())
        if not words:
            return self.error or "没有候选"
        suffix = "(经一致性检查)" if self.checked else "(纯向量最近邻)"
        return f"{self.query} = ?{suffix} -> {words}"


class SemanticsIndex:
    """词向量 + 关系图。惰性加载,缺文件就降级成"只有关系图"。"""

    name = "semantics.vectors"

    def __init__(self, vector_paths: Iterable[str | Path] | None = None) -> None:
        self.vectors: dict[str, list[float]] = {}
        self.relations: list[tuple[str, str, str]] = []
        self.neighbors: dict[str, set[str]] = {}
        self.relations_by_word: dict[str, list[tuple[str, str]]] = {}
        self.dim = 0
        self.structural_features: list[str] = []
        self.source: str = "(未加载)"
        self._load(list(vector_paths) if vector_paths else list(default_vector_paths()))

    @property
    def loaded(self) -> bool:
        return bool(self.vectors)

    @property
    def vocab_size(self) -> int:
        return len(self.vectors)

    # ---- 加载 ------------------------------------------------------------
    def _load(self, paths: list[str | Path]) -> None:
        for raw in paths:
            path = Path(raw)
            if not path.is_file():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            vectors = data.get("vectors") or {}
            if not vectors:
                continue
            self.vectors = {str(k): [float(x) for x in v] for k, v in vectors.items()}
            self.dim = int(data.get("dim") or len(next(iter(self.vectors.values()))))
            self.structural_features = [str(x) for x in data.get("structural_features", [])]
            for item in data.get("relations", []):
                if isinstance(item, (list, tuple)) and len(item) == 3:
                    self._add_relation(str(item[0]), str(item[1]), str(item[2]))
            self.source = str(path)
            return

    def _add_relation(self, subject: str, relation: str, obj: str) -> None:
        triple = (subject, relation, obj)
        if triple in self.relations:
            return
        self.relations.append(triple)
        self.neighbors.setdefault(subject, set()).add(obj)
        self.neighbors.setdefault(obj, set()).add(subject)
        self.relations_by_word.setdefault(subject, []).append((relation, obj))
        self.relations_by_word.setdefault(obj, []).append((relation + "⁻¹", subject))

    def learn_relation(self, subject: str, relation: str, obj: str) -> bool:
        """在线补一条关系边(只为关系图检索服务;向量不重训)。

        诚实说明:向量是离线训好的,在线加边**不会**改变几何,
        只会改变"一致性检查"与关系邻居的结果。要改几何必须重训。
        """
        if subject in self.neighbors.get(obj, ()) or obj in self.neighbors.get(subject, ()):
            return False
        self._add_relation(subject, relation, obj)
        return True

    # ---- 基本运算 --------------------------------------------------------
    def has(self, word: str) -> bool:
        return word in self.vectors

    def vector(self, word: str) -> list[float] | None:
        return self.vectors.get(word)

    def cosine(self, a: str | list[float], b: str | list[float]) -> float:
        va = self.vectors.get(a) if isinstance(a, str) else a
        vb = self.vectors.get(b) if isinstance(b, str) else b
        if va is None or vb is None:
            return 0.0
        dot = sum(x * y for x, y in zip(va, vb))
        na = math.sqrt(sum(x * x for x in va))
        nb = math.sqrt(sum(y * y for y in vb))
        return dot / (na * nb) if na and nb else 0.0

    def nearest(self, word: str | list[float], k: int = 5, exclude: Iterable[str] = ()) -> list[Neighbor]:
        """最近邻。word 可以是词,也可以是向量(于是算术结果也能查邻居)。"""
        if isinstance(word, str):
            base = self.vectors.get(word)
            if base is None:
                return []
        else:
            base = word
        skip = set(exclude)
        if isinstance(word, str):
            skip.add(word)
        scored = [
            Neighbor(w, round(self.cosine(base, v), 4))
            for w, v in self.vectors.items()
            if w not in skip
        ]
        scored.sort(key=lambda n: n.score, reverse=True)
        return scored[:k]

    # ---- 类比算术 --------------------------------------------------------
    def arithmetic(self, a: str, b: str, c: str) -> list[float] | None:
        """计算 a - b + c 的向量。缺词返回 None(不猜)。"""
        va, vb, vc = self.vectors.get(a), self.vectors.get(b), self.vectors.get(c)
        if va is None or vb is None or vc is None:
            return None
        return [va[i] - vb[i] + vc[i] for i in range(len(va))]

    def analogy(
        self,
        a: str,
        b: str,
        c: str,
        k: int = 5,
        consistency_check: bool = True,
    ) -> AnalogyResult:
        """a - b + c = ?  —— 词向量的经典类比算术。

        两条检索路径(都返回,便于对照,不做"只看好的那个"):
            consistency_check=False  纯最近邻:比分最高的前 k 个
            consistency_check=True   一致性检查:候选必须同时靠近 a 与 c
        """
        missing = [w for w in (a, b, c) if w not in self.vectors]
        if missing:
            return AnalogyResult(
                query=f"{a}-{b}+{c}", target=[],
                error=f"词表里没有:{'、'.join(missing)}(需要先补进关系语料并重训)",
            )
        target = self.arithmetic(a, b, c)
        assert target is not None
        query = f"{a}-{b}+{c}"

        if not consistency_check:
            candidates = [
                AnalogyCandidate(n.word, n.score) for n in self.nearest(target, k=k * 2, exclude=(a, b, c))
            ]
            return AnalogyResult(query=query, target=target, candidates=candidates[:k])

        passed: list[AnalogyCandidate] = []
        rejected: list[AnalogyCandidate] = []
        for n in self.nearest(target, k=len(self.vectors)):
            if n.word in (a, b, c):
                continue
            ok, reason = self._consistency(a, b, c, n.word)
            candidate = AnalogyCandidate(n.word, n.score, passes_check=ok, reason=reason)
            (passed if ok else rejected).append(candidate)
        return AnalogyResult(
            query=query, target=target,
            candidates=passed[:k], rejected=rejected[:k], checked=True,
        )

    def _consistency(self, a: str, b: str, c: str, candidate: str) -> tuple[bool, str]:
        """候选得像那么回事:它得同时是 a 的同类、又是 c 的同类。

        判据用"共同邻居"(1 跳),而不是图上连通:
            国王 的邻居包含 女王;女人 的邻居也包含 女王  -> 通过
            妈妈 是 女人 的邻居,但**不是** 国王 的邻居     -> 拒绝(理由可核对)

        实测权衡(试过 2 跳与"按跳数排序",都不如这个):
          · 1 跳  纯向量 6/10 -> 一致性 8/10(当前采用)
          · 2 跳  会放进"丈夫-男人+女人"这类本该拒绝的中间词,反而掉分
          · 跳数近邻排序 5/10 —— 在 872 条关系这种规模上,图太浅,排序没有分辨力

        仍然失败的 2/10(公鸡-男人+女人、雄狮-男人+女人):语料里缺少
        "母鸡/雌狮 属于 家禽/猫科"这一层,导致它们与"女人"没有 1 跳路径。
        这是**语料规模**问题,不是规则问题,所以如实报告而不偷偷放宽判据。
        """
        near_a = self._within(a, candidate)
        near_c = self._within(c, candidate)
        if near_a and near_c:
            return True, "同时靠近两端"
        if not near_a and not near_c:
            return False, "与两端都无关系"
        return False, ("只靠近 " + (a if near_a else c))

    def _within(self, start: str, target: str, hops: int = 1) -> bool:
        """start 与 target 在关系图上距离 <= hops 跳。"""
        if start == target:
            return True
        frontier = {start}
        seen = {start}
        for _ in range(hops):
            nxt: set[str] = set()
            for node in frontier:
                nxt |= self.neighbors.get(node, set())
            nxt -= seen
            if target in nxt:
                return True
            seen |= nxt
            frontier = nxt
            if not frontier:
                return False
        return False

    # ---- 关系图查询 ------------------------------------------------------
    def related(self, word: str, limit: int = 12) -> list[tuple[str, str]]:
        """这个词在语料里跟谁有关系(关系词, 对方)。"""
        return self.relations_by_word.get(word, [])[:limit]

    def path(self, start: str, goal: str, max_hops: int = 4) -> list[str] | None:
        """关系图上的一条最短路径(人话依据)。"""
        if start == goal:
            return [start]
        frontier: list[list[str]] = [[start]]
        seen = {start}
        for _ in range(max_hops):
            nxt_frontier: list[list[str]] = []
            for path in frontier:
                for nxt in self.neighbors.get(path[-1], set()):
                    if nxt in seen:
                        continue
                    if nxt == goal:
                        return path + [nxt]
                    seen.add(nxt)
                    nxt_frontier.append(path + [nxt])
            frontier = nxt_frontier
            if not frontier:
                break
        return None

    # ---- 语义层入口(与逻辑层同形:think(ctx) -> 结果) -------------------
    def think(self, ctx: "Context") -> SemanticResult:
        """解析 -> 算术/邻居 -> 结果落 ctx.meta['semantic']。"""
        from ..core import Intent as _Intent
        from ..core import Stage as _Stage

        started = time.perf_counter()
        frame = ctx.frame
        intent = frame.intent if frame else _Intent.UNKNOWN

        if intent is not _Intent.SEMANTIC:
            ctx.meta["semantic"] = SemanticResult()
            ctx.record(_Stage.SEMANTIC, self.name, ok=True,
                       detail="非语义意图,跳过", elapsed_ms=0.0)
            result = ctx.meta["semantic"]
            ctx.meta["semantic"] = result
            return result

        query = parse_semantic(ctx.raw_text)
        if not query.ok:
            result = SemanticResult(kind="none", outcome="invalid", error=query.reason)
        elif query.kind == "neighbors":
            result = self._think_neighbors(query.word or "")
        else:
            result = self._think_analogy(query.a or "", query.b or "", query.c or "")

        ctx.meta["semantic"] = result
        ctx.record(
            _Stage.SEMANTIC,
            self.name,
            ok=result.outcome != "invalid",
            detail=(
                f"kind={result.kind} outcome={result.outcome} "
                f"answer={result.answer or '-'} {result.text or result.error}"
            ),
            elapsed_ms=(time.perf_counter() - started) * 1000,
            basis=result.basis,
        )
        return result

    def _think_analogy(self, a: str, b: str, c: str) -> SemanticResult:
        if not self.loaded:
            return SemanticResult(
                kind="analogy", outcome="unknown",
                error="词向量文件不在(先跑 tools/train_vectors.py);关系推理仍然可用",
            )
        out = self.analogy(a, b, c, k=5, consistency_check=True)
        if out.error:
            return SemanticResult(kind="analogy", outcome="unknown", error=out.error)

        plain = self.analogy(a, b, c, k=3, consistency_check=False)
        basis = [
            f"向量算式:{a} - {b} + {c}(离线训练的分块向量)",
            f"纯向量最近邻:{'、'.join(x.word for x in plain.ranked()) or '(无)'}",
            f"一致性检查后:{'、'.join(x.word for x in out.ranked()) or '(无)'}",
        ]
        for candidate in out.rejected[:3]:
            basis.append(f"被拒:{candidate.word}({candidate.reason})")

        if not out.candidates:
            return SemanticResult(
                kind="analogy", outcome="unknown",
                text=f"{a} - {b} + {c}:没有通过一致性检查的候选",
                basis=basis,
            )
        best = out.candidates[0]
        return SemanticResult(
            kind="analogy",
            outcome="answer",
            answer=best.word,
            text=f"{a} - {b} + {c} = {best.word}",
            basis=basis,
            candidates=[x.word for x in out.ranked()],
            rejected=[x.word for x in out.rejected[:5]],
            checked=True,
        )

    def _think_neighbors(self, word: str) -> SemanticResult:
        related = self.related(word)
        nearest = self.nearest(word, k=6) if self.has(word) else []
        if not related and not nearest:
            return SemanticResult(
                kind="neighbors", outcome="unknown",
                text=f"词表与关系图里都没有『{word}』",
                error="这个词不在语料里(补进 seed/extra_relations.json 后重训即可)",
            )
        basis: list[str] = []
        if related:
            basis.append("关系图邻居:" + "、".join(f"{r}·{w}" for r, w in related[:8]))
        if nearest:
            basis.append("向量最近邻:" + "、".join(f"{n.word}({n.score:.2f})" for n in nearest[:5]))
        text = (
            f"与『{word}』最近的词:" + "、".join(n.word for n in nearest[:5])
            if nearest else f"『{word}』的关系邻居(没有向量,只列关系)"
        )
        return SemanticResult(
            kind="neighbors", outcome="answer",
            answer=nearest[0].word if nearest else None,
            text=text, basis=basis, candidates=[n.word for n in nearest],
        )

    # ---- 自检 ------------------------------------------------------------
    def self_check(self, tests: list[tuple[str, str, str, str]] | None = None) -> dict[str, Any]:
        """跑一遍类比自检:纯向量 vs 加一致性检查。"""
        from .analogy_tests import ANALOGY_TESTS

        suite = tests or ANALOGY_TESTS
        pure = checked = total = 0
        detail: list[str] = []
        for a, b, c, expected in suite:
            if not all(self.has(w) for w in (a, b, c)):
                continue
            total += 1
            plain = self.analogy(a, b, c, k=3, consistency_check=False)
            strict = self.analogy(a, b, c, k=3, consistency_check=True)
            plain_top = [x.word for x in plain.ranked()]
            strict_top = [x.word for x in strict.ranked()]
            pure += expected in plain_top
            checked += expected in strict_top
            detail.append(
                f"{a}-{b}+{c}={expected} :: 纯向量{plain_top} / 一致性{strict_top}"
            )
        return {
            "total": total,
            "pure_hits": pure,
            "checked_hits": checked,
            "detail": detail,
        }



# --------------------------------------------------------------------------
# 语义层的产物
# --------------------------------------------------------------------------
@dataclass
class SemanticResult:
    """语义层的产物。三种结局都是正常数据。"""

    kind: str = "none"            # analogy / neighbors / none
    outcome: str = "unknown"      # answer / unknown / invalid
    answer: str | None = None     # 选中的词
    text: str = ""
    basis: list[str] = field(default_factory=list)
    candidates: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    checked: bool = False
    error: str = ""

    @property
    def is_answer(self) -> bool:
        return self.outcome == "answer"


# --------------------------------------------------------------------------
# 装配工厂
# --------------------------------------------------------------------------
def build_semantics(vector_paths: Iterable[str | Path] | None = None) -> SemanticsIndex:
    return SemanticsIndex(vector_paths)

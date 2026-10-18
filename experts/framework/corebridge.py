"""单进程内核桥:让"查一个数据包"不再每问一次开一个 Python 子进程。

以前的做法(仍然是 `--subprocess` 的退路):每个数据包、每次提问都
`subprocess.run([python, core/run.py, --pack …])`。好处是隔离,代价是每次都要
重新解释器 + 重新导入 + 重新读 kb/词向量 —— 这是小设备上最大的固定开销。

现在默认走**同一个进程**:核心代码只 import 一次,每个数据包按需装配一条流水线并
缓存起来,下一问直接复用;只有当 `pack.json` / `kb.json` / `word_vectors.json`
的修改时间或大小变了,才重建那一条。于是:

    · 第一次问某个包:加载内核 + 装配那一包(和原来一次子进程差不多);
    · 之后再问同一个包:两次字符串检索,毫秒级;
    · 问另一个包:只装配那个包,已装好的不动。

**失败不静默**:这里抛出的任何异常都由 `framework/pipeline.py` 接住 ——
它会如实记一条"这一包在单进程里失败了,改用子进程重试",然后走原来的子进程路径。
所以单进程是加速,不是把隔离换掉:兜底还在。

它不打印任何东西(打印是终端的事),也不改数据包;写盘仍由 `lessons.py` 负责。
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Any

#: 唯一一份核心代码的位置(与 catalog.CORE_DIR 同一个约定,这里不 import catalog 免得循环)
CORE_DIR = Path(__file__).resolve().parents[1] / "core"

_LOCK = threading.RLock()
_READY = False
_ERROR: str | None = None
_PIPELINES: dict[str, tuple[tuple[str, ...], Any]] = {}
#: 词向量按**解析到的文件**共享:大多数包不覆盖 word_vectors.json,
#: 没必要时每个包各读一遍 180KB 的 JSON。
_SEMANTICS: dict[tuple[tuple[str, str], ...], Any] = {}
#: 事实库同理:同一份 kb.json 的包共用一个逻辑引擎(只读推理,不串知识)。
_LOGIC: dict[tuple[str, ...], Any] = {}
_STATS = {"builds": 0, "hits": 0, "asks": 0, "guards": 0}

_core_packs: Any = None
_core_pipeline: Any = None
_core_domain: Any = None
_core_safety: Any = None


def _ensure() -> None:
    """把核心 import 进来(只做一次)。失败就抛给调用方去回退子进程。"""
    global _READY, _ERROR, _core_packs, _core_pipeline, _core_domain, _core_safety
    if _READY:
        return
    with _LOCK:
        if _READY:
            return
        core_path = str(CORE_DIR)
        if core_path not in sys.path:
            sys.path.insert(0, core_path)
        try:
            import smallai.domain as core_domain
            import smallai.domain.safety as core_safety
            import smallai.packs as core_packs
            import smallai.pipeline as core_pipeline
        except Exception as exc:                       # noqa: BLE001
            _ERROR = f"{type(exc).__name__}: {exc}"
            raise
        _core_packs = core_packs
        _core_pipeline = core_pipeline
        _core_domain = core_domain
        _core_safety = core_safety
        _READY = True
        _ERROR = None


def available() -> bool:
    """单进程内核能不能用(只 import,不装配任何包)。"""
    try:
        _ensure()
    except Exception:                                  # noqa: BLE001
        return False
    return True


def _stamp(path: Path) -> str:
    """文件指纹:修改时间 + 大小。文件不在就给 "-"(换包/新增文件也会让指纹变)。"""
    try:
        stat = path.stat()
    except OSError:
        return "-"
    return f"{stat.st_mtime_ns}:{stat.st_size}"


def _fingerprint(card: Any) -> tuple[str, ...]:
    pack = Path(card.pack_path)
    pack_dir = Path(card.pack_dir)
    return (
        str(pack),
        _stamp(pack),
        _stamp(pack_dir / "kb.json"),
        _stamp(pack_dir / "word_vectors.json"),
    )


def _activate(card: Any) -> None:
    """把核心的"当前数据包"切到这一包(核心是单核,必须每次切)。"""
    _core_packs.set_pack_dir(str(Path(card.pack_dir).resolve()))


def _pipeline_for(card: Any) -> Any:
    """这一包的流水线:指纹没变就复用,变了就重建。"""
    key = str(Path(card.pack_path).resolve())
    fingerprint = _fingerprint(card)
    cached = _PIPELINES.get(key)
    if cached is not None and cached[0] == fingerprint:
        _STATS["hits"] += 1
        return cached[1]
    pipeline = _core_pipeline.Pipeline(
        logic=_logic_for(card),
        semantics=_semantics_for(),
    )
    _PIPELINES[key] = (fingerprint, pipeline)
    _STATS["builds"] += 1
    return pipeline


def _logic_for(card: Any) -> Any:
    """事实库按**数据包**共享/隔离。

    包里的 facts/rules 会并进事实库,所以不能只按 kb.json 路径共享 ——
    两个包共用默认 kb.json 时,它们各自的结构化知识必须分开。
    """
    from smallai.logic.engine import LogicEngine, default_kb_path

    path = Path(default_kb_path())
    pack = Path(card.pack_path)
    if getattr(card, "has_structure", False) or bool(
        getattr(card, "facts", ()) or getattr(card, "rules", ()) or getattr(card, "relations", ())
    ):
        # 有结构化知识:每个包的事实库不同,必须按包隔离
        key = (
            str(path.resolve()), _stamp(path),
            str(pack.resolve()), _stamp(pack),
        )
    else:
        # 普通问答包:共用默认事实库,不必每包各读一遍
        key = (str(path.resolve()), _stamp(path))
    cached = _LOGIC.get(key)
    if cached is None:
        cached = LogicEngine()
        _LOGIC[key] = cached
    return cached


def _semantics_for() -> Any:
    """词向量按**解析到的文件**共享;包自己覆盖了就自然拿到自己那份。"""
    from smallai.semantics import build_semantics
    from smallai.semantics.index import default_vector_paths

    paths = tuple(Path(p) for p in default_vector_paths())
    key = tuple((str(path.resolve()), _stamp(path)) for path in paths)
    cached = _SEMANTICS.get(key)
    if cached is None:
        cached = build_semantics(paths)
        _SEMANTICS[key] = cached
    return cached


def _semantic_payload(semantic: Any) -> dict[str, Any] | None:
    if not semantic:
        return None
    return {
        "kind": getattr(semantic, "kind", ""),
        "outcome": getattr(semantic, "outcome", ""),
        "answer": getattr(semantic, "answer", None),
        "text": getattr(semantic, "text", ""),
        "basis": getattr(semantic, "basis", []),
        "candidates": getattr(semantic, "candidates", []),
        "rejected": getattr(semantic, "rejected", []),
    }


def _logic_payload(logic: Any) -> dict[str, Any] | None:
    if logic is None:
        return None
    answer = getattr(logic, "answer", None)
    if not isinstance(answer, (bool, int, float, str, type(None))):
        answer = str(answer)
    return {
        "kind": getattr(logic, "kind", ""),
        "outcome": getattr(logic, "outcome", ""),
        "answer": answer,
        "text": getattr(logic, "text", ""),
        "basis": list(getattr(logic, "basis", []) or []),
        "store_size": getattr(logic, "store_size", 0),
    }


def _payload(result: Any) -> dict[str, Any]:
    """把 PipelineResult 变成与 `core/run.py --json` 同形的 dict。"""
    report = result.context.meta.get("safety")
    safety = None
    if report is not None:
        safety = {
            "level": getattr(report, "level", "ok"),
            "labels": getattr(report, "labels", "无"),
            "rule_ids": list(getattr(report, "rule_ids", ()) or ()),
        }
    return {
        "pack": {"id": result.pack_id, "name": result.pack_name},
        "text": result.text,
        "ok": result.ok,
        "intent": result.intent.value,
        "confidence": result.context.route.confidence if result.context.route else None,
        "source": result.source,
        "loops": result.loops,
        "elapsed_ms": round(result.elapsed_ms, 2),
        "domain": result.context.meta.get("domain"),
        "safety": safety,
        "logic": _logic_payload(result.context.logic),
        "semantic": _semantic_payload(result.context.meta.get("semantic")),
    }


def ask(card: Any, question: str) -> dict[str, Any]:
    """在单进程里问一个数据包,返回与 `--json` 同形的 dict。"""
    with _LOCK:
        _ensure()
        _activate(card)
        pipeline = _pipeline_for(card)
        result = pipeline.ask(question)
        _STATS["asks"] += 1
        return _payload(result)


def guard(card: Any, text: str) -> dict[str, Any]:
    """在单进程里只跑这一包的安全闸(与 `--guard --json` 同形)。"""
    with _LOCK:
        _ensure()
        _activate(card)
        report = _core_safety.assess(text)
        _STATS["guards"] += 1
        return {
            "level": report.level,
            "labels": report.labels,
            "rule_ids": list(report.rule_ids),
            "advice": list(report.advice()),
            "text": report.render(),
        }


def invalidate(pack_dir: str | Path | None = None) -> None:
    """丢掉缓存的流水线(教完/删完知识后调用;None = 全丢)。

    核心的 `PACK` 代理还按文件指纹自己重读,这里先把流水线丢掉,
    下一问就会用新的 pack.json 重新装配(关键词、条目都换新)。
    """
    with _LOCK:
        if not _READY:
            return
        if pack_dir is None:
            _PIPELINES.clear()
            _SEMANTICS.clear()
            _LOGIC.clear()
        else:
            key = str((Path(pack_dir) / "pack.json").resolve())
            _PIPELINES.pop(key, None)
        try:
            _core_domain.PACK.invalidate()
        except Exception:                              # noqa: BLE001
            pass


def stats() -> dict[str, Any]:
    """给 `:stat` 看:装了几条流水线、命中缓存几次、内核能不能用。"""
    return {
        "engine": "inprocess",
        "ready": _READY,
        "error": _ERROR,
        "loaded": len(_PIPELINES),
        "semantics": len(_SEMANTICS),
        "logic": len(_LOGIC),
        "builds": _STATS["builds"],
        "hits": _STATS["hits"],
        "asks": _STATS["asks"],
        "guards": _STATS["guards"],
    }


__all__ = ["CORE_DIR", "available", "ask", "guard", "invalidate", "stats"]

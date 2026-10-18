"""核心契约层:层间数据结构。

这一层不 import 任何其它层 —— 依赖方向永远是 各层 -> core。
模型端口(core/ports.py)在这一版里已删除:这一版不接任何模型。
"""

from .context import (
    Context,
    Intent,
    Reply,
    RouteDecision,
    SemanticFrame,
    Stage,
    TraceStep,
    Verdict,
    has_cjk,
    normalize,
    tokenize,
    truncate,
)

__all__ = [
    "Context",
    "Intent",
    "Reply",
    "RouteDecision",
    "SemanticFrame",
    "Stage",
    "TraceStep",
    "Verdict",
    "normalize",
    "has_cjk",
    "tokenize",
    "truncate",
]

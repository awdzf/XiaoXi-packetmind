"""逻辑层:事实库 + 前向推理 + 布尔求值 + 比较。

设计上它和别的层一样,只认 core 的 Context:
    输入  ctx.frame(理解层抽好的槽位/事实/表达式)
    输出  ctx.logic(LogicResult)
"""

from .compare import CompareParse, CompareResult, Quantity, compare_quantities, parse_comparison
from .engine import LogicEngine, LogicResult
from .expr import And, Expr, Fact, Not, Or, collect_facts, pprint_tree, to_text
from .nlp import (
    ask_across_predicates,
    parse_conditional,
    parse_fact,
    parse_logic,
    parse_property,
    split_clauses,
)
from .reasoner import (
    Derivation,
    FactStore,
    QueryResult,
    Rule,
    decide,
    default_store,
    evaluate,
    forward_chain,
    query,
    unknown_facts,
)

__all__ = [
    "LogicEngine",
    "LogicResult",
    "FactStore",
    "Rule",
    "Derivation",
    "QueryResult",
    "Fact",
    "Not",
    "And",
    "Or",
    "Expr",
    "to_text",
    "pprint_tree",
    "collect_facts",
    "parse_logic",
    "parse_fact",
    "parse_property",
    "parse_conditional",
    "split_clauses",
    "ask_across_predicates",
    "evaluate",
    "decide",
    "unknown_facts",
    "forward_chain",
    "default_store",
    "query",
    "Quantity",
    "CompareResult",
    "CompareParse",
    "compare_quantities",
    "parse_comparison",
]

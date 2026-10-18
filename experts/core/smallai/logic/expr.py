"""逻辑层的表达式树。

一句话里的逻辑结构就用这四种节点表示,不多不少:

    Fact   原子:谓词 + 参数,如 ("属于", ("北京", "华北"))
    Not    取反
    And    合取(且 / 并且 / 和 / && )
    Or     析取(或 / 或者 / 要么 / || )

为什么用显式树而不是直接求值:树能被打印、能被校验、能被生成层渲染成
"先看 A,再看 B,两个都真所以整句为真" 这种可核对的说法。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Fact:
    """原子命题:谓词 + 参数元组。参数为空表示零元谓词(如 ('下雨', ()))。"""

    predicate: str
    args: tuple[str, ...] = ()

    def __str__(self) -> str:
        return f"{self.predicate}({', '.join(self.args)})" if self.args else self.predicate

    def key(self) -> tuple[str, tuple[str, ...]]:
        """给事实库用的哈希键(与 Fact 等价,便于存 set)。"""
        return (self.predicate, self.args)

    @staticmethod
    def from_key(key: tuple[str, tuple[str, ...]]) -> "Fact":
        return Fact(key[0], tuple(key[1]))


@dataclass(frozen=True)
class Not:
    operand: "Expr"


@dataclass(frozen=True)
class And:
    left: "Expr"
    right: "Expr"


@dataclass(frozen=True)
class Or:
    left: "Expr"
    right: "Expr"


Expr = Fact | Not | And | Or


def to_text(expr: Expr) -> str:
    """把树还原成人能读的中文串(用于追踪与渲染)。"""
    if isinstance(expr, Fact):
        if not expr.args:
            return expr.predicate
        if len(expr.args) == 1:
            return f"{expr.args[0]} {expr.predicate}"
        return f"{expr.args[0]} {expr.predicate} {expr.args[1]}"
    if isinstance(expr, Not):
        return f"并非({to_text(expr.operand)})"
    if isinstance(expr, And):
        return f"({to_text(expr.left)} 且 {to_text(expr.right)})"
    if isinstance(expr, Or):
        return f"({to_text(expr.left)} 或 {to_text(expr.right)})"
    return str(expr)


def collect_facts(expr: Expr) -> list[Fact]:
    """展开树里所有原子,交给事实库判真假。"""
    if isinstance(expr, Fact):
        return [expr]
    if isinstance(expr, Not):
        return collect_facts(expr.operand)
    if isinstance(expr, (And, Or)):
        return collect_facts(expr.left) + collect_facts(expr.right)
    return []


def pprint_tree(expr: Expr, indent: int = 0) -> str:
    """多行打印,便于 --trace 时看结构。"""
    pad = "  " * indent
    if isinstance(expr, Fact):
        return f"{pad}Fact({to_text(expr)})"
    if isinstance(expr, Not):
        return f"{pad}Not\n{pprint_tree(expr.operand, indent + 1)}"
    if isinstance(expr, And):
        return f"{pad}And\n{pprint_tree(expr.left, indent + 1)}\n{pprint_tree(expr.right, indent + 1)}"
    if isinstance(expr, Or):
        return f"{pad}Or\n{pprint_tree(expr.left, indent + 1)}\n{pprint_tree(expr.right, indent + 1)}"
    return f"{pad}{expr!r}"

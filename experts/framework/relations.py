"""数据包之间的关系:**包含 / 被包含**(子集结构)。

`pack.json` 的 `expert` 段可以声明两种关系(写一个方向就够,框架合并成同一张图):

    "contains": ["apple"]        // 本包包含 apple —— apple ⊆ 本包
    "contained_by": ["fruit"]    // 本包被 fruit 包含 —— 本包 ⊆ fruit

例:苹果包 `contained_by: ["fruit"]`,或者水果包 `contains: ["apple"]`,
两种写法等价。**记忆数据包也可以当父包/子包**(比如把用户教的小包挂到 memory 下)。

这一层只影响**检索的走向**(反向关键词能不能把"包含它的包"一起带进来),
不改变任何条目内容 —— 它描述的是知识的组织结构,不是知识本身。

坏关系不静默:指向不存在的包、自包含、环,都由 `problems()` 如实报出来,
遍历时用 visited 集合挡住,不会死循环。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:                                     # 只为类型标注,避免循环 import
    from .catalog import ExpertCard


@dataclass(frozen=True)
class PackGraph:
    """包之间的包含关系(子 -> 父)。"""

    #: 子包 -> 直接包含它的父包
    parents: dict[str, frozenset[str]]
    #: 引用了不存在的包("包:a->b")
    unknown: tuple[str, ...] = ()
    #: 检测到的环(每个环是一串包名)
    cycles: tuple[tuple[str, ...], ...] = ()

    # ---- 构建 ------------------------------------------------------------
    @classmethod
    def from_cards(cls, cards: Iterable["ExpertCard"]) -> "PackGraph":
        pool = tuple(cards)
        known = {card.folder for card in pool}
        edges: dict[str, set[str]] = {folder: set() for folder in known}
        unknown: list[str] = []

        def link(child: str, parent: str) -> None:
            if child == parent:
                unknown.append(f"{child} 包含自己(自包含,已忽略)")
                return
            if parent not in known:
                unknown.append(f"{child} 声明包含它的包 {parent} 不存在(已忽略)")
                return
            if child not in known:
                unknown.append(f"有包声明包含 {child},但没有这个包(已忽略)")
                return
            edges[child].add(parent)

        for card in pool:
            for parent in card.contained_by:
                link(card.folder, str(parent))
            for child in card.contains:
                link(str(child), card.folder)

        cycles = _find_cycles(edges)
        return cls(
            parents={folder: frozenset(targets) for folder, targets in edges.items()},
            unknown=tuple(dict.fromkeys(unknown)),
            cycles=cycles,
        )

    # ---- 查询 ------------------------------------------------------------
    def parents_of(self, folder: str) -> tuple[str, ...]:
        return tuple(sorted(self.parents.get(folder, ())))

    def children_of(self, folder: str) -> tuple[str, ...]:
        kids = [child for child, parents in self.parents.items() if folder in parents]
        return tuple(sorted(kids))

    def ancestors(self, folder: str) -> tuple[str, ...]:
        """所有(间接)包含它的包,近的在前。"""
        seen: list[str] = []
        queue = [folder]
        while queue:
            current = queue.pop(0)
            for parent in sorted(self.parents.get(current, ())):
                if parent in seen or parent == folder:
                    continue
                seen.append(parent)
                queue.append(parent)
        return tuple(seen)

    def descendants(self, folder: str) -> tuple[str, ...]:
        """它(间接)包含的所有包,近的在前。"""
        seen: list[str] = []
        queue = [folder]
        while queue:
            current = queue.pop(0)
            for child in sorted(self.children_of(current)):
                if child in seen or child == folder:
                    continue
                seen.append(child)
                queue.append(child)
        return tuple(seen)

    def contains(self, parent: str, child: str) -> bool:
        """parent 是否(间接)包含 child。等价于 child ⊆ parent。"""
        if parent == child:
            return False
        return parent in self.ancestors(child)

    def describe(self, folder: str) -> str:
        parents = self.parents_of(folder)
        children = self.children_of(folder)
        parts: list[str] = []
        if parents:
            parts.append("属于 " + "、".join(parents))
        if children:
            parts.append("包含 " + "、".join(children))
        return ";".join(parts)

    def problems(self) -> list[str]:
        """给体检用:未知引用、自包含、环。"""
        out = list(self.unknown)
        for cycle in self.cycles:
            out.append("包关系成环:" + " → ".join(cycle))
        return out


def _find_cycles(edges: dict[str, set[str]]) -> tuple[tuple[str, ...], ...]:
    """找环(子 -> 父 方向上的有向环)。用三色标记,找到就记一串。"""
    WHITE, GREY, BLACK = 0, 1, 2
    color = {node: WHITE for node in edges}
    stack: list[str] = []
    found: list[tuple[str, ...]] = []

    def visit(node: str) -> None:
        color[node] = GREY
        stack.append(node)
        for parent in sorted(edges.get(node, ())):
            if color.get(parent, WHITE) == GREY:
                if parent in stack:
                    loop = stack[stack.index(parent):] + [parent]
                    found.append(tuple(loop))
            elif color.get(parent, WHITE) == WHITE:
                visit(parent)
        stack.pop()
        color[node] = BLACK

    for node in sorted(edges):
        if color[node] == WHITE:
            visit(node)
    return tuple(found)


__all__ = ["PackGraph"]

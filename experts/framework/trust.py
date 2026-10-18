"""条目信任与知识冲突优先级。

两件事分开：

    · **信任级别**（high / normal / low）决定“这条证据有多硬”：
      作者数据包里的条目默认 high；记忆包默认 normal；用户可以显式把某条
      标成 high 或 low（`config.json` 的 `trust.high_confidence` /
      `low_confidence`，或终端里的 `:trust`）。
    · **冲突优先级**决定“两边都答同一件事时先听谁”：
      默认 `author > memory`；也可以写包名，例如 `["math", "physics", "memory"]`。

它是**设定**，不是审读戳：改的是检索/排序口径，不是“这条内容对不对”。
"""

from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_PRIORITY = ("author", "memory")
DEFAULT_POLICY = "prefer"
AUTHOR = "author"
MEMORY = "memory"


def entry_ref(folder: str, entry_id: str) -> str:
    return f"{folder}/{entry_id}"


def split_ref(ref: str) -> tuple[str, str]:
    text = (ref or "").strip()
    if "/" not in text:
        return "", ""
    folder, entry_id = text.split("/", 1)
    return folder.strip(), entry_id.strip()


def is_author(card) -> bool:
    """作者包 = 不是记忆包。general 也算作者包，但冲突分析会跳过它。"""
    return getattr(card, "role", "domain") != MEMORY


@dataclass(frozen=True)
class TrustMap:
    high: frozenset[str] = field(default_factory=frozenset)
    low: frozenset[str] = field(default_factory=frozenset)
    author_entries_high: bool = True
    conflict_priority: tuple[str, ...] = DEFAULT_PRIORITY
    conflict_policy: str = DEFAULT_POLICY

    @classmethod
    def from_settings(cls, settings) -> "TrustMap":
        return cls(
            high=frozenset(getattr(settings, "high_confidence", ()) or ()),
            low=frozenset(getattr(settings, "low_confidence", ()) or ()),
            author_entries_high=bool(getattr(settings, "author_entries_high", True)),
            conflict_priority=tuple(
                getattr(settings, "conflict_priority", DEFAULT_PRIORITY)
                or DEFAULT_PRIORITY
            ),
            conflict_policy=str(getattr(settings, "conflict_policy", DEFAULT_POLICY)),
        )

    def key(self, folder: str, entry_id: str) -> str:
        return entry_ref(folder, entry_id)

    def level(self, card, entry_id: str) -> str:
        key = self.key(getattr(card, "folder", ""), entry_id)
        if key in self.high:
            return "high"
        if key in self.low:
            return "low"
        if self.author_entries_high and is_author(card):
            return "high"
        return "normal"

    def is_high(self, card, entry_id: str) -> bool:
        return self.level(card, entry_id) == "high"

    def rank(self, folder: str, role: str) -> int:
        """冲突顺序：数字越小越优先；没写到的排在最后。"""
        for index, token in enumerate(self.conflict_priority):
            if token == folder:
                return index
            if token == AUTHOR and role != MEMORY:
                return index
            if token == role:
                return index
        return len(self.conflict_priority)

    def priority_text(self) -> str:
        return " > ".join(self.conflict_priority) or "(未设置)"

    def describe_lines(self, cards=()) -> list[str]:
        lines = [
            f"作者包条目默认高置信：{'是' if self.author_entries_high else '否'}",
            f"冲突优先级：{self.priority_text()}（策略 {self.conflict_policy}）",
        ]
        if self.high:
            lines.append("显式高置信：" + "、".join(sorted(self.high)))
        if self.low:
            lines.append("显式低置信：" + "、".join(sorted(self.low)))
        if not self.high and not self.low:
            lines.append("显式条目：无（用 `:trust <包>/<条目>` 设置）")
        return lines


__all__ = [
    "DEFAULT_PRIORITY",
    "DEFAULT_POLICY",
    "AUTHOR",
    "MEMORY",
    "TrustMap",
    "entry_ref",
    "split_ref",
    "is_author",
]

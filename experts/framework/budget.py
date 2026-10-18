"""World Info 式预算：条目优先级 / 包含组 / 条数 / 正文字符预算。

借 SillyTavern 世界书的三个机制，但只做小析需要的部分：

    · `priority`（条目字段，缺省用包级 `expert.priority`）：数字大的先选；
    · `group`（条目字段）：同组只取一条，避免“同一件事的几种写法”重复占预算；
    · `max_entries` / `text_budget`（config.json 的 retrieval 段）：
      一次最多并入几条、答案正文最多多少字符。

它只做**选择**，不改条目文字；被预算挡掉的条目会在回答末尾如实说明，
不静默消失。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

DEFAULT_PRIORITY = 100


def entry_priority(card, entry_id: str, fallback: int = DEFAULT_PRIORITY) -> int:
    entry = card.entry(entry_id) if card is not None else None
    raw = entry.get("priority") if entry else None
    try:
        return int(raw) if raw is not None else int(getattr(card, "priority", fallback))
    except (TypeError, ValueError):
        return fallback


def entry_group(card, entry_id: str) -> str:
    entry = card.entry(entry_id) if card is not None else None
    return str(entry.get("group") or "").strip() if entry else ""


def select_entries(card, entry_ids: Iterable[str], max_entries: int) -> tuple[list[str], int]:
    """按优先级/包含组挑条目；返回 (保留的 id, 被省略的条数)。"""
    ids = [str(item) for item in entry_ids if str(item)]
    if not ids:
        return [], 0
    ordered = sorted(
        enumerate(ids),
        key=lambda item: (-entry_priority(card, item[1]), item[0]),
    )
    kept: list[str] = []
    groups: set[str] = set()
    omitted = 0
    limit = max(1, int(max_entries))
    for _index, entry_id in ordered:
        group = entry_group(card, entry_id)
        if group and group in groups:
            omitted += 1
            continue
        if len(kept) >= limit:
            omitted += 1
            continue
        kept.append(entry_id)
        if group:
            groups.add(group)
    return kept, omitted


@dataclass(frozen=True)
class BudgetResult:
    answers: list[Any]
    text_chars: int
    omitted: int
    note: str = ""


def trim_answers(
    answers: list[Any],
    *,
    max_entries: int,
    text_budget: int,
) -> BudgetResult:
    """按条数与字符预算裁剪答案；第一段总是保留（它是主答案）。

    `answers` 是同形的 `ExpertAnswer`（有 `.text`）；只裁剪，不重写。
    """
    if not answers:
        return BudgetResult(answers=[], text_chars=0, omitted=0)
    kept: list[Any] = []
    used = 0
    omitted = 0
    limit = max(1, int(max_entries))
    budget = max(0, int(text_budget))
    for answer in answers:
        text = str(getattr(answer, "text", "") or "")
        cost = len(text)
        if len(kept) >= limit:
            omitted += 1
            continue
        if budget and kept and used + cost > budget:
            omitted += 1
            continue
        kept.append(answer)
        used += cost
    note = ""
    if omitted:
        note = (
            f"按 World Info 预算省略了 {omitted} 条（最多 {limit} 条 / "
            f"{budget or '不限'} 字符）"
        )
    return BudgetResult(answers=kept, text_chars=used, omitted=omitted, note=note)


__all__ = [
    "DEFAULT_PRIORITY",
    "entry_priority",
    "entry_group",
    "select_entries",
    "BudgetResult",
    "trim_answers",
]

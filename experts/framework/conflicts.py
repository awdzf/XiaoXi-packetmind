"""知识冲突：两边对同一件事都有说法时，按优先级排，而不是随机并列。

判据和旧版 `_ambiguity_folders` 一样：两个条目的**题目/别名/关键词有交集**，
才叫“对同一件事都有说法”；只是都答了但讲的是两件事，不算冲突。

默认策略是 `prefer`：高优先级（默认作者包 > 用户记忆）排在前面，低优先级
仍然列出来，并标注“按优先级未先采用”——不静默丢掉用户教的知识，也不假装
两边一样可信。`strict` 则只保留高优先级那一套。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .trust import TrustMap

_PUNCT_RE = re.compile(r"[，。！？、；:：,.!?;~～\"'“”‘’()（）\[\]【】《》<>]+")


def _key(text: str) -> str:
    cleaned = _PUNCT_RE.sub(" ", str(text or "").lower())
    return " ".join(cleaned.split())


def entry_keys(card, entry_id: str) -> set[str]:
    """一个条目“声称自己答什么”：题目 / 别名 / 关键词。"""
    entry = card.entry(entry_id) if card is not None else None
    if entry is None:
        return set()
    keys: set[str] = set()
    for item in [entry.get("question")] + list(entry.get("aliases") or []) + list(
        entry.get("keywords") or []
    ):
        key = _key(str(item or ""))
        if len(key) >= 2:
            keys.add(key)
    return keys


@dataclass
class ConflictReport:
    answers: list[Any]
    ambiguity: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    groups: tuple[tuple[str, ...], ...] = field(default_factory=tuple)


def _card_map(cards) -> dict[str, Any]:
    return {getattr(card, "folder", ""): card for card in cards}


def resolve(
    answers: list[Any],
    cards,
    trust: TrustMap,
) -> ConflictReport:
    """按信任/优先级重排 answers，并给出冲突说明。

    answers 是 `ExpertAnswer`（或同形对象）；只处理 ok 且带 entry 的答案。
    """
    card_by_folder = _card_map(cards)
    order = {folder: index for index, folder in enumerate(card_by_folder)}
    infos: list[dict[str, Any]] = []
    for index, answer in enumerate(answers):
        folder = str(getattr(answer, "folder", "") or "")
        entry_id = str(getattr(answer, "entry", "") or "")
        card = card_by_folder.get(folder)
        if not getattr(answer, "ok", False) or not entry_id or card is None:
            continue
        keys = entry_keys(card, entry_id)
        if not keys:
            continue
        infos.append({
            "index": index,
            "answer": answer,
            "folder": folder,
            "keys": keys,
            "rank": trust.rank(folder, getattr(card, "role", "domain")),
        })

    if len(infos) < 2:
        return ConflictReport(answers=list(answers))

    # 按“共享检索键”把答案并成冲突组（并查集；条目量很小，够用）
    parent = list(range(len(infos)))

    def find(node: int) -> int:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(left: int, right: int) -> None:
        root_l, root_r = find(left), find(right)
        if root_l != root_r:
            parent[root_r] = root_l

    for left in range(len(infos)):
        for right in range(left + 1, len(infos)):
            if infos[left]["folder"] == infos[right]["folder"]:
                continue
            if infos[left]["keys"] & infos[right]["keys"]:
                union(left, right)

    grouped: dict[int, list[int]] = {}
    for index in range(len(infos)):
        grouped.setdefault(find(index), []).append(index)
    conflict_groups = [members for members in grouped.values() if len(members) > 1]
    if not conflict_groups:
        return ConflictReport(answers=list(answers))

    policy = (
        trust.conflict_policy
        if trust.conflict_policy in ("prefer", "strict")
        else "prefer"
    )
    ambiguity: list[str] = []
    notes: list[str] = []
    replacement: dict[int, list[Any]] = {}
    skip: set[int] = set()

    for members in conflict_groups:
        members.sort(
            key=lambda item: (
                infos[item]["rank"],
                order.get(infos[item]["folder"], len(order)),
                infos[item]["index"],
            )
        )
        folders = tuple(dict.fromkeys(infos[item]["folder"] for item in members))
        # 旧渲染约定：记忆包由 persona 自动提，这里只放“别的包”，
        # 否则会出现“记忆和 physics、memory 各有一套说法”的重复。
        for folder in folders:
            card = card_by_folder.get(folder)
            if getattr(card, "role", "domain") != "memory":
                ambiguity.append(folder)
        top_rank = infos[members[0]]["rank"]
        top = [infos[item] for item in members if infos[item]["rank"] == top_rank]
        lower = [infos[item] for item in members if infos[item]["rank"] > top_rank]
        if lower:
            note = (
                f"{'、'.join(item['folder'] for item in lower)} 与 "
                f"{'、'.join(item['folder'] for item in top)} 对同一问题都有说法；"
                f"按优先级 {trust.priority_text()}，先列 "
                f"{'、'.join(item['folder'] for item in top)}"
            )
            if note not in notes:
                notes.append(note)
        if policy == "strict" and lower:
            for item in lower:
                skip.add(item["index"])

        chosen = [infos[item] for item in members if item not in skip]
        for item in chosen:
            if item["rank"] > top_rank:
                setattr(
                    item["answer"],
                    "conflict_note",
                    f"低优先级（{trust.priority_text()}），仍列出供你判断",
                )
        anchor = min(members)
        replacement[anchor] = [item["answer"] for item in chosen]
        for item in members:
            if item != anchor:
                skip.add(item)

    result: list[Any] = []
    for index, answer in enumerate(answers):
        if index in replacement:
            result.extend(replacement[index])
        elif index in skip:
            continue
        else:
            result.append(answer)

    return ConflictReport(
        answers=result,
        ambiguity=tuple(dict.fromkeys(ambiguity)),
        notes=tuple(notes),
        groups=tuple(
            tuple(dict.fromkeys(infos[item]["folder"] for item in members))
            for members in conflict_groups
        ),
    )


__all__ = ["ConflictReport", "entry_keys", "resolve"]

"""小析的检索评测集：无模型、可重复、看的是“路由到哪一条”。

评测不调用核心生成答案，只跑 `GoChat.route()`：

    · `folder hit@1`：第一名是否落在期望数据包；
    · `entry hit@1`：第一名是否命中期望条目（更严格）；
    · `MRR`：期望条目第一次出现在第几名；
    · `abstain`：负例是否真的没有被领域包接住。

用例是 `experts/evaluation/cases.jsonl`，一行一条；`--eval` 或 `:eval` 跑。
这不是“模型分数”，是检索层的回归护栏：改 BM25/权重/预算后先跑它。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = ROOT / "evaluation" / "cases.jsonl"


@dataclass(frozen=True)
class EvalCase:
    id: str
    question: str
    folder: str = ""
    entry: str = ""
    category: str = "direct"
    must_abstain: bool = False


@dataclass(frozen=True)
class CaseResult:
    case: EvalCase
    folders: tuple[str, ...]
    entries: tuple[str, ...]
    folder_rank: int
    entry_rank: int
    abstained: bool
    passed: bool
    detail: str = ""


@dataclass(frozen=True)
class EvalReport:
    results: tuple[CaseResult, ...]
    total: int
    folder_hit: int
    entry_hit: int
    mrr: float
    abstain_total: int
    abstain_ok: int

    @property
    def positive_total(self) -> int:
        return self.total - self.abstain_total

    @property
    def folder_accuracy(self) -> float:
        return (self.folder_hit / self.positive_total) if self.positive_total else 0.0

    @property
    def entry_accuracy(self) -> float:
        return (self.entry_hit / self.positive_total) if self.positive_total else 0.0

    @property
    def abstain_accuracy(self) -> float:
        return (self.abstain_ok / self.abstain_total) if self.abstain_total else 0.0

    @property
    def failures(self) -> list[CaseResult]:
        return [item for item in self.results if not item.passed]

    def render(self, limit: int = 20) -> str:
        lines = [
            f"评测 {self.total} 条（正例 {self.positive_total} / 负例 {self.abstain_total}）"
            f" · 文件夹 hit@1 {self.folder_hit}/{self.positive_total}"
            f" ({self.folder_accuracy:.1%}) · 条目 hit@1 {self.entry_hit}/{self.positive_total}"
            f" ({self.entry_accuracy:.1%}) · MRR {self.mrr:.3f}",
            f"负例弃权 {self.abstain_ok}/{self.abstain_total}"
            f" ({self.abstain_accuracy:.1%})",
        ]
        fails = self.failures
        if fails:
            lines.append(f"未通过 {len(fails)} 条:")
            for item in fails[:limit]:
                lines.append(
                    f"  · {item.case.id} [{item.case.category}] {item.case.question}"
                    f" -> {item.detail or '未命中'}"
                )
            if len(fails) > limit:
                lines.append(f"  ……还有 {len(fails) - limit} 条")
        return "\n".join(lines)


def load_cases(path: str | Path | None = None) -> list[EvalCase]:
    target = Path(path) if path is not None else CASES_PATH
    if not target.is_file():
        raise FileNotFoundError(f"评测集不存在:{target}")
    cases: list[EvalCase] = []
    for line_no, line in enumerate(
        target.read_text(encoding="utf-8").splitlines(), 1
    ):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{target.name}:{line_no} 不是合法 JSON({exc})") from exc
        if not isinstance(raw, dict):
            raise ValueError(f"{target.name}:{line_no} 不是 JSON 对象")
        question = str(raw.get("question") or "").strip()
        if not question:
            raise ValueError(f"{target.name}:{line_no} 缺 question")
        cases.append(EvalCase(
            id=str(raw.get("id") or f"case-{line_no}"),
            question=question,
            folder=str(raw.get("folder") or "").strip(),
            entry=str(raw.get("entry") or "").strip(),
            category=str(raw.get("category") or "direct").strip(),
            must_abstain=bool(raw.get("must_abstain")),
        ))
    return cases


def evaluate(chat: Any, cases: Iterable[EvalCase]) -> EvalReport:
    """跑路由评测；`chat` 只需要有 `route(question)`。"""
    results: list[CaseResult] = []
    for case in cases:
        _decomposition, routing = chat.route(case.question)
        folders = tuple(
            target.card.folder
            for target in getattr(routing, "targets", ())
            if not target.card.is_general
        )
        entries: list[str] = []
        for target in getattr(routing, "targets", ()):
            entries.extend(getattr(target, "entry_ids", ()) or ())
        folder_rank = 0
        entry_rank = 0
        if case.folder:
            try:
                folder_rank = folders.index(case.folder) + 1
            except ValueError:
                folder_rank = 0
        if case.entry:
            for index, target in enumerate(getattr(routing, "targets", ()), 1):
                if target.card.folder != case.folder:
                    continue
                if case.entry in (getattr(target, "entry_ids", ()) or ()):
                    entry_rank = index
                    break
        abstained = not folders
        if case.must_abstain:
            passed = abstained
            detail = "" if passed else "本应弃权，却路由到 " + "、".join(folders)
        else:
            passed = (
                folder_rank == 1
                and (not case.entry or entry_rank == 1)
            )
            detail = (
                f"文件夹第 {folder_rank or '-'} 名；条目第 {entry_rank or '-'} 名；"
                f"候选 {','.join(entries[:8]) or '无'}"
            )
        results.append(CaseResult(
            case=case,
            folders=folders,
            entries=tuple(entries),
            folder_rank=folder_rank,
            entry_rank=entry_rank,
            abstained=abstained,
            passed=passed,
            detail=detail,
        ))

    total = len(results)
    folder_hit = sum(1 for item in results if item.folder_rank == 1)
    entry_hit = sum(
        1 for item in results
        if item.entry_rank == 1 and not item.case.must_abstain
    )
    reciprocal = sum(
        (1.0 / item.entry_rank) for item in results
        if item.entry_rank > 0 and not item.case.must_abstain
    )
    pos = sum(1 for item in results if not item.case.must_abstain)
    negatives = [item for item in results if item.case.must_abstain]
    return EvalReport(
        results=tuple(results),
        total=total,
        folder_hit=folder_hit,
        entry_hit=entry_hit,
        mrr=(reciprocal / pos) if pos else 0.0,
        abstain_total=len(negatives),
        abstain_ok=sum(1 for item in negatives if item.passed),
    )


def run(
    chat: Any,
    path: str | Path | None = None,
) -> EvalReport:
    return evaluate(chat, load_cases(path))


__all__ = [
    "ROOT",
    "CASES_PATH",
    "EvalCase",
    "CaseResult",
    "EvalReport",
    "load_cases",
    "evaluate",
    "run",
]

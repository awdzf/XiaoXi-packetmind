"""小析的评测集：无模型、可重复、分两层看。

**为什么分两层**（这是 1.0.2 之前最大的度量缺口）：

原来只跑 `GoChat.route()` —— 它量的是"路由器把哪个包排第一"，**不是"最后答没答对"**。
于是"什么是德摩根定律"在路由层是 `physics` 排第一（算命中），在答案层却是
拿热力学定律冒充德摩根定律（严重错）。评测两行都写着 100%，事故照样上线。

现在两层都量：

    · 路由层：`folder hit@1` / `entry hit@1` / `MRR` —— 改 BM25、权重、预算后先看它；
    · 答案层：`answer hit@1`（真正给出的答案对不对）/ `答案层弃权` / **`错误引用`**
      —— 负例如果还给出了带出处的答案，记一条 `错误引用`，这是最严重的一类。

`chat` 只要有 `ask()` 就顺便量答案层；只有 `route()` 的测试替身仍能跑（答案层留空）。
用例是 `experts/evaluation/cases.jsonl`，一行一条；`--eval` 或 `:eval` 跑。
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
    #: 答案层（`chat.ask()`）的结果；None = 这一轮的 chat 没有 ask()，答案层没量
    answer_folder: str | None = None
    answer_entry: str | None = None
    answer_abstained: bool = True
    answer_passed: bool = True
    answer_detail: str = ""


@dataclass(frozen=True)
class EvalReport:
    results: tuple[CaseResult, ...]
    total: int
    folder_hit: int
    entry_hit: int
    mrr: float
    abstain_total: int
    abstain_ok: int
    #: 答案层：正例真正答对了几条
    answer_hit: int = 0
    #: 答案层：负例真正弃权了几条
    answer_abstain_ok: int = 0
    #: **错误引用**：负例还给出了带出处的答案（最严重的一类）
    answer_false: int = 0
    #: 答案层是否真的跑过（chat 有 ask() 才跑）
    answer_measured: bool = False

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
    def answer_accuracy(self) -> float:
        return (self.answer_hit / self.positive_total) if self.positive_total else 0.0

    @property
    def answer_abstain_accuracy(self) -> float:
        return (self.answer_abstain_ok / self.abstain_total) if self.abstain_total else 0.0

    @property
    def failures(self) -> list[CaseResult]:
        return [item for item in self.results if not item.passed]

    @property
    def answer_failures(self) -> list[CaseResult]:
        return [item for item in self.results if not item.answer_passed]

    def render(self, limit: int = 20) -> str:
        lines = [
            f"评测 {self.total} 条（正例 {self.positive_total} / 负例 {self.abstain_total}）"
            f" · 文件夹 hit@1 {self.folder_hit}/{self.positive_total}"
            f" ({self.folder_accuracy:.1%}) · 条目 hit@1 {self.entry_hit}/{self.positive_total}"
            f" ({self.entry_accuracy:.1%}) · MRR {self.mrr:.3f}",
            f"负例弃权 {self.abstain_ok}/{self.abstain_total}"
            f" ({self.abstain_accuracy:.1%})",
        ]
        if self.answer_measured:
            lines.append(
                f"答案层(真正给出的回答)命中 {self.answer_hit}/{self.positive_total}"
                f" ({self.answer_accuracy:.1%}) · 答案层弃权 {self.answer_abstain_ok}/"
                f"{self.abstain_total} ({self.answer_abstain_accuracy:.1%})"
                f" · **错误引用 {self.answer_false} 条**"
            )
        fails = self.failures
        if fails:
            lines.append(f"路由未通过 {len(fails)} 条:")
            for item in fails[:limit]:
                lines.append(
                    f"  · {item.case.id} [{item.case.category}] {item.case.question}"
                    f" -> {item.detail or '未命中'}"
                )
            if len(fails) > limit:
                lines.append(f"  ……还有 {len(fails) - limit} 条")
        answer_fails = [item for item in self.answer_failures if item.answer_passed is False]
        if answer_fails:
            lines.append(f"答案层未通过 {len(answer_fails)} 条:")
            for item in answer_fails[:limit]:
                lines.append(
                    f"  · {item.case.id} [{item.case.category}] {item.case.question}"
                    f" -> {item.answer_detail or '未作答'}"
                )
            if len(answer_fails) > limit:
                lines.append(f"  ……还有 {len(answer_fails) - limit} 条")
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


def _answer_layer(chat: Any, case: EvalCase) -> tuple[str | None, str | None, bool, bool, str]:
    """跑一次真正的 `ask()`，返回 (文件夹, 条目, 是否作答, 是否通过, 说明)。

    这一层是 **1.0.2 之前完全没有的**：之前只跑路由，于是"路由对了、答案错了"
    （德摩根定律 -> 热力学定律）在评测里看不出来。负例给出带出处的答案
    记为`错误引用`，比"没答上"严重得多。
    """
    try:
        result = chat.ask(case.question, detail=False, plain=True)
    except Exception as exc:              # noqa: BLE001 - 评测不该把异常变成"通过"
        return None, None, False, False, f"ask() 抛异常:{type(exc).__name__}: {exc}"
    #: 只有"把某条知识当答案给出"才算作答：`source=template/persona` 的自述与工具类回答
    #: （问候、帮助、天气模板、当前时间）没有条目可引用，不算答了那个问题。
    answered = [
        item for item in getattr(result, "answered", ())
        if getattr(item, "ok", False) and getattr(item, "entry", None)
    ]
    if not answered:
        passed = bool(case.must_abstain)
        return None, None, False, passed, ("" if passed else "没有给出答案")
    first = answered[0]
    folder = str(getattr(first, "folder", "") or "")
    entry = str(getattr(first, "entry", "") or "") or None
    if case.must_abstain:
        return folder, entry, True, False, f"本应弃权,却引用 {folder}/{entry or '?'}"
    ok = (folder == case.folder) and (not case.entry or entry == case.entry)
    detail = "" if ok else f"答成 {folder}/{entry or '?'}（期望 {case.folder}/{case.entry or '?'}）"
    return folder, entry, True, ok, detail


def evaluate(chat: Any, cases: Iterable[EvalCase]) -> EvalReport:
    """跑两层评测：路由层（`route()`）+ 答案层（`ask()`，chat 支持时）。

    只有 `route()` 的替身仍然可用 —— 答案层留空，`answer_measured=False`，
    不会把"没量"说成"全过"。
    """
    can_ask = callable(getattr(chat, "ask", None))
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
        answer_folder = answer_entry = None
        answer_abstained = True
        answer_passed = True
        answer_detail = ""
        if can_ask:
            answer_folder, answer_entry, answered, answer_passed, answer_detail = _answer_layer(chat, case)
            answer_abstained = not answered
        results.append(CaseResult(
            case=case,
            folders=folders,
            entries=tuple(entries),
            folder_rank=folder_rank,
            entry_rank=entry_rank,
            abstained=abstained,
            passed=passed,
            detail=detail,
            answer_folder=answer_folder,
            answer_entry=answer_entry,
            answer_abstained=answer_abstained,
            answer_passed=answer_passed,
            answer_detail=answer_detail,
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
        answer_hit=sum(1 for item in results if not item.case.must_abstain and item.answer_passed) if can_ask else 0,
        answer_abstain_ok=sum(1 for item in negatives if item.answer_passed) if can_ask else 0,
        answer_false=sum(1 for item in negatives if item.answer_abstained is False) if can_ask else 0,
        answer_measured=can_ask,
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

"""可手动调整的开关:数据包上限、上下文、检索层与信任/冲突优先级。

开关放在 `experts/config.json`(纯数据;手改后重启 GoChat 生效,`:trust` /
`:priority` 会原子写回并立即生效),优先级:

    命令行 `--max-packs N`  >  环境变量 SMALLAI_MAX_PACKS  >  config.json  >  默认

**上限不改变精确性**:一个数据包要被查,仍然得先过检索阈值(规则 ≥3 分;
混合兜底再过校准弃权);`max_packs` 只在"达标的包超过上限"时按分数截断。

坏值不静默:数不是数、超范围、JSON 读坏、条目引用写错 —— 一律退回默认值,
并把原因记在 `Settings.notes` 里,由 `GoChat.py` 启动时打印。

`trust` 段是**设定**,不是审读戳:它只改检索排序/冲突口径,不改内容真假。
终端里的 `:trust` / `:priority` 会经 `update_trust()` 原子写回本文件。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # experts/
CONFIG_PATH = ROOT / "config.json"

ENV_MAX_PACKS = "SMALLAI_MAX_PACKS"

DEFAULT_MAX_PACKS = 3
#: 上限的硬边界:比这更大没有意义(没有那么多包),还容易把"广泛撒网"当精确
MAX_PACKS_LIMIT = 8
DEFAULT_MAX_TURNS = 8
DEFAULT_MAX_KEYWORDS = 12
DEFAULT_REHIT_ROUNDS = 1
MAX_REHIT_ROUNDS_LIMIT = 3
DEFAULT_K1 = 1.5
DEFAULT_B = 0.75
DEFAULT_EXPANSION = 4
DEFAULT_ABSTAIN_CONFIDENCE = 0.35
DEFAULT_MAX_ENTRIES = 6
DEFAULT_TEXT_BUDGET = 2400
DEFAULT_CONFLICT_PRIORITY = ("author", "memory")
DEFAULT_CONFLICT_POLICY = "prefer"

_REF_RE = re.compile(r"^[^/\s]+/[^/\s]+$")
_PRIORITY_RE = re.compile(r"^[\w.-]+$", re.UNICODE)


@dataclass(frozen=True)
class RetrievalSettings:
    """检索层开关：BM25 / 混合 / 查询扩展 / 分层 / 校准弃权。"""

    bm25: bool = True
    k1: float = DEFAULT_K1
    b: float = DEFAULT_B
    hybrid: bool = True
    query_expansion: int = DEFAULT_EXPANSION
    hierarchy: bool = True
    abstain_confidence: float = DEFAULT_ABSTAIN_CONFIDENCE
    #: World Info 式预算：一次最多并入几条条目、答案正文最多多少字符
    max_entries: int = DEFAULT_MAX_ENTRIES
    text_budget: int = DEFAULT_TEXT_BUDGET


@dataclass(frozen=True)
class TrustSettings:
    """条目信任与知识冲突优先级。"""

    author_entries_high: bool = True
    high_confidence: tuple[str, ...] = ()
    low_confidence: tuple[str, ...] = ()
    conflict_priority: tuple[str, ...] = DEFAULT_CONFLICT_PRIORITY
    conflict_policy: str = DEFAULT_CONFLICT_POLICY


@dataclass(frozen=True)
class Settings:
    """一次运行的开关(不可变;临时改动走 GoChat 的命令/参数)。"""

    max_packs: int = DEFAULT_MAX_PACKS
    context_enabled: bool = True
    max_turns: int = DEFAULT_MAX_TURNS
    max_keywords: int = DEFAULT_MAX_KEYWORDS
    rehit_rounds: int = DEFAULT_REHIT_ROUNDS
    retrieval: RetrievalSettings = field(default_factory=RetrievalSettings)
    trust: TrustSettings = field(default_factory=TrustSettings)
    notes: tuple[str, ...] = field(default_factory=tuple)

    def describe(self) -> str:
        state = "on" if self.context_enabled else "off"
        parts = ["规则"]
        if self.retrieval.bm25:
            parts.append("BM25")
        if self.retrieval.hybrid:
            parts.append("混合")
        retrieval = "+".join(parts)
        return (f"上限 {self.max_packs} 个数据包 · 上下文 {state} · "
                f"再命中 {self.rehit_rounds} 轮 · 检索 {retrieval}"
                f"(扩展 {self.retrieval.query_expansion}) · "
                f"冲突 {self.trust.conflict_policy}")


def _clamp_int(value: object, default: int, low: int, high: int,
               label: str, notes: list[str]) -> int:
    try:
        number = int(value)                         # type: ignore[arg-type]
    except (TypeError, ValueError):
        notes.append(f"{label} 不是整数({value!r}),按默认 {default} 处理")
        return default
    if number < low or number > high:
        fixed = max(low, min(number, high))
        notes.append(f"{label}={number} 超出范围 {low}–{high},已按 {fixed} 处理")
        return fixed
    return number


def _clamp_float(value: object, default: float, low: float, high: float,
                 label: str, notes: list[str]) -> float:
    try:
        number = float(value)                       # type: ignore[arg-type]
    except (TypeError, ValueError):
        notes.append(f"{label} 不是数字({value!r}),按默认 {default} 处理")
        return default
    if number < low or number > high:
        fixed = max(low, min(number, high))
        notes.append(f"{label}={number} 超出范围 {low}–{high},已按 {fixed} 处理")
        return fixed
    return number


def _bool(value: object, default: bool, label: str, notes: list[str]) -> bool:
    if isinstance(value, bool):
        return value
    notes.append(f"{label} 不是 true/false({value!r}),按默认 {default} 处理")
    return default


def _entry_refs(value: object, label: str, notes: list[str]) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        notes.append(f"{label} 不是数组,按空处理")
        return ()
    out: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if not text:
            continue
        if not _REF_RE.match(text):
            notes.append(f"{label} 里的 {text!r} 不是「包/条目」格式,已忽略")
            continue
        if text not in out:
            out.append(text)
    return tuple(out)


def _priority(value: object, notes: list[str]) -> tuple[str, ...]:
    if value is None:
        return DEFAULT_CONFLICT_PRIORITY
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        notes.append("trust.conflict_priority 不是数组,按默认 author > memory 处理")
        return DEFAULT_CONFLICT_PRIORITY
    out: list[str] = []
    for item in items:
        token = str(item or "").strip()
        if not token:
            continue
        if not _PRIORITY_RE.match(token):
            notes.append(f"trust.conflict_priority 里的 {token!r} 不是合法名字,已忽略")
            continue
        if token not in out:
            out.append(token)
    if not out:
        notes.append("trust.conflict_priority 为空,按默认 author > memory 处理")
        return DEFAULT_CONFLICT_PRIORITY
    return tuple(out)


def _parse_retrieval(raw: object, notes: list[str]) -> RetrievalSettings:
    if raw is None:
        return RetrievalSettings()
    if not isinstance(raw, dict):
        notes.append("retrieval 不是一个 JSON 对象,按默认值处理")
        return RetrievalSettings()
    return RetrievalSettings(
        bm25=_bool(raw.get("bm25", True), True, "retrieval.bm25", notes),
        k1=_clamp_float(raw.get("k1", DEFAULT_K1), DEFAULT_K1, 0.1, 3.0,
                        "retrieval.k1", notes),
        b=_clamp_float(raw.get("b", DEFAULT_B), DEFAULT_B, 0.0, 1.0,
                       "retrieval.b", notes),
        hybrid=_bool(raw.get("hybrid", True), True, "retrieval.hybrid", notes),
        query_expansion=_clamp_int(
            raw.get("query_expansion", DEFAULT_EXPANSION), DEFAULT_EXPANSION,
            0, 10, "retrieval.query_expansion", notes,
        ),
        hierarchy=_bool(raw.get("hierarchy", True), True, "retrieval.hierarchy", notes),
        abstain_confidence=_clamp_float(
            raw.get("abstain_confidence", DEFAULT_ABSTAIN_CONFIDENCE),
            DEFAULT_ABSTAIN_CONFIDENCE, 0.0, 1.0,
            "retrieval.abstain_confidence", notes,
        ),
        max_entries=_clamp_int(
            raw.get("max_entries", DEFAULT_MAX_ENTRIES), DEFAULT_MAX_ENTRIES,
            1, 20, "retrieval.max_entries", notes,
        ),
        text_budget=_clamp_int(
            raw.get("text_budget", DEFAULT_TEXT_BUDGET), DEFAULT_TEXT_BUDGET,
            0, 20000, "retrieval.text_budget", notes,
        ),
    )


def _parse_trust(raw: object, notes: list[str]) -> TrustSettings:
    if raw is None:
        return TrustSettings()
    if not isinstance(raw, dict):
        notes.append("trust 不是一个 JSON 对象,按默认值处理")
        return TrustSettings()
    policy = str(raw.get("conflict_policy", DEFAULT_CONFLICT_POLICY)).strip().lower()
    if policy not in ("prefer", "strict"):
        notes.append(f"trust.conflict_policy={policy!r} 不是 prefer/strict,按 prefer 处理")
        policy = DEFAULT_CONFLICT_POLICY
    return TrustSettings(
        author_entries_high=_bool(
            raw.get("author_entries_high", True), True,
            "trust.author_entries_high", notes,
        ),
        high_confidence=_entry_refs(
            raw.get("high_confidence"), "trust.high_confidence", notes,
        ),
        low_confidence=_entry_refs(
            raw.get("low_confidence"), "trust.low_confidence", notes,
        ),
        conflict_priority=_priority(raw.get("conflict_priority"), notes),
        conflict_policy=policy,
    )


def load(path: str | Path | None = None, environ: dict[str, str] | None = None) -> Settings:
    """读配置:文件缺失/读坏 -> 用默认值,并把原因写进 notes(不抛异常)。"""
    notes: list[str] = []
    config_path = Path(path) if path is not None else CONFIG_PATH
    raw: dict = {}

    if config_path.is_file():
        try:
            parsed = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            notes.append(f"{config_path.name} 读取失败({exc}),全部用默认值")
            parsed = {}
        if isinstance(parsed, dict):
            raw = parsed
        else:
            notes.append(f"{config_path.name} 不是一个 JSON 对象,全部用默认值")

    max_packs = _clamp_int(raw.get("max_packs", DEFAULT_MAX_PACKS), DEFAULT_MAX_PACKS,
                           1, MAX_PACKS_LIMIT, "max_packs", notes)

    env_value = (environ if environ is not None else os.environ).get(ENV_MAX_PACKS)
    if env_value not in (None, ""):
        env_packs = _clamp_int(env_value, max_packs, 1, MAX_PACKS_LIMIT,
                               f"环境变量 {ENV_MAX_PACKS}", notes)
        if env_packs != max_packs:
            notes.append(f"环境变量 {ENV_MAX_PACKS}={env_packs} 覆盖了配置文件")
            max_packs = env_packs

    context_raw = raw.get("context")
    context = context_raw if isinstance(context_raw, dict) else {}
    if context_raw is not None and not isinstance(context_raw, dict):
        notes.append("context 不是一个 JSON 对象,按默认值处理")

    enabled_raw = context.get("enabled", True)
    if not isinstance(enabled_raw, bool):
        notes.append(f"context.enabled 不是 true/false({enabled_raw!r}),按开启处理")
        enabled = True
    else:
        enabled = enabled_raw

    max_turns = _clamp_int(context.get("max_turns", DEFAULT_MAX_TURNS), DEFAULT_MAX_TURNS,
                           1, 50, "context.max_turns", notes)
    max_keywords = _clamp_int(context.get("max_keywords", DEFAULT_MAX_KEYWORDS),
                              DEFAULT_MAX_KEYWORDS, 1, 50, "context.max_keywords", notes)
    rehit_rounds = _clamp_int(raw.get("rehit_rounds", DEFAULT_REHIT_ROUNDS),
                              DEFAULT_REHIT_ROUNDS, 0, MAX_REHIT_ROUNDS_LIMIT,
                              "rehit_rounds", notes)

    return Settings(
        max_packs=max_packs,
        context_enabled=enabled,
        max_turns=max_turns,
        max_keywords=max_keywords,
        rehit_rounds=rehit_rounds,
        retrieval=_parse_retrieval(raw.get("retrieval"), notes),
        trust=_parse_trust(raw.get("trust"), notes),
        notes=tuple(notes),
    )


def _read_raw(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _atomic_write(path: Path, raw: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def update_trust(
    path: str | Path | None = None,
    *,
    high_add: tuple[str, ...] = (),
    high_remove: tuple[str, ...] = (),
    low_add: tuple[str, ...] = (),
    low_remove: tuple[str, ...] = (),
    author_entries_high: bool | None = None,
    conflict_priority: tuple[str, ...] | None = None,
    conflict_policy: str | None = None,
) -> dict:
    """原子地改 config.json 的 trust 段（`:trust` / `:priority` 用）。"""
    config_path = Path(path) if path is not None else CONFIG_PATH
    raw = _read_raw(config_path)
    trust = raw.get("trust")
    if not isinstance(trust, dict):
        trust = {}

    def _current(name: str) -> list[str]:
        value = trust.get(name)
        if isinstance(value, str):
            return [value]
        if isinstance(value, (list, tuple)):
            return [str(item) for item in value]
        return []

    high = _current("high_confidence")
    low = _current("low_confidence")

    def add(target: list[str], items: tuple[str, ...]) -> None:
        for item in items:
            text = str(item).strip()
            if text and text not in target:
                target.append(text)

    def remove(target: list[str], items: tuple[str, ...]) -> None:
        drop = {str(item).strip() for item in items}
        target[:] = [item for item in target if item not in drop]

    remove(high, high_remove)
    remove(low, low_remove)
    remove(high, high_add)
    remove(low, high_add)
    add(high, high_add)
    remove(low, low_add)
    remove(high, low_add)
    add(low, low_add)

    if high:
        trust["high_confidence"] = high
    else:
        trust.pop("high_confidence", None)
    if low:
        trust["low_confidence"] = low
    else:
        trust.pop("low_confidence", None)
    if author_entries_high is not None:
        trust["author_entries_high"] = bool(author_entries_high)
    if conflict_priority is not None:
        trust["conflict_priority"] = list(conflict_priority)
    if conflict_policy is not None:
        trust["conflict_policy"] = conflict_policy
    raw["trust"] = trust
    _atomic_write(config_path, raw)
    return raw


def set_trust_ref(ref: str, level: str, path: str | Path | None = None) -> dict:
    """把一个「包/条目」设为 high / low / normal（normal = 取消显式设置）。"""
    text = str(ref or "").strip()
    if not _REF_RE.match(text):
        raise ValueError("条目写法应是「包/条目」，例如 math/vector-field")
    normalized = level.strip().lower()
    if normalized == "high":
        return update_trust(path, high_add=(text,), low_remove=(text,))
    if normalized == "low":
        return update_trust(path, low_add=(text,), high_remove=(text,))
    if normalized == "normal":
        return update_trust(path, high_remove=(text,), low_remove=(text,))
    raise ValueError("level 只能是 high / low / normal")


def set_priority(tokens: tuple[str, ...], path: str | Path | None = None) -> dict:
    cleaned: list[str] = []
    for token in tokens:
        text = str(token or "").strip()
        if not text or not _PRIORITY_RE.match(text):
            raise ValueError(f"优先级名字不合法:{text!r}")
        if text not in cleaned:
            cleaned.append(text)
    if not cleaned:
        raise ValueError("至少给一个优先级名字，例如 author memory")
    return update_trust(path, conflict_priority=tuple(cleaned))


def clamp_rehit_rounds(value: object, notes: list[str] | None = None) -> int:
    """把命令行/临时设置收敛到 0–MAX_REHIT_ROUNDS_LIMIT;坏值退回默认。"""
    sink = notes if notes is not None else []
    return _clamp_int(value, DEFAULT_REHIT_ROUNDS, 0, MAX_REHIT_ROUNDS_LIMIT,
                      "rehit_rounds", sink)


def clamp_max_packs(value: object, notes: list[str] | None = None) -> int:
    """把命令行/临时设置收敛到 1–MAX_PACKS_LIMIT;坏值退回默认。"""
    sink = notes if notes is not None else []
    return _clamp_int(value, DEFAULT_MAX_PACKS, 1, MAX_PACKS_LIMIT, "max_packs", sink)


__all__ = [
    "CONFIG_PATH",
    "DEFAULT_MAX_PACKS",
    "MAX_PACKS_LIMIT",
    "DEFAULT_REHIT_ROUNDS",
    "MAX_REHIT_ROUNDS_LIMIT",
    "DEFAULT_K1",
    "DEFAULT_B",
    "DEFAULT_EXPANSION",
    "DEFAULT_ABSTAIN_CONFIDENCE",
    "DEFAULT_MAX_ENTRIES",
    "DEFAULT_TEXT_BUDGET",
    "DEFAULT_CONFLICT_PRIORITY",
    "DEFAULT_CONFLICT_POLICY",
    "ENV_MAX_PACKS",
    "RetrievalSettings",
    "TrustSettings",
    "Settings",
    "load",
    "update_trust",
    "set_trust_ref",
    "set_priority",
    "clamp_max_packs",
    "clamp_rehit_rounds",
]

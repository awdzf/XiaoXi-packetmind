"""漏问记录:小析没接住的问题,留在本地,当成"下一步该教它什么"的清单。

借鉴 RAGless 的 **missed query logging**,但目的更偏个人化:

    RAGless 记漏问是为了调阈值 / 分析覆盖率;
    小析记漏问是为了**让你把它教成自己的知识** —— 下一条 `#` 教什么,从这里挑。

纪律:

    · 只写本地文件(`experts/data/missed.jsonl`),不联网、不发送；
    · 不记录整段聊天,只记那一句没接住的话 + 当时拆出的关键词；
    · 同一句不重复堆(最近 20 条里已有就不写)；
    · 文件有上限(约 256KB / 最多 500 条):超过就裁掉最旧的,
      小设备上不会越跑越占空间；
    · 读/清都由你主动触发(`:missed` / `:missed clear`),没有后台任务。

它是"学习队列",不是日志系统:数据量小、可直接看、可直接删。
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]          # experts/
DEFAULT_PATH = ROOT / "data" / "missed.jsonl"
#: 测试/多实例可以用环境变量换一个文件
ENV_PATH = "SMALLAI_MISSED_PATH"
#: 文件大小上限:超过就裁到 MAX_ENTRIES 条(小设备友好)
MAX_FILE_BYTES = 256_000
MAX_ENTRIES = 500
_DEDUPE_WINDOW = 20


def _path(path: str | Path | None = None) -> Path:
    raw = path if path is not None else os.environ.get(ENV_PATH) or DEFAULT_PATH
    return Path(raw)


def _normalize(query: str) -> str:
    return " ".join((query or "").split()).lower()


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_missed(limit: int = 20, *, path: str | Path | None = None) -> list[dict[str, Any]]:
    """读最近的漏问(最新的在前);坏行跳过,不抛异常。"""
    file = _path(path)
    if not file.is_file():
        return []
    records: list[dict[str, Any]] = []
    try:
        with file.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict) and str(item.get("query") or "").strip():
                    records.append(item)
    except OSError:
        return []
    if limit > 0:
        records = records[-limit:]
    return list(reversed(records))


def _trim(file: Path) -> None:
    """文件超过上限时只留最近 MAX_ENTRIES 条;重写走临时文件。"""
    try:
        if file.stat().st_size <= MAX_FILE_BYTES:
            return
    except OSError:
        return
    records: list[dict[str, Any]] = []
    try:
        with file.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict):
                    records.append(item)
    except OSError:
        return
    records = records[-MAX_ENTRIES:]
    tmp = file.with_name(file.name + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as handle:
            for item in records:
                handle.write(json.dumps(item, ensure_ascii=False) + "\n")
        os.replace(tmp, file)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def log_missed(
    query: str,
    keywords: tuple[str, ...] | list[str] = (),
    *,
    path: str | Path | None = None,
) -> bool:
    """记一条漏问。返回 False 表示空句子/刚刚记过(不重复写)。"""
    text = " ".join((query or "").split()).strip()
    if not text:
        return False
    file = _path(path)
    recent = read_missed(_DEDUPE_WINDOW, path=file)
    if any(_normalize(item.get("query") or "") == _normalize(text) for item in recent):
        return False

    record = {
        "ts": _now(),
        "query": text,
        "keywords": [str(word) for word in dict.fromkeys(keywords) if str(word).strip()],
    }
    try:
        file.parent.mkdir(parents=True, exist_ok=True)
        with file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        _trim(file)
    except OSError:
        return False
    return True


def clear_missed(*, path: str | Path | None = None) -> int:
    """清空漏问记录,返回清掉多少条(文件不存在返回 0)。"""
    file = _path(path)
    if not file.is_file():
        return 0
    count = len(read_missed(0, path=file))
    try:
        file.unlink()
    except OSError:
        return 0
    return count


__all__ = [
    "DEFAULT_PATH",
    "ENV_PATH",
    "MAX_FILE_BYTES",
    "MAX_ENTRIES",
    "read_missed",
    "log_missed",
    "clear_missed",
]

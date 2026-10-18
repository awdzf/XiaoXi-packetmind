"""数据包解析:核心只有一份,数据包可以有任意多个。

分层:

    核心代码    experts/core/smallai/                ← 只有这一份(本包)
    数据包      experts/packs/<名字>/pack.json        ← 每块知识一个目录,只有数据
    内置示例    experts/core/smallai/domain/pack.json ← 一个包都没指定时的兜底

为什么要有这一层:以前每个数据目录里都塞着一份完整的 smallai 副本,
8 份代码逐字节相同,只有 pack.json 不同。现在代码只留一份,「这一轮用哪个包」
由一个目录决定 —— 增删知识 = 增删一个数据包目录,核心一个字节都不用改。
数据包也因此可以被随便拷走、比对、单独发给别人看。

一个数据包可以**覆盖**的核心数据文件(包里有就用包里的,没有就用核心自带的):

    pack.json          领域包(必需)
    kb.json            事实库(可选)
    word_vectors.json  词向量(可选)

数据包是**怎么被选中的**,永远说得清(`--health` 与排错都靠 `describe()`)。
"""

from __future__ import annotations

import os
from pathlib import Path

#: 环境变量:进程级指定数据包(GoChat 的子进程调用会同时用 --pack 和它,双保险)
ENV_VAR = "SMALLAI_PACK"

#: 数据包的身份文件:一个目录里有它才算数据包
PACK_FILENAME = "pack.json"

#: 核心自带的兜底数据包(一个包都没指定时用)
_BUNDLED_DIR = Path(__file__).resolve().parent / "domain"

_EXPLICIT: Path | None = None
_CACHE_KEY: tuple[str | None, str | None, str] | None = None
_CACHE_VALUE: tuple[Path, str] | None = None


# --------------------------------------------------------------------------
# 选择数据包
# --------------------------------------------------------------------------
def is_pack_dir(path: str | Path) -> bool:
    """这个目录是不是一个数据包(有 pack.json)。"""
    return (Path(path) / PACK_FILENAME).is_file()


def set_pack_dir(path: str | Path | None) -> Path | None:
    """显式指定数据包目录(CLI --pack / 进程内切换)。传 None 表示撤销。"""
    global _EXPLICIT, _CACHE_KEY, _CACHE_VALUE
    if path is None or str(path).strip() == "":
        _EXPLICIT = None
    else:
        target = Path(path).expanduser().resolve()
        if not is_pack_dir(target):
            raise FileNotFoundError(
                f"不是一个数据包(缺 {PACK_FILENAME}):{target}"
                + _pack_hint()
            )
        _EXPLICIT = target
    _CACHE_KEY, _CACHE_VALUE = None, None
    return _EXPLICIT


def _pack_hint() -> str:
    names = available_packs()
    if not names:
        return ""
    return "。现有的数据包:" + "、".join(names)


def _walk_up(start: Path) -> Path | None:
    """从 start 逐级向上,找第一个含 pack.json 的目录。"""
    for candidate in (start, *start.parents):
        if is_pack_dir(candidate):
            return candidate
    return None


def resolve() -> tuple[Path, str]:
    """(数据包目录, 选中理由)。理由只有四种:explicit / env / cwd / bundled。"""
    global _CACHE_KEY, _CACHE_VALUE
    cwd = Path.cwd()
    key = (
        str(_EXPLICIT) if _EXPLICIT is not None else None,
        os.environ.get(ENV_VAR) or None,
        str(cwd),
    )
    if _CACHE_VALUE is not None and _CACHE_KEY == key:
        return _CACHE_VALUE

    found: tuple[Path, str]
    if _EXPLICIT is not None:
        found = (_EXPLICIT, "explicit")
    else:
        env = os.environ.get(ENV_VAR)
        if env and env.strip():
            env_dir = Path(env).expanduser().resolve()
            if not is_pack_dir(env_dir):
                raise FileNotFoundError(
                    f"{ENV_VAR} 指向的不是数据包(缺 {PACK_FILENAME}):{env_dir}"
                    + _pack_hint()
                )
            found = (env_dir, "env")
        else:
            walked = _walk_up(cwd)
            found = (walked, "cwd") if walked is not None else (_BUNDLED_DIR, "bundled")

    _CACHE_KEY, _CACHE_VALUE = key, found
    return found


def active_pack_dir() -> Path:
    return resolve()[0]


def active_pack_file() -> Path:
    return active_pack_dir() / PACK_FILENAME


def reason() -> str:
    return resolve()[1]


def describe() -> str:
    """一句话交代「用的是哪个数据包、凭什么选它」。"""
    pack_dir, why = resolve()
    labels = {
        "explicit": "命令行 --pack 指定",
        "env": f"环境变量 {ENV_VAR}",
        "cwd": "从当前目录向上找到",
        "bundled": "核心自带示例包(没有指定任何数据包)",
    }
    return f"{pack_dir}({labels.get(why, why)})"


# --------------------------------------------------------------------------
# 数据文件
# --------------------------------------------------------------------------
def bundled_pack_dir() -> Path:
    """核心自带的示例数据包目录。"""
    return _BUNDLED_DIR


def pack_data(name: str, bundled: str | Path) -> Path:
    """数据文件:数据包里有同名文件就用它,否则用核心自带的那份。

    覆盖是逐个文件生效的:一个包可以只覆盖 kb.json 而不碰词向量。
    """
    local = active_pack_dir() / name
    if local.is_file():
        return local
    return Path(bundled)


def data_sources(name: str, bundled: str | Path) -> tuple[Path, str]:
    """(实际用的文件, 来源说明)—— 给 --health 如实交代用。"""
    local = active_pack_dir() / name
    if local.is_file():
        return local, "数据包覆盖"
    fallback = Path(bundled)
    return fallback, "核心自带"


# --------------------------------------------------------------------------
# 目录约定(给报错提示与 --health 用;核心被拷走时这些目录可能不存在)
# --------------------------------------------------------------------------
def sibling_packs_dir() -> Path:
    """experts/packs —— 本核心旁边的数据包仓库(约定,不要求存在)。"""
    return Path(__file__).resolve().parents[2] / "packs"


def available_packs() -> list[str]:
    """列出旁边的数据包名字(只用于提示,不参与选择)。"""
    root = sibling_packs_dir()
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir() and is_pack_dir(p))


__all__ = [
    "ENV_VAR",
    "PACK_FILENAME",
    "is_pack_dir",
    "set_pack_dir",
    "resolve",
    "active_pack_dir",
    "active_pack_file",
    "reason",
    "describe",
    "bundled_pack_dir",
    "pack_data",
    "data_sources",
    "sibling_packs_dir",
    "available_packs",
]

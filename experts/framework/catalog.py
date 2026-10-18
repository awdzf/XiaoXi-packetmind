"""数据包目录:自动发现 `experts/packs/*/pack.json`,不写死名单。

小析(**一个人格**)只有一个,知识则按主题分成**数据包**:
**核心只有一份**(`experts/core/smallai/`),数据包只有 JSON ——

    新增知识 = 放一个数据包目录;
    移除知识 = 删掉那个目录;
    改某一块知识 = 改它自己的 pack.json,核心一个字节都不动。

人格(名字、口吻、自述)从 `core/smallai/persona.py` 来,与数据包无关:
删掉所有数据包,小析依然知道自己是谁,只是没有知识可查。

框架只认三个字段(其余可选):

    expert.role  "domain"(默认)或 "general"
    expert.order 排序权重(小的先;默认 100)
    expert.routes 可选的转交规则:[{"keywords": [...], "to": "physics"}]

为什么 general 要单独标出来:
  · 查找先按领域词找包;只有**一个领域包都没命中**时,才轮到它;
  · 所以兜底包不会抢走领域问题,也不参与"问得广 → 多个包"的分配。

读不到/读坏某份 pack.json 时**跳过并记账**(不静默):少一包知识只是少一部分功能,
框架本身仍然可用 —— 这正是"知识可自由移出"的要求。
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # experts/
PACKS_DIR = ROOT / "packs"
PACK_GLOB = "packs/*/pack.json"

#: 单核:所有数据包共用这一份代码(子进程调用时由它带 --pack 指到各自的包)
CORE_DIR = ROOT / "core"
CORE_RUN = CORE_DIR / "run.py"

# 人格与核心同源:框架也 import 同一个 persona,保证终端(框架)与核心(cli)
# 说话是同一口径。core/ 放进 sys.path 只为了这一件事。
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))
from smallai import persona  # noqa: E402  (必须在 sys.path 调整之后)

GENERAL_ROLE = "general"
MEMORY_ROLE = "memory"
DEFAULT_ORDER = 100


@dataclass(frozen=True)
class ExpertCard:
    folder: str
    pack_id: str
    name: str
    tagline: str
    scope: tuple[str, ...]
    entries: tuple[dict, ...]
    pack_path: Path
    role: str = "domain"
    order: int = DEFAULT_ORDER
    routes: tuple[dict, ...] = ()
    #: 包关系:本包包含谁 / 本包被谁包含(两边写一个就够,见 relations.PackGraph)
    contains: tuple[str, ...] = ()
    contained_by: tuple[str, ...] = ()
    #: 结构化知识:事实 / 规则 / 关系(可选;没有就仍按问答条目工作)
    facts: tuple[dict, ...] = ()
    rules: tuple[dict, ...] = ()
    relations: tuple[dict, ...] = ()
    #: 可选的 Datalog 子集（事实/规则字符串或对象），给有界规划器做递归推理
    datalog: dict = field(default_factory=dict)
    #: 包级优先级（World Info 式：数字越大越先进入预算；默认 100）
    priority: int = DEFAULT_ORDER

    @property
    def has_structure(self) -> bool:
        return bool(self.facts or self.rules or self.relations or self.datalog)

    @property
    def is_general(self) -> bool:
        return self.role == GENERAL_ROLE

    @property
    def is_memory(self) -> bool:
        """记忆数据包:用户教的知识(`role: "memory"`)。"""
        return self.role == MEMORY_ROLE

    def entry(self, entry_id: str) -> dict | None:
        for item in self.entries:
            if item.get("id") == entry_id:
                return item
        return None

    def examples(self, limit: int = 3) -> list[str]:
        return [str(e.get("question", "")) for e in self.entries[:limit] if e.get("question")]

    @property
    def pack_dir(self) -> Path:
        """数据包目录(核心以 `--pack <这里>` 被调用)。"""
        return self.pack_path.parent


DISCOVERY_ERRORS: list[str] = []

#: 解析过的数据包:路径 -> (mtime_ns, size, 卡片)。
#: 教完知识会 `reload()`(重新 discover),但没变的包不必重新读 JSON、重解条目 ——
#: 单进程常驻时这个缓存让 reload 只碰真正改过的那一个文件。
_CARD_CACHE: dict[Path, tuple[int, int, ExpertCard]] = {}


def _cached_card(path: Path) -> ExpertCard:
    stat = path.stat()
    cached = _CARD_CACHE.get(path)
    if cached is not None and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
        return cached[2]

    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("数据包不是一个 JSON 对象")

    folder = path.parent.name
    expert = raw.get("expert") or {}
    role = str(expert.get("role") or "domain")
    try:
        order = int(expert.get("order", DEFAULT_ORDER))
    except (TypeError, ValueError):
        order = DEFAULT_ORDER
    routes = tuple(r for r in expert.get("routes") or () if isinstance(r, dict))
    contains = tuple(str(x) for x in expert.get("contains") or () if str(x).strip())
    contained_by = tuple(str(x) for x in expert.get("contained_by") or () if str(x).strip())
    facts = tuple(x for x in raw.get("facts") or () if isinstance(x, dict))
    rules = tuple(x for x in raw.get("rules") or () if isinstance(x, dict))
    relations = tuple(x for x in raw.get("relations") or () if isinstance(x, dict))
    datalog_raw = raw.get("datalog")
    datalog = datalog_raw if isinstance(datalog_raw, dict) else {}
    try:
        priority = int(expert.get("priority", DEFAULT_ORDER))
    except (TypeError, ValueError):
        priority = DEFAULT_ORDER

    card = ExpertCard(
        folder=folder,
        pack_id=str(expert.get("id") or folder),
        name=str(expert.get("name") or folder),
        tagline=str(expert.get("tagline") or ""),
        scope=tuple(str(t) for t in expert.get("scope") or ()),
        entries=tuple(raw.get("entries") or ()),
        pack_path=path,
        role=role,
        order=order,
        routes=routes,
        contains=contains,
        contained_by=contained_by,
        facts=facts,
        rules=rules,
        relations=relations,
        datalog=datalog,
        priority=priority,
    )
    _CARD_CACHE[path] = (stat.st_mtime_ns, stat.st_size, card)
    return card


def discover(root: Path = ROOT) -> tuple[ExpertCard, ...]:
    """扫描目录,返回排好序的数据包卡片;读坏的包记进 DISCOVERY_ERRORS。

    root 会先解析成绝对路径:核心是子进程调用(`--pack <目录>`),相对路径在
    子进程里会被按它自己的 cwd 再解析一次,直接跑偏。
    """
    global DISCOVERY_ERRORS
    errors: list[str] = []
    cards: list[ExpertCard] = []
    root = Path(root).resolve()

    for path in sorted(root.glob(PACK_GLOB)):
        folder = path.parent.name
        try:
            cards.append(_cached_card(path))
        except (OSError, json.JSONDecodeError, ValueError, TypeError, AttributeError) as exc:
            _CARD_CACHE.pop(path, None)
            errors.append(f"{folder}:数据包读取失败({exc})")

    # 领域包在前(按 order、再按文件夹),兜底包永远最后
    cards.sort(key=lambda c: (1 if c.is_general else 0, c.order, c.folder))
    DISCOVERY_ERRORS = errors
    return tuple(cards)


CATALOG: tuple[ExpertCard, ...] = discover()


def card_for(folder: str, cards: tuple[ExpertCard, ...] | None = None) -> ExpertCard | None:
    pool = CATALOG if cards is None else cards
    for card in pool:
        if card.folder == folder:
            return card
    return None


def general_card(cards: tuple[ExpertCard, ...] | None = None) -> ExpertCard | None:
    pool = CATALOG if cards is None else cards
    for card in pool:
        if card.is_general:
            return card
    return None


__all__ = [
    "ROOT",
    "PACKS_DIR",
    "PACK_GLOB",
    "CORE_DIR",
    "CORE_RUN",
    "GENERAL_ROLE",
    "MEMORY_ROLE",
    "DEFAULT_ORDER",
    "ExpertCard",
    "CATALOG",
    "DISCOVERY_ERRORS",
    "discover",
    "card_for",
    "general_card",
]

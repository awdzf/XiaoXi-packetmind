"""数据包:本地知识条目 + 检索 —— **这一层不接任何模型**。

这位本地小 AI 叫「小析」(人格见 `smallai/persona.py`),它的知识按主题分成
若干个**数据包**;这里负责把"当前数据包"的条目读进来、按字符串匹配检索,
每一步都可核对:

    1. 先在**当前数据包**的 pack.json 里检索(题目 / 别名 / 关键词,纯字符串匹配,零依赖);
    2. 检索到 -> 直接给条目里写好的答案,并把条目 id 一并交代;
       检索不到 -> 如实说"我的知识条目里没有这一条",给相邻条目与边界说明。

所以小析**不做实事**(不写代码、不跑构建、不联网查最新文档),但基础概念问题
答得出;每句话都能追到 pack.json 的某一条 —— 不会编。

改知识 = 改数据包的 pack.json(加条目、加别名/关键词),不用改一行 Python;
这与基础版"加事实改 kb.json"是同一条纪律。

**代码只有一份,数据包可以有很多个**:这份代码不知道自己在哪个包里,
「这一轮是哪个包」由 `smallai/packs.py` 解析出来的目录决定。所以 `PACK` 是个
**惰性代理**(见文件末尾):它的属性每次访问都落在"当前数据包"上 —— 同一个进程里
换一个包,知识立刻换成那一包。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core import normalize
from ..packs import active_pack_dir, active_pack_file

# 命中一个关键词(12 分)即可作答;低于这个分视为"没找到条目"。
_MIN_SCORE = 10
# 候选建议的门槛:只把"沾边"的条目列出来,不把整本目录倒出来
_MIN_SUGGEST = 4

_PUNCT_RE = re.compile(r"[，。！？、；:：,.!?;~～\"'“”‘’()（）\[\]【】《》<>]+")


def _key(text: str) -> str:
    """归一化检索键:全角转半角、标点换成空格、转小写、压缩空白。

    保留空格是为了让英文词能按**词边界**匹配("what is java" 里 "java"
    不该被当成 "javascript" 的一部分);中文没有空格,子串匹配不受影响。
    """
    cleaned = _PUNCT_RE.sub(" ", normalize(text or "").lower())
    return " ".join(cleaned.split())


def _term_in(key: str, term: str) -> bool:
    """term 是否出现在 key 里。

    ASCII 词按**词边界**匹配:否则 java 包的领域词 "java" 会把
    "javascript 是什么" 也认成自己的问题(这是实测最容易踩的坑)。
    中文词按子串匹配(中文没有空格分词,子串是唯一现实做法)。
    """
    t = _key(term)
    if not t:
        return False
    if t.isascii() and t.isalnum():
        return re.search(rf"(?<![a-z0-9]){re.escape(t)}(?![a-z0-9])", key) is not None
    return t in key


_SECONDARY_STOP_CHARS = frozenset(
    "的是了和与在有我你他她它们这那什么怎样如何为可以能否多少哪些个之于其也都就而但又很太更最不没请帮该把被对从到及或会要"
)
_ASCII_SECONDARY_RE = re.compile(r"[A-Za-z0-9_+.-]{2,}")
_CJK_SECONDARY_RE = re.compile(r"[\u4e00-\u9fff]{2,}")


def _derive_secondary(text: str) -> tuple[str, ...]:
    """为老记忆条目补次要关键词（只用于 user-taught）。

    核心不依赖框架的分词器，所以这里用最保守的两字组合：ASCII 词 + 中文
    两字窗口，跳过含虚词/代词的窗口。它只用于兼容旧条目；新条目在写入时
    已经由 `framework/lessons.secondary_keywords` 抽好了。
    """
    out: list[str] = []
    for word in _ASCII_SECONDARY_RE.findall(text or ""):
        word = word.lower()
        if word not in out:
            out.append(word)
    for run in _CJK_SECONDARY_RE.findall(text or ""):
        for index in range(len(run) - 1):
            gram = run[index:index + 2]
            if gram[0] in _SECONDARY_STOP_CHARS or gram[1] in _SECONDARY_STOP_CHARS:
                continue
            if gram not in out:
                out.append(gram)
    return tuple(out[:8])


@dataclass(frozen=True)
class Entry:
    """一条知识条目:一个基础问题的**可核对答案**。"""

    id: str
    question: str
    answer: str
    aliases: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    secondary_keywords: tuple[str, ...] = ()
    points: tuple[str, ...] = ()
    see_also: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> "Entry":
        def tup(name: str) -> tuple[str, ...]:
            value = raw.get(name) or ()
            if isinstance(value, str):
                value = (value,)
            return tuple(str(v) for v in value if str(v).strip())

        tags = tup("tags")
        secondary = tup("secondary_keywords")
        question = str(raw.get("question", "")).strip()
        # 兼容升级前教下的记忆:那时还没写 secondary_keywords。
        # 只在 user-taught 条目上补,避免给手写条目凭空加检索键。
        if not secondary and "user-taught" in tags and question:
            secondary = _derive_secondary(question)
        return cls(
            id=str(raw.get("id", "")).strip(),
            question=question,
            answer=str(raw.get("answer", "")).strip(),
            aliases=tup("aliases"),
            keywords=tup("keywords"),
            secondary_keywords=secondary,
            points=tup("points"),
            see_also=tup("see_also"),
            tags=tags,
        )


@dataclass(frozen=True)
class Hit:
    """一次检索的结果。

    entry=None 有两种情况:完全没沾边(score 很小),或沾边但没有条目够格
    (score > 0 但 < _MIN_SCORE)—— 后者用 near 给出相邻条目,让"没答上"也有用。
    """

    entry: Entry | None = None
    score: int = 0
    terms: tuple[str, ...] = ()
    near: tuple[Entry, ...] = ()

    @property
    def found(self) -> bool:
        return self.entry is not None


def _score_entry(entry: Entry, key: str) -> tuple[int, tuple[str, ...]]:
    """给一个条目打分。题目/别名最重,关键词次之,标签最轻。"""
    score = 0
    terms: list[str] = []

    for alias in (entry.question,) + entry.aliases:
        a = _key(alias)
        if len(a) < 2:
            continue
        if key == a:
            score = max(score, 100 + len(a))
            terms.append(alias)
        elif a in key:
            score = max(score, 40 + min(len(a), 20))
            terms.append(alias)
        elif len(key) >= 3 and key in a and len(a) - len(key) <= 3:
            # 用户只写了题目的一半(如 "html" 对 "html是什么"),也算命中
            score = max(score, 30 + min(len(key), 20))
            terms.append(alias)

    for keyword in entry.keywords:
        if _term_in(key, keyword):
            score += 12
            terms.append(keyword)
    primary_keys = {_key(keyword) for keyword in entry.keywords}
    for keyword in entry.secondary_keywords:
        if _key(keyword) in primary_keys:
            continue
        if _term_in(key, keyword):
            # 次要关键词:低权重,但单一个也能过 _MIN_SCORE(10),
            # 用于"那它是什么意思"这种只带一个区分词的追问。
            score += 10
            terms.append(keyword)
    for tag in entry.tags:
        if _term_in(key, tag):
            score += 4
            terms.append(tag)

    return score, tuple(dict.fromkeys(terms))


class ExpertPack:
    """一个数据包(pack.json)在内存里的形态。"""

    def __init__(self, raw: dict[str, Any], source: Path) -> None:
        self.raw = raw
        self.source = source
        expert = raw.get("expert") or {}
        self.expert: dict[str, Any] = expert
        self.entries: tuple[Entry, ...] = tuple(Entry.from_json(e) for e in raw.get("entries") or ())
        self._by_id = {e.id: e for e in self.entries}
        self.scope: tuple[str, ...] = tuple(str(t) for t in expert.get("scope") or ())
        self.siblings: tuple[dict[str, Any], ...] = tuple(expert.get("siblings") or ())
        self.safety: dict[str, Any] = raw.get("safety") or {}
        # 结构化知识(可选):事实 / 规则 / 关系。没有这些字段时行为和以前完全一样。
        self.facts: tuple[dict[str, Any], ...] = tuple(
            item for item in raw.get("facts") or () if isinstance(item, dict)
        )
        self.rules: tuple[dict[str, Any], ...] = tuple(
            item for item in raw.get("rules") or () if isinstance(item, dict)
        )
        self.relations: tuple[dict[str, Any], ...] = tuple(
            item for item in raw.get("relations") or () if isinstance(item, dict)
        )
        # 可选：Datalog 子集（由框架层的有界规划器解释；核心只负责如实携带/体检）
        datalog_raw = raw.get("datalog")
        self.datalog: dict[str, Any] = datalog_raw if isinstance(datalog_raw, dict) else {}

    # ---- 名片 ------------------------------------------------------------
    @property
    def id(self) -> str:
        return str(self.expert.get("id", "unknown"))

    @property
    def name(self) -> str:
        return str(self.expert.get("name", "未命名数据包"))

    @property
    def tagline(self) -> str:
        return str(self.expert.get("tagline", ""))

    @property
    def limits(self) -> tuple[str, ...]:
        return tuple(str(t) for t in self.expert.get("limits") or ())

    @property
    def role(self) -> str:
        return str(self.expert.get("role") or "domain")

    @property
    def is_memory(self) -> bool:
        """记忆数据包:用户教的知识。空着是正常状态,不算坏包。"""
        return self.role == "memory"

    @property
    def has_structure(self) -> bool:
        """这个包有没有结构化知识(事实/规则/关系/Datalog)。"""
        return bool(self.facts or self.rules or self.relations or self.datalog)

    def entry(self, entry_id: str) -> Entry | None:
        return self._by_id.get(entry_id)

    def sample_questions(self, limit: int | None = None) -> list[str]:
        questions = [e.question for e in self.entries]
        return questions if limit is None else questions[:limit]

    def card(self) -> str:
        lines = [
            f"数据包    : {self.name}({self.id})",
            f"一句话    : {self.tagline}",
            f"领域词    : {'、'.join(self.scope)}",
            f"知识条目  : {len(self.entries)} 条(来源 {self.source.name})",
            "能答的例子:",
        ]
        lines.extend("  · " + q for q in self.sample_questions(6))
        if self.has_structure:
            lines.append(
                f"结构化知识: 事实 {len(self.facts)} / 规则 {len(self.rules)} / "
                f"关系 {len(self.relations)} / Datalog "
                f"{len(self.datalog.get('facts') or ()) + len(self.datalog.get('rules') or ())}"
            )
        if self.limits:
            lines.append("明确不做  :")
            lines.extend("  · " + item for item in self.limits)
        refuse, caution = self.safety_counts()
        lines.append(f"安全兜底  : 拒答规则 {refuse} 条 / 灰区提醒 {caution} 条(定义在同一份 pack.json)")
        return "\n".join(lines)

    def safety_counts(self) -> tuple[int, int]:
        return (
            len(self.safety.get("refuse") or ()),
            len(self.safety.get("caution") or ()),
        )

    # ---- 检索 ------------------------------------------------------------
    def matches(self, text: str) -> bool:
        """这句话是不是落在这个包的知识范围里(查找用)。

        判据:命中领域词,或命中某个条目的关键词/题目。
        """
        key = _key(text)
        if not key:
            return False
        if any(_term_in(key, term) for term in self.scope):
            return True
        return self.lookup(key).found

    def lookup(self, text: str) -> Hit:
        key = _key(text)
        if not key:
            return Hit()

        scored: list[tuple[int, Entry, tuple[str, ...]]] = []
        for entry in self.entries:
            score, terms = _score_entry(entry, key)
            if score > 0:
                scored.append((score, entry, terms))
        if not scored:
            return Hit()
        scored.sort(key=lambda item: (-item[0], item[1].id))

        best_score, best_entry, best_terms = scored[0]
        near = tuple(entry for score, entry, _ in scored[1:4] if score >= _MIN_SUGGEST)
        if best_score < _MIN_SCORE:
            return Hit(entry=None, score=best_score, terms=best_terms,
                       near=tuple(entry for _, entry, _ in scored[:3]))
        return Hit(entry=best_entry, score=best_score, terms=best_terms, near=near)

    # ---- 自检(给 --health 与测试用)-------------------------------------
    def validate(self) -> list[str]:
        """体检领域包:返回问题清单,空清单才算健康。"""
        problems: list[str] = []
        if not self.expert.get("id"):
            problems.append("expert.id 缺失")
        if not self.expert.get("name"):
            problems.append("expert.name 缺失")
        if not self.scope and not self.has_structure:
            problems.append("expert.scope 为空:没有领域词就无法判断'这是不是我的问题'")
        if not self.entries and not self.is_memory and not self.has_structure:
            # 记忆数据包一开始就是空的(等用户教);结构化知识包也可以只有事实/规则
            problems.append("entries 为空:一个知识条目都没有")

        seen_ids: set[str] = set()
        seen_keys: dict[str, str] = {}
        for entry in self.entries:
            if not entry.id:
                problems.append(f"有条目缺 id:{entry.question!r}")
                continue
            if entry.id in seen_ids:
                problems.append(f"条目 id 重复:{entry.id}")
            seen_ids.add(entry.id)
            if not entry.question or not entry.answer:
                problems.append(f"条目 {entry.id} 的题目或答案为空")
            if not entry.keywords and not entry.aliases:
                problems.append(f"条目 {entry.id} 既没有别名也没有关键词:除了原题,检索不到它")
            for keyword in entry.keywords:
                if len(_key(keyword)) < 2:
                    problems.append(f"条目 {entry.id} 的关键词太短:{keyword!r}(会误命中)")
            for keyword in entry.secondary_keywords:
                if len(_key(keyword)) < 2:
                    problems.append(f"条目 {entry.id} 的次要关键词太短:{keyword!r}(会误命中)")
            for alias in (entry.question,) + entry.aliases:
                key = _key(alias)
                if not key:
                    continue
                owner = seen_keys.get(key)
                if owner and owner != entry.id:
                    problems.append(f"题目/别名 {alias!r} 同时指向 {owner} 与 {entry.id}(会二义)")
                else:
                    seen_keys[key] = entry.id
            for other in entry.see_also:
                if other not in self._by_id:
                    problems.append(f"条目 {entry.id} 的 see_also 指向不存在的条目:{other}")

        # 覆盖自检:每条条目的原题都必须能检回它自己
        for entry in self.entries:
            if not entry.question:
                continue
            hit = self.lookup(entry.question)
            if hit.entry is None or hit.entry.id != entry.id:
                got = hit.entry.id if hit.entry else "无"
                problems.append(f"原题检不回自己:{entry.question!r} -> {got}")

        # 安全规则的结构性检查(正则能不能编由 safety 模块负责报)
        for level in ("refuse", "caution"):
            for rule in self.safety.get(level) or ():
                rid = rule.get("id") or "<无 id>"
                if not rule.get("label"):
                    problems.append(f"安全规则 {rid} 缺 label")
                if not rule.get("patterns"):
                    problems.append(f"安全规则 {rid} 没有 patterns")
                if not rule.get("advice"):
                    problems.append(f"安全规则 {rid} 没有 advice(拒答/提醒时没话说)")

        problems.extend(self._structural_problems())
        return problems

    def _structural_problems(self) -> list[str]:
        """检查可选的结构化知识(事实 / 规则 / 关系),坏条目如实报出来。"""
        problems: list[str] = []

        for index, item in enumerate(self.facts, 1):
            predicate = str(item.get("predicate") or "").strip()
            args = item.get("args")
            if not predicate:
                problems.append(f"事实 #{index} 缺 predicate")
                continue
            if not isinstance(args, (list, tuple)) or not args:
                problems.append(f"事实 #{index}({predicate})的 args 必须是非空数组")
                continue
            if any(not str(arg).strip() for arg in args):
                problems.append(f"事实 #{index}({predicate})里有空参数")

        seen_rules: set[str] = set()
        for index, item in enumerate(self.rules, 1):
            name = str(item.get("name") or "").strip()
            condition = item.get("if")
            conclusion = item.get("then")
            if not name:
                problems.append(f"规则 #{index} 缺 name")
            elif name in seen_rules:
                problems.append(f"规则名重复(后一条会覆盖前一条):{name}")
            else:
                seen_rules.add(name)
            if not isinstance(condition, dict) or not condition:
                problems.append(f"规则 {name or '#' + str(index)} 的 if 必须是对象")
            if not isinstance(conclusion, (list, tuple)) or not conclusion:
                problems.append(f"规则 {name or '#' + str(index)} 的 then 必须是非空数组")
            elif not str(conclusion[0]).strip():
                problems.append(f"规则 {name or '#' + str(index)} 的 then 缺谓词")

        for index, item in enumerate(self.relations, 1):
            relation = str(item.get("type") or item.get("predicate") or "").strip()
            subject = str(item.get("from") or item.get("subject") or "").strip()
            obj = str(item.get("to") or item.get("object") or "").strip()
            if not relation or not subject or not obj:
                problems.append(
                    f"关系 #{index} 需要 type/from/to(或 predicate/subject/object)"
                )

        if self.datalog:
            facts = self.datalog.get("facts", ())
            rules = self.datalog.get("rules", ())
            if not isinstance(facts, (list, tuple)):
                problems.append("datalog.facts 必须是数组")
            if not isinstance(rules, (list, tuple)):
                problems.append("datalog.rules 必须是数组")
        return problems


def load_pack(path: Path | None = None) -> ExpertPack:
    """读一个数据包。不给路径 = 读"当前数据包"(见 smallai/packs.py)。"""
    path = Path(path) if path is not None else active_pack_file()
    if not path.is_file():
        raise FileNotFoundError(f"领域包不存在:{path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    return ExpertPack(raw, path)


class _LazyPack:
    """当前数据包的惰性代理:属性访问时才读盘,换了包就自动重读。

    为什么要惰性:这份代码是**单核**的,同一份代码在不同数据包下是不同的知识。
    导入时把数据包钉死,`--pack` 与进程内切换就都没法生效了;而每层都去
    `load_pack()` 又会到处重复读盘。代理把两者折中:名字 `PACK` 始终是同一个
    对象(各层 import 到的就是它),它背后指向哪个包、什么时候读,由 `packs.py`
    的解析结果决定。

    缓存键不只认路径,还认 `pack.json` 的**修改时间与大小**:单进程常驻时,
    教完知识(`lessons.py` 原子替换同路径文件)不重启进程也要能读到新条目;
    否则会一直拿旧包答题。`invalidate()` 给显式丢弃用。

    读不到就当场报错(FileNotFoundError),不静默降级成"什么都不知道的空包"。
    """

    __slots__ = ("_key", "_pack")

    def __init__(self) -> None:
        self._key: str = ""
        self._pack: ExpertPack | None = None

    @staticmethod
    def _stamp(path: Path) -> str:
        try:
            stat = path.stat()
        except OSError:
            return str(path)
        return f"{path}:{stat.st_mtime_ns}:{stat.st_size}"

    def current(self) -> ExpertPack:
        path = active_pack_file()
        key = self._stamp(path)
        if self._pack is None or self._key != key:
            self._pack = load_pack(path)
            self._key = key
        return self._pack

    def invalidate(self) -> None:
        """丢掉已读的包,下一次属性访问重新读盘。"""
        self._pack = None
        self._key = ""

    @property
    def stamp(self) -> str:
        """当前包的指纹(给按包缓存的东西当 key,例如安全规则编译结果)。"""
        self.current()
        return self._key

    def __getattr__(self, name: str) -> Any:
        return getattr(self.current(), name)

    def __repr__(self) -> str:
        try:
            return f"<当前数据包 {self.current().name}({self.current().id})>"
        except (FileNotFoundError, ValueError) as exc:      # pragma: no cover - 排错用
            return f"<当前数据包不可用:{exc}>"


#: 当前数据包(单核共用这一个名字;它指向哪个包见 packs.active_pack_dir())
PACK = _LazyPack()

__all__ = [
    "Entry",
    "Hit",
    "ExpertPack",
    "PACK",
    "_LazyPack",
    "active_pack_dir",
    "active_pack_file",
    "load_pack",
]

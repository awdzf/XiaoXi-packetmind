"""记忆数据包:把用户教的知识写进去,下次用它的关键词命中。

用户教东西的格式(在终端里直接输入一行)：

    # 问题~答案
    # 量子退相干是什么~量子系统的相干性因与环境纠缠而衰减的过程
    # 量子退相干是量子系统的相干性因与环境纠缠而衰减的过程

`#` 是**写入记忆符**：带 `~` 时，前面是题面、后面是答案；不带 `~` 时，
整句同时当题面和答案（旧格式仍兼容）。没有内容（`#` 后面空着）就当没写。

与它配套的指令(同一个 `#` 家族):

    #!问题~新答案   修正同一道题（CBR 的 revise；旧答案留在 revisions 里）
    #?              看看我现在记着哪些你教的知识(**原样回放你当时发的那行**)
    #? 量子         只看匹配的一部分
    #- 量子退相干   忘掉这条知识(先列出来,确认后才删)

写入纪律(和"不编"同一条):

  · 先问再写 —— `teach()` 只负责写文件;`是否写入记忆[y/n]:` 的确认在终端里做;
  · 关键词来自**拆句**(`Decomposition.keywords`),它同时是以后命中的检索键;
  · **次要关键词**(`secondary_keywords`)从整句再扫一遍,权重低一点,
    用来区分同主题的多条记忆(例如"What 是英语单词"和"What 是'什么'的意思");
  · 写进去的条目带 `user-taught` 标记,来源可查;`raw` 保存用户当时的原话;
  · 同一道题教两次不重复写:更新答案/关键词,返回 `created=False`；
    答案变了会追加一条 `revisions`（旧答案可追溯），这是 CBR 的 revise；
  · 删掉的条目 id 不再复用(`_next_user_id` 记账,新条目从更大的号开始)。

它不改用户的原话:question 与 answer 分别是用户写的题面/答案，raw 是带 `#` 的整行。
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from .catalog import ExpertCard
from .decomposer import PARTICLES, PRONOUNS

#: 写入记忆符
MARKER = "#"
#: 遗忘记忆符(`#-……`)
FORGET_MARKER = "#-"
#: 显式修正符(`#!问题~新答案`);不带 `!` 时,同题再教也会自动修正
CORRECTION_MARKERS = ("#!", "#！")
#: 问答题分隔符（中文全角也认）
PAIR_SEPARATORS = ("~", "～")
#: 查看记忆的写法(第一个是主写法,其余是别名)
VIEW_MARKERS = ("#?", "#？", "#ls", "#list", "#记忆", "#查看")
#: 可选前缀(用户的写法:`#这是新知识:……`)
PREFIX = "这是新知识"
#: 条目标记:这些是"人教的",不是数据包自带的
TAUGHT_TAG = "user-taught"

_ID_RE = re.compile(r"^user-(\d+)$")
_YES = {"y", "yes", "是", "好", "写入", "写", "删", "删除", "1"}
_WS_RE = re.compile(r"\s+")
#: 次要关键词跳过的词：功能词 / 代词 / 纯疑问词。
#: 注意**不跳过**「意思」这类 CORE_WORDS —— 它正是"What 是'什么'的意思"里
#: 用来区分另一条 "What 是英语单词" 的关键。
_SECONDARY_SKIP = set(PARTICLES) | set(PRONOUNS) | {
    "什么", "怎么", "怎样", "如何", "为什么", "为何", "是否", "可以", "能否",
    "多少", "哪个", "哪些", "什么样", "怎样做",
}


def parse_view(text: str) -> str | None:
    """`#?……` -> 过滤词;不是查看指令返回 None。

    空串 `""` 表示"看全部"。`#?`/`#？` 带任何后缀都算查看(后缀当过滤词)。
    """
    item = (text or "").strip()
    if not item:
        return None
    for marker in ("#?", "#？"):
        if item.startswith(marker):
            body = item[len(marker):].strip()
            return body.lstrip(":：,，、 ").strip()
    lowered = item.lower()
    for marker in ("#ls", "#list"):
        if lowered == marker or lowered.startswith(marker + " ") or lowered.startswith(marker + ":"):
            return item[len(marker):].strip().lstrip(":：,，、 ").strip()
    for marker in ("#记忆", "#查看"):
        if item.startswith(marker):
            return item[len(marker):].strip().lstrip(":：,，、 ").strip()
    return None


def is_view_command(text: str) -> bool:
    return parse_view(text) is not None


def parse_forget(text: str) -> str | None:
    """`#-……` -> 要忘掉的那段话(关键词或原话);不是遗忘指令返回 None。

    `#-` 后面空着也返回 `""` —— 让调用方提示用法,而不是"把所有记忆都忘了"。
    顺手认全角减号(`#－`)与破折号(`#—`)。
    """
    item = (text or "").strip()
    if not item:
        return None
    for marker in (FORGET_MARKER, "#－", "#—", "#——"):
        if item.startswith(marker):
            body = item[len(marker):].strip()
            return body.lstrip(":：,，、 ").strip()
    return None


def parse_marker(text: str) -> str | None:
    """`#……` -> 要写入的知识原文;不是写入指令返回 None。

    没有内容(只有 `#` 或只有前缀)也返回 None —— 让调用方提示用户补内容,
    而不是把一句空话写进记忆。`#-` 与 `#?` 属于另外两个指令,这里一律不认。
    """
    item = (text or "").strip()
    if not item.startswith(MARKER):
        return None
    if item.startswith(CORRECTION_MARKERS):
        return None
    if parse_forget(item) is not None:
        return None
    if is_view_command(item):
        return None
    body = item[len(MARKER):].strip()
    for prefix in (PREFIX + ":", PREFIX + "：", PREFIX):
        if body.startswith(prefix):
            body = body[len(prefix):].strip()
            break
    body = body.lstrip(":：,，、 ").strip()
    return body or None


def _split_pair(body: str) -> tuple[str, str] | None:
    """把 `问题~答案` 拆开；没有分隔符或一边为空就返回 None。"""
    text = (body or "").strip()
    for separator in PAIR_SEPARATORS:
        if separator in text:
            question, answer = text.split(separator, 1)
            question = question.strip()
            answer = answer.strip()
            if question and answer:
                return question, answer
            return None
    return None


def parse_pair(text: str) -> tuple[str, str] | None:
    """`#问题~答案` -> (问题, 答案)；不是成对写法返回 None。

    旧写法 `#一句话知识` 仍然有效（问题=答案），这里只负责识别新格式。
    """
    body = parse_marker(text)
    if body is None:
        return None
    return _split_pair(body)


def parse_correction(text: str) -> tuple[str, str] | None:
    """`#!问题~新答案` -> (问题, 新答案)；不是修正指令返回 None。"""
    item = (text or "").strip()
    for marker in CORRECTION_MARKERS:
        if not item.startswith(marker):
            continue
        body = item[len(marker):].strip()
        return _split_pair(body)
    return None


def is_pair_teaching(text: str) -> bool:
    return parse_pair(text) is not None


def is_yes(text: str) -> bool:
    return (text or "").strip().lower() in _YES


def next_id(entries: list[dict[str, Any]], counter: int = 0) -> str:
    """下一个条目 id:`user-0001` 起,不复用已删掉的号。

    `counter` 是包里的 `_next_user_id`(下一个要发的号);没有就按现有最大号 +1。
    """
    number = max(1, int(counter or 0))
    for entry in entries:
        match = _ID_RE.match(str(entry.get("id") or ""))
        if match:
            number = max(number, int(match.group(1)) + 1)
    return f"user-{number:04d}"


def build_entry(knowledge: str, keywords: tuple[str, ...] | list[str],
                entry_id: str, raw: str | None = None,
                secondary_keywords: tuple[str, ...] | list[str] = (),
                answer: str | None = None) -> dict[str, Any]:
    """一条要写进记忆数据包的条目:题面与答案可以分开,关键词当检索键。

    旧调用 `build_entry(知识, ...)` 仍然把知识同时当题面和答案；
    新写法 `build_entry(问题, ..., answer=答案)` 存成一对 Q&A。
    `raw` 是用户当时输入的那一行(含 `#`)。没有就按 `# 问题~答案` 拼一个，
    这样 `#?` 至少能回放出一个可复制的等价写法。
    """
    question = (knowledge or "").strip()
    answer_text = (answer if answer is not None else question).strip()
    if not answer_text:
        answer_text = question
    keys = [k for k in dict.fromkeys(str(k).strip() for k in keywords) if k]
    secondary = [
        k for k in dict.fromkeys(str(k).strip() for k in secondary_keywords)
        if k and k not in keys
    ]
    if raw:
        raw_line = raw.strip()
    elif answer_text != question:
        raw_line = f"{MARKER} {question}{PAIR_SEPARATORS[0]}{answer_text}"
    else:
        raw_line = f"{MARKER} {question}"
    entry = {
        "id": entry_id,
        "question": question,
        "raw": raw_line,
        "aliases": [],
        "keywords": keys,
        "secondary_keywords": secondary,
        "tags": [TAUGHT_TAG],
        "answer": answer_text,
        "points": ["来自用户教我的记忆(可继续用 # 补充,或直接改这个 pack.json)"],
        "see_also": [],
    }
    if answer_text != question:
        entry["qa"] = True
    return entry


def secondary_keywords(
    text: str,
    primary: tuple[str, ...] | list[str] = (),
    tokenizer: Any = None,
    limit: int = 8,
) -> list[str]:
    """从教的原话里抽**次要关键词**（权重低于主关键词，但能提高区分度）。

    主关键词来自拆句，会漏掉 `CORE_WORDS`（如“意思”）；这里把整句的所有
    内容词再扫一遍，补上主关键词之外的词。写入时由终端调用，结果存进
    `secondary_keywords`，核心检索时按较低权重计分。
    """
    text = (text or "").strip()
    if not text:
        return []
    if tokenizer is not None and hasattr(tokenizer, "tokenize"):
        tokens = list(tokenizer.tokenize(text))
    else:
        tokens = re.findall(r"[A-Za-z0-9_+.-]+|[\u4e00-\u9fff]{2,}", text)

    primary_keys = {str(item).strip() for item in primary if str(item).strip()}
    picked: list[str] = []
    for token in tokens:
        token = str(token).strip()
        if len(token) < 2 or token in primary_keys or token in _SECONDARY_SKIP:
            continue
        if token not in picked:
            picked.append(token)
        if len(picked) >= limit:
            break
    return picked


def _read(card: ExpertCard) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    data = json.loads(card.pack_path.read_text(encoding="utf-8"))
    entries = data.setdefault("entries", [])
    if not isinstance(entries, list):
        entries = []
        data["entries"] = entries
    return data, entries


def _touch_counter(data: dict[str, Any], entries: list[dict[str, Any]]) -> None:
    """把"下一个 id"记在包里:删掉最后一条后,新条目不会复用旧号。"""
    next_number = max(1, int(data.get("_next_user_id") or 0))
    for entry in entries:
        match = _ID_RE.match(str(entry.get("id") or ""))
        if match:
            next_number = max(next_number, int(match.group(1)) + 1)
    data["_next_user_id"] = next_number


def teach(card: ExpertCard, knowledge: str,
          keywords: tuple[str, ...] | list[str] = (),
          raw: str | None = None,
          secondary: tuple[str, ...] | list[str] = (),
          answer: str | None = None) -> tuple[dict[str, Any], bool]:
    """写进记忆数据包。返回 (条目, 是否新建)。

    同一道题再教一次：**更新答案、关键词与次要关键词**，不重复追加
    (`created=False`)；答案变了会记一条 `revisions`（CBR 的 revise）。
    原先没有 `raw` 的旧条目会顺手补上；修正时用新 raw 覆盖。
    写文件用"临时文件 + 原子替换",中途被杀不会留下半份 JSON。
    """
    question = (knowledge or "").strip()
    if not question:
        raise ValueError("没有要写入的内容")
    answer_text = (answer if answer is not None else question).strip()
    if not answer_text:
        raise ValueError("答案不能为空")
    path = card.pack_path
    data, entries = _read(card)

    for existing in entries:
        if str(existing.get("question") or "").strip() != question:
            continue
        previous_answer = str(existing.get("answer") or "").strip()
        existing["keywords"] = list(dict.fromkeys(
            [str(k) for k in (existing.get("keywords") or [])]
            + [str(k) for k in keywords]
        ))
        existing["secondary_keywords"] = list(dict.fromkeys(
            [str(k) for k in (existing.get("secondary_keywords") or [])]
            + [str(k) for k in secondary]
        ))
        if answer_text != previous_answer:
            revisions = existing.get("revisions")
            if not isinstance(revisions, list):
                revisions = []
            revisions.append({"previous_answer": previous_answer})
            existing["revisions"] = revisions[-5:]
            existing["answer"] = answer_text
            existing["qa"] = True
            existing["raw"] = (
                (raw or "").strip()
                if raw else f"{MARKER} {question}{PAIR_SEPARATORS[0]}{answer_text}"
            )
        elif raw and not str(existing.get("raw") or "").strip():
            existing["raw"] = (raw or "").strip()
        tags = existing.get("tags")
        if not isinstance(tags, list):
            tags = []
            existing["tags"] = tags
        if TAUGHT_TAG not in tags:
            tags.append(TAUGHT_TAG)
        _touch_counter(data, entries)
        _write(path, data)
        return existing, False

    counter = int(data.get("_next_user_id") or 0)
    entry = build_entry(
        question, keywords, next_id(entries, counter), raw=raw,
        secondary_keywords=secondary, answer=answer_text,
    )
    entries.append(entry)
    _touch_counter(data, entries)
    _write(path, data)
    return entry, True


def revise(card: ExpertCard, question: str, answer: str,
           keywords: tuple[str, ...] | list[str] = (),
           raw: str | None = None,
           secondary: tuple[str, ...] | list[str] = ()) -> tuple[dict[str, Any], str | None]:
    """CBR 的 revise：同题改答案；返回 (条目, 旧答案)。

    找不到同题条目时退回“新建”，旧答案返回 None —— 不假装修正了一条不存在的记忆。
    """
    question = (question or "").strip()
    if not question:
        raise ValueError("没有要修正的问题")
    answer = (answer or "").strip()
    if not answer:
        raise ValueError("修正后的答案不能为空")
    previous: str | None = None
    for entry in user_entries(card):
        if str(entry.get("question") or "").strip() == question:
            previous = str(entry.get("answer") or "").strip()
            break
    entry, _created = teach(
        card, question, keywords, raw=raw, secondary=secondary, answer=answer,
    )
    return entry, previous


def user_entries(card: ExpertCard) -> list[dict[str, Any]]:
    """只挑"人教的"条目(带 user-taught 标记,或 id 是 user-*)。"""
    out: list[dict[str, Any]] = []
    for entry in card.entries:
        tags = [str(t) for t in entry.get("tags") or ()]
        if TAUGHT_TAG in tags or _ID_RE.match(str(entry.get("id") or "")):
            out.append(entry)
    return out


def raw_of(entry: dict[str, Any]) -> str:
    """这一条知识当时的原话;旧条目没有 raw 就按 `# <知识>` 还原。"""
    raw = str(entry.get("raw") or "").strip()
    if raw:
        return raw
    question = str(entry.get("question") or "").strip()
    return f"{MARKER} {question}".strip()


def _match_key(text: str) -> str:
    return _WS_RE.sub("", (text or "").lower())


def find_taught(card: ExpertCard, query: str = "") -> list[dict[str, Any]]:
    """按原话/知识/关键词/条目 id 找教过的条目(空查询 = 全部)。

    先认 id 与完全相同的原话,再认子串;不拿答案正文乱匹配 —— 忘错东西比忘不掉更糟。
    """
    entries = user_entries(card)
    q = _match_key(query)
    if not q:
        return entries

    scored: list[tuple[int, str, dict[str, Any]]] = []
    for entry in entries:
        eid = str(entry.get("id") or "")
        raw = raw_of(entry)
        question = str(entry.get("question") or "")
        best = 0
        if _match_key(eid) == q:
            best = 100
        elif _match_key(raw) == q or _match_key(question) == q:
            best = 90
        elif q in _match_key(raw) or q in _match_key(question):
            best = 80
        else:
            for keyword in entry.get("keywords") or ():
                key = _match_key(str(keyword))
                if not key:
                    continue
                if key == q:
                    best = max(best, 60)
                    break
                if q in key:
                    best = max(best, 50)
        if best > 0:
            scored.append((best, eid, entry))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [entry for _score, _eid, entry in scored]


def render_taught(card: ExpertCard, query: str = "", limit: int | None = None) -> str:
    """把教过的知识**按原话**列出来(给 `#?` 用)。

    输出只做两件事:报数量、逐行回放用户当时输入的 `#……`;不做摘要、不重写。
    """
    matched = find_taught(card, query)
    total = len(user_entries(card))
    if not total:
        return "我还没记住你教的知识。用「#」开头发一条,我会先确认再写进去。"
    if not matched:
        return (
            f"没有找到和「{query.strip()}」对得上的记忆。"
            f"现在记着 {total} 条;用 #? 看全部。"
        )

    shown = matched if limit is None else matched[:limit]
    lines = [f"我现在记着 {len(matched)} 条你教的知识(下面是你当时的原话):", ""]
    lines.extend(raw_of(entry) for entry in shown)
    if limit is not None and len(matched) > limit:
        lines.append(f"……还有 {len(matched) - limit} 条,去掉过滤词看全部。")
    lines.extend([
        "",
        "要忘掉哪条:用「#-<那句话里的一小段>」;只看一部分:用「#?<关键词>」。",
    ])
    return "\n".join(lines)


def forget(card: ExpertCard, query: str) -> list[dict[str, Any]]:
    """删掉和 `query` 对得上的教过的条目;返回真正删掉的条目(可能为空)。

    空查询不删任何东西 —— "忘掉一切"必须由人明说(目前没有那个指令)。
    """
    query = (query or "").strip()
    if not query:
        return []
    matches = find_taught(card, query)
    if not matches:
        return []
    ids = {str(entry.get("id") or "") for entry in matches}
    path = card.pack_path
    data, entries = _read(card)
    kept: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    for entry in entries:
        if str(entry.get("id") or "") in ids:
            removed.append(entry)
        else:
            kept.append(entry)
    if not removed:
        return []
    data["entries"] = kept
    _touch_counter(data, kept)
    _write(path, data)
    return removed


def _write(path, data: dict[str, Any]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        # 写一半失败(编码错、磁盘满)时别把 .tmp 留在数据包目录里
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


__all__ = [
    "MARKER",
    "FORGET_MARKER",
    "VIEW_MARKERS",
    "PREFIX",
    "TAUGHT_TAG",
    "CORRECTION_MARKERS",
    "PAIR_SEPARATORS",
    "parse_marker",
    "parse_pair",
    "parse_correction",
    "is_pair_teaching",
    "parse_forget",
    "parse_view",
    "is_view_command",
    "is_yes",
    "next_id",
    "build_entry",
    "secondary_keywords",
    "teach",
    "revise",
    "user_entries",
    "raw_of",
    "find_taught",
    "render_taught",
    "forget",
]

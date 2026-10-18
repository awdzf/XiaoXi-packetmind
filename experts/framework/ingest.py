"""把用户自己的 Markdown / 纯文本笔记或 Q&A CSV 变成一个**可核对的数据包**（零依赖）。

思路借自 RAGless 的 "smart chunking"（先量令牌再决定切不切）与 QnA-Chatbot
的 "每行一条问答" 数据格式：

    · **先量再切**：整篇没超过令牌预算就整篇当一个块，绝不为了切而切；
    · 超过了才按 Markdown 标题 → 段落 → 句子的顺序切，块与块之间留重叠；
    · 每块都是原文（answer 就是源文本），并留下 `source_file` / `source_heading`
      / `source_quote`，回答时能倒查回哪份文件的哪一段；
    · 每块的 id 由「文件名 + 标题 + 块号 + 内容前缀」哈希得到 —— 同一份文件
      重复导入得到同一批 id，不会悄悄堆出重复条目（RAGless 的幂等思路）。

**和小析的底线对齐**（这也是和 RAGless 不同的地方）：

    · 没有模型、没有嵌入、没有向量库：只用标准库；
    · 没有第三方分词器：令牌数是**本地保守估算**（CJK 1 字≈1 token，
      ASCII 4 字符≈1 token），并把估算方式写进 `_import.estimator`，
      宁可早切一点，不让单块大到拖慢小设备；
    · 不生成、不改写：块内文字就是原文；问题/别名/关键词是从标题与正文里
      **抽**出来的检索键，抽得不好可以直接改 pack.json；
    · 个人化：这就是"把你自己的笔记装进小析"的入口，所有东西留在本地。
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .decomposer import CORE_WORDS, PARTICLES, PRONOUNS

#: 整篇不超过这个估算令牌数 -> 不分块（对应 RAGless 的 MAX_TOKENS_DOC）
MAX_TOKENS_WHOLE = 1_200
#: 超过之后再切时，单块的目标上限
CHUNK_TOKENS = 900
#: 相邻块的重叠（估算令牌数）
OVERLAP_TOKENS = 120
#: 单个输入文件的上限：2MB。再大请先拆分（小设备上不跟内存较劲）
MAX_FILE_BYTES = 2_000_000
#: 支持的扩展名（PDF 需要第三方库，明确不支持）
SUPPORTED_SUFFIXES = (".md", ".markdown", ".txt")
#: CSV 每行一条 Q&A 的上限（小设备不一次吞太大）
MAX_CSV_ROWS = 5_000

_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]{2,}")
_CJK_CHAR = re.compile(r"[\u4e00-\u9fff]")
_ASCII_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_+.-]{1,24}")
_SENTENCE_END = re.compile(r"[。！？!?；;]\s*")

_ASCII_STOP: frozenset[str] = frozenset({
    "a", "an", "the", "of", "to", "in", "on", "for", "and", "or", "is", "are",
    "was", "were", "be", "been", "by", "with", "as", "at", "from", "this",
    "that", "these", "those", "it", "its", "we", "you", "they", "he", "she",
    "i", "not", "no", "do", "does", "did", "can", "will", "would", "should",
    "could", "may", "might", "must", "has", "have", "had", "per", "con", "del",
    "della", "dello", "dei", "delle", "di", "da", "su", "un", "una", "il",
    "lo", "la", "gli", "le", "che", "non", "tra", "fra", "si",
    "what", "who", "whom", "whose", "how", "why", "when", "where", "which",
    "if", "then", "than", "so", "very", "just", "much", "many", "some", "any",
    "all", "our", "your", "their", "my", "me", "him", "her", "us", "them",
    "also", "about", "into", "over", "after", "before", "between", "during",
    "without", "within", "because", "while", "although", "though", "even",
    "only", "own", "same", "other", "such", "more", "most", "less", "least",
    "one", "two", "three", "yes", "ok", "okay", "please",
})

#: 不该当关键词的常见两字组合（功能词 + 泛问词）
_BIGRAM_STOP: frozenset[str] = frozenset(
    set(PARTICLES) | set(CORE_WORDS) | set(PRONOUNS) | {
        "我们", "你们", "他们", "她们", "它们", "这个", "那个", "这些", "那些",
        "可以", "以及", "如果", "因为", "所以", "但是", "而且", "或者", "没有",
        "就是", "不是", "需要", "使用", "时候", "问题", "方法", "内容", "部分",
        "其中", "然后", "这样", "那样", "一个", "进行", "通过", "用于", "对于",
        "关于", "根据", "由于", "因此", "并且", "同时", "已经", "正在", "可能",
        "应该", "必须", "能够", "得到", "提供", "支持", "包括", "例如", "比如",
        "注意", "重要", "主要", "一般", "通常", "常见", "相关", "如下", "以上",
        "以下", "第一", "第二", "第三", "这里", "那里", "什么", "怎么", "如何",
        "为什么", "是否", "哪些", "哪个", "多少", "一种", "一些", "这个", "其",
    }
)


@dataclass
class Section:
    heading: str
    body: str
    level: int = 0


@dataclass
class Chunk:
    heading: str
    text: str
    index: int
    tokens: int
    part: int = 0          # 同一个标题下的第几块（0 = 第一块）
    level: int = 0


@dataclass
class ImportResult:
    pack: dict[str, Any]
    chunks: int
    entries: int
    tokens: int
    bytes: int
    sha1: str
    source: str
    kind: str = "text"      # text = .md/.txt 智能分块;csv = 每行一条 Q&A


# ---------------------------------------------------------------------------
# 令牌估算：没有模型分词器时的保守替代
# ---------------------------------------------------------------------------
def estimate_tokens(text: str) -> int:
    """估算一段文本的令牌数（**不是**模型的真实分词器）。

    口径（可核对、可复现）：
      · 每个 CJK 汉字算 1 token；
      · ASCII 字母/数字每 4 个字符算 1 token（向上取整）；
      · 空白与标点不计。

    对中文偏保守（真实分词器常见 1 字 ≈ 1–2 token），所以会**早一点切**，
    不会让单块超过小设备能舒服处理的量。`_import.estimator` 会写明这个口径。
    """
    if not text:
        return 0
    cjk = len(_CJK_CHAR.findall(text))
    ascii_chars = sum(len(word) for word in _ASCII_WORD.findall(text))
    return max(1, cjk + math.ceil(ascii_chars / 4))


# ---------------------------------------------------------------------------
# 读文件 / 前置元数据
# ---------------------------------------------------------------------------
def read_document(path: str | Path) -> str:
    """读 .md / .markdown / .txt（UTF-8，容忍 BOM 与少量坏字节）。"""
    file = Path(path)
    suffix = file.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError(
            f"不支持的文件类型:{file.suffix or '(无扩展名)'};只支持 "
            + " / ".join(SUPPORTED_SUFFIXES) + "（PDF 需要第三方库,本工具不装）"
        )
    size = file.stat().st_size
    if size > MAX_FILE_BYTES:
        raise ValueError(
            f"文件太大:{size / 1_000_000:.1f}MB(上限 {MAX_FILE_BYTES / 1_000_000:.0f}MB);"
            "先拆成几份再导入。"
        )
    return file.read_text(encoding="utf-8-sig", errors="replace")


def split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """读可选的简单前置元数据（不引入 YAML 依赖）。

    支持：

        ---
        title: 我的部署笔记
        keywords: 部署, 回滚, 灰度
        ---

    只认 `key: value` 单行；解析失败就当作正文，不抛异常。
    """
    lines = (text or "").splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    for index in range(1, min(len(lines), 40)):
        if lines[index].strip() in ("---", "..."):
            meta: dict[str, str] = {}
            for line in lines[1:index]:
                if ":" not in line:
                    continue
                key, value = line.split(":", 1)
                key = key.strip().lower()
                value = value.strip().strip("'\"")
                if key and value:
                    meta[key] = value
            return meta, "\n".join(lines[index + 1:])
    return {}, text


# ---------------------------------------------------------------------------
# 切分：先量，再决定切不切
# ---------------------------------------------------------------------------
def parse_sections(text: str) -> list[Section]:
    """按 Markdown 标题切段（没有标题就整篇一段）。"""
    sections: list[Section] = []
    heading, level, buf = "", 0, []

    def flush() -> None:
        body = "\n".join(buf).strip()
        if body:
            sections.append(Section(heading=heading, body=body, level=level))

    for line in (text or "").splitlines():
        match = _HEADING_RE.match(line)
        if match:
            flush()
            heading = match.group(2).strip()
            level = len(match.group(1))
            buf = []
        else:
            buf.append(line)
    flush()
    if not sections and text.strip():
        sections.append(Section(heading="", body=text.strip()))
    return sections


def _sentences(text: str) -> list[str]:
    pieces: list[str] = []
    start = 0
    for match in _SENTENCE_END.finditer(text):
        piece = text[start:match.end()].strip()
        if piece:
            pieces.append(piece)
        start = match.end()
    tail = text[start:].strip()
    if tail:
        pieces.append(tail)
    return pieces or [text.strip()]


def _paragraphs(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]


def _units(body: str, budget: int) -> list[str]:
    """把一段正文拆成"不超过预算"的切分单元：段落 → 句子 → 字符窗。"""
    units: list[str] = []
    for paragraph in _paragraphs(body):
        if estimate_tokens(paragraph) <= budget:
            units.append(paragraph)
            continue
        for sentence in _sentences(paragraph):
            if estimate_tokens(sentence) <= budget:
                units.append(sentence)
                continue
            # 极端情况：一句话本身就超预算（代码/长串）。按字符窗硬切，保证能前进。
            # 纯 ASCII 按 4 字符≈1 token 放宽,中文仍按 1 字≈1 token,不超预算。
            ratio = 1 if _CJK_CHAR.search(sentence) else 4
            window = max(40, budget * ratio)
            for start in range(0, len(sentence), window):
                piece = sentence[start:start + window].strip()
                if piece:
                    units.append(piece)
    return units


def _tail(units: list[str], overlap: int) -> list[str]:
    """从尾部取不超过 overlap 令牌的单元，作为下一块的重叠。"""
    if overlap <= 0:
        return []
    tail: list[str] = []
    total = 0
    for unit in reversed(units):
        cost = estimate_tokens(unit)
        if tail and total + cost > overlap:
            break
        tail.insert(0, unit)
        total += cost
    return tail


def _chunk_units(units: list[str], budget: int, overlap: int) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    for unit in units:
        if not current:
            current = [unit]
            continue
        candidate = "\n\n".join(current + [unit])
        if estimate_tokens(candidate) <= budget:
            current.append(unit)
            continue
        chunks.append("\n\n".join(current))
        tail = _tail(current, overlap)
        nxt = tail + [unit]
        # 重叠本身也可能把新块顶超预算:这时宁可丢掉重叠,先守住块上限
        if estimate_tokens("\n\n".join(nxt)) > budget:
            nxt = [unit]
        current = nxt
    if current:
        chunks.append("\n\n".join(current))
    return [chunk for chunk in chunks if chunk.strip()]


def _section_chunks(section: Section, budget: int, overlap: int) -> list[str]:
    body = section.body.strip()
    if not body:
        return []
    if estimate_tokens(body) <= budget:
        return [body]
    return _chunk_units(_units(body, budget), budget, overlap)


def smart_chunks(
    text: str,
    *,
    max_tokens_whole: int = MAX_TOKENS_WHOLE,
    chunk_tokens: int = CHUNK_TOKENS,
    overlap: int = OVERLAP_TOKENS,
) -> list[Chunk]:
    """智能分块：整篇没超预算就不切；超了才按标题/段落/句子切，并带重叠。"""
    _meta, body = split_frontmatter(text)
    sections = parse_sections(body)
    total = estimate_tokens(body)

    if total <= max_tokens_whole:
        # 短文档:正文不切。但 Markdown 标题本身就是天然的条目边界 ——
        # 有多个标题就按标题分条(每条正文仍是完整的一整段),不是"为了切而切"。
        if len(sections) > 1:
            chunks = []
            for section in sections:
                body_text = section.body.strip()
                if not body_text:
                    continue
                chunks.append(
                    Chunk(
                        heading=section.heading,
                        text=body_text,
                        index=len(chunks),
                        tokens=estimate_tokens(body_text),
                        part=0,
                        level=section.level,
                    )
                )
            return chunks
        heading = sections[0].heading if sections else ""
        level = sections[0].level if sections else 0
        return [Chunk(heading=heading, text=body.strip(), index=0,
                      tokens=total, part=0, level=level)] if body.strip() else []

    chunks: list[Chunk] = []
    for section in sections:
        parts = _section_chunks(section, chunk_tokens, overlap)
        for part_index, part_text in enumerate(parts):
            chunks.append(
                Chunk(
                    heading=section.heading,
                    text=part_text,
                    index=len(chunks),
                    tokens=estimate_tokens(part_text),
                    part=part_index,
                    level=section.level,
                )
            )
    if not chunks and body.strip():
        chunks.append(Chunk(heading="", text=body.strip(), index=0,
                            tokens=total, level=0))
    return chunks


# ---------------------------------------------------------------------------
# 从块到条目：问题 / 别名 / 关键词 / 来源 / 稳定 id
# ---------------------------------------------------------------------------
def _is_question(text: str) -> bool:
    item = (text or "").strip()
    if item.endswith(("?", "？")):
        return True
    return item.startswith(("什么是", "怎么", "怎样", "如何", "为什么", "为何", "哪个", "哪些"))


def _first_sentence(text: str, limit: int = 60) -> str:
    for sentence in _sentences(text):
        sentence = sentence.strip()
        if not sentence:
            continue
        sentence = sentence.rstrip("。！？!?；;").strip()
        if len(sentence) > limit:
            sentence = sentence[: limit - 1] + "…"
        return sentence
    return ""


def _ascii_terms(text: str) -> list[str]:
    out: list[str] = []
    for word in _ASCII_WORD.findall(text or ""):
        word = word.lower().strip(".-")
        if len(word) >= 2 and word not in _ASCII_STOP and word not in _BIGRAM_STOP and word not in out:
            out.append(word)
    return out


def _ascii_term_iter(text: str):
    for word in _ASCII_WORD.findall(text or ""):
        word = word.lower().strip(".-")
        if len(word) >= 2 and word not in _ASCII_STOP and word not in _BIGRAM_STOP:
            yield word


def _cjk_bigram_iter(text: str):
    for run in _CJK_RUN.findall(text or ""):
        for index in range(len(run) - 1):
            bigram = run[index:index + 2]
            if bigram not in _BIGRAM_STOP:
                yield bigram


def _cjk_bigrams(text: str) -> list[str]:
    return list(dict.fromkeys(_cjk_bigram_iter(text)))


def keywords_from(heading: str, body: str, limit: int = 10) -> list[str]:
    """抽检索键：标题词优先，正文按出现频率补；全是两字组合（中文无需分词）。

    先放标题整体（短标题最有用），再放标题里的两字组合/英文词；
    正文只补高频词：先要出现 ≥2 次的，不够再拿出现 1 次的凑数。
    """
    heading = (heading or "").strip()
    picked: list[str] = []

    def add(term: str) -> None:
        term = term.strip()
        if not term or len(term) < 2 or term in _BIGRAM_STOP:
            return
        if term not in picked:
            picked.append(term)

    if 2 <= len(heading) <= 12 and _CJK_CHAR.search(heading):
        add(heading)
    for term in _ascii_terms(heading) + _cjk_bigrams(heading):
        add(term)

    counts = Counter(list(_cjk_bigram_iter(body)) + list(_ascii_term_iter(body)))
    for minimum in (2, 1):
        for term, count in counts.most_common():
            if len(picked) >= limit:
                break
            if count >= minimum:
                add(term)
    if not picked:
        add(heading)
    return picked[:limit]


def stable_id(source_name: str, heading: str, index: int, text: str) -> str:
    """确定性 id：同一文件、同一段内容重复导入得到同一个 id（RAGless 的幂等思路）。"""
    digest = hashlib.sha1(
        f"{source_name}|{heading}|{index}|{text[:200]}".encode("utf-8")
    ).hexdigest()
    return "doc-" + digest[:12]


def _unique_question(base: str, used: set[str], fallback: str) -> str:
    question = (base or "").strip() or fallback
    if question not in used:
        used.add(question)
        return question
    number = 2
    while f"{question}（{number}）" in used:
        number += 1
    unique = f"{question}（{number}）"
    used.add(unique)
    return unique


def build_entries(chunks: list[Chunk], source_name: str, stem: str) -> list[dict[str, Any]]:
    """把块变成数据包条目：原文当 answer，标题/首句当 question，标题+正文当关键词。"""
    entries: list[dict[str, Any]] = []
    used_keys: set[str] = set()          # question 与 alias 共用，保证全包唯一
    heading_used: set[str] = set()

    for chunk in chunks:
        heading = chunk.heading.strip()
        first_of_heading = heading not in heading_used

        if heading and first_of_heading and len(heading) <= 40:
            question = heading if _is_question(heading) else f"{heading}是什么?"
            aliases: list[str] = []
            for alias in (heading, f"什么是{heading}", f"{heading}是什么"):
                alias = alias.strip()
                if alias and alias != question and alias not in used_keys:
                    aliases.append(alias)
                    used_keys.add(alias)
        else:
            question = _first_sentence(chunk.text)
            if len(question) < 4:
                question = f"{stem} 第{chunk.index + 1}段"
            aliases = []

        question = _unique_question(question, used_keys, f"{stem} 第{chunk.index + 1}段")
        if heading:
            heading_used.add(heading)

        keywords = keywords_from(heading, chunk.text)
        if not keywords:
            keywords = [question[:24]] if len(question) >= 2 else [stem]

        entry: dict[str, Any] = {
            "id": stable_id(source_name, heading, chunk.index, chunk.text),
            "question": question,
            "aliases": aliases,
            "keywords": keywords,
            "tags": ["imported"],
            "answer": chunk.text.strip(),
            "points": [],
            "see_also": [],
            "source_file": source_name,
            "source_heading": heading or None,
            "source_quote": _first_sentence(chunk.text, limit=200),
            "source_chunk": chunk.index,
            "tokens_est": chunk.tokens,
        }
        if entry["source_heading"] is None:
            entry.pop("source_heading")
        entries.append(entry)

    return entries


def build_scope(entries: list[dict[str, Any]], title: str, stem: str) -> list[str]:
    """数据包的领域词：标题 + 各条目标题/关键词里挑，去重、限 30。"""
    scope: list[str] = []

    def add(term: str) -> None:
        term = str(term or "").strip()
        if len(term) < 2 or len(term) > 40 or term in _BIGRAM_STOP or term in scope:
            return
        scope.append(term)

    add(title)
    add(stem)
    for entry in entries:
        add(str(entry.get("source_heading") or ""))
        question = str(entry.get("question") or "")
        if len(question) <= 40:
            add(question)
        for keyword in entry.get("keywords") or ():
            add(str(keyword))
        if len(scope) >= 30:
            break
    if not scope:
        add("笔记")
    return scope[:30]


# ---------------------------------------------------------------------------
# CSV：每行一条 Q&A（QnA-Chatbot 的数据格式）
# ---------------------------------------------------------------------------
_CSV_QUESTION_KEYS = ("pattern", "question", "q", "ask")
_CSV_ANSWER_KEYS = ("response", "answer", "a", "reply")


def _first_value(row: dict[str, str], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def read_csv_pairs(path: str | Path) -> list[dict[str, Any]]:
    """读 Q&A CSV：支持 `pattern,response`、`question,answer` 两种表头。

    没有表头时按"前两列 = 问题,答案"读；`category` / `aliases` 列可选。
    只读标准库 `csv`，不引入 pandas。
    """
    file = Path(path)
    size = file.stat().st_size
    if size > MAX_FILE_BYTES:
        raise ValueError(
            f"CSV 太大:{size / 1_000_000:.1f}MB(上限 {MAX_FILE_BYTES / 1_000_000:.0f}MB);"
            "先拆成几份再导入。"
        )

    rows: list[dict[str, Any]] = []
    with file.open("r", encoding="utf-8-sig", newline="") as handle:
        sample = handle.read(8192)
        handle.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel

        reader = csv.DictReader(handle, dialect=dialect)
        headers = [str(name or "").strip().lower() for name in (reader.fieldnames or [])]
        has_header = (
            any(key in headers for key in _CSV_QUESTION_KEYS)
            and any(key in headers for key in _CSV_ANSWER_KEYS)
        )

        if has_header:
            for index, raw_row in enumerate(reader):
                if len(rows) >= MAX_CSV_ROWS:
                    break
                row = {str(k or "").strip().lower(): str(v or "") for k, v in raw_row.items()}
                question = _first_value(row, _CSV_QUESTION_KEYS)
                answer = _first_value(row, _CSV_ANSWER_KEYS)
                if not question or not answer:
                    continue
                aliases = [
                    item.strip()
                    for item in re.split(r"[|;；]", row.get("aliases", ""))
                    if item.strip()
                ]
                rows.append({
                    "question": question,
                    "answer": answer,
                    "category": row.get("category", "").strip(),
                    "aliases": aliases,
                    "row": index + 2,
                })
            return rows

        # 无表头（或表头不认识）：按前两列读
        handle.seek(0)
        plain = csv.reader(handle, dialect=dialect)
        for index, raw_row in enumerate(plain):
            if len(rows) >= MAX_CSV_ROWS:
                break
            if len(raw_row) < 2:
                continue
            question = str(raw_row[0]).strip()
            answer = str(raw_row[1]).strip()
            if index == 0:
                first_q = question.lower()
                first_a = answer.lower()
                if first_q in _CSV_QUESTION_KEYS or first_a in _CSV_ANSWER_KEYS:
                    continue
            if question and answer:
                rows.append({
                    "question": question,
                    "answer": answer,
                    "category": "",
                    "aliases": [],
                    "row": index + 1,
                })
    return rows


def build_csv_pack(
    rows: list[dict[str, Any]],
    *,
    source_name: str,
    name: str,
    title: str,
    safety: dict[str, Any] | None = None,
    source_bytes: int = 0,
) -> ImportResult:
    """把 Q&A 行变成数据包：每行一条，答案原文，问题/关键词做检索键。"""
    entries: list[dict[str, Any]] = []
    used: set[str] = set()
    stem = Path(source_name).stem
    total_tokens = 0

    for row in rows:
        question = str(row.get("question") or "").strip()
        answer = str(row.get("answer") or "").strip()
        if not question or not answer:
            continue
        row_number = int(row.get("row") or 0)
        unique = _unique_question(question, used, f"{stem} 第{row_number or len(entries) + 1}条")
        aliases: list[str] = []
        for alias in row.get("aliases") or ():
            alias = str(alias).strip()
            if alias and alias != unique and alias not in used:
                aliases.append(alias)
                used.add(alias)
        category = str(row.get("category") or "").strip()
        # Q-Q 匹配：检索键只从**问题**里抽，不从答案里抽。
        # 答案里常有"work / help / time"这类词，拿来当关键词会把
        # "how does a chatbot work" 拉到一条讲 work 的答案上（实测踩到）。
        keywords = keywords_from(category, question, limit=10)
        if not keywords:
            keywords = [question[:24]] if len(question) >= 2 else [stem]

        entry: dict[str, Any] = {
            "id": stable_id(source_name, f"row-{row_number}", row_number, f"{question}|{answer}"),
            "question": unique,
            "aliases": aliases,
            "keywords": keywords,
            "tags": ["imported", "csv"],
            "answer": answer,
            "points": [],
            "see_also": [],
            "source_file": source_name,
            "source_row": row_number,
            "source_quote": question,
        }
        if category:
            entry["source_category"] = category
        entries.append(entry)
        total_tokens += estimate_tokens(question) + estimate_tokens(answer)

    if not entries:
        raise ValueError("CSV 里没有可导入的 Q&A 行（需要问题与答案两列且非空）。")

    scope = build_scope(entries, title, stem)
    payload = [f"{row.get('question', '')}\t{row.get('answer', '')}" for row in rows]
    pack: dict[str, Any] = {
        "_readme": [
            "这个数据包是**导入的 Q&A CSV**（experts/framework/ingest.py）：",
            "每行一条:question = pattern/question 列,answer = response/answer 列。",
            "关键词是从问题与答案里抽的检索键;重复导入同一份 CSV 会得到同一批 id。",
            "没有模型参与,不调用 pandas / 向量库;可手改这个文件。",
        ],
        "expert": {
            "id": name,
            "name": title,
            "tagline": f"从 {source_name} 导入的 Q&A:每行一条,答案原文。",
            "order": 100,
            "scope": scope,
            "limits": [
                "只答 CSV 里写过的问答;没写到的如实说没有",
                "不改写答案、不做推理、不联网",
                "关键词是本地规则抽取的,不保证覆盖所有问法;可手改 pack.json",
            ],
        },
        "_import": {
            "source": source_name,
            "format": "csv",
            "sha1": hashlib.sha1("\n".join(payload).encode("utf-8")).hexdigest(),
            "bytes": source_bytes,
            "rows": len(entries),
            "tokens_est": total_tokens,
            "estimator": "cjk_char=1, ascii_chars/4; 标点与空白不计",
        },
        "entries": entries,
    }
    if safety:
        pack["safety"] = safety
    return ImportResult(
        pack=pack,
        chunks=0,
        entries=len(entries),
        tokens=total_tokens,
        bytes=source_bytes,
        sha1=pack["_import"]["sha1"],
        source=source_name,
        kind="csv",
    )


def build_pack(
    text: str,
    *,
    source_name: str,
    name: str,
    title: str,
    safety: dict[str, Any] | None = None,
    max_tokens_whole: int = MAX_TOKENS_WHOLE,
    chunk_tokens: int = CHUNK_TOKENS,
    overlap: int = OVERLAP_TOKENS,
) -> ImportResult:
    """完整导入：读文本 → 智能分块 → 条目 → pack.json 的 dict。"""
    meta, body = split_frontmatter(text)
    display_title = (meta.get("title") or title or Path(source_name).stem).strip()
    stem = Path(source_name).stem
    chunks = smart_chunks(
        body,
        max_tokens_whole=max_tokens_whole,
        chunk_tokens=chunk_tokens,
        overlap=overlap,
    )
    entries = build_entries(chunks, source_name, stem)
    if not entries:
        raise ValueError("文档里没有可导入的正文（全是空白或只有标题）。")

    # 用户可以在前置元数据里补关键词：keywords: 部署, 回滚
    extra_keywords = [
        item.strip() for item in re.split(r"[,，、\s]+", meta.get("keywords", ""))
        if len(item.strip()) >= 2
    ]
    for entry in entries:
        merged = list(dict.fromkeys(extra_keywords + list(entry.get("keywords") or ())))
        entry["keywords"] = merged[:16]

    scope = build_scope(entries, display_title, stem)
    for keyword in extra_keywords:
        if len(scope) < 30 and keyword not in scope and len(keyword) >= 2:
            scope.append(keyword)

    pack: dict[str, Any] = {
        "_readme": [
            "这个数据包是**导入的笔记**（experts/framework/ingest.py）：",
            "每条的 answer 是原文，question/aliases/keywords 是从标题与正文抽出来的检索键。",
            "抽得不合适就手改这个文件；重复导入同一份文件会得到同一批 id（幂等）。",
            "没有模型参与：分块与关键词都是本地规则，令牌数是保守估算（见 _import）。",
        ],
        "expert": {
            "id": name,
            "name": display_title,
            "tagline": f"从 {source_name} 导入的笔记:每块是原文,问题来自标题/首句。",
            "order": 100,
            "scope": scope,
            "limits": [
                "只答导入文件里写过的内容;没写到的如实说没有",
                "不做推理、不改写原文、不联网",
                "分块与关键词是本地规则,不保证覆盖所有问法;可手改 pack.json",
            ],
        },
        "_import": {
            "source": source_name,
            "sha1": hashlib.sha1(text.encode("utf-8")).hexdigest(),
            "bytes": len(text.encode("utf-8")),
            "chunks": len(chunks),
            "tokens_est": sum(chunk.tokens for chunk in chunks),
            "estimator": "cjk_char=1, ascii_chars/4; 标点与空白不计",
            "max_tokens_whole": max_tokens_whole,
            "chunk_tokens": chunk_tokens,
            "overlap_tokens": overlap,
        },
        "entries": entries,
    }
    if safety:
        pack["safety"] = safety
    return ImportResult(
        pack=pack,
        chunks=len(chunks),
        entries=len(entries),
        tokens=sum(chunk.tokens for chunk in chunks),
        bytes=len(text.encode("utf-8")),
        sha1=pack["_import"]["sha1"],
        source=source_name,
    )


# ---------------------------------------------------------------------------
# 落盘
# ---------------------------------------------------------------------------
def write_pack(pack_dir: str | Path, pack: dict[str, Any]) -> Path:
    """原子写入 `pack.json`；返回写出的路径。"""
    target = Path(pack_dir)
    target.mkdir(parents=True, exist_ok=True)
    path = target / "pack.json"
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(pack, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return path


__all__ = [
    "MAX_TOKENS_WHOLE",
    "CHUNK_TOKENS",
    "OVERLAP_TOKENS",
    "MAX_FILE_BYTES",
    "SUPPORTED_SUFFIXES",
    "MAX_CSV_ROWS",
    "Section",
    "Chunk",
    "ImportResult",
    "estimate_tokens",
    "read_document",
    "split_frontmatter",
    "parse_sections",
    "smart_chunks",
    "keywords_from",
    "stable_id",
    "build_entries",
    "build_scope",
    "read_csv_pairs",
    "build_csv_pack",
    "build_pack",
    "write_pack",
]

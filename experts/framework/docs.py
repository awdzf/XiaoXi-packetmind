"""文档模式:这一轮**只读一份文档**,并且只引用它的原文。

为什么单独一层:小析原有的知识是"数据包"(离线快照),而用户手里常常还有一份
正在看的文档 —— 需求说明、部署手册、合同、笔记。让他们把那句话贴进数据包里
太重了;他们真正想说的是"只按这份文档回答我"。

约定(全部可核对、可复现,不联网):

    问题@文档名            在 <项目根>/docs/ 里找这份文档(可省扩展名、大小写不敏感、
                           可带子目录,如 问题@手册、问题@team/周报)
    问题@@相对路径         按 **xiaoxi.py 所在目录**的相对路径取,如 问题@@docs/手册.md
    @文档名 / @@相对路径   只给文档不给问题 -> 列出它的章节(目录)

几条硬边界:

  · **只读项目目录里的文件**:`@@` 的 `..` 不许跑出项目根,也不接受绝对路径 ——
    一个"问答程序"没有理由去读项目之外的任意文件;
  · 只支持 `.md` / `.markdown` / `.txt`(与 `ingest.py` 同一份口径,不装 PDF 库);
  · **只引用、不改写**:回答里的每一段都是文档原文,并带上章节与行号;
  · 找不到就如实说没找到,并列出这份文档有哪些章节 —— 不拿数据包的知识补,
    也不猜"你大概想问哪一段";
  · 读进来的内容还要过一遍安全底线:命中红线的段落不引用,并如实说明有几段被挡。

切分复用 `ingest.smart_chunks`(先量令牌,短文档不切;超过才按标题/段落/句子切),
所以"文档模式"和"把笔记导入成数据包"看到的是同一种切法,不会出现两套口径。
"""

from __future__ import annotations

import datetime
import hashlib
import math
import os
import re
from dataclasses import dataclass, replace
from pathlib import Path

from . import ingest
from . import safety
from .decomposer import normalize
from .fuzzy import similarity
from .retrieval import BM25Index
from .tfidf import tokenize

#: 这一轮回答的"来源名"(与数据包同形,便于上层统一记账)
ROLE = "docs"
SOURCE_NOUN = "文档"

#: 项目根 = xiaoxi.py 所在目录(`experts/framework/docs.py` 往上三层)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DOCS_DIRNAME = "docs"
DEFAULT_DOCS_ROOT = PROJECT_ROOT / DOCS_DIRNAME

#: 与 `ingest.py` 同一份口径:PDF 需要第三方库,明确不支持
SUPPORTED_SUFFIXES = ingest.SUPPORTED_SUFFIXES
#: 一次最多扫多少个文件(文档目录不该像代码仓库那样大)
MAX_SCAN_FILES = 500
#: 最多缓存几份文档的切块与索引(按插入顺序淘汰;文档模式一次只读一份,够用)
MAX_CACHED_DOCS = 16
#: 一次最多引用几段
MAX_HITS = 3
#: 一次最多引用多少字符(超了就在句子边界截断,并说清楚还有多少段没引)
MAX_QUOTE_CHARS = 1600
#: 命中的最低"查询覆盖":词项重合太少就当作没找到
MIN_COVERAGE = 0.34
#: 找不到文档时,相似度多高才值得提示"是不是想问"
SUGGEST_THRESHOLD = 0.45


# ---------------------------------------------------------------------------
# 引用语法
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DocRef:
    """用户写在句尾的那一段引用。"""

    kind: str          # "name"(在 docs/ 里找)或 "path"(相对 xiaoxi.py)
    text: str          # 用户写的文档名 / 相对路径
    marker: str = "@"  # 原样保留写法,回答时能说清"你是用哪种写法指的"

    @property
    def is_path(self) -> bool:
        return self.kind == "path"


@dataclass(frozen=True)
class DocError:
    """解析/读取失败。每个 code 对应一种"说清楚为什么不行"的口径。

    它是**值对象**(放进 `DocAnswer.error` 交给渲染),不是异常;
    真正要抛出去时用 `DocReadError` 包一层(见下)。
    """

    code: str
    message: str
    suggestions: tuple[str, ...] = ()


class DocReadError(Exception):
    """内部用:读盘失败时抛出,带一个 `DocError` 说明(上层转成回答里的错误)。"""

    def __init__(self, error: DocError) -> None:
        super().__init__(error.message)
        self.error = error


#: 标记前面要是这些字符,说明 `@` 是单词的一部分(邮箱、用户名),不是引用
_GLUED = re.compile(r"[A-Za-z0-9._%+\-]")

def parse_reference(text: str) -> tuple[str, DocRef | None]:
    """把「问题@文档名 / 问题@@相对路径」拆成 (问题, 引用)。

    只认**句尾**最后一个 `@` 串:

      · 连续的 `@` 只有 1 个或 2 个才算标记,3 个以上按普通文字处理;
      · 标记后面必须写东西(空的话说明用户就在说 `@` 这个符号,不是引用);
      · 标记`@`前面不能是英文字母/数字/`.`/`-`/`_`/`+`/`%` —— 那是邮箱或用户名
        (`a@b.com` 不会被当成"引用 b.com");中文后面、空格后面、句首才算标记;
      · 引用**之前**的部分才是问题;问题为空就是"只看目录"。

    所以「邮箱是 a@b.com 吗」不会被误拆,而「怎么回滚@手册」会被拆成
    (怎么回滚, 手册)。
    """
    raw = (text or "").strip()
    if "@" not in raw:
        return raw, None
    last = raw.rfind("@")
    start = last
    while start > 0 and raw[start - 1] == "@":
        start -= 1
    marker = raw[start:last + 1]
    if len(marker) > 2:
        return raw, None                      # 三个以上 @:当作普通文字
    if start > 0 and _GLUED.match(raw[start - 1]):
        return raw, None                      # 邮箱/用户名里的 @,不是引用
    target = raw[last + 1:].strip().strip("\"'“”‘’").strip()
    if not target:
        return raw, None                      # `@` 后面没东西:不是引用
    question = raw[:start].strip()
    kind = "path" if marker == "@@" else "name"
    return question, DocRef(kind=kind, text=target, marker=marker)


#: 段内排序的四路权重(与 `retrieval.py` 的混合融合同一种口味:写死、可核对、加起来是 1)
#:   BM25 词面证据 / 标题命中 / 长块优先 / how-to 步骤先验
W_BM25 = 0.45
W_HEADING = 0.20
W_LENGTH = 0.15
W_HOWTO = 0.20

#: 标题命中:查询词里有几个落在这一节的标题(含各级父标题)上
#: 长块优先:块越大,能给出的原文越多;按本文档最长块归一,开方压一下
#: how-to 先验:只在问法像"怎么做"时才启用(见 `howto_intent`)
_HEADING_SEP = " › "

#: "怎么做"的问法:命中就算强意图,给步骤型段落加权
_HOWTO_STRONG = (
    "怎么", "如何", "怎样", "步骤", "流程", "教程", "操作方法", "操作步骤",
    "怎么做", "怎么用", "如何做", "如何用", "上手", "指南", "指引",
)
_HOWTO_WEAK = ("先做什么", "要准备", "需要准备", "注意什么", "要注意", "顺序", "注意事项")

#: 步骤行长什么样:有序列表 / 圆点列表 / 中文序号
_STEP_LINE = re.compile(
    r"^\s*(?:[0-9]+[.、)）]|[(（][0-9]+[)）]|①|②|③|④|⑤|⑥|⑦|⑧|⑨"
    r"|[-*·•]\s|[一二三四五六七八九十]+[、.])"
)
#: 步骤行的常见起手(祈使句/顺序词)
_STEP_VERBS = (
    "先", "再", "然后", "接着", "最后", "第一步", "第二步", "第三步",
    "执行", "运行", "打开", "点击", "输入", "确认", "备份", "复制", "粘贴",
    "删除", "安装", "启动", "设置", "检查", "登录", "配置", "重启", "停止",
)


def howto_intent(question: str) -> float:
    """问法有多像"怎么做":1 = 强意图,0.5 = 弱意图,0 = 不是。

    它只是**先验**,不改变"有没有命中"的判据:非 how-to 问法把这一路权重
    留给别的证据(见 `_blend` 的归一)。
    """
    text = normalize(question or "")
    if not text:
        return 0.0
    if any(marker in text for marker in _HOWTO_STRONG):
        return 1.0
    if any(marker in text for marker in _HOWTO_WEAK):
        return 0.5
    return 0.0


def _length_prior(chunk: DocChunk, longest: int) -> float:
    """长块优先:块越大,能给出的原文越多。按本文档最长块归一,开方压一下。"""
    if longest <= 0:
        return 0.0
    return math.sqrt(max(0, chunk.tokens) / longest)


def _howto_prior(chunk: DocChunk) -> float:
    """这一段有多像"步骤":有序/列表行多、祈使句起手、短行多。

    单行块不给"短行"这一分:一行正文本来就短,那不代表它是步骤。
    """
    lines = [line.strip() for line in chunk.text.splitlines() if line.strip()]
    if not lines:
        return 0.0
    steps = sum(1 for line in lines if _STEP_LINE.match(line))
    verbs = sum(1 for line in lines if line.startswith(_STEP_VERBS))
    short = sum(1 for line in lines if len(line) <= 40)
    density = (steps + 0.5 * verbs) / len(lines)
    shape = (short / len(lines)) if len(lines) >= 2 else 0.0
    return min(1.0, 0.7 * density + 0.3 * shape)


def _blend(bm25: float, heading: float, length: float,
           howto: float, intent: float) -> float:
    """四路加权;没有 how-to 意图时把那一份权重还给 BM25(不让先验稀释证据)。"""
    if intent <= 0:
        return ((W_BM25 + W_HOWTO) * bm25
                + W_HEADING * heading + W_LENGTH * length)
    return (W_BM25 * bm25 + W_HEADING * heading
            + W_LENGTH * length + W_HOWTO * howto * intent)


# ---------------------------------------------------------------------------
# 文档:块 / 命中 / 结果
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DocChunk:
    """文档里的一块原文(切法见 `ingest.smart_chunks`)。"""

    index: int
    heading: str
    text: str
    tokens: int
    part: int = 0
    #: 在原文里的行号(1 起);找不到就记 0,回答时只说章节不说行号
    line_start: int = 0
    line_end: int = 0
    #: 行号是不是"原文逐字定位"得到的(False = 退一步按首行估的)
    exact: bool = False
    #: 从一级标题到这一节的路径(「安装 › 回滚」),排版时按层次显示
    breadcrumb: tuple[str, ...] = ()
    #: Markdown 标题级别(0 = 没有标题)
    level: int = 0

    @property
    def section(self) -> str:
        """这一节的完整路径;没有标题就是空串。"""
        return _HEADING_SEP.join(self.breadcrumb) if self.breadcrumb else self.heading

    @property
    def span(self) -> str:
        """行号(精确定位的不加"约")。"""
        if not self.line_start:
            return ""
        text = (
            f"第 {self.line_start} 行"
            if self.line_end <= self.line_start
            else f"第 {self.line_start}–{self.line_end} 行"
        )
        return text if self.exact else "约" + text

    @property
    def location(self) -> str:
        """章节 + 行号的短标签,给引用行用。"""
        pieces: list[str] = []
        if self.section:
            pieces.append(f"章节「{self.section}」")
        if self.span:
            pieces.append(self.span)
        return " · ".join(pieces) or "开头"


@dataclass(frozen=True)
class Document:
    """一份读进来的文档(只读快照;文件变了会重新读)。"""

    path: Path
    rel_path: str
    name: str
    title: str
    chunks: tuple[DocChunk, ...]
    tokens: int
    bytes: int
    sha1: str
    mtime_ns: int

    @property
    def headings(self) -> tuple[str, ...]:
        """各节路径(不含文档标题),去重保序。"""
        return tuple(dict.fromkeys(c.section for c in self.chunks if c.section))


@dataclass(frozen=True)
class DocHit:
    """文档里命中的一段(分量留着,`--trace` 能把分数拆开核对)。"""

    chunk: DocChunk
    #: 综合分(四路加权后的 0–1 量级,只用于排序)
    score: float
    coverage: float
    matched: tuple[str, ...] = ()
    #: 各分量(0–1):BM25 归一 / 标题命中 / 长块优先 / 步骤先验
    bm25: float = 0.0
    heading: float = 0.0
    length: float = 0.0
    howto: float = 0.0
    #: 这一问的 how-to 意图(0 / 0.5 / 1)
    intent: float = 0.0

    def breakdown(self) -> str:
        """一行可核对的分数拆解。"""
        parts = [
            f"BM25 {self.bm25:.2f}×{W_BM25:.2f}",
            f"标题 {self.heading:.2f}×{W_HEADING:.2f}",
            f"长块 {self.length:.2f}×{W_LENGTH:.2f}",
        ]
        if self.intent > 0:
            parts.append(
                f"步骤 {self.howto:.2f}×{W_HOWTO:.2f}(意图 {self.intent:.1f})"
            )
        return f"{self.score:.2f} = " + " + ".join(parts)


@dataclass
class DocAnswer:
    """文档模式这一轮的结果:成功、没找到、还是根本没读到文档。"""

    question: str
    ref: DocRef
    document: Document | None = None
    hits: tuple[DocHit, ...] = ()
    outline_only: bool = False
    error: DocError | None = None
    #: 命中但被安全底线挡掉的段数(不静默)
    blocked: int = 0

    @property
    def ok(self) -> bool:
        if self.error is not None:
            return False
        if self.outline_only:
            return self.document is not None
        return bool(self.hits)


@dataclass(frozen=True)
class DocSummary:
    """`:docs` 列出来的那一行。"""

    name: str
    rel_path: str
    bytes: int
    tokens: int
    chunks: int
    modified: str
    error: str = ""


# ---------------------------------------------------------------------------
# 文档库:扫描 / 解析 / 读盘 / 查
# ---------------------------------------------------------------------------
#: 行首的 Markdown 标题(取标题文字)
_HEADING = re.compile(r"^#{1,6}[ \t]+(.+?)[ \t]*#*[ \t]*$", re.MULTILINE)


def _read_body(path: Path) -> tuple[str, dict[str, str], int]:
    """读文件并拆前置元数据;返回 (正文, 元数据, 正文相对文件的行偏移)。

    行偏移是为了让多出来的 `---\ntitle: …\n---` 不至于把行号算少几行:
    `split_frontmatter` 会把前置元数据从正文里切掉,行号得补回去。
    """
    try:
        text = ingest.read_document(path)
    except ValueError as exc:
        raise DocReadError(DocError(code="unsupported", message=str(exc))) from exc
    except OSError as exc:
        raise DocReadError(
            DocError(code="read-error", message=f"读不了这份文件:{exc}")
        ) from exc
    meta, body = ingest.split_frontmatter(text)
    if not body.strip():
        raise DocReadError(DocError(
            code="empty",
            message=f"`{path.name}` 是空的(只有空白/只有前置元数据),没有可引用的正文。",
        ))
    # 行偏移 = 被切掉的前置元数据占了几行(按"行数差"算,不怕结尾有没有换行)
    offset = max(0, len(text.splitlines()) - len(body.splitlines()))
    return body, meta, offset


def _line_span(body: str, chunk_text: str, cursor: int) -> tuple[int, int, bool, int]:
    """算一块原文在文件里的行号;同时返回下次从哪继续找。

    先试**逐字定位**(块没被改写时都能命中);块是重叠拼接出来的、原文里找不到,
    再退一步按"首行"从上次的位置往后找 —— 这时行号是估的,回答里会写"约"。
    定位到正文后,如果上一行正是这一节的 Markdown 标题,就把标题行也包进来:
    引用"章节「回滚」· 第 12–18 行"时,第 12 行就是那行 `## 回滚`。
    """
    offset = body.find(chunk_text, cursor)
    exact = offset >= 0
    if not exact:
        first = next((line.strip() for line in chunk_text.splitlines() if line.strip()), "")
        offset = body.find(first, cursor) if first else -1
        if offset < 0:
            return (0, 0, False, cursor)
    lines = body.splitlines()
    body_start = body[:offset].count("\n") + 1
    end = body_start + chunk_text.count("\n")
    start = body_start
    # 往上跳过空行,如果撞上的是这一节的 Markdown 标题,就把标题行也算进来
    probe = body_start - 2
    while probe >= 0 and not lines[probe].strip():
        probe -= 1
    if probe >= 0 and lines[probe].lstrip().startswith("#"):
        start = probe + 1
    next_cursor = offset + (len(chunk_text) if exact else 1)
    return (start, end, exact, next_cursor)


class DocLibrary:
    """一个文档目录 + 读盘缓存 + 段内检索。

    缓存按「修改时间 + 大小」判新旧:文件没动就复用上一次切好的块与索引,
    改了(哪怕只改一个字)就重新读 —— 和 `corebridge` 的缓存口径一致。
    """

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        project_root: str | Path | None = None,
    ) -> None:
        self.root = Path(root) if root is not None else DEFAULT_DOCS_ROOT
        #: `@@` 的相对路径以它为准(默认 xiaoxi.py 所在目录)
        self.project_root = (
            Path(project_root) if project_root is not None else PROJECT_ROOT
        )
        self._cache: dict[Path, tuple[tuple[int, int], Document, BM25Index]] = {}
        self.reads = 0
        self.reuses = 0

    # ---- 目录 ------------------------------------------------------------
    @property
    def available(self) -> bool:
        return self.root.is_dir()

    def _scan_files(self) -> list[Path]:
        """列出 docs/ 里可读的文档(递归,跳过隐藏目录与 __pycache__)。"""
        if not self.available:
            return []
        found: list[Path] = []
        for base, dirs, files in os.walk(self.root):
            dirs[:] = sorted(
                name for name in dirs
                if not name.startswith(".") and name != "__pycache__"
            )
            for name in sorted(files):
                if name.startswith("."):
                    continue
                path = Path(base) / name
                if path.suffix.lower() not in SUPPORTED_SUFFIXES:
                    continue
                found.append(path)
                if len(found) >= MAX_SCAN_FILES:
                    return found
        return found

    def rel_path(self, path: Path) -> str:
        """给用户看的路径:优先相对项目根(`docs/手册.md`)。"""
        resolved = path.resolve()
        for base in (self.project_root, self.root):
            try:
                return resolved.relative_to(base.resolve()).as_posix()
            except ValueError:
                continue
        return str(resolved)

    def count(self) -> int:
        """目录里有几份可读文档(只扫文件名,不读正文;给横幅/`:stat` 用)。"""
        return len(self._scan_files())

    @property
    def cache_size(self) -> int:
        """缓存里现在有几份文档(给 `:stat` 看,别让内存悄悄涨)。"""
        return len(self._cache)

    # ---- 解析:名字 / 路径 -> 文件 ---------------------------------------
    def resolve(self, ref: DocRef) -> tuple[Path | None, DocError | None]:
        """把引用解析成一个真实文件;解析不了就说清为什么。"""
        if ref.is_path:
            return self._resolve_path(ref)
        return self._resolve_name(ref)

    def _resolve_path(self, ref: DocRef) -> tuple[Path | None, DocError | None]:
        raw = ref.text.replace("\\", "/").strip()
        if re.match(r"^[A-Za-z]:", raw) or raw.startswith(("/", "~")):
            return None, DocError(
                code="outside-project",
                message=(
                    f"`@@{ref.text}` 是绝对路径。文档模式只接受**相对 xiaoxi.py 的路径**"
                    "(例如 @@docs/手册.md)。"
                ),
            )
        try:
            resolved = (self.project_root / raw).resolve()
        except OSError as exc:                                    # pragma: no cover
            return None, DocError(code="read-error", message=f"路径解析失败:{exc}")
        try:
            resolved.relative_to(self.project_root.resolve())
        except ValueError:
            return None, DocError(
                code="outside-project",
                message=(
                    f"`@@{ref.text}` 跑出了小析项目目录。文档模式只读项目目录里的文件"
                    "(`..` 不能跑出去);想读别处的文件,先把它复制进 docs/。"
                ),
            )
        if resolved.is_dir():
            return None, DocError(
                code="not-a-file",
                message=f"`@@{ref.text}` 是一个目录,不是文件;请指到具体的 .md/.txt 文件。",
            )
        if not resolved.is_file():
            return None, DocError(
                code="not-found",
                message=(
                    f"找不到 `@@{ref.text}`(按 xiaoxi.py 的相对路径找:{resolved})"
                    f"\n{self._hint()}"
                ),
                suggestions=self._suggest(ref.text),
            )
        if resolved.suffix.lower() not in SUPPORTED_SUFFIXES:
            return None, DocError(
                code="unsupported",
                message=(
                    "只支持 " + " / ".join(SUPPORTED_SUFFIXES) + " 这几种文档;"
                    f"`{resolved.name}` 不行(PDF/Word 需要第三方库,本工具不装)。"
                ),
            )
        return resolved, None

    def _resolve_name(self, ref: DocRef) -> tuple[Path | None, DocError | None]:
        if not self.available:
            return None, DocError(
                code="missing-dir",
                message=(
                    f"还没有文档目录。在项目根(xiaoxi.py 所在目录)新建 {DOCS_DIRNAME}/,"
                    f"把 .md / .markdown / .txt 放进去,再用「问题@{ref.text}」提问。\n"
                    f"现在找的是:{self.root}"
                ),
            )
        files = self._scan_files()
        if not files:
            return None, DocError(
                code="empty-dir",
                message=(
                    f"{DOCS_DIRNAME}/ 里还没有可读的文档(只认 "
                    + " / ".join(SUPPORTED_SUFFIXES) + ")。"
                ),
            )
        wanted = ref.text.replace("\\", "/").strip().strip("/").lower()

        def match(path: Path, *, with_suffix: bool) -> bool:
            rel = path.relative_to(self.root).as_posix().lower()
            candidates = {
                path.name.lower(),
                path.stem.lower(),
                rel,
                rel.rsplit(".", 1)[0],          # 带子目录但省扩展名:team/周报
            }
            if with_suffix:
                candidates |= {f"{path.stem.lower()}{suffix}" for suffix in SUPPORTED_SUFFIXES}
                candidates.add(f"{wanted.rsplit('/', 1)[-1]}{path.suffix.lower()}")
            return wanted in candidates

        # 先按"整名 / 去扩展名 / 相对 docs 的路径"找;再放宽到"名字 + 扩展名"
        for with_suffix in (False, True):
            matched = [path for path in files if match(path, with_suffix=with_suffix)]
            if not matched:
                continue
            if len(matched) == 1:
                return matched[0], None
            listed = "\n".join(f"  · {self.rel_path(path)}" for path in matched[:5])
            return None, DocError(
                code="ambiguous",
                message=(
                    f"「{ref.text}」能对上 {len(matched)} 份,我不猜是哪一份:\n{listed}\n"
                    f"用「问题@@{self.rel_path(matched[0])}」明确指定。"
                ),
                suggestions=tuple(self.rel_path(path) for path in matched[:5]),
            )
        return None, DocError(
            code="not-found",
            message=f"{DOCS_DIRNAME}/ 里没有「{ref.text}」。\n{self._hint()}",
            suggestions=self._suggest(wanted),
        )

    def _suggest(self, wanted: str) -> tuple[str, ...]:
        """给"是不是想问这份"的提示:按名字相似度取前 3。"""
        scored: list[tuple[float, str]] = []
        for path in self._scan_files():
            label = path.stem or path.name
            score = max(
                similarity(normalize(wanted), normalize(label)),
                similarity(normalize(wanted), normalize(path.name)),
            )
            if score >= SUGGEST_THRESHOLD:
                scored.append((score, label))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return tuple(dict.fromkeys(label for _score, label in scored[:3]))

    def _existing(self, limit: int = 6) -> str:
        """现有文档的名字(给"找不到"的消息用,省得用户再敲一次 :docs)。"""
        labels = list(dict.fromkeys(
            path.stem or path.name for path in self._scan_files()
        ))
        return "、".join(labels[:limit]) if labels else "(目录是空的)"

    def _hint(self) -> str:
        return f"现有文档:{self._existing()}(:docs 看全部)"

    # ---- 读盘 + 切块 -----------------------------------------------------
    def load(self, path: Path) -> Document:
        """读一份文档(带缓存:修改时间/大小没变就不重读)。

        缓存最多留 `MAX_CACHED_DOCS` 份,按插入顺序淘汰 —— 文档模式一次只读一份,
        没必要为了十几份文档把内存一直占着。
        """
        resolved = path.resolve()
        try:
            stat = resolved.stat()
        except OSError as exc:
            raise DocReadError(
                DocError(code="read-error", message=f"读不了这份文件:{exc}")
            ) from exc
        stamp = (stat.st_mtime_ns, stat.st_size)
        cached = self._cache.get(resolved)
        if cached is not None and cached[0] == stamp:
            self.reuses += 1
            return cached[1]

        body, meta, line_offset = _read_body(resolved)
        chunks: list[DocChunk] = []
        cursor = 0
        # 标题栈:按 Markdown 级别把「二级 › 三级」拼成这一节的路径,
        # 这样长文档里同名的「回滚」不会和「安装 › 回滚」混在一起。
        # 一级标题等读完再判断:它通常就是文档标题,不作为"章节"的一部分。
        stack: list[tuple[int, str]] = []
        for raw in ingest.smart_chunks(body):
            line_start, line_end, exact, cursor = _line_span(body, raw.text, cursor)
            if line_start:
                line_start += line_offset
                line_end += line_offset
            level = int(raw.level or 0)
            heading = (raw.heading or "").strip()
            if heading:
                while stack and level and stack[-1][0] >= level:
                    stack.pop()
                stack.append((level, heading))
                breadcrumb = tuple(name for _level, name in stack)
            else:
                breadcrumb = ()
            chunks.append(DocChunk(
                index=raw.index, heading=heading, text=raw.text,
                tokens=raw.tokens, part=raw.part, level=level,
                breadcrumb=breadcrumb,
                line_start=line_start, line_end=line_end, exact=exact,
            ))
        if not chunks:
            raise DocReadError(DocError(code="empty", message="这份文档没有可引用的正文"))
        headings = [chunk.heading for chunk in chunks if chunk.heading]
        # 标题优先取 frontmatter 的 title;其次取**正文里第一个 Markdown 标题**
        # (不能直接拿"第一块"的标题:一级标题下面往往紧跟二级标题,
        # 那个一级标题会因为正文为空而不成块);最后退回文件名
        first_heading = next(
            (match.group(1).strip() for match in _HEADING.finditer(body)),
            "",
        )
        title = (
            str(meta.get("title") or "").strip()
            or first_heading
            or (headings[0] if headings else "")
            or resolved.stem
        )
        # 文档标题不算"章节":把它从每条路径的最前面去掉,排版与标题加权都更准。
        # 一级标题下面若是正文(没有二级标题),那一段就按"开头"算,不重复标题。
        def _strip_title(chunk: DocChunk) -> DocChunk:
            crumbs = chunk.breadcrumb
            if not crumbs or crumbs[0] != title:
                return chunk
            rest = crumbs[1:]
            return replace(
                chunk,
                breadcrumb=rest,
                heading=chunk.heading if rest else "",
            )

        chunks = [_strip_title(chunk) for chunk in chunks]
        document = Document(
            path=resolved,
            rel_path=self.rel_path(resolved),
            name=resolved.name,
            title=title,
            chunks=tuple(chunks),
            tokens=sum(chunk.tokens for chunk in chunks),
            bytes=stat.st_size,
            sha1=hashlib.sha1(body.encode("utf-8")).hexdigest(),
            mtime_ns=stat.st_mtime_ns,
        )
        self._cache[resolved] = (stamp, document, self._build_index(document))
        if len(self._cache) > MAX_CACHED_DOCS:
            for stale in list(self._cache)[: len(self._cache) - MAX_CACHED_DOCS]:
                self._cache.pop(stale, None)
        self.reads += 1
        return document

    def _build_index(self, document: Document) -> BM25Index:
        index = BM25Index()
        for chunk in document.chunks:
            # 索引文本 = 章节路径 + 正文:标题里的词也算证据(再加标题命中权重)
            index.add_document(
                f"{ROLE}:{document.rel_path}",
                f"chunk-{chunk.index}",
                chunk.section or document.title,
                tokenize(chunk.section + "\n" + chunk.text),
                heading=chunk.section,
                source_file=document.rel_path,
            )
        return index

    # ---- 段内检索 --------------------------------------------------------
    def search(self, document: Document, question: str, limit: int = MAX_HITS) -> list[DocHit]:
        """在这份文档里找与问题最相关的几段;找不到就返回空(不猜)。

        排序不是单看 BM25,而是四路加权(权重在模块顶部,可核对):

          · **BM25**(0.45):词面证据,按本文档第一名的分归一到 0–1;
          · **标题命中**(0.20):查询词落在这一节标题(含父标题)上的比例;
          · **长块优先**(0.15):块越大给得越多,按最长块归一并开方;
          · **how-to 先验**(0.20):问法像"怎么做"时,步骤型段落加权;
            没有意图时这一份权重还给 BM25(见 `_blend`)。

        "有没有命中"仍由证据判据守:词项覆盖 ≥0.34、或命中 ≥2 个词、
        或标题里有词 —— 其余一律按没找到处理。权重只影响**排序**,不放宽阈值。
        """
        query_terms = tuple(dict.fromkeys(tokenize(question)))
        if not query_terms:
            return []
        cached = self._cache.get(document.path)
        # 只认"同一份文档"的缓存索引:传进来的 Document 若是更早的版本,
        # 宁可重算一遍索引,也不要拿新索引去查旧块
        index = (
            cached[2]
            if cached is not None and cached[1] is document
            else self._build_index(document)
        )
        by_id = {f"chunk-{chunk.index}": chunk for chunk in document.chunks}
        terms = set(query_terms)
        total_terms = len(terms)
        intent = howto_intent(question)
        longest = max((chunk.tokens for chunk in document.chunks), default=0)

        raw_hits = index.search(query_terms, limit=max(limit * 3, 6))
        top = max((raw.score for raw in raw_hits), default=0.0) or 1.0
        hits: list[DocHit] = []
        for raw in raw_hits:
            chunk = by_id.get(raw.entry_id)
            if chunk is None:                                   # pragma: no cover
                continue
            matched = tuple(dict.fromkeys(raw.matched))
            coverage = len(set(matched) & terms) / total_terms
            heading_tokens = set(tokenize(chunk.section))
            heading = len(heading_tokens & terms) / total_terms
            # 只在**有证据**时才留:词项覆盖够、命中够、或标题里真有查询词
            if coverage < MIN_COVERAGE and len(matched) < 2 and heading <= 0:
                continue
            length = _length_prior(chunk, longest)
            howto = _howto_prior(chunk)
            score = _blend(
                bm25=raw.score / top, heading=heading,
                length=length, howto=howto, intent=intent,
            )
            hits.append(DocHit(
                chunk=chunk, score=round(score, 4),
                coverage=round(coverage, 4), matched=matched,
                bm25=round(raw.score / top, 4), heading=round(heading, 4),
                length=round(length, 4), howto=round(howto, 4),
                intent=intent,
            ))
        # 先按综合分排;综合分保留一位小数后**相同**的,长块先出(它给的原文更多)。
        # 这样"长块优先"只在证据相当的时候起作用,不会盖过词面证据。
        hits.sort(key=lambda hit: (
            -round(hit.score, 1), -hit.chunk.tokens, hit.chunk.index,
        ))
        return self._drop_overlaps(hits, limit)

    @staticmethod
    def _drop_overlaps(hits: list[DocHit], limit: int) -> list[DocHit]:
        """相邻块、包含块只留一份:文字更全的那一块优先。

        `ingest` 在切长段时会给相邻块留重叠,一份文档里也可能出现"简版"
        和"详版"这种包含关系。这里按三条规矩收敛:

          1. 已经引了某块 → 它包含的短块不再引(短块没带来新内容);
          2. 新来的块**包含**已引的块(可能不止一个)→ 用新的换掉它们
             (同样的文字,但更全);
          3. 同一节的相邻块**且有重叠文字** → 只留分高的那块;切的时候如果因为
             预算把重叠丢了(两块首尾并不重复),那就两块都留,别白丢内容。
        """
        def _overlaps(left: str, right: str) -> bool:
            """两块文字首尾有没有重叠(切长段时留的那段重复文字)。"""
            probe = 60
            tail = left[-probe:]
            head = right[:probe]
            return bool(tail) and (tail in right or (bool(head) and head in left))

        kept: list[DocHit] = []
        for hit in hits:
            if any(hit.chunk.text in chosen.chunk.text for chosen in kept):
                continue                                # 1. 已被更全(或同样)的块覆盖
            # 2. 新块包含已引的块:把被包含的那些全部换掉(可能不止一条)
            before = len(kept)
            kept = [
                chosen for chosen in kept
                if chosen.chunk.text not in hit.chunk.text
            ]
            replaced = len(kept) != before
            # 3. 同一节的相邻块只留一条 —— 但刚替换掉了旧块时不能再丢(否则两块都没了)
            if not replaced and any(
                chosen.chunk.section
                and hit.chunk.section == chosen.chunk.section
                and chosen.chunk.part != hit.chunk.part
                and abs(chosen.chunk.index - hit.chunk.index) <= 1
                and _overlaps(chosen.chunk.text, hit.chunk.text)
                for chosen in kept
            ):
                continue
            kept.append(hit)
            if len(kept) >= limit:
                break
        kept.sort(key=lambda hit: (-round(hit.score, 1), -hit.chunk.tokens, hit.chunk.index))
        return kept[:limit]

    # ---- 一次完整的文档问答 ---------------------------------------------
    def answer(self, question: str, ref: DocRef, *, limit: int = MAX_HITS) -> DocAnswer:
        path, error = self.resolve(ref)
        if error is not None:
            return DocAnswer(question=question, ref=ref, error=error)
        assert path is not None
        try:
            document = self.load(path)
        except DocReadError as exc:
            return DocAnswer(question=question, ref=ref, error=exc.error)
        if not question.strip():
            return DocAnswer(question=question, ref=ref, document=document,
                             outline_only=True)
        hits = self.search(document, question, limit=limit)
        if not hits:
            return DocAnswer(question=question, ref=ref, document=document)
        allowed, blocked = self._screen(hits)
        return DocAnswer(question=question, ref=ref, document=document,
                         hits=tuple(allowed), blocked=blocked)

    @staticmethod
    def _screen(hits: list[DocHit]) -> tuple[list[DocHit], int]:
        """读进来的文字也要过安全底线:命中红线的段落不引用。"""
        allowed: list[DocHit] = []
        blocked = 0
        for hit in hits:
            report = safety.assess(hit.chunk.text)
            if str(report.get("level")) == "refuse":
                blocked += 1
                continue
            allowed.append(hit)
        return allowed, blocked

    # ---- 列表 ------------------------------------------------------------
    def list(self) -> tuple[DocSummary, ...]:
        """`:docs` 用:目录里有哪些文档、各多大、切了几块。"""
        summaries: list[DocSummary] = []
        for path in self._scan_files():
            try:
                document = self.load(path)
            except DocReadError as exc:
                summaries.append(DocSummary(
                    name=path.name,
                    rel_path=self.rel_path(path),
                    bytes=_safe_size(path),
                    tokens=0, chunks=0, modified="", error=exc.error.message,
                ))
                continue
            summaries.append(DocSummary(
                name=document.name,
                rel_path=document.rel_path,
                bytes=document.bytes,
                tokens=document.tokens,
                chunks=len(document.chunks),
                modified=_stamp(document.mtime_ns),
            ))
        return tuple(summaries)


def _safe_size(path: Path) -> int:
    """文件大小;文件刚好被删掉之类的情况返回 0(列目录不该因为一份坏文件崩掉)。"""
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _stamp(mtime_ns: int) -> str:
    try:
        moment = datetime.datetime.fromtimestamp(mtime_ns / 1_000_000_000)
    except (OverflowError, OSError, ValueError):                  # pragma: no cover
        return ""
    return moment.strftime("%Y-%m-%d %H:%M")


# ---------------------------------------------------------------------------
# 渲染:只引用原文
# ---------------------------------------------------------------------------
def _sentence_trim(text: str, budget: int) -> tuple[str, bool]:
    """超预算时在句子边界截断;返回 (文字, 是否截断过)。"""
    if len(text) <= budget:
        return text, False
    cut = text[:budget]
    for index in range(len(cut) - 1, max(0, budget // 2), -1):
        if cut[index] in "。！？!?；;\n":
            return cut[: index + 1].rstrip(), True
    return cut.rstrip() + "…", True


#: 会操纵终端的字符:ANSI 转义序列、C0/C1 控制符、双向覆盖符(Trojan Source)。
#: 文档可能来自别处,原样回显会把终端搞乱、甚至伪装内容 —— 只去掉这些控制字符,
#: 正文其他字符一个不动,引用仍然是原文。
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_BIDI = re.compile(r"[\u202a-\u202e\u2066-\u2069]")


def _safe(text: str) -> str:
    """把会操纵终端的字符去掉(内容本身不动)。"""
    cleaned = _ANSI_ESCAPE.sub("", text or "")
    cleaned = _CONTROL.sub("", cleaned)
    return _BIDI.sub("", cleaned)


def _title(document: Document) -> str:
    return f"《{document.title}》"


def _title_with_path(document: Document) -> str:
    return f"{_title(document)}({document.rel_path})"


def _outline_lines(document: Document) -> list[str]:
    """目录:按 Markdown 级别缩进,每行只给章节名与行号。"""
    lines = ["目录:"]
    headings = [chunk for chunk in document.chunks if chunk.section]
    if not headings:
        lines.append("  (这份文档没有 Markdown 标题,直接问里面的内容就行:"
                     f"例如 问题@{document.path.stem})")
        return lines
    seen: set[str] = set()
    for chunk in headings:
        section = chunk.section
        if section in seen:
            continue
        seen.add(section)
        depth = max(0, min(chunk.level - 2, 3))  # 一级标题已当标题用,二级起算第 0 层
        lines.append(f"{'  ' * (depth + 1)}· {section}({chunk.span or '行号未知'})")
    return lines


def _error_lines(answer: DocAnswer) -> list[str]:
    error = answer.error
    assert error is not None
    lines = [error.message]
    if error.suggestions:
        lines.append("是不是想问:" + "、".join(error.suggestions) + "?")
    if error.code in ("missing-dir", "empty-dir"):
        lines.append("放好文档后:python xiaoxi.py \"问题@文档名\"")
    elif error.code in ("not-found", "ambiguous"):
        lines.append("现在有哪些文档:在对话里输 :docs(或命令行 --docs)")
        lines.append(
            "（如果这句里的 @ 只是符号本身、不是在指定文档,去掉它再问一次就是普通提问。）"
        )
    return lines


def _quote_lines(text: str) -> list[str]:
    """原文统一压成引用条:每行前面加 `│`,一眼能看出"这是文档原话"。"""
    lines: list[str] = []
    for raw in _safe(text).splitlines():
        line = raw.rstrip()
        lines.append(f"│ {line}" if line.strip() else "│")
    return lines


def _quote_blocks(answer: DocAnswer, *, budget: int) -> tuple[list[str], int]:
    """把命中的段落排成引用块;返回 (块, 没引用的段数)。

    排版约定:块之间空一行;每块先给"章节 · 行号",原文用 `│` 起头;
    被截断的块在末尾单独说明(不混进原文里)。
    """
    blocks: list[str] = []
    used = 0
    omitted = 0
    total = len(answer.hits)
    for position, hit in enumerate(answer.hits, 1):
        remaining = budget - used
        if remaining <= 120:
            omitted += total - position + 1
            break
        text, truncated = _sentence_trim(hit.chunk.text, remaining)
        body: list[str] = []
        label = hit.chunk.location
        body.append(f"{position}) {label}" if total > 1 else label)
        body.append("")
        body.extend(_quote_lines(text))
        if truncated:
            body.append("")
            body.append("(这一段太长,只引到这里;要看全文请直接打开这份文档)")
            omitted += total - position
        blocks.append("\n".join(body))
        used += len(text)
        if truncated:
            break
    return blocks, omitted


def _provenance(answer: DocAnswer) -> str:
    """最后一行:哪份文档、哪几段、原文照录。"""
    document = answer.document
    assert document is not None
    spans = [hit.chunk.span for hit in answer.hits if hit.chunk.span]
    where = document.rel_path
    if len(answer.hits) > 1:
        where += f" 的 {len(answer.hits)} 段原文"
    if spans:
        where += "(" + "、".join(dict.fromkeys(spans)) + ")"
    return f"—— 摘自 {where};原文照录,没有改写。"


def render(answer: DocAnswer, *, trace: bool = False,
           budget: int = MAX_QUOTE_CHARS) -> str:
    """文档模式的回答:只引原文,出处可倒查。

    排版固定三段,便于扫读:

        文档: 《标题》(路径) · 块数 / 字数
        这一轮只读这一份…(命中几段 / 只看目录 / 没找到)
        1) 章节「父 › 子」 · 第 a–b 行
           │ 原文…
        —— 摘自 …;原文照录,没有改写。

    `budget` 是这一次最多引用多少字(`--brief` 会把它调小)。
    """
    lines: list[str] = []
    document = answer.document
    if trace:
        lines.append(
            f"[文档] {answer.ref.marker}{answer.ref.text}"
            + (f" -> {document.rel_path}" if document is not None else " -> (没读到)")
        )
    if answer.error is not None:
        if trace:
            lines.append(f"[文档错误] {answer.error.code}")
        lines.extend(_error_lines(answer))
        return "\n".join(lines).strip()

    assert document is not None
    if trace:
        lines.append(
            f"[文档信息] {document.bytes} 字节 · {document.tokens} 字 · "
            f"{len(document.chunks)} 块 · 更新于 {_stamp(document.mtime_ns)}"
        )
        lines.append(f"[问法] {answer.question or '(没写问题,只看目录)'}")

    head = (
        f"文档:{_title_with_path(document)} · "
        f"{len(document.chunks)} 块 / 约 {document.tokens} 字"
    )

    if answer.outline_only:
        body = _outline_lines(document)
        lead = "这一轮只看目录,没有查数据包:"
        return "\n".join(lines + [head, lead, ""] + body).strip()

    if not answer.hits:
        if answer.blocked:
            # 对得上的段落确实有,但每一段都命中安全底线 —— 这不能说成"没找到"
            lines.extend([head, "这一轮只读这一份,没有查数据包。"])
            lines.append("")
            lines.append(
                f"对得上的 {answer.blocked} 段内容命中安全底线,我不引用。"
            )
            lines.append("这类内容不在可回答范围内;如果是出于防护/合规的疑问,可以换一种问法。")
            if trace:
                lines.append(f"[安全] {answer.blocked} 段命中安全底线,没有引用")
            return "\n".join(lines).strip()
        lines.extend([head, "这一轮只读这一份,没有查数据包。"])
        lines.append("")
        if not tokenize(answer.question):
            # 全是「怎么 / 什么」这类功能词:没有可检索的实词,说清楚,别让人以为"文档里确实没有"
            lines.append(
                f"「{answer.question}」里没有可检索的实词(全是功能词),"
                "我没法在这份文档里按词找。"
            )
            lines.append(
                "换一种问法:用文档里可能出现的词提问;或者先看目录(@"
                f"{document.path.stem})。"
            )
        else:
            lines.append(f"在它里面没有找到和「{answer.question}」对得上的段落。")
            lines.append("我只读了这一份,不拿数据包的知识补;你可以换个说法,或先看它的目录。")
            if document.headings:
                lines.append("这份文档的章节:" + "、".join(document.headings[:8]))
        if trace:
            lines.append(
                f"[文档片段] 命中 0 段(阈值:词项覆盖 ≥ {MIN_COVERAGE:.2f},"
                "或命中 ≥2 个词,或标题里有查询词)"
            )
        return "\n".join(lines).strip()

    if trace:
        lines.append(f"[文档片段] 命中 {len(answer.hits)} 段:")
        for position, hit in enumerate(answer.hits, 1):
            lines.append(
                f"  {position}) {hit.chunk.section or '(开头)'}"
                f" · 第 {hit.chunk.index + 1} 块 · 覆盖 {hit.coverage:.2f}"
                f" · {hit.breakdown()}"
            )
        if answer.blocked:
            lines.append(f"[安全] {answer.blocked} 段命中安全底线,没有引用")

    blocks, omitted = _quote_blocks(answer, budget=budget)
    lead = (
        "这一轮只读这一份,没有查数据包;"
        f"下面是命中的原文(共 {len(answer.hits)} 段,按相关度排):"
        if len(answer.hits) > 1 else
        "这一轮只读这一份,没有查数据包:"
    )
    tail: list[str] = []
    if omitted:
        tail.append(f"(还有 {omitted} 段相关段落没引用:段落太长或超出引用预算)")
    if answer.blocked:
        tail.append(f"(另有 {answer.blocked} 段内容命中安全底线,没有引用)")
    if len(answer.hits) > 1:
        tail.append("以上都是原文,我没有改写。")
    tail.append(_provenance(answer))
    lines.extend([head, lead, "", "\n\n".join(blocks), "", "\n\n".join(tail)])
    if trace:
        lines.append(f"[引用] {document.rel_path}")
    return _safe("\n".join(lines).strip())


def quote_text(answer: DocAnswer) -> str:
    """给上层记账用的纯引用正文(不含标题与出处行)。"""
    if not answer.hits:
        return ""
    return _safe("\n\n".join(hit.chunk.text for hit in answer.hits))


__all__ = [
    "ROLE",
    "SOURCE_NOUN",
    "PROJECT_ROOT",
    "DOCS_DIRNAME",
    "DEFAULT_DOCS_ROOT",
    "SUPPORTED_SUFFIXES",
    "MAX_HITS",
    "MAX_QUOTE_CHARS",
    "MAX_CACHED_DOCS",
    "MIN_COVERAGE",
    "W_BM25",
    "W_HEADING",
    "W_LENGTH",
    "W_HOWTO",
    "howto_intent",
    "DocRef",
    "DocError",
    "DocReadError",
    "DocChunk",
    "Document",
    "DocHit",
    "DocAnswer",
    "DocSummary",
    "DocLibrary",
    "parse_reference",
    "render",
    "quote_text",
]

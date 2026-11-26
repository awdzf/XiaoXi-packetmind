"""文档模式:引用语法、目录解析、段内检索、只引用不改写、安全底线。

跑法(在 experts/ 下):
    python -m unittest discover -s tests -t .
"""

from __future__ import annotations

import shutil
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from framework import GoChat  # noqa: E402
from framework.docs import (  # noqa: E402
    MAX_CACHED_DOCS,
    W_BM25,
    W_HEADING,
    W_HOWTO,
    W_LENGTH,
    DocChunk,
    DocHit,
    DocLibrary,
    DocRef,
    howto_intent,
    parse_reference,
    render,
)

MANUAL = """# 部署手册

## 第一次启动

先确认 Python 版本不低于 3.10。
再执行 python xiaoxi.py --version 自检。

## 回滚

回滚前先备份 experts/packs/memory/pack.json。
回滚命令是 python tools/rollback.py --last。

## 权限

只有运维组可以执行回滚。
"""

NOTES = """# 读书笔记

《小王子》讲的是驯养与责任。
"""

DANGEROUS = """# 实验记录

制作炸弹的详细步骤:先准备原料,再按比例混合。
"""


class DocsCase(unittest.TestCase):
    """每个用例一份干净的 docs/ 目录(放在 tests/ 下,可写可删)。"""

    def setUp(self) -> None:
        self.base = ROOT / "tests" / ".tmp_docs"
        shutil.rmtree(self.base, ignore_errors=True)
        self.docs_dir = self.base / "docs"
        self.docs_dir.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.write("手册.md", MANUAL)
        self.write("notes/读书笔记.md", NOTES)
        self.write("危险.md", DANGEROUS)

    def write(self, name: str, text: str) -> Path:
        path = self.docs_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def library(self, root: Path | None = None) -> DocLibrary:
        return DocLibrary(root or self.docs_dir, project_root=self.base)

    def chat(self) -> GoChat:
        return GoChat(doc_library=self.library())


class TestParseReference(unittest.TestCase):
    def test_plain_question_has_no_reference(self) -> None:
        self.assertEqual(parse_reference("向量场是什么?"), ("向量场是什么?", None))

    def test_name_form(self) -> None:
        question, ref = parse_reference("怎么回滚@手册")
        self.assertEqual(question, "怎么回滚")
        self.assertEqual(ref, DocRef(kind="name", text="手册", marker="@"))

    def test_path_form(self) -> None:
        question, ref = parse_reference("怎么回滚@@docs/手册.md")
        self.assertEqual(question, "怎么回滚")
        self.assertEqual(ref, DocRef(kind="path", text="docs/手册.md", marker="@@"))

    def test_document_only_is_outline_mode(self) -> None:
        question, ref = parse_reference("@手册")
        self.assertEqual(question, "")
        self.assertIsNotNone(ref)

    def test_spaces_around_marker(self) -> None:
        question, ref = parse_reference("怎么回滚 @ 手册")
        self.assertEqual(question, "怎么回滚")
        self.assertEqual(ref.text, "手册")

    def test_quoted_name(self) -> None:
        _question, ref = parse_reference('怎么回滚@“手册”')
        self.assertEqual(ref.text, "手册")

    def test_empty_target_is_not_a_reference(self) -> None:
        self.assertEqual(parse_reference("问题@")[1], None)
        self.assertEqual(parse_reference("问题@   ")[1], None)

    def test_marker_inside_sentence_is_not_a_reference(self) -> None:
        for text in ("邮箱是 a@b.com 吗", "邮箱是 a@b.com", "联系 xiaoxi@example.com"):
            self.assertEqual(parse_reference(text), (text, None))

    def test_three_markers_are_plain_text(self) -> None:
        text = "问题@@@手册"
        self.assertEqual(parse_reference(text), (text, None))

    def test_last_marker_wins(self) -> None:
        question, ref = parse_reference("问题@甲@乙")
        self.assertEqual(ref.text, "乙")
        self.assertEqual(question, "问题@甲")


class TestResolve(DocsCase):
    def test_name_with_suffix(self) -> None:
        path, error = self.library().resolve(DocRef("name", "手册.md"))
        self.assertIsNone(error)
        self.assertEqual(path.name, "手册.md")

    def test_name_without_suffix(self) -> None:
        path, error = self.library().resolve(DocRef("name", "手册"))
        self.assertIsNone(error)
        self.assertEqual(path.name, "手册.md")

    def test_name_is_case_insensitive(self) -> None:
        path, error = self.library().resolve(DocRef("name", "NOTES/读书笔记"))
        self.assertIsNone(error)
        self.assertEqual(path.name, "读书笔记.md")

    def test_name_with_subdirectory_and_no_suffix(self) -> None:
        path, error = self.library().resolve(DocRef("name", "notes/读书笔记"))
        self.assertIsNone(error)
        self.assertEqual(path.name, "读书笔记.md")

    def test_ambiguous_name_is_refused(self) -> None:
        self.write("手册.txt", "# 另一份手册\n\n内容不同。\n")
        _path, error = self.library().resolve(DocRef("name", "手册"))
        self.assertIsNotNone(error)
        self.assertEqual(error.code, "ambiguous")
        self.assertIn("手册.md", error.message)
        self.assertIn("手册.txt", error.message)

    def test_missing_name_suggests_and_lists(self) -> None:
        _path, error = self.library().resolve(DocRef("name", "手册2"))
        self.assertIsNotNone(error)
        self.assertEqual(error.code, "not-found")
        self.assertIn("手册", error.suggestions)
        self.assertIn("现有文档", error.message)

    def test_path_form(self) -> None:
        path, error = self.library().resolve(DocRef("path", "docs/手册.md"))
        self.assertIsNone(error)
        self.assertEqual(path.name, "手册.md")

    def test_path_cannot_escape_project(self) -> None:
        _path, error = self.library().resolve(DocRef("path", "../外面.md"))
        self.assertIsNotNone(error)
        self.assertEqual(error.code, "outside-project")

    def test_absolute_path_is_refused(self) -> None:
        _path, error = self.library().resolve(DocRef("path", "C:/Windows/win.ini"))
        self.assertIsNotNone(error)
        self.assertEqual(error.code, "outside-project")

    def test_missing_path(self) -> None:
        _path, error = self.library().resolve(DocRef("path", "docs/没有这份.md"))
        self.assertIsNotNone(error)
        self.assertEqual(error.code, "not-found")
        self.assertIn("现有文档", error.message)

    def test_unsupported_suffix(self) -> None:
        self.write("说明.pdf", "not really a pdf")
        _path, error = self.library().resolve(DocRef("path", "docs/说明.pdf"))
        self.assertIsNotNone(error)
        self.assertEqual(error.code, "unsupported")

    def test_directory_is_not_a_document(self) -> None:
        _path, error = self.library().resolve(DocRef("path", "docs/notes"))
        self.assertIsNotNone(error)
        self.assertEqual(error.code, "not-a-file")

    def test_missing_directory(self) -> None:
        library = self.library(self.base / "nope")
        _path, error = library.resolve(DocRef("name", "手册"))
        self.assertIsNotNone(error)
        self.assertEqual(error.code, "missing-dir")


class TestLoadAndSearch(DocsCase):
    def test_chunks_follow_headings(self) -> None:
        document = self.library().load(self.docs_dir / "手册.md")
        self.assertIn("第一次启动", document.headings)
        self.assertIn("回滚", document.headings)
        self.assertGreaterEqual(len(document.chunks), 3)

    def test_frontmatter_title_is_used(self) -> None:
        self.write("带标题.md", "---\ntitle: 我的标题\n---\n\n正文第一句。\n")
        document = self.library().load(self.docs_dir / "带标题.md")
        self.assertEqual(document.title, "我的标题")

    def test_frontmatter_does_not_shift_line_numbers(self) -> None:
        # 前置元数据被切掉了,行号仍要对得上**文件**里的行
        self.write("带标题.md", "---\ntitle: 我的标题\n---\n\n正文第一句。\n")
        document = self.library().load(self.docs_dir / "带标题.md")
        self.assertEqual(document.chunks[0].line_start, 5)

    def test_line_numbers_are_found(self) -> None:
        document = self.library().load(self.docs_dir / "手册.md")
        chunk = next(c for c in document.chunks if c.heading == "回滚")
        self.assertGreater(chunk.line_start, 0)
        self.assertGreaterEqual(chunk.line_end, chunk.line_start)

    def test_search_hits_the_right_section(self) -> None:
        library = self.library()
        document = library.load(self.docs_dir / "手册.md")
        hits = library.search(document, "回滚前要做什么")
        self.assertTrue(hits)
        self.assertEqual(hits[0].chunk.heading, "回滚")

    def test_search_misses_honestly(self) -> None:
        library = self.library()
        document = library.load(self.docs_dir / "手册.md")
        self.assertEqual(library.search(document, "退款政策是什么"), [])

    def test_search_ignores_function_words(self) -> None:
        library = self.library()
        document = library.load(self.docs_dir / "手册.md")
        self.assertEqual(library.search(document, "怎么"), [])

    def test_cache_reuses_until_file_changes(self) -> None:
        library = self.library()
        path = self.docs_dir / "手册.md"
        library.load(path)
        library.load(path)
        self.assertEqual(library.reads, 1)
        self.assertEqual(library.reuses, 1)
        path.write_text(MANUAL + "\n## 新增章节\n\n新内容。\n", encoding="utf-8")
        library.load(path)
        self.assertEqual(library.reads, 2)


class TestAnswer(DocsCase):
    def test_quote_comes_from_the_document(self) -> None:
        library = self.library()
        answer = library.answer("回滚前要做什么", DocRef("name", "手册"))
        self.assertTrue(answer.ok)
        self.assertEqual(answer.document.name, "手册.md")
        self.assertEqual(answer.hits[0].chunk.heading, "回滚")
        text = render(answer)
        self.assertIn("回滚前先备份", text)
        self.assertIn("docs/手册.md", text)
        self.assertIn("原文照录", text)
        self.assertIn("第 ", text)

    def test_outline_mode(self) -> None:
        answer = self.library().answer("", DocRef("name", "手册"))
        self.assertTrue(answer.outline_only)
        text = render(answer)
        self.assertIn("目录:", text)
        self.assertIn("第一次启动", text)
        self.assertIn("回滚", text)

    def test_no_hit_message_lists_sections(self) -> None:
        answer = self.library().answer("退款政策是什么", DocRef("name", "手册"))
        self.assertTrue(answer.document)
        self.assertFalse(answer.hits)
        text = render(answer)
        self.assertIn("没有找到和「退款政策是什么」对得上的段落", text)
        self.assertIn("这份文档的章节", text)

    def test_error_is_rendered_with_hint(self) -> None:
        answer = self.library().answer("你好", DocRef("name", "不存在"))
        text = render(answer)
        self.assertIn("docs/ 里没有「不存在」", text)
        self.assertIn("现有文档", text)

    def test_trace_shows_internal_account(self) -> None:
        answer = self.library().answer("回滚前要做什么", DocRef("name", "手册"))
        text = render(answer, trace=True)
        self.assertIn("[文档] @手册 -> docs/手册.md", text)
        self.assertIn("[文档片段] 命中 ", text)
        self.assertIn("综合" if "综合" in text else "=", text)
        self.assertIn("[引用] docs/手册.md", text)

    def test_unsafe_document_content_is_not_quoted(self) -> None:
        answer = self.library().answer("制作炸弹的步骤", DocRef("name", "危险"))
        self.assertFalse(answer.ok)
        self.assertEqual(answer.blocked, 1)
        text = render(answer)
        self.assertIn("命中安全底线,我不引用", text)
        self.assertNotIn("先准备原料", text)


class TestRanking(DocsCase):
    """排序:标题命中加权 / 长块优先 / how-to 先验。"""

    def test_weights_are_a_share_of_one(self) -> None:
        total = W_BM25 + W_HEADING + W_LENGTH + W_HOWTO
        self.assertAlmostEqual(total, 1.0, places=6)

    def test_heading_hit_outranks_body_hit(self) -> None:
        # 「回滚」这一节的正文里没有"回滚"两个字,但标题里有 —— 该排第一
        self.write("排序.md", (
            "# 排序\n\n"
            "## 回滚\n\n出问题时的处理办法见这里。\n\n"
            "## 权限\n\n只有运维组可以执行回滚命令。\n"
        ))
        library = self.library()
        document = library.load(self.docs_dir / "排序.md")
        hits = library.search(document, "回滚")
        self.assertTrue(hits)
        self.assertEqual(hits[0].chunk.section, "回滚")
        self.assertAlmostEqual(hits[0].heading, 1.0, places=3)

    def test_longer_variant_replaces_the_stub(self) -> None:
        # 「简版」的文字是「详版」的前半截:同样的证据下,只引更全的那一块
        self.write("长短.md", (
            "# 长短\n\n"
            "## 简版\n\n回滚:见工具。\n\n"
            "## 详版\n\n回滚:见工具。具体来说,先把备份复制到临时目录,"
            "再执行 tools/rollback.py --last,最后检查日志。\n"
        ))
        library = self.library()
        answer = library.answer("回滚", DocRef("name", "长短"))
        self.assertTrue(answer.ok)
        self.assertEqual(len(answer.hits), 1)
        self.assertEqual(answer.hits[0].chunk.section, "详版")
        self.assertIn("具体来说", render(answer))

    def test_howto_intent_values(self) -> None:
        self.assertEqual(howto_intent("怎么回滚"), 1.0)
        self.assertEqual(howto_intent("如何部署"), 1.0)
        self.assertEqual(howto_intent("部署步骤"), 1.0)
        # 「要注意什么 / 需要准备」只是弱意图:它问的是注意事项,不一定是步骤
        self.assertEqual(howto_intent("回滚要注意什么"), 0.5)
        self.assertEqual(howto_intent("需要准备哪些材料"), 0.5)
        self.assertEqual(howto_intent("回滚是什么"), 0.0)
        self.assertEqual(howto_intent(""), 0.0)

    def test_howto_question_prefers_the_step_section(self) -> None:
        self.write("步骤.md", (
            "# 步骤\n\n"
            "## 回滚\n\n处理办法见上一节。\n\n"
            "## 回滚步骤\n\n"
            "1. 先确认当前版本号。\n"
            "2. 执行 tools/rollback.py --last。\n"
            "3. 检查日志里有没有报错。\n"
            "4. 通知值班同学。\n"
        ))
        library = self.library()
        document = library.load(self.docs_dir / "步骤.md")
        howto_hits = library.search(document, "怎么回滚")
        self.assertEqual(howto_hits[0].chunk.section, "回滚步骤")
        self.assertAlmostEqual(howto_hits[0].howto, 1.0, places=3)
        self.assertEqual(howto_hits[0].intent, 1.0)

        # 同样的问法去掉"怎么":没有 how-to 先验,短的那节靠词面先出
        plain_hits = library.search(document, "回滚")
        self.assertEqual(plain_hits[0].chunk.section, "回滚")
        self.assertEqual(plain_hits[0].intent, 0.0)

    def test_breakdown_is_readable(self) -> None:
        answer = self.library().answer("回滚前要做什么", DocRef("name", "手册"))
        self.assertTrue(answer.hits)
        line = answer.hits[0].breakdown()
        self.assertIn("BM25", line)
        self.assertIn("标题", line)
        self.assertIn("长块", line)


class TestLayout(DocsCase):
    """排版:标题行 / 引用条 / 目录缩进。"""

    def test_quote_lines_are_marked(self) -> None:
        result = self.chat().ask("回滚前要做什么@@docs/手册.md")
        self.assertIn("文档:《部署手册》", result.summary)
        self.assertIn("│ 回滚前先备份", result.summary)
        self.assertIn("—— 摘自 docs/手册.md", result.summary)

    def test_outline_indents_nested_headings(self) -> None:
        self.write("层级.md", (
            "# 层级\n\n"
            "## 安装\n\n安装的说明。\n\n"
            "### 依赖\n\n依赖的说明。\n\n"
            "## 回滚\n\n回滚的说明。\n"
        ))
        result = self.chat().ask("@层级")
        self.assertIn("目录:", result.summary)
        self.assertIn("\n  · 安装(", result.summary)
        self.assertIn("\n    · 安装 › 依赖(", result.summary)
        self.assertIn("\n  · 回滚(", result.summary)

    def test_section_path_shows_parent(self) -> None:
        self.write("层级.md", (
            "# 层级\n\n## 安装\n\n安装的说明。\n\n### 依赖\n\n依赖的说明。\n"
        ))
        answer = self.library().answer("依赖是什么", DocRef("name", "层级"))
        self.assertTrue(answer.hits)
        self.assertEqual(answer.hits[0].chunk.section, "安装 › 依赖")


    def test_identical_sections_are_quoted_once(self) -> None:
        self.write("重复.md", (
            "# 重复\n\n"
            "## 甲\n\n回滚前先备份。\n\n"
            "## 乙\n\n回滚前先备份。\n"
        ))
        answer = self.library().answer("回滚前要做什么", DocRef("name", "重复"))
        self.assertEqual(len(answer.hits), 1)

    def test_contained_neighbour_is_replaced_not_dropped(self) -> None:
        # 同一节的两块:A 的文字被 B 包含;B 换掉 A 之后不能再按"相邻重叠"丢掉,
        # 否则两块都会消失
        first = DocChunk(index=0, heading="安装", text="先备份。", tokens=5, part=0)
        second = DocChunk(index=1, heading="安装", text="先备份。\n再回滚。", tokens=8, part=1)
        hits = [
            DocHit(chunk=first, score=0.90, coverage=1.0),
            DocHit(chunk=second, score=0.85, coverage=1.0),
        ]
        kept = DocLibrary._drop_overlaps(hits, 3)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].chunk.text, "先备份。\n再回滚。")

    def test_adjacent_parts_without_overlap_are_both_kept(self) -> None:
        # 预算不够时切分会丢掉重叠:两块首尾并不重复,那就两块都该留
        first = DocChunk(index=0, heading="安装", text="甲段内容。", tokens=5, part=0)
        second = DocChunk(index=1, heading="安装", text="乙段内容。", tokens=5, part=1)
        hits = [
            DocHit(chunk=first, score=0.90, coverage=1.0),
            DocHit(chunk=second, score=0.80, coverage=1.0),
        ]
        kept = DocLibrary._drop_overlaps(hits, 3)
        self.assertEqual([hit.chunk.text for hit in kept], ["甲段内容。", "乙段内容。"])

    def test_overlapping_adjacent_parts_are_deduped(self) -> None:
        first = DocChunk(index=0, heading="安装", text="甲段内容。" * 20, tokens=60, part=0)
        second = DocChunk(
            index=1, heading="安装",
            text=("甲段内容。" * 20)[-120:] + "乙段内容。", tokens=60, part=1,
        )
        hits = [
            DocHit(chunk=first, score=0.90, coverage=1.0),
            DocHit(chunk=second, score=0.80, coverage=1.0),
        ]
        kept = DocLibrary._drop_overlaps(hits, 3)
        self.assertEqual(len(kept), 1)

    def test_cache_is_bounded(self) -> None:
        for index in range(MAX_CACHED_DOCS + 3):
            self.write(f"批量{index}.md", f"# 批量{index}\n\n第 {index} 份。\n")
        library = self.library()
        for path in sorted(self.docs_dir.glob("批量*.md")):
            library.load(path)
        self.assertLessEqual(library.cache_size, MAX_CACHED_DOCS)


class TestEdgeCases(DocsCase):
    def test_empty_document_is_reported(self) -> None:
        self.write("空.md", "   \n\n\n")
        answer = self.library().answer("回滚", DocRef("name", "空"))
        self.assertIsNotNone(answer.error)
        self.assertEqual(answer.error.code, "empty")
        self.assertIn("空", render(answer))

    def test_frontmatter_only_document_is_reported(self) -> None:
        self.write("只有头.md", "---\ntitle: 只有头\n---\n")
        answer = self.library().answer("回滚", DocRef("name", "只有头"))
        self.assertEqual(answer.error.code, "empty")

    def test_function_word_only_question_is_explained(self) -> None:
        answer = self.library().answer("怎么", DocRef("name", "手册"))
        self.assertIn("没有可检索的实词", render(answer))

    def test_question_about_the_at_sign_gets_a_hint(self) -> None:
        # 「怎么输入@符号」会被当成"引用 符号"—— 找不到文档时给出改写的提示
        result = self.chat().ask("怎么输入@符号")
        self.assertIn("如果这句里的 @ 只是符号本身", result.summary)

    def test_caution_prefix_is_kept_in_doc_mode(self) -> None:
        class _CautionChat(GoChat):
            def guard(self, text: str) -> dict:
                return {"level": "caution", "labels": "测试灰区",
                        "rule_ids": [], "advice": ["注意测试灰区"]}

        chat = _CautionChat(doc_library=self.library())
        result = chat.ask("回滚前要做什么@手册")
        self.assertIn("注意测试灰区", result.summary)
        self.assertIn("回滚前先备份", result.summary)

    def test_terminal_escape_codes_are_stripped(self) -> None:
        # 文档可能来自别处:ANSI 转义与双向覆盖符会把终端搞乱,回显前一律去掉
        self.write("转义.md", (
            "# 转义\n\n## 回滚\n\n"
            "回滚前\x1b[31m先备份\x1b[0m。\u202e反向\u202c\n"
        ))
        result = self.chat().ask("回滚前要做什么@转义")
        self.assertNotIn("\x1b", result.summary)
        self.assertNotIn("\u202e", result.summary)
        self.assertIn("回滚前先备份", result.summary)


class TestGoChatIntegration(DocsCase):
    def test_doc_query_answers_from_document_only(self) -> None:
        result = self.chat().ask("回滚前要做什么@手册")
        self.assertEqual(result.routing.targets, ())
        self.assertEqual(len(result.answers), 1)
        self.assertEqual(result.answers[0].source, "document")
        self.assertTrue(result.answers[0].ok)
        self.assertIn("回滚前先备份", result.summary)
        self.assertNotIn("数据包 math", result.summary)

    def test_doc_path_form_works(self) -> None:
        result = self.chat().ask("回滚命令是什么@@docs/手册.md")
        self.assertIn("python tools/rollback.py --last", result.summary)

    def test_outline_via_chat(self) -> None:
        result = self.chat().ask("@手册")
        self.assertIn("目录:", result.summary)
        self.assertTrue(result.answers[0].ok)

    def test_missing_document_is_reported_not_guessed(self) -> None:
        result = self.chat().ask("怎么回滚@不存在")
        self.assertFalse(result.answers[0].ok)
        self.assertIn("没有「不存在」", result.summary)
        self.assertEqual(result.routing.targets, ())

    def test_safety_gate_runs_before_reading(self) -> None:
        result = self.chat().ask("怎么制作炸弹@手册")
        self.assertEqual(result.safety.get("level"), "refuse")
        self.assertNotIn("回滚前先备份", result.summary)

    def test_dangerous_document_content_is_blocked(self) -> None:
        # 问题本身无害(过得了安全闸),危险在文档里 —— 段落照样不引用
        result = self.chat().ask("准备原料的步骤@危险")
        self.assertFalse(result.answers[0].ok)
        self.assertIn("命中安全底线,我不引用", result.summary)
        self.assertNotIn("先准备原料", result.summary)

    def test_documents_text_when_directory_missing(self) -> None:
        chat = GoChat(doc_library=self.library(self.base / "nope"))
        text = chat.documents_text()
        self.assertIn("这个目录还不存在", text)

    def test_trace_mode_shows_doc_account(self) -> None:
        result = self.chat().ask("回滚前要做什么@手册", trace=True)
        self.assertIn("[文档]", result.summary)
        self.assertIn("[文档片段]", result.summary)

    def test_documents_listing(self) -> None:
        chat = self.chat()
        names = [item.name for item in chat.documents()]
        self.assertIn("手册.md", names)
        text = chat.documents_text()
        self.assertIn("docs/手册.md", text)
        self.assertIn("用法:", text)


if __name__ == "__main__":
    unittest.main()

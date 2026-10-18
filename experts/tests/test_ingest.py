"""导入笔记:智能分块、确定性 id、来源字段（零依赖）。"""

from __future__ import annotations

import json
import shutil
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from framework import ingest  # noqa: E402


LONG_PARAGRAPH = "部署时先停止写入,备份数据,再切换版本,最后检查健康状态。"


class TestTokenEstimate(unittest.TestCase):
    def test_cjk_counts_per_character(self) -> None:
        self.assertEqual(ingest.estimate_tokens("部署步骤"), 4)

    def test_ascii_is_four_chars_per_token(self) -> None:
        self.assertEqual(ingest.estimate_tokens("abcdefgh"), 2)

    def test_empty(self) -> None:
        self.assertEqual(ingest.estimate_tokens(""), 0)


class TestSmartChunking(unittest.TestCase):
    def test_short_document_without_heading_stays_whole(self) -> None:
        text = "部署就是把新版本发上去。先打包,再上传。"
        chunks = ingest.smart_chunks(text)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].heading, "")
        self.assertEqual(chunks[0].text, text)

    def test_short_document_with_headings_becomes_one_entry_per_section(self) -> None:
        text = "# 部署\n\n先打包,再上传。\n\n# 回滚\n\n切回旧版本。\n"
        chunks = ingest.smart_chunks(text)
        self.assertEqual([chunk.heading for chunk in chunks], ["部署", "回滚"])
        self.assertTrue(all(chunk.tokens <= ingest.MAX_TOKENS_WHOLE for chunk in chunks))

    def test_long_section_is_chunked_under_budget(self) -> None:
        text = "# 部署步骤\n\n" + "\n\n".join(
            f"第{i}步:{LONG_PARAGRAPH}" for i in range(120)
        )
        chunks = ingest.smart_chunks(text)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(chunk.tokens <= ingest.CHUNK_TOKENS for chunk in chunks))
        self.assertTrue(all(chunk.heading == "部署步骤" for chunk in chunks))
        # 同一标题下的后续块 part 递增（用来区分"续块"）
        self.assertEqual(chunks[0].part, 0)
        self.assertEqual(chunks[1].part, 1)

    def test_frontmatter_is_parsed_not_answered(self) -> None:
        text = "---\ntitle: 我的笔记\nkeywords: 部署, 回滚\n---\n\n正文。\n"
        meta, body = ingest.split_frontmatter(text)
        self.assertEqual(meta["title"], "我的笔记")
        self.assertNotIn("title:", body)
        self.assertIn("正文", body)


class TestBuildPack(unittest.TestCase):
    def _pack(self, text: str) -> ingest.ImportResult:
        return ingest.build_pack(
            text,
            source_name="notes.md",
            name="notes",
            title="我的笔记",
        )

    def test_entries_have_source_fields_and_keywords(self) -> None:
        result = self._pack("# 部署步骤\n\n先停止写入,再切换版本。\n")
        self.assertEqual(result.entries, 1)
        entry = result.pack["entries"][0]
        self.assertEqual(entry["source_file"], "notes.md")
        self.assertEqual(entry["source_heading"], "部署步骤")
        self.assertIn("source_quote", entry)
        self.assertIn("部署步骤", entry["keywords"])
        self.assertIn("部署步骤", entry["question"])

    def test_ids_are_deterministic(self) -> None:
        text = "# 部署\n\n甲乙丙丁。\n\n# 回滚\n\n戊己庚辛。\n"
        first = self._pack(text)
        second = self._pack(text)
        self.assertEqual(
            [e["id"] for e in first.pack["entries"]],
            [e["id"] for e in second.pack["entries"]],
        )
        self.assertEqual(first.sha1, second.sha1)

    def test_scope_is_never_empty(self) -> None:
        result = self._pack("# A\n\n内容。\n")
        self.assertTrue(result.pack["expert"]["scope"])

    def test_no_duplicate_questions_or_aliases(self) -> None:
        text = "# 部署\n\n" + "\n\n".join(f"第{i}段内容。" for i in range(30))
        result = self._pack(text)
        keys = []
        for entry in result.pack["entries"]:
            keys.append(entry["question"])
            keys.extend(entry.get("aliases") or ())
        self.assertEqual(len(keys), len(set(keys)))

    def test_write_pack_is_readable(self) -> None:
        result = self._pack("# 部署\n\n先打包。\n")
        target = ROOT / "tests" / ".tmp_ingest" / "demo"
        shutil.rmtree(target.parent, ignore_errors=True)
        self.addCleanup(shutil.rmtree, target.parent, True)
        try:
            path = ingest.write_pack(target, result.pack)
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(raw["expert"]["id"], "notes")
            self.assertEqual(len(raw["entries"]), 1)
            self.assertIn("_import", raw)
        finally:
            shutil.rmtree(target.parent, ignore_errors=True)


class TestCsvImport(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = ROOT / "tests" / ".tmp_csv"
        shutil.rmtree(self._dir, ignore_errors=True)
        self._dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(shutil.rmtree, self._dir, True)

    def _write(self, text: str) -> Path:
        path = self._dir / "qa.csv"
        path.write_text(text, encoding="utf-8")
        return path

    def test_read_pattern_response_header(self) -> None:
        path = self._write(
            "pattern,response\n"
            "what is a chatbot?,A chatbot is a program.\n"
            "recommend a movie,\"Try Inception, it is fun.\"\n"
        )
        rows = ingest.read_csv_pairs(path)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["question"], "what is a chatbot?")
        self.assertIn("Inception", rows[1]["answer"])

    def test_read_question_answer_header(self) -> None:
        path = self._write("question,answer\n量子是什么?,量子是能量的最小单位。\n")
        rows = ingest.read_csv_pairs(path)
        self.assertEqual(rows[0]["question"], "量子是什么?")

    def test_headerless_csv_uses_first_two_columns(self) -> None:
        path = self._write("hello,hi there\nbye,see you\n")
        rows = ingest.read_csv_pairs(path)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["answer"], "hi there")

    def test_build_csv_pack_uses_question_keywords_only(self) -> None:
        path = self._write(
            "pattern,response\n"
            "what is a chatbot?,A chatbot is a program that does a lot of work.\n"
        )
        rows = ingest.read_csv_pairs(path)
        result = ingest.build_csv_pack(
            rows, source_name="qa.csv", name="qa", title="问答样本",
        )
        self.assertEqual(result.kind, "csv")
        self.assertEqual(result.entries, 1)
        entry = result.pack["entries"][0]
        self.assertEqual(entry["answer"], "A chatbot is a program that does a lot of work.")
        self.assertIn("chatbot", entry["keywords"])
        self.assertNotIn("work", entry["keywords"])

    def test_csv_ids_are_deterministic(self) -> None:
        path = self._write("pattern,response\nq1,a1\nq2,a2\n")
        rows = ingest.read_csv_pairs(path)
        first = ingest.build_csv_pack(rows, source_name="qa.csv", name="qa", title="问答")
        second = ingest.build_csv_pack(rows, source_name="qa.csv", name="qa", title="问答")
        self.assertEqual(
            [e["id"] for e in first.pack["entries"]],
            [e["id"] for e in second.pack["entries"]],
        )
        self.assertEqual(first.sha1, second.sha1)


if __name__ == "__main__":
    unittest.main()

"""漏问记录:本地学习队列（写、读、去重、裁剪、清空）。"""

from __future__ import annotations

import shutil
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from framework import learning  # noqa: E402


class TestLearning(unittest.TestCase):
    def setUp(self) -> None:
        # 放在工作区里：Windows 沙箱下系统临时目录的 ACL 可能不可写
        self._dir = ROOT / "tests" / ".tmp_learning"
        shutil.rmtree(self._dir, ignore_errors=True)
        self._dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(shutil.rmtree, self._dir, True)
        self.path = self._dir / "missed.jsonl"

    def test_log_and_read_newest_first(self) -> None:
        self.assertTrue(learning.log_missed("第一个问题", ("第一个", "问题"), path=self.path))
        self.assertTrue(learning.log_missed("第二个问题", ("第二个",), path=self.path))
        records = learning.read_missed(path=self.path)
        self.assertEqual([item["query"] for item in records], ["第二个问题", "第一个问题"])
        self.assertEqual(records[0]["keywords"], ["第二个"])

    def test_duplicate_recent_is_skipped(self) -> None:
        self.assertTrue(learning.log_missed("同一个问题", path=self.path))
        self.assertFalse(learning.log_missed("同一个问题", path=self.path))
        self.assertEqual(len(learning.read_missed(path=self.path)), 1)

    def test_empty_query_is_ignored(self) -> None:
        self.assertFalse(learning.log_missed("   ", path=self.path))
        self.assertEqual(learning.read_missed(path=self.path), [])

    def test_clear_removes_file(self) -> None:
        learning.log_missed("问题", path=self.path)
        self.assertEqual(learning.clear_missed(path=self.path), 1)
        self.assertFalse(self.path.exists())
        self.assertEqual(learning.read_missed(path=self.path), [])

    def test_corrupt_lines_are_skipped(self) -> None:
        self.path.write_text("{bad json}\n\n", encoding="utf-8")
        self.assertEqual(learning.read_missed(path=self.path), [])

    def test_file_is_trimmed_to_limit(self) -> None:
        old_size = learning.MAX_FILE_BYTES
        old_entries = learning.MAX_ENTRIES
        learning.MAX_FILE_BYTES = 1
        learning.MAX_ENTRIES = 3
        try:
            for index in range(6):
                learning.log_missed(f"问题{index}", path=self.path)
            records = learning.read_missed(0, path=self.path)
            self.assertEqual(len(records), 3)
            self.assertEqual(records[0]["query"], "问题5")
        finally:
            learning.MAX_FILE_BYTES = old_size
            learning.MAX_ENTRIES = old_entries


if __name__ == "__main__":
    unittest.main()

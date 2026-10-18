"""检索评测集与指标。"""

from __future__ import annotations

import json
import shutil
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from framework import GoChat, evaluation  # noqa: E402


class TestEvaluationSet(unittest.TestCase):
    def test_bundled_cases_load(self) -> None:
        cases = evaluation.load_cases()
        self.assertGreaterEqual(len(cases), 20)
        self.assertTrue(any(case.must_abstain for case in cases))
        self.assertTrue(any(case.category == "paraphrase" for case in cases))

    def test_bundled_cases_run(self) -> None:
        report = evaluation.run(GoChat())
        self.assertEqual(report.total, 26)
        self.assertEqual(report.positive_total, 21)
        self.assertEqual(report.abstain_total, 5)
        self.assertEqual(report.folder_hit, 21)
        self.assertEqual(report.entry_hit, 21)
        self.assertEqual(report.abstain_ok, 5)
        self.assertAlmostEqual(report.mrr, 1.0, places=3)

    def test_report_counts_failures_honestly(self) -> None:
        path = ROOT / "tests" / ".tmp_eval" / "cases.jsonl"
        shutil.rmtree(path.parent, ignore_errors=True)
        self.addCleanup(shutil.rmtree, path.parent, ignore_errors=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "\n".join([
                json.dumps({"id": "ok", "question": "向量场是什么?",
                            "folder": "math", "entry": "vector-field"}),
                json.dumps({"id": "miss", "question": "怎么给猫剪指甲",
                            "must_abstain": True}),
                json.dumps({"id": "wrong", "question": "向量是什么?",
                            "folder": "physics", "entry": "gravity"}),
            ]) + "\n",
            encoding="utf-8",
        )
        report = evaluation.run(GoChat(), path)
        self.assertEqual(report.total, 3)
        self.assertEqual(report.positive_total, 2)
        self.assertEqual(len(report.failures), 1)
        self.assertIn("wrong", report.render())


if __name__ == "__main__":
    unittest.main()

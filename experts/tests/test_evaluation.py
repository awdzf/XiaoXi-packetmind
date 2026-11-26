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
        self.assertTrue(any(case.category == "typo" for case in cases))

    def test_bundled_cases_run(self) -> None:
        report = evaluation.run(GoChat())
        # 1.0.3 起评测集分两层：路由层 + 答案层。总数从 41 扩到 79，
        # 新增的全是**近域负例**（共享词面但包里没有答案），因为原来 8 条负例
        # 全是整句零重叠的，量不出漂移（见 README「评测」一节）。
        self.assertEqual(report.total, 79)
        self.assertEqual(report.positive_total, 33)
        self.assertEqual(report.abstain_total, 46)
        # 1.0.3：闸门抬到 0.40 且"每个分句都要有证据"之后，两条正例（Java 的虚拟机 /
        # jvmm）仍然答不上来（前者答案引擎挑错条目、后者是双字母错字），
        # 它们留在失败清单里，不藏。
        self.assertEqual(report.folder_hit, 32)
        self.assertEqual(report.entry_hit, 32)
        self.assertAlmostEqual(report.mrr, 0.970, places=3)
        # 路由层仍有 2 条负例被"接住"（对称矩阵 / 黎曼积分），答案层挡住了。
        # 这正是为什么发布前置只看答案层 —— 接住不等于答对。
        self.assertEqual(report.abstain_ok, 44)

    def test_answer_layer_is_measured(self) -> None:
        """答案层必须真的量到：路由全对时，答案层仍可能答错。

        这条测试守的是"尺子"本身 —— 1.0.2 的评测只跑 route()，于是
        「德摩根定律 -> 热力学定律」这种错在评测里完全看不见。
        """
        report = evaluation.run(GoChat())
        self.assertTrue(report.answer_measured)
        # 答案层：错误引用必须是 0（1.0.2 残留 3 条：费马→证明、熵增和热寂→熵、
        # Docker→JVM；1.0.3 分别由"分句全过"与阈值 0.40 挡住）。
        self.assertEqual(report.answer_false, 0)
        self.assertGreaterEqual(report.answer_hit, 30)   # 正例不许比这个低
        # 答案层的弃权数必须高于路由层：闸门就是在"接住了但不该答"这一层生效的
        self.assertGreater(report.answer_abstain_ok, report.abstain_ok)

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

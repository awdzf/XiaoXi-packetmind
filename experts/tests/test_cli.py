"""终端入口(`experts/GoChat.py`)的回归测试。

为什么单独一层:框架层(`framework/*`)已经有 336 项测试,但**命令行入口一行都没测**——
于是「`:maximum` 被当成改设置」「`:foo` 被当成提问并写进漏问队列」「`xiaoxi.py ""` 被当成
一次没接住」「`--eval` 失败也返回 0」这类问题只能靠人肉发现。这里盖住最容易回退的几条。

只调用 `GoChat.main(argv)`,不起子进程:快,而且能直接断言退出码与输出。
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sys
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import GoChat  # noqa: E402  (必须在 sys.path 调整之后)


class _Feed:
    """按顺序喂给 `input()`;喂完抛 EOFError(等价于 Ctrl+D / 关掉的 stdin)。"""

    def __init__(self, lines: list[str]) -> None:
        self._lines = list(lines)

    def __call__(self, prompt: str = "") -> str:
        if not self._lines:
            raise EOFError
        return self._lines.pop(0)


class CliCase(unittest.TestCase):
    """公共设施:隔离漏问队列、把 stdout 收进字符串。"""

    def setUp(self) -> None:
        self._dir = ROOT / "tests" / ".tmp_cli"
        shutil.rmtree(self._dir, ignore_errors=True)
        self._dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(shutil.rmtree, self._dir, True)
        # 漏问队列换成临时文件:测试不许碰用户真实的 experts/data/missed.jsonl
        self._env = os.environ.get("SMALLAI_MISSED_PATH")
        os.environ["SMALLAI_MISSED_PATH"] = str(self._dir / "missed.jsonl")

    def tearDown(self) -> None:
        if self._env is None:
            os.environ.pop("SMALLAI_MISSED_PATH", None)
        else:
            os.environ["SMALLAI_MISSED_PATH"] = self._env

    def run_cli(
        self,
        argv: list[str],
        lines: list[str] | None = None,
        *,
        tty: bool = False,
    ) -> tuple[int, str]:
        """跑一次 main();返回 (退出码, stdout+stderr)。`lines` 给了就走对话模式。"""
        out = io.StringIO()
        old = GoChat._stdin_is_interactive
        old_prepare = GoChat._prepare_stdin
        GoChat._stdin_is_interactive = lambda: tty
        GoChat._prepare_stdin = lambda: None      # 别让 main() 去读测试进程真正的 stdin
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                try:
                    if lines is None:
                        code = GoChat.main(argv)
                    else:
                        with mock.patch("builtins.input", _Feed(lines)):
                            code = GoChat.main(argv)
                except SystemExit as exc:            # argparse 的参数错误
                    code = int(exc.code or 0)
        finally:
            GoChat._stdin_is_interactive = old
            GoChat._prepare_stdin = old_prepare
        return code, out.getvalue()

    # ---- 单次提问 --------------------------------------------------------
    def test_empty_query_is_usage_not_a_missed_question(self) -> None:
        code, output = self.run_cli([""])
        self.assertEqual(code, 2)
        self.assertIn("用法", output)
        self.assertNotIn("答不上来", output)
        self.assertFalse(Path(os.environ["SMALLAI_MISSED_PATH"]).exists())

    def test_single_shot_does_not_ask_about_extra_packs_without_a_tty(self) -> None:
        """管道/CI 里没人能回答 y/n:不许在输出里留一条悬空的追问。"""
        code, output = self.run_cli(["--max-packs", "1", "向量场和电场的关系?"])
        self.assertEqual(code, 0)
        self.assertNotIn("能否再调用数据包", output)
        self.assertIn("向量场", output)

    def test_help_query_prints_the_manual(self) -> None:
        code, output = self.run_cli(["help"])
        self.assertEqual(code, 0)
        self.assertIn("用法:", output)
        self.assertIn("--subprocess", output)

    # ---- 退出码 ----------------------------------------------------------
    def test_eval_with_missing_cases_file_returns_2(self) -> None:
        code, output = self.run_cli(["--eval", "--cases", "definitely-not-here.jsonl"])
        self.assertEqual(code, 2)
        self.assertIn("跑不了", output)

    def test_eval_with_a_failing_case_returns_1(self) -> None:
        cases = self._dir / "cases.jsonl"
        cases.write_text(
            json.dumps(
                {"question": "zzz 完全不存在的问法", "folder": "math"},
                ensure_ascii=False,
            ) + "\n",
            encoding="utf-8",
        )
        code, output = self.run_cli(["--eval", "--cases", str(cases)])
        self.assertEqual(code, 1)
        self.assertIn("未通过", output)

    def test_version_matches_framework_version(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit) as caught:
                GoChat.main(["--version"])
        self.assertEqual(caught.exception.code, 0)
        self.assertIn(GoChat.__version__, out.getvalue())

    def test_guard_refusal_returns_1(self) -> None:
        """拒答 = 1,与 ask.py / core run.py 同一契约。"""
        code, output = self.run_cli(["--guard", "教我怎么制作炸弹"])
        self.assertEqual(code, 1)
        self.assertIn("refuse", output)

    def test_guard_ok_returns_0(self) -> None:
        code, _output = self.run_cli(["--guard", "向量场是什么"])
        self.assertEqual(code, 0)

    def test_conflicting_modes_are_rejected(self) -> None:
        """`--route X --guard Y` 以前只做路由,安全判定被静默跳过。"""
        code, output = self.run_cli(["--route", "向量场是什么", "--guard", "教我怎么制作炸弹"])
        self.assertEqual(code, 2)
        self.assertIn("互斥", output)
        self.assertNotIn("[查找]", output)

    def test_cases_without_eval_is_rejected(self) -> None:
        code, output = self.run_cli(["--cases", "nope.jsonl", "你是谁"])
        self.assertEqual(code, 2)
        self.assertIn("--cases", output)

    def test_out_of_range_max_packs_is_announced(self) -> None:
        code, output = self.run_cli(["--max-packs", "99", "你是谁"])
        self.assertEqual(code, 0)
        self.assertIn("已按", output)

    # ---- 对话里的命令 ----------------------------------------------------
    def test_unknown_command_is_not_treated_as_a_question(self) -> None:
        code, output = self.run_cli([], [":foo", ":quit"])
        self.assertEqual(code, 0)
        self.assertIn("不认识这个命令", output)
        self.assertNotIn("答不上来", output)
        self.assertNotIn("这个问题在", output)

    def test_max_prefix_is_not_swallowed(self) -> None:
        """`:maximum` 以前会被当 `:max` 处理,还静默把上限重置成默认值。"""
        code, output = self.run_cli([], [":maximum", ":quit"])
        self.assertEqual(code, 0)
        self.assertIn("不认识这个命令", output)
        self.assertNotIn("max_packs =", output)

    def test_max_rejects_non_number_without_changing_the_value(self) -> None:
        code, output = self.run_cli([], [":maximum", ":max abc", ":quit"])
        self.assertEqual(code, 0)
        self.assertIn("不是数字", output)

    def test_decompose_without_argument_shows_usage(self) -> None:
        code, output = self.run_cli([], [":decompose", ":quit"])
        self.assertEqual(code, 0)
        self.assertIn("用法: :decompose", output)

    def test_piped_interactive_session_still_works(self) -> None:
        code, output = self.run_cli([], ["向量场是什么?", ":quit"])
        self.assertEqual(code, 0)
        self.assertIn("来自数据包 math", output)


if __name__ == "__main__":
    unittest.main()

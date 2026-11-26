"""跑小析的检索评测集（无模型）。

    python experts/eval.py
    python experts/eval.py --cases experts/evaluation/cases.jsonl

退出码:0 = 没有错误引用;1 = 有负例被当成答案引用了;2 = 评测集本身读不了(路径错 / JSON 坏)。

**为什么只看"错误引用"**：路由层量的是"打算查谁"，答案层才是"真正说出去的话"。
两者在 1.0.3 起用同一道证据闸门，但仍有一处必然的差：路由层不知道答案引擎最后
挑中哪一条。发布前置盯住用户真正拿到的东西 —— **答错了才是错，答不上只是漏**。
正例命中率由 `tests/test_evaluation.py` 守着（低于 30/33 直接测试失败）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from framework import GoChat, evaluation  # noqa: E402


def _force_utf8() -> None:
    """和 GoChat.py / ask.py / pack.py 同一口径:重定向到文件或 CI 时也是 UTF-8。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")   # type: ignore[union-attr]
        except Exception:                                            # noqa: BLE001
            pass


def main(argv: list[str] | None = None) -> int:
    _force_utf8()
    parser = argparse.ArgumentParser(description="小析检索评测")
    parser.add_argument("--cases", default=None, help="cases.jsonl 路径")
    parser.add_argument("--max-packs", type=int, default=None)
    args = parser.parse_args(argv)
    chat = GoChat(max_packs=args.max_packs)
    try:
        report = evaluation.run(chat, args.cases)
    except (OSError, ValueError) as exc:
        # 以前会让 FileNotFoundError 冒到顶层:CI 日志里只有一段 traceback,
        # 看不到"是评测集路径写错了"。
        print(f"[评测] 跑不了：{exc}", file=sys.stderr)
        return 2
    print(report.render())
    if report.answer_measured:
        if report.failures:
            print(f"[提示] 路由层有 {len(report.failures)} 条拟查了证据不足的包,"
                  "答案层已挡住 —— 不阻断发布,但这是路由层与答案层的已知差异。")
        return 1 if report.answer_false else 0
    return 1 if report.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

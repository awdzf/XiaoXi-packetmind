"""跑小析的检索评测集（无模型）。

    python experts/eval.py
    python experts/eval.py --cases experts/evaluation/cases.jsonl
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from framework import GoChat, evaluation  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="小析检索评测")
    parser.add_argument("--cases", default=None, help="cases.jsonl 路径")
    parser.add_argument("--max-packs", type=int, default=None)
    args = parser.parse_args(argv)
    chat = GoChat(max_packs=args.max_packs)
    report = evaluation.run(chat, args.cases)
    print(report.render())
    return 0 if not report.failures else 1


if __name__ == "__main__":
    raise SystemExit(main())

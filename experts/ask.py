"""简单入口:把一句话交给小析,打印总结。

    python experts/ask.py "HTML是什么?"
    python experts/ask.py --list        # 看小析现在装着哪些数据包
    python experts/ask.py --guard "某段文本"

小析是**一个人格**(`experts/core/smallai/persona.py`),知识是若干个数据包
(`experts/packs/*/pack.json`)。这里不自己维护名单,也不自己写分派逻辑 ——
数据包由 `framework/catalog.py` 自动发现,分解/查找/兜底/总结都复用 GoChat,
所以加减知识只需要动一个数据包目录。

要看分解与查找的中间过程,用 `python experts/GoChat.py`(带 --trace)。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from framework import CATALOG, GoChat, persona  # noqa: E402  (必须在 sys.path 调整之后)


def _list() -> int:
    print("=" * 72)
    print(f"{persona.NAME} 现在装着 {len(CATALOG)} 个数据包(自动扫描 experts/packs/*/pack.json)")
    print("=" * 72)
    if not CATALOG:
        print("  一个也没有。放一个 experts/packs/<名字>/pack.json 就会出现。")
    for card in CATALOG:
        role = " · 兜底包" if card.is_general else ""
        print(f"\n数据包 {card.folder}({card.name}){role}")
        print(f"  {card.tagline}")
        print(f"  路径:{card.pack_path}")
        print(f"  条目 {len(card.entries)} 条;例子:")
        for question in card.examples(3):
            print(f"    · {question}")
    print("\n" + "-" * 72)
    print('用法:python experts/ask.py "HTML是什么?"')
    print("     想走「分解 → 查数据包 → 引用」框架:python experts/GoChat.py")
    print("     加/删知识:往 experts/packs/ 里加/删一个数据包目录即可,不用改代码。")
    return 0


def _force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")   # type: ignore[union-attr]
        except Exception:                                            # noqa: BLE001
            pass


def main(argv: list[str] | None = None) -> int:
    _force_utf8()
    parser = argparse.ArgumentParser(
        prog="ask",
        description=f"{persona.NAME}的简单入口:一句话 → 总结",
    )
    parser.add_argument("query", nargs="*", help="要问的句子")
    parser.add_argument("--list", action="store_true", help="列出当前装着的数据包")
    parser.add_argument("--guard", metavar="文本", default=None, help="只做安全判定")
    parser.add_argument("--max-packs", type=int, default=None, metavar="N",
                        help="这一次最多查几个数据包(省略=用 config.json)")
    parser.add_argument("--no-context", action="store_true", help="这一次不启用上下文记忆")
    args = parser.parse_args(argv)

    chat = GoChat(
        max_packs=args.max_packs,
        context=False if args.no_context else None,
    )

    if args.list:
        return _list()
    if args.guard is not None:
        report = chat.guard(args.guard)
        print(f"[安全判定] {report.get('level', 'ok')}")
        if report.get("rule_ids"):
            print("  命中规则:" + "、".join(str(r) for r in report.get("rule_ids") or ()))
        if report.get("labels"):
            print("  类别:" + str(report.get("labels")))
        for advice in report.get("advice") or ():
            print("  建议:" + str(advice))
        if report.get("error"):
            print("  注意:" + str(report["error"]))
        return 1 if report.get("level") == "refuse" else 0

    text = " ".join(args.query).strip()
    if not text:
        print('用法:python experts/ask.py "你的问题"   或   --list 看数据包')
        return 2
    print(chat.ask(text).summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

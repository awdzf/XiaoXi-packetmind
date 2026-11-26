"""删掉"定义了但没人引用"的模块级函数 —— 只做减法，不改行为。

删之前再核一遍：名字在全树里只能出现一次（定义本身），否则拒绝删。
删完由 `python -m unittest discover` 兜底：426 项测试必须仍然全过。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TARGETS = {
    "core/smallai/logic/reasoner.py": ["store_for"],
    "core/smallai/rules/lexicon.py": ["match_by_length"],
    "core/smallai/logic/compare.py": ["match_unit_alias"],
    "core/smallai/validation/units.py": ["supported_kinds"],
    "core/smallai/logic/nlp.py": ["_looks_like_fact", "describe_expr"],
}


def tree_text() -> str:
    me = Path(__file__).resolve()
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in ROOT.rglob("*.py")
        if "__pycache__" not in path.parts and path.resolve() != me
    )


def main() -> None:
    blob = tree_text()
    removed = 0
    for relative, names in TARGETS.items():
        path = ROOT / relative
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
        spans: list[tuple[int, int]] = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
                if blob.count(node.name) > 1:
                    raise SystemExit(f"{relative}:{node.name} 仍被引用，拒绝删除")
                spans.append((node.lineno, node.end_lineno))
        if not spans:
            print(f"  跳过 {relative}：没找到 {names}")
            continue
        lines = text.splitlines()
        for start, end in sorted(spans, reverse=True):
            # 顺手吃掉紧跟着的一个空行，避免留下两个连续空行
            stop = end + 1 if end < len(lines) and not lines[end].strip() else end
            del lines[start - 1:stop]
            removed += stop - start + 1
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"  {relative}: 删 {len(spans)} 个（{', '.join(names)}）")
    print(f"合计删除约 {removed} 行")


if __name__ == "__main__":
    sys.exit(main())

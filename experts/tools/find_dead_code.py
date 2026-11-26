"""找出"定义了但没人用"的模块级函数/类 —— 只做减法，不动行为。

判据故意保守：名字在整个树里（含测试、含 `__all__`、含 `__init__.py` 的转出）
只出现一次 = 只有定义本身。这种才敢删。
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def files():
    for path in ROOT.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        yield path


def main() -> None:
    sources = {path: path.read_text(encoding="utf-8") for path in files()}
    blob = "\n".join(sources.values())

    dead = []
    for path, text in sources.items():
        if path.parts[-2] == "tests":
            continue
        tree = ast.parse(text)
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            name = node.name
            if name.startswith("__"):
                continue
            count = blob.count(name)
            if count <= 1:
                dead.append((str(path.relative_to(ROOT)), name, node.end_lineno - node.lineno + 1))

    dead.sort(key=lambda item: -item[2])
    print(f"只出现一次的名字：{len(dead)} 个")
    for path, name, lines in dead:
        print(f"  {lines:4d} 行  {path}:{name}")


if __name__ == "__main__":
    main()

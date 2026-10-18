"""单核启动器:核心代码只有这一份,知识是一堆数据包目录。

    python experts/core/run.py --pack experts/packs/math "向量场是什么?"
    python experts/core/run.py --pack experts/packs/math --health
    python experts/core/run.py "咖啡是什么?"          # 不指定包 -> 核心自带示例包

    cd experts/packs/math
    python ../../core/run.py "向量场是什么?"           # 从当前目录认包(cwd 里有 pack.json)

为什么需要这个文件:`smallai` 是核心包,必须能被 import。以前每个数据目录里
都放着一份同样的 `run.py` + `smallai/` 副本;现在两者都只有一份,放在 `core/`:
这个文件把 core/ 放进 sys.path,再把参数原样交给 `smallai.cli`。

参数与 `smallai` 的 CLI 完全一致(见 `python run.py --help`),额外支持 `--pack`。
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from smallai.cli import main  # noqa: E402  (必须在 sys.path 调整之后)

if __name__ == "__main__":
    raise SystemExit(main())

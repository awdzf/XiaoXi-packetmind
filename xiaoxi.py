"""从仓库根目录启动小析：

    python xiaoxi.py
    python xiaoxi.py "向量场是什么?"
    python xiaoxi.py --eval
    python xiaoxi.py --version

它只是 `experts/GoChat.py` 的薄包装，方便发布后“一条命令跑起来”。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "experts"))

from GoChat import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())

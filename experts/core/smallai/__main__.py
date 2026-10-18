"""允许 `python -m smallai` 直接跑。"""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())

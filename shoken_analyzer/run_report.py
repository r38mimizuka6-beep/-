#!/usr/bin/env python3
"""エントリポイント。`python run_report.py --help` で使い方が出ます。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from shoken.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python
"""ソースツリーから直接実行するためのエントリポイント。

    python run.py demo --out out/
    python run.py run --input data.csv --out out/
    python run.py check-config
    python run.py verify-audit out/audit_log.json

インストール後は `revenue-risk <command>` でも同じことができる。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from revenue_risk.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())

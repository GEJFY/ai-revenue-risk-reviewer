"""テストパッケージ。src/ をインポートパスに載せる（未インストールでも実行可能に）。"""
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

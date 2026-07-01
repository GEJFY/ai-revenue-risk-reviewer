"""pytest 用のパス設定（src/ をインポート可能にする）。unittest では tests/__init__.py が担う。"""
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

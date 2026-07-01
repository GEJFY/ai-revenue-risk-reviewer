"""合成不正データ生成（検出力評価・モデルリスク管理のバリデーション用）。

`config/rules/fraud_scenarios.yaml` の `synthetic_test_ja` を単一の定義源として、
ラベル付きの模擬不正取引を生成する。検知とテストの定義源を一致させる（rule-authoring スキル）。
"""

from .generator import SyntheticGenerator, InjectedFraud

__all__ = ["SyntheticGenerator", "InjectedFraud"]

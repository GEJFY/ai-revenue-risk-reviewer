"""L4: 機械学習（異常検知）。

外れ値を 0-100 のスコアにし、SHAP で寄与要因を提示する（根拠なきスコア禁止・説明可能性）。
scikit-learn / shap があれば IsolationForest を併用し、無ければ numpy の robust-z 集約で
同等のスコアと寄与（標準化偏差）を返す。モデル・特徴量・バージョンを所見に紐付ける（再現性）。
"""
from __future__ import annotations

import datetime as _dt
import warnings
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from ..contracts.models import SalesTransaction


@contextmanager
def _suppress_all_nan_warning():
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="All-NaN slice encountered")
        yield

# 特徴量の定義（順序が寄与の索引に対応）
_FEATURES = [
    "amount",
    "quantity",
    "unit_price",
    "margin_rate",
    "payment_terms_days",
    "credit_utilization",
    "ship_gap_days",
    "delivery_gap_days",
    "period_end_proximity_days",
    "discount_rate",
]


def _feature_row(t: SalesTransaction, period_end: Optional[_dt.date]) -> List[Optional[float]]:
    rec = t.d_recognition()
    ship = t.d_ship()
    deliv = t.d_delivery()
    margin = None
    if t.unit_price and t.unit_cost is not None and float(t.unit_price) > 0:
        margin = (float(t.unit_price) - float(t.unit_cost)) / float(t.unit_price)
    credit_util = None
    if t.credit_limit:
        credit_util = (float(t.credit_used or 0) + float(t.amount or 0)) / float(t.credit_limit)
    ship_gap = (ship - rec).days if (ship and rec) else None
    deliv_gap = (deliv - rec).days if (deliv and rec) else None
    pe_prox = (period_end - rec).days if (period_end and rec) else None
    return [
        float(t.amount) if t.amount is not None else None,
        float(t.quantity) if t.quantity is not None else None,
        float(t.unit_price) if t.unit_price is not None else None,
        margin,
        float(t.payment_terms_days) if t.payment_terms_days is not None else None,
        credit_util,
        float(ship_gap) if ship_gap is not None else None,
        float(deliv_gap) if deliv_gap is not None else None,
        float(pe_prox) if pe_prox is not None else None,
        float(t.discount_rate) if t.discount_rate is not None else None,
    ]


@dataclass
class AnomalyScore:
    transaction_id: str
    anomaly_score: float           # 0-100
    model_id: str
    shap_top: List[Dict[str, float]] = field(default_factory=list)

    def to_ml_scores(self) -> Dict[str, object]:
        return {
            "anomaly_score": round(self.anomaly_score, 2),
            "model_id": self.model_id,
            "shap_top": [
                {"feature": s["feature"], "contribution": round(s["contribution"], 4)}
                for s in self.shap_top
            ],
        }


class AnomalyModel:
    """異常検知モデル。fit_score で母集団学習と全件スコアリングを行う。"""

    VERSION = "v1"

    def __init__(self, cap_sigma: float = 4.0, feature_version: str = "feat-v1") -> None:
        self.cap_sigma = cap_sigma
        self.feature_version = feature_version
        self._features = list(_FEATURES)
        self._median: Optional[np.ndarray] = None
        self._mad: Optional[np.ndarray] = None
        self._backend = "robust-z"
        self._sk_model = None

    @property
    def model_id(self) -> str:
        return f"{self._backend}-{self.VERSION}/{self.feature_version}"

    def _matrix(self, transactions: Sequence[SalesTransaction], period_ends: Dict[str, _dt.date]) -> np.ndarray:
        rows = [_feature_row(t, period_ends.get(t.period)) for t in transactions]
        arr = np.array([[np.nan if v is None else v for v in row] for row in rows], dtype=float)
        # 欠損を列中央値で補完（全欠損の列は 0 とする。All-NaN 警告は抑止）
        with np.errstate(all="ignore"), _suppress_all_nan_warning():
            col_median = np.nanmedian(arr, axis=0)
        col_median = np.where(np.isnan(col_median), 0.0, col_median)
        inds = np.where(np.isnan(arr))
        arr[inds] = np.take(col_median, inds[1])
        return arr

    def fit_score(
        self, transactions: Sequence[SalesTransaction], period_ends: Optional[Dict[str, _dt.date]] = None
    ) -> Dict[str, AnomalyScore]:
        """母集団で学習し、全件をスコアリングして {transaction_id: AnomalyScore} を返す。"""
        if not transactions:
            return {}
        period_ends = period_ends or {}
        X = self._matrix(transactions, period_ends)

        # robust 標準化（中央値・MAD）。数学的に一定な列でも浮動小数の割り算で MAD が
        # 5e-17 程度の微小非ゼロになりうるため、スケール相対の許容値以下は退化列として 1.0 に置換する
        # （＝丸め誤差を多σの偽異常に増幅しない・根拠なきスコア禁止）。
        self._median = np.median(X, axis=0)
        mad = np.median(np.abs(X - self._median), axis=0)
        tol = 1e-9 * np.maximum(1.0, np.abs(self._median))
        self._mad = np.where(mad <= tol, 1.0, mad * 1.4826)  # 正規分布近似
        Z = (X - self._median) / self._mad

        raw, backend = self._raw_scores(Z)
        self._backend = backend

        # 0-100 正規化（robust sigma を cap_sigma で頭打ち）
        scores = np.clip(raw / self.cap_sigma, 0, 1) * 100.0

        out: Dict[str, AnomalyScore] = {}
        for i, t in enumerate(transactions):
            contrib = np.abs(Z[i])
            order = np.argsort(contrib)[::-1][:3]
            shap_top = [
                {"feature": self._features[j], "contribution": float(contrib[j])}
                for j in order
                if contrib[j] > 0.5
            ]
            out[t.transaction_id] = AnomalyScore(
                transaction_id=t.transaction_id,
                anomaly_score=float(scores[i]),
                model_id=self.model_id,
                shap_top=shap_top,
            )
        return out

    def _raw_scores(self, Z: np.ndarray) -> Tuple[np.ndarray, str]:
        """生の異常度（robust sigma 単位）。sklearn があれば IsolationForest を併用。"""
        rms = np.sqrt(np.mean(Z ** 2, axis=1))  # 全特徴からの総合的な逸脱
        if Z.shape[0] < 8:
            return rms, "robust-z"  # 小標本では IsolationForest は使わない
        try:
            from sklearn.ensemble import IsolationForest  # type: ignore

            model = IsolationForest(random_state=0, contamination="auto")
            model.fit(Z)
            self._sk_model = model
            s = -model.score_samples(Z)  # 高いほど異常
            # rms・cap_sigma と同じ robust-σ 単位へ揃える（中央値・MAD 標準化）。
            # 従来の (s-min)/std は母集団依存の任意スケールで、rms と単位が食い違い
            # sklearn 有無でスコアが不整合になっていた（監査再現性を毀損）。
            med = float(np.median(s))
            mad = float(np.median(np.abs(s - med))) * 1.4826
            if mad <= 1e-12:
                mad = float(np.std(s)) or 1.0
            iso_sigma = np.clip((s - med) / mad, 0, None)  # 正側（異常方向）のみ
            return np.maximum(rms, iso_sigma), "iforest+robust-z"
        except Exception:
            return rms, "robust-z"

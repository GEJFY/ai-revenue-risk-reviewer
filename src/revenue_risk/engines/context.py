"""ルール評価の共有コンテキスト（母集団の事前集計）と RuleHit 型。

detectors.py と rule_engine.py の双方が使う。母集団全体に対する集計（分位点・得意先別統計・
期間境界・分布）をここで一度だけ計算し、各 detector はそれを参照して決定論的に評価する。
"""
from __future__ import annotations

import calendar
import datetime as _dt
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

from ..contracts.models import SalesTransaction


@dataclass
class RuleHit:
    """1ルールの発火。対象取引ID（複数可）と根拠の要約を持つ。"""

    rule_id: str
    transaction_ids: List[str]
    detail_ja: str = ""
    entity_ids: List[str] = field(default_factory=list)


_Q_RE = re.compile(r"(\d{4}).*?Q\s*([1-4])", re.IGNORECASE)
_M_RE = re.compile(r"(\d{4}).*?M\s*(\d{1,2})", re.IGNORECASE)
_YM_RE = re.compile(r"^(?:FY)?(\d{4})[-/](\d{1,2})$", re.IGNORECASE)


def _month_end(year: int, month: int) -> _dt.date:
    last = calendar.monthrange(year, month)[1]
    return _dt.date(year, month, last)


def parse_period_end(period: str) -> Optional[_dt.date]:
    """会計期間ラベルから期末日を推定する（'2025-Q4' / 'FY2025-M09' / '2025-09' 等）。"""
    if not period:
        return None
    m = _Q_RE.search(period)
    if m:
        year, q = int(m.group(1)), int(m.group(2))
        return _month_end(year, q * 3)
    m = _M_RE.search(period)
    if m:
        return _month_end(int(m.group(1)), int(m.group(2)))
    m = _YM_RE.match(period.strip())
    if m:
        return _month_end(int(m.group(1)), int(m.group(2)))
    return None


class Context:
    """母集団の事前集計。すべての detector はこれを引数に取る。"""

    def __init__(self, transactions: Sequence[SalesTransaction], params: Optional[Dict[str, Any]] = None) -> None:
        self.transactions: List[SalesTransaction] = list(transactions)
        self.params: Dict[str, Any] = params or {}
        self._rule_params: Dict[str, Any] = (self.params.get("rules") or {})

        # 索引
        self.by_id: Dict[str, SalesTransaction] = {t.transaction_id: t for t in self.transactions}
        self.by_customer: Dict[str, List[SalesTransaction]] = {}
        self.by_product: Dict[str, List[SalesTransaction]] = {}
        self.by_salesperson: Dict[str, List[SalesTransaction]] = {}
        self.by_entity: Dict[str, List[SalesTransaction]] = {}
        self.by_period: Dict[str, List[SalesTransaction]] = {}
        for t in self.transactions:
            self.by_customer.setdefault(t.customer_id, []).append(t)
            if t.product_id:
                self.by_product.setdefault(t.product_id, []).append(t)
            if t.salesperson_id:
                self.by_salesperson.setdefault(t.salesperson_id, []).append(t)
            self.by_entity.setdefault(t.entity_id, []).append(t)
            self.by_period.setdefault(t.period, []).append(t)

        # 金額分布
        self._amounts = np.array([float(t.amount or 0.0) for t in self.transactions], dtype=float)

        # 母集団の最新認識日（滞留・入金なしの as-of 判定に使用）
        _all_dates = [t.d_recognition() for t in self.transactions if t.d_recognition()]
        self.max_date: Optional[_dt.date] = max(_all_dates) if _all_dates else None

        # 期末日（parsed 優先、無ければ観測最大認識日）
        self._period_end: Dict[str, _dt.date] = {}
        for period, txns in self.by_period.items():
            parsed = parse_period_end(period)
            if parsed is not None:
                self._period_end[period] = parsed
            else:
                dates = [t.d_recognition() for t in txns if t.d_recognition()]
                if dates:
                    self._period_end[period] = max(dates)

        # 得意先の初回認識日（新設・初回取引の代理指標）
        self._customer_first: Dict[str, _dt.date] = {}
        for cid, txns in self.by_customer.items():
            dates = [t.d_recognition() for t in txns if t.d_recognition()]
            if dates:
                self._customer_first[cid] = min(dates)

    # ---- パラメータアクセス --------------------------------------------
    def p(self, rule_id: str, key: str, default: Any) -> Any:
        return (self._rule_params.get(rule_id, {}) or {}).get(key, default)

    def period_common(self, key: str, default: Any) -> Any:
        return (self.params.get("period_end", {}) or {}).get(key, default)

    # ---- 期間・日付ヘルパ ----------------------------------------------
    def period_end(self, period: str) -> Optional[_dt.date]:
        return self._period_end.get(period)

    def period_ends_map(self) -> Dict[str, _dt.date]:
        return dict(self._period_end)

    def is_period_end_booking(self, t: SalesTransaction, n_days: Optional[int] = None) -> bool:
        """認識日が期末最終 n 日以内か。"""
        pe = self.period_end(t.period)
        rec = t.d_recognition()
        if pe is None or rec is None:
            return False
        n = n_days if n_days is not None else int(self.period_common("last_n_days", 5))
        return _dt.timedelta(0) <= (pe - rec) <= _dt.timedelta(days=n)

    def customer_first_date(self, customer_id: str) -> Optional[_dt.date]:
        return self._customer_first.get(customer_id)

    # ---- 分布ヘルパ -----------------------------------------------------
    def amount_quantile(self, q: float) -> float:
        if self._amounts.size == 0:
            return 0.0
        return float(np.quantile(self._amounts, q))

    @staticmethod
    def zscores(values: Sequence[float]) -> np.ndarray:
        arr = np.asarray(values, dtype=float)
        if arr.size == 0:
            return arr
        mu = float(np.mean(arr))
        sd = float(np.std(arr))
        if sd == 0:
            return np.zeros_like(arr)
        return (arr - mu) / sd

    # ---- 業態判定 -------------------------------------------------------
    @staticmethod
    def is_physical_goods(t: SalesTransaction) -> bool:
        """物品取引（出荷・納品の概念があるべき取引）か。役務・ライセンスは False。"""
        if t.channel in ("agent",):
            return False
        if t.performance_obligation_status == "satisfied_over_time":
            return False
        return True

    def is_journal(self, t: SalesTransaction) -> bool:
        """仕訳（journal）として扱うべき対象か。JE 系ルールの適用対象。"""
        if t.is_journal is True:
            return True
        return t.source_system == "manual"

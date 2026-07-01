"""L3: 探索的分析。得意先・製品・担当・時系列・粗利の分布を要約し異常の当たりをつける。

この層自体は所見を確定せず、母集団の姿（集中・偏り・外れ値）を可視化用に集計する。
出力はレポート（reporting）とエージェントの観察フェーズが参照する。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Sequence

import numpy as np

from ..contracts.models import SalesTransaction


@dataclass
class ExploratoryProfile:
    total_amount: float = 0.0
    transaction_count: int = 0
    by_period: Dict[str, float] = field(default_factory=dict)
    top_customers: List[Dict[str, Any]] = field(default_factory=list)
    top_products: List[Dict[str, Any]] = field(default_factory=list)
    customer_concentration_hhi: float = 0.0  # ハーフィンダール指数（集中度）
    margin_stats: Dict[str, float] = field(default_factory=dict)
    period_end_ratio: float = 0.0
    related_party_amount: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_amount": self.total_amount,
            "transaction_count": self.transaction_count,
            "by_period": self.by_period,
            "top_customers": self.top_customers,
            "top_products": self.top_products,
            "customer_concentration_hhi": self.customer_concentration_hhi,
            "margin_stats": self.margin_stats,
            "period_end_ratio": self.period_end_ratio,
            "related_party_amount": self.related_party_amount,
        }


def build_profile(transactions: Sequence[SalesTransaction], top_n: int = 10) -> ExploratoryProfile:
    prof = ExploratoryProfile(transaction_count=len(transactions))
    if not transactions:
        return prof

    total = 0.0
    cust: Dict[str, float] = {}
    prod: Dict[str, float] = {}
    period: Dict[str, float] = {}
    margins: List[float] = []
    rp_amount = 0.0

    for t in transactions:
        amt = float(t.amount or 0.0)
        total += amt
        cust[t.customer_id] = cust.get(t.customer_id, 0.0) + amt
        if t.product_id:
            prod[t.product_id] = prod.get(t.product_id, 0.0) + amt
        period[t.period] = period.get(t.period, 0.0) + amt
        if t.related_party_flag:
            rp_amount += amt
        if t.unit_price and t.unit_cost is not None and float(t.unit_price) > 0:
            margins.append((float(t.unit_price) - float(t.unit_cost)) / float(t.unit_price))

    prof.total_amount = total
    prof.by_period = dict(sorted(period.items()))
    prof.related_party_amount = rp_amount

    def _top(d: Dict[str, float]) -> List[Dict[str, Any]]:
        ranked = sorted(d.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
        return [
            {"id": k, "amount": v, "share": (v / total if total else 0.0)}
            for k, v in ranked
        ]

    prof.top_customers = _top(cust)
    prof.top_products = _top(prod)

    if total > 0:
        shares = np.array(list(cust.values())) / total
        prof.customer_concentration_hhi = float(np.sum(shares ** 2))

    if margins:
        arr = np.array(margins)
        prof.margin_stats = {
            "mean": float(np.mean(arr)),
            "std": float(np.std(arr)),
            "min": float(np.min(arr)),
            "max": float(np.max(arr)),
        }

    return prof

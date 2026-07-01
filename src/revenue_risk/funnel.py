"""コスト・ファネル（高リスク部分集合の選別）。

全件は L2/L4/L5 が決定論的・低コストで処理し、ここで選別した高リスク部分集合のみを
L6 エージェントが深掘りする（docs/architecture.md §3・コスト設計）。順序が逆だと破綻する。

選別ライン（thresholds）:
  - risk_score >= high（既定70）
  - critical ルール発火は閾値に関わらず対象（critical_override）
  - occurrence（実在性）/ cutoff（期間帰属）に紐づく high 以上は閾値未満でも対象（assertion_gate）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Sequence

from .config_loader import Catalog
from .scoring import TransactionScore

_GATE_ASSERTIONS = {"occurrence", "cutoff"}


@dataclass
class FunnelResult:
    selected: Dict[str, List[str]] = field(default_factory=dict)  # txn_id -> reasons
    stats: Dict[str, object] = field(default_factory=dict)

    @property
    def selected_ids(self) -> List[str]:
        return list(self.selected.keys())


def select_high_risk(catalog: Catalog, scores: Dict[str, TransactionScore]) -> FunnelResult:
    high = catalog.high_threshold
    result = FunnelResult()
    by_sev: Dict[str, int] = {}

    for tid, ts in scores.items():
        if ts.severity:
            by_sev[ts.severity] = by_sev.get(ts.severity, 0) + 1
        reasons: List[str] = []
        if ts.risk_score >= high:
            reasons.append(f"risk_score>={high:.0f}")
        if ts.has_critical:
            reasons.append("critical_override")
        if ts.severity in ("high", "critical") and (set(ts.assertions) & _GATE_ASSERTIONS):
            gated = sorted(set(ts.assertions) & _GATE_ASSERTIONS)
            reasons.append(f"assertion_gate({'/'.join(gated)})")
        if reasons:
            result.selected[tid] = reasons

    result.stats = {
        "total": len(scores),
        "selected": len(result.selected),
        "selection_rate": (len(result.selected) / len(scores)) if scores else 0.0,
        "by_severity": by_sev,
        "high_threshold": high,
    }
    return result

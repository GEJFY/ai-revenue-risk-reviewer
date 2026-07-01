"""統合リスクスコアリング。

`config/rules/rule_catalog.yaml` の `risk_scoring` に従い、発火ルールの base_weight を
severity 係数で調整して合算（上限100）し、ML 異常スコア（0-100）と重み付き統合する。
critical ルール発火は閾値に関わらず高リスク扱い（funnel で選別）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from .config_loader import Catalog
from .contracts.models import SEVERITIES
from .engines.context import RuleHit
from .engines.ml_anomaly import AnomalyScore
from .engines.network import NetworkFinding

_SEVERITY_RANK = {s: i for i, s in enumerate(SEVERITIES)}  # low<medium<high<critical


def _max_severity(sevs: Sequence[str]) -> Optional[str]:
    ranked = [s for s in sevs if s in _SEVERITY_RANK]
    if not ranked:
        return None
    return max(ranked, key=lambda s: _SEVERITY_RANK[s])


@dataclass
class TransactionScore:
    transaction_id: str
    rule_ids: List[str] = field(default_factory=list)
    rule_details: List[str] = field(default_factory=list)
    rule_score: float = 0.0
    ml_score: float = 0.0
    ml: Optional[AnomalyScore] = None
    risk_score: float = 0.0
    severity: Optional[str] = None
    assertions: List[str] = field(default_factory=list)
    has_critical: bool = False
    entity_ids: List[str] = field(default_factory=list)


def score_transactions(
    catalog: Catalog,
    rule_hits_by_txn: Dict[str, List[RuleHit]],
    network_findings: Sequence[NetworkFinding],
    ml_scores: Dict[str, AnomalyScore],
    all_txn_ids: Sequence[str],
) -> Dict[str, TransactionScore]:
    blend = catalog.rule_ml_blend
    rw, mw = blend["rule_weight"], blend["ml_weight"]

    # txn -> [(rule_id, detail, entity_ids)]
    contrib: Dict[str, List[tuple]] = {tid: [] for tid in all_txn_ids}
    for tid, hits in rule_hits_by_txn.items():
        for h in hits:
            contrib.setdefault(tid, []).append((h.rule_id, h.detail_ja, h.entity_ids))
    for nf in network_findings:
        for tid in nf.transaction_ids:
            contrib.setdefault(tid, []).append((nf.rule_id, nf.detail_ja, nf.entity_ids))

    scores: Dict[str, TransactionScore] = {}
    for tid in all_txn_ids:
        ts = TransactionScore(transaction_id=tid)
        raw = 0.0
        severities: List[str] = []
        assertions: set = set()
        ent: set = set()
        scored_rules: set = set()  # base_weight は rule_id あたり1回だけ加算（多重計上を防ぐ）
        for rule_id, detail, ent_ids in contrib.get(tid, []):
            rule = catalog.rules.get(rule_id)
            if rule is None:
                continue
            if rule_id not in scored_rules:
                scored_rules.add(rule_id)
                ts.rule_ids.append(rule_id)
                raw += rule.base_weight * catalog.severity_multiplier(rule.severity)
                severities.append(rule.severity)
                assertions.update(rule.assertion)
                if rule.severity == "critical":
                    ts.has_critical = True
            if detail:  # 根拠(detail)は各発火ぶん保持してよい（スコアは重複加算しない）
                ts.rule_details.append(f"[{rule_id}] {detail}")
            ent.update(ent_ids or [])

        ts.rule_score = min(100.0, raw)
        ms = ml_scores.get(tid)
        if ms is not None:
            ts.ml = ms
            ts.ml_score = ms.anomaly_score
        # ML のみで顕著な異常は最低でも medium 扱い
        if not severities and ts.ml_score >= 70:
            severities.append("medium")

        ts.risk_score = min(100.0, rw * ts.rule_score + mw * ts.ml_score)
        ts.severity = _max_severity(severities)
        ts.assertions = sorted(assertions)
        ts.entity_ids = sorted(ent)
        scores[tid] = ts
    return scores

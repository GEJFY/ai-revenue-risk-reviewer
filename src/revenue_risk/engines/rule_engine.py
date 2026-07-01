"""L2: ルールベース評価。65ルールを全件に決定論的適用し、各所見にアサーションを付す。

検知述語は detectors.py（rule_id ごと）、entity_network 系は network.py。ここは両者を束ね、
catalog（メタデータ）と detection_params（しきい値）を与えて評価を実行する。
プライバシー機能の有効化条件（docs/security-privacy.md §3）を engagement で強制する。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from ..config_loader import Catalog, EngagementConfig, load_catalog, load_detection_params, load_engagement
from ..contracts.models import SalesTransaction
from . import detectors as _detectors
from .context import Context, RuleHit

# プライバシー法的基盤が要る機能 -> それを無効化する engagement フラグ
_PRIVACY_GATED = {
    "CUST-005": "officer_customer_matching_enabled",
    "PATT-002": "employee_behavior_analysis_enabled",
}


@dataclass
class RuleEvaluation:
    hits: List[RuleHit] = field(default_factory=list)
    by_txn: Dict[str, List[RuleHit]] = field(default_factory=dict)
    disabled_rules: List[str] = field(default_factory=list)   # プライバシー等で無効化したルール
    unimplemented_rules: List[str] = field(default_factory=list)  # detector 未登録のルール

    def rule_ids_for(self, txn_id: str) -> List[str]:
        return [h.rule_id for h in self.by_txn.get(txn_id, [])]


class RuleEngine:
    def __init__(
        self,
        catalog: Optional[Catalog] = None,
        params: Optional[dict] = None,
        engagement: Optional[EngagementConfig] = None,
    ) -> None:
        self.catalog = catalog or load_catalog()
        self.params = params if params is not None else load_detection_params()
        self.engagement = engagement or load_engagement()

    def _disabled(self) -> List[str]:
        out: List[str] = []
        for rule_id, flag in _PRIVACY_GATED.items():
            if rule_id in self.catalog.rules and not self.engagement.privacy_enabled(flag):
                out.append(rule_id)
        return out

    def evaluate(self, transactions: Sequence[SalesTransaction]) -> RuleEvaluation:
        ctx = Context(transactions, self.params)
        disabled = set(self._disabled())
        result = RuleEvaluation(disabled_rules=sorted(disabled))

        for rule_id, rule in self.catalog.rules.items():
            if rule_id in disabled:
                continue
            if rule_id in _detectors.NETWORK_RULES:
                continue  # network.py が担当
            fn = _detectors.REGISTRY.get(rule_id)
            if fn is None:
                result.unimplemented_rules.append(rule_id)
                continue
            try:
                for hit in fn(ctx):
                    result.hits.append(hit)
                    for tid in hit.transaction_ids:
                        result.by_txn.setdefault(tid, []).append(hit)
            except Exception as exc:  # 1ルールの失敗で全体を止めない
                result.unimplemented_rules.append(f"{rule_id}(error:{exc})")
        return result

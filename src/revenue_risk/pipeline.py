"""パイプライン全体のオーケストレーション（L1→L5 → ファネル → L6 → レポート）。

コスト設計（docs/architecture.md §3）を厳守: 全件は決定論的・低コストの L2/L4/L5 で評価し、
ファネルで選別した高リスク部分集合のみを L6 エージェントが深掘りする。
すべての行動は監査ログ（WORM＋ハッシュチェーン）に記録する。
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from .agent.connectors import MockConnectorProvider, ReadOnlyConnectors
from .agent.orchestrator import AgentOrchestrator, AgentResult
from .audit.audit_log import AuditLog
from .config_loader import (
    Catalog,
    EngagementConfig,
    Scenario,
    load_catalog,
    load_detection_params,
    load_engagement,
    load_scenarios,
)
from .contracts.models import RiskFinding, SalesTransaction
from .engines.context import Context
from .engines.exploratory import ExploratoryProfile, build_profile
from .engines.ml_anomaly import AnomalyModel
from .engines.network import EntityNetwork, NetworkFinding
from .engines.rule_engine import RuleEngine, RuleEvaluation
from .etl.ingest import IngestResult, ingest_records
from .funnel import FunnelResult, select_high_risk
from .findings import build_findings
from .reporting.report import ReportBuilder, ReportBundle
from .scoring import TransactionScore, score_transactions


@dataclass
class PipelineResult:
    ingest: IngestResult
    rule_eval: RuleEvaluation
    ml_scores: Dict[str, Any] = field(default_factory=dict)
    network_findings: List[NetworkFinding] = field(default_factory=list)
    scores: Dict[str, TransactionScore] = field(default_factory=dict)
    funnel: Optional[FunnelResult] = None
    findings: List[RiskFinding] = field(default_factory=list)
    agent_result: Optional[AgentResult] = None
    profile: Optional[ExploratoryProfile] = None
    audit: Optional[AuditLog] = None
    report: Optional[ReportBundle] = None


class Pipeline:
    def __init__(
        self,
        catalog: Optional[Catalog] = None,
        engagement: Optional[EngagementConfig] = None,
        params: Optional[dict] = None,
        scenarios: Optional[Sequence[Scenario]] = None,
    ) -> None:
        self.catalog = catalog or load_catalog()
        self.engagement = engagement or load_engagement()
        self.params = params if params is not None else load_detection_params()
        self.scenarios = list(scenarios or load_scenarios())

    def run(
        self,
        ingest: IngestResult,
        connectors: Optional[ReadOnlyConnectors] = None,
        approvals: Optional[set] = None,
        run_agent: bool = True,
        clock=None,
    ) -> PipelineResult:
        txns = ingest.transactions
        clk = clock or (lambda: _dt.datetime.now(_dt.timezone.utc).isoformat())
        audit = AuditLog(clock=clk)
        audit.append("system", "ingest_complete", target="population",
                     inputs={"count": len(txns), "invalid": ingest.invalid_count})

        # L2 ルール
        rule_engine = RuleEngine(self.catalog, self.params, self.engagement)
        rule_eval = rule_engine.evaluate(txns)
        audit.append("system", "rules_evaluated", inputs={"hits": len(rule_eval.hits),
                                                          "disabled": rule_eval.disabled_rules})

        # L4 ML（異常検知）
        ctx = Context(txns, self.params)
        ml = AnomalyModel().fit_score(txns, ctx.period_ends_map())
        audit.append("system", "ml_scored", inputs={"model": next(iter(ml.values())).model_id if ml else None})

        # L5 ネットワーク
        net = EntityNetwork(txns, self.params)
        network_findings = net.analyze(
            max_hops=int(self.engagement.limit("network_max_hops", 4)),
            max_entities=int(self.engagement.limit("network_max_entities", 200)),
        )
        audit.append("system", "network_analyzed",
                     inputs={"nodes": net.summary()["nodes"], "findings": len(network_findings)})

        # 統合スコア
        order = [t.transaction_id for t in txns]
        scores = score_transactions(self.catalog, rule_eval.by_txn, network_findings, ml, order)

        # ファネル
        funnel = select_high_risk(self.catalog, scores)
        audit.append("system", "funnel_selected", inputs=funnel.stats)

        # 所見の生成
        findings = build_findings(self.catalog, scores, order)
        for f in findings:
            audit.append("system", "finding_created", target=f.finding_id,
                         inputs={"risk_score": f.risk_score, "assertion": f.assertion, "rule_ids": f.rule_ids})

        # L6 エージェント（高リスク部分集合のみ）
        agent_result: Optional[AgentResult] = None
        if run_agent:
            selected_ids = set(funnel.selected_ids)
            selected_findings = [f for f in findings if any(t in selected_ids for t in f.transaction_ids)]
            conns = connectors or ReadOnlyConnectors(MockConnectorProvider(
                transactions=txns,
                legal_basis=self.engagement.privacy.get("legal_basis_note_ja"),
            ))
            orch = AgentOrchestrator(
                connectors=conns, scenarios=self.scenarios, engagement=self.engagement,
                audit_log=audit, clock=clk,
            )
            txn_index = {t.transaction_id: t for t in txns}
            agent_result = orch.investigate(selected_findings, txn_index, approvals=approvals)

        # L3 探索プロファイル
        profile = build_profile(txns)

        # レポート
        report = ReportBuilder(self.catalog, self.engagement).build(
            ingest=ingest, findings=findings, funnel=funnel, profile=profile,
            agent_result=agent_result, audit_log=audit, disabled_rules=rule_eval.disabled_rules,
        )

        return PipelineResult(
            ingest=ingest, rule_eval=rule_eval, ml_scores=ml, network_findings=network_findings,
            scores=scores, funnel=funnel, findings=findings, agent_result=agent_result,
            profile=profile, audit=audit, report=report,
        )


def run_from_records(
    records: List[Dict[str, Any]],
    *,
    gl_totals=None,
    engagement: Optional[EngagementConfig] = None,
    connectors: Optional[ReadOnlyConnectors] = None,
    approvals: Optional[set] = None,
    run_agent: bool = True,
    clock=None,
) -> PipelineResult:
    """dict レコード列から取り込み→評価→レポートまで一気通貫で実行する。"""
    ingest = ingest_records(records, gl_totals=gl_totals)
    return Pipeline(engagement=engagement).run(
        ingest, connectors=connectors, approvals=approvals, run_agent=run_agent, clock=clock
    )

"""L7: レポート生成。

出力:
  - 統合リスクレポート（JSON・機械可読の一次成果物）
  - 経営者・監査役向けサマリ（Markdown・平易な要約）
  - 証跡付き明細（CSV・内部監査/会計監査向け）
  - 改ざん不能の監査ログ（JSON）

すべての所見に根拠（違反ルール／SHAP寄与／収集証憑）とアサーションを付す。
AI は所見を提示するのみで、確定は人間（HITL）である旨を明記する。
"""
from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from ..agent.orchestrator import AgentResult
from ..audit.audit_log import AuditLog
from ..config_loader import Catalog, EngagementConfig
from ..contracts.models import RiskFinding
from ..engines.exploratory import ExploratoryProfile
from ..etl.ingest import IngestResult
from ..funnel import FunnelResult


@dataclass
class ReportBundle:
    report: Dict[str, Any] = field(default_factory=dict)
    summary_md: str = ""
    findings_csv: str = ""
    audit: List[Dict[str, Any]] = field(default_factory=list)
    audit_checkpoint: Dict[str, Any] = field(default_factory=dict)

    def save(self, outdir: str | Path) -> Dict[str, str]:
        out = Path(outdir)
        out.mkdir(parents=True, exist_ok=True)
        paths = {
            "report": str(out / "integrated_report.json"),
            "summary": str(out / "management_summary.md"),
            "findings": str(out / "findings.csv"),
            "audit": str(out / "audit_log.json"),
            "audit_checkpoint": str(out / "audit_log.checkpoint.json"),
        }
        with open(paths["report"], "w", encoding="utf-8") as fh:
            json.dump(self.report, fh, ensure_ascii=False, indent=2)
        with open(paths["summary"], "w", encoding="utf-8") as fh:
            fh.write(self.summary_md)
        with open(paths["findings"], "w", encoding="utf-8", newline="") as fh:
            fh.write(self.findings_csv)
        with open(paths["audit"], "w", encoding="utf-8") as fh:
            json.dump(self.audit, fh, ensure_ascii=False, indent=2)
        with open(paths["audit_checkpoint"], "w", encoding="utf-8") as fh:
            json.dump(self.audit_checkpoint, fh, ensure_ascii=False, indent=2)
        return paths


_SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, None: 4}


class ReportBuilder:
    def __init__(self, catalog: Catalog, engagement: EngagementConfig) -> None:
        self.catalog = catalog
        self.engagement = engagement

    def build(
        self,
        ingest: IngestResult,
        findings: Sequence[RiskFinding],
        funnel: FunnelResult,
        profile: ExploratoryProfile,
        agent_result: Optional[AgentResult] = None,
        audit_log: Optional[AuditLog] = None,
        disabled_rules: Optional[Sequence[str]] = None,
    ) -> ReportBundle:
        findings = sorted(findings, key=lambda f: (_SEV_ORDER.get(f.severity, 4), -f.risk_score))
        chain = audit_log.verify() if audit_log else None

        # アサーション別・カテゴリ別の集計
        assertion_counts: Dict[str, int] = {}
        rule_fire_counts: Dict[str, int] = {}
        sev_counts: Dict[str, int] = {}
        for f in findings:
            for a in f.assertion:
                assertion_counts[a] = assertion_counts.get(a, 0) + 1
            for r in f.rule_ids:
                rule_fire_counts[r] = rule_fire_counts.get(r, 0) + 1
            if f.severity:
                sev_counts[f.severity] = sev_counts.get(f.severity, 0) + 1

        report = {
            "metadata": {
                "generated_by": "revenue_risk (AI) — 所見は提示のみ。確定は人間(HITL)",
                "engagement": {
                    "track": self.engagement.track,
                    "deployment_layer": self.engagement.deployment_layer,
                },
                "thresholds": self.catalog.thresholds,
                "rule_ml_blend": self.catalog.rule_ml_blend,
                "catalog_version": self.catalog.version,
            },
            "population": {
                "transaction_count": ingest.transactions and len(ingest.transactions) or 0,
                "total_amount": profile.total_amount,
                "period_coverage": ingest.period_coverage,
            },
            "data_quality": {
                "invalid_count": ingest.invalid_count,
                "missing_summary": ingest.missing_summary,
                "sequence_gaps": ingest.sequence_gaps,
                "sequence_duplicates": ingest.sequence_duplicates,
                "missing_periods": ingest.missing_periods,
                "gl_reconciliation": ingest.gl_reconciliation,
                "reconciled_to_gl": ingest.reconciled(),
            },
            "funnel": funnel.stats,
            "coverage": {
                "rules_fired": sorted(rule_fire_counts.keys()),
                "rules_fired_count": len(rule_fire_counts),
                "rule_fire_counts": rule_fire_counts,
                "disabled_rules": list(disabled_rules or []),
            },
            "breakdown": {
                "by_severity": sev_counts,
                "by_assertion": assertion_counts,
            },
            "exploratory": profile.to_dict(),
            "agent": (agent_result.stats if agent_result else {}),
            "audit": {
                "entries": (chain.entries_checked if chain else 0),
                "chain_valid": (chain.valid if chain else None),
                "problems": (chain.problems if chain else []),
            },
            "findings": [self._finding_dict(f, funnel) for f in findings],
            "hitl_notice_ja": (
                "本レポートの所見はAIによる提示であり確定ではない。確定・棄却・是正・通報・開示は "
                "独立した人間（監査人・監査役・経営者）が判断する（RACIでAIは説明責任Aを持たない）。"
            ),
        }

        return ReportBundle(
            report=report,
            summary_md=self._summary_md(report, findings),
            findings_csv=self._findings_csv(findings, funnel),
            audit=(audit_log.to_list() if audit_log else []),
            audit_checkpoint=(audit_log.checkpoint() if audit_log else {}),
        )

    def _finding_dict(self, f: RiskFinding, funnel: FunnelResult) -> Dict[str, Any]:
        d = f.to_dict()
        d["selected_for_deepdive"] = f.finding_id.replace("F-", "") in funnel.selected or any(
            tid in funnel.selected for tid in f.transaction_ids
        )
        d["funnel_reasons"] = next(
            (funnel.selected[tid] for tid in f.transaction_ids if tid in funnel.selected), []
        )
        return d

    def _summary_md(self, report: Dict[str, Any], findings: Sequence[RiskFinding]) -> str:
        pop = report["population"]
        dq = report["data_quality"]
        fn = report["funnel"]
        sev = report["breakdown"]["by_severity"]
        lines: List[str] = []
        lines.append("# 売上・収益リスク分析 サマリ（経営者・監査役向け）\n")
        lines.append(
            "> 本サマリはAIエージェントによる**所見の提示**です。確定・是正・通報・開示は"
            "独立した人間（監査人・監査役・経営者）が判断します（HITL）。\n"
        )
        lines.append("## 母集団と完全性")
        lines.append(f"- 取引件数: **{pop['transaction_count']:,}** 件 / 売上合計: {pop['total_amount']:,.0f}")
        lines.append(f"- 対象期間: {', '.join(pop['period_coverage']) or 'n/a'}")
        recon = dq["reconciled_to_gl"]
        recon_txt = "突合済み" if recon else ("未突合/不一致あり" if dq["gl_reconciliation"] else "GL情報未提供")
        lines.append(f"- 総勘定元帳との突合（網羅性の裏付け）: **{recon_txt}**")
        if dq["sequence_gaps"]:
            lines.append(f"- 連番の欠番（改ざん/計上漏れの兆候）: {len(dq['sequence_gaps'])} 箇所")
        if dq.get("sequence_duplicates"):
            lines.append(f"- 連番の重複・再利用（二重計上/架空の兆候）: {len(dq['sequence_duplicates'])} 件")
        if dq.get("missing_periods"):
            lines.append(f"- 欠落した期間（網羅性ギャップ）: {', '.join(dq['missing_periods'])}")
        if dq["invalid_count"]:
            lines.append(f"- スキーマ不適合（データ品質の限界）: {dq['invalid_count']} 件（所見に data_quality を付記）")
        lines.append("")
        lines.append("## リスクの絞り込み（ファネル）")
        lines.append(
            f"- 全 {fn.get('total', 0):,} 件を決定論的に評価し、高リスク **{fn.get('selected', 0):,}** 件"
            f"（{fn.get('selection_rate', 0):.1%}）をエージェント深掘り・人間レビュー対象に選別。"
        )
        lines.append(f"- 重要度別: " + (", ".join(f"{k}={v}" for k, v in sev.items()) or "該当なし"))
        lines.append("")
        lines.append("## 主要な所見（重要度順・上位10）")
        lines.append("各所見は財務諸表アサーション（発生・網羅性・正確性・期間帰属 等）に紐づきます。\n")
        lines.append("| 所見ID | 重要度 | スコア | アサーション | 概要 |")
        lines.append("|---|---|---|---|---|")
        for f in list(findings)[:10]:
            summary = f.rationale.get("summary_ja", "")[:60].replace("|", "／")
            lines.append(
                f"| {f.finding_id} | {f.severity or '-'} | {f.risk_score:.0f} | "
                f"{', '.join(f.assertion) or '-'} | {summary} |"
            )
        if not findings:
            lines.append("| — | — | — | — | 高リスク所見は検出されませんでした |")
        lines.append("")
        cov = report["coverage"]
        if cov["disabled_rules"]:
            lines.append(
                f"> 注: プライバシー法的基盤が未充足のため、次のルールは無効化されています: "
                f"{', '.join(cov['disabled_rules'])}（従業員/役職員データの取扱いは適法根拠が前提）。\n"
            )
        eng = report["metadata"]["engagement"]
        lines.append(
            f"> 独立性: track={eng['track']} / 配備={eng['deployment_layer']}。"
            "売上不正は財務諸表不正であり、確定と是正の説明責任は独立した立場に置きます（要法務確認）。"
        )
        return "\n".join(lines) + "\n"

    def _findings_csv(self, findings: Sequence[RiskFinding], funnel: FunnelResult) -> str:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([
            "finding_id", "transaction_ids", "entity_ids", "risk_score", "severity",
            "assertions", "rule_ids", "hitl_status", "created_by",
            "selected_for_deepdive", "hypothesis_ja", "recommended_review_ja", "summary_ja",
        ])
        for f in findings:
            selected = any(tid in funnel.selected for tid in f.transaction_ids)
            writer.writerow([
                f.finding_id,
                "|".join(f.transaction_ids),
                "|".join(f.entity_ids),
                f"{f.risk_score:.2f}",
                f.severity or "",
                "|".join(f.assertion),
                "|".join(f.rule_ids),
                f.hitl_status,
                f.created_by,
                "yes" if selected else "no",
                (f.hypothesis_ja or "").replace("\n", " "),
                (f.recommended_review_ja or "").replace("\n", " "),
                (f.rationale.get("summary_ja", "") or "").replace("\n", " "),
            ])
        return buf.getvalue()

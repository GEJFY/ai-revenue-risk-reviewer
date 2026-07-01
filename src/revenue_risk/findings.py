"""スコアから RiskFinding を組み立てる。

各所見は財務諸表アサーションと根拠（rationale）を必須で持つ（絶対原則 #2・説明可能性）。
生成主体（created_by）を rule_engine / ml_model に区別する。AI は hitl_status を open までしか
設定しない（確定は人間のみ・絶対原則 #3）。エージェント層が後段でこれらを in_review に進め、
仮説・証憑・推奨手続を付与する。
"""
from __future__ import annotations

from typing import Dict, List, Sequence

from .config_loader import Catalog
from .contracts.models import RiskFinding
from .scoring import TransactionScore


# ML 単独（ルール非発火）の所見に付す既定アサーション。
# 説明できない売上異常は、まず実在性(occurrence)と正確性(accuracy)を脅かす。
# 具体的アサーションはレビュー担当が判断する（rationale に明記）。
_ML_ONLY_DEFAULT_ASSERTIONS = ["occurrence", "accuracy"]


def build_findings(
    catalog: Catalog,
    scores: Dict[str, TransactionScore],
    order: Sequence[str],
) -> List[RiskFinding]:
    high = catalog.high_threshold
    findings: List[RiskFinding] = []
    for tid in order:
        ts = scores.get(tid)
        if ts is None:
            continue
        # ルール発火は必ず所見化。ML 単独は high 以上のみ（弱い異常でノイズを出さない）。
        ml_only = not ts.rule_ids
        has_signal = bool(ts.rule_ids) or ts.ml_score >= high
        if not has_signal:
            continue

        created_by = "ml_model" if ml_only else "rule_engine"

        summary_parts: List[str] = []
        if ts.rule_details:
            summary_parts.extend(ts.rule_details[:3])
        if ml_only:
            top = ", ".join(s["feature"] for s in (ts.ml.shap_top if ts.ml else []))
            summary_parts.append(f"ML異常スコア {ts.ml_score:.0f}（寄与: {top or 'n/a'}）")
        summary = " / ".join(summary_parts) or "リスク兆候あり"

        assertions = list(ts.assertions)
        rationale = {"summary_ja": summary, "rule_violations": list(ts.rule_ids)}
        if not assertions:
            # 絶対原則 #2: 根拠なきスコア・所見を出さない。空でも必ずアサーションを付すが、
            # 由来（ML 単独 vs ルール発火だが assertion 未定義）を取り違えない。
            assertions = list(_ML_ONLY_DEFAULT_ASSERTIONS)
            if ml_only:
                rationale["assertion_note_ja"] = "MLが説明困難な異常を検出。具体的アサーションはレビュー担当が確定する。"
            else:
                # ルールは発火したがそのルールに assertion が未定義（設定不備）。ML由来を偽装しない。
                rationale["assertion_note_ja"] = "ルール発火だが当該ルールに assertion 未定義（設定不備）。レビュー担当が確定する。"

        finding = RiskFinding(
            finding_id=f"F-{tid}",
            transaction_ids=[tid],
            entity_ids=list(ts.entity_ids),
            rule_ids=list(ts.rule_ids),
            ml_scores=(ts.ml.to_ml_scores() if ts.ml and ts.ml_score >= high else {}),
            risk_score=round(ts.risk_score, 2),
            severity=ts.severity or ("medium" if ml_only else None),
            assertion=assertions,
            rationale=rationale,
            hitl_status="open",
            created_by=created_by,
        )
        findings.append(finding)
    # リスクスコア降順
    findings.sort(key=lambda f: f.risk_score, reverse=True)
    return findings

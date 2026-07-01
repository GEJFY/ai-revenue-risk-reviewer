"""L6: 自律AIエージェント（5フェーズループ）。

高リスク部分集合（ファネル選別済み）の各所見について、観察→仮説生成→探索→検証→統合 を反復する。
- ツールは read-only・最小権限。証憑からツールを発火させない（計画側の判断でのみ起動）。
- HITL ゲート G0（プライバシー収集前）/ G1（高感度探索前）を尊重。未承認なら該当ツールを実行しない。
- 証憑コンテンツはインジェクション走査し、埋め込み命令には従わない。一次データとの矛盾はフラグ。
- エージェントは所見を提示するのみ。hitl_status を confirmed/dismissed にはできない（in_review まで）。

決定論的に動作する（LLM 非依存）。実運用で LLM を差す場合も、ツール起動判断と HITL 不変条件は
このオーケストレータが保持する。
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set

from ..config_loader import EngagementConfig, Scenario, load_engagement, load_scenarios
from ..contracts.models import Evidence, RiskFinding, SalesTransaction
from ..audit.audit_log import AuditLog
from .connectors import (
    PRIVACY_TOOLS,
    SENSITIVE_TOOLS,
    TOOL_EVIDENCE_TYPE,
    UNTRUSTED_TOOLS,
    MockConnectorProvider,
    ReadOnlyConnectors,
)

#: 1所見あたりに計画するツールの最大数（探索コストの上限）
_MAX_PLAN_TOOLS = 8
#: 全体の暴走防止セーフティ（1回の investigate 全体でのツール呼び出し上限）
_GLOBAL_TOOL_SAFETY = 2000
from .injection import scan_for_injection


@dataclass
class AgentResult:
    findings: List[RiskFinding] = field(default_factory=list)
    evidence: List[Evidence] = field(default_factory=list)
    stats: Dict[str, object] = field(default_factory=dict)


class ScenarioMatcher:
    """所見の rule_ids / category から関連する不正シナリオを引く。"""

    def __init__(self, scenarios: Sequence[Scenario]) -> None:
        self.scenarios = list(scenarios)

    def match(self, rule_ids: Sequence[str]) -> List[Scenario]:
        rid = set(rule_ids)
        scored = []
        for sc in self.scenarios:
            overlap = len(set(sc.linked_rules) & rid)
            if overlap:
                scored.append((overlap, sc))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [sc for _, sc in scored]


class AgentOrchestrator:
    def __init__(
        self,
        connectors: Optional[ReadOnlyConnectors] = None,
        scenarios: Optional[Sequence[Scenario]] = None,
        engagement: Optional[EngagementConfig] = None,
        audit_log: Optional[AuditLog] = None,
        clock=None,
    ) -> None:
        self.connectors = connectors or ReadOnlyConnectors(MockConnectorProvider())
        self.matcher = ScenarioMatcher(scenarios or load_scenarios())
        self.engagement = engagement or load_engagement()
        self.audit = audit_log or AuditLog()
        self._clock = clock or (lambda: _dt.datetime.now(_dt.timezone.utc).isoformat())

    # ---- ゲート判定 -----------------------------------------------------
    def _gate_allows(self, tool: str, approvals: Set[str]) -> bool:
        hitl = self.engagement.hitl
        if tool in PRIVACY_TOOLS and hitl.get("require_g0_privacy_approval", True) and "G0" not in approvals:
            return False
        if tool in SENSITIVE_TOOLS and hitl.get("require_g1_sensitive_approval", True) and "G1" not in approvals:
            return False
        return True

    # ---- メイン ---------------------------------------------------------
    def investigate(
        self,
        findings: Sequence[RiskFinding],
        txn_index: Dict[str, SalesTransaction],
        approvals: Optional[Set[str]] = None,
    ) -> AgentResult:
        approvals = set(approvals or set())
        result = AgentResult(findings=list(findings))
        tool_calls = 0
        gated_skips = 0
        injections = 0
        contradictions = 0
        verdicts: Dict[str, int] = {}

        max_iter = int(self.engagement.limit("max_iterations", 5))
        max_calls = int(self.engagement.limit("max_tool_calls", 20))

        skipped_decided = 0
        for finding in findings:
            # 人間が確定/棄却済みの所見はエージェントが再処理しない（人間判断を覆さない・絶対原則 #3）
            if finding.hitl_status in ("confirmed", "dismissed"):
                skipped_decided += 1
                self.audit.append("agent", "skip_human_decided", target=finding.finding_id,
                                  inputs={"hitl_status": finding.hitl_status})
                continue
            primary = self._primary_txn(finding, txn_index)
            if primary is None:
                continue

            # --- 観察 ---
            self.audit.append("agent", "observe", target=finding.finding_id,
                              inputs={"rule_ids": finding.rule_ids, "txns": finding.transaction_ids})

            # --- 仮説生成 ---
            scs = self.matcher.match(finding.rule_ids)
            top = scs[0] if scs else None
            if top:
                finding.hypothesis_ja = top.hypothesis_ja
                for a in top.assertion:
                    if a not in finding.assertion:
                        finding.assertion.append(a)
            planned_tools = self._plan_tools(scs)
            self.audit.append("agent", "hypothesize", target=finding.finding_id,
                              inputs={"scenario": top.id if top else None, "planned_tools": planned_tools})

            # --- 探索（read-only・ゲート尊重・上限あり）---
            collected: List[Evidence] = []
            responses: Dict[str, "object"] = {}
            per_finding_calls = 0
            for it in range(max_iter):
                new_call = False
                for tool in planned_tools:
                    # max_calls は「1所見あたり」の上限（docs/agent-design.md §4）。全体は暴走防止のみ。
                    if per_finding_calls >= max_calls or tool_calls >= _GLOBAL_TOOL_SAFETY:
                        break
                    if not self._gate_allows(tool, approvals):
                        gated_skips += 1
                        self.audit.append("agent", "gate_blocked", target=finding.finding_id,
                                          inputs={"tool": tool, "reason": "HITL未承認(G0/G1)"})
                        continue
                    resp = self.connectors.call(tool, primary)
                    responses[tool] = resp
                    tool_calls += 1
                    per_finding_calls += 1
                    new_call = True
                    self.audit.append("agent", "tool_call", target=tool,
                                      inputs={"finding": finding.finding_id, "provenance": resp.provenance})

                    inj_flags: List[str] = []
                    if resp.is_untrusted and resp.content:
                        scan = scan_for_injection(resp.content)
                        if scan.suspected:
                            injections += 1
                            inj_flags = scan.flags
                    ev = Evidence(
                        evidence_id=f"EV-{finding.finding_id}-{tool.replace('.', '_')}-{per_finding_calls}",
                        finding_id=finding.finding_id,
                        type=TOOL_EVIDENCE_TYPE.get(tool, "other"),
                        source=resp.source,
                        provenance=resp.provenance,
                        collected_at=self._clock(),
                        legal_basis=resp.legal_basis,
                        injection_flags=inj_flags,
                        content_summary_ja=(resp.content[:120] if resp.content else self._summ(resp.data)),
                    )
                    collected.append(ev)
                    result.evidence.append(ev)
                    self.audit.append("agent", "evidence_collected", target=ev.evidence_id,
                                      inputs={"tool": tool, "injection_flags": inj_flags})
                # 反復停止: これ以上新しい呼び出しが無い / 上限
                if not new_call:
                    break
                # 単純化: 計画ツールを一巡したら十分（決定論・無限探索の禁止）
                break

            # --- 検証 ---
            verdict, notes = self._verify(finding, primary, collected, responses)
            verdicts[verdict] = verdicts.get(verdict, 0) + 1
            if notes.get("contradiction"):
                contradictions += 1
            self.audit.append("agent", "verify", target=finding.finding_id,
                              inputs={"verdict": verdict, **notes})

            # --- 統合（提示まで。確定はしない）---
            finding.rationale.setdefault("evidence_refs", [])
            finding.rationale["evidence_refs"] = [e.evidence_id for e in collected]
            finding.recommended_review_ja = self._recommend(finding, verdict, notes)
            # エージェントは in_review までしか設定できない（confirmed は人間のみ）
            finding.set_hitl_status("in_review", actor_is_human=False)
            self.audit.append("agent", "finding_updated", target=finding.finding_id,
                              inputs={"hitl_status": finding.hitl_status, "verdict": verdict})

        result.stats = {
            "tool_calls": tool_calls,
            "gated_skips": gated_skips,
            "skipped_human_decided": skipped_decided,
            "injections_detected": injections,
            "contradictions_detected": contradictions,
            "verdicts": verdicts,
            "audit_entries": len(self.audit),
        }
        return result

    # ---- 補助 -----------------------------------------------------------
    @staticmethod
    def _primary_txn(finding: RiskFinding, idx: Dict[str, SalesTransaction]) -> Optional[SalesTransaction]:
        for tid in finding.transaction_ids:
            if tid in idx:
                return idx[tid]
        return None

    def _plan_tools(self, scenarios: Sequence[Scenario]) -> List[str]:
        """関連シナリオの connectors を計画する。

        非構造（untrusted: comms/contract/delivery）ツールは、いずれかの関連シナリオが参照する限り
        必ず計画に含める ── 攻撃者が仕込みうる証憑を必ず走査してインジェクションを検出するため。
        残りは重なりの高いシナリオ順に埋め、_MAX_PLAN_TOOLS で頭打ち（探索コストの上限）。
        """
        ordered: List[str] = []
        for sc in scenarios:  # overlap 降順に整列済み
            for c in sc.connectors:
                if c and c not in ordered:
                    ordered.append(c)
        untrusted = [t for t in ordered if t in UNTRUSTED_TOOLS]
        others = [t for t in ordered if t not in UNTRUSTED_TOOLS]
        return (untrusted + others)[:_MAX_PLAN_TOOLS]

    @staticmethod
    def _summ(data: Dict[str, object]) -> str:
        return ", ".join(f"{k}={v}" for k, v in data.items() if v is not None)[:120]

    def _verify(self, finding: RiskFinding, primary: SalesTransaction,
                evidence: List[Evidence], responses: Dict[str, object]):
        """証憑（ConnectorResponse）と一次データを突き合わせ、仮説の支持/反証を判断する。"""
        support: List[str] = []
        refute: List[str] = []
        notes: Dict[str, object] = {}

        # インジェクションが検出されたら、命令には従わずリスクを維持
        if any(e.injection_flags for e in evidence):
            support.append("証憑に埋め込み命令の疑い（指示に従わずリスク評価を維持）")
            notes["injection"] = True

        ship = responses.get("shipment.lookup")
        bank = responses.get("bank.receipt_match")
        cust = responses.get("customer.verify")
        sanc = responses.get("sanctions.lookup")
        comms = responses.get("comms.search")

        is_cutoff = "cutoff" in set(finding.assertion)

        # 出荷: 「存在」だけで反証にしない。期間帰属では時系列（出荷日 vs 認識日）を評価する。
        # 出荷記録が一次データの自己申告に過ぎない場合、実在性の反証は弱く、期間帰属は決して反証しない。
        if ship is not None:
            rec = primary.d_recognition()
            shipd = primary.d_ship()
            if not ship.found:
                support.append("出荷証憑なし（実在性に疑義）")
            elif shipd is not None and rec is not None and shipd > rec:
                support.append(f"出荷日{shipd}が収益認識日{rec}より後（未出荷計上＝期間帰属・実在性に疑義）")
                notes["cutoff_gap"] = True
            elif not is_cutoff:
                # 出荷記録あり・時系列整合。実在性の弱い反証（独立照会なら確度が上がる）
                refute.append("出荷証憑あり（時系列整合）")

        # 入金: 期間帰属の反証にはしない（実在性のみに弱く効く）。
        if bank is not None:
            if not bank.found:
                support.append("対応入金なし")
            elif not is_cutoff:
                refute.append("対応入金あり")

        if cust is not None and cust.data.get("registry_verified") is False:
            support.append("得意先の登記/所在が未確認（実在性に疑義）")
        if sanc is not None and sanc.data.get("hit"):
            support.append("反社/制裁スクリーニング該当")

        # 証憑（非構造）と一次データの矛盾検出（SC-RED-01）
        if comms is not None and comms.content:
            claims_delivered = ("納品" in comms.content or "出荷" in comms.content or "delivered" in comms.content.lower())
            if claims_delivered and ship is not None and not ship.found:
                support.append("通信は納品済と主張するが出荷記録なし（証憑と一次データの矛盾）")
                notes["contradiction"] = True

        if support:
            verdict = "supported"
        elif refute and not support:
            verdict = "refuted"
        else:
            verdict = "inconclusive"
        notes["support"] = support
        notes["refute"] = refute
        return verdict, notes

    @staticmethod
    def _recommend(finding: RiskFinding, verdict: str, notes: Dict[str, object]) -> str:
        # 助言表現に限定し、是正指示は含めない（RACI: 確定・是正は独立した人間）
        base = "推奨手続: "
        if verdict == "supported":
            base += "収集証憑が仮説を支持。実在性・期間帰属・仕訳の妥当性を重点的に確認することを推奨"
        elif verdict == "refuted":
            base += "収集証憑は正当性を支持する方向。念のため根拠証憑の整合を確認"
        else:
            base += "証憑が不十分。追加の出荷・入金・契約証憑の入手を検討"
        if notes.get("injection"):
            base += "（注: 証憑に埋め込み命令の疑い。内容を指示として扱わないこと）"
        if notes.get("contradiction"):
            base += "（注: 証憑と一次データに矛盾あり）"
        return base

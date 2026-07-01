"""エンドツーエンド: 合成不正の検出力（recall）・監査ログ・レポート・HITL 不変条件。

これがモデルリスク管理（docs/governance.md §3）のバリデーションに相当する検証手段。
"""
import unittest

from revenue_risk.agent.connectors import MockConnectorProvider, ReadOnlyConnectors
from revenue_risk.etl.ingest import ingest_records
from revenue_risk.pipeline import Pipeline
from revenue_risk.synthetic.generator import SyntheticGenerator


class EndToEndTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        gen = SyntheticGenerator(seed=42)
        txns, injected = gen.generate()
        cls.injected = injected
        ingest = ingest_records([t.to_dict() for t in txns])
        conns = ReadOnlyConnectors(MockConnectorProvider(
            transactions=ingest.transactions, comms_store=gen.comms_store))
        cls.result = Pipeline().run(ingest, connectors=conns, approvals={"G0", "G1"})

    def test_recall_all_scenarios_detected(self):
        flagged = {t for f in self.result.findings for t in f.transaction_ids}
        missed = [inj.scenario_id for inj in self.injected if not (set(inj.transaction_ids) & flagged)]
        self.assertEqual(missed, [], f"未検出シナリオ: {missed}")

    def test_audit_chain_valid(self):
        self.assertTrue(self.result.audit.verify().valid)

    def test_no_finding_confirmed_by_ai(self):
        # AI は確定できない。すべて open / in_review のいずれか
        for f in self.result.findings:
            self.assertIn(f.hitl_status, ("open", "in_review"))

    def test_every_finding_has_assertion_and_rationale(self):
        for f in self.result.findings:
            self.assertTrue(f.assertion, f"{f.finding_id}: アサーション必須")
            self.assertTrue(f.rationale.get("summary_ja"), f"{f.finding_id}: 根拠必須")

    def test_injection_and_contradiction_detected(self):
        stats = self.result.agent_result.stats
        self.assertGreaterEqual(stats["injections_detected"], 1)
        self.assertGreaterEqual(stats["contradictions_detected"], 1)

    def test_funnel_reduces_population(self):
        stats = self.result.funnel.stats
        self.assertLess(stats["selected"], stats["total"])
        self.assertGreater(stats["selected"], 0)

    def test_report_bundle_complete(self):
        rb = self.result.report
        self.assertIn("findings", rb.report)
        self.assertTrue(rb.summary_md.strip())
        self.assertTrue(rb.findings_csv.startswith("finding_id"))
        self.assertTrue(rb.audit)

    def test_network_findings_present(self):
        rids = {nf.rule_id for nf in self.result.network_findings}
        self.assertIn("CIRC-006", rids)  # 循環取引が検出される


class GlReconciliationE2ETest(unittest.TestCase):
    def test_reconciled_flag_in_report(self):
        gen = SyntheticGenerator(seed=1)
        txns, _ = gen.generate()
        total = sum(float(t.amount) for t in txns if t.entity_id == "E1" and t.period == "2025-Q4")
        # E1/2025-Q4 のみ突合（他区分は未提供）
        gl = {("E1", "2025-Q4"): total}
        ingest = ingest_records([t.to_dict() for t in txns], gl_totals=gl)
        recon = {(r["entity_id"], r["period"]): r["reconciled"] for r in ingest.gl_reconciliation}
        self.assertTrue(recon[("E1", "2025-Q4")])


if __name__ == "__main__":
    unittest.main()

"""敵対的コードレビューで確証された20件の欠陥に対する回帰テスト。

各テストは修正前に失敗し、修正後に通ることを担保する。コメントの [dim] は発見次元。
"""
import datetime as dt
import unittest

from revenue_risk.agent.connectors import MockConnectorProvider, ReadOnlyConnectors
from revenue_risk.agent.orchestrator import AgentOrchestrator
from revenue_risk.audit.audit_log import AuditLog
from revenue_risk.config_loader import EngagementConfig, load_catalog, load_scenarios
from revenue_risk.contracts.models import RiskFinding, SalesTransaction
from revenue_risk.engines.context import RuleHit
from revenue_risk.engines.ml_anomaly import AnomalyModel
from revenue_risk.engines.network import EntityNetwork
from revenue_risk.engines.rule_engine import RuleEngine
from revenue_risk.etl.ingest import ingest_records
from revenue_risk.scoring import score_transactions


def txn(tid, **kw):
    base = dict(entity_id="E1", period="2025-Q4", customer_id="C1",
                revenue_recognition_date="2025-11-15", amount=1_000_000, currency="JPY")
    base.update(kw)
    return SalesTransaction(transaction_id=tid, **base)


# ============================ CRITICAL ====================================
class HitlOverwriteGuardTest(unittest.TestCase):
    """[contracts-hitl] AIは人間の confirmed/dismissed を別状態へ戻せない。"""

    def test_ai_cannot_revert_human_confirmed(self):
        f = RiskFinding("F1", ["T1"], 80.0, ["occurrence"], {"summary_ja": "x"}, "agent")
        f.set_hitl_status("confirmed", actor_is_human=True)
        with self.assertRaises(PermissionError):
            f.set_hitl_status("in_review", actor_is_human=False)
        self.assertEqual(f.hitl_status, "confirmed")

    def test_human_can_reopen(self):
        f = RiskFinding("F1", ["T1"], 80.0, ["occurrence"], {"summary_ja": "x"}, "agent")
        f.set_hitl_status("dismissed", actor_is_human=True)
        f.set_hitl_status("in_review", actor_is_human=True)  # 人間なら可
        self.assertEqual(f.hitl_status, "in_review")

    def test_orchestrator_skips_human_decided(self):
        f = RiskFinding("F-TX", ["TX"], 90.0, ["occurrence"], {"summary_ja": "x"}, "rule_engine",
                        rule_ids=["FICT-001"])
        f.set_hitl_status("confirmed", actor_is_human=True)
        eng = EngagementConfig(hitl={"require_g0_privacy_approval": False, "require_g1_sensitive_approval": False})
        orch = AgentOrchestrator(connectors=ReadOnlyConnectors(MockConnectorProvider()),
                                 scenarios=load_scenarios(), engagement=eng)
        res = orch.investigate([f], {"TX": txn("TX")}, approvals={"G0", "G1"})
        self.assertEqual(res.stats["skipped_human_decided"], 1)
        self.assertEqual(res.findings[0].hitl_status, "confirmed")  # 変更されない


class AuditTailAnchorTest(unittest.TestCase):
    """[audit] 末尾切り詰め・最終エントリ書換え・seq==0 の検知。"""

    def _log(self, k=5):
        n = {"i": 0}
        clock = lambda: (n.__setitem__("i", n["i"] + 1) or f"2025-01-01T00:00:{n['i']:02d}Z")
        log = AuditLog(clock=clock)
        for i in range(k):
            log.append("agent", "tool_call", target=f"t{i}")
        return log

    def test_truncation_detected_with_checkpoint(self):
        log = self._log(5)
        cp = log.checkpoint()
        del log._entries[3:]  # 末尾2件を切り詰め
        self.assertTrue(log.verify().valid)  # 内部連結だけでは検知できない
        chain = log.verify(expected_length=cp["length"], expected_head_hash=cp["head_hash"])
        self.assertFalse(chain.valid)

    def test_last_entry_rewrite_detected_with_checkpoint(self):
        log = self._log(5)
        cp = log.checkpoint()
        # 最終エントリを書換え、その自己hashだけ再計算（内部検証は通ってしまう）
        from revenue_risk.audit.audit_log import _entry_hash
        e = log._entries[-1]
        e.actor = "EVIL"
        e.hash = _entry_hash(e.seq, e.timestamp, e.actor, e.action, e.target, e.inputs_hash, e.prev_hash)
        self.assertTrue(log.verify().valid)
        self.assertFalse(log.verify(expected_head_hash=cp["head_hash"]).valid)

    def test_first_broken_seq_zero(self):
        from revenue_risk.audit.audit_log import _entry_hash
        log = self._log(6)
        log._entries[0].seq = 0  # 先頭を seq=0 に改ざん（falsy）
        log._entries[4].actor = "EVIL"  # 後方も改ざん
        chain = log.verify()
        self.assertFalse(chain.valid)
        self.assertEqual(chain.first_broken_seq, 1)  # 位置0（期待seq=1）が最初の破損


class EtlResilienceTest(unittest.TestCase):
    """[etl] 壊れた1件で母集団評価を止めない。"""

    def test_malformed_data_quality_does_not_crash(self):
        recs = [
            {"transaction_id": "T1", "entity_id": "E1", "period": "2025-Q4", "customer_id": "C1",
             "revenue_recognition_date": "2025-11-15", "amount": "100", "currency": "JPY",
             "data_quality": "oops"},
            {"transaction_id": "T2", "entity_id": "E1", "period": "2025-Q4", "customer_id": "C1",
             "revenue_recognition_date": "2025-11-15", "amount": "200", "currency": "JPY"},
        ]
        r = ingest_records(recs)  # 例外を投げない
        self.assertEqual(len(r.transactions), 2)
        self.assertGreaterEqual(r.invalid_count, 1)

    def test_schema_errors_keyed_uniquely(self):
        recs = [
            {"transaction_id": "DUP", "entity_id": "E1"},   # period 等欠落
            {"transaction_id": "DUP", "customer_id": "C1"},  # 別の欠落
        ]
        r = ingest_records(recs)
        self.assertEqual(len(r.schema_errors), 2)  # 上書きされず両方残る


# ============================ HIGH / MEDIUM ================================
class DetectorFixesTest(unittest.TestCase):
    def setUp(self):
        self.eng = RuleEngine()

    def _fired(self, ts, tid):
        return set(self.eng.evaluate(ts).rule_ids_for(tid))

    def test_recog_002_no_false_positive_on_correct_agent_net(self):
        # [detectors] agent チャネル＋agent_net は正常。RECOG-002 を発火させない
        t = txn("T", channel="agent", gross_net_indicator="agent_net")
        self.assertNotIn("RECOG-002", self._fired([t], "T"))

    def test_recog_002_fires_on_mismatch(self):
        t = txn("T", channel="direct", gross_net_indicator="agent_net")
        self.assertIn("RECOG-002", self._fired([t], "T"))

    def test_credit_002_bad_receipt_date_no_crash(self):
        # [detectors] 不正な receipt_date でルール全体が例外化しない
        t1 = txn("T1", receipt_date="N/A", period="2025-Q3", revenue_recognition_date="2025-08-01")
        t2 = txn("T2", receipt_date="2025/13/40")
        ev = self.eng.evaluate([t1, t2])
        self.assertFalse(any("CREDIT-002(error" in u for u in ev.unimplemented_rules))


class EtlGapTest(unittest.TestCase):
    def _rec(self, tid, amount="100"):
        return {"transaction_id": tid, "entity_id": "E1", "period": "2025-Q4", "customer_id": "C1",
                "revenue_recognition_date": "2025-11-15", "amount": amount, "currency": "JPY"}

    def test_gaps_grouped_by_prefix(self):
        # [etl] 系列混在で幻の欠番を作らない
        r = ingest_records([self._rec("INV-001"), self._rec("INV-002"), self._rec("CN-050")])
        self.assertEqual(r.sequence_gaps, [])  # INV と CN は別系列

    def test_gap_within_series_detected(self):
        r = ingest_records([self._rec("INV-001"), self._rec("INV-002"), self._rec("INV-005")])
        self.assertTrue(r.sequence_gaps)  # INV-003, INV-004

    def test_duplicate_sequence_reported(self):
        r = ingest_records([self._rec("INV-001"), self._rec("INV-001"), self._rec("INV-002")])
        self.assertIn("INV-1", r.sequence_duplicates)

    def test_missing_period_detected(self):
        r = ingest_records([self._rec("INV-001")], expected_periods=["2025-Q3", "2025-Q4"])
        self.assertIn("2025-Q3", r.missing_periods)


class NetworkFixesTest(unittest.TestCase):
    def _edge(self, tid, s, b, day, amount=1_000_000):
        return SalesTransaction(tid, s, "2025-Q4", b, f"2025-11-{day:02d}", amount, "JPY")

    def test_buyback_not_labeled_passthrough(self):
        # [network] A->B->A は買戻し。CIRC-004(スルー取引)にしない
        txns = [self._edge("a", "A", "B", 1), self._edge("b", "B", "A", 5)]
        rids = {f.rule_id for f in EntityNetwork(txns).analyze(max_hops=4)}
        self.assertNotIn("CIRC-004", rids)
        self.assertIn("CIRC-002", rids)  # 買戻しとしては検出

    def test_passthrough_respects_max_entities(self):
        # [network] CIRC-004 も entity スコープ上限に従う
        txns = [self._edge("in", "A", "X", 1, 1_000_000), self._edge("out", "X", "C", 5, 1_010_000)]
        full = {f.rule_id for f in EntityNetwork(txns).analyze(max_hops=4, max_entities=200)}
        scoped = {f.rule_id for f in EntityNetwork(txns).analyze(max_hops=4, max_entities=2)}
        self.assertIn("CIRC-004", full)
        self.assertNotIn("CIRC-004", scoped)  # 3当事者が2枠に収まらない

    def test_circ003_needs_full_date_coverage(self):
        # [network] 日付が全辺に揃わない閉路は短期性を主張しない
        txns = [self._edge("a", "N1", "N2", 1), self._edge("b", "N2", "N3", 5),
                SalesTransaction("c", "N3", "2025-Q4", "N1", "", 1_000_000, "JPY")]  # 日付欠落
        rids = {f.rule_id for f in EntityNetwork(txns).analyze(max_hops=4)}
        self.assertIn("CIRC-006", rids)      # 循環自体は検出
        self.assertNotIn("CIRC-003", rids)   # 短期資金還流は主張しない


class ScoringDedupTest(unittest.TestCase):
    def test_duplicate_rule_hits_counted_once(self):
        # [scoring-funnel] 同一 rule_id の重複ヒットで base_weight を多重計上しない
        catalog = load_catalog()
        hits = {"T1": [RuleHit("CUTOFF-001", ["T1"], "a"), RuleHit("CUTOFF-001", ["T1"], "b")]}
        scores = score_transactions(catalog, hits, [], {}, ["T1"])
        rule = catalog.rules["CUTOFF-001"]
        expected = rule.base_weight * catalog.severity_multiplier(rule.severity)
        self.assertAlmostEqual(scores["T1"].rule_score, expected, places=6)
        self.assertEqual(scores["T1"].rule_ids, ["CUTOFF-001"])


class MlNoiseTest(unittest.TestCase):
    def test_constant_ratio_column_not_flagged(self):
        # [ml] 数学的に一定な粗利率(0.3)が浮動小数ノイズで異常寄与にならない
        txns = []
        for i, p in enumerate([7, 11, 13, 17, 19, 23, 29, 31, 37, 41]):
            price = p * 1000
            txns.append(SalesTransaction(f"T{i}", "E1", "2025-Q4", "C1", "2025-11-15",
                                         price, "JPY", quantity=1, unit_price=price, unit_cost=price * 0.7))
        scores = AnomalyModel().fit_score(txns, {})
        for s in scores.values():
            feats = [x["feature"] for x in s.shap_top]
            self.assertNotIn("margin_rate", feats)


class AgentVerifyCutoffTest(unittest.TestCase):
    def test_cutoff_finding_not_refuted_by_shipment_presence(self):
        # [agent-injection] 出荷記録の「存在」だけで期間帰属の所見を正当化しない
        eng = EngagementConfig(hitl={"require_g0_privacy_approval": False, "require_g1_sensitive_approval": False})
        orch = AgentOrchestrator(connectors=ReadOnlyConnectors(MockConnectorProvider()),
                                 scenarios=load_scenarios(), engagement=eng)
        # 出荷日が認識日より後（＝未出荷計上）だが shipment_id は存在する
        t = txn("TX", revenue_recognition_date="2025-12-31", ship_date="2026-01-05",
                shipment_id="S1", channel="direct", period="2025-Q4")
        f = RiskFinding("F-TX", ["TX"], 90.0, ["cutoff", "occurrence"],
                        {"summary_ja": "未出荷計上"}, "rule_engine", rule_ids=["CUTOFF-002"], severity="critical")
        res = orch.investigate([f], {"TX": t}, approvals={"G0", "G1"})
        rec = res.findings[0].recommended_review_ja or ""
        self.assertNotIn("正当性を支持する方向", rec)  # 誤って正当化しない


if __name__ == "__main__":
    unittest.main()

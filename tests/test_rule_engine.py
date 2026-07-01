"""L2 ルールエンジンと個別 detector の発火。"""
import unittest

from revenue_risk.config_loader import EngagementConfig, load_catalog
from revenue_risk.contracts.models import SalesTransaction
from revenue_risk.engines.rule_engine import RuleEngine


def txn(tid, **kw):
    base = dict(entity_id="E1", period="2025-Q4", customer_id="C1",
                revenue_recognition_date="2025-11-15", amount=1_000_000, currency="JPY")
    base.update(kw)
    return SalesTransaction(transaction_id=tid, **base)


class DetectorFiringTest(unittest.TestCase):
    def setUp(self):
        self.eng = RuleEngine()

    def _fired(self, transactions, tid):
        ev = self.eng.evaluate(transactions)
        return set(ev.rule_ids_for(tid))

    def test_cutoff_002_unshipped(self):
        t = txn("T", channel="direct", revenue_recognition_date="2025-12-31",
                ship_date="2026-01-05", performance_obligation_status="satisfied_point_in_time")
        self.assertIn("CUTOFF-002", self._fired([t], "T"))

    def test_je_manual_and_executive(self):
        t = txn("T", source_system="manual", poster_role="executive")
        fired = self._fired([t], "T")
        self.assertIn("JE-001", fired)
        self.assertIn("JE-003", fired)

    def test_credit_limit_breach(self):
        t = txn("T", credit_limit=1_000_000, credit_used=900_000, amount=500_000, approver_id=None)
        self.assertIn("CREDIT-001", self._fired([t], "T"))

    def test_recog_not_satisfied(self):
        t = txn("T", performance_obligation_status="not_satisfied")
        self.assertIn("RECOG-001", self._fired([t], "T"))

    def test_sanctions_hit(self):
        t = txn("T", screening_status="hit")
        self.assertIn("CUST-001", self._fired([t], "T"))

    def test_clean_transaction_low_noise(self):
        t = txn("T", channel="direct", order_id="O", order_date="2025-10-20",
                shipment_id="S", ship_date="2025-11-05", delivery_date="2025-11-12",
                invoice_id="I", invoice_date="2025-11-16", performance_obligation_status="satisfied_point_in_time",
                approver_id="A1", salesperson_id="S1", source_system="system_generated",
                registry_verified=True, screening_status="clear", receipt_date="2025-12-20",
                quantity=10, unit_price=100000, amount=1_000_000)
        fired = self._fired([t], "T")
        # 正常な単一取引に critical 級の実在性/期間帰属ルールは発火しないこと
        self.assertNotIn("CUTOFF-002", fired)
        self.assertNotIn("FICT-001", fired)


class PrivacyGatingTest(unittest.TestCase):
    def test_privacy_disabled_by_default(self):
        eng = RuleEngine()
        ev = eng.evaluate([txn("T", officer_related=True, salesperson_id="S1")])
        self.assertIn("CUST-005", ev.disabled_rules)
        self.assertIn("PATT-002", ev.disabled_rules)
        self.assertNotIn("CUST-005", ev.rule_ids_for("T"))

    def test_officer_matching_when_enabled(self):
        cfg = EngagementConfig(privacy={"officer_customer_matching_enabled": True})
        eng = RuleEngine(engagement=cfg)
        ev = eng.evaluate([txn("T", officer_related=True)])
        self.assertNotIn("CUST-005", ev.disabled_rules)
        self.assertIn("CUST-005", ev.rule_ids_for("T"))


class CoverageTest(unittest.TestCase):
    def test_no_unimplemented_rules(self):
        eng = RuleEngine()
        ev = eng.evaluate([txn("T")])
        self.assertEqual(ev.unimplemented_rules, [], f"未実装: {ev.unimplemented_rules}")


if __name__ == "__main__":
    unittest.main()

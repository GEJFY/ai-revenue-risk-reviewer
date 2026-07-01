"""データ契約（スキーマ検証と HITL 不変条件）。"""
import unittest

from revenue_risk.contracts.models import Evidence, RiskFinding, SalesTransaction
from revenue_risk.contracts.validation import ContractError, SchemaValidator


class SchemaValidationTest(unittest.TestCase):
    def setUp(self):
        self.v = SchemaValidator()

    def test_valid_sales_transaction(self):
        t = SalesTransaction("T1", "E1", "2025-Q4", "C1", "2025-12-31", 1000.0, "JPY")
        self.assertTrue(self.v.is_valid("SalesTransaction", t.to_dict()))

    def test_missing_required_field_rejected(self):
        bad = {"transaction_id": "T1"}  # entity_id 等が欠落
        self.assertFalse(self.v.is_valid("SalesTransaction", bad))

    def test_bad_enum_rejected(self):
        t = SalesTransaction("T1", "E1", "2025-Q4", "C1", "2025-12-31", 1000.0, "JPY", channel="invalid")
        self.assertFalse(self.v.is_valid("SalesTransaction", t.to_dict()))

    def test_valid_risk_finding(self):
        f = RiskFinding("F1", ["T1"], 80.0, ["occurrence"], {"summary_ja": "x"}, "rule_engine")
        self.assertTrue(self.v.is_valid("RiskFinding", f.to_dict()))

    def test_validate_raises(self):
        with self.assertRaises(ContractError):
            self.v.validate("SalesTransaction", {"transaction_id": "x"})


class HitlInvariantTest(unittest.TestCase):
    """絶対原則 #3: confirmed/dismissed は人間のみ。"""

    def _finding(self):
        return RiskFinding("F1", ["T1"], 80.0, ["occurrence"], {"summary_ja": "x"}, "agent")

    def test_agent_can_set_in_review(self):
        f = self._finding()
        f.set_hitl_status("in_review", actor_is_human=False)
        self.assertEqual(f.hitl_status, "in_review")

    def test_agent_cannot_confirm(self):
        f = self._finding()
        with self.assertRaises(PermissionError):
            f.set_hitl_status("confirmed", actor_is_human=False)

    def test_agent_cannot_dismiss(self):
        f = self._finding()
        with self.assertRaises(PermissionError):
            f.set_hitl_status("dismissed", actor_is_human=False)

    def test_human_can_confirm(self):
        f = self._finding()
        f.set_hitl_status("confirmed", actor_is_human=True)
        self.assertEqual(f.hitl_status, "confirmed")

    def test_unknown_status_rejected(self):
        f = self._finding()
        with self.assertRaises(ValueError):
            f.set_hitl_status("weird", actor_is_human=True)


class EvidenceTest(unittest.TestCase):
    def test_evidence_is_read_only(self):
        e = Evidence("EV1", "F1", "shipment_confirmation", "mock", "prov", "2025-01-01T00:00:00Z")
        self.assertTrue(e.read_only)
        e.read_only = False  # 上書きしても
        self.assertTrue(e.to_dict()["read_only"])  # 出力は常に True


if __name__ == "__main__":
    unittest.main()

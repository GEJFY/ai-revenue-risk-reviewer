"""L1 ETL 取込（型キャスト・欠損・連番・GL 突合）。"""
import unittest

from revenue_risk.etl.ingest import ingest_records


def rec(tid, **kw):
    base = {"transaction_id": tid, "entity_id": "E1", "period": "2025-Q4", "customer_id": "C1",
            "revenue_recognition_date": "2025-11-15", "amount": "1000", "currency": "JPY"}
    base.update(kw)
    return base


class IngestTest(unittest.TestCase):
    def test_numeric_casting_from_strings(self):
        r = ingest_records([rec("T1", amount="1500.5", quantity="3", payment_terms_days="30")])
        t = r.transactions[0]
        self.assertEqual(t.amount, 1500.5)
        self.assertEqual(t.quantity, 3.0)
        self.assertEqual(t.payment_terms_days, 30)

    def test_boolean_casting(self):
        r = ingest_records([rec("T1", return_flag="true", related_party_flag="0")])
        t = r.transactions[0]
        self.assertIs(t.return_flag, True)
        self.assertIs(t.related_party_flag, False)

    def test_missing_required_recorded(self):
        r = ingest_records([{"transaction_id": "T1", "entity_id": "E1"}])  # 多数欠落
        t = r.transactions[0]
        self.assertIn("customer_id", t.missing_fields())
        self.assertGreater(r.invalid_count, 0)

    def test_sequence_gap_detected(self):
        rows = [rec("INV-001"), rec("INV-002"), rec("INV-005")]
        r = ingest_records(rows)
        self.assertTrue(r.sequence_gaps)  # 003,004 が欠番

    def test_gl_reconciliation_match(self):
        rows = [rec("T1", amount="600"), rec("T2", amount="400")]
        r = ingest_records(rows, gl_totals={("E1", "2025-Q4"): 1000.0})
        self.assertTrue(r.reconciled())
        self.assertTrue(r.transactions[0].reconciled_to_gl)

    def test_gl_reconciliation_mismatch(self):
        rows = [rec("T1", amount="600")]
        r = ingest_records(rows, gl_totals={("E1", "2025-Q4"): 1000.0})
        self.assertFalse(r.reconciled())
        recon = r.gl_reconciliation[0]
        self.assertEqual(recon["difference"], -400.0)

    def test_period_coverage(self):
        rows = [rec("T1", period="2025-Q3"), rec("T2", period="2025-Q4")]
        r = ingest_records(rows)
        self.assertEqual(r.period_coverage, ["2025-Q3", "2025-Q4"])


if __name__ == "__main__":
    unittest.main()

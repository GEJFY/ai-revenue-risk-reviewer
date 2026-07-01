"""L4 ML 異常検知（スコアと SHAP 寄与）。"""
import datetime as dt
import unittest

from revenue_risk.contracts.models import SalesTransaction
from revenue_risk.engines.ml_anomaly import AnomalyModel


def t(tid, amount, qty):
    return SalesTransaction(tid, "E1", "2025-Q4", "C1", "2025-11-15", amount, "JPY",
                            quantity=qty, unit_price=amount / max(qty, 1))


class AnomalyModelTest(unittest.TestCase):
    def test_outlier_scores_higher_than_clean(self):
        txns = [t(f"C{i}", 100000, 2) for i in range(20)]
        txns.append(t("OUT", 99_000_000, 990))  # 明確な外れ値
        scores = AnomalyModel().fit_score(txns, {"2025-Q4": dt.date(2025, 12, 31)})
        clean_avg = sum(scores[f"C{i}"].anomaly_score for i in range(20)) / 20
        self.assertGreater(scores["OUT"].anomaly_score, clean_avg)

    def test_scores_bounded_0_100(self):
        txns = [t(f"C{i}", 100000 * (i + 1), i + 1) for i in range(10)]
        scores = AnomalyModel().fit_score(txns, {})
        for s in scores.values():
            self.assertGreaterEqual(s.anomaly_score, 0)
            self.assertLessEqual(s.anomaly_score, 100)

    def test_shap_present_for_outlier(self):
        txns = [t(f"C{i}", 100000, 2) for i in range(20)]
        txns.append(t("OUT", 99_000_000, 990))
        scores = AnomalyModel().fit_score(txns, {})
        self.assertTrue(scores["OUT"].shap_top, "外れ値には寄与要因が付く")
        self.assertIn("feature", scores["OUT"].shap_top[0])

    def test_deterministic(self):
        txns = [t(f"C{i}", 100000 * (i + 1), i + 1) for i in range(10)]
        a = AnomalyModel().fit_score(txns, {})
        b = AnomalyModel().fit_score(txns, {})
        self.assertEqual(a["C0"].anomaly_score, b["C0"].anomaly_score)

    def test_empty(self):
        self.assertEqual(AnomalyModel().fit_score([], {}), {})


if __name__ == "__main__":
    unittest.main()

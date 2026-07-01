"""統合スコアリングとファネル（critical override・assertion gate）。"""
import unittest

from revenue_risk.config_loader import load_catalog
from revenue_risk.contracts.models import SalesTransaction
from revenue_risk.engines.context import RuleHit
from revenue_risk.funnel import select_high_risk
from revenue_risk.scoring import score_transactions


class ScoringFunnelTest(unittest.TestCase):
    def setUp(self):
        self.catalog = load_catalog()

    def _score(self, txn_ids, hits_by_txn, ml=None):
        return score_transactions(self.catalog, hits_by_txn, [], ml or {}, txn_ids)

    def test_critical_override_selects_below_threshold(self):
        # 単独の high 未満スコアでも critical ルール発火なら選別される
        hit = RuleHit("CUST-001", ["T1"], "反社該当")  # critical, base_weight 38
        # base_weight 38 * 2.0(critical) = 76 -> rule_score 76 -> blended 0.6*76=45.6 (<70 単独では?)
        # ここでは低スコアを作るため base_weight の小さい critical を別途検証
        scores = self._score(["T1"], {"T1": [hit]})
        ts = scores["T1"]
        self.assertTrue(ts.has_critical)
        funnel = select_high_risk(self.catalog, scores)
        self.assertIn("T1", funnel.selected)
        self.assertTrue(any("critical" in r for r in funnel.selected["T1"]))

    def test_assertion_gate_selects_occurrence_high(self):
        # occurrence に紐づく high ルール（例 FICT-002, base_weight 24, high）
        hit = RuleHit("FICT-002", ["T2"], "新設先高額")
        scores = self._score(["T2"], {"T2": [hit]})
        ts = scores["T2"]
        self.assertLess(ts.risk_score, self.catalog.high_threshold)  # 単独では閾値未満
        self.assertIn("occurrence", ts.assertions)
        funnel = select_high_risk(self.catalog, scores)
        self.assertIn("T2", funnel.selected)
        self.assertTrue(any("assertion_gate" in r for r in funnel.selected["T2"]))

    def test_clean_not_selected(self):
        scores = self._score(["T3"], {"T3": []})
        funnel = select_high_risk(self.catalog, scores)
        self.assertNotIn("T3", funnel.selected)

    def test_blend_weights(self):
        blend = self.catalog.rule_ml_blend
        self.assertAlmostEqual(blend["rule_weight"] + blend["ml_weight"], 1.0, places=3)


if __name__ == "__main__":
    unittest.main()

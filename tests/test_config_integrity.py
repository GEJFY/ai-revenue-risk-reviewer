"""config/ の整合性（rule-authoring スキル: 変更後に検証）。"""
import unittest

from revenue_risk import config_loader as cl
from revenue_risk.engines import detectors
from revenue_risk.contracts.models import ASSERTIONS, SEVERITIES


class ConfigIntegrityTest(unittest.TestCase):
    def setUp(self):
        self.catalog = cl.load_catalog()
        self.scenarios = cl.load_scenarios()
        self.params = cl.load_detection_params()

    def test_no_integrity_problems(self):
        problems = cl.check_integrity(self.catalog, self.scenarios, self.params)
        self.assertEqual(problems, [], f"整合性の問題: {problems}")

    def test_rule_and_scenario_counts(self):
        self.assertEqual(len(self.catalog.rules), 65)
        self.assertEqual(len(self.scenarios), 18)
        cats = {r.category for r in self.catalog.rules.values()}
        self.assertEqual(len(cats), 12)

    def test_every_rule_has_assertion_and_fp_notes(self):
        for r in self.catalog.rules.values():
            self.assertTrue(r.assertion, f"{r.id}: assertion 必須")
            self.assertTrue(all(a in ASSERTIONS for a in r.assertion), f"{r.id}: 未知のアサーション")
            self.assertIn(r.severity, SEVERITIES)
            self.assertTrue(r.false_positive_notes_ja.strip(), f"{r.id}: 誤検知注記 必須")

    def test_every_rule_has_a_handler(self):
        """全ルールは detector 実装または network 実装のどちらかに割り当てられている。"""
        covered = set(detectors.REGISTRY) | set(detectors.NETWORK_RULES)
        missing = [rid for rid in self.catalog.rules if rid not in covered]
        self.assertEqual(missing, [], f"未実装のルール: {missing}")

    def test_scenario_linked_rules_resolve(self):
        for sc in self.scenarios:
            for rid in sc.linked_rules:
                self.assertIn(rid, self.catalog.rules, f"{sc.id}: {rid} が存在しない")


if __name__ == "__main__":
    unittest.main()

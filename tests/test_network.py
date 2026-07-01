"""L5 エンティティ・ネットワーク分析（循環取引・買戻し・スルー取引）。"""
import unittest

from revenue_risk.contracts.models import SalesTransaction
from revenue_risk.engines.network import EntityNetwork


def edge(tid, seller, buyer, day, amount=1_000_000):
    return SalesTransaction(tid, seller, "2025-Q4", buyer, f"2025-11-{day:02d}", amount, "JPY",
                            channel="distributor")


class NetworkTest(unittest.TestCase):
    def test_three_cycle_detected(self):
        txns = [edge("a", "N1", "N2", 1), edge("b", "N2", "N3", 5), edge("c", "N3", "N1", 10)]
        rids = {f.rule_id for f in EntityNetwork(txns).analyze(max_hops=4)}
        self.assertIn("CIRC-006", rids)  # 循環経路
        self.assertIn("CIRC-003", rids)  # 金額均衡＝資金還流

    def test_two_cycle_is_buysell_overlap(self):
        txns = [edge("a", "A", "B", 1), edge("b", "B", "A", 3)]
        rids = {f.rule_id for f in EntityNetwork(txns).analyze(max_hops=4)}
        self.assertIn("CIRC-002", rids)

    def test_passthrough_thin_margin(self):
        # X が B から買い（B→X）、短期に僅少粗利で C へ売る（X→C）
        txns = [edge("in", "B", "X", 1, amount=1_000_000), edge("out", "X", "C", 10, amount=1_010_000)]
        rids = {f.rule_id for f in EntityNetwork(txns).analyze(max_hops=4)}
        self.assertIn("CIRC-004", rids)

    def test_hop_limit_respected(self):
        # 4社の閉路。max_hops=3 では見つからない
        txns = [edge("a", "N1", "N2", 1), edge("b", "N2", "N3", 3),
                edge("c", "N3", "N4", 5), edge("d", "N4", "N1", 7)]
        rids3 = {f.rule_id for f in EntityNetwork(txns).analyze(max_hops=3)}
        rids4 = {f.rule_id for f in EntityNetwork(txns).analyze(max_hops=4)}
        self.assertNotIn("CIRC-006", rids3)
        self.assertIn("CIRC-006", rids4)

    def test_no_cycle_no_finding(self):
        txns = [edge("a", "A", "B", 1), edge("b", "B", "C", 3)]  # 直線、閉路なし
        rids = {f.rule_id for f in EntityNetwork(txns).analyze(max_hops=4)}
        self.assertNotIn("CIRC-006", rids)


if __name__ == "__main__":
    unittest.main()

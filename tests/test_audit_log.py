"""改ざん不能の監査ログ（WORM＋ハッシュチェーン）。"""
import unittest

from revenue_risk.audit.audit_log import AuditLog


def _counter_clock():
    n = {"i": 0}

    def clock():
        n["i"] += 1
        return f"2025-01-01T00:00:{n['i']:02d}Z"
    return clock


class AuditLogTest(unittest.TestCase):
    def _log(self, k=5):
        log = AuditLog(clock=_counter_clock())
        for i in range(k):
            log.append("agent", "tool_call", target=f"tool{i}", inputs={"i": i})
        return log

    def test_valid_chain(self):
        log = self._log()
        chain = log.verify()
        self.assertTrue(chain.valid)
        self.assertEqual(chain.entries_checked, 5)
        self.assertEqual(chain.problems, [])

    def test_seq_is_contiguous(self):
        log = self._log(3)
        self.assertEqual([e.seq for e in log.entries], [1, 2, 3])

    def test_genesis_prev_hash(self):
        log = self._log(1)
        self.assertEqual(log.entries[0].prev_hash, "0" * 64)

    def test_tamper_detected(self):
        log = self._log()
        # 3件目の内容を書き換える → 以降の鎖が壊れる
        log._entries[2].action = "hitl_decision"
        chain = log.verify()
        self.assertFalse(chain.valid)
        self.assertEqual(chain.first_broken_seq, 3)

    def test_deleted_entry_breaks_seq(self):
        log = self._log()
        del log._entries[2]  # seq 欠番
        chain = log.verify()
        self.assertFalse(chain.valid)

    def test_reordered_entries_break_chain(self):
        log = self._log()
        log._entries[1], log._entries[2] = log._entries[2], log._entries[1]
        self.assertFalse(log.verify().valid)

    def test_save_load_roundtrip(self):
        import tempfile
        import os
        log = self._log()
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "audit.json")
            log.save(p)
            loaded = AuditLog.load(p)
        self.assertTrue(loaded.verify().valid)
        self.assertEqual(len(loaded), 5)


if __name__ == "__main__":
    unittest.main()

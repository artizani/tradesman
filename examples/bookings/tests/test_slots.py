"""INV-101: a slot holds at most one active booking."""
import sys, os, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
import slots


class ClaimSlot(unittest.TestCase):
    def test_first_claim_succeeds(self):
        self.assertIsNotNone(slots.claim('S1', 'P1'))

    def test_concurrent_claim_loses_cleanly(self):
        self.assertIsNone(slots.claim('S1', 'P2', held_by='P1'))

    def test_holder_may_complete_own_claim(self):
        self.assertIsNotNone(slots.claim('S1', 'P1', held_by='P1'))


if __name__ == '__main__':
    unittest.main()

"""utils/investigation_state.py 단위 테스트."""

import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils.investigation_state import (
    InvestigationStateManager,
    get_investigation_state,
)


class InvestigationStateManagerTest(unittest.TestCase):
    def setUp(self):
        self.manager = InvestigationStateManager()

    def test_enter_and_get(self):
        self.manager.enter("alice", "병원")
        self.assertEqual(self.manager.get_location("alice"), "병원")

    def test_get_missing_returns_none(self):
        self.assertIsNone(self.manager.get_location("bob"))

    def test_overwrite_location(self):
        self.manager.enter("alice", "병원")
        self.manager.enter("alice", "학교")
        self.assertEqual(self.manager.get_location("alice"), "학교")

    def test_clear(self):
        self.manager.enter("alice", "병원")
        self.manager.clear("alice")
        self.assertIsNone(self.manager.get_location("alice"))

    def test_clear_missing_is_noop(self):
        self.manager.clear("ghost")  # should not raise
        self.assertIsNone(self.manager.get_location("ghost"))

    def test_isolation_between_users(self):
        self.manager.enter("alice", "병원")
        self.manager.enter("bob", "학교")
        self.assertEqual(self.manager.get_location("alice"), "병원")
        self.assertEqual(self.manager.get_location("bob"), "학교")

    def test_snapshot_is_copy(self):
        self.manager.enter("alice", "병원")
        snap = self.manager.snapshot()
        snap["alice"] = "다른 장소"
        self.assertEqual(self.manager.get_location("alice"), "병원")

    def test_thread_safety_many_writers(self):
        def writer(user_id: str, location: str, times: int) -> None:
            for _ in range(times):
                self.manager.enter(user_id, location)

        threads = [
            threading.Thread(target=writer, args=(f"user{i}", f"장소{i}", 500))
            for i in range(10)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        for i in range(10):
            self.assertEqual(self.manager.get_location(f"user{i}"), f"장소{i}")


class SingletonTest(unittest.TestCase):
    def test_singleton_identity(self):
        a = get_investigation_state()
        b = get_investigation_state()
        self.assertIs(a, b)


if __name__ == '__main__':
    unittest.main()

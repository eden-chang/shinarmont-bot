"""utils/operation_period.py 단위 테스트.

가동 기간 경계(시작 전 / 가동 중 / 만료)와 시작 전 무시 동작을 검증한다.
시간 의존성은 충분히 먼 과거/미래 날짜를 사용해 모킹 없이 안정적으로 확인한다.
"""

import os
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils.operation_period import (
    KST,
    check_operation_period,
    is_before_operation_start,
    get_seconds_until_expiry,
    parse_operation_dates,
)


def _kst_offset(days: int) -> str:
    """오늘(KST) 기준 days만큼 떨어진 날짜를 YYYY.MM.DD로 반환."""
    target = datetime.now(KST) + timedelta(days=days)
    return target.strftime("%Y.%m.%d")


class ParseOperationDatesTest(unittest.TestCase):
    def test_supported_formats_parse(self):
        for raw in ("2026.02.01", "2026-02-01", "2026/02/01"):
            start, end = parse_operation_dates(raw, raw)
            self.assertIsNotNone(start, raw)
            self.assertIsNotNone(end, raw)

    def test_end_date_is_next_day_midnight(self):
        start, end = parse_operation_dates("2026.02.01", "2026.02.01")
        # 종료일 23:59:59까지 가동 → 다음날 0시
        self.assertEqual((end - start), timedelta(days=1))

    def test_invalid_returns_none(self):
        start, end = parse_operation_dates("not-a-date", "2026.02.01")
        self.assertIsNone(start)
        self.assertIsNone(end)

    def test_empty_returns_none(self):
        self.assertEqual(parse_operation_dates("", "2026.02.01"), (None, None))


class IsBeforeOperationStartTest(unittest.TestCase):
    def test_before_start_returns_true(self):
        self.assertTrue(is_before_operation_start(_kst_offset(5)))

    def test_after_start_returns_false(self):
        self.assertFalse(is_before_operation_start(_kst_offset(-5)))

    def test_empty_start_returns_false(self):
        # 미설정 = 제한 없음 → 무시하지 않음
        self.assertFalse(is_before_operation_start(""))

    def test_invalid_start_returns_false(self):
        self.assertFalse(is_before_operation_start("not-a-date"))


class CheckOperationPeriodTest(unittest.TestCase):
    def test_unset_is_always_active(self):
        is_active, _ = check_operation_period("", "")
        self.assertTrue(is_active)

    def test_active_window(self):
        is_active, msg = check_operation_period(_kst_offset(-1), _kst_offset(5))
        self.assertTrue(is_active, msg)

    def test_before_start_is_inactive(self):
        is_active, _ = check_operation_period(_kst_offset(3), _kst_offset(10))
        self.assertFalse(is_active)
        # 시작 전은 '무시하고 대기'로 분류되어야 한다
        self.assertTrue(is_before_operation_start(_kst_offset(3)))

    def test_expired_is_inactive(self):
        is_active, _ = check_operation_period(_kst_offset(-10), _kst_offset(-3))
        self.assertFalse(is_active)
        # 만료는 시작 전이 아니다 → 종료 경로를 타야 한다
        self.assertFalse(is_before_operation_start(_kst_offset(-10)))

    def test_parse_failure_is_active(self):
        # 파싱 실패 시 무제한 가동
        is_active, _ = check_operation_period("bad", "also-bad")
        self.assertTrue(is_active)


class SecondsUntilExpiryTest(unittest.TestCase):
    def test_unset_returns_none(self):
        self.assertIsNone(get_seconds_until_expiry(""))

    def test_future_end_is_positive(self):
        self.assertGreater(get_seconds_until_expiry(_kst_offset(5)), 0)

    def test_past_end_is_non_positive(self):
        self.assertLessEqual(get_seconds_until_expiry(_kst_offset(-5)), 0)


if __name__ == "__main__":
    unittest.main()

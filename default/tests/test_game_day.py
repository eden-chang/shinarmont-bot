"""utils/game_day.py 단위 테스트.

current_day()가 KST 오늘과 GAME_START_DATE 차이로 일차를 계산하고,
시작일 이전/파싱 실패를 최소 1로 방어하는지 검증한다.
시간 의존성은 오늘(KST) 기준 상대 오프셋 날짜로 안정적으로 확인한다.
"""

import os
import sys
import unittest
from datetime import datetime, timedelta

import pytz

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from config.settings import config
import utils.game_day as game_day

_KST = pytz.timezone('Asia/Seoul')


def _kst_offset(days: int) -> str:
    """오늘(KST) 기준 days만큼 떨어진 날짜를 YYYY.MM.DD로 반환."""
    target = datetime.now(_KST) + timedelta(days=days)
    return target.strftime("%Y.%m.%d")


class CurrentDayTest(unittest.TestCase):
    def setUp(self):
        self._orig = config.GAME_START_DATE

    def tearDown(self):
        config.GAME_START_DATE = self._orig

    def test_today_is_day_one(self):
        config.GAME_START_DATE = _kst_offset(0)
        self.assertEqual(game_day.current_day(), 1)

    def test_started_ten_days_ago_is_day_eleven(self):
        config.GAME_START_DATE = _kst_offset(-10)
        self.assertEqual(game_day.current_day(), 11)

    def test_before_start_clamped_to_one(self):
        config.GAME_START_DATE = _kst_offset(5)
        self.assertEqual(game_day.current_day(), 1)

    def test_dash_and_slash_formats(self):
        target = datetime.now(_KST) - timedelta(days=3)
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
            config.GAME_START_DATE = target.strftime(fmt)
            self.assertEqual(game_day.current_day(), 4, fmt)

    def test_parse_failure_returns_one(self):
        for bad in ("", "not-a-date", "2026.13.40", None):
            config.GAME_START_DATE = bad
            self.assertEqual(game_day.current_day(), 1, repr(bad))

    def test_return_type_is_int(self):
        config.GAME_START_DATE = _kst_offset(-1)
        self.assertIsInstance(game_day.current_day(), int)


if __name__ == '__main__':
    unittest.main()

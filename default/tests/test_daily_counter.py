"""utils/daily_counter.py 단위 테스트 (페이크 매니저)."""

import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils import daily_counter


def _make_manager(rows, header):
    """관리 시트를 흉내내는 페이크 sheets_manager 생성."""
    manager = MagicMock()
    manager.get_worksheet_data.return_value = rows
    ws = MagicMock()
    ws.row_values.return_value = header
    manager.get_worksheet.return_value = ws
    manager.update_cell.return_value = True
    manager.batch_update_cells.return_value = True
    return manager


class CheckAndIncSheetTest(unittest.TestCase):
    HEADER = ['이름', '아이디', '추적', '조사']

    def test_increments_when_below_limit(self):
        rows = [{'이름': 'A', '아이디': 'alice', '추적': '0', '조사': '1', '_row_number': 3}]
        m = _make_manager(rows, self.HEADER)
        result = daily_counter.check_and_inc_sheet(m, 'alice', '추적', 1)
        self.assertTrue(result)
        # 오늘추적은 3번째 컬럼(1-indexed), 행 3, 값 1
        m.update_cell.assert_called_once_with('관리', 3, 3, 1)

    def test_false_when_at_limit(self):
        rows = [{'이름': 'A', '아이디': 'alice', '추적': '1', '조사': '2', '_row_number': 3}]
        m = _make_manager(rows, self.HEADER)
        result = daily_counter.check_and_inc_sheet(m, 'alice', '조사', 2)
        self.assertFalse(result)
        m.update_cell.assert_not_called()

    def test_empty_value_treated_as_zero(self):
        rows = [{'이름': 'A', '아이디': 'alice', '추적': '', '조사': '', '_row_number': 5}]
        m = _make_manager(rows, self.HEADER)
        result = daily_counter.check_and_inc_sheet(m, 'alice', '조사', 2)
        self.assertTrue(result)
        m.update_cell.assert_called_once_with('관리', 5, 4, 1)

    def test_float_value_parsed(self):
        rows = [{'이름': 'A', '아이디': 'alice', '조사': '1.0', '_row_number': 4}]
        m = _make_manager(rows, ['이름', '아이디', '조사'])
        result = daily_counter.check_and_inc_sheet(m, 'alice', '조사', 2)
        self.assertTrue(result)
        m.update_cell.assert_called_once_with('관리', 4, 3, 2)

    def test_missing_user_returns_false(self):
        rows = [{'이름': 'A', '아이디': 'alice', '추적': '0', '_row_number': 3}]
        m = _make_manager(rows, self.HEADER)
        result = daily_counter.check_and_inc_sheet(m, 'bob', '추적', 1)
        self.assertFalse(result)
        m.update_cell.assert_not_called()

    def test_missing_column_returns_false(self):
        rows = [{'이름': 'A', '아이디': 'alice', '_row_number': 3}]
        m = _make_manager(rows, ['이름', '아이디'])
        result = daily_counter.check_and_inc_sheet(m, 'alice', '추적', 1)
        self.assertFalse(result)
        m.update_cell.assert_not_called()

    def test_none_manager_returns_false(self):
        self.assertFalse(daily_counter.check_and_inc_sheet(None, 'alice', '추적', 1))


class NoResetApiTest(unittest.TestCase):
    """0시 리셋은 이 봇의 책임이 아니다 (2026-07-16 운영 결정).

    `관리` 시트의 추적/조사/출석 컬럼은 별도 스케줄러 봇이 초기화한다.
    리셋 주체가 둘이면 서로의 결과를 덮어쓰고, 실패해도 누구 탓인지 알 수 없다.
    이 모듈은 세기만 하고 지우지 않는다 — 리셋 API 가 다시 생기면 여기서 잡힌다.
    """

    def test_module_exposes_no_reset_helper(self):
        for name in ('reset_sheet_counters', 'default_counter_columns',
                     'DEFAULT_COUNTER_COLUMNS'):
            self.assertFalse(
                hasattr(daily_counter, name),
                f"daily_counter.{name} 부활 — 관리 시트 리셋은 별도 봇의 책임이다",
            )

    def test_still_counts_up(self):
        """리셋은 안 하지만 증가/되돌리기는 계속 동작해야 한다."""
        self.assertTrue(callable(daily_counter.check_and_inc_sheet))
        self.assertTrue(callable(daily_counter.dec_sheet))


if __name__ == '__main__':
    unittest.main()

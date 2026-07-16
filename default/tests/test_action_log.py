"""utils/action_log.py 단위 테스트 (페이크 시스템 시트 매니저)."""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils import action_log


def _make_manager():
    manager = MagicMock()
    manager.append_row.return_value = True
    manager.update_cell.return_value = True
    manager.get_worksheet_data.return_value = []
    return manager


class AppendTest(unittest.TestCase):
    def test_column_order_and_values(self):
        """**7열 순서가 시트 헤더와 같아야 한다.**

        2026-07-16까지 코드는 9열을 썼는데 시트 헤더는 7열이라 모든 값이 밀려 있었고,
        `소문화` 칸에 `상세`가 들어가 **소문 후보 판정이 전부 "이미 소문냄"** 이 됐다.
        """
        m = _make_manager()
        with patch.object(action_log, '_now', return_value='2026-07-14 10:00:00'), \
             patch.object(action_log, '_current_day', return_value=5):
            ok = action_log.append(m, '추적', 'alice', 'bob', '요약내용')
        self.assertTrue(ok)
        m.append_row.assert_called_once()
        sheet_name, row = m.append_row.call_args[0]
        self.assertEqual(sheet_name, '행동로그')
        # 7열 순서 고정: [일시, 일차, 행위자, 종류, 대상, 요약, 소문화]
        self.assertEqual(row, [
            '2026-07-14 10:00:00', 5, 'alice', '추적', 'bob', '요약내용', '',
        ])

    def test_row_matches_columns_constant(self):
        """COLUMNS 상수와 실제 행 길이·순서가 어긋나면 안 된다(단일 소스)."""
        m = _make_manager()
        action_log.append(m, '조사', 'alice', 'bob', 's')
        row = m.append_row.call_args[0][1]
        self.assertEqual(len(row), len(action_log.COLUMNS))
        self.assertEqual(action_log.COLUMNS,
                         ['일시', '일차', '행위자', '종류', '대상', '요약', '소문화'])
        # 행위자/종류 자리 확인 — 이 둘이 뒤집혀 있던 게 원인이었다
        self.assertEqual(row[action_log.COLUMNS.index('행위자')], 'alice')
        self.assertEqual(row[action_log.COLUMNS.index('종류')], '조사')

    def test_sohwa_starts_blank(self):
        """소문화는 공란으로 시작한다 — 소문이 이 칸을 보고 후보를 고른다.

        여기에 다른 값이 들어가면 그 줄은 영영 소문이 되지 못한다(실제로 겪었다).
        """
        m = _make_manager()
        with patch.object(action_log, '_now', return_value='T'), \
             patch.object(action_log, '_current_day', return_value=1):
            action_log.append(m, '교류', 'a', 'b', 's')
        row = m.append_row.call_args[0][1]
        self.assertEqual(row[-1], '')
        self.assertEqual(row[action_log.COLUMNS.index('소문화')], '')

    def test_none_manager_skips(self):
        # None 이면 조용히 skip, False 반환, 예외 없음
        self.assertFalse(action_log.append(None, '고발', 'a', 'b', 's'))

    def test_append_failure_returns_false(self):
        m = _make_manager()
        m.append_row.return_value = False
        with patch.object(action_log, '_now', return_value='T'), \
             patch.object(action_log, '_current_day', return_value=1):
            self.assertFalse(action_log.append(m, '의무실', 'a', 'b', 's'))


class RecentTest(unittest.TestCase):
    ROWS = [
        {'일차': '3', '종류': '추적', '_row_number': 3},
        {'일차': '5', '종류': '고발', '_row_number': 4},
        {'일차': '5', '종류': '추적', '_row_number': 5},
    ]

    def test_none_manager_returns_empty(self):
        self.assertEqual(action_log.recent(None), [])

    def test_no_filter_returns_all(self):
        m = _make_manager()
        m.get_worksheet_data.return_value = list(self.ROWS)
        self.assertEqual(len(action_log.recent(m)), 3)

    def test_kind_filter(self):
        m = _make_manager()
        m.get_worksheet_data.return_value = list(self.ROWS)
        res = action_log.recent(m, kind='추적')
        self.assertEqual(len(res), 2)
        self.assertTrue(all(r['종류'] == '추적' for r in res))

    def test_days_filter(self):
        m = _make_manager()
        m.get_worksheet_data.return_value = list(self.ROWS)
        with patch.object(action_log, '_current_day', return_value=5):
            # 최근 1일차: min_day=5 → 일차 5인 2개만
            res = action_log.recent(m, days=1)
        self.assertEqual(len(res), 2)
        self.assertTrue(all(str(r['일차']) == '5' for r in res))


class MarkRumoredTest(unittest.TestCase):
    def test_marks_o_at_the_sohwa_column(self):
        """9열 시절엔 소문화가 9열이었다. 7열로 줄면서 이 테스트가 틀린 동작을
        검증하는 바람에(빈 열에 O) 사고를 통과시켰다 — 이제 헤더에서 파생한다."""
        m = _make_manager()
        ok = action_log.mark_rumored(m, 7)
        self.assertTrue(ok)
        expected_col = action_log.COLUMNS.index('소문화') + 1
        self.assertEqual(expected_col, 7)
        m.update_cell.assert_called_once_with('행동로그', 7, expected_col, 'O')

    def test_rumor_col_tracks_the_header(self):
        """상수를 손으로 적으면 헤더가 바뀔 때 또 어긋난다."""
        self.assertEqual(action_log.RUMOR_COL, action_log.COLUMNS.index('소문화') + 1)
        self.assertLessEqual(action_log.RUMOR_COL, len(action_log.COLUMNS),
                             "소문화 열이 헤더 밖을 가리킨다")

    def test_none_manager_false(self):
        self.assertFalse(action_log.mark_rumored(None, 7))

    def test_invalid_row_false(self):
        m = _make_manager()
        self.assertFalse(action_log.mark_rumored(m, 'abc'))
        m.update_cell.assert_not_called()


if __name__ == '__main__':
    unittest.main()

"""commands/shinarmont/track_command.py 단위 테스트 (페이크 매니저)."""

import os
import random
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from commands.base_command import CommandContext
from commands.shinarmont import track_command
from commands.shinarmont.track_command import TrackCommand, _split_token


# ---------------------------------------------------------------------------
# 페이크 시트 매니저
# ---------------------------------------------------------------------------

class FakeBaseSheets:
    """명단/관리 시트를 흉내내는 기본 시트 매니저."""

    def __init__(self, roster, management, mgmt_header):
        self._roster = roster
        self._management = management
        self._mgmt_header = mgmt_header
        self.updates = []  # (sheet, row, col, value)

    def get_roster_data(self, use_cache=True):
        return self._roster

    def get_worksheet_data(self, name, use_cache=False):
        if name == '관리':
            return self._management
        return []

    def get_worksheet(self, name, use_cache=True):
        ws = MagicMock()
        ws.row_values.return_value = self._mgmt_header
        return ws

    def update_cell(self, name, row, col, value):
        self.updates.append((name, row, col, value))
        # 관리 시트 오늘추적 반영 (간단히 값 갱신)
        for r in self._management:
            if r.get('_row_number') == row:
                header = self._mgmt_header
                if 0 <= col - 1 < len(header):
                    r[header[col - 1]] = value
        return True

    def batch_update_cells(self, name, updates):
        for (row, col, value) in updates:
            self.update_cell(name, row, col, value)
        return True


class FakeSystemSheets:
    """추적/추적기록/행동로그 시스템 시트를 흉내내는 매니저."""

    def __init__(self, track_rows, record_rows, record_header):
        self._track = track_rows
        self._record = record_rows
        self._record_header = record_header
        self.appended = []       # (sheet, row_values)
        self.cell_updates = []   # (sheet, row, col, value)

    def get_worksheet_data(self, name, use_cache=False):
        if name == '추적':
            return self._track
        if name == '추적기록':
            return self._record
        if name == '행동로그':
            return []
        return []

    def get_worksheet(self, name, use_cache=True):
        ws = MagicMock()
        if name == '추적기록':
            ws.row_values.return_value = self._record_header
        else:
            ws.row_values.return_value = []
        return ws

    def update_cell(self, name, row, col, value):
        self.cell_updates.append((name, row, col, value))
        if name == '추적기록':
            for r in self._record:
                if r.get('_row_number') == row:
                    header = self._record_header
                    if 0 <= col - 1 < len(header):
                        r[header[col - 1]] = value
        return True

    def append_row(self, name, values):
        self.appended.append((name, values))
        if name == '추적기록':
            # 새 행을 데이터에 반영 (다음 조회 대비)
            rec = {}
            for idx, col in enumerate(self._record_header):
                rec[col] = values[idx] if idx < len(values) else ''
            rec['_row_number'] = len(self._record) + 3
            self._record.append(rec)
        return True


# ---------------------------------------------------------------------------
# 픽스처 빌더
# ---------------------------------------------------------------------------

MGMT_HEADER = ['이름', '아이디', '소지금', '건강', '이성', '추적', '조사']


def _build_command(track_rows, record_rows, *, sanity='80', 오늘추적='0',
                   record_header=None):
    if record_header is None:
        record_header = ['이름', '추적기록']

    roster = [
        {'이름': '앨리스', '아이디': 'alice', '은는': '는'},
        {'이름': '밥', '아이디': 'bob', '은는': '은'},
    ]
    management = [
        {'이름': '앨리스', '아이디': 'alice', '소지금': '100', '건강': '90',
         '이성': sanity, '추적': 오늘추적, '조사': '0', '_row_number': 3},
    ]
    base = FakeBaseSheets(roster, management, MGMT_HEADER)
    system = FakeSystemSheets(track_rows, record_rows, record_header)
    cmd = TrackCommand(sheets_manager=base, api=None, system_sheets_manager=system)
    return cmd, base, system


def _track_rows():
    return [
        {'이름': 'ㅇ', '추적1': 'ㅇ', '추적2': 'ㅇ', '추적3': 'ㅇ',
         '추적4': 'ㅇ', '추적5': 'ㅇ', '_row_number': 2},
        {'이름': '데이나', '추적1': '단서A', '추적2': '단서B', '추적3': '',
         '추적4': '', '추적5': '', '_row_number': 3},
        {'이름': '데이비드', '추적1': '데이비드단서1', '추적2': '', '추적3': '데이비드단서3',
         '추적4': '', '추적5': '', '_row_number': 4},
        {'이름': '요나스', '추적1': '', '추적2': '', '추적3': '',
         '추적4': '', '추적5': '', '_row_number': 5},
    ]


def _ctx(target, user_id='alice', user_name='앨리스', visibility='direct'):
    return CommandContext(
        user_id=user_id, user_name=user_name, keywords=['추적', target],
        metadata={'visibility': visibility},
    )


# ---------------------------------------------------------------------------
# 테스트
# ---------------------------------------------------------------------------

class TokenParseTest(unittest.TestCase):
    def test_split_basic(self):
        self.assertEqual(_split_token('데이비드2'), ('데이비드', 2))

    def test_split_multidigit(self):
        self.assertEqual(_split_token('베라12'), ('베라', 12))

    def test_split_with_space(self):
        self.assertEqual(_split_token(' 오토 3 '), ('오토', 3))

    def test_split_no_number(self):
        self.assertIsNone(_split_token('데이나'))

    def test_split_empty(self):
        self.assertIsNone(_split_token(''))


class DmGuardTest(unittest.TestCase):
    def test_public_blocked(self):
        cmd, _, _ = _build_command(_track_rows(), [])
        resp = cmd.execute(_ctx('데이나', visibility='public'))
        self.assertFalse(resp.is_successful())
        self.assertIn('DM', resp.message)


class TrackFlowTest(unittest.TestCase):
    def setUp(self):
        random.seed(0)

    def test_first_track_appends_new_record_row(self):
        cmd, base, system = _build_command(_track_rows(), [])
        resp = cmd.execute(_ctx('데이나'))
        self.assertTrue(resp.is_successful(), resp.message)
        # 신규 추적기록 행 append 확인 (행동로그 append 는 제외)
        rec_appends = [a for a in system.appended if a[0] == '추적기록']
        self.assertEqual(len(rec_appends), 1)
        _, values = rec_appends[0]
        self.assertEqual(values[0], '앨리스')  # 이름 (명단에서 해석)
        self.assertTrue(values[1].startswith('데이나'))  # 토큰
        # 오늘추적 소비 확인 (관리 시트 update)
        self.assertTrue(any(u[0] == '관리' for u in base.updates))
        # 응답에 단서 텍스트 포함
        self.assertTrue('단서A' in resp.message or '단서B' in resp.message)

    def test_daily_limit_blocks_second(self):
        cmd, base, system = _build_command(_track_rows(), [], 오늘추적='1')
        resp = cmd.execute(_ctx('데이나'))
        self.assertFalse(resp.is_successful())
        self.assertIn('하루 1회', resp.message)

    def test_exception_rolls_back_daily_counter(self):
        # 소비 후 _run_track에서 예외가 나면 오늘추적 카운터를 롤백한다
        cmd, base, system = _build_command(_track_rows(), [])
        with patch.object(track_command.daily_counter, 'check_and_inc_sheet', return_value=True), \
             patch.object(track_command.daily_counter, 'dec_sheet') as dec, \
             patch.object(cmd, '_run_track', side_effect=RuntimeError('boom')):
            resp = cmd.execute(_ctx('데이나'))
        self.assertFalse(resp.is_successful())
        dec.assert_called_once()
        self.assertEqual(dec.call_args[0][1], 'alice')
        self.assertEqual(dec.call_args[0][2], track_command.TRACK_DAILY_COLUMN)

    def test_unknown_target_no_consume(self):
        cmd, base, system = _build_command(_track_rows(), [])
        resp = cmd.execute(_ctx('없는사람'))
        self.assertFalse(resp.is_successful())
        # 일일제한 소비 없음
        self.assertFalse(any(u[0] == '관리' for u in base.updates))

    def test_no_duplicate_clue_for_same_tracker(self):
        """이미 데이나1,데이나2 받았으면 더 알아낼 것이 없다."""
        record = [
            {'이름': '앨리스', '추적기록': '데이나1, 데이나2', '_row_number': 3},
        ]
        cmd, base, system = _build_command(_track_rows(), record)
        resp = cmd.execute(_ctx('데이나'))
        self.assertTrue(resp.is_successful())
        self.assertIn('더 알아낼 것이 없다', resp.message)
        # 새 토큰 append/update 없음
        self.assertEqual(system.appended, [])

    def test_existing_record_appends_to_cell(self):
        """다른 대상 기록만 있는 추적자는 셀에 콤마 append."""
        record = [
            {'이름': '앨리스', '추적기록': '베라1', '_row_number': 3},
        ]
        cmd, base, system = _build_command(_track_rows(), record)
        resp = cmd.execute(_ctx('데이나'))
        self.assertTrue(resp.is_successful(), resp.message)
        # 기존 셀 갱신(update_cell)로 처리, 신규행 append 아님
        rec_updates = [u for u in system.cell_updates if u[0] == '추적기록']
        self.assertEqual(len(rec_updates), 1)
        _, row, col, value = rec_updates[0]
        self.assertTrue(value.startswith('베라1, 데이나'))

    def test_zero_clue_target_exhausted(self):
        cmd, base, system = _build_command(_track_rows(), [])
        resp = cmd.execute(_ctx('요나스'))
        self.assertTrue(resp.is_successful())
        self.assertIn('더 알아낼 것이 없다', resp.message)

    def test_diff_covers_remaining_only(self):
        """데이비드는 1,3만 존재. 1을 이미 받았으면 3만 나온다."""
        for _ in range(20):
            record = [{'이름': '앨리스', '추적기록': '데이비드1', '_row_number': 3}]
            cmd, base, system = _build_command(_track_rows(), record)
            resp = cmd.execute(_ctx('데이비드'))
            self.assertTrue(resp.is_successful(), resp.message)
            self.assertIn('데이비드단서3', resp.message)

    def test_self_track_blocked_no_consume(self):
        """자기 자신(앨리스)을 추적하면 차단되고 일일 제한을 소비하지 않는다."""
        rows = _track_rows() + [
            {'이름': '앨리스', '추적1': '자기단서', '추적2': '', '추적3': '',
             '추적4': '', '추적5': '', '_row_number': 6},
        ]
        cmd, base, system = _build_command(rows, [])
        resp = cmd.execute(_ctx('앨리스'))
        self.assertFalse(resp.is_successful())
        self.assertIn('자기 자신', resp.message)
        # 일일 제한 소비 없음 (관리 update 없음), 기록/로그 append 없음
        self.assertFalse(any(u[0] == '관리' for u in base.updates))
        self.assertEqual(system.appended, [])


if __name__ == '__main__':
    unittest.main()

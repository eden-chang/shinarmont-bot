"""commands/shinarmont/vote_command.py 단위 테스트.

투표는 '투표' 시트에 표를 남기는 것이 전부다. 조건은 둘뿐이다:
명단에 있는 인물이어야 하고, 표는 유죄/무죄 중 하나여야 한다.
단상(설정 시트)은 더 이상 보지 않는다.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from commands.base_command import CommandContext
from commands.shinarmont.vote_command import VoteCommand
import utils.game_day as game_day


ROSTER = [
    {'아이디': 'alice', '이름': '한참'},
    {'아이디': 'bob', '이름': '데보라'},
    {'아이디': 'carol', '이름': '홍 길동'},
]


def _dm_context(keywords, user_id="alice", user_name="한참"):
    return CommandContext(
        user_id=user_id,
        user_name=user_name,
        keywords=keywords,
        metadata={'visibility': 'direct'},
    )


def _make_system(vote_rows):
    """'투표' 시트를 흉내내는 system_sheets_manager mock."""
    sm = MagicMock()
    appended = []
    updated = []

    def _get_data(sheet, use_cache=False):
        return vote_rows if sheet == '투표' else []

    def _append(sheet, values):
        appended.append((sheet, values))
        return True

    def _update(sheet, row, col, value):
        updated.append((sheet, row, col, value))
        return True

    sm.get_worksheet_data.side_effect = _get_data
    sm.append_row.side_effect = _append
    sm.update_cell.side_effect = _update
    sm._appended = appended
    sm._updated = updated
    return sm


def _cmd(system, roster=None):
    cmd = VoteCommand(sheets_manager=MagicMock(), system_sheets_manager=system)
    return cmd, patch(
        'commands.shinarmont.vote_command.load_user_data',
        return_value=ROSTER if roster is None else roster,
    )


class VoteCommandTest(unittest.TestCase):
    def setUp(self):
        # 일차 결정성 확보
        self._orig_current_day = game_day.current_day
        game_day.current_day = lambda: 3
        # vote_command 모듈이 import 시점에 참조를 바인딩하므로 그쪽도 교체
        import commands.shinarmont.vote_command as vc
        self._vc = vc
        self._orig_vc_day = vc.current_day
        vc.current_day = lambda: 3

    def tearDown(self):
        game_day.current_day = self._orig_current_day
        self._vc.current_day = self._orig_vc_day

    # ---- 정상 접수 ----
    def test_vote_appends_row(self):
        system = _make_system(vote_rows=[])
        cmd, roster = _cmd(system)
        with roster:
            resp = cmd.execute(_dm_context(['투표', '데보라', '유죄']))

        self.assertTrue(resp.is_successful(), resp.message)
        sheet, values = system._appended[0]
        self.assertEqual(sheet, '투표')
        # 일차 / 대상 / 투표자(캐릭터 이름) / 표
        self.assertEqual(values, [3, '데보라', '한참', '유죄'])

    def test_reversed_order_still_works(self):
        """'유죄'/'무죄'는 예약어라 자리가 뒤바뀌어도 알아본다."""
        system = _make_system(vote_rows=[])
        cmd, roster = _cmd(system)
        with roster:
            resp = cmd.execute(_dm_context(['투표', '유죄', '데보라']))
        self.assertTrue(resp.is_successful(), resp.message)
        self.assertEqual(system._appended[0][1], [3, '데보라', '한참', '유죄'])

    def test_self_vote_allowed(self):
        """피고가 스스로 무죄를 던지는 건 자연스럽다 — 막지 않는다."""
        system = _make_system(vote_rows=[])
        cmd, roster = _cmd(system)
        with roster:
            resp = cmd.execute(_dm_context(['투표', '한참', '무죄']))
        self.assertTrue(resp.is_successful(), resp.message)
        self.assertEqual(system._appended[0][1], [3, '한참', '한참', '무죄'])

    # ---- 조건 1: 명단에 없는 사람 ----
    def test_unknown_target_rejected(self):
        system = _make_system(vote_rows=[])
        cmd, roster = _cmd(system)
        with roster:
            resp = cmd.execute(_dm_context(['투표', '없는사람', '유죄']))
        self.assertFalse(resp.is_successful())
        self.assertIn('명단에 없는', resp.message)
        self.assertEqual(len(system._appended), 0)

    def test_target_matching_ignores_space(self):
        """저장은 명단의 정규 표기를 쓴다."""
        system = _make_system(vote_rows=[])
        cmd, roster = _cmd(system)
        with roster:
            resp = cmd.execute(_dm_context(['투표', '홍길동', '유죄']))
        self.assertTrue(resp.is_successful(), resp.message)
        self.assertEqual(system._appended[0][1][1], '홍 길동')

    def test_roster_unavailable_rejected(self):
        system = _make_system(vote_rows=[])
        cmd, _ = _cmd(system)
        with patch('commands.shinarmont.vote_command.load_user_data', return_value=[]):
            resp = cmd.execute(_dm_context(['투표', '데보라', '유죄']))
        self.assertFalse(resp.is_successful())
        self.assertEqual(len(system._appended), 0)

    # ---- 조건 2: 유죄/무죄만 ----
    def test_invalid_verdict_rejected(self):
        system = _make_system(vote_rows=[])
        cmd, roster = _cmd(system)
        with roster:
            resp = cmd.execute(_dm_context(['투표', '데보라', '보류']))
        self.assertFalse(resp.is_successful())
        self.assertIn('유죄', resp.message)
        self.assertEqual(len(system._appended), 0)

    def test_verdict_only_rejected(self):
        """대상 없이 표만 던지면 거절. 단상 자동 지정은 없어졌다."""
        system = _make_system(vote_rows=[])
        cmd, roster = _cmd(system)
        with roster:
            resp = cmd.execute(_dm_context(['투표', '유죄']))
        self.assertFalse(resp.is_successful())
        self.assertEqual(len(system._appended), 0)

    def test_no_args_rejected(self):
        system = _make_system(vote_rows=[])
        cmd, roster = _cmd(system)
        with roster:
            resp = cmd.execute(_dm_context(['투표']))
        self.assertFalse(resp.is_successful())
        self.assertEqual(len(system._appended), 0)

    # ---- 재투표 = 표 변경 (행을 더하지 않는다) ----
    def test_revote_updates_existing_row(self):
        system = _make_system(vote_rows=[
            {'일차': '3', '대상': '데보라', '투표자': '한참', '표': '유죄', '_row_number': 7},
        ])
        cmd, roster = _cmd(system)
        with roster:
            resp = cmd.execute(_dm_context(['투표', '데보라', '무죄']))

        self.assertTrue(resp.is_successful(), resp.message)
        self.assertIn('변경', resp.message)
        self.assertEqual(len(system._appended), 0, "재투표가 행을 추가하면 GM이 중복으로 센다")
        self.assertEqual(system._updated, [('투표', 7, 4, '무죄')])
        self.assertTrue(resp.data['변경'])

    def test_same_verdict_twice_is_noop(self):
        system = _make_system(vote_rows=[
            {'일차': '3', '대상': '데보라', '투표자': '한참', '표': '유죄', '_row_number': 7},
        ])
        cmd, roster = _cmd(system)
        with roster:
            resp = cmd.execute(_dm_context(['투표', '데보라', '유죄']))

        self.assertTrue(resp.is_successful(), resp.message)
        self.assertIn('이미', resp.message)
        self.assertEqual(len(system._appended), 0)
        self.assertEqual(len(system._updated), 0, "같은 표면 시트를 건드릴 이유가 없다")

    def test_other_day_is_a_new_row(self):
        system = _make_system(vote_rows=[
            {'일차': '2', '대상': '데보라', '투표자': '한참', '표': '유죄', '_row_number': 7},
        ])
        cmd, roster = _cmd(system)
        with roster:
            resp = cmd.execute(_dm_context(['투표', '데보라', '유죄']))
        self.assertTrue(resp.is_successful(), resp.message)
        self.assertEqual(len(system._appended), 1)
        self.assertEqual(len(system._updated), 0)

    def test_other_target_same_day_is_a_new_row(self):
        """같은 일차라도 대상이 다르면 별개의 표다."""
        system = _make_system(vote_rows=[
            {'일차': '3', '대상': '데보라', '투표자': '한참', '표': '유죄', '_row_number': 7},
        ])
        cmd, roster = _cmd(system)
        with roster:
            resp = cmd.execute(_dm_context(['투표', '홍길동', '무죄']))
        self.assertTrue(resp.is_successful(), resp.message)
        self.assertEqual(system._appended[0][1], [3, '홍 길동', '한참', '무죄'])

    def test_other_voter_same_target_is_a_new_row(self):
        system = _make_system(vote_rows=[
            {'일차': '3', '대상': '데보라', '투표자': '한참', '표': '유죄', '_row_number': 7},
        ])
        cmd, roster = _cmd(system)
        with roster:
            resp = cmd.execute(_dm_context(['투표', '데보라', '무죄'],
                                           user_id='carol', user_name='홍 길동'))
        self.assertTrue(resp.is_successful(), resp.message)
        self.assertEqual(system._appended[0][1], [3, '데보라', '홍 길동', '무죄'])

    def test_missing_row_number_rejects_rather_than_duplicating(self):
        """행 번호를 모르면 덮어쓸 수 없다. 중복 행을 만드느니 거절한다."""
        system = _make_system(vote_rows=[
            {'일차': '3', '대상': '데보라', '투표자': '한참', '표': '유죄'},
        ])
        cmd, roster = _cmd(system)
        with roster:
            resp = cmd.execute(_dm_context(['투표', '데보라', '무죄']))
        self.assertFalse(resp.is_successful())
        self.assertEqual(len(system._appended), 0)

    def test_vote_sheet_read_failure_blocks(self):
        """조회 실패 시 append 하면 이미 던진 표 위에 한 행이 더 쌓인다."""
        system = _make_system(vote_rows=[])
        system.get_worksheet_data.side_effect = RuntimeError('429')
        cmd, roster = _cmd(system)
        with roster:
            resp = cmd.execute(_dm_context(['투표', '데보라', '유죄']))
        self.assertFalse(resp.is_successful())
        self.assertEqual(len(system._appended), 0)

    # ---- 투표자 이름 ----
    def test_voter_recorded_as_character_name(self):
        """'대상' 열이 이름이라 '투표자'도 이름이어야 GM이 대조할 수 있다."""
        system = _make_system(vote_rows=[])
        cmd, roster = _cmd(system)
        with roster:
            resp = cmd.execute(_dm_context(['투표', '데보라', '유죄'],
                                           user_id='@Alice', user_name='표시명바뀜'))
        self.assertTrue(resp.is_successful(), resp.message)
        self.assertEqual(system._appended[0][1][2], '한참')

    def test_unregistered_voter_falls_back_to_display_name(self):
        """투표자 미등록은 GM의 데이터 문제다 — 표를 버리진 않는다."""
        system = _make_system(vote_rows=[])
        cmd, roster = _cmd(system)
        with roster:
            resp = cmd.execute(_dm_context(['투표', '데보라', '유죄'],
                                           user_id='ghost', user_name='유령'))
        self.assertTrue(resp.is_successful(), resp.message)
        self.assertEqual(system._appended[0][1][2], '유령')

    # ---- DM 전용 ----
    def test_non_dm_blocked(self):
        system = _make_system(vote_rows=[])
        cmd, roster = _cmd(system)
        ctx = CommandContext(
            user_id='alice', user_name='한참',
            keywords=['투표', '데보라', '유죄'],
            metadata={'visibility': 'public'},
        )
        with roster:
            resp = cmd.execute(ctx)
        self.assertFalse(resp.is_successful())
        self.assertEqual(len(system._appended), 0)

    # ---- 시트 미연결 ----
    def test_no_system_sheets_manager(self):
        cmd = VoteCommand(sheets_manager=MagicMock(), system_sheets_manager=None)
        with patch('commands.shinarmont.vote_command.load_user_data', return_value=ROSTER):
            resp = cmd.execute(_dm_context(['투표', '데보라', '유죄']))
        self.assertFalse(resp.is_successful())


if __name__ == '__main__':
    unittest.main()

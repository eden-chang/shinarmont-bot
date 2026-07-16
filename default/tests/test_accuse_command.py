"""commands.shinarmont.accuse_command 단위/스모크 테스트."""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# game_state 는 디스크(state/game_state.json)에 영속하므로,
# 테스트가 실제 상태 파일을 건드리지 않도록 임시 경로로 격리한다.
import utils.game_state as gs
_tmp_dir = tempfile.mkdtemp(prefix='accuse_test_')
gs._state_manager = gs.GameStateManager(path=os.path.join(_tmp_dir, 'game_state.json'))

from commands.base_command import CommandContext
from commands.shinarmont.accuse_command import AccuseCommand


ROSTER = [
    {'이름': '한참', '아이디': 'alice', '은는': '은'},
    {'이름': '철수', '아이디': 'bob', '은는': '는'},
]

MANAGEMENT = [
    {'아이디': 'alice', '소지금': 100, '소지품': '', '건강': 50, '이성': 40, '_row_number': 3},
    {'아이디': 'bob', '소지금': 30, '소지품': '', '건강': 20, '이성': 10, '_row_number': 4},
]


def _make_command():
    sm = MagicMock()
    sm.get_roster_data.return_value = ROSTER
    sm.get_worksheet_data.return_value = MANAGEMENT
    sm.batch_update_cells.return_value = True
    ssm = MagicMock()
    ssm.append_row.return_value = True
    cmd = AccuseCommand(sheets_manager=sm, api=None, system_sheets_manager=ssm)
    return cmd, sm, ssm


def _dm_ctx(user_id='alice', keywords=None, original_text=''):
    ctx = CommandContext(
        user_id=user_id,
        keywords=keywords if keywords is not None else ['고발', '철수'],
        original_text=original_text,
        metadata={'visibility': 'direct'},
    )
    return ctx


class AccuseHappyPathTest(unittest.TestCase):
    def setUp(self):
        gs.get_game_state().reset_daily()
        # 이전 테스트 잔여 상태 완전 초기화
        gs._state_manager._state = {}

    def test_success_records_and_rewards(self):
        cmd, sm, ssm = _make_command()
        ctx = _dm_ctx(original_text='[고발/철수] 창고 근처에서 수상한 행동을 목격했습니다.')
        resp = cmd.execute(ctx)
        self.assertTrue(resp.success, resp.message)

        # 고발 시트 append 호출: [일차, 고발자, 대상, 사유, 처리='']
        self.assertTrue(ssm.append_row.called)
        first_call = ssm.append_row.call_args_list[0]
        self.assertEqual(first_call.args[0], '고발')
        row = first_call.args[1]
        self.assertEqual(row[1], '한참')       # 고발자
        self.assertEqual(row[2], '철수')       # 대상
        self.assertIn('수상한 행동', row[3])   # 사유
        self.assertEqual(row[4], '')           # 처리 공란

        # 소지금 보상 지급: 100 + 50 = 150, 소지금 컬럼(index)
        self.assertTrue(sm.batch_update_cells.called)
        ws, updates = sm.batch_update_cells.call_args.args
        self.assertEqual(ws, '관리')
        r, c, val = updates[0]
        self.assertEqual(r, 3)
        self.assertEqual(val, 150)

        # 행동로그 append (종류='고발') 호출 확인
        kinds = [c.kwargs.get('kind') for c in ssm.append_row.call_args_list if c.args and c.args[0] == '행동로그']
        # action_log.append 는 위치 인자로 시트명을 넘기므로 append_row 호출들 중 '행동로그' 존재 확인
        log_calls = [c for c in ssm.append_row.call_args_list if c.args and c.args[0] == '행동로그']
        self.assertEqual(len(log_calls), 1)

    def test_reason_extracted_with_bbcode_color(self):
        # original_text 에 BBCode 색상 태그가 섞여도 사유가 올바르게 추출되어야 함
        cmd, sm, ssm = _make_command()
        ctx = _dm_ctx(
            original_text='[color:ff0000][고발/철수][/color] 창고에서 수상한 행동을 봤습니다.'
        )
        resp = cmd.execute(ctx)
        self.assertTrue(resp.success, resp.message)
        first_call = ssm.append_row.call_args_list[0]
        self.assertEqual(first_call.args[0], '고발')
        row = first_call.args[1]
        # 사유에 명령 대괄호나 색상 태그가 섞여 들어가지 않아야 함
        self.assertEqual(row[3], '창고에서 수상한 행동을 봤습니다.')

    def test_daily_limit_blocks_second(self):
        cmd, sm, ssm = _make_command()
        ctx1 = _dm_ctx(original_text='[고발/철수] 첫 번째 고발 사유입니다.')
        self.assertTrue(cmd.execute(ctx1).success)
        ctx2 = _dm_ctx(original_text='[고발/철수] 두 번째 시도.')
        resp2 = cmd.execute(ctx2)
        self.assertFalse(resp2.success)
        self.assertIn('하루 1회', resp2.message)


class AccuseValidationTest(unittest.TestCase):
    def setUp(self):
        gs._state_manager._state = {}

    def test_non_dm_blocked(self):
        cmd, sm, ssm = _make_command()
        ctx = CommandContext(
            user_id='alice',
            keywords=['고발', '철수'],
            original_text='[고발/철수] 사유',
            metadata={'visibility': 'public'},
        )
        resp = cmd.execute(ctx)
        self.assertFalse(resp.success)
        self.assertFalse(ssm.append_row.called)

    def test_missing_reason(self):
        cmd, sm, ssm = _make_command()
        ctx = _dm_ctx(original_text='[고발/철수]')
        resp = cmd.execute(ctx)
        self.assertFalse(resp.success)
        self.assertIn('사유', resp.message)
        # 사유 없으면 일일 제한을 소비하지 않아야 함
        self.assertFalse(ssm.append_row.called)

    def test_self_accuse_blocked(self):
        cmd, sm, ssm = _make_command()
        ctx = _dm_ctx(keywords=['고발', '한참'], original_text='[고발/한참] 나를 고발')
        resp = cmd.execute(ctx)
        self.assertFalse(resp.success)
        self.assertFalse(ssm.append_row.called)

    def test_unknown_target(self):
        cmd, sm, ssm = _make_command()
        ctx = _dm_ctx(keywords=['고발', '없는사람'], original_text='[고발/없는사람] 사유입니다')
        resp = cmd.execute(ctx)
        self.assertFalse(resp.success)
        self.assertFalse(ssm.append_row.called)

    def test_missing_target_keyword(self):
        cmd, sm, ssm = _make_command()
        ctx = _dm_ctx(keywords=['고발'], original_text='[고발] 사유만 있음')
        resp = cmd.execute(ctx)
        self.assertFalse(resp.success)
        self.assertFalse(ssm.append_row.called)


if __name__ == '__main__':
    unittest.main()

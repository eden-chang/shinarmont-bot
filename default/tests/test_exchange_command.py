"""commands/shinarmont/exchange_command.py 단위/스모크 테스트 (페이크 매니저)."""

import os
import sys
import unittest

from utils import action_log
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from commands.base_command import CommandContext
from commands.shinarmont import exchange_command
from commands.shinarmont.exchange_command import ExchangeCommand


ROSTER = [
    {'이름': '가나', '아이디': 'alice', '은는': '는'},
    {'이름': '다람', '아이디': 'bob', '은는': '은'},
]

# 이성: alice 50, bob 90 (상한 100 근처)
MGMT = [
    {'이름': '가나', '아이디': 'alice', '건강': 80, '이성': 50, '_row_number': 3},
    {'이름': '다람', '아이디': 'bob', '건강': 70, '이성': 90, '_row_number': 4},
]


class _FakeStatus:
    def __init__(self, mentions):
        self.mentions = mentions


def _make_sheets():
    sm = MagicMock()
    sm.get_roster_data.return_value = ROSTER
    sm.get_worksheet_data.return_value = MGMT
    sm.batch_update_cells.return_value = True
    return sm


def _make_context(keywords, mentions=None, visibility='direct', user_id='alice', user_name='가나'):
    meta = {'visibility': visibility}
    if mentions is not None:
        meta['original_status'] = _FakeStatus(mentions)
    return CommandContext(user_id=user_id, user_name=user_name, keywords=keywords, metadata=meta)


class ExchangeHappyPathTest(unittest.TestCase):
    """농도 배수를 **테스트가 고정한다**.

    GM이 `.env`의 EXCHANGE_SANITY_PER_LEVEL을 조정하면(2026-07-16: 6→5)
    값에 묶인 단정이 빨개진다. 검증 대상은 '농도 × 배수만큼 양쪽이 회복하고
    상한 100에서 잘린다'는 규칙이지 배수 값이 아니다.
    """

    PER_LEVEL = 6

    def setUp(self):
        from config.settings import config
        self._saved = config.EXCHANGE_SANITY_PER_LEVEL
        config.EXCHANGE_SANITY_PER_LEVEL = self.PER_LEVEL
        self.addCleanup(setattr, config, 'EXCHANGE_SANITY_PER_LEVEL', self._saved)

    def _run(self, ctx, sm=None, system=None):
        sm = sm or _make_sheets()
        system = system or MagicMock()
        cmd = ExchangeCommand(sheets_manager=sm, api=None, system_sheets_manager=system)
        with patch.object(exchange_command, 'relay_dm') as relay, \
             patch.object(exchange_command.stat_gate, 'apply_sanity_messages') as sanity, \
             patch.object(exchange_command, 'invalidate_user_cache') as inval:
            resp = cmd.execute(ctx)
        return resp, sm, system, relay, sanity, inval

    def test_exchange_updates_both_sanity_with_cap(self):
        ctx = _make_context(['교류', '3'], mentions=[{'acct': 'bob'}])
        resp, sm, system, relay, sanity, inval = self._run(ctx)

        self.assertTrue(resp.success, resp.message)
        # gain = 3 * 6 = 18. alice 50->68 (+18), bob 90->100 (+10, 상한)
        args = sm.batch_update_cells.call_args
        self.assertEqual(args[0][0], '관리')
        updates = args[0][1]
        self.assertIn((3, 4, 68), updates)   # alice 이성열=4번째 컬럼
        self.assertIn((4, 4, 100), updates)  # bob 상한 100
        # 상대 DM 알림 (실제 회복치 +10 반영)
        relay.assert_called_once()
        self.assertEqual(relay.call_args[0][0], 'bob')
        self.assertIn('10', relay.call_args[0][1])
        # 양쪽 이성 문구 검사
        self.assertEqual(sanity.call_count, 2)
        inval.assert_called_once()
        # 행동로그 append 실행됨
        system.append_row.assert_called_once()
        log_args = system.append_row.call_args[0]
        self.assertEqual(log_args[0], '행동로그')
        self.assertEqual(len(log_args[1]), 7)  # 7열 고정 (2026-07-16: 시트 헤더와 일치)
        self.assertEqual(log_args[1][action_log.COLUMNS.index('종류')], '교류')  # 종류
        # 응답에 상대 이름을 되읊지 않는다(2026-07-16 수정4). 교류는 사적인 일이고
        # 상대에게는 DM으로 따로 알린다. 본인 이성 변동만 보여 준다.
        self.assertNotIn('다람', resp.message)
        self.assertIn('접수 완료', resp.message)

    def test_min_level_1(self):
        ctx = _make_context(['교류', '1'], mentions=[{'acct': 'bob'}])
        resp, sm, *_ = self._run(ctx)
        self.assertTrue(resp.success)
        updates = sm.batch_update_cells.call_args[0][1]
        # gain=6: alice 50->56, bob 90->96
        self.assertIn((3, 4, 56), updates)
        self.assertIn((4, 4, 96), updates)


class ExchangeValidationTest(unittest.TestCase):
    def _exec(self, ctx):
        sm = _make_sheets()
        cmd = ExchangeCommand(sheets_manager=sm, api=None, system_sheets_manager=MagicMock())
        with patch.object(exchange_command, 'relay_dm'), \
             patch.object(exchange_command.stat_gate, 'apply_sanity_messages'), \
             patch.object(exchange_command, 'invalidate_user_cache'):
            return cmd.execute(ctx), sm

    def test_dm_only_guard_blocks_public(self):
        ctx = _make_context(['교류', '3'], mentions=[{'acct': 'bob'}], visibility='public')
        resp, sm = self._exec(ctx)
        self.assertFalse(resp.success)
        sm.batch_update_cells.assert_not_called()

    def test_level_zero_rejected(self):
        ctx = _make_context(['교류', '0'], mentions=[{'acct': 'bob'}])
        resp, sm = self._exec(ctx)
        self.assertFalse(resp.success)
        sm.batch_update_cells.assert_not_called()

    def test_level_six_rejected(self):
        ctx = _make_context(['교류', '6'], mentions=[{'acct': 'bob'}])
        resp, sm = self._exec(ctx)
        self.assertFalse(resp.success)

    def test_non_digit_rejected(self):
        ctx = _make_context(['교류', '세'], mentions=[{'acct': 'bob'}])
        resp, sm = self._exec(ctx)
        self.assertFalse(resp.success)

    def test_unicode_superscript_rejected_not_crash(self):
        # '²'.isdigit()==True 이지만 int() 불가 → 크래시 대신 검증 오류로 응답
        ctx = _make_context(['교류', '²'], mentions=[{'acct': 'bob'}])
        resp, sm = self._exec(ctx)
        self.assertFalse(resp.success)
        self.assertIn('농도', resp.message)
        sm.batch_update_cells.assert_not_called()

    def test_missing_level_rejected(self):
        ctx = _make_context(['교류'], mentions=[{'acct': 'bob'}])
        resp, sm = self._exec(ctx)
        self.assertFalse(resp.success)

    def test_no_mention_rejected(self):
        ctx = _make_context(['교류', '3'], mentions=[])
        resp, sm = self._exec(ctx)
        self.assertFalse(resp.success)
        sm.batch_update_cells.assert_not_called()

    def test_self_target_rejected(self):
        ctx = _make_context(['교류', '3'], mentions=[{'acct': 'alice'}])
        resp, sm = self._exec(ctx)
        self.assertFalse(resp.success)


if __name__ == '__main__':
    unittest.main()


class ExchangeGainScaleTest(unittest.TestCase):
    """농도 × 5 = 회복량 (2026-07-16 확정). 양쪽 모두 회복한다."""

    def setUp(self):
        from config.settings import config
        self.config = config
        self._saved = config.EXCHANGE_SANITY_PER_LEVEL
        config.EXCHANGE_SANITY_PER_LEVEL = 5
        self.addCleanup(setattr, config, 'EXCHANGE_SANITY_PER_LEVEL', self._saved)

    def _updates(self, level):
        sm = _make_sheets()
        cmd = ExchangeCommand(sheets_manager=sm, api=None, system_sheets_manager=MagicMock())
        ctx = _make_context(['교류', str(level)], mentions=[{'acct': 'bob'}])
        with patch.object(exchange_command, 'relay_dm'), \
             patch.object(exchange_command.stat_gate, 'apply_sanity_messages'), \
             patch.object(exchange_command, 'invalidate_user_cache'):
            resp = cmd.execute(ctx)
        return resp, sm.batch_update_cells.call_args[0][1]

    def test_level_scales_by_five(self):
        """1단계=5 · 2단계=10 · 3단계=15 · 4단계=20 · 5단계=25."""
        for level in range(1, 6):
            resp, updates = self._updates(level)
            self.assertTrue(resp.success, resp.message)
            expected = min(100, 50 + level * 5)          # alice 이성 50 시작
            self.assertIn((3, 4, expected), updates,
                          f"농도 {level} → 이성 +{level * 5} 여야 한다")

    def test_both_characters_recover(self):
        """시전자와 상대 **둘 다** 회복한다."""
        _resp, updates = self._updates(2)
        rows = {row for row, _col, _val in updates}
        self.assertEqual(rows, {3, 4}, "양쪽 행이 모두 갱신돼야 한다")

    def test_response_shows_only_own_gain(self):
        resp, _ = self._updates(1)
        self.assertIn('접수 완료', resp.message)
        self.assertIn('➭ 이성 +5', resp.message)
        self.assertNotIn('농도', resp.message, "상대 이름·농도를 되읊지 않는다")

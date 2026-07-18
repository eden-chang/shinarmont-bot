"""조사 제한 기능 테스트.

1) 조사 자격 박탈(INVESTIGATION_DISQUALIFIED) — [장소 목록]·[진입]·[조사] 모두 차단.
2) 다른 장소의 포인트를 현재 위치에서 조사하려 할 때의 '이동 후 조사' 안내.
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from commands.base_command import CommandContext
from commands.investigate.base_investigate import is_disqualified
from commands.investigate.investigate_command import InvestigateCommand
from commands.investigate.enter_command import EnterCommand
from commands.investigate.location_list_command import LocationListCommand
from config.settings import config
from tests.investigation_fixtures import (
    entry_row, investigation_sheets, main_sheets, mgmt_row, point_row,
)
from utils.investigation_state import InvestigationStateManager
import utils.investigation_state as state_module
import utils.investigation_notify as notify_module


class DisqualifyParseTest(unittest.TestCase):
    """is_disqualified 파서 단위 테스트."""

    def test_single_day(self):
        self.assertTrue(is_disqualified('evaristo:4', 'evaristo', 4))
        self.assertFalse(is_disqualified('evaristo:4', 'evaristo', 3))

    def test_all_days_when_no_day(self):
        self.assertTrue(is_disqualified('evaristo', 'evaristo', 1))
        self.assertTrue(is_disqualified('evaristo:*', 'evaristo', 99))

    def test_range(self):
        self.assertTrue(is_disqualified('evaristo:4-6', 'evaristo', 5))
        self.assertFalse(is_disqualified('evaristo:4-6', 'evaristo', 7))

    def test_multiple_and_normalization(self):
        raw = 'evaristo:4, mika:2'
        self.assertTrue(is_disqualified(raw, '@Evaristo', 4))   # @/대소문자 무시
        self.assertTrue(is_disqualified(raw, 'mika', 2))
        self.assertFalse(is_disqualified(raw, 'mika', 4))

    def test_other_user_unaffected(self):
        self.assertFalse(is_disqualified('evaristo:4', 'clara', 4))

    def test_empty(self):
        self.assertFalse(is_disqualified('', 'evaristo', 4))

    def test_trailing_colon_is_typo_not_all_days(self):
        # 'evaristo:' (콜론 뒤 빈값)은 오타로 보고 막지 않는다(전체 차단 오인 방지).
        self.assertFalse(is_disqualified('evaristo:', 'evaristo', 4))

    def test_malformed_day_spec_fails_open(self):
        self.assertFalse(is_disqualified('evaristo:4o', 'evaristo', 4))
        self.assertFalse(is_disqualified('evaristo:4-', 'evaristo', 4))


class _Base(unittest.TestCase):
    def setUp(self):
        state_module._state_manager = InvestigationStateManager()
        state_module.get_investigation_state().enter('alice', '연구소')
        notify_module.reset_once_cache()
        self._orig_notify = notify_module.notify_admin
        notify_module.notify_admin = (
            lambda m, api=None, prefix=None: True)

    def tearDown(self):
        notify_module.notify_admin = self._orig_notify

    def _managers(self, locations, mgmt=None, entry=None):
        inv = investigation_sheets(
            entry=entry or [entry_row('연구소', available=True), entry_row('광산', available=True)],
            locations=locations,
            logs=[],
        )
        main = main_sheets([mgmt or mgmt_row(role='연구자')])
        return inv, main

    def _investigate(self, locations, mgmt=None, entry=None):
        inv, main = self._managers(locations, mgmt, entry)
        return InvestigateCommand(sheets_manager=main, api=None,
                                  investigation_sheets_manager=inv)


class DisqualifyIntegrationTest(_Base):
    """설정된 캐릭터는 세 명령 모두에서 차단된다."""

    def _run_all(self, day):
        # 진입 시트에 오른 모든 장소(연구소·광산)는 대응 시트가 있어야 한다(운영 불변식).
        locations = {
            '연구소': [point_row('전압계', roles='연구자', available=True)],
            '광산': [point_row('갱도', roles='연구자', available=True)],
        }
        inv, main = self._managers(locations)
        kwargs = dict(sheets_manager=main, api=None, investigation_sheets_manager=inv)
        with patch.object(config, 'INVESTIGATION_DISQUALIFIED', 'alice:4'), \
             patch('utils.game_day.current_day', return_value=day):
            return (
                InvestigateCommand(**kwargs).execute(
                    CommandContext(user_id='alice', keywords=['조사', '전압계'])),
                EnterCommand(**kwargs).execute(
                    CommandContext(user_id='alice', keywords=['진입', '연구소'])),
                LocationListCommand(**kwargs).execute(
                    CommandContext(user_id='alice', keywords=['장소 목록'])),
            )

    def test_blocked_on_matching_day(self):
        msg = config.investigation_message('DISQUALIFIED')
        for resp in self._run_all(day=4):
            self.assertEqual(resp.message, msg)

    def test_allowed_on_other_day(self):
        # 4일차가 아니면 박탈 문구가 아니어야 하고, 실제로 정상 동작해야 한다.
        investigate, enter, loclist = self._run_all(day=3)
        disq = config.investigation_message('DISQUALIFIED')
        for resp in (investigate, enter, loclist):
            self.assertNotEqual(resp.message, disq)
        self.assertTrue(investigate.is_successful(), investigate.message)
        self.assertTrue(enter.is_successful(), enter.message)
        self.assertTrue(loclist.is_successful(), loclist.message)


class WrongLocationHintTest(_Base):
    """현재 위치에 없는(다른 장소의) 포인트를 조사하려 하면 이동 안내.

    단, 정보 비대칭 유지를 위해 '지금 이 캐릭터가 갈 수 있는 열린 장소'에 있을 때만 안내한다.
    닫힌/미개방 장소나 직군 접근 불가 장소의 포인트명은 새지 않아야 한다(H1).
    """

    # 연구소(열림), 광산(열림), 폐광(닫힘), 병기고(열림·군인전용)
    ENTRY = [
        entry_row('연구소', available=True),
        entry_row('광산', available=True),
        entry_row('폐광', available=False),
        entry_row('병기고', available=True),
    ]
    LOCATIONS = {
        '연구소': [
            point_row('전압계', roles='연구자', available=True),
            point_row('금고', roles='군인', available=True),   # 현재 위치에 있으나 접근 불가
        ],
        '광산': [point_row('갱도', roles='연구자', available=True)],
        '폐광': [point_row('유물', roles='연구자', available=True)],   # 직군OK지만 장소가 닫힘
        '병기고': [point_row('탄약', roles='군인', available=True)],   # 장소 열림이나 직군 불가
    }

    def _cmd(self):
        return self._investigate(self.LOCATIONS, entry=self.ENTRY)

    def test_point_in_reachable_other_location_gives_move_hint(self):
        resp = self._cmd().execute(
            CommandContext(user_id='alice', keywords=['조사', '갱도']))
        self.assertTrue(resp.is_successful())
        self.assertIn('연구소', resp.message)       # 현재 위치를 알려준다
        self.assertIn('갱도', resp.message)          # 조사하려던 포인트
        self.assertIn('[진입/장소명]', resp.message)  # 이동 안내
        self.assertNotIn('광산', resp.message)        # 어느 장소인지는 밝히지 않는다

    def test_point_here_but_no_access_keeps_generic(self):
        # '금고'는 현재 위치(연구소)에 있으나 직군 불일치 → 정보 비대칭 유지(일반 문구)
        resp = self._cmd().execute(
            CommandContext(user_id='alice', keywords=['조사', '금고']))
        self.assertEqual(resp.message, config.investigation_message('NO_SUCH_POINT'))

    def test_point_only_in_closed_location_keeps_generic(self):
        # '유물'은 닫힌 장소(폐광)에만 있다 → 미개방/미래 콘텐츠 노출 방지(H1)
        resp = self._cmd().execute(
            CommandContext(user_id='alice', keywords=['조사', '유물']))
        self.assertEqual(resp.message, config.investigation_message('NO_SUCH_POINT'))

    def test_point_only_in_inaccessible_location_keeps_generic(self):
        # '탄약'은 열렸지만 군인 전용인 병기고에만 있다. alice=연구자 → 접근 불가(H1)
        resp = self._cmd().execute(
            CommandContext(user_id='alice', keywords=['조사', '탄약']))
        self.assertEqual(resp.message, config.investigation_message('NO_SUCH_POINT'))

    def test_point_nowhere_keeps_generic(self):
        resp = self._cmd().execute(
            CommandContext(user_id='alice', keywords=['조사', '없는포인트']))
        self.assertEqual(resp.message, config.investigation_message('NO_SUCH_POINT'))

    def test_hint_disabled_keeps_generic(self):
        with patch.object(config, 'INVESTIGATION_WRONG_LOCATION_HINT', False):
            resp = self._cmd().execute(
                CommandContext(user_id='alice', keywords=['조사', '갱도']))
        self.assertEqual(resp.message, config.investigation_message('NO_SUCH_POINT'))


if __name__ == '__main__':
    unittest.main()

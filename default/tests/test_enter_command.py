"""[진입/장소명] 명령어 테스트 (가이드 §3.2, §10-3 ~ §10-5)."""

import os
import random
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from commands.base_command import CommandContext
from commands.investigate.enter_command import EnterCommand
from config.settings import config
from tests.investigation_fixtures import (
    entry_row, exception_row, investigation_sheets, main_sheets, mgmt_row, point_row,
)
from utils.investigation_state import InvestigationStateManager
import utils.investigation_state as state_module
import utils.investigation_notify as notify_module


def _context(location, user_id='alice'):
    return CommandContext(user_id=user_id, user_name='한참', keywords=['진입', location])


class EnterTest(unittest.TestCase):
    def setUp(self):
        state_module._state_manager = InvestigationStateManager()
        notify_module.reset_once_cache()
        self.entry = [
            entry_row('연구소', available=True, text='분석실의 공기가 차다.'),
            entry_row('보안 경계', available=True, text='초소가 보인다.'),
            entry_row('광산', available=False, text='갱도가 어둡다.'),
        ]
        self.locations = {
            '연구소': [
                point_row('전압계', roles='연구자', available=True),
                point_row('진료 대장', roles='연구자', available=True),
                point_row('야금 실험대', roles='연구자, 기술공', available=True),
            ],
            '보안 경계': [point_row('정문 검문 기록', roles='군인', available=True)],
            '광산': [point_row('갱도 입구 경고판', roles='기술공', available=True)],
        }

    def _cmd(self, role='연구자', entry=None, locations=None, exceptions=None):
        self.inv = investigation_sheets(
            entry=entry if entry is not None else self.entry,
            locations=locations if locations is not None else self.locations,
            exceptions=exceptions or [],
        )
        return EnterCommand(
            sheets_manager=main_sheets([mgmt_row(role=role)]),
            api=None,
            investigation_sheets_manager=self.inv,
        )

    # -- 성공 -------------------------------------------------------------
    def test_researcher_enters_with_point_list(self):
        """§10-3: 연구자 [진입/연구소] → 진입 문구 + 접근 가능 포인트 목록."""
        response = self._cmd(role='연구자').execute(_context('연구소'))
        self.assertTrue(response.is_successful())
        # 장소명이 제목 줄로 선다 — 진입 문구만 오면 어디에 들어왔는지 본문에 묻힌다.
        self.assertTrue(response.message.startswith('연구소\n\n'))
        self.assertIn('분석실의 공기가 차다.', response.message)
        self.assertIn('[전압계]', response.message)
        self.assertIn('[진료 대장]', response.message)
        self.assertIn('[야금 실험대]', response.message)
        self.assertIn('조사할 수 있다.', response.message)

    def test_technician_sees_only_own_points(self):
        """§10-4: 기술공 [진입/연구소] → 야금 실험대만."""
        response = self._cmd(role='기술공').execute(_context('연구소'))
        self.assertTrue(response.is_successful())
        self.assertEqual(response.data['points'], ['야금 실험대'])
        self.assertNotIn('[전압계]', response.message)

    def test_sets_location_state(self):
        cmd = self._cmd(role='연구자')
        cmd.execute(_context('연구소'))
        self.assertEqual(state_module.get_investigation_state().get_location('alice'), '연구소')

    def test_logs_entry(self):
        cmd = self._cmd(role='연구자')
        cmd.execute(_context('연구소'))
        sheet, values = self.inv.appended[0]
        self.assertEqual(sheet, '로그')
        self.assertEqual(values[1:], ['한참', '연구소', '', '진입'])

    def test_repeated_entry_to_same_place_logs_once(self):
        """진입은 횟수 제한이 없다. 매번 로그를 쌓으면 로그가 무한 증식하고
        로그 전체를 읽는 [조사]의 판정 비용까지 커진다."""
        cmd = self._cmd(role='연구자')
        for _ in range(5):
            cmd.execute(_context('연구소'))
        self.assertEqual(len(self.inv.appended), 1)

    def test_moving_to_another_place_logs_again(self):
        cmd = self._cmd(role='연구자')
        cmd.execute(_context('연구소'))
        cmd.execute(_context('보안 경계'))   # 군인 전용이라 실패
        cmd = self._cmd(role='군인')
        cmd.execute(_context('보안 경계'))
        self.assertEqual(len(self.inv.appended), 1)  # 새 매니저라 1건

    def test_log_failure_warns_admin(self):
        """진입 로그가 없으면 재시작 시 위치 복원이 불가능해진다 → 관리자 알림."""
        notices = []
        orig = notify_module.notify_admin
        notify_module.notify_admin = lambda m, api=None, prefix=None: notices.append(m) or True
        try:
            cmd = self._cmd(role='연구자')
            self.inv.write_ok = False
            response = cmd.execute(_context('연구소'))
        finally:
            notify_module.notify_admin = orig
        # 진입 자체는 성립(메모리 상태)
        self.assertTrue(response.is_successful())
        self.assertTrue(any('진입 로그 기록 실패' in n for n in notices))

    def test_name_matching_ignores_spaces(self):
        response = self._cmd(role='군인').execute(_context('보안경계'))
        self.assertTrue(response.is_successful())
        self.assertEqual(response.data['location'], '보안 경계')

    # -- 실패 -------------------------------------------------------------
    def test_unknown_place(self):
        response = self._cmd().execute(_context('없는 곳'))
        self.assertEqual(response.message, config.investigation_message('NO_SUCH_PLACE'))

    def test_closed_location(self):
        """§10-5-2: 진입 행이 닫혀 있으면 차단."""
        response = self._cmd(role='기술공').execute(_context('광산'))
        self.assertEqual(response.message, config.investigation_message('CANNOT_ENTER'))

    def test_no_accessible_points(self):
        """§10-5: 가족 [진입/연구소] → 조사할 수 있는 것이 없다."""
        response = self._cmd(role='가족').execute(_context('연구소'))
        self.assertEqual(response.message, config.investigation_message('CANNOT_ENTER'))

    def test_closed_and_no_access_are_indistinguishable(self):
        """정보 비대칭: 장소가 닫힌 것인지 권한이 없는 것인지 구분해 주지 않는다."""
        closed = self._cmd(role='기술공').execute(_context('광산')).message
        no_access = self._cmd(role='가족').execute(_context('연구소')).message
        self.assertEqual(closed, no_access)

    def test_missing_argument(self):
        cmd = self._cmd()
        response = cmd.execute(CommandContext(user_id='alice', keywords=['진입']))
        self.assertEqual(response.message, config.investigation_message('NO_SUCH_PLACE'))

    def test_failure_does_not_set_state_or_log(self):
        cmd = self._cmd(role='가족')
        cmd.execute(_context('연구소'))
        self.assertIsNone(state_module.get_investigation_state().get_location('alice'))
        self.assertEqual(self.inv.appended, [])

    # -- 예외 -------------------------------------------------------------
    def test_exception_grants_entry(self):
        """§10-6: 예외 추가 후 가족 직군도 진입 성공, 목록에 해당 포인트 표시."""
        exceptions = [exception_row('한참', '연구소', '진료 대장')]
        response = self._cmd(role='가족', exceptions=exceptions).execute(_context('연구소'))
        self.assertTrue(response.is_successful())
        self.assertEqual(response.data['points'], ['진료 대장'])

    # -- 변주 -------------------------------------------------------------
    def test_entry_text_variants_random(self):
        """§2.1: '가능' 행이 둘 이상이면 무작위 선택 (문구 변주)."""
        entry = [
            entry_row('연구소', available=True, text='문구A'),
            entry_row('연구소', available=True, text='문구B'),
        ]
        random.seed(5)
        seen = set()
        for _ in range(60):
            response = self._cmd(role='연구자', entry=entry,
                                 locations={'연구소': self.locations['연구소']}).execute(
                _context('연구소'))
            for candidate in ('문구A', '문구B'):
                if candidate in response.message:
                    seen.add(candidate)
        self.assertEqual(seen, {'문구A', '문구B'})

    # -- 조사 문장 --------------------------------------------------------
    def test_josa_eul_for_final_consonant(self):
        locations = {'연구소': [point_row('전압계', roles='연구자', available=True),
                             point_row('부검실', roles='연구자', available=True)]}
        response = self._cmd(role='연구자', locations=locations).execute(_context('연구소'))
        self.assertIn('[부검실]을 조사할 수 있다.', response.message)

    def test_josa_reul_for_open_syllable(self):
        locations = {'연구소': [point_row('전압계', roles='연구자', available=True)]}
        response = self._cmd(role='연구자', locations=locations).execute(_context('연구소'))
        self.assertIn('[전압계]를 조사할 수 있다.', response.message)

    # -- 장애 -------------------------------------------------------------
    def test_sheet_failure_is_temporary_error(self):
        cmd = self._cmd(role='연구자')
        self.inv.fail_sheets.add('진입')
        response = cmd.execute(_context('연구소'))
        self.assertFalse(response.is_successful())
        self.assertEqual(response.message, config.investigation_message('TEMPORARY'))

    def test_unknown_character(self):
        cmd = EnterCommand(
            sheets_manager=main_sheets([mgmt_row(user_id='other')]), api=None,
            investigation_sheets_manager=investigation_sheets(
                entry=self.entry, locations=self.locations),
        )
        response = cmd.execute(_context('연구소'))
        self.assertEqual(response.message, config.investigation_message('NO_CHARACTER'))


if __name__ == '__main__':
    unittest.main()

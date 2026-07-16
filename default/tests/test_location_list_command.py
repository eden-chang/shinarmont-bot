"""[장소 목록] 명령어 테스트 (가이드 §3.1, §10-1 / §10-5-1)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from commands.base_command import CommandContext
from commands.investigate.location_list_command import LocationListCommand
from config.settings import config
from tests.investigation_fixtures import (
    entry_row, exception_row, investigation_sheets, main_sheets, mgmt_row, point_row,
)
import utils.investigation_notify as notify_module


def _context(user_id='alice'):
    return CommandContext(user_id=user_id, user_name='한참', keywords=['장소 목록'])


class LocationListTest(unittest.TestCase):
    def setUp(self):
        notify_module.reset_once_cache()
        self.entry = [
            entry_row('중앙 광장', available=True),
            entry_row('연구소', available=True),
            entry_row('광산', available=True),
        ]
        self.locations = {
            '중앙 광장': [point_row('수위계', roles='연구자, 기술공, 군인, 행정관, 가족',
                                 available=True)],
            '연구소': [point_row('전압계', roles='연구자', available=True)],
            '광산': [point_row('갱도 입구 경고판', roles='기술공', available=True)],
        }

    def _cmd(self, role='연구자', entry=None, locations=None, exceptions=None):
        return LocationListCommand(
            sheets_manager=main_sheets([mgmt_row(role=role)]),
            api=None,
            investigation_sheets_manager=investigation_sheets(
                entry=entry if entry is not None else self.entry,
                locations=locations if locations is not None else self.locations,
                exceptions=exceptions or [],
            ),
        )

    def test_researcher_sees_open_locations_with_accessible_points(self):
        response = self._cmd(role='연구자').execute(_context())
        self.assertTrue(response.is_successful())
        self.assertEqual(response.data['locations'], ['중앙 광장', '연구소'])
        self.assertTrue(response.message.startswith('진입 가능 장소\n\n'))
        self.assertIn('[진입/장소명]으로 조사 시작', response.message)

    def test_technician_sees_different_set(self):
        """§3.1 예: 기술공이 조사할 포인트가 있는 장소만."""
        response = self._cmd(role='기술공').execute(_context())
        self.assertEqual(response.data['locations'], ['중앙 광장', '광산'])

    def test_family_excluded_from_role_locked_locations(self):
        """§10-5-1: 가족은 연구소·광산이 목록에서 빠진다."""
        response = self._cmd(role='가족').execute(_context())
        self.assertEqual(response.data['locations'], ['중앙 광장'])

    def test_closed_entry_excluded(self):
        """§10-5-2: 진입 행이 닫히면 포인트가 열려 있어도 목록에서 빠진다."""
        entry = [entry_row('중앙 광장', available=True), entry_row('연구소', available=False)]
        response = self._cmd(role='연구자', entry=entry).execute(_context())
        self.assertEqual(response.data['locations'], ['중앙 광장'])

    def test_all_closed_shows_nothing(self):
        """§10-1: 개장 직후 전 행이 불가능 → 아무 장소도 나오지 않음."""
        entry = [entry_row('중앙 광장'), entry_row('연구소')]
        response = self._cmd(role='연구자', entry=entry).execute(_context())
        self.assertTrue(response.is_successful())
        self.assertEqual(response.message, config.investigation_message('NO_LOCATIONS'))

    def test_open_entry_without_accessible_points_excluded(self):
        """진입은 '가능'인데 그 직군이 볼 포인트가 없으면 목록에서 빠진다."""
        response = self._cmd(role='행정관').execute(_context())
        self.assertEqual(response.data['locations'], ['중앙 광장'])

    def test_exception_adds_location(self):
        """§2.3-5: 예외로 조사 가능한 포인트가 생기면 진입 조건 2가 충족된다."""
        exceptions = [exception_row('한참', '연구소', '전압계')]
        response = self._cmd(role='가족', exceptions=exceptions).execute(_context())
        self.assertEqual(response.data['locations'], ['중앙 광장', '연구소'])

    def test_duplicate_location_names_deduplicated(self):
        """§2.1: 같은 장소명이 여러 행이어도 1회만 표시."""
        entry = [
            entry_row('중앙 광장', available=True, text='A'),
            entry_row('중앙 광장', available=True, text='B'),
        ]
        response = self._cmd(
            role='연구자', entry=entry,
            locations={'중앙 광장': self.locations['중앙 광장']},
        ).execute(_context())
        self.assertEqual(response.data['locations'], ['중앙 광장'])

    def test_unknown_character_refused(self):
        cmd = LocationListCommand(
            sheets_manager=main_sheets([mgmt_row(user_id='someone-else')]),
            api=None,
            investigation_sheets_manager=investigation_sheets(
                entry=self.entry, locations=self.locations),
        )
        response = cmd.execute(_context())
        self.assertEqual(response.message, config.investigation_message('NO_CHARACTER'))

    def test_entry_sheet_failure_is_temporary_error(self):
        """시트 장애를 '장소 없음'으로 오해시키지 않는다."""
        inv = investigation_sheets(entry=self.entry, locations=self.locations)
        inv.fail_sheets.add('진입')
        cmd = LocationListCommand(
            sheets_manager=main_sheets([mgmt_row()]), api=None,
            investigation_sheets_manager=inv,
        )
        response = cmd.execute(_context())
        self.assertFalse(response.is_successful())
        self.assertEqual(response.message, config.investigation_message('TEMPORARY'))

    def test_missing_location_sheet_fails_loudly(self):
        """장소 시트를 못 읽으면 그 장소만 조용히 빼지 않고 명령 전체를 실패시킨다.

        건너뛰면 러너에게는 장소가 사라진 것처럼 보이고(어제는 보였는데?)
        아무도 원인을 모른 채 목록이 계속 틀리게 나간다.
        """
        inv = investigation_sheets(entry=self.entry, locations=self.locations)
        inv.fail_sheets.add('연구소')
        notices = []
        orig = notify_module.notify_admin
        notify_module.notify_admin = lambda m, api=None, prefix=None: notices.append(m) or True
        try:
            cmd = LocationListCommand(
                sheets_manager=main_sheets([mgmt_row(role='연구자')]), api=None,
                investigation_sheets_manager=inv,
            )
            response = cmd.execute(_context())
        finally:
            notify_module.notify_admin = orig

        self.assertFalse(response.is_successful())
        self.assertEqual(response.message, config.investigation_message('TEMPORARY'))
        self.assertTrue(any('연구소' in n for n in notices))


if __name__ == '__main__':
    unittest.main()

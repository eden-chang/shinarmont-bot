"""utils/investigation_open.py — 조사 자동 개방 테스트 (가이드 §6)."""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from config.settings import config
from tests.investigation_fixtures import entry_row, investigation_sheets, point_row
from utils.investigation_open import (
    KST, UNAVAILABLE_VALUE, is_executed, load_state, mark_executed, pending_opens,
    plan_sheet, run_open, scheduled_opens,
)
from utils.investigation_sheet import COL_LOCATION, COL_POINT
import utils.investigation_notify as notify_module


class _StateFileMixin:
    """실행 기록 파일을 임시 경로로 격리."""

    def setUp(self):
        super().setUp()
        self._tmp = tempfile.mkdtemp()
        self._orig_state = config.INVESTIGATION_OPEN_STATE_FILE
        config.INVESTIGATION_OPEN_STATE_FILE = os.path.join(self._tmp, 'opens.json')
        type(config).INVESTIGATION_OPEN_STATE_FILE = config.INVESTIGATION_OPEN_STATE_FILE
        notify_module.reset_once_cache()
        self.notices = []
        self._orig_notify = notify_module.notify_admin
        notify_module.notify_admin = (
            lambda msg, api=None, prefix=None: self.notices.append(msg) or True)
        import utils.investigation_open as open_module
        self._orig_open_notify = open_module.notify_admin
        open_module.notify_admin = notify_module.notify_admin

    def tearDown(self):
        config.INVESTIGATION_OPEN_STATE_FILE = self._orig_state
        type(config).INVESTIGATION_OPEN_STATE_FILE = self._orig_state
        notify_module.notify_admin = self._orig_notify
        import utils.investigation_open as open_module
        open_module.notify_admin = self._orig_open_notify
        super().tearDown()


class ScheduleTest(unittest.TestCase):
    def test_default_schedule_matches_guide(self):
        """§6.2 일차 ↔ 실행 일시 매핑."""
        opens = dict((label, when) for label, when in scheduled_opens())
        self.assertEqual(len(opens), 8)
        self.assertEqual(opens['1주-4일차'].strftime('%Y-%m-%d %H:%M'), '2026-07-20 21:00')
        self.assertEqual(opens['4주-26일차'].strftime('%Y-%m-%d %H:%M'), '2026-08-11 21:00')

    def test_sorted_by_time(self):
        times = [when for _, when in scheduled_opens()]
        self.assertEqual(times, sorted(times))


class PlanSheetTest(unittest.TestCase):
    """개방 계획 계산 (시트 쓰기 없음)."""

    def _rows(self, rows):
        fake = investigation_sheets(locations={'X': rows})
        return fake.get_worksheet_data('X')

    def test_opens_matching_day(self):
        rows = self._rows([point_row('수위계', day='1주-4일차', available=False)])
        updates, opened, closed = plan_sheet(rows, COL_POINT, '1주-4일차')
        self.assertEqual((opened, closed), (1, 0))
        self.assertEqual(updates, [(3, 1, '가능')])

    def test_idempotent_when_already_open(self):
        """§6.3-3 멱등성: 이미 가능인 행은 건너뛴다."""
        rows = self._rows([point_row('수위계', day='1주-4일차', available=True)])
        updates, opened, closed = plan_sheet(rows, COL_POINT, '1주-4일차')
        self.assertEqual(updates, [])
        self.assertEqual((opened, closed), (0, 0))

    def test_version_transition_closes_previous(self):
        """§6.1: 부검실 3주-16일차를 켜고 1주-4일차를 끈다."""
        rows = self._rows([
            point_row('부검실', day='1주-4일차', available=True),
            point_row('부검실', day='3주-16일차', available=False),
        ])
        updates, opened, closed = plan_sheet(rows, COL_POINT, '3주-16일차')
        self.assertEqual((opened, closed), (1, 1))
        self.assertIn((4, 1, '가능'), updates)
        self.assertIn((3, 1, UNAVAILABLE_VALUE), updates)

    def test_untouched_names_keep_state(self):
        """§6.3-1 단방향 원칙: 이번 일차에 새 행이 없는 이름은 건드리지 않는다."""
        rows = self._rows([
            point_row('수위계', day='1주-4일차', available=True),
            point_row('부검실', day='3주-16일차', available=False),
        ])
        updates, opened, closed = plan_sheet(rows, COL_POINT, '3주-16일차')
        self.assertEqual((opened, closed), (1, 0))
        self.assertEqual(updates, [(4, 1, '가능')])

    def test_variant_set_opens_together(self):
        """§6.1 주의: 같은 이름·같은 오픈 일자 행들은 함께 켜진다."""
        rows = self._rows([
            point_row('급수대 줄', day='1주-4일차', available=False, text='A'),
            point_row('급수대 줄', day='1주-4일차', available=False, text='B'),
        ])
        updates, opened, closed = plan_sheet(rows, COL_POINT, '1주-4일차')
        self.assertEqual((opened, closed), (2, 0))

    def test_variant_set_not_closed_by_own_day(self):
        rows = self._rows([
            point_row('급수대 줄', day='1주-4일차', available=True, text='A'),
            point_row('급수대 줄', day='1주-4일차', available=False, text='B'),
        ])
        updates, opened, closed = plan_sheet(rows, COL_POINT, '1주-4일차')
        self.assertEqual((opened, closed), (1, 0))
        self.assertEqual(closed, 0)

    def test_no_rows_for_day_is_noop(self):
        rows = self._rows([point_row('수위계', day='1주-4일차', available=True)])
        updates, opened, closed = plan_sheet(rows, COL_POINT, '2주-8일차')
        self.assertEqual(updates, [])

    def test_entry_sheet_version_transition(self):
        fake = investigation_sheets(entry=[
            entry_row('중앙 광장', day='1주-4일차', available=True),
            entry_row('중앙 광장', day='2주-8일차', available=False),
            entry_row('보안 경계', day='1주-4일차', available=True),
        ])
        rows = fake.get_worksheet_data('진입')
        updates, opened, closed = plan_sheet(rows, COL_LOCATION, '2주-8일차')
        self.assertEqual((opened, closed), (1, 1))
        # 보안 경계(3행)는 2주-8일차 행이 없으므로 건드리지 않는다
        self.assertNotIn(5, [u[0] for u in updates])


class RunOpenTest(_StateFileMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.fake = investigation_sheets(
            entry=[
                entry_row('중앙 광장', day='1주-4일차', available=False),
                entry_row('연구소', day='1주-4일차', available=False),
            ],
            locations={
                '중앙 광장': [point_row('수위계', day='1주-4일차', available=False)],
                '연구소': [point_row('전압계', day='1주-4일차', available=False)],
            },
        )

    def test_opens_points_and_entries(self):
        result = run_open(self.fake, '1주-4일차')
        self.assertEqual(result['opened'], 4)  # 포인트 2 + 진입 2
        self.assertEqual(self.fake.column('진입', '현재 조사 가능'), ['가능', '가능'])
        self.assertEqual(self.fake.column('중앙 광장', '현재 조사 가능'), ['가능'])

    def test_admin_notified_on_completion(self):
        """§6.3-5: '{일차} 조사 {n}건 개방 완료'"""
        run_open(self.fake, '1주-4일차')
        self.assertTrue(any('1주-4일차 조사 4건 개방 완료' in n for n in self.notices))

    def test_records_execution(self):
        run_open(self.fake, '1주-4일차')
        self.assertTrue(is_executed('1주-4일차'))

    def test_record_false_does_not_mark(self):
        run_open(self.fake, '1주-4일차', record=False)
        self.assertFalse(is_executed('1주-4일차'))

    def test_rerun_is_idempotent(self):
        run_open(self.fake, '1주-4일차')
        result = run_open(self.fake, '1주-4일차')
        self.assertEqual(result['opened'], 0)

    def test_manual_close_survives_rerun(self):
        """§6.3-2: 운영진이 수동으로 닫은 것을 스케줄러가 되돌리지 않는다.

        같은 일차의 재실행은 그 일차 행을 다시 켜므로, 이 보장은 '각 일차를
        정확히 한 번만 실행한다'(DateTrigger + 실행 기록)로 이뤄진다.
        여기서는 '다른 일차 개방'이 무관한 행을 건드리지 않음을 확인한다.
        """
        run_open(self.fake, '1주-4일차')
        # GM이 수위계를 수동으로 닫음
        self.fake.batch_update_cells('중앙 광장', [(3, 1, UNAVAILABLE_VALUE)])
        # 다른 일차 개방이 일어나도 수위계는 닫힌 채 유지
        run_open(self.fake, '2주-8일차')
        self.assertEqual(self.fake.column('중앙 광장', '현재 조사 가능'), [UNAVAILABLE_VALUE])

    def test_missing_manager_is_safe(self):
        result = run_open(None, '1주-4일차')
        self.assertEqual(result['opened'], 0)

    def test_entry_sheet_failure_aborts(self):
        self.fake.fail_sheets.add('진입')
        result = run_open(self.fake, '1주-4일차')
        self.assertEqual(result['opened'], 0)
        self.assertTrue(any('진입 시트' in n for n in self.notices))

    def test_entry_sheet_failure_does_not_mark_executed(self):
        self.fake.fail_sheets.add('진입')
        run_open(self.fake, '1주-4일차')
        self.assertFalse(is_executed('1주-4일차'))

    def test_location_sheet_failure_skips_that_sheet(self):
        self.fake.fail_sheets.add('연구소')
        result = run_open(self.fake, '1주-4일차')
        # 중앙 광장 포인트 1 + 진입 2 = 3
        self.assertEqual(result['opened'], 3)
        self.assertTrue(any('연구소' in n for n in self.notices))

    def test_partial_failure_does_not_mark_executed(self):
        """일부 실패인데 '실행 완료'로 찍으면 handle_missed_opens가 영영 못 잡는다.

        개방은 멱등하므로 재실행이 안전하다 → 기록을 남기지 않는 편이 옳다.
        """
        self.fake.fail_sheets.add('연구소')
        result = run_open(self.fake, '1주-4일차')
        self.assertTrue(result['failures'])
        self.assertFalse(is_executed('1주-4일차'))
        self.assertTrue(any('부분 실패' in n for n in self.notices))

    def test_write_failure_does_not_mark_executed(self):
        self.fake.write_ok = False
        run_open(self.fake, '1주-4일차')
        self.assertFalse(is_executed('1주-4일차'))

    def test_full_success_marks_executed(self):
        result = run_open(self.fake, '1주-4일차')
        self.assertEqual(result['failures'], [])
        self.assertTrue(is_executed('1주-4일차'))

    def test_unavailable_value_is_configurable(self):
        """GM 드롭다운 선택지에 맞출 수 있어야 한다."""
        from utils.investigation_open import unavailable_value
        orig = config.INVESTIGATION_UNAVAILABLE_VALUE
        try:
            config.INVESTIGATION_UNAVAILABLE_VALUE = '닫힘'
            self.assertEqual(unavailable_value(), '닫힘')
        finally:
            config.INVESTIGATION_UNAVAILABLE_VALUE = orig


class PendingOpenTest(_StateFileMixin, unittest.TestCase):
    def test_past_unexecuted_is_pending(self):
        future = datetime.now(KST) + timedelta(days=3650)
        pending = pending_opens(now=future)
        self.assertEqual(len(pending), 8)

    def test_executed_not_pending(self):
        mark_executed('1주-4일차')
        future = datetime.now(KST) + timedelta(days=3650)
        labels = [label for label, _ in pending_opens(now=future)]
        self.assertNotIn('1주-4일차', labels)

    def test_future_not_pending(self):
        past = KST.localize(datetime(2026, 1, 1))
        self.assertEqual(pending_opens(now=past), [])

    def test_state_roundtrip(self):
        mark_executed('2주-8일차')
        self.assertIn('2주-8일차', load_state())


if __name__ == '__main__':
    unittest.main()

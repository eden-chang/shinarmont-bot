"""[조사/포인트명] 명령어 테스트 (가이드 §3.3, §10-6 ~ §10-11)."""

import os
import random
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from commands.base_command import CommandContext
from commands.investigate.investigate_command import InvestigateCommand
from config.settings import config
from tests.investigation_fixtures import (
    entry_row, exception_row, investigation_sheets, log_row, main_sheets,
    mgmt_row, point_row,
)
from utils.investigation_log import ENTRY_RESULT, KST
from utils.investigation_state import InvestigationStateManager
from utils.store_helpers import parse_inventory_string
import utils.investigation_state as state_module
import utils.investigation_notify as notify_module

TODAY = datetime.now(KST)


def stamp(dt, hour=12):
    return dt.strftime('%m.%d ') + f'{hour:02d}:00'


def _context(point, user_id='alice'):
    return CommandContext(user_id=user_id, user_name='한참', keywords=['조사', point])


class _Base(unittest.TestCase):
    def setUp(self):
        state_module._state_manager = InvestigationStateManager()
        state_module.get_investigation_state().enter('alice', '연구소')
        notify_module.reset_once_cache()
        self.notices = []
        self._orig_notify = notify_module.notify_admin
        # prefix 인자를 받는다(출석 등 다른 기능이 말머리를 바꿔 재사용)
        notify_module.notify_admin = (
            lambda m, api=None, prefix=None: self.notices.append(m) or True)

    def tearDown(self):
        notify_module.notify_admin = self._orig_notify

    def build(self, points=None, mgmt=None, exceptions=None, logs=None, shop=None):
        self.inv = investigation_sheets(
            entry=[entry_row('연구소', available=True), entry_row('광산', available=True)],
            locations={'연구소': points if points is not None else
                       [point_row('전압계', roles='연구자', available=True,
                                  text='바늘이 떨린다.')]},
            exceptions=exceptions or [],
            logs=logs or [],
        )
        self.main = main_sheets([mgmt or mgmt_row()], shop=shop)
        return InvestigateCommand(
            sheets_manager=self.main, api=None,
            investigation_sheets_manager=self.inv,
        )


class SuccessTest(_Base):
    def test_basic_investigation(self):
        response = self.build().execute(_context('전압계'))
        self.assertTrue(response.is_successful())
        # 제목엔 대괄호가 없다 — 이 게임에서 [ ]는 '입력할 수 있는 명령어'를 뜻한다.
        self.assertTrue(response.message.startswith('전압계\n\n'))
        self.assertNotIn('[전압계]', response.message)
        self.assertIn('바늘이 떨린다.', response.message)

    def test_no_changes_has_no_arrow_lines(self):
        response = self.build().execute(_context('전압계'))
        self.assertNotIn('➭', response.message)

    def test_logs_investigation(self):
        self.build().execute(_context('전압계'))
        sheet, values = self.inv.appended[0]
        self.assertEqual(sheet, '로그')
        self.assertEqual(values[1:], ['한참', '연구소', '전압계', '조사'])

    def test_name_matching_ignores_spaces(self):
        points = [point_row('야금 실험대', roles='연구자', available=True)]
        response = self.build(points=points).execute(_context('야금실험대'))
        self.assertTrue(response.is_successful())
        # 저장·표기는 시트의 정규 표기(공백 포함)를 따른다
        self.assertTrue(response.message.startswith('야금 실험대\n\n'))


class RewardTest(_Base):
    def test_item_and_stat(self):
        points = [point_row('부검실', roles='연구자', available=True,
                            item='메스', count='2', stat='이성', value='-5')]
        response = self.build(points=points).execute(_context('부검실'))
        self.assertIn("➭ '메스' 2개 획득", response.message)
        self.assertIn('➭ 이성 -5', response.message)
        self.assertEqual(self.main.value('관리', 3, '이성'), 95)
        # 직렬화 형식에 의존하지 않도록 파싱해서 확인한다
        self.assertEqual(
            parse_inventory_string(self.main.value('관리', 3, '소지품')), {'메스': 2})

    def test_positive_stat_shows_sign(self):
        points = [point_row('침상', roles='연구자', available=True, stat='건강', value='5')]
        mgmt = mgmt_row(health=50)
        response = self.build(points=points, mgmt=mgmt).execute(_context('침상'))
        self.assertIn('➭ 건강 +5', response.message)
        self.assertEqual(self.main.value('관리', 3, '건강'), 55)

    def test_blank_count_defaults_to_one(self):
        """§2.2: 획득 아이템이 있는데 개수가 빈칸이면 1."""
        points = [point_row('선반', roles='연구자', available=True, item='담배', count='')]
        response = self.build(points=points).execute(_context('선반'))
        self.assertIn("➭ '담배' 1개 획득", response.message)

    def test_money_gain_and_loss(self):
        points = [point_row('바 카운터', roles='연구자', available=True, money='-5')]
        response = self.build(points=points, mgmt=mgmt_row(money=10)).execute(
            _context('바 카운터'))
        self.assertIn('지불', response.message)
        self.assertEqual(self.main.value('관리', 3, '소지금'), 5)

    def test_stat_clamped_to_max(self):
        """§9-4 결정: 0~100 클램프."""
        points = [point_row('침상', roles='연구자', available=True, stat='건강', value='30')]
        self.build(points=points, mgmt=mgmt_row(health=90)).execute(_context('침상'))
        self.assertEqual(self.main.value('관리', 3, '건강'), 100)

    def test_stat_clamped_to_min(self):
        points = [point_row('방사선 측정기', roles='연구자', available=True,
                            stat='건강', value='-30')]
        self.build(points=points, mgmt=mgmt_row(health=10)).execute(_context('방사선 측정기'))
        self.assertEqual(self.main.value('관리', 3, '건강'), 0)

    def test_clamped_response_shows_actual_change(self):
        """§3.3 "실제 발생한 변동만 출력": 굴림 +30이어도 실제 +5면 +5로 보여야 한다."""
        points = [point_row('침상', roles='연구자', available=True, stat='건강', value='30')]
        response = self.build(points=points, mgmt=mgmt_row(health=95)).execute(
            _context('침상'))
        self.assertIn('➭ 건강 +5', response.message)
        self.assertNotIn('+30', response.message)

    def test_clamped_log_records_actual_change(self):
        """로그는 GM 정산 기록이다. 굴림값이 아니라 실제 반영값을 남겨야 한다."""
        points = [point_row('침상', roles='연구자', available=True, stat='건강', value='30')]
        self.build(points=points, mgmt=mgmt_row(health=95)).execute(_context('침상'))
        self.assertEqual(self.inv.appended[0][1][4], '건강 +5')

    def test_no_line_when_already_at_cap(self):
        """이미 상한이면 실제 변동이 0 → ➭ 줄도 없고 시트도 안 건드린다."""
        points = [point_row('침상', roles='연구자', available=True, stat='건강', value='30')]
        response = self.build(points=points, mgmt=mgmt_row(health=100)).execute(
            _context('침상'))
        self.assertTrue(response.is_successful())
        self.assertNotIn('➭ 건강', response.message)
        self.assertEqual(self.main.value('관리', 3, '건강'), 100)
        self.assertEqual(self.inv.appended[0][1][4], '조사')

    def test_dice_value_is_resolved_not_formula(self):
        """§3.3: 출력하는 수치는 굴린 확정값이다."""
        points = [point_row('방사선 측정기', roles='연구자', available=True,
                            stat='건강', value='-(1d2)')]
        random.seed(1)
        response = self.build(points=points).execute(_context('방사선 측정기'))
        self.assertNotIn('1d2', response.message)
        self.assertRegex(response.message, r'➭ 건강 -[12]')

    def test_log_summary_records_changes(self):
        points = [point_row('부검실', roles='연구자', available=True,
                            item='메스', count='1', stat='이성', value='-2')]
        self.build(points=points).execute(_context('부검실'))
        _, values = self.inv.appended[0]
        # 순서: 스탯 → 아이템 → 재화 (2026-07-16 확정). ➭ 표시와 로그 요약이 같은 순서다.
        self.assertEqual(values[4], '이성 -2, 메스 1 획득')

    def test_negative_item_count_skipped_with_warning(self):
        """§2.2: 개수 음수는 지원하지 않는다 → 지급 건너뛰고 경고."""
        points = [point_row('선반', roles='연구자', available=True, item='담배', count='-2')]
        response = self.build(points=points).execute(_context('선반'))
        self.assertTrue(response.is_successful())
        self.assertNotIn('담배', response.message)
        self.assertTrue(any('음수' in n for n in self.notices))

    def test_unparseable_value_skips_only_that_reward(self):
        """§4: 파싱 불가는 해당 보상만 건너뛰고 조사는 진행."""
        points = [point_row('선반', roles='연구자', available=True,
                            item='담배', count='1', stat='이성', value='아무거나')]
        response = self.build(points=points).execute(_context('선반'))
        self.assertTrue(response.is_successful())
        self.assertIn("➭ '담배' 1개 획득", response.message)
        self.assertNotIn('➭ 이성', response.message)
        self.assertTrue(any('파싱 실패' in n for n in self.notices))

    def test_item_not_in_shop_is_granted_with_warning(self):
        points = [point_row('선반', roles='연구자', available=True, item='수상한물건', count='1')]
        response = self.build(points=points, shop=[{'아이템': '담배'}]).execute(_context('선반'))
        self.assertIn("➭ '수상한물건' 1개 획득", response.message)
        self.assertTrue(any('상점' in n for n in self.notices))


class BalanceTest(_Base):
    def test_insufficient_balance_fails(self):
        """§10-8: 재화 3달러인 캐릭터 → 잔액 부족 실패."""
        points = [point_row('바 카운터', roles='연구자', available=True,
                            money='-5', stat='이성', value='2')]
        response = self.build(points=points, mgmt=mgmt_row(money=3)).execute(
            _context('바 카운터'))
        self.assertEqual(response.message, config.investigation_message('NO_MONEY'))

    def test_insufficient_balance_applies_nothing(self):
        """이성과 재화 모두 변동 없음."""
        points = [point_row('바 카운터', roles='연구자', available=True,
                            money='-5', stat='이성', value='2')]
        self.build(points=points, mgmt=mgmt_row(money=3, sanity=50)).execute(
            _context('바 카운터'))
        self.assertEqual(self.main.value('관리', 3, '소지금'), 3)
        self.assertEqual(self.main.value('관리', 3, '이성'), 50)
        self.assertEqual(self.inv.appended, [])

    def test_exact_balance_allowed(self):
        points = [point_row('바 카운터', roles='연구자', available=True, money='-5')]
        response = self.build(points=points, mgmt=mgmt_row(money=5)).execute(
            _context('바 카운터'))
        self.assertTrue(response.is_successful())
        self.assertEqual(self.main.value('관리', 3, '소지금'), 0)


class DailyLimitTest(_Base):
    def _logs(self, count):
        return [log_row(stamp(TODAY), '한참', '연구소', f'포인트{i}', '조사')
                for i in range(count)]

    def test_under_limit_succeeds(self):
        response = self.build(logs=self._logs(1)).execute(_context('전압계'))
        self.assertTrue(response.is_successful())
        self.assertEqual(response.data['used'], 2)

    def test_at_limit_fails(self):
        """§10-9: 한도 도달 후 추가 조사는 실패. 기본 한도 2회."""
        response = self.build(logs=self._logs(2)).execute(_context('전압계'))
        self.assertEqual(response.message, config.investigation_message('DAILY_LIMIT'))

    def test_limit_failure_applies_nothing(self):
        points = [point_row('전압계', roles='연구자', available=True, stat='이성', value='-5')]
        self.build(points=points, logs=self._logs(2), mgmt=mgmt_row(sanity=50)).execute(
            _context('전압계'))
        self.assertEqual(self.main.value('관리', 3, '이성'), 50)
        self.assertEqual(self.inv.appended, [])

    def test_yesterday_does_not_count(self):
        logs = [log_row(stamp(TODAY - timedelta(days=1)), '한참', '연구소', 'p', '조사')
                for _ in range(3)]
        response = self.build(logs=logs).execute(_context('전압계'))
        self.assertTrue(response.is_successful())

    def test_entries_do_not_count(self):
        logs = [log_row(stamp(TODAY), '한참', '연구소', '', ENTRY_RESULT) for _ in range(5)]
        response = self.build(logs=logs).execute(_context('전압계'))
        self.assertTrue(response.is_successful())

    def test_mirrors_counter_to_management_sheet(self):
        self.build(logs=self._logs(1)).execute(_context('전압계'))
        self.assertEqual(self.main.value('관리', 3, '조사'), 2)

    def test_mirror_self_heals_from_log(self):
        """카운터가 어긋나 있어도 로그에서 센 값으로 교정된다."""
        self.build(logs=self._logs(1), mgmt=mgmt_row(today_count=99)).execute(
            _context('전압계'))
        self.assertEqual(self.main.value('관리', 3, '조사'), 2)

    def test_same_point_can_be_reinvestigated(self):
        """§5.2: 같은 포인트 재조사 제한은 없다."""
        logs = [log_row(stamp(TODAY), '한참', '연구소', '전압계', '조사')]
        response = self.build(logs=logs).execute(_context('전압계'))
        self.assertTrue(response.is_successful())


class OpenDayAdjustTest(_Base):
    """§8 오픈일 조사 횟수 보정 — 개방 시각이 지난 개방일에만 적용.

    '개방 시각이 지났는가'는 실제 시계에 의존하므로 open_time_passed_today를 갈아끼워
    시간에 독립적으로 검증한다. 명령어가 이 함수를 지연 임포트하므로 모듈 속성
    패치가 그대로 반영된다.
    """

    def setUp(self):
        super().setUp()
        self._orig_mode = config.INVESTIGATION_OPEN_ADJUST
        import utils.investigation_open as open_module
        self._open_module = open_module
        self._orig_passed = open_module.open_time_passed_today

    def tearDown(self):
        type(config).INVESTIGATION_OPEN_ADJUST = self._orig_mode
        self._open_module.open_time_passed_today = self._orig_passed
        super().tearDown()

    def _set(self, mode, opened_at_hour=None):
        """mode를 설정하고, opened_at_hour가 주어지면 오늘 그 시각에 개방된 것으로 본다."""
        type(config).INVESTIGATION_OPEN_ADJUST = mode
        if opened_at_hour is None:
            self._open_module.open_time_passed_today = lambda now=None: None
        else:
            opened = KST.localize(datetime(
                TODAY.year, TODAY.month, TODAY.day, opened_at_hour, 0))
            self._open_module.open_time_passed_today = lambda now=None: opened

    def _logs(self, count, hour=12):
        return [log_row(stamp(TODAY, hour), '한참', '연구소', f'p{i}', '조사')
                for i in range(count)]

    def test_off_is_default_behaviour(self):
        self._set('off', opened_at_hour=20)
        response = self.build(logs=self._logs(2)).execute(_context('전압계'))
        self.assertEqual(response.message, config.investigation_message('DAILY_LIMIT'))

    def test_plus_one_raises_limit(self):
        self._set('plus_one', opened_at_hour=20)
        response = self.build(logs=self._logs(2)).execute(_context('전압계'))
        self.assertNotEqual(response.message, config.investigation_message('DAILY_LIMIT'))
        self.assertEqual(response.data['limit'], 3)

    def test_plus_one_still_bounded(self):
        self._set('plus_one', opened_at_hour=20)
        response = self.build(logs=self._logs(3)).execute(_context('전압계'))
        self.assertEqual(response.message, config.investigation_message('DAILY_LIMIT'))

    def test_reset_ignores_pre_open_investigations(self):
        """개방(20시) 이전의 12시 조사 2건은 세지 않는다."""
        self._set('reset', opened_at_hour=20)
        response = self.build(logs=self._logs(2, hour=12)).execute(_context('전압계'))
        self.assertNotEqual(response.message, config.investigation_message('DAILY_LIMIT'))
        self.assertEqual(response.data['used'], 1)

    def test_reset_counts_post_open_investigations(self):
        """개방(20시) 이후의 21시 조사는 그대로 센다."""
        self._set('reset', opened_at_hour=20)
        response = self.build(logs=self._logs(2, hour=21)).execute(_context('전압계'))
        self.assertEqual(response.message, config.investigation_message('DAILY_LIMIT'))

    def test_no_adjust_before_open_time(self):
        """개방 시각 전이거나 개방일이 아니면 보정하지 않는다."""
        self._set('plus_one', opened_at_hour=None)
        response = self.build(logs=self._logs(2)).execute(_context('전압계'))
        self.assertEqual(response.message, config.investigation_message('DAILY_LIMIT'))


class AccessTest(_Base):
    def test_wrong_role_refused_as_if_absent(self):
        """§3.3-3: 권한 없음을 티 내지 않는다."""
        points = [point_row('전압계', roles='연구자', available=True)]
        response = self.build(points=points, mgmt=mgmt_row(role='가족')).execute(
            _context('전압계'))
        self.assertEqual(response.message, config.investigation_message('NO_SUCH_POINT'))

    def test_missing_point_same_message(self):
        response = self.build().execute(_context('없는포인트'))
        self.assertEqual(response.message, config.investigation_message('NO_SUCH_POINT'))

    def test_closed_point_same_message(self):
        points = [point_row('전압계', roles='연구자', available=False)]
        response = self.build(points=points).execute(_context('전압계'))
        self.assertEqual(response.message, config.investigation_message('NO_SUCH_POINT'))

    def test_not_entered(self):
        state_module.get_investigation_state().clear('alice')
        response = self.build().execute(_context('전압계'))
        self.assertEqual(response.message, config.investigation_message('NOT_ENTERED'))

    def test_closed_location_blocks_investigation(self):
        """운영진이 진입 행을 닫으면 이미 안에 있던 러너도 조사할 수 없다."""
        cmd = self.build()
        # GM이 연구소 진입 행을 수동으로 닫음 (포인트는 열린 채)
        self.inv.set('진입', [entry_row('연구소', available=False),
                             entry_row('광산', available=True)],
                     header=list(entry_row('x').keys()))
        response = cmd.execute(_context('전압계'))
        self.assertEqual(response.message, config.investigation_message('CANNOT_ENTER'))

    def test_closed_location_applies_nothing(self):
        points = [point_row('전압계', roles='연구자', available=True, money='100')]
        cmd = self.build(points=points, mgmt=mgmt_row(money=0))
        self.inv.set('진입', [entry_row('연구소', available=False)],
                     header=list(entry_row('x').keys()))
        cmd.execute(_context('전압계'))
        self.assertEqual(self.main.value('관리', 3, '소지금'), 0)
        self.assertEqual(self.inv.appended, [])

    def test_missing_argument(self):
        cmd = self.build()
        response = cmd.execute(CommandContext(user_id='alice', keywords=['조사']))
        self.assertEqual(response.message, config.investigation_message('NO_SUCH_POINT'))


class ExceptionTest(_Base):
    def test_exception_text_replaces_default(self):
        """§10-6: 예외 문구 출력."""
        points = [point_row('진료 대장', roles='연구자', available=True, text='기본 문구')]
        exceptions = [exception_row('한참', '연구소', '진료 대장', '특수 문구다.')]
        response = self.build(points=points, exceptions=exceptions,
                              mgmt=mgmt_row(role='가족')).execute(_context('진료 대장'))
        self.assertTrue(response.is_successful())
        self.assertIn('특수 문구다.', response.message)
        self.assertNotIn('기본 문구', response.message)

    def test_exception_keeps_base_rewards(self):
        """§2.3-3: 예외는 문구와 접근만 바꾼다. 보상은 기본 행 값."""
        points = [point_row('진료 대장', roles='연구자', available=True,
                            item='기록부', count='1', stat='이성', value='-3')]
        exceptions = [exception_row('한참', '연구소', '진료 대장', '특수 문구다.')]
        response = self.build(points=points, exceptions=exceptions,
                              mgmt=mgmt_row(role='가족')).execute(_context('진료 대장'))
        self.assertIn("➭ '기록부' 1개 획득", response.message)
        self.assertIn('➭ 이성 -3', response.message)

    def test_exception_cannot_open_closed_point(self):
        """§2.3-4: 개방 여부가 항상 우선."""
        points = [point_row('진료 대장', roles='연구자', available=False)]
        exceptions = [exception_row('한참', '연구소', '진료 대장', '특수')]
        response = self.build(points=points, exceptions=exceptions,
                              mgmt=mgmt_row(role='가족')).execute(_context('진료 대장'))
        self.assertEqual(response.message, config.investigation_message('NO_SUCH_POINT'))


class VariantTest(_Base):
    def test_random_variant_text(self):
        """§10-2-2: 같은 포인트의 변주 행이 무작위로 섞여 나온다."""
        points = [
            point_row('급수대 줄', roles='연구자', available=True, text='문구A'),
            point_row('급수대 줄', roles='연구자', available=True, text='문구B'),
        ]
        random.seed(7)
        seen = set()
        for _ in range(60):
            response = self.build(points=points).execute(_context('급수대 줄'))
            for candidate in ('문구A', '문구B'):
                if candidate in response.message:
                    seen.add(candidate)
        self.assertEqual(seen, {'문구A', '문구B'})


class RestoreTest(_Base):
    def test_restores_location_from_log(self):
        """§10-11: 봇 재시작 후에도 로그에서 위치가 복원된다."""
        state_module._state_manager = InvestigationStateManager()  # 재시작 시뮬레이션
        logs = [log_row(stamp(TODAY), '한참', '연구소', '', ENTRY_RESULT)]
        response = self.build(logs=logs).execute(_context('전압계'))
        self.assertTrue(response.is_successful())
        self.assertEqual(
            state_module.get_investigation_state().get_location('alice'), '연구소')

    def test_no_log_means_not_entered(self):
        state_module._state_manager = InvestigationStateManager()
        response = self.build(logs=[]).execute(_context('전압계'))
        self.assertEqual(response.message, config.investigation_message('NOT_ENTERED'))


class DistortionTest(_Base):
    def test_high_sanity_text_unchanged(self):
        response = self.build(mgmt=mgmt_row(sanity=100)).execute(_context('전압계'))
        self.assertIn('바늘이 떨린다.', response.message)

    def test_low_sanity_distorts_text(self):
        """이성이 낮으면 조사 문구가 왜곡된다 (기존 기획 유지)."""
        response = self.build(mgmt=mgmt_row(sanity=25)).execute(_context('전압계'))
        self.assertTrue(response.is_successful())
        self.assertNotIn('바늘이 떨린다.', response.message)

    def test_distortion_does_not_touch_reward_lines(self):
        """➭ 줄은 시트에 실제 반영된 값이므로 왜곡하지 않는다."""
        points = [point_row('전압계', roles='연구자', available=True,
                            text='바늘이 떨린다.', stat='이성', value='-2')]
        response = self.build(points=points, mgmt=mgmt_row(sanity=25)).execute(
            _context('전압계'))
        self.assertIn('➭ 이성 -2', response.message)

    def test_disabled_by_config(self):
        orig = config.INVESTIGATION_DISTORTION_ENABLED
        type(config).INVESTIGATION_DISTORTION_ENABLED = False
        try:
            response = self.build(mgmt=mgmt_row(sanity=5)).execute(_context('전압계'))
            self.assertIn('바늘이 떨린다.', response.message)
        finally:
            type(config).INVESTIGATION_DISTORTION_ENABLED = orig


class FailureTest(_Base):
    def test_reward_write_failure_alerts_admin(self):
        """§7: 일부 적용 후 실패는 관리자 알림 필수."""
        points = [point_row('전압계', roles='연구자', available=True, stat='이성', value='-2')]
        cmd = self.build(points=points)
        self.main.write_ok = False
        response = cmd.execute(_context('전압계'))
        self.assertFalse(response.is_successful())
        self.assertTrue(any('수동 정산' in n for n in self.notices))

    def test_duplicate_character_name_warns_admin(self):
        """로그·예외가 캐릭터명 키라서 동명이인이면 상태가 섞인다 → 경고."""
        rows = [mgmt_row(name='한참', user_id='alice'),
                mgmt_row(name='한참', user_id='bob')]
        self.build(mgmt=rows[0])
        self.main.set('관리', rows)
        cmd = InvestigateCommand(sheets_manager=self.main, api=None,
                                 investigation_sheets_manager=self.inv)
        cmd.execute(_context('전압계'))
        self.assertTrue(any('동명이인' in n or '캐릭터명' in n for n in self.notices))

    def test_log_write_failure_aborts_without_reward(self):
        """로그가 일일 제한의 기준이므로, 기록 실패 시 보상을 주면 안 된다."""
        points = [point_row('금고', roles='연구자', available=True, money='100',
                            stat='이성', value='5')]
        cmd = self.build(points=points, mgmt=mgmt_row(money=0, sanity=50))
        self.inv.write_ok = False  # 로그 시트만 쓰기 실패

        response = cmd.execute(_context('금고'))

        self.assertFalse(response.is_successful())
        self.assertEqual(response.message, config.investigation_message('TEMPORARY'))
        # 아무것도 반영되지 않아야 한다
        self.assertEqual(self.main.value('관리', 3, '소지금'), 0)
        self.assertEqual(self.main.value('관리', 3, '이성'), 50)
        self.assertEqual(self.main.value('관리', 3, '조사'), 0)
        self.assertTrue(any('로그 기록 실패' in n for n in self.notices))

    def test_log_write_failure_cannot_be_farmed(self):
        """회귀 방지: 로그 실패 상태에서 반복 호출해도 보상이 누적되면 안 된다.

        (이전 구현은 보상 → 로그 순서라, 로그가 실패하면 횟수가 소모되지 않아
         한도 2회를 무시하고 무한히 재화를 얻을 수 있었다.)
        """
        points = [point_row('금고', roles='연구자', available=True, money='100')]
        cmd = self.build(points=points, mgmt=mgmt_row(money=0))
        self.inv.write_ok = False

        for _ in range(6):
            cmd.execute(_context('금고'))

        self.assertEqual(self.main.value('관리', 3, '소지금'), 0,
                         "로그 기록이 실패하면 재화가 단 1원도 나가면 안 된다.")

    def test_reward_write_failure_after_log_alerts_for_settlement(self):
        """로그는 남았는데 보상이 실패하면 횟수만 소모 → 수동 정산 알림."""
        points = [point_row('금고', roles='연구자', available=True, money='100')]
        cmd = self.build(points=points, mgmt=mgmt_row(money=0))
        self.main.write_ok = False  # 관리 시트만 쓰기 실패

        response = cmd.execute(_context('금고'))

        self.assertFalse(response.is_successful())
        self.assertEqual(len(self.inv.appended), 1, "로그는 기록돼 있어야 한다.")
        self.assertTrue(any('수동 정산' in n for n in self.notices))

    def test_location_sheet_failure_is_temporary_error(self):
        cmd = self.build()
        self.inv.fail_sheets.add('연구소')
        response = cmd.execute(_context('전압계'))
        self.assertFalse(response.is_successful())
        self.assertEqual(response.message, config.investigation_message('TEMPORARY'))


if __name__ == '__main__':
    unittest.main()

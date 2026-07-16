"""
utils/shinarmont_scheduler 단위 테스트.

- owner=False no-op
- 잡 함수 수동 호출 시 의존 유틸을 순서대로 호출 (모듈 함수 분리 계약)
- 한 단계 실패가 다른 단계를 막지 않음 (격리)
- 요일 정규화(_normalize_weekday) / 설정 시트 우선 해석
"""

import os
import sys
import types
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from utils import shinarmont_scheduler as sched


def _install_fake(name, module):
    """utils.<name> 자리에 페이크 모듈을 주입하고 원복 함수를 반환.

    `from utils import <name>` 는 sys.modules 뿐 아니라 이미 로드된 `utils` 패키지의
    속성을 우선 참조하므로, 다른 테스트가 실물 모듈을 먼저 임포트한 뒤에는 sys.modules
    교체만으로는 페이크가 적용되지 않는다(테스트 실행 순서 의존). 따라서 패키지 속성도
    함께 교체하여 실행 순서와 무관하게 페이크가 적용되도록 한다.
    """
    import utils as _utils_pkg
    full = f'utils.{name}'
    old_mod = sys.modules.get(full)
    had_attr = hasattr(_utils_pkg, name)
    old_attr = getattr(_utils_pkg, name, None)

    def restore():
        if old_mod is None:
            sys.modules.pop(full, None)
        else:
            sys.modules[full] = old_mod
        if had_attr:
            setattr(_utils_pkg, name, old_attr)
        elif hasattr(_utils_pkg, name):
            delattr(_utils_pkg, name)

    sys.modules[full] = module
    setattr(_utils_pkg, name, module)
    return restore


class TestStartOwnerGate(unittest.TestCase):
    def test_owner_false_is_noop(self):
        self.assertIsNone(sched.start(owner=False))

    def test_owner_false_default(self):
        self.assertIsNone(sched.start())


class TestWeekdayNormalize(unittest.TestCase):
    def test_korean(self):
        self.assertEqual(sched._normalize_weekday('토'), 'sat')
        self.assertEqual(sched._normalize_weekday('월요일'), 'mon')

    def test_english_and_numeric(self):
        self.assertEqual(sched._normalize_weekday('SUN'), 'sun')
        self.assertEqual(sched._normalize_weekday('friday'), 'fri')
        self.assertEqual(sched._normalize_weekday('5'), 'sat')  # 0=mon..6=sun

    def test_invalid(self):
        self.assertIsNone(sched._normalize_weekday(''))
        self.assertIsNone(sched._normalize_weekday('xyz'))
        self.assertIsNone(sched._normalize_weekday(None))


class TestResolveWeekday(unittest.TestCase):
    def test_setting_sheet_priority(self):
        class FakeSys:
            def get_worksheet_data(self, name, use_cache=False):
                return [{'항목': '소문 요일', '값': '수'}, {'소문 요일': '수'}]
        self.assertEqual(sched._resolve_rumor_weekday(FakeSys()), 'wed')

    def test_config_fallback(self):
        class FakeSys:
            def get_worksheet_data(self, name, use_cache=False):
                return []  # 설정에 없음 → config 폴백
        wd = sched._resolve_rumor_weekday(FakeSys())
        self.assertIn(wd, {'mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'})

    def test_none_manager(self):
        wd = sched._resolve_rumor_weekday(None)
        self.assertIn(wd, {'mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'})


class TestDailyResetJob(unittest.TestCase):
    """00:00 잡.

    **봇 JSON은 더 이상 리셋하지 않는다** — `오늘*` 키는 날짜 스탬프로 자동 만료되고
    '어제대화상대'는 파생된다(utils/game_state.py). 예전 carry/reset 잡은
    BOT1 프로세스의 싱글톤만 건드려 @STORY·@DOCTOR에는 닿지도 않았다.
    지금 남은 일은 지난 기록 정리(purge_stale)와 관리시트 카운터 리셋뿐이다.
    """

    def _fakes(self, calls, decay=None):
        stat_gate = types.ModuleType('utils.stat_gate')
        stat_gate.daily_decay_all = decay or (lambda sm, ssm, api: calls.append('decay'))

        class FakeGS:
            def purge_stale(self):
                calls.append('purge')
                return 0

        game_state = types.ModuleType('utils.game_state')
        game_state.get_game_state = lambda: FakeGS()

        return [
            _install_fake('stat_gate', stat_gate),
            _install_fake('game_state', game_state),
        ]

    def test_calls_all_steps(self):
        calls = []
        restores = self._fakes(calls)
        try:
            sched.run_daily_reset('SM', 'SSM', 'API')
        finally:
            for r in restores:
                r()
        self.assertEqual(set(calls), {'decay', 'purge'})

    def test_step_failure_isolated(self):
        calls = []

        def boom(sm, ssm, api):
            raise RuntimeError('decay failed')

        restores = self._fakes(calls, decay=boom)
        try:
            # 예외가 밖으로 전파되지 않아야 하고, 나머지 단계는 실행돼야 한다
            sched.run_daily_reset('SM', 'SSM', 'API')
        finally:
            for r in restores:
                r()

        self.assertEqual(set(calls), {'purge'})

    def test_does_not_touch_management_sheet(self):
        """0시 리셋은 구글 시트를 건드리지 않는다 (2026-07-16 운영 결정).

        `관리` 시트의 추적/조사/출석 리셋은 별도 스케줄러 봇의 몫이다.
        양쪽이 같은 셀을 리셋하면 서로의 결과를 덮어쓰고, 실패해도 누구 탓인지 모른다.
        여기서 시트 쓰기가 다시 생기면 이 테스트가 잡는다.
        """
        calls = []
        restores = self._fakes(calls)

        class Tripwire:
            def __getattr__(self, name):
                raise AssertionError(
                    f"0시 리셋이 시트를 건드렸다: sheets_manager.{name} — "
                    f"추적/조사/출석 리셋은 별도 봇의 책임이다"
                )

        try:
            sched.run_daily_reset(Tripwire(), Tripwire(), None)
        finally:
            for r in restores:
                r()

        # 시트를 안 건드리면서도 JSON 정리는 계속 돌아야 한다
        self.assertIn('purge', calls)


class TestWeeklyRumorJob(unittest.TestCase):
    def test_calls_rumor(self):
        calls = []
        rumor = types.ModuleType('utils.rumor')
        rumor.send_weekly_rumor = lambda sm, ssm, api: calls.append((sm, ssm, api))
        restore = _install_fake('rumor', rumor)
        try:
            sched.run_weekly_rumor('SM', 'SSM', 'API')
        finally:
            restore()
        self.assertEqual(calls, [('SM', 'SSM', 'API')])

    def test_calls_send_and_propose(self):
        # 주간 잡은 확정 소문 발송 + 다음 주 후보 적재를 모두 수행해야 한다.
        calls = []
        rumor = types.ModuleType('utils.rumor')
        rumor.send_weekly_rumor = lambda sm, ssm, api: calls.append('send')
        rumor.propose_candidates = lambda ssm: calls.append('propose')
        restore = _install_fake('rumor', rumor)
        try:
            sched.run_weekly_rumor('SM', 'SSM', 'API')
        finally:
            restore()
        self.assertEqual(calls, ['send', 'propose'])

    def test_send_failure_does_not_block_propose(self):
        # 발송 단계 실패가 후보 적재를 막지 않아야 한다(단계 격리).
        calls = []

        def _boom(*a, **k):
            raise RuntimeError('send boom')

        rumor = types.ModuleType('utils.rumor')
        rumor.send_weekly_rumor = _boom
        rumor.propose_candidates = lambda ssm: calls.append('propose')
        restore = _install_fake('rumor', rumor)
        try:
            sched.run_weekly_rumor('SM', 'SSM', 'API')
        finally:
            restore()
        self.assertEqual(calls, ['propose'])

    def test_missing_module_is_isolated(self):
        # utils.rumor 부재 시에도 예외가 전파되지 않아야 함
        old = sys.modules.pop('utils.rumor', None)
        try:
            sched.run_weekly_rumor('SM', 'SSM', 'API')  # ImportError 삼켜야 함
        finally:
            if old is not None:
                sys.modules['utils.rumor'] = old


if __name__ == '__main__':
    unittest.main()

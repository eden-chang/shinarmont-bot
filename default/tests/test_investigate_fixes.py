"""조사 봇의 API 호출 절약·동시성·라우팅 회귀 테스트.

기능 규칙 자체는 test_investigate_command.py 등에서 다룬다.
여기서는 '어떻게' 동작하는지(호출 횟수, 락, 라우터 구조)를 고정한다.
"""

import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from commands.base_command import CommandContext
from commands.investigate.investigate_command import InvestigateCommand
from tests.investigation_fixtures import (
    entry_row, investigation_sheets, main_sheets, mgmt_row, point_row,
)
from utils.investigation_state import InvestigationStateManager
import utils.investigation_state as state_module


def _build(points=None, mgmt=None):
    inv = investigation_sheets(
        entry=[entry_row('연구소', available=True), entry_row('광산', available=True)],
        locations={
            '연구소': points or [point_row('전압계', roles='연구자', available=True,
                                        text='연구소 문구', item='메스', count='1',
                                        stat='이성', value='-5')],
            '광산': [point_row('전압계', roles='연구자', available=True, text='광산 문구')],
        },
    )
    main = main_sheets([mgmt or mgmt_row()])
    cmd = InvestigateCommand(sheets_manager=main, api=None,
                             investigation_sheets_manager=inv)
    return cmd, main, inv


class SheetCallBudgetTest(unittest.TestCase):
    """관리 시트 조회·쓰기 횟수를 최소로 유지한다."""

    def setUp(self):
        state_module._state_manager = InvestigationStateManager()
        state_module.get_investigation_state().enter('alice', '연구소')

    def test_management_sheet_read_once(self):
        cmd, main, _ = _build()
        cmd.execute(CommandContext(user_id='alice', user_name='한참',
                                   keywords=['조사', '전압계']))
        reads = [name for name, _ in main.reads if name == '관리']
        self.assertEqual(len(reads), 1, f"관리 시트는 1회만 조회되어야 함 (실제 {len(reads)}회)")

    def test_management_sheet_read_uncached(self):
        """스탯·재화는 정합성이 중요하므로 항상 최신을 읽는다."""
        cmd, main, _ = _build()
        cmd.execute(CommandContext(user_id='alice', user_name='한참',
                                   keywords=['조사', '전압계']))
        for name, use_cache in main.reads:
            if name == '관리':
                self.assertFalse(use_cache, "관리 시트는 캐시를 쓰면 안 됨")

    def test_header_derived_from_data_no_extra_worksheet_call(self):
        """헤더는 읽어 온 행의 키 순서에서 산출한다(별도 get_worksheet/row_values 없음)."""
        cmd, main, _ = _build()
        self.assertFalse(hasattr(main, 'get_worksheet_called'))
        cmd.execute(CommandContext(user_id='alice', user_name='한참',
                                   keywords=['조사', '전압계']))
        # FakeSheets에는 get_worksheet가 없다 — 호출했다면 AttributeError로 실패했을 것이다
        self.assertEqual(main.value('관리', 3, '이성'), 95)

    def test_rewards_written_in_single_batch(self):
        """아이템·스탯·카운터 미러를 한 번의 batch_update_cells로 쓴다."""
        cmd, main, _ = _build()
        calls = []
        original = main.batch_update_cells
        main.batch_update_cells = lambda name, updates: (
            calls.append((name, list(updates))) or original(name, updates))
        cmd.execute(CommandContext(user_id='alice', user_name='한참',
                                   keywords=['조사', '전압계']))
        mgmt_calls = [c for c in calls if c[0] == '관리']
        self.assertEqual(len(mgmt_calls), 1, "관리 시트 쓰기는 1회 배치여야 함")
        # 소지품 + 이성 + 오늘조사
        self.assertEqual(len(mgmt_calls[0][1]), 3)


class LocationLockTest(unittest.TestCase):
    """현재 위치는 락 안에서 확정된다."""

    def setUp(self):
        state_module._state_manager = InvestigationStateManager()

    def test_location_resolved_inside_lock(self):
        state = state_module.get_investigation_state()
        state.enter('alice', '연구소')
        cmd, _, _ = _build()
        ctx = CommandContext(user_id='alice', user_name='한참', keywords=['조사', '전압계'])

        response = cmd.execute(ctx)
        self.assertIn('연구소 문구', response.message)

        state.enter('alice', '광산')
        response = cmd.execute(ctx)
        self.assertIn('광산 문구', response.message)

    def test_concurrent_investigations_are_serialized(self):
        """캐릭터 단위 락으로 read-modify-write 경합을 막는다 (§7).

        동시에 2회 조사하면 이성 변동(-5)이 정확히 두 번 반영되어야 한다
        (락이 없으면 둘 다 100을 읽어 95로 덮어써 한 번만 반영된다).
        """
        state_module.get_investigation_state().enter('alice', '연구소')
        cmd, main, _ = _build(mgmt=mgmt_row(sanity=100))
        ctx = CommandContext(user_id='alice', user_name='한참', keywords=['조사', '전압계'])

        barrier = threading.Barrier(2)

        def run():
            barrier.wait()
            cmd.execute(ctx)

        threads = [threading.Thread(target=run) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(main.value('관리', 3, '이성'), 90)


class RegistryDiscoveryTest(unittest.TestCase):
    """레지스트리가 조사 명령어만 등록하고 베이스 클래스는 걸러내는지."""

    def _registry(self):
        from commands.registry import get_registry
        reg = get_registry()
        reg.discover_commands()
        return reg

    def test_base_class_is_not_registered(self):
        """베이스 클래스가 유령 명령어로 등록되면 안 된다.

        BaseCommand.execute는 @abstractmethod가 아니라서 상속만으로는 추상이 되지 않는다.
        베이스 클래스가 execute를 추상으로 선언해야 레지스트리가 걸러낸다.
        """
        names = {n.lower() for n in self._registry()._commands}
        self.assertNotIn('baseinvestigate', names)
        self.assertNotIn('basestore', names)

    def test_investigate_commands_registered(self):
        reg = self._registry()
        for name in ('진입', '조사', '장소 목록', '조사 개방'):
            self.assertIn(name, reg._commands, f"'{name}' 명령어가 등록되지 않음")

    def test_open_admin_is_admin_only(self):
        """[조사 개방]은 관리자 전용이어야 한다 (validate_context가 강제).

        권한 정보는 RegisteredCommand.metadata에 있다.
        """
        reg = self._registry()
        self.assertTrue(reg._commands['조사 개방'].metadata.admin_only)
        self.assertFalse(reg._commands['조사'].metadata.admin_only)


class InvestigateBotSilentIgnoreTest(unittest.TestCase):
    """BOT_TYPE=investigate에서 커스텀 명령어까지 silent ignore되는지 검증.

    command_router의 흐름을 모의하기 어려우므로 소스 코드 구조 자체를 확인.
    """

    def test_silent_ignore_block_precedes_custom_command_block(self):
        router_path = os.path.join(
            os.path.dirname(__file__), '..', 'handlers', 'command_router.py'
        )
        with open(router_path, 'r', encoding='utf-8') as f:
            src = f.read()

        silent_guard = "if IMPORTS_AVAILABLE and getattr(config, 'BOT_TYPE', '').lower() == 'investigate':"
        custom_guard = "if is_custom_command(first_keyword):"

        silent_pos = src.find(silent_guard)
        custom_pos = src.find(custom_guard)

        self.assertGreater(silent_pos, -1, "investigate silent-ignore 블록을 찾을 수 없음")
        self.assertGreater(custom_pos, -1, "custom command 블록을 찾을 수 없음")
        self.assertLess(
            silent_pos, custom_pos,
            "silent-ignore 가드는 custom command 검사보다 먼저 와야 함"
        )


if __name__ == '__main__':
    unittest.main()

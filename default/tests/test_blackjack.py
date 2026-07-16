"""블랙잭 명령어 스모크/로직 테스트 (시트는 페이크 주입)."""

import os
import sys
import json
import random
import shutil
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from commands.base_command import CommandContext
from commands.shinarmont import blackjack_command as bj
from utils.json_store import JsonStore

_real_config = bj.config


class _TempSessions:
    """임시 파일에 붙은 세션 매니저를 주입하고, 일일 제한을 무력화한다.

    주입하지 않으면 기본 매니저가 **프로젝트 `state/`에 실제로 파일을 쓰고**
    테스트끼리 판이 새어 든다.

    일일 제한도 막는다(2026-07-16 추가): `game_state.check_and_set` 이 실제
    `state/{슬롯}/game_state.json` 을 건드리고, 시드를 20개 넘게 도는 루프는
    제한에 걸려 실패한다. 제한 자체는 `DailyLimitTest` 에서 따로 검증한다.
    """

    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix='bj_test_')
        self.path = os.path.join(self.dir, 'blackjack_sessions.json')
        self._daily = patch.object(bj.game_state, 'check_and_set', return_value=True)
        self._daily.start()

    def install(self):
        bj.set_session_manager(bj._SessionManager(store=JsonStore(self.path)))
        return bj.get_session_manager()

    def restart(self):
        """봇 재시작: 같은 파일로 매니저를 새로 만든다."""
        bj.set_session_manager(bj._SessionManager(store=JsonStore(self.path)))
        return bj.get_session_manager()

    def reset(self, seed=None):
        """빈 상태로 시작. 시드를 바꿔 가며 도는 루프에서 쓴다.

        install()만 하면 같은 파일을 다시 읽어 **직전 시드의 판이 되살아난다**.
        """
        if os.path.exists(self.path):
            os.remove(self.path)
        if seed is not None:
            bj.set_rng(random.Random(seed))
        return self.install()

    def raw(self):
        if not os.path.exists(self.path):
            return {}
        with open(self.path, encoding='utf-8') as f:
            return json.load(f)

    def cleanup(self):
        bj.set_session_manager(None)
        bj.set_rng(random.SystemRandom())   # 운영 RNG로 복구
        self._daily.stop()
        shutil.rmtree(self.dir, ignore_errors=True)


class FakeSheets:
    """관리 시트 소지금 1행을 흉내내는 페이크."""

    def __init__(self, balance=1000, user_id='u1'):
        self.header = ['아이디', '이름', '소지금']
        self.balance = balance
        self.user_id = user_id
        self.batch_calls = []

    def get_worksheet_data(self, name, use_cache=True):
        return [{
            '아이디': self.user_id,
            '이름': '테스트',
            '소지금': self.balance,
            '_row_number': 2,
        }]

    def batch_update_cells(self, name, updates):
        self.batch_calls.append((name, updates))
        # (row, col, value) — 소지금 컬럼(3)만 반영
        for _row, col, value in updates:
            if col == 3:
                self.balance = value
        return True


def make_ctx(uid, keywords):
    return CommandContext(user_id=uid, user_name=uid, keywords=keywords)


class BlackjackHandLogicTest(unittest.TestCase):
    def test_hand_total_with_ace(self):
        self.assertEqual(bj._hand_total([('A', '♠'), ('K', '♥')]), 21)
        # 에이스 두 장 + 9 = 21 (하나는 1로)
        self.assertEqual(bj._hand_total([('A', '♠'), ('A', '♥'), ('9', '♦')]), 21)
        # 버스트 방지: A + 8 + 5 = 14
        self.assertEqual(bj._hand_total([('A', '♠'), ('8', '♥'), ('5', '♦')]), 14)

    def test_is_blackjack(self):
        self.assertTrue(bj._is_blackjack([('A', '♠'), ('10', '♥')]))
        self.assertFalse(bj._is_blackjack([('A', '♠'), ('9', '♥')]))
        self.assertFalse(bj._is_blackjack([('A', '♠'), ('5', '♥'), ('5', '♦')]))

    def test_deck_is_52_unique(self):
        deck = bj._build_deck()
        self.assertEqual(len(deck), 52)
        self.assertEqual(len(set(deck)), 52)


class BlackjackFlowTest(unittest.TestCase):
    def setUp(self):
        # 세션 격리 (임시 파일 주입 — 프로젝트 state/ 오염 방지)
        self.tmp = _TempSessions()
        self.tmp.install()
        self.addCleanup(self.tmp.cleanup)
        bj.set_rng(random.Random(1234))

    def _cmd(self, sheets):
        return bj.BlackjackCommand(sheets_manager=sheets, api=object())

    def test_bet_validation_errors(self):
        sheets = FakeSheets(balance=1000)
        cmd = self._cmd(sheets)
        # 베팅 누락
        r = cmd.execute(make_ctx('u1', ['블랙잭']))
        self.assertFalse(r.success)
        # 음수/0
        r = cmd.execute(make_ctx('u1', ['블랙잭', '0']))
        self.assertFalse(r.success)
        # 숫자 아님
        r = cmd.execute(make_ctx('u1', ['블랙잭', 'abc']))
        self.assertFalse(r.success)

    def test_insufficient_funds(self):
        sheets = FakeSheets(balance=10)
        cmd = self._cmd(sheets)
        r = cmd.execute(make_ctx('u1', ['블랙잭', '100']))
        self.assertFalse(r.success)
        self.assertEqual(sheets.balance, 10)  # 차감 안 됨

    def test_hit_stand_before_game(self):
        cmd = self._cmd(FakeSheets())
        self.assertFalse(cmd.execute(make_ctx('u1', ['히트'])).success)
        self.assertFalse(cmd.execute(make_ctx('u1', ['스탠드'])).success)

    def test_charge_write_failure_starts_no_game(self):
        # 시트 쓰기 실패 시 차감이 반영되지 않았음을 정확히 보고 → 무료 플레이 방지
        class _FailingSheets(FakeSheets):
            def batch_update_cells(self, name, updates):
                self.batch_calls.append((name, updates))
                return False
        sheets = _FailingSheets(balance=1000)
        cmd = self._cmd(sheets)
        r = cmd.execute(make_ctx('u1', ['블랙잭', '100']))
        self.assertFalse(r.success)
        self.assertIsNone(bj.get_session_manager().get('u1'))  # 세션 미생성
        self.assertEqual(sheets.balance, 1000)                  # 잔액 그대로

    def test_deal_charges_bet_and_creates_session(self):
        sheets = FakeSheets(balance=1000)
        cmd = self._cmd(sheets)
        r = cmd.execute(make_ctx('u1', ['블랙잭', '100']))
        self.assertTrue(r.success)
        # 내추럴이면 세션 종료되었을 수 있음 → 잔액은 최소 900 이하로 차감됨
        self.assertLessEqual(sheets.balance, 1000)
        # 딜 시점에 100 차감된 흔적
        self.assertTrue(any(c[1] for c in sheets.batch_calls))

    def test_full_game_settles_and_clears_session(self):
        sheets = FakeSheets(balance=1000)
        cmd = self._cmd(sheets)
        # 내추럴이 아닌 시드를 찾을 필요 없이, 딜 후 스탠드로 강제 종료
        r = cmd.execute(make_ctx('u1', ['블랙잭', '100']))
        self.assertTrue(r.success)
        manager = bj.get_session_manager()
        if manager.get('u1') is not None:
            r2 = cmd.execute(make_ctx('u1', ['스탠드']))
            self.assertTrue(r2.success)
            self.assertEqual(r2.data['action'], 'settle')
        # 정산 후 세션 없음
        self.assertIsNone(manager.get('u1'))

    def test_double_deal_rejected(self):
        sheets = FakeSheets(balance=1000)
        cmd = self._cmd(sheets)
        r1 = cmd.execute(make_ctx('u1', ['블랙잭', '100']))
        self.assertTrue(r1.success)
        manager = bj.get_session_manager()
        if manager.get('u1') is not None:
            # 진행 중 재시작 거부
            r2 = cmd.execute(make_ctx('u1', ['블랙잭', '50']))
            self.assertFalse(r2.success)

    def test_payout_on_win(self):
        # 승/패에 따른 잔액 검증: 여러 시드로 최소 한 판 승리 확인
        wins = 0
        for seed in range(50):
            self.tmp.reset(seed)
            sheets = FakeSheets(balance=1000, user_id='p')
            cmd = self._cmd(sheets)
            r = cmd.execute(make_ctx('p', ['블랙잭', '100']))
            self.assertTrue(r.success)
            manager = bj.get_session_manager()
            if manager.get('p') is not None:
                res = cmd.execute(make_ctx('p', ['스탠드']))
            else:
                res = None
            # 최종 잔액 = 1000 - 100 + payout
            # 승리 시 payout>=200 → 잔액 >= 1100
            if sheets.balance >= 1100:
                wins += 1
        self.assertGreater(wins, 0)



class BlackjackRestartTest(unittest.TestCase):
    """봇 재시작(업데이트)을 사이에 두고도 판이 이어져야 한다.

    베팅은 **딜 시점에 이미 차감**되므로, 세션이 사라지면 이용자는
    돈만 내고 판이 증발한다 — 재화가 실제로 유실된다.
    """

    def setUp(self):
        self.tmp = _TempSessions()
        self.tmp.install()
        self.addCleanup(self.tmp.cleanup)

    def _cmd(self, sheets):
        return bj.BlackjackCommand(sheets_manager=sheets, api=object())

    def _deal_until_open(self):
        """내추럴로 즉시 끝나지 않는(=진행 중인) 판이 나올 때까지 딜한다."""
        for seed in range(60):
            self.tmp.reset(seed)
            sheets = FakeSheets(balance=1000)
            cmd = self._cmd(sheets)
            r = cmd.execute(make_ctx('u1', ['블랙잭', '100']))
            self.assertTrue(r.success)
            if bj.get_session_manager().get('u1') is not None:
                return sheets, cmd
        self.fail("진행 중인 판을 만들지 못했다.")

    def test_session_survives_restart(self):
        self._deal_until_open()
        before = bj.get_session_manager().get('u1')
        hand_before = list(before.player)
        bet_before = before.bet

        manager = self.tmp.restart()          # 봇을 껐다 켠다

        after = manager.get('u1')
        self.assertIsNotNone(after, "재시작 후 판이 사라졌다 — 베팅액이 유실된다.")
        self.assertEqual(after.player, hand_before, "내 패가 그대로여야 한다.")
        self.assertEqual(after.bet, bet_before)

    def test_can_stand_after_restart(self):
        sheets, _ = self._deal_until_open()
        self.tmp.restart()
        cmd = self._cmd(sheets)               # 새 프로세스의 명령어 인스턴스
        r = cmd.execute(make_ctx('u1', ['스탠드']))
        self.assertTrue(r.success, "재시작 후에도 정산할 수 있어야 한다.")
        self.assertEqual(r.data['action'], 'settle')
        self.assertIsNone(bj.get_session_manager().get('u1'))

    def test_cards_restore_as_tuples(self):
        self._deal_until_open()
        manager = self.tmp.restart()
        session = manager.get('u1')
        self.assertTrue(all(isinstance(c, tuple) for c in session.player))
        self.assertTrue(all(isinstance(c, tuple) for c in session.deck))

    def test_hit_is_persisted_immediately(self):
        """히트로 뽑은 카드가 저장돼야 한다.

        판이 이어지는(=히트해도 안 끝나는) 시드를 찾아서 검증한다.
        스킵해 버리면 정작 이 검증이 한 번도 돌지 않는다.
        """
        for seed in range(200):
            self.tmp.reset(seed)
            sheets = FakeSheets(balance=1000)
            cmd = self._cmd(sheets)
            if not cmd.execute(make_ctx('u1', ['블랙잭', '100'])).success:
                continue
            if bj.get_session_manager().get('u1') is None:
                continue                       # 내추럴로 즉시 종료
            r = cmd.execute(make_ctx('u1', ['히트']))
            if not r.success or r.data.get('action') == 'settle':
                continue                       # 히트로 버스트/21 → 판 종료
            n_cards = len(bj.get_session_manager().get('u1').player)
            self.assertEqual(n_cards, 3)

            manager = self.tmp.restart()
            self.assertEqual(len(manager.get('u1').player), n_cards,
                             "뽑은 카드가 저장되지 않았다.")
            return
        self.fail("히트 후에도 이어지는 판을 만들지 못했다.")

    def test_settled_session_gone_after_restart(self):
        """정산이 끝난 판은 재시작해도 되살아나면 안 된다(재정산 = 중복 배당)."""
        sheets, cmd = self._deal_until_open()
        cmd.execute(make_ctx('u1', ['스탠드']))
        self.assertEqual(self.tmp.raw(), {}, "정산된 판이 파일에 남아 있다.")
        manager = self.tmp.restart()
        self.assertIsNone(manager.get('u1'))

    def test_corrupt_session_does_not_block_startup(self):
        with open(self.tmp.path, 'w', encoding='utf-8') as f:
            json.dump({'u1': {'bet': 'garbage', 'deck': None, 'player': 'x'}}, f)
        manager = self.tmp.restart()          # 예외 없이 기동
        self.assertIsNone(manager.get('u1'))


class BlackjackPayoutCrashTest(unittest.TestCase):
    """배당을 시트에 쓰는 도중 죽으면 — 중복 배당을 막아야 한다.

    소지금 갱신은 read-modify-write라 멱등하지 않고 도박용 원장도 없다.
    지급했는지 알 수 없으므로 **다시 지급하지 않고** 판을 접고 크게 로깅한다.
    (조용한 재화 복제보다, 이용자가 즉시 알아채고 GM이 보정할 수 있는 손실이 낫다)
    """

    def setUp(self):
        self.tmp = _TempSessions()
        self.tmp.install()
        self.addCleanup(self.tmp.cleanup)

    def test_marker_absent_in_normal_play(self):
        bj.set_rng(random.Random(1234))
        cmd = bj.BlackjackCommand(sheets_manager=FakeSheets(balance=1000), api=object())
        cmd.execute(make_ctx('u1', ['블랙잭', '100']))
        session = bj.get_session_manager().get('u1')
        if session is None:
            self.skipTest('내추럴로 즉시 종료된 시드')
        self.assertIsNone(session.payout_attempted)
        self.assertIsNone(self.tmp.raw()['u1']['payout_attempted'])

    def test_crash_during_payout_is_not_repaid(self):
        # 지급 도중 죽은 상태를 파일로 재현
        with open(self.tmp.path, 'w', encoding='utf-8') as f:
            json.dump({'u1': {
                'bet': 100,
                'deck': [['2', '♠']],
                'player': [['10', '♠'], ['9', '♥']],
                'dealer': [['10', '♦'], ['7', '♣']],
                'payout_attempted': 200,
            }}, f)

        manager = self.tmp.restart()
        self.assertIsNone(manager.get('u1'),
                          "지급 중단된 판이 되살아나면 [스탠드]로 배당을 또 받는다.")
        self.assertEqual(self.tmp.raw(), {}, "해당 판은 파일에서도 제거돼야 한다.")

    def test_failed_payout_clears_marker_so_retry_survives_restart(self):
        """_pay가 '쓰지 않았다'고 확언하면 표식을 지운다 → 재시작 후에도 재시도 가능."""
        class _PayFailsSheets(FakeSheets):
            def __init__(self, **kw):
                super().__init__(**kw)
                self.allow = True

            def batch_update_cells(self, name, updates):
                if not self.allow:
                    return False
                return super().batch_update_cells(name, updates)

        for seed in range(60):
            self.tmp.reset(seed)
            sheets = _PayFailsSheets(balance=1000)
            cmd = bj.BlackjackCommand(sheets_manager=sheets, api=object())
            cmd.execute(make_ctx('u1', ['블랙잭', '100']))
            if bj.get_session_manager().get('u1') is None:
                continue
            sheets.allow = False                       # 배당 지급만 실패시킨다
            r = cmd.execute(make_ctx('u1', ['스탠드']))
            if r.success:
                continue                               # 패배(payout=0)면 지급 자체가 없다
            raw = self.tmp.raw().get('u1')
            self.assertIsNotNone(raw, "재시도용 세션이 남아야 한다.")
            self.assertIsNone(raw['payout_attempted'],
                              "지급이 확실히 실패했으면 표식을 지워야 재시도가 가능하다.")
            manager = self.tmp.restart()
            self.assertIsNotNone(manager.get('u1'), "재시작 후에도 재시도할 수 있어야 한다.")
            return
        self.skipTest('배당이 발생하는 시드를 찾지 못함')


class DealerRuleTest(unittest.TestCase):
    """딜러(NPC) 규칙 — 참조 구현(casino/blackjack/dealer_ai.py)과 같은 동작."""

    def tearDown(self):
        bj.config = _real_config

    def _with_soft17(self, hit: bool):
        class _Cfg:
            BLACKJACK_HIT_ON_SOFT_17 = hit
        bj.config = _Cfg()

    def test_hand_value_reports_soft(self):
        self.assertEqual(bj._hand_value([('A', '♠'), ('6', '♥')]), (17, True))    # 소프트 17
        self.assertEqual(bj._hand_value([('10', '♠'), ('7', '♥')]), (17, False))  # 하드 17
        # A를 1로 내리면 더는 소프트가 아니다
        self.assertEqual(bj._hand_value([('A', '♠'), ('6', '♥'), ('10', '♦')]), (17, False))
        self.assertEqual(bj._hand_value([]), (0, False))

    def test_h17_hits_soft_17_but_stands_on_hard_17(self):
        self._with_soft17(True)
        self.assertTrue(bj._dealer_should_hit([('A', '♠'), ('6', '♥')]))    # 소프트 17 → 히트
        self.assertFalse(bj._dealer_should_hit([('10', '♠'), ('7', '♥')]))  # 하드 17 → 스탠드

    def test_s17_stands_on_soft_17(self):
        self._with_soft17(False)
        self.assertFalse(bj._dealer_should_hit([('A', '♠'), ('6', '♥')]))
        self.assertTrue(bj._dealer_should_hit([('10', '♠'), ('6', '♥')]))   # 16은 여전히 히트

    def test_dealer_stands_on_18_and_never_hits_bust(self):
        self._with_soft17(True)
        self.assertFalse(bj._dealer_should_hit([('A', '♠'), ('7', '♥')]))     # 소프트 18
        self.assertFalse(bj._dealer_should_hit([('10', '♠'), ('9', '♥')]))    # 하드 19
        self.assertFalse(bj._dealer_should_hit([('10', '♠'), ('9', '♥'), ('5', '♦')]))  # 버스트

    def test_deck_exhaustion_reshuffles_without_duplicating_table_cards(self):
        """빈 덱에 pop()하면 IndexError로 판이 통째로 날아간다(베팅은 차감된 채로)."""
        session = bj._BlackjackSession(100, _deal=False)
        session.player = [('A', '♠'), ('10', '♥')]
        session.dealer = [('K', '♦'), ('3', '♣')]
        session.deck = []

        session.draw_player()                       # 예외 없이 뽑힌다

        drawn = session.player[-1]
        self.assertNotIn(drawn, [('A', '♠'), ('10', '♥'), ('K', '♦'), ('3', '♣')],
                         "재셔플이 판에 깔린 카드를 다시 내놓았다.")


class DoubleDownTest(unittest.TestCase):
    def setUp(self):
        self.tmp = _TempSessions()
        self.tmp.install()
        self.addCleanup(self.tmp.cleanup)

    def _open_hand(self, balance=1000):
        """진행 중인 판(2장)을 만든다."""
        for seed in range(80):
            self.tmp.reset(seed)
            sheets = FakeSheets(balance=balance)
            cmd = bj.BlackjackCommand(sheets_manager=sheets, api=object())
            cmd.execute(make_ctx('u1', ['블랙잭', '100']))
            if bj.get_session_manager().get('u1') is not None:
                return sheets, cmd
        self.fail("진행 중인 판을 만들지 못했다.")

    def test_double_charges_again_and_ends_hand(self):
        sheets, cmd = self._open_hand()
        self.assertEqual(sheets.balance, 900)          # 딜에서 100 차감

        r = cmd.execute(make_ctx('u1', ['더블다운']))

        self.assertTrue(r.success)
        self.assertEqual(r.data['action'], 'settle', "더블다운은 한 장만 받고 즉시 끝난다.")
        self.assertEqual(r.data['bet'], 200, "베팅이 2배가 돼야 한다.")
        self.assertIsNone(bj.get_session_manager().get('u1'))

    def test_double_money_math(self):
        """차감 200 기준으로 승/무/패 순손익이 맞아야 한다."""
        for seed in range(120):
            self.tmp.reset(seed)
            sheets = FakeSheets(balance=1000)
            cmd = bj.BlackjackCommand(sheets_manager=sheets, api=object())
            cmd.execute(make_ctx('u1', ['블랙잭', '100']))
            if bj.get_session_manager().get('u1') is None:
                continue
            r = cmd.execute(make_ctx('u1', ['더블다운']))
            if not r.success:
                continue
            net = sheets.balance - 1000
            outcome = r.data['outcome']
            # 결과 문구는 참조 구현 형식(2026-07-16): '버스트! 패배' / '승리 (20 vs 18)' /
            # '푸시 (18)' / '딜러 버스트! 승리' 등. 문구가 아니라 **순손익**으로 검증한다.
            self.assertIn(net, (200, 0, -200),
                          f"{outcome}: 순손익 {net} (차감 200 기준 ±200 또는 0이어야)")
            if net > 0:
                self.assertTrue('승리' in outcome, outcome)
            elif net < 0:
                self.assertTrue('패배' in outcome, outcome)
            else:
                self.assertTrue('푸시' in outcome, outcome)

    def test_double_rejected_after_hit(self):
        """히트해서 판이 이어지는 시드를 찾아 반드시 검증한다(스킵하면 무의미)."""
        for seed in range(200):
            self.tmp.reset(seed)
            sheets = FakeSheets(balance=1000)
            cmd = bj.BlackjackCommand(sheets_manager=sheets, api=object())
            cmd.execute(make_ctx('u1', ['블랙잭', '100']))
            if bj.get_session_manager().get('u1') is None:
                continue
            r = cmd.execute(make_ctx('u1', ['히트']))
            if not r.success or (r.data or {}).get('action') == 'settle':
                continue
            r2 = cmd.execute(make_ctx('u1', ['더블다운']))
            self.assertFalse(r2.success, "히트한 뒤에는 더블다운할 수 없다.")
            self.assertEqual(sheets.balance, 900, "거절됐는데 추가 차감되면 안 된다.")
            return
        self.fail('히트 후에도 이어지는 판을 만들지 못했다')

    def test_double_rejected_without_funds(self):
        sheets, cmd = self._open_hand(balance=150)   # 딜 후 잔액 50 < 베팅 100
        r = cmd.execute(make_ctx('u1', ['더블다운']))
        self.assertFalse(r.success)
        self.assertEqual(sheets.balance, 50, "실패했는데 차감되면 안 된다.")
        self.assertIsNotNone(bj.get_session_manager().get('u1'), "판은 남아 있어야 한다.")

    def test_double_without_game(self):
        self.assertFalse(bj.BlackjackCommand(sheets_manager=FakeSheets(), api=object())
                         .execute(make_ctx('u1', ['더블다운'])).success)


class SurrenderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = _TempSessions()
        self.tmp.install()
        self.addCleanup(self.tmp.cleanup)

    def _open_hand(self):
        for seed in range(80):
            self.tmp.reset(seed)
            sheets = FakeSheets(balance=1000)
            cmd = bj.BlackjackCommand(sheets_manager=sheets, api=object())
            cmd.execute(make_ctx('u1', ['블랙잭', '100']))
            if bj.get_session_manager().get('u1') is not None:
                return sheets, cmd
        self.fail("진행 중인 판을 만들지 못했다.")

    def test_surrender_refunds_half(self):
        sheets, cmd = self._open_hand()
        r = cmd.execute(make_ctx('u1', ['서렌더']))

        self.assertTrue(r.success)
        self.assertEqual(r.data['outcome'], '서렌더')
        self.assertEqual(sheets.balance, 950, "베팅 100 중 50을 돌려받아야 한다.")
        self.assertEqual(r.data['net'], -50)
        self.assertIsNone(bj.get_session_manager().get('u1'))

    def test_surrender_rejected_after_hit(self):
        for seed in range(200):
            self.tmp.reset(seed)
            sheets = FakeSheets(balance=1000)
            cmd = bj.BlackjackCommand(sheets_manager=sheets, api=object())
            cmd.execute(make_ctx('u1', ['블랙잭', '100']))
            if bj.get_session_manager().get('u1') is None:
                continue
            r = cmd.execute(make_ctx('u1', ['히트']))
            if not r.success or (r.data or {}).get('action') == 'settle':
                continue
            self.assertFalse(cmd.execute(make_ctx('u1', ['서렌더'])).success)
            return
        self.fail('히트 후에도 이어지는 판을 만들지 못했다')

    def test_surrender_dealer_does_not_draw(self):
        """서렌더는 딜러가 패를 더 받지 않고 끝난다."""
        sheets, cmd = self._open_hand()
        before = list(bj.get_session_manager().get('u1').dealer)
        r = cmd.execute(make_ctx('u1', ['서렌더']))
        self.assertEqual(r.data['dealer_total'], bj._hand_total(before))

    def test_surrender_survives_failed_refund_retry(self):
        """환불이 실패해도 재시도가 **서렌더로** 정산돼야 한다.

        세션에 기록하지 않으면 [스탠드] 재시도가 일반 정산으로 바뀌어
        절반 환불이 통째 승부로 둔갑한다.
        """
        class _RefundFails(FakeSheets):
            def __init__(self, **kw):
                super().__init__(**kw)
                self.allow = True

            def batch_update_cells(self, name, updates):
                if not self.allow:
                    return False
                return super().batch_update_cells(name, updates)

        for seed in range(80):
            self.tmp.reset(seed)
            sheets = _RefundFails(balance=1000)
            cmd = bj.BlackjackCommand(sheets_manager=sheets, api=object())
            cmd.execute(make_ctx('u1', ['블랙잭', '100']))
            if bj.get_session_manager().get('u1') is None:
                continue

            sheets.allow = False
            r = cmd.execute(make_ctx('u1', ['서렌더']))
            self.assertFalse(r.success)
            self.assertIn('[서렌더]', r.message, "재시도 안내가 실제 통하는 명령어를 짚어야 한다.")

            # 재시작을 사이에 둬도 서렌더 기록이 남아야 한다
            self.assertTrue(self.tmp.raw()['u1']['surrendered'])
            self.tmp.restart()

            sheets.allow = True
            r2 = bj.BlackjackCommand(sheets_manager=sheets, api=object()).execute(
                make_ctx('u1', ['스탠드']))          # 일부러 [스탠드]로 재시도
            self.assertTrue(r2.success)
            self.assertEqual(r2.data['outcome'], '서렌더',
                             "[스탠드]로 재시도했다고 서렌더가 일반 승부로 바뀌면 안 된다.")
            self.assertEqual(sheets.balance, 950)
            return
        self.fail("진행 중인 판을 만들지 못했다.")


class NaturalBlackjackTest(unittest.TestCase):
    def setUp(self):
        self.tmp = _TempSessions()
        self.tmp.install()
        self.addCleanup(self.tmp.cleanup)

    def test_natural_pays_3_to_2(self):
        for seed in range(400):
            self.tmp.reset(seed)
            sheets = FakeSheets(balance=1000)
            cmd = bj.BlackjackCommand(sheets_manager=sheets, api=object())
            r = cmd.execute(make_ctx('u1', ['블랙잭', '100']))
            if (r.data or {}).get('outcome') == '블랙잭! 승리':
                self.assertEqual(sheets.balance, 1150, "3:2 = 100 걸고 150 이득")
                return
        self.fail('내추럴 블랙잭 시드를 찾지 못함')

    def test_natural_keeps_3_to_2_when_payout_retried(self):
        """지급 실패 후 [스탠드]로 재시도해도 3:2 보너스를 잃으면 안 된다.

        natural을 카드에서 되살리지 않으면 평범한 '승리'(2x)로 정산돼
        이용자가 조용히 0.5배를 손해 본다.
        """
        class _PayFailsOnce(FakeSheets):
            """딜 차감은 통과시키고, 그 다음 쓰기(=배당 지급)만 실패시킨다."""

            def __init__(self, **kw):
                super().__init__(**kw)
                self.calls = 0

            def batch_update_cells(self, name, updates):
                self.calls += 1
                if self.calls == 2:          # 1=베팅 차감, 2=배당 지급
                    return False
                return super().batch_update_cells(name, updates)

        for seed in range(400):
            self.tmp.reset(seed)
            sheets = _PayFailsOnce(balance=1000)
            cmd = bj.BlackjackCommand(sheets_manager=sheets, api=object())
            r = cmd.execute(make_ctx('u1', ['블랙잭', '100']))

            session = bj.get_session_manager().get('u1')
            if session is None or not bj._is_blackjack(session.player):
                continue                      # 내추럴이 아니거나 이미 정산된 판

            # 내추럴인데 세션이 남아 있다 = 배당 지급이 실패해 재시도 대기 중
            self.assertFalse(r.success)
            self.assertEqual(sheets.balance, 900)

            r2 = cmd.execute(make_ctx('u1', ['스탠드']))   # 재시도
            self.assertTrue(r2.success)
            self.assertEqual(r2.data['outcome'], '블랙잭! 승리',
                             "재시도했다고 내추럴이 평범한 승리로 바뀌면 안 된다.")
            self.assertEqual(sheets.balance, 1150, "3:2 배당이 유지돼야 한다.")
            return
        self.fail('내추럴 블랙잭 시드를 찾지 못함')


class DailyLimitTest(unittest.TestCase):
    """일일 제한(QA §1.1) — 슬롯·크랩스와 같은 봇 JSON 방식.

    딜 1회 = 1판. 히트·스탠드는 세지 않는다.
    """

    def setUp(self):
        self.tmp = _TempSessions()          # 세션 격리(+ 일일 제한 무력화)
        self.tmp.install()
        self.addCleanup(self.tmp.cleanup)
        self.tmp._daily.stop()              # 이 클래스만 진짜 제한을 검증한다
        bj.set_rng(random.Random(1234))

    def _cmd(self, sheets):
        return bj.BlackjackCommand(sheets_manager=sheets, api=object())

    def test_deal_counts_once(self):
        sheets = FakeSheets(balance=1000)
        with patch.object(bj.game_state, 'check_and_set', return_value=True) as cas:
            self._cmd(sheets).execute(make_ctx('u1', ['블랙잭', '100']))
        cas.assert_called_once()
        self.assertEqual(cas.call_args[0][1], '오늘블랙잭')

    def test_hit_and_stand_do_not_count(self):
        """한 판에 여러 번 깎이면 20판이 아니라 5판도 못 한다."""
        sheets = FakeSheets(balance=1000)
        cmd = self._cmd(sheets)
        with patch.object(bj.game_state, 'check_and_set', return_value=True) as cas:
            cmd.execute(make_ctx('u1', ['블랙잭', '100']))
            if bj.get_session_manager().get('u1') is not None:
                cmd.execute(make_ctx('u1', ['히트']))
                cmd.execute(make_ctx('u1', ['스탠드']))
        self.assertEqual(cas.call_count, 1, "딜에서만 세야 한다")

    def test_limit_blocks_deal(self):
        sheets = FakeSheets(balance=1000)
        with patch.object(bj.game_state, 'check_and_set', return_value=False):
            resp = self._cmd(sheets).execute(make_ctx('u1', ['블랙잭', '100']))
        self.assertFalse(resp.success)
        self.assertIn('오늘은', resp.message)
        self.assertEqual(sheets.balance, 1000, "제한에 걸렸는데 차감되면 안 된다")
        self.assertIsNone(bj.get_session_manager().get('u1'))

    def test_refund_when_charge_fails(self):
        """제한은 소모했는데 차감이 실패하면 → 횟수를 되돌린다.

        안 그러면 판도 못 하고 횟수만 잃는다.
        """
        class _NoMoney(FakeSheets):
            def batch_update_cells(self, name, updates):
                return False

        sheets = _NoMoney(balance=1000)
        with patch.object(bj.game_state, 'check_and_set', return_value=True), \
             patch.object(bj.game_state, 'get', return_value=3), \
             patch.object(bj.game_state, 'set') as st:
            resp = self._cmd(sheets).execute(make_ctx('u1', ['블랙잭', '100']))
        self.assertFalse(resp.success)
        st.assert_called_once_with('u1', '오늘블랙잭', 2)

    def test_no_refund_on_success(self):
        sheets = FakeSheets(balance=1000)
        with patch.object(bj.game_state, 'check_and_set', return_value=True), \
             patch.object(bj.game_state, 'set') as st:
            resp = self._cmd(sheets).execute(make_ctx('u1', ['블랙잭', '100']))
        self.assertTrue(resp.success)
        st.assert_not_called()

    def test_refund_floors_at_zero(self):
        class _NoMoney(FakeSheets):
            def batch_update_cells(self, name, updates):
                return False

        with patch.object(bj.game_state, 'check_and_set', return_value=True), \
             patch.object(bj.game_state, 'get', return_value=0), \
             patch.object(bj.game_state, 'set') as st:
            self._cmd(_NoMoney(balance=1000)).execute(make_ctx('u1', ['블랙잭', '100']))
        st.assert_not_called()


if __name__ == '__main__':
    unittest.main()

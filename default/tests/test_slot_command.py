"""commands/shinarmont/slot_command.py 단위 테스트.

스펙(2026-07-16 확정):
  심볼   ▲ △ 각 25% · ■ □ 각 20% · ● ○ 각 5%  (가중치 합 100)
  3개    ●●● 100배 · ■■■ 20배 · ▲▲▲ 10배
  2개    ●● 1.5배 · ■■ 0.7배 · ▲▲ 0.3배      → RTP 약 89.8%
  베팅   3~10달러 (BET_MAX와 무관한 슬롯 전용 한도)
  반올림 **올림**(math.ceil) — 최소 베팅에서도 2개 일치가 0원이 되지 않게
  제한   하루 20회 — **봇 JSON**(game_state '오늘슬롯'). 시트 컬럼을 쓰지 않는다.
"""

import os
import sys
import math
import random
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from commands.base_command import CommandContext
import commands.shinarmont.slot_command as slot
from commands.shinarmont.slot_command import (
    SlotCommand,
    spin_reels,
    evaluate,
    payout_multiplier,
    SYMBOLS,
    TIER_LOW,
    TIER_MID,
    TIER_HIGH,
    PAYOUT_TRIPLE,
    PAYOUT_DOUBLE,
    _parse_money,
)


def _make_manager(money=1000, row_number=2, header=None):
    """관리 시트를 흉내내는 페이크 매니저."""
    if header is None:
        header = ['아이디', '이름', '소지금', '소지품']
    mgr = MagicMock()
    mgr.get_worksheet_data.return_value = [
        {'아이디': 'alice', '이름': 'Alice', '소지금': money, '_row_number': row_number},
    ]
    ws = MagicMock()
    ws.row_values.return_value = header
    mgr.get_worksheet.return_value = ws
    mgr.batch_update_cells.return_value = True
    return mgr


def _ctx(bet="5", user_id="alice"):
    return CommandContext(user_id=user_id, user_name="Alice", keywords=["슬롯머신", bet])


class SymbolTableTest(unittest.TestCase):
    def test_weights_sum_to_100(self):
        """가중치 합이 100이어야 _weighted_symbol 의 구간 계산이 성립한다."""
        self.assertEqual(sum(w for _tier, w in SYMBOLS.values()), 100)

    def test_symbol_set(self):
        self.assertEqual(set(SYMBOLS), {'▲', '△', '■', '□', '●', '○'})

    def test_tiers(self):
        self.assertEqual(SYMBOLS['▲'][0], TIER_LOW)
        self.assertEqual(SYMBOLS['△'][0], TIER_LOW)
        self.assertEqual(SYMBOLS['■'][0], TIER_MID)
        self.assertEqual(SYMBOLS['□'][0], TIER_MID)
        self.assertEqual(SYMBOLS['●'][0], TIER_HIGH)
        self.assertEqual(SYMBOLS['○'][0], TIER_HIGH)

    def test_weights(self):
        self.assertEqual(SYMBOLS['▲'][1], 25)
        self.assertEqual(SYMBOLS['■'][1], 20)
        self.assertEqual(SYMBOLS['●'][1], 5)

    def test_higher_tier_pays_more(self):
        """등급이 높을수록 더 준다 — 이게 뒤집히면 확률 설계가 무의미해진다."""
        self.assertLess(PAYOUT_TRIPLE[TIER_LOW], PAYOUT_TRIPLE[TIER_MID])
        self.assertLess(PAYOUT_TRIPLE[TIER_MID], PAYOUT_TRIPLE[TIER_HIGH])
        self.assertLess(PAYOUT_DOUBLE[TIER_LOW], PAYOUT_DOUBLE[TIER_MID])
        self.assertLess(PAYOUT_DOUBLE[TIER_MID], PAYOUT_DOUBLE[TIER_HIGH])


class EvaluateTest(unittest.TestCase):
    def test_triple_high(self):
        for s in ('●', '○'):
            mult, tier, matched = evaluate([s, s, s])
            self.assertEqual(mult, 100.0)
            self.assertEqual(tier, TIER_HIGH)
            self.assertEqual(matched, 3)

    def test_triple_mid(self):
        for s in ('■', '□'):
            self.assertEqual(evaluate([s, s, s])[0], 20.0)

    def test_triple_low(self):
        for s in ('▲', '△'):
            self.assertEqual(evaluate([s, s, s])[0], 10.0)

    def test_double_by_tier(self):
        self.assertEqual(evaluate(['●', '●', '▲'])[0], 1.5)
        self.assertEqual(evaluate(['■', '■', '▲'])[0], 0.7)
        self.assertEqual(evaluate(['▲', '▲', '●'])[0], 0.3)

    def test_double_position_independent(self):
        for reels in (['●', '●', '▲'], ['●', '▲', '●'], ['▲', '●', '●']):
            self.assertEqual(evaluate(reels)[0], 1.5, reels)

    def test_double_uses_the_paired_symbol_not_the_odd_one(self):
        """▲▲● 는 세모 2개다 — 끼어든 ●를 보고 1.5배를 주면 안 된다."""
        mult, tier, _ = evaluate(['▲', '▲', '●'])
        self.assertEqual(tier, TIER_LOW)
        self.assertEqual(mult, 0.3)

    def test_no_match(self):
        mult, tier, matched = evaluate(['●', '■', '▲'])
        self.assertEqual(mult, 0.0)
        self.assertIsNone(tier)
        self.assertEqual(matched, 0)

    def test_same_tier_different_symbols_is_not_a_match(self):
        """▲와 △는 같은 등급이지만 다른 심볼 → 일치가 아니다."""
        self.assertEqual(evaluate(['▲', '△', '●'])[0], 0.0)

    def test_payout_multiplier_wrapper(self):
        self.assertEqual(payout_multiplier(['○', '○', '○']), 100.0)


class SpinTest(unittest.TestCase):
    def test_three_reels_of_valid_symbols(self):
        for _ in range(50):
            reels = spin_reels(random.Random(0))
            self.assertEqual(len(reels), 3)
            for s in reels:
                self.assertIn(s, SYMBOLS)

    def test_injected_rng_does_not_leak(self):
        """rng 를 넘겨도 전역 RNG가 바뀌면 안 된다(다음 스핀이 예측 가능해진다)."""
        before = slot._rng
        spin_reels(random.Random(1))
        self.assertIs(slot._rng, before)

    def test_distribution_matches_weights(self):
        """가중치대로 나오는지 — 여기가 틀어지면 RTP 설계가 통째로 무너진다."""
        slot.set_rng(random.Random(20260716))
        self.addCleanup(slot.set_rng, random.SystemRandom())
        n = 60_000
        counts = {}
        for _ in range(n):
            s = slot._weighted_symbol()
            counts[s] = counts.get(s, 0) + 1
        for symbol, (_tier, weight) in SYMBOLS.items():
            got = counts.get(symbol, 0) / n * 100
            self.assertAlmostEqual(got, weight, delta=1.0, msg=f"{symbol}: {got:.2f}%")

    def test_rtp_is_about_90_percent(self):
        """RTP 설계값 89.8%. 크게 벗어나면 경제가 흔들린다."""
        slot.set_rng(random.Random(42))
        self.addCleanup(slot.set_rng, random.SystemRandom())
        bet, n, paid = 10, 60_000, 0.0
        for _ in range(n):
            paid += bet * evaluate(spin_reels())[0]
        rtp = paid / (n * bet) * 100
        self.assertAlmostEqual(rtp, 89.8, delta=3.0, msg=f"RTP {rtp:.2f}%")


class ParseMoneyTest(unittest.TestCase):
    def test_parse_money(self):
        self.assertEqual(_parse_money("1,000"), 1000)
        self.assertEqual(_parse_money(""), 0)
        self.assertEqual(_parse_money("abc"), 0)
        self.assertEqual(_parse_money(250.7), 250)


class BetRangeTest(unittest.TestCase):
    """베팅 한도를 **테스트가 고정한다** — GM이 .env를 만져도 빨개지면 안 된다."""

    def setUp(self):
        from config.settings import config
        self.config = config
        self._saved = (getattr(config, 'SLOT_BET_MIN', None),
                       getattr(config, 'SLOT_BET_MAX', None))
        config.SLOT_BET_MIN = 3
        config.SLOT_BET_MAX = 10
        self.addCleanup(self._restore)

    def _restore(self):
        for k, v in zip(('SLOT_BET_MIN', 'SLOT_BET_MAX'), self._saved):
            if v is not None:
                setattr(self.config, k, v)

    def _cmd(self, mgr):
        return SlotCommand(sheets_manager=mgr, api=None)

    def test_below_min_rejected(self):
        mgr = _make_manager()
        resp = self._cmd(mgr).execute(_ctx("2"))
        self.assertFalse(resp.is_successful())
        self.assertIn('최소', resp.message)
        mgr.batch_update_cells.assert_not_called()

    def test_above_max_rejected(self):
        mgr = _make_manager()
        resp = self._cmd(mgr).execute(_ctx("11"))
        self.assertFalse(resp.is_successful())
        self.assertIn('최대', resp.message)
        mgr.batch_update_cells.assert_not_called()

    def test_boundaries_accepted(self):
        for bet in ('3', '10'):
            mgr = _make_manager(money=1000)
            with patch.object(slot.game_state, 'check_and_set', return_value=True):
                resp = self._cmd(mgr).execute(_ctx(bet))
            self.assertTrue(resp.is_successful(), f"베팅 {bet}: {resp.message}")

    def test_zero_and_negative_rejected(self):
        for bet in ('0', '-5'):
            mgr = _make_manager()
            resp = self._cmd(mgr).execute(_ctx(bet))
            self.assertFalse(resp.is_successful())
            mgr.batch_update_cells.assert_not_called()

    def test_missing_bet_shows_usage(self):
        mgr = _make_manager()
        ctx = CommandContext(user_id="alice", keywords=["슬롯머신"])
        resp = self._cmd(mgr).execute(ctx)
        self.assertFalse(resp.is_successful())
        self.assertIn('[슬롯머신/3]', resp.message)

    def test_reversed_config_does_not_lock_everyone_out(self):
        """MIN > MAX 로 잘못 설정해도 아무도 못 걸면 안 된다 → 뒤바꿔 적용."""
        self.config.SLOT_BET_MIN = 10
        self.config.SLOT_BET_MAX = 2
        self.assertEqual(SlotCommand._bet_range(), (2, 10))


class SettlementTest(unittest.TestCase):
    def setUp(self):
        from config.settings import config
        config.SLOT_BET_MIN, config.SLOT_BET_MAX = 3, 10
        self._p = patch.object(slot.game_state, 'check_and_set', return_value=True)
        self._p.start()
        self.addCleanup(self._p.stop)

    def _cmd(self, mgr):
        return SlotCommand(sheets_manager=mgr, api=None)

    def _force(self, reels):
        p = patch.object(slot, 'spin_reels', return_value=reels)
        p.start()
        self.addCleanup(p.stop)

    def test_jackpot_settlement(self):
        self._force(['○', '○', '○'])
        mgr = _make_manager(money=100)
        resp = self._cmd(mgr).execute(_ctx("10"))
        self.assertTrue(resp.is_successful())
        # 100 - 10 + 10*100 = 1090
        self.assertEqual(resp.data['balance'], 1090)
        self.assertEqual(resp.data['net'], 990)
        self.assertEqual(mgr.batch_update_cells.call_args[0][1][0], (2, 3, 1090))

    def test_loss_settlement(self):
        self._force(['●', '■', '▲'])
        mgr = _make_manager(money=100)
        resp = self._cmd(mgr).execute(_ctx("10"))
        self.assertTrue(resp.is_successful())
        self.assertEqual(resp.data['balance'], 90)
        self.assertEqual(resp.data['net'], -10)

    def test_fractional_payout_ceils(self):
        """0.3배 같은 소수 배수는 **올림**한다(2026-07-16 운영 결정).

        내림이면 최소 베팅(3)에서 가장 흔한 당첨(▲▲ 0.3배 = 0.9)이 0원이 되어
        꽝과 구분되지 않는다.
        """
        self._force(['▲', '▲', '●'])
        mgr = _make_manager(money=100)
        resp = self._cmd(mgr).execute(_ctx("5"))       # 5 × 0.3 = 1.5 → 2
        self.assertEqual(resp.data['payout'], 2)
        self.assertEqual(resp.data['balance'], 97)

    def test_min_bet_double_is_never_zero(self):
        """최소 베팅에서도 2개 일치는 최소 1달러를 돌려준다."""
        self._force(['▲', '▲', '●'])
        mgr = _make_manager(money=100)
        resp = self._cmd(mgr).execute(_ctx("3"))       # 3 × 0.3 = 0.9 → 1
        self.assertEqual(resp.data['payout'], 1)

    def test_insufficient_funds(self):
        mgr = _make_manager(money=1)
        resp = self._cmd(mgr).execute(_ctx("5"))
        self.assertFalse(resp.is_successful())
        self.assertIn('부족', resp.message)
        mgr.batch_update_cells.assert_not_called()

    def test_user_not_found(self):
        mgr = _make_manager()
        mgr.get_worksheet_data.return_value = []
        resp = self._cmd(mgr).execute(_ctx("5"))
        self.assertFalse(resp.is_successful())


class DailyLimitTest(unittest.TestCase):
    """일일 제한은 **봇 JSON**으로 센다(시트 컬럼 없음)."""

    def setUp(self):
        from config.settings import config
        config.SLOT_BET_MIN, config.SLOT_BET_MAX = 3, 10
        self._force = patch.object(slot, 'spin_reels', return_value=['●', '■', '▲'])
        self._force.start()
        self.addCleanup(self._force.stop)

    def _cmd(self, mgr):
        return SlotCommand(sheets_manager=mgr, api=None)

    def test_uses_json_not_sheet_column(self):
        mgr = _make_manager(money=100)
        with patch.object(slot.game_state, 'check_and_set', return_value=True) as cas:
            self._cmd(mgr).execute(_ctx("5"))
        cas.assert_called_once()
        self.assertEqual(cas.call_args[0][1], '오늘슬롯')
        # 카운터 때문에 관리 시트를 더 쓰지 않는다 — 소지금 갱신 1회뿐
        self.assertEqual(mgr.batch_update_cells.call_count, 1)

    def test_limit_reached_blocks_spin(self):
        mgr = _make_manager(money=100)
        with patch.object(slot.game_state, 'check_and_set', return_value=False):
            resp = self._cmd(mgr).execute(_ctx("5"))
        self.assertFalse(resp.is_successful())
        self.assertIn('오늘은', resp.message)
        mgr.batch_update_cells.assert_not_called()

    def test_refund_when_settlement_fails(self):
        """정산이 실패하면 소모한 횟수를 되돌린다 — 안 그러면 판도 못 하고 횟수만 잃는다."""
        mgr = _make_manager(money=100)
        mgr.batch_update_cells.return_value = False
        with patch.object(slot.game_state, 'check_and_set', return_value=True), \
             patch.object(slot.game_state, 'get', return_value=3), \
             patch.object(slot.game_state, 'set') as st:
            resp = self._cmd(mgr).execute(_ctx("5"))
        self.assertFalse(resp.is_successful())
        st.assert_called_once_with('alice', '오늘슬롯', 2)

    def test_no_refund_on_success(self):
        mgr = _make_manager(money=100)
        with patch.object(slot.game_state, 'check_and_set', return_value=True), \
             patch.object(slot.game_state, 'set') as st:
            resp = self._cmd(mgr).execute(_ctx("5"))
        self.assertTrue(resp.is_successful())
        st.assert_not_called()

    def test_refund_floors_at_zero(self):
        """이미 0이면 음수로 내려가지 않는다."""
        mgr = _make_manager(money=100)
        mgr.batch_update_cells.return_value = False
        with patch.object(slot.game_state, 'check_and_set', return_value=True), \
             patch.object(slot.game_state, 'get', return_value=0), \
             patch.object(slot.game_state, 'set') as st:
            self._cmd(mgr).execute(_ctx("5"))
        st.assert_not_called()


class MessageTest(unittest.TestCase):
    def setUp(self):
        self.cmd = SlotCommand.__new__(SlotCommand)

    def _msg(self, reels, bet, before=10):
        mult, tier, matched = evaluate(reels)
        # 실제 코드와 같은 반올림을 써야 한다 — int()로 두면 코드는 올림인데
        # 테스트만 내림이라 있지도 않은 '0원 당첨'을 검증하게 된다(실제로 그랬다).
        payout = math.ceil(bet * mult)
        after = before - bet + payout
        return self.cmd._build_message(reels, mult, tier, matched, bet, payout, after, '달러')

    def test_reel_line_and_balance_always_present(self):
        msg = self._msg(['●', '■', '▲'], 4)
        self.assertTrue(msg.startswith('[ ● ■ ▲ ]'))
        self.assertIn('➭ 현재 소지금', msg, "꽝이어도 소지금은 알려준다")

    def test_jackpot_message(self):
        msg = self._msg(['○', '○', '○'], 4)
        self.assertIn('동그라미 잭팟!', msg)
        self.assertIn('100배', msg)
        self.assertIn('➭ 400달러 획득', msg)

    def test_double_high_is_a_win(self):
        msg = self._msg(['●', '●', '▲'], 4)
        self.assertIn('1.5배', msg)
        self.assertIn('➭ 6달러 획득', msg)

    def test_double_low_says_partial(self):
        """0.3배는 본전 미만 → '딴다'가 아니라 '일부를 돌려받는다'."""
        msg = self._msg(['▲', '▲', '●'], 4)
        self.assertIn('일부를 돌려받습니다', msg)
        self.assertIn('➭ 2달러 획득', msg)      # 4 × 0.3 = 1.2 → 올림 2

    def test_multiplier_formatting(self):
        self.assertIn('10배', self._msg(['△', '△', '△'], 4))    # 10.0 → '10'
        self.assertIn('1.5배', self._msg(['●', '●', '▲'], 4))   # 1.5 → '1.5'

    def test_min_bet_double_still_pays(self):
        """올림이라 최소 베팅(3)의 ▲▲(0.9)도 1달러가 나온다 — 0원 케이스가 사라졌다."""
        msg = self._msg(['▲', '▲', '●'], 3)
        self.assertIn('➭ 1달러 획득', msg)
        self.assertNotIn('잃음', msg)


if __name__ == '__main__':
    unittest.main()

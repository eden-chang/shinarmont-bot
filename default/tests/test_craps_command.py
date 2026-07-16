"""[크랩스/베팅액] + [주사위 굴림] 단위 테스트 (2026-07-16 다단계 개편).

계약:
  · `[크랩스/N]` 은 베팅만 접수한다. **차감하지 않는다** — 판이 사라져도 손실이 없어야 한다.
  · `[주사위 굴림]` 로 컴아웃 → (포인트 설정 시) 재현 시도 최대 CRAPS_MAX_REROLLS 회.
  · 순이익: 컴아웃 7·11 → +베팅×2 / 포인트 재현 → +베팅×1 / 패배 → -베팅
  · 일일 제한은 **판 시작 시 1회**만 센다(굴림마다 세면 한 판에 여러 번 깎인다).
  · 세션은 디스크 영속 — 봇을 껐다 켜도 굴리던 판이 이어진다.
"""

import os
import sys
import random
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

import commands.shinarmont.craps_command as cc
from commands.shinarmont.craps_command import CrapsCommand
from commands.base_command import CommandContext
from utils.json_store import JsonStore


class FakeSheets:
    """관리 시트 소지금 갱신을 흉내내는 페이크."""

    def __init__(self, balance=1000, write_ok=True):
        self.rows = [{'아이디': 'u1', '이름': '테스트', '소지금': balance, '_row_number': 3}]
        self.batch_calls = []
        self.write_ok = write_ok

    def get_worksheet_data(self, name, use_cache=True):
        return [dict(r) for r in self.rows]

    def get_worksheet(self, name):
        ws = MagicMock()
        ws.row_values.return_value = ['아이디', '이름', '소지금']
        return ws

    def batch_update_cells(self, name, updates):
        self.batch_calls.append((name, updates))
        if not self.write_ok:
            return False
        for row, col, value in updates:
            for r in self.rows:
                if r['_row_number'] == row and col == 3:   # 소지금
                    r['소지금'] = value
        return True

    @property
    def balance(self):
        return self.rows[0]['소지금']


def _ctx(*keywords, user_id='u1'):
    return CommandContext(user_id=user_id, user_name='테스트', keywords=list(keywords))


class _Base(unittest.TestCase):
    """세션·설정·일일제한을 테스트가 고정한다(프로젝트 state/ 오염 방지)."""

    def setUp(self):
        from config.settings import config
        self.config = config
        config.CRAPS_BET_MIN, config.CRAPS_BET_MAX = 1, 100
        config.CRAPS_MAX_REROLLS = 3
        config.CURRENCY = '달러'

        self.dir = tempfile.mkdtemp(prefix='craps_')
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = os.path.join(self.dir, 's.json')
        cc.set_session_manager(cc._CrapsSessionManager(store=JsonStore(self.path)))
        self.addCleanup(cc.set_session_manager, None)

        self._daily = patch.object(cc.game_state, 'check_and_set', return_value=True)
        self.daily = self._daily.start()
        self.addCleanup(self._daily.stop)

        self._inval = patch.object(cc, 'invalidate_user_cache', lambda: None)
        self._inval.start()
        self.addCleanup(self._inval.stop)

    def _cmd(self, sheets):
        return CrapsCommand(sheets_manager=sheets, api=None)

    def _rolls(self, *pairs):
        it = iter(pairs)
        p = patch.object(cc, 'roll_dice', lambda: next(it))
        p.start()
        self.addCleanup(p.stop)


class BetTest(_Base):
    def test_bet_does_not_charge(self):
        """베팅만 접수한다. **차감하면 안 된다** — 판이 사라졌을 때 돈만 잃는다."""
        sheets = FakeSheets(balance=100)
        resp = self._cmd(sheets).execute(_ctx('크랩스', '3'))
        self.assertTrue(resp.is_successful(), resp.message)
        self.assertEqual(sheets.balance, 100, "베팅 시점에 차감되면 안 된다")
        self.assertEqual(sheets.batch_calls, [])

    def test_bet_message(self):
        resp = self._cmd(FakeSheets()).execute(_ctx('크랩스', '3'))
        self.assertIn('크랩스 - 베팅액 3달러', resp.message)
        self.assertIn('[주사위 굴림]', resp.message)

    def test_missing_bet_shows_usage(self):
        resp = self._cmd(FakeSheets()).execute(_ctx('크랩스'))
        self.assertFalse(resp.is_successful())
        self.assertIn('베팅할 달러를 입력하세요', resp.message)

    def test_bet_range(self):
        for bet, ok in (('0', False), ('1', True), ('100', True), ('101', False)):
            cc.set_session_manager(cc._CrapsSessionManager(store=JsonStore(
                os.path.join(self.dir, f'{bet}.json'))))
            resp = self._cmd(FakeSheets()).execute(_ctx('크랩스', bet))
            self.assertEqual(resp.is_successful(), ok, f"베팅 {bet}")

    def test_insufficient_balance(self):
        resp = self._cmd(FakeSheets(balance=2)).execute(_ctx('크랩스', '3'))
        self.assertFalse(resp.is_successful())
        self.assertIn('부족', resp.message)

    def test_rebet_allowed_before_rolling(self):
        """굴리기 전이면 베팅을 고칠 수 있다(QA §2).

        아직 아무 정보도 얻지 않았으니 안전하다. 막아 두면 금액을 잘못 넣은 러너가
        그날 크랩스를 못 한다(취소 수단이 없었다).
        """
        cmd = self._cmd(FakeSheets())
        cmd.execute(_ctx('크랩스', '3'))
        resp = cmd.execute(_ctx('크랩스', '5'))
        self.assertTrue(resp.is_successful(), resp.message)
        self.assertTrue(resp.data['replaced'])
        self.assertEqual(cc.get_session_manager().get('u1')['bet'], 5)
        self.assertIn('베팅을 다시 걸었습니다', resp.message)

    def test_rebet_does_not_consume_another_daily_count(self):
        """같은 판의 금액 변경이다 — 고칠 때마다 횟수를 잃으면 안 된다."""
        cmd = self._cmd(FakeSheets())
        cmd.execute(_ctx('크랩스', '3'))
        cmd.execute(_ctx('크랩스', '5'))
        cmd.execute(_ctx('크랩스', '7'))
        self.assertEqual(self.daily.call_count, 1)

    def test_rebet_rejected_after_rolling(self):
        """굴린 뒤엔 못 바꾼다 — 포인트를 보고 베팅을 고치면 부정이다."""
        self._rolls((5, 5))
        cmd = self._cmd(FakeSheets(balance=100))
        cmd.execute(_ctx('크랩스', '3'))
        cmd.execute(_ctx('주사위 굴림'))            # 포인트 10
        resp = cmd.execute(_ctx('크랩스', '100'))
        self.assertFalse(resp.is_successful())
        self.assertIn('이미 굴린 판', resp.message)
        self.assertEqual(cc.get_session_manager().get('u1')['bet'], 3, "베팅이 바뀌면 안 된다")

    def test_stale_session_from_previous_day_is_discarded(self):
        """어제 베팅만 하고 굴리지 않았다고 오늘까지 막히면 안 된다(QA §2)."""
        cmd = self._cmd(FakeSheets())
        with patch.object(cc, '_current_day', return_value=3):
            cmd.execute(_ctx('크랩스', '3'))
        self.assertEqual(cc.get_session_manager().get('u1')['day'], 3)

        with patch.object(cc, '_current_day', return_value=4):   # 날이 바뀜
            resp = cmd.execute(_ctx('크랩스', '9'))
        self.assertTrue(resp.is_successful(), resp.message)
        self.assertFalse(resp.data['replaced'], "새 판이므로 교체가 아니다")
        self.assertEqual(cc.get_session_manager().get('u1')['bet'], 9)
        self.assertEqual(self.daily.call_count, 2, "새 날의 새 판 → 횟수를 센다")

    def test_same_day_session_is_kept(self):
        cmd = self._cmd(FakeSheets())
        with patch.object(cc, '_current_day', return_value=3):
            cmd.execute(_ctx('크랩스', '3'))
            resp = cmd.execute(_ctx('크랩스', '5'))
        self.assertTrue(resp.data['replaced'], "같은 날이면 교체")

    def test_unknown_day_keeps_session(self):
        """일차를 모르면 판을 버리지 않는다 — 멀쩡한 판이 날아가면 안 된다."""
        cmd = self._cmd(FakeSheets())
        with patch.object(cc, '_current_day', return_value=None):
            cmd.execute(_ctx('크랩스', '3'))
            resp = cmd.execute(_ctx('크랩스', '5'))
        self.assertTrue(resp.data['replaced'])

    def test_daily_limit_counted_once_per_game(self):
        """굴림마다 세면 한 판에 일일 횟수가 여러 번 깎인다."""
        self._rolls((5, 5), (3, 5), (5, 5))
        cmd = self._cmd(FakeSheets(balance=100))
        cmd.execute(_ctx('크랩스', '3'))
        for _ in range(3):
            cmd.execute(_ctx('주사위 굴림'))
        self.assertEqual(self.daily.call_count, 1)
        self.assertEqual(self.daily.call_args[0][1], '오늘크랩스')

    def test_daily_limit_blocks(self):
        self.daily.return_value = False
        resp = self._cmd(FakeSheets()).execute(_ctx('크랩스', '3'))
        self.assertFalse(resp.is_successful())
        self.assertIn('오늘은', resp.message)


class ComeoutTest(_Base):
    def test_seven_wins_double(self):
        """컴아웃 7 → 순이익 +베팅×2."""
        self._rolls((3, 4))
        sheets = FakeSheets(balance=10)
        cmd = self._cmd(sheets)
        cmd.execute(_ctx('크랩스', '3'))
        resp = cmd.execute(_ctx('주사위 굴림'))
        self.assertEqual(resp.data['net'], 6)
        self.assertEqual(sheets.balance, 16)
        self.assertIn('➭ 순이익 +6달러', resp.message)
        self.assertIn('➭ 현재 소지금 16달러', resp.message)

    def test_eleven_wins_double(self):
        self._rolls((5, 6))
        sheets = FakeSheets(balance=10)
        cmd = self._cmd(sheets)
        cmd.execute(_ctx('크랩스', '3'))
        self.assertEqual(cmd.execute(_ctx('주사위 굴림')).data['net'], 6)

    def test_craps_numbers_lose(self):
        for d1, d2 in ((1, 1), (1, 2), (6, 6)):     # 2, 3, 12
            cc.set_session_manager(cc._CrapsSessionManager(store=JsonStore(
                os.path.join(self.dir, f'{d1}{d2}.json'))))
            with patch.object(cc, 'roll_dice', return_value=(d1, d2)):
                sheets = FakeSheets(balance=10)
                cmd = self._cmd(sheets)
                cmd.execute(_ctx('크랩스', '3'))
                resp = cmd.execute(_ctx('주사위 굴림'))
            self.assertEqual(resp.data['net'], -3, f"{d1}+{d2}")
            self.assertEqual(sheets.balance, 7)

    def test_point_is_set(self):
        self._rolls((5, 5))
        cmd = self._cmd(FakeSheets(balance=10))
        cmd.execute(_ctx('크랩스', '3'))
        resp = cmd.execute(_ctx('주사위 굴림'))
        self.assertEqual(resp.data['action'], 'point_set')
        self.assertEqual(resp.data['point'], 10)
        self.assertIn('포인트가 10으로 설정되었습니다', resp.message)
        self.assertIn('3회 내로', resp.message)

    def test_roll_without_bet_rejected(self):
        resp = self._cmd(FakeSheets()).execute(_ctx('주사위 굴림'))
        self.assertFalse(resp.is_successful())
        self.assertIn('진행 중인 판이 없습니다', resp.message)


class PointTest(_Base):
    def test_point_repeat_wins_single(self):
        """포인트 재현 → 순이익 +베팅×1 (컴아웃 승리의 절반)."""
        self._rolls((5, 5), (5, 5))
        sheets = FakeSheets(balance=10)
        cmd = self._cmd(sheets)
        cmd.execute(_ctx('크랩스', '3'))
        cmd.execute(_ctx('주사위 굴림'))            # 포인트 10
        resp = cmd.execute(_ctx('주사위 굴림'))     # 재현
        self.assertEqual(resp.data['net'], 3)
        self.assertEqual(sheets.balance, 13)
        self.assertIn('포인트 10 재현 성공!', resp.message)

    def test_seven_out_loses(self):
        self._rolls((5, 5), (3, 4))
        sheets = FakeSheets(balance=10)
        cmd = self._cmd(sheets)
        cmd.execute(_ctx('크랩스', '3'))
        cmd.execute(_ctx('주사위 굴림'))
        resp = cmd.execute(_ctx('주사위 굴림'))
        self.assertEqual(resp.data['net'], -3)
        self.assertIn('7이 나왔습니다', resp.message)

    def test_reroll_countdown(self):
        self._rolls((5, 5), (3, 5), (2, 3))
        cmd = self._cmd(FakeSheets(balance=10))
        cmd.execute(_ctx('크랩스', '3'))
        cmd.execute(_ctx('주사위 굴림'))
        r1 = cmd.execute(_ctx('주사위 굴림'))
        self.assertEqual(r1.data['rerolls_left'], 2)
        self.assertIn('남은 재굴림 횟수는 2회', r1.message)
        r2 = cmd.execute(_ctx('주사위 굴림'))
        self.assertEqual(r2.data['rerolls_left'], 1)

    def test_rerolls_exhausted_loses(self):
        """재굴림을 다 쓰면 패배. 무한히 굴리면 하루 제한이 무의미해진다."""
        self._rolls((5, 5), (3, 5), (2, 3), (4, 4))
        sheets = FakeSheets(balance=10)
        cmd = self._cmd(sheets)
        cmd.execute(_ctx('크랩스', '3'))
        cmd.execute(_ctx('주사위 굴림'))            # 포인트 10, 재굴림 3
        cmd.execute(_ctx('주사위 굴림'))            # 실패 → 2
        cmd.execute(_ctx('주사위 굴림'))            # 실패 → 1
        resp = cmd.execute(_ctx('주사위 굴림'))     # 실패 → 소진
        self.assertEqual(resp.data['net'], -3)
        self.assertIn('재굴림을 모두 썼습니다', resp.message)
        self.assertEqual(sheets.balance, 7)

    def test_session_cleared_after_settle(self):
        self._rolls((3, 4))
        cmd = self._cmd(FakeSheets(balance=10))
        cmd.execute(_ctx('크랩스', '3'))
        cmd.execute(_ctx('주사위 굴림'))
        self.assertIsNone(cc.get_session_manager().get('u1'))


class SettlementEdgeTest(_Base):
    def test_write_failure_keeps_session(self):
        """시트 갱신이 실패하면 판을 남긴다 — 지우면 러너가 결과를 못 받는다."""
        self._rolls((3, 4))
        sheets = FakeSheets(balance=10, write_ok=False)
        cmd = self._cmd(sheets)
        cmd.execute(_ctx('크랩스', '3'))
        resp = cmd.execute(_ctx('주사위 굴림'))
        self.assertFalse(resp.is_successful())
        self.assertIsNotNone(cc.get_session_manager().get('u1'))

    def test_balance_drained_mid_game_caps_loss(self):
        """굴리는 사이 돈이 빠져나갔으면 있는 만큼만 잃는다(음수 잔액 방지)."""
        self._rolls((5, 5), (3, 4))
        sheets = FakeSheets(balance=10)
        cmd = self._cmd(sheets)
        cmd.execute(_ctx('크랩스', '10'))
        cmd.execute(_ctx('주사위 굴림'))            # 포인트
        sheets.rows[0]['소지금'] = 4               # 그 사이 소비
        resp = cmd.execute(_ctx('주사위 굴림'))     # 7 → 패배
        self.assertEqual(resp.data['net'], -4)
        self.assertEqual(sheets.balance, 0)

    def test_user_not_found(self):
        sheets = FakeSheets()
        sheets.rows = []
        resp = self._cmd(sheets).execute(_ctx('크랩스', '3'))
        self.assertFalse(resp.is_successful())


class RestartTest(_Base):
    def test_session_survives_restart(self):
        """봇을 껐다 켜도 굴리던 판이 이어진다."""
        self._rolls((5, 5), (5, 5))
        sheets = FakeSheets(balance=10)
        self._cmd(sheets).execute(_ctx('크랩스', '3'))
        self._cmd(sheets).execute(_ctx('주사위 굴림'))       # 포인트 10

        # 재시작: 같은 파일로 매니저 재생성
        cc.set_session_manager(cc._CrapsSessionManager(store=JsonStore(self.path)))
        session = cc.get_session_manager().get('u1')
        self.assertIsNotNone(session, "재시작 후 판이 사라졌다")
        self.assertEqual(session['point'], 10)

        resp = self._cmd(sheets).execute(_ctx('주사위 굴림'))
        self.assertEqual(resp.data['net'], 3)


class DiceTest(unittest.TestCase):
    def test_roll_range(self):
        cc.set_rng(random.Random(7))
        self.addCleanup(cc.set_rng, random.SystemRandom())
        for _ in range(200):
            d1, d2 = cc.roll_dice()
            self.assertIn(d1, range(1, 7))
            self.assertIn(d2, range(1, 7))

    def test_totals_cover_2_to_12(self):
        cc.set_rng(random.Random(1))
        self.addCleanup(cc.set_rng, random.SystemRandom())
        totals = {sum(cc.roll_dice()) for _ in range(2000)}
        self.assertEqual(totals, set(range(2, 13)))


if __name__ == '__main__':
    unittest.main()

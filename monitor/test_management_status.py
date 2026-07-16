#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""신규 건강/이성 모니터링 로직 회귀 테스트.

외부 서비스(구글 시트/마스토돈)에 의존하지 않도록 워크시트와 DM 전송을 모의한다.
핵심 검증:
  - 임계값 판정(건강 50/20, 이성 50/30) 및 경계값
  - 수치 파싱(50.5 과잉발동 방지, inf/nan/공란 스킵)
  - 전송 성공 건만 발송여부 기록(원자성), 전송 실패 시 미기록(재시도)
  - 비어있는 문구는 전송/기록 보류
  - 이성 시트 읽기 실패 시에도 건강 알림은 계속
"""

import unittest
from main import AutoBot, HEALTH_MSG_1, HEALTH_MSG_2, SANITY_MSG_PREFIX


def sp(text):
    """이성 문구는 코드가 프리픽스를 붙여 전송한다."""
    return SANITY_MSG_PREFIX + text


class FakeWS:
    def __init__(self, values):
        self.values = values
        self.updates = []

    def get_all_values(self):
        return self.values

    def batch_update(self, chunk, value_input_option=None):
        self.updates.extend(chunk)


class FakeSS:
    def __init__(self, ws):
        self._ws = ws

    def worksheet(self, name):
        return self._ws


MANAGE_HEADER = ['이름', '아이디', '직군', '추적', '조사', '소지금', '출석', '소지품', '건강', '이성']
SANITY_HEADER = ['이름', '이성문구1', '문구1 발송여부', '이성문구2', '문구2 발송여부']


def m_row(name, uid, hp, sanity):
    r = [''] * 10
    r[0], r[1], r[8], r[9] = name, uid, str(hp), str(sanity)
    return r


class MonitorLogicTests(unittest.TestCase):
    def _make_bot(self, manage_rows, sanity_rows, fail_ids=frozenset(), sanity_read_error=False):
        manage = [MANAGE_HEADER, ['.'] * 10] + manage_rows
        sanity = [SANITY_HEADER, ['.'] * 5] + sanity_rows
        self.manage_ws = FakeWS(manage)
        self.sanity_ws = FakeWS(sanity)

        bot = object.__new__(AutoBot)
        bot.manage_ss = FakeSS(self.manage_ws)

        if sanity_read_error:
            class BrokenSS:
                def worksheet(self, name):
                    raise RuntimeError("permission denied (simulated)")
            bot.sanity_ss = BrokenSS()
        else:
            bot.sanity_ss = FakeSS(self.sanity_ws)

        bot.management_status_cache = {}
        bot._status_cache_dirty = False
        bot._sheets_api_call = lambda op, *a, **k: op(*a, **k)
        bot._save_status_cache = lambda: None

        self.sent = []

        def fake_send(uid, msg):
            if uid in fail_ids:
                return False
            self.sent.append((uid, msg))
            return True

        bot._send_dm = fake_send
        return bot

    def _sent_to(self, uid):
        return [msg for u, msg in self.sent if u == uid]

    # ---- 건강 경계값 ----
    def test_health_boundaries(self):
        bot = self._make_bot(
            [m_row('A', 'a', 51, 100), m_row('B', 'b', 50, 100),
             m_row('C', 'c', 21, 100), m_row('D', 'd', 20, 100)],
            [])
        bot._check_and_notify()
        self.assertEqual(self._sent_to('a'), [])                       # 51 → 없음
        self.assertEqual(self._sent_to('b'), [HEALTH_MSG_1])           # 50 → 문구1
        self.assertEqual(self._sent_to('c'), [HEALTH_MSG_1])           # 21 → 문구1
        self.assertEqual(sorted(self._sent_to('d')), sorted([HEALTH_MSG_1, HEALTH_MSG_2]))  # 20 → 둘 다

    # ---- 이성 경계값 + 발송여부 기록 ----
    def test_sanity_boundaries_and_flags(self):
        bot = self._make_bot(
            [m_row('빌', 'bill', 100, 30), m_row('모린', 'maureen', 100, 50),
             m_row('짐', 'jim', 100, 51)],
            [['빌', 'B1', '', 'B2', ''], ['모린', 'M1', '', 'M2', ''], ['짐', 'J1', '', 'J2', '']])
        bot._check_and_notify()
        self.assertEqual(sorted(self._sent_to('bill')), sorted([sp('B1'), sp('B2')]))  # 30 → 둘 다
        self.assertEqual(self._sent_to('maureen'), [sp('M1')])                 # 50 → 문구1만
        self.assertEqual(self._sent_to('jim'), [])                            # 51 → 없음
        ranges = {u['range'] for u in self.sanity_ws.updates}
        # 빌=시트 3행(C3,E3), 모린=4행(C4)
        self.assertEqual(ranges, {'C3', 'E3', 'C4'})

    # ---- 과잉발동 방지 / 비유한 수치 ----
    def test_number_parsing(self):
        bot = self._make_bot(
            [m_row('x', 'x', '50.5', '50.5'), m_row('y', 'y', 'inf', 'inf'),
             m_row('z', 'z', '', 'abc')],
            [['x', 'X1', '', 'X2', '']])
        bot._check_and_notify()  # 크래시 없이 완료돼야 함
        self.assertEqual(self.sent, [])  # 50.5>50, inf/공란/abc → 아무것도 발송 안 함

    # ---- 원자성: 전송 실패 시 발송여부 미기록 ----
    def test_atomicity_on_send_failure(self):
        bot = self._make_bot(
            [m_row('빌', 'bill', 40, 40), m_row('데보라', 'deborah', 20, 100)],
            [['빌', 'B1', '', 'B2', '']],
            fail_ids={'bill'})  # 빌 전송 실패
        bot._check_and_notify()
        # 빌: 전송 실패 → 시트 발송여부 미기록 + 건강 캐시 미기록
        self.assertEqual(self.sanity_ws.updates, [])
        self.assertNotIn('bill', bot.management_status_cache)
        # 데보라: 전송 성공 → 건강 캐시 기록
        self.assertTrue(bot.management_status_cache['deborah'].get('health50_sent'))
        self.assertTrue(bot.management_status_cache['deborah'].get('health20_sent'))

    # ---- 빈 문구는 전송/기록 보류 ----
    def test_empty_message_deferred(self):
        bot = self._make_bot(
            [m_row('빌', 'bill', 100, 40)],
            [['빌', '', '', '', '']])  # 문구1 공란
        bot._check_and_notify()
        self.assertEqual(self.sent, [])
        self.assertEqual(self.sanity_ws.updates, [])

    # ---- 이성 시트 장애 시에도 건강 알림은 계속 ----
    def test_sanity_read_failure_isolated(self):
        bot = self._make_bot(
            [m_row('빌', 'bill', 40, 40)],
            [],
            sanity_read_error=True)
        bot._check_and_notify()
        self.assertEqual(self._sent_to('bill'), [HEALTH_MSG_1])  # 건강은 발송됨

    # ---- 이미 O면 재발송 안 함 ----
    def test_already_marked_no_resend(self):
        bot = self._make_bot(
            [m_row('빌', 'bill', 100, 25)],
            [['빌', 'B1', 'O', 'B2', '']])  # 문구1 이미 발송됨
        bot._check_and_notify()
        self.assertEqual(self._sent_to('bill'), [sp('B2')])  # 문구2만


class NumberParserTests(unittest.TestCase):
    def test_parse_number(self):
        f = AutoBot._parse_number
        self.assertEqual(f('100'), 100.0)
        self.assertEqual(f('0'), 0.0)
        self.assertEqual(f('50.5'), 50.5)
        self.assertEqual(f('1,000'), 1000.0)
        self.assertIsNone(f(''))
        self.assertIsNone(f('abc'))
        self.assertIsNone(f('inf'))
        self.assertIsNone(f('nan'))
        self.assertIsNone(f(None))
        self.assertIsNone(f(True))  # bool 방어


if __name__ == "__main__":
    unittest.main()

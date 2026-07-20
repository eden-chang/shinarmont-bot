"""commands/store/attendance_command.py 스모크 테스트.

[출석] 검증 — **하루 1회 판정의 기준은 '관리' 시트 출석 컬럼**:
- 빈칸 → 출석 가능. 소지금 +ATTENDANCE_AMOUNT, 출석 컬럼 'O' 기록(한 번의 batch_update).
- 비어 있지 않음 → 이미 출석 → 실패 응답, 소지금 변동 없음.
- 출석 컬럼이 없으면 판정이 불가능하므로 **거절(fail closed)** — 무한 지급 방지.
- 운세 문구는 '운세' 시트 '문구' 열에서 선택.

봇 JSON('오늘출석')은 더 이상 출석 판정에 쓰지 않는다(시트가 단일 기준).
시트/캐시는 페이크 주입.
"""

import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from config.settings import config
from utils.cache_manager import bot_cache
from commands.store.attendance_command import AttendanceCommand


class FakeWorksheet:
    def __init__(self, headers):
        self._headers = headers

    def row_values(self, n):
        return self._headers


class FakeSheets:
    def __init__(self, with_attendance_column=True):
        self.headers = ['아이디', '이름', '소지금', '소지품', '건강', '이성']
        if with_attendance_column:
            self.headers.append('출석')  # 빈칸 = 오늘 출석 안 함

        # 실제 get_worksheet_data는 dict(zip(header, row))라 **모든 헤더 키**를 갖는다.
        # 일부 키를 빠뜨리면 행 키 순서에서 산출하는 열 번호가 헤더와 어긋난다.
        # 데이터는 3행부터(1행 헤더/2행 설명).
        self.mgmt = []
        for i, (uid, name) in enumerate([('u1', '앨리스'), ('u2', '밥')]):
            values = {'아이디': uid, '이름': name, '소지금': '10'}
            row = {h: values.get(h, '') for h in self.headers}
            row['_row_number'] = 3 + i
            self.mgmt.append(row)
        self.roster = [{'아이디': r['아이디'], '이름': r['이름']} for r in self.mgmt]
        self.fortunes = [{'문구': '좋은 일이 생깁니다'}]
        self.batch_calls = []

    def get_worksheet(self, name):
        return FakeWorksheet(self.headers)

    def get_worksheet_data(self, name, use_cache=True):
        if name == '관리':
            return [dict(r) for r in self.mgmt]
        if name == '운세':
            return list(self.fortunes)
        return []

    def get_roster_data(self, use_cache=True):
        return list(self.roster)

    def get_item_data(self):
        return []

    def batch_update_cells(self, name, updates):
        self.batch_calls.append((name, list(updates)))
        for (row_number, col, val) in updates:
            # 행 번호로 대상 행을 찾고, 열 번호(1-based)는 헤더명으로 되돌려 반영
            for row in self.mgmt:
                if row['_row_number'] == row_number and 1 <= col <= len(self.headers):
                    row[self.headers[col - 1]] = val
        return True

    def col_of(self, header):
        """1-based 열 번호 (테스트 헬퍼)."""
        return self.headers.index(header) + 1


def _ctx(user_id='u1', user_name='앨리스'):
    return SimpleNamespace(user_id=user_id, user_name=user_name, keywords=['출석'])


class AttendanceCommandTest(unittest.TestCase):
    def setUp(self):
        # config 격리
        self._orig_enabled = getattr(config, 'ATTENDANCE_ENABLED', False)
        self._orig_amount = getattr(config, 'ATTENDANCE_AMOUNT', 3)
        config.ATTENDANCE_ENABLED = True
        config.ATTENDANCE_AMOUNT = 3

        # 운세 캐시 비우기(시트 로드 경로 강제)
        try:
            bot_cache.cache_fortune_phrases([], ttl_seconds=1)
            bot_cache.command_cache.delete("fortune_phrases_with_ttl")
        except Exception:
            pass

        # 화폐 단위를 **테스트가 고정한다**. `.env`의 CURRENCY(달러)에 기대면
        # .env가 없는 환경에서는 기본값 '포인트'가 나와 빨개진다(실제로 겪었다).
        self._orig_currency = getattr(config, 'CURRENCY', '포인트')
        config.CURRENCY = '달러'

        self.sheets = FakeSheets()
        self.cmd = AttendanceCommand(sheets_manager=self.sheets)

    def tearDown(self):
        config.ATTENDANCE_ENABLED = self._orig_enabled
        config.ATTENDANCE_AMOUNT = self._orig_amount
        config.CURRENCY = self._orig_currency

    def test_first_attendance_grants_fortune_and_money(self):
        resp = self.cmd.execute(_ctx())
        self.assertTrue(resp.success, resp.message)
        self.assertIn('좋은 일이 생깁니다', resp.message)
        self.assertIn('3달러 획득', resp.message)
        # 소지금 10 -> 13
        self.assertEqual(int(self.sheets.mgmt[0]['소지금']), 13)

    def test_blank_column_becomes_O(self):
        """빈칸이던 '관리' 출석 컬럼이 'O'(알파벳)가 된다."""
        self.assertEqual(self.sheets.mgmt[0]['출석'], '')
        resp = self.cmd.execute(_ctx())
        self.assertTrue(resp.success, resp.message)
        self.assertEqual(self.sheets.mgmt[0]['출석'], 'O')

    def test_mark_is_latin_letter_not_digit_zero(self):
        """숫자 0이 아니라 알파벳 대문자 O (U+004F)."""
        self.cmd.execute(_ctx())
        mark = self.sheets.mgmt[0]['출석']
        self.assertEqual(ord(mark), 0x4F)

    def test_money_and_mark_written_in_single_batch(self):
        """소지금과 출석 기록이 어긋나지 않도록 한 번의 배치로 쓴다."""
        self.cmd.execute(_ctx())
        self.assertEqual(len(self.sheets.batch_calls), 1)
        _, updates = self.sheets.batch_calls[0]
        self.assertEqual(len(updates), 2)
        by_col = {col: val for (_row, col, val) in updates}
        self.assertEqual(by_col[self.sheets.col_of('소지금')], 13)
        self.assertEqual(by_col[self.sheets.col_of('출석')], 'O')

    def test_missing_attendance_column_refuses(self):
        """컬럼이 곧 제한이다. 없으면 거절해야 한다 — 없으면 무한 지급이 된다."""
        sheets = FakeSheets(with_attendance_column=False)
        cmd = AttendanceCommand(sheets_manager=sheets)
        resp = cmd.execute(_ctx())
        self.assertFalse(resp.success)
        self.assertEqual(int(sheets.mgmt[0]['소지금']), 10)  # 지급 없음
        self.assertEqual(sheets.batch_calls, [])

    def test_write_failure_leaves_nothing_written(self):
        """쓰기가 실패하면 소지금도 출석 칸도 그대로라 재시도할 수 있다."""
        self.sheets.batch_update_cells = lambda name, updates: False
        resp = self.cmd.execute(_ctx())
        self.assertFalse(resp.success)
        self.assertEqual(int(self.sheets.mgmt[0]['소지금']), 10)
        self.assertEqual(self.sheets.mgmt[0]['출석'], '')

    def test_second_attendance_blocked(self):
        first = self.cmd.execute(_ctx())
        self.assertTrue(first.success)
        second = self.cmd.execute(_ctx())
        self.assertFalse(second.success)
        self.assertIn('한 번', second.message)
        # 소지금은 그대로(13), batch_update 추가 호출 없음
        self.assertEqual(int(self.sheets.mgmt[0]['소지금']), 13)
        self.assertEqual(len(self.sheets.batch_calls), 1)

    def test_preexisting_mark_blocks_without_any_write(self):
        """이미 'O'인 칸은 봇이 기록한 적 없어도 출석한 것으로 본다(GM 수동 기입 포함)."""
        self.sheets.mgmt[0]['출석'] = 'O'
        resp = self.cmd.execute(_ctx())
        self.assertFalse(resp.success)
        self.assertIn('한 번', resp.message)
        self.assertEqual(self.sheets.batch_calls, [])

    def test_gm_clearing_mark_allows_reattendance(self):
        """시트가 판정 기준이므로 GM이 칸을 지우면 그날 다시 출석할 수 있다."""
        self.assertTrue(self.cmd.execute(_ctx()).success)
        self.sheets.mgmt[0]['출석'] = ''  # GM이 손으로 지움 = 출석 취소
        second = self.cmd.execute(_ctx())
        self.assertTrue(second.success, second.message)
        self.assertEqual(int(self.sheets.mgmt[0]['소지금']), 16)
        self.assertEqual(self.sheets.mgmt[0]['출석'], 'O')

    def test_non_blank_variants_count_as_attended(self):
        """'빈칸 = 출석 안 함'이므로 비어 있지 않으면 전부 출석으로 본다."""
        for value in ('O', 'o', 'ㅇ', 'v', ' O '):
            with self.subTest(value=value):
                sheets = FakeSheets()
                sheets.mgmt[0]['출석'] = value
                cmd = AttendanceCommand(sheets_manager=sheets)
                resp = cmd.execute(_ctx())
                self.assertFalse(resp.success, f"{value!r}는 출석한 것으로 처리돼야 함")

    def test_whitespace_only_counts_as_blank(self):
        """공백만 있는 칸은 빈칸으로 본다."""
        self.sheets.mgmt[0]['출석'] = '   '
        resp = self.cmd.execute(_ctx())
        self.assertTrue(resp.success, resp.message)
        self.assertEqual(self.sheets.mgmt[0]['출석'], 'O')

    def test_other_user_unaffected(self):
        """한 사람의 출석이 다른 사람의 칸을 건드리지 않는다."""
        self.cmd.execute(_ctx(user_id='u1'))
        self.assertEqual(self.sheets.mgmt[1]['출석'], '')
        self.assertEqual(int(self.sheets.mgmt[1]['소지금']), 10)
        resp = self.cmd.execute(_ctx(user_id='u2', user_name='밥'))
        self.assertTrue(resp.success, resp.message)
        self.assertEqual(self.sheets.mgmt[1]['출석'], 'O')

    def test_disabled_returns_error(self):
        config.ATTENDANCE_ENABLED = False
        resp = self.cmd.execute(_ctx())
        self.assertFalse(resp.success)

    def test_fortune_fallback_when_sheet_empty(self):
        """'운세' 시트가 비면 폴백 문구로 대체하되, 출석·지급은 정상 진행된다.

        메시지 형식은 `{운세}\\n➭ N달러 획득` — '오늘의 운세' 같은 머리표는 없다.
        """
        from commands.store.attendance_command import _FALLBACK_FORTUNES
        self.sheets.fortunes = []
        resp = self.cmd.execute(_ctx())
        self.assertTrue(resp.success, resp.message)
        self.assertTrue(any(f in resp.message for f in _FALLBACK_FORTUNES),
                        f"폴백 운세 문구가 없다: {resp.message!r}")
        self.assertIn('3달러 획득', resp.message)


if __name__ == '__main__':
    unittest.main()

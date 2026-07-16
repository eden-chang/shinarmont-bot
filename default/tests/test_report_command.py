"""commands/shinarmont/report_command.py 단위 테스트 (페이크 매니저).

계약(2026-07-16 개편):
  - `[결과 보고/키워드]` — 키워드로 지령을 찾는다.
  - `부탁지령기록` 시트는 **폐지**됐다. 진행 상태·중복 방지를 모두
    `부탁지령`의 '상태' 열이 담당한다(빈칸/전송됨/완료됨/실패).
  - 완료 시 그 행에 상태='완료됨' + 완료 일차 + 완료 시각 + 완료 내용을 쓴다.
  - **상태를 먼저 쓰고 그 다음 지급한다** — 순서가 뒤집히면 재보고로 이중 지급된다.
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from commands.base_command import CommandContext
from commands.shinarmont.report_command import ReportCommand

# 실측한 '부탁지령' 열 순서(2026-07-16). 열 번호 계산이 여기에 걸려 있다.
DIRECTIVE_HEADER = [
    '일차', '대상', '키워드', '내용', '경중', '보상',
    '상태', '완료 일차', '완료 시각', '완료 내용',
]
COL = {name: i + 1 for i, name in enumerate(DIRECTIVE_HEADER)}


def _directive(row_number, day, target, keyword, reward, status, severity='3'):
    """실제 시트와 같은 열 순서·구성의 행 하나."""
    return {
        '일차': str(day), '대상': target, '키워드': keyword,
        '내용': f'{keyword} 지령 내용', '경중': severity, '보상': str(reward),
        '상태': status, '완료 일차': '', '완료 시각': '', '완료 내용': '',
        '_row_number': row_number,
    }


class FakeBaseSheets:
    """명단/관리 담당 기본 시트."""

    def __init__(self, balance=100):
        self.management = [
            {'이름': '홍길동', '아이디': 'gildong', '소지금': balance, '_row_number': 3},
        ]
        self.roster = [{'이름': '홍길동', '아이디': 'gildong', '은는': '은'}]
        self.batch_calls = []

    def get_worksheet_data(self, name, use_cache=True):
        if name == '관리':
            return [dict(r) for r in self.management]
        return []

    def get_roster_data(self, use_cache=True):
        return [dict(r) for r in self.roster]

    def batch_update_cells(self, name, updates):
        self.batch_calls.append((name, updates))
        for row, col, value in updates:
            for r in self.management:
                if r['_row_number'] == row and col == 3:   # 이름,아이디,소지금 → 3
                    r['소지금'] = value
        return True


class FakeSystemSheets:
    """시스템 시트(부탁지령/행동로그). `부탁지령기록`은 더 이상 존재하지 않는다."""

    def __init__(self, directives=None, write_ok=True):
        self.directives = directives if directives is not None else []
        self.batch_calls = []
        self.appended = []
        self.write_ok = write_ok

    def get_worksheet_data(self, name, use_cache=True):
        if name == '부탁지령':
            return [dict(r) for r in self.directives]
        return []

    def batch_update_cells(self, name, updates):
        self.batch_calls.append((name, updates))
        if not self.write_ok:
            return False
        for row, col, value in updates:
            for d in self.directives:
                if d['_row_number'] == row:
                    d[DIRECTIVE_HEADER[col - 1]] = value
        return True

    def append_row(self, name, values):
        self.appended.append((name, values))
        return True

    def update_cell(self, name, row, col, value):
        return True


def _ctx(keyword='까마귀', body='보고 내용: 처리했습니다.'):
    text = f"[결과 보고/{keyword}]" if keyword else "[결과 보고]"
    if body:
        text += "\n" + body
    return CommandContext(
        user_id='gildong',
        user_name='홍길동',
        original_text=text,
        keywords=['결과 보고', keyword] if keyword else ['결과 보고'],
        metadata={'visibility': 'direct'},
    )


class ReportCommandTest(unittest.TestCase):
    def setUp(self):
        self.base = FakeBaseSheets()
        patchers = [
            patch('commands.shinarmont.report_command.game_day.current_day', return_value=5),
            patch('commands.shinarmont.report_command.action_log.append', return_value=True),
            patch('commands.shinarmont.report_command.load_user_data',
                  return_value=[{'이름': '홍길동', '아이디': 'gildong'}]),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)

    def _cmd(self, directives, write_ok=True):
        system = FakeSystemSheets(directives, write_ok=write_ok)
        cmd = ReportCommand(sheets_manager=self.base, api=None,
                            system_sheets_manager=system)
        return cmd, system

    # ── 정상 경로 ────────────────────────────────────────────────
    def test_reports_and_pays(self):
        cmd, _system = self._cmd([_directive(3, 1, '홍길동', '까마귀', 50, '전송됨')])

        resp = cmd.execute(_ctx('까마귀', '보고 내용: 까마귀를 쫓아냈습니다.'))

        self.assertTrue(resp.success, resp.message)
        self.assertEqual(resp.data['reward_paid'], 50)
        self.assertEqual(self.base.management[0]['소지금'], 150)

    def test_writes_status_day_time_and_body(self):
        """완료 4종을 그 행에 쓴다."""
        cmd, system = self._cmd([_directive(3, 1, '홍길동', '까마귀', 50, '전송됨')])

        cmd.execute(_ctx('까마귀', '보고 내용: 광장에서 쫓아냈음.'))

        name, updates = system.batch_calls[0]
        self.assertEqual(name, '부탁지령')
        written = {col: val for _row, col, val in updates}
        self.assertEqual(written[COL['상태']], '완료됨')
        self.assertEqual(written[COL['완료 일차']], 5)
        self.assertEqual(written[COL['완료 내용']], '광장에서 쫓아냈음.')
        self.assertIn(COL['완료 시각'], written)
        self.assertTrue(all(r == 3 for r, _c, _v in updates), "지령 행에만 써야 한다.")

    def test_body_prefix_stripped(self):
        """'보고 내용:' 접두사는 빼고 뒤 문장만 기록한다."""
        cmd, system = self._cmd([_directive(3, 1, '홍길동', '까마귀', 50, '전송됨')])
        cmd.execute(_ctx('까마귀', "보고 내용: 검은 옷의 남자에게 '집에 도둑이 들었다'고 말했음."))
        written = {c: v for _r, c, v in system.batch_calls[0][1]}
        self.assertEqual(written[COL['완료 내용']],
                         "검은 옷의 남자에게 '집에 도둑이 들었다'고 말했음.")

    def test_body_without_prefix_is_kept_whole(self):
        """접두사를 안 붙였다고 보고를 거절하지 않는다."""
        cmd, system = self._cmd([_directive(3, 1, '홍길동', '까마귀', 50, '전송됨')])
        cmd.execute(_ctx('까마귀', '그냥 이렇게 적었습니다.'))
        written = {c: v for _r, c, v in system.batch_calls[0][1]}
        self.assertEqual(written[COL['완료 내용']], '그냥 이렇게 적었습니다.')

    def test_empty_body_still_reports(self):
        """내용이 없어도 보고 자체는 성립한다(완료 내용만 빈다)."""
        cmd, system = self._cmd([_directive(3, 1, '홍길동', '까마귀', 50, '전송됨')])
        resp = cmd.execute(_ctx('까마귀', ''))
        self.assertTrue(resp.success, resp.message)
        written = {c: v for _r, c, v in system.batch_calls[0][1]}
        self.assertEqual(written[COL['상태']], '완료됨')
        self.assertNotIn(COL['완료 내용'], written)

    # ── 키워드 매칭 ──────────────────────────────────────────────
    def test_keyword_required(self):
        cmd, _ = self._cmd([_directive(3, 1, '홍길동', '까마귀', 50, '전송됨')])
        resp = cmd.execute(_ctx(keyword=''))
        self.assertFalse(resp.success)
        self.assertIn('키워드', resp.message)

    def test_unknown_keyword_rejected(self):
        cmd, system = self._cmd([_directive(3, 1, '홍길동', '까마귀', 50, '전송됨')])
        resp = cmd.execute(_ctx('없는키워드'))
        self.assertFalse(resp.success)
        self.assertEqual(system.batch_calls, [], "매칭 실패면 아무것도 쓰면 안 된다.")

    def test_keyword_whitespace_and_case_insensitive(self):
        cmd, _ = self._cmd([_directive(3, 1, '홍길동', 'Crow', 50, '전송됨')])
        resp = cmd.execute(_ctx(' crow '))
        self.assertTrue(resp.success, resp.message)

    def test_other_persons_directive_not_matched(self):
        """남에게 간 지령은 키워드를 알아도 보고할 수 없다."""
        cmd, _ = self._cmd([_directive(3, 1, '다른사람', '까마귀', 50, '전송됨')])
        resp = cmd.execute(_ctx('까마귀'))
        self.assertFalse(resp.success)
        self.assertEqual(self.base.management[0]['소지금'], 100)

    # ── 상태별 거절 ──────────────────────────────────────────────
    def test_unsent_directive_rejected(self):
        """상태 빈칸 = 아직 전송 전 → 보고 불가."""
        cmd, system = self._cmd([_directive(3, 1, '홍길동', '까마귀', 50, '')])
        resp = cmd.execute(_ctx('까마귀'))
        self.assertFalse(resp.success)
        self.assertIn('전달되지 않', resp.message)
        self.assertEqual(system.batch_calls, [])

    def test_completed_directive_rejected(self):
        """이중 지급 차단 — 상태 열이 곧 중복 방지 마커다."""
        cmd, _ = self._cmd([_directive(3, 1, '홍길동', '까마귀', 50, '완료됨')])
        resp = cmd.execute(_ctx('까마귀'))
        self.assertFalse(resp.success)
        self.assertIn('이미', resp.message)
        self.assertEqual(self.base.management[0]['소지금'], 100, "재지급되면 안 된다.")

    def test_failed_directive_rejected(self):
        cmd, _ = self._cmd([_directive(3, 1, '홍길동', '까마귀', 50, '실패')])
        resp = cmd.execute(_ctx('까마귀'))
        self.assertFalse(resp.success)
        self.assertIn('기한', resp.message)

    def test_double_report_blocked_end_to_end(self):
        """같은 지령을 두 번 보고해도 보상은 한 번만."""
        cmd, _system = self._cmd([_directive(3, 1, '홍길동', '까마귀', 50, '전송됨')])

        first = cmd.execute(_ctx('까마귀'))
        second = cmd.execute(_ctx('까마귀'))

        self.assertTrue(first.success)
        self.assertFalse(second.success, "두 번째 보고는 거절돼야 한다.")
        self.assertEqual(self.base.management[0]['소지금'], 150, "50만 지급")

    # ── 쓰기 실패 시 지급 보류 ────────────────────────────────────
    def test_status_write_failure_blocks_payment(self):
        """상태를 못 남기면 지급하지 않는다.

        지급부터 하면, 상태가 안 남아 재보고 시 또 지급된다(이중 지급).
        """
        cmd, _ = self._cmd([_directive(3, 1, '홍길동', '까마귀', 50, '전송됨')], write_ok=False)

        resp = cmd.execute(_ctx('까마귀'))

        self.assertFalse(resp.success)
        self.assertEqual(self.base.management[0]['소지금'], 100, "지급되면 안 된다.")

    def test_missing_status_column_rejects_report(self):
        """'상태' 열이 없으면 중복 방지 마커를 남길 수 없다 → 보고 거부.

        받아 버리면 재보고로 보상을 무한히 탈 수 있다.
        """
        d = _directive(3, 1, '홍길동', '까마귀', 50, '전송됨')
        del d['상태']
        cmd, _ = self._cmd([d])

        resp = cmd.execute(_ctx('까마귀'))

        self.assertFalse(resp.success)
        self.assertEqual(self.base.management[0]['소지금'], 100)

    # ── 보상 ─────────────────────────────────────────────────────
    def test_reward_from_column(self):
        cmd, _ = self._cmd([_directive(3, 1, '홍길동', '알약', 30, '전송됨')])
        resp = cmd.execute(_ctx('알약'))
        self.assertEqual(resp.data['reward_paid'], 30)
        self.assertEqual(self.base.management[0]['소지금'], 130)

    def test_severity_fallback_when_reward_empty(self):
        d = _directive(3, 1, '홍길동', '까마귀', '', '전송됨', severity='중')
        cmd, _ = self._cmd([d])
        resp = cmd.execute(_ctx('까마귀'))
        self.assertEqual(resp.data['reward_paid'], 50)   # DEFAULT_SEVERITY_REWARD['중']

    def test_latest_row_wins_for_duplicate_keyword(self):
        """같은 키워드가 여러 건 열려 있으면 아래 행(최신)."""
        cmd, system = self._cmd([
            _directive(3, 1, '홍길동', '까마귀', 50, '전송됨'),
            _directive(7, 4, '홍길동', '까마귀', 80, '전송됨'),
        ])
        resp = cmd.execute(_ctx('까마귀'))
        self.assertEqual(resp.data['reward_paid'], 80)
        self.assertEqual(system.batch_calls[0][1][0][0], 7)

    # ── 응답 ─────────────────────────────────────────────────────
    def test_response_format(self):
        cmd, _ = self._cmd([_directive(3, 1, '홍길동', '까마귀', 50, '전송됨')])
        resp = cmd.execute(_ctx('까마귀'))
        self.assertIn('정상적으로 접수되었습니다.', resp.message)
        self.assertIn('➭', resp.message)
        self.assertNotIn('까마귀 지령 내용', resp.message, "지령문을 되읊지 않는다.")


if __name__ == '__main__':
    unittest.main()

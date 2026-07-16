"""commands/shinarmont/infirmary_command.py 스모크 테스트 (페이크 시트/AI 주입).

- DM 게이트, 시작 무회복, 의사 자기-종료, 최소/최대 발화, 종료 시 처치(관리 반영)·클램프·규칙 폴백,
  다중 타래(새 방문이 이전 세션을 종료하지 않음, 자정 넘겨 이어가도 시작일로 카운트), [대화 끝내기],
  플레인텍스트, AI 대사 폴백을 검증.
- 이어가기는 핸들러가 실어주는 metadata['reply_thread'](session_key)로 라우팅된다(테스트는 이를 흉내낸다).
"""

import os
import shutil
import tempfile
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from commands.base_command import CommandContext
from commands.shinarmont import infirmary_command as inf
from commands.shinarmont.infirmary_command import (
    InfirmaryCommand,
    EndDoctorTalkCommand,
    get_doctor_session,
)
from utils import reply_threads
from utils.json_store import JsonStore


def _reply(text='네.', concluding=False):
    """doctor_reply 구조화 반환값."""
    return {'reply': text, 'concluding': concluding}


def _mgmt_manager(health=50, sanity=40, job=''):
    row = {
        '이름': '엘리스', '아이디': 'alice',
        '건강': str(health), '이성': str(sanity),
        '_row_number': 3,
    }
    if job:
        row['직군'] = job
    m = MagicMock()
    m.get_worksheet_data.return_value = [row]
    m.batch_update_cells.return_value = True
    return m


def _ctx(visibility='direct', text='[의무실 방문] 머리가 아파요', user='alice'):
    """새 방문(top-level) 컨텍스트."""
    return CommandContext(
        user_id=user,
        user_name='엘리스',
        original_text=text,
        keywords=['의무실 방문'],
        metadata={'visibility': visibility},
    )


def _reply_ctx(session_id, text='계속', user='alice', visibility='direct'):
    """답글-스레드 이어가기 컨텍스트(핸들러가 reply_thread를 실어준 상태를 흉내)."""
    return CommandContext(
        user_id=user,
        user_name='엘리스',
        original_text=f'[의무실] {text}',
        keywords=['의무실'],
        metadata={
            'visibility': visibility,
            'reply_thread': {'keyword': '의무실', 'user_id': user, 'session_key': session_id},
        },
    )


def _end_ctx(session_id=None, user='alice', visibility='direct'):
    md = {'visibility': visibility}
    if session_id:
        md['reply_thread'] = {'keyword': '의무실', 'user_id': user, 'session_key': session_id}
    return CommandContext(
        user_id=user, user_name='엘리스',
        original_text='[대화 끝내기]', keywords=['대화 끝내기'], metadata=md,
    )


class InfirmaryTest(unittest.TestCase):
    def setUp(self):
        # 세션·답글 레지스트리가 디스크 영속이라 임시 파일로 격리한다.
        self._tmpdir = tempfile.mkdtemp()
        inf._session_manager = inf._DoctorSessionManager(
            store=JsonStore(os.path.join(self._tmpdir, 'doctor_sessions.json'))
        )
        reply_threads.set_registry(reply_threads._ReplyThreadRegistry(
            store=JsonStore(os.path.join(self._tmpdir, 'reply_threads.json'))
        ))
        self.p_day = patch.object(inf.game_day, 'current_day', return_value=5)
        self.p_log = patch.object(inf.action_log, 'append', return_value=True)
        self.p_recent = patch.object(inf.action_log, 'recent', return_value=[])
        self.p_sanity = patch.object(inf.stat_gate, 'apply_sanity_messages', return_value=None)
        self.p_cache = patch.object(inf, 'invalidate_user_cache', return_value=True)
        self.p_heal = patch.object(inf, 'evaluate_amount', return_value=(5, ''))
        for p in (self.p_day, self.p_log, self.p_recent, self.p_sanity, self.p_cache, self.p_heal):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(shutil.rmtree, self._tmpdir, True)
        self.addCleanup(reply_threads.set_registry, None)
        self.addCleanup(setattr, inf, '_session_manager', None)

    def _cmd(self, sheets=None):
        return InfirmaryCommand(
            sheets_manager=sheets or _mgmt_manager(),
            api=MagicMock(),
            system_sheets_manager=MagicMock(),
        )

    def _end_cmd(self):
        return EndDoctorTalkCommand(
            sheets_manager=MagicMock(), api=MagicMock(), system_sheets_manager=MagicMock(),
        )

    def _sid(self, user='alice'):
        s = get_doctor_session().latest_for_user(user)
        return s['id'] if s else None

    @staticmethod
    def _all_updates(sheets):
        return [u for call in sheets.batch_update_cells.call_args_list for u in call[0][1]]

    # --- DM 게이트 ---
    def test_non_dm_blocked(self):
        resp = self._cmd().execute(_ctx(visibility='public'))
        self.assertFalse(resp.success)
        self.assertIn('DM', resp.message)

    # --- 세션 시작: 대화만, 회복 없음, 첫 발화에서 종료 안 함 ---
    def test_start_replies_without_heal(self):
        sheets = _mgmt_manager(health=50, sanity=40)
        cmd = self._cmd(sheets)
        with patch.object(inf, 'check_and_set', return_value=True), \
             patch.object(inf.ai_client, 'doctor_reply', return_value=_reply('어디가 아프신가요?', concluding=True)):
            resp = cmd.execute(_ctx())
        self.assertTrue(resp.success)
        self.assertIn('어디가 아프신가요?', resp.message)
        self.assertEqual(sheets.batch_update_cells.call_count, 0)
        self.assertTrue(get_doctor_session().latest_for_user('alice')['active'])

    # --- 일일 제한 초과 ---
    def test_daily_limit_blocks(self):
        with patch.object(inf, 'check_and_set', return_value=False), \
             patch.object(inf.ai_client, 'doctor_reply', return_value=_reply()):
            resp = self._cmd().execute(_ctx())
        self.assertTrue(resp.success)
        self.assertIn('이미 의무실', resp.message)

    # --- 이어가기: 종료 전에는 시트 미반영 ---
    def test_continuation_no_write_until_finalize(self):
        sheets = _mgmt_manager()
        cmd = self._cmd(sheets)
        with patch.object(inf, 'check_and_set', return_value=True), \
             patch.object(inf.ai_client, 'doctor_reply', return_value=_reply('계속 말씀하세요.', concluding=False)):
            cmd.execute(_ctx())
            sid = self._sid()
            resp2 = cmd.execute(_reply_ctx(sid, '계속 어지러워요'))
        self.assertTrue(resp2.success)
        self.assertEqual(sheets.batch_update_cells.call_count, 0)
        self.assertEqual(get_doctor_session().get(sid)['turns'], 2)

    # --- AI 대사 폴백 ---
    def test_ai_fallback_line(self):
        with patch.object(inf, 'check_and_set', return_value=True), \
             patch.object(inf.ai_client, 'doctor_reply', return_value=None):
            resp = self._cmd().execute(_ctx())
        self.assertTrue(resp.success)
        self.assertTrue(any(line in resp.message for line in inf.DOCTOR_FALLBACK_LINES))

    # --- 의사 자기-종료 → 처치 반영 ---
    def test_doctor_self_concludes_applies_treatment(self):
        sheets = _mgmt_manager(health=50, sanity=40)
        cmd = self._cmd(sheets)
        outcome = {'treatment': '안정제', 'health_delta': 0, 'sanity_delta': 6, 'reason': 'r'}
        with patch.object(inf, 'check_and_set', return_value=True), \
             patch.object(inf.ai_client, 'doctor_reply', return_value=_reply('오늘은 여기까지 하죠.', concluding=True)), \
             patch.object(inf.ai_client, 'doctor_treatment', return_value=outcome):
            cmd.execute(_ctx())
            sid = self._sid()
            self.assertEqual(sheets.batch_update_cells.call_count, 0)
            resp = cmd.execute(_reply_ctx(sid, '감사합니다'))
        self.assertTrue(resp.success)
        updates = sheets.batch_update_cells.call_args[0][1]
        self.assertIn((3, 4, 46), updates)   # 이성 40+6

        # 러셀이 스스로 마무리한 대사 + 처치 결과 한 줄. 그게 전부다.
        self.assertEqual(resp.message, '오늘은 여기까지 하죠.\n\n➭ 안정제 사용, 이성 6 회복')

        # 고정 끝인사를 덧붙이지 않는다(2026-07-16 수정7).
        # 의사가 이미 작별했는데 또 붙이면 **두 번 작별**하는 꼴이 된다.
        self.assertNotIn(inf.DOCTOR_CLOSING_LINE, resp.message)
        self.assertFalse(get_doctor_session().get(sid)['active'])

    # --- 최소 발화 전에는 종료 금지 ---
    def test_no_conclude_before_min_turns(self):
        sheets = _mgmt_manager()
        cmd = self._cmd(sheets)
        outcome = {'treatment': '안정제', 'health_delta': 0, 'sanity_delta': 6, 'reason': 'r'}
        with patch.object(inf, 'check_and_set', return_value=True), \
             patch.object(inf.ai_client, 'doctor_reply', return_value=_reply('음.', concluding=True)), \
             patch.object(inf.ai_client, 'doctor_treatment', return_value=outcome), \
             patch.object(inf.config, 'DOCTOR_TURNS_MIN', 3), \
             patch.object(inf.config, 'DOCTOR_MAX_TURNS', 8):
            cmd.execute(_ctx())
            sid = self._sid()
            cmd.execute(_reply_ctx(sid, 'a'))    # turn2 < min3 → 종료 금지
            self.assertEqual(sheets.batch_update_cells.call_count, 0)
            self.assertTrue(get_doctor_session().get(sid)['active'])
            resp3 = cmd.execute(_reply_ctx(sid, 'b'))   # turn3 → 종료
        self.assertTrue(resp3.success)
        self.assertIn('안정제', resp3.message)
        self.assertFalse(get_doctor_session().get(sid)['active'])

    # --- 최대 발화에서 강제 종료 ---
    def test_force_conclude_at_max_turns(self):
        sheets = _mgmt_manager()
        cmd = self._cmd(sheets)
        outcome = {'treatment': '진통제', 'health_delta': 4, 'sanity_delta': 0, 'reason': 'r'}
        with patch.object(inf, 'check_and_set', return_value=True), \
             patch.object(inf.ai_client, 'doctor_reply', return_value=_reply('아직 더 봅시다.', concluding=False)), \
             patch.object(inf.ai_client, 'doctor_treatment', return_value=outcome), \
             patch.object(inf.config, 'DOCTOR_MAX_TURNS', 2), \
             patch.object(inf.config, 'DOCTOR_TURNS_MIN', 2):
            cmd.execute(_ctx())
            sid = self._sid()
            resp2 = cmd.execute(_reply_ctx(sid, 'a'))   # turn2 == max → 강제 종료
        self.assertTrue(resp2.success)
        self.assertIn('진통제', resp2.message)
        self.assertFalse(get_doctor_session().get(sid)['active'])

    # --- 상한 100 ---
    def test_finalize_caps_at_100(self):
        sheets = _mgmt_manager(health=98, sanity=99)
        cmd = self._cmd(sheets)
        outcome = {'treatment': '상담', 'health_delta': 10, 'sanity_delta': 10, 'reason': 'r'}
        with patch.object(inf, 'check_and_set', return_value=True), \
             patch.object(inf.ai_client, 'doctor_reply', return_value=_reply('x', concluding=True)), \
             patch.object(inf.ai_client, 'doctor_treatment', return_value=outcome):
            cmd.execute(_ctx())
            resp = cmd.execute(_reply_ctx(self._sid(), '더'))
        updates = sheets.batch_update_cells.call_args[0][1]
        self.assertIn((3, 3, 100), updates)
        self.assertIn((3, 4, 100), updates)
        self.assertTrue(resp.success)

    # --- 변동 클램프 ---
    def test_finalize_clamps_delta_to_config(self):
        sheets = _mgmt_manager(health=50, sanity=50)
        cmd = self._cmd(sheets)
        outcome = {'treatment': '진통제', 'health_delta': 999, 'sanity_delta': -999, 'reason': 'r'}
        with patch.object(inf, 'check_and_set', return_value=True), \
             patch.object(inf.ai_client, 'doctor_reply', return_value=_reply('x', concluding=True)), \
             patch.object(inf.ai_client, 'doctor_treatment', return_value=outcome), \
             patch.object(inf.config, 'DOCTOR_TREAT_DELTA_MAX', 12), \
             patch.object(inf.config, 'DOCTOR_TREAT_DELTA_MIN', -6):
            cmd.execute(_ctx())
            resp = cmd.execute(_reply_ctx(self._sid(), 'x'))
        updates = sheets.batch_update_cells.call_args[0][1]
        self.assertIn((3, 3, 62), updates)
        self.assertIn((3, 4, 44), updates)
        self.assertTrue(resp.success)

    # --- 규칙 폴백(AI 미사용) + end_at 기준 종료 ---
    def test_finalize_rule_fallback_heals_lower_stat(self):
        sheets = _mgmt_manager(health=30, sanity=60)
        cmd = self._cmd(sheets)
        with patch.object(inf, 'check_and_set', return_value=True), \
             patch.object(inf.ai_client, 'doctor_reply', return_value=None), \
             patch.object(inf.ai_client, 'doctor_treatment', return_value=None):
            cmd.execute(_ctx())
            sid = self._sid()
            get_doctor_session().get(sid)['end_at'] = 2   # 폴백 종료 시점 고정
            resp = cmd.execute(_reply_ctx(sid, 'x'))
        updates = sheets.batch_update_cells.call_args[0][1]
        self.assertIn((3, 3, 35), updates)   # 건강 30+5
        self.assertIn('진통제', resp.message)

    # --- 반영 실패 시 안내 ---
    def test_finalize_write_failure_reports(self):
        sheets = _mgmt_manager()
        sheets.batch_update_cells.return_value = False
        cmd = self._cmd(sheets)
        outcome = {'treatment': '안정제', 'health_delta': 0, 'sanity_delta': 6, 'reason': 'r'}
        with patch.object(inf, 'check_and_set', return_value=True), \
             patch.object(inf.ai_client, 'doctor_reply', return_value=_reply('x', concluding=True)), \
             patch.object(inf.ai_client, 'doctor_treatment', return_value=outcome):
            cmd.execute(_ctx())
            resp = cmd.execute(_reply_ctx(self._sid(), 'x'))
        self.assertTrue(resp.success)
        self.assertIn('문제', resp.message)
        self.assertIn(inf.DOCTOR_CLOSING_LINE, resp.message)

    # --- 다중 타래: 새 방문이 이전 세션을 종료시키지 않는다 + 자정 넘겨 이어가도 시작일 ---
    def test_new_visit_keeps_old_thread_and_start_day(self):
        sheets = _mgmt_manager(health=50, sanity=40)
        cmd = self._cmd(sheets)
        # day 5 방문 시작(종료 없이 중단)
        with patch.object(inf.game_day, 'current_day', return_value=5), \
             patch.object(inf, 'check_and_set', return_value=True), \
             patch.object(inf.ai_client, 'doctor_reply', return_value=_reply('네.', concluding=False)):
            cmd.execute(_ctx())
        sid5 = self._sid()
        self.assertEqual(get_doctor_session().get(sid5)['day'], 5)

        # day 6 새 방문 → 이전 세션은 그대로 활성(종료/반영 없음)
        with patch.object(inf.game_day, 'current_day', return_value=6), \
             patch.object(inf, 'check_and_set', return_value=True), \
             patch.object(inf.ai_client, 'doctor_reply', return_value=_reply('네.', concluding=False)):
            cmd.execute(_ctx())
        self.assertTrue(get_doctor_session().get(sid5)['active'])          # 이전 타래 유지
        self.assertEqual(sheets.batch_update_cells.call_count, 0)          # 어느 쪽도 처치 안 됨
        sid6 = self._sid()
        self.assertNotEqual(sid5, sid6)                                    # 두 타래 공존

        # day 6에 옛 타래(day5)를 이어가도 시작일(5)로 카운트
        with patch.object(inf.game_day, 'current_day', return_value=6), \
             patch.object(inf.ai_client, 'doctor_reply', return_value=_reply('음.', concluding=False)):
            cmd.execute(_reply_ctx(sid5, '어제 얘기 계속'))
        self.assertEqual(get_doctor_session().get(sid5)['day'], 5)

    # --- 메시지에 이모지(🏥) 없음 (➭ 같은 기호는 허용) ---
    def test_messages_have_no_emoji_header(self):
        sheets = _mgmt_manager()
        cmd = self._cmd(sheets)
        outcome = {'treatment': '안정제', 'health_delta': 0, 'sanity_delta': 6, 'reason': 'r'}
        with patch.object(inf, 'check_and_set', return_value=True), \
             patch.object(inf.ai_client, 'doctor_reply', return_value=_reply('네.', concluding=True)), \
             patch.object(inf.ai_client, 'doctor_treatment', return_value=outcome):
            start = cmd.execute(_ctx())
            fin = cmd.execute(_reply_ctx(self._sid(), 'x'))
        for msg in (start.message, fin.message):
            self.assertNotIn('🏥', msg)

    # --- [대화 끝내기]: 처치 없이 종료 ---
    def test_end_talk_ends_without_treatment(self):
        sheets = _mgmt_manager()
        cmd = self._cmd(sheets)
        with patch.object(inf, 'check_and_set', return_value=True), \
             patch.object(inf.ai_client, 'doctor_reply', return_value=_reply('네.', concluding=False)):
            cmd.execute(_ctx())
        sid = self._sid()
        resp = self._end_cmd().execute(_end_ctx())   # 최근 진행 세션 종료
        self.assertTrue(resp.success)
        self.assertIn(inf.DOCTOR_CLOSING_LINE, resp.message)
        self.assertIn('처치는 없', resp.message)
        self.assertFalse(get_doctor_session().get(sid)['active'])
        self.assertEqual(sheets.batch_update_cells.call_count, 0)

    def test_end_talk_specific_thread(self):
        cmd = self._cmd()
        with patch.object(inf, 'check_and_set', return_value=True), \
             patch.object(inf.ai_client, 'doctor_reply', return_value=_reply('네.', concluding=False)):
            cmd.execute(_ctx())
        sid = self._sid()
        resp = self._end_cmd().execute(_end_ctx(session_id=sid))
        self.assertTrue(resp.success)
        self.assertFalse(get_doctor_session().get(sid)['active'])

    def test_end_talk_no_active_session(self):
        resp = self._end_cmd().execute(_end_ctx())
        self.assertTrue(resp.success)
        self.assertIn('진행 중인', resp.message)

    def test_end_talk_dm_only(self):
        resp = self._end_cmd().execute(_end_ctx(visibility='public'))
        self.assertFalse(resp.success)
        self.assertIn('DM', resp.message)

    # --- 종료 선점은 원자적(중복 처치 방지) ---
    def test_begin_finalize_is_atomic(self):
        mgr = get_doctor_session()
        s = mgr.start('alice', 5, {'이름': '엘리스'})
        sid = s['id']
        self.assertTrue(mgr.begin_finalize(sid))
        self.assertFalse(mgr.begin_finalize(sid))   # 두 번째 선점 실패
        self.assertFalse(mgr.get(sid)['active'])
        self.assertTrue(mgr.get(sid)['finalized'])

    # --- 시작 중 예외 시 오늘의무실 슬롯 롤백 ---
    def test_start_failure_rolls_back_daily_slot(self):
        sheets = _mgmt_manager()
        cmd = self._cmd(sheets)
        with patch.object(inf, 'check_and_set', return_value=True), \
             patch.object(inf.stat_gate, 'is_hospitalized', side_effect=RuntimeError('boom')), \
             patch.object(inf.game_state, 'set') as mock_set:
            resp = cmd.execute(_ctx())
        self.assertFalse(resp.success)
        mock_set.assert_called_once_with('alice', '오늘의무실', 0)

    # --- 폴백 대사 형식 불변식 ---
    def test_fallback_lines_format_invariants(self):
        lines = list(inf.DOCTOR_FALLBACK_LINES) + [inf.DOCTOR_CLOSING_LINE]
        self.assertTrue(lines)
        for line in lines:
            self.assertTrue(line.strip())
            self.assertNotIn('"', line, f'대사에 따옴표("): {line!r}')
            self.assertEqual(line.count('('), line.count(')'), f'괄호 불균형: {line!r}')
            self.assertGreaterEqual(len(line.replace(' ', '')), 20, f'너무 짧음: {line!r}')


if __name__ == '__main__':
    unittest.main()

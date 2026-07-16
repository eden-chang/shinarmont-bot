"""의무실 차트 — 지난 진료 소견을 남겨 다음 내원 때 이어 말하게 한다.

핵심 계약:
  - 소견은 **별도 AI 호출 없이** 마무리 발화의 `chart` 필드로 온다(DOCTOR_REPLY_SCHEMA).
  - 소견이 없으면(AI 꺼짐/폴백) 차트에 아무것도 남기지 않는다 — 지어내지 않는다.
  - 다음 내원 시 `patient['지난차트']` 로 프롬프트에 실린다.
"""

import os
import sys
import json
import shutil
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.json_store import JsonStore  # noqa: E402
from utils.doctor_charts import (  # noqa: E402
    ChartBook, format_charts, get_chart_book, set_chart_book,
    MAX_NOTES_PER_PATIENT, MAX_NOTE_CHARS,
)


class ChartBookTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='chart_')
        self.path = os.path.join(self.dir, 'charts.json')
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.book = ChartBook(store=JsonStore(self.path))

    def test_add_and_read(self):
        self.book.add('u1', 3, '두통 호소. 아세트아미노펜 3일분 처방.')
        notes = self.book.notes('u1')
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]['일차'], 3)
        self.assertIn('아세트아미노펜', notes[0]['소견'])

    def test_unknown_patient_is_empty(self):
        self.assertEqual(self.book.notes('nobody'), [])

    def test_empty_note_is_not_stored(self):
        """AI가 꺼졌거나 폴백이면 소견이 없다. 빈 기록을 남기면 안 된다."""
        self.book.add('u1', 3, '')
        self.book.add('u1', 3, '   ')
        self.book.add('u1', 3, None)
        self.assertEqual(self.book.notes('u1'), [])

    def test_notes_are_oldest_first(self):
        self.book.add('u1', 1, '첫 진료')
        self.book.add('u1', 5, '두번째')
        self.assertEqual([n['일차'] for n in self.book.notes('u1')], [1, 5])

    def test_keeps_only_recent_notes(self):
        """무한히 쌓이면 프롬프트가 계속 부풀어 오른다."""
        for day in range(1, MAX_NOTES_PER_PATIENT + 4):
            self.book.add('u1', day, f'{day}일차 소견')
        notes = self.book.notes('u1')
        self.assertEqual(len(notes), MAX_NOTES_PER_PATIENT)
        self.assertEqual(notes[0]['일차'], 4, "오래된 것부터 버려야 한다.")
        self.assertEqual(notes[-1]['일차'], MAX_NOTES_PER_PATIENT + 3)

    def test_long_note_is_truncated(self):
        """모델이 분량 규칙을 어겨도 프롬프트가 터지면 안 된다."""
        self.book.add('u1', 1, '가' * (MAX_NOTE_CHARS + 500))
        self.assertLessEqual(len(self.book.notes('u1')[0]['소견']), MAX_NOTE_CHARS + 1)

    def test_patients_do_not_leak_into_each_other(self):
        self.book.add('u1', 1, 'u1 소견')
        self.book.add('u2', 1, 'u2 소견')
        self.assertIn('u1 소견', self.book.notes('u1')[0]['소견'])
        self.assertIn('u2 소견', self.book.notes('u2')[0]['소견'])

    def test_survives_restart(self):
        """봇을 껐다 켜도 차트가 남아야 한다 — 안 그러면 다음 진료가 초진이 된다."""
        self.book.add('u1', 3, '두통약 처방')
        reborn = ChartBook(store=JsonStore(self.path))
        self.assertIn('두통약', reborn.notes('u1')[0]['소견'])

    def test_clear(self):
        self.book.add('u1', 1, '소견')
        self.book.clear('u1')
        self.assertEqual(self.book.notes('u1'), [])

    def test_corrupt_entry_ignored(self):
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump({'u1': ['문자열', {'소견': ''}, {'일차': 2, '소견': '정상'}, 42]}, f)
        book = ChartBook(store=JsonStore(self.path))
        notes = book.notes('u1')
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]['소견'], '정상')

    def test_non_list_value_ignored(self):
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump({'u1': '망가진 값'}, f)
        self.assertEqual(ChartBook(store=JsonStore(self.path)).notes('u1'), [])


class FormatChartsTest(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(format_charts([]), '')
        self.assertEqual(format_charts(None), '')

    def test_formats_with_day(self):
        out = format_charts([{'일차': 3, '소견': '두통약 처방'}])
        self.assertIn('3일차', out)
        self.assertIn('두통약 처방', out)

    def test_missing_day(self):
        out = format_charts([{'일차': None, '소견': '소견만'}])
        self.assertIn('지난 진료', out)


class DoctorPromptChartTest(unittest.TestCase):
    """지난 차트가 실제로 의사 프롬프트에 실리는지."""

    def setUp(self):
        from utils import ai_client as ai
        self.ai = ai

        class _Msgs:
            last = None

            def create(self, **kw):
                _Msgs.last = kw
                raise RuntimeError('프롬프트만 검사')

        class _Client:
            def __init__(self):
                self.messages = _Msgs()

        self._saved = (ai._client, ai._client_init_attempted)
        ai._client = _Client()
        ai._client_init_attempted = True
        self.msgs = ai._client.messages

    def tearDown(self):
        self.ai._client, self.ai._client_init_attempted = self._saved

    def _dynamic_block(self, patient):
        self.ai.doctor_reply(history=None, patient=patient, utterance='...')
        return self.msgs.last['system'][-1]['text']

    def test_past_chart_reaches_prompt(self):
        text = self._dynamic_block({
            '이름': '다즈', '방문횟수': 2,
            '지난차트': '  - 3일차: 두통 호소. 아세트아미노펜 처방.',
        })
        self.assertIn('[지난 차트]', text)
        self.assertIn('아세트아미노펜', text)

    def test_no_chart_block_on_first_visit(self):
        text = self._dynamic_block({'이름': '다즈', '방문횟수': 1})
        self.assertNotIn('[지난 차트]', text)

    def test_blank_chart_omits_block(self):
        text = self._dynamic_block({'이름': '다즈', '지난차트': '   '})
        self.assertNotIn('[지난 차트]', text)

    def test_chart_is_returned_from_reply(self):
        """마무리 응답의 chart 를 받아 와야 저장할 수 있다."""
        class _Block:
            type = 'text'
            text = json.dumps({'reply': '몸조심하십시오.', 'concluding': True,
                               'chart': '불면 호소. 수면제 3일분.'}, ensure_ascii=False)

        class _Resp:
            content = [_Block()]

        class _Msgs2:
            def create(self, **kw):
                return _Resp()

        self.ai._client.messages = _Msgs2()
        out = self.ai.doctor_reply(None, {'이름': '다즈'}, '잠이 안 옵니다', turn_no=4)
        self.assertTrue(out['concluding'])
        self.assertEqual(out['chart'], '불면 호소. 수면제 3일분.')

    def test_schema_allows_chart(self):
        self.assertIn('chart', self.ai.DOCTOR_REPLY_SCHEMA['properties'])


class SingletonTest(unittest.TestCase):
    def tearDown(self):
        set_chart_book(None)

    def test_injection_is_respected(self):
        """JsonStore는 __len__이 있어 빈 저장소가 falsy다 — 주입이 무시되면 안 된다."""
        tmp = tempfile.mkdtemp(prefix='chart_s_')
        self.addCleanup(shutil.rmtree, tmp, True)
        book = ChartBook(store=JsonStore(os.path.join(tmp, 'c.json')))
        self.assertEqual(len(book.notes('x')), 0)      # 비어 있음(falsy)
        set_chart_book(book)
        self.assertIs(get_chart_book(), book)



class SeamTest(unittest.TestCase):
    """_doctor_turn → 세션 → 디스크 → _save_chart 이음새.

    소견이 어느 단계에서든 새면 러셀은 다음 진료에서 지난 처방을 모른다.
    """

    def setUp(self):
        from utils import ai_client
        from commands.shinarmont import infirmary_command as inf
        self.ai, self.inf = ai_client, inf

        self.dir = tempfile.mkdtemp(prefix='seam_')
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.spath = os.path.join(self.dir, 's.json')

        self.book = ChartBook(store=JsonStore(os.path.join(self.dir, 'c.json')))
        set_chart_book(self.book)
        self.addCleanup(set_chart_book, None)

        self.mgr = inf._DoctorSessionManager(store=JsonStore(self.spath))
        self._saved_mgr = getattr(inf, '_doctor_session', None)
        inf._doctor_session = self.mgr
        self.addCleanup(lambda: setattr(inf, '_doctor_session', self._saved_mgr))

        self._saved_ai = (ai_client._client, ai_client._client_init_attempted)
        self.addCleanup(
            lambda: setattr(ai_client, '_client', self._saved_ai[0]))

    def _fake_ai(self, payload):
        class _B:
            type = 'text'
            text = json.dumps(payload, ensure_ascii=False)

        class _R:
            content = [_B()]

        class _M:
            def create(self, **kw):
                return _R()

        class _C:
            def __init__(self):
                self.messages = _M()

        self.ai._client = _C()
        self.ai._client_init_attempted = True

    def test_chart_flows_from_reply_to_book(self):
        self._fake_ai({'reply': '몸조심하십시오.', 'concluding': True,
                       'chart': '불면 호소. 수면제 사흘분 처방.'})
        cmd = self.inf.InfirmaryCommand(sheets_manager=None, api=None)
        session = self.mgr.start('daz', 5, {'이름': '다즈'})

        reply, concluding = cmd._doctor_turn([], {'이름': '다즈'}, '잠이 안 옵니다',
                                             turn_no=4, session=session)
        self.assertTrue(concluding)
        self.assertEqual(session.get('chart'), '불면 호소. 수면제 사흘분 처방.')

        # append_turn 이 세션을 통째로 저장 → 소견도 디스크에
        self.mgr.append_turn(session['id'], '잠이 안 옵니다', reply)
        with open(self.spath, encoding='utf-8') as f:
            self.assertEqual(json.load(f)[session['id']]['chart'],
                             '불면 호소. 수면제 사흘분 처방.',
                             "정산 전에 죽으면 소견이 날아간다.")

        cmd._save_chart('daz', session)
        notes = self.book.notes('daz')
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]['일차'], 5)
        self.assertIn('수면제', notes[0]['소견'])

    def test_no_chart_when_ai_falls_back(self):
        """AI가 실패하면 폴백 대사만 나온다 → 남길 소견이 없다."""
        class _M:
            def create(self, **kw):
                raise RuntimeError('AI 다운')

        class _C:
            def __init__(self):
                self.messages = _M()

        self.ai._client = _C()
        self.ai._client_init_attempted = True

        cmd = self.inf.InfirmaryCommand(sheets_manager=None, api=None)
        session = self.mgr.start('daz', 5, {'이름': '다즈'})
        session['end_at'] = 3
        reply, _ = cmd._doctor_turn([], {'이름': '다즈'}, '아픕니다', turn_no=4, session=session)

        self.assertTrue(reply, "폴백 대사는 나와야 한다.")
        self.assertIsNone(session.get('chart'))
        cmd._save_chart('daz', session)
        self.assertEqual(self.book.notes('daz'), [], "지어낸 소견을 남기면 안 된다.")

    def test_chart_not_saved_on_non_concluding_turn(self):
        """진행 중인 턴에는 chart 가 빈 문자열 → 세션에 담기지 않는다."""
        self._fake_ai({'reply': '좀 어떠십니까?', 'concluding': False, 'chart': ''})
        cmd = self.inf.InfirmaryCommand(sheets_manager=None, api=None)
        session = self.mgr.start('daz', 5, {'이름': '다즈'})
        cmd._doctor_turn([], {'이름': '다즈'}, '아픕니다', turn_no=3, session=session)
        self.assertFalse(session.get('chart'))


if __name__ == '__main__':
    unittest.main()

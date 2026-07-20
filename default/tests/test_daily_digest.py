"""utils/digest_facts.py · digest_report.py · daily_digest.py 단위 테스트.

일일보고는 GM이 소문을 짓는 재료다. 두 가지가 중요하다:
1. 사실 절(§1~6)에 AI가 끼어들지 않을 것 — 환각이 정본을 오염시키면 안 된다.
2. 타래가 한도를 넘지 않을 것 — 한 통이 실패하면 거기서 타래가 멈춘다.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils import daily_digest, digest_facts, digest_report, mention_guard


def _action(actor, kind, target, summary, day='3', when='07-16 09:00', row=3):
    return {'일시': when, '일차': day, '행위자': actor, '종류': kind,
            '대상': target, '요약': summary, '소문화': '', '_row_number': row}


ROSTER = [{'이름': n} for n in ('데보라', '한참', '클라라', '휴고')]
MGMT = [{'이름': '데보라', '소지금': '144', '건강': '88', '이성': '59'},
        {'이름': '한참', '소지금': '212', '건강': '70', '이성': '81'}]


def _managers(actions, system_sheets=None, inv_log=None, mgmt=None, roster=None):
    system_sheets = system_sheets or {}
    sysm = MagicMock()
    sysm.get_worksheet_data.side_effect = lambda s, use_cache=False: (
        actions if s == '행동로그' else system_sheets.get(s, []))

    main = MagicMock()
    main.get_worksheet_data.side_effect = lambda s, use_cache=False: (
        {'관리': mgmt if mgmt is not None else MGMT,
         '명단': roster if roster is not None else ROSTER}.get(s, []))

    inv = MagicMock()
    inv.get_worksheet_data.side_effect = lambda s, use_cache=False: (
        (inv_log or []) if s == '로그' else [])
    return main, sysm, inv


class FetchFactsTest(unittest.TestCase):
    def test_only_the_requested_day(self):
        actions = [
            _action('데보라', '추적', '한참', '데보라가 한참을 뒤쫓았다', day='3'),
            _action('한참', '고발', '데보라', '한참이 데보라를 고발했다', day='2'),
        ]
        main, sysm, inv = _managers(actions)
        facts = digest_facts.fetch_facts(3, main, sysm, inv)
        self.assertEqual(len(facts['actions']), 1)
        self.assertEqual(facts['actions'][0]['행위자'], '데보라')

    def test_day_compare_tolerates_float_strings(self):
        actions = [_action('데보라', '추적', '한참', 'x', day='3.0')]
        main, sysm, inv = _managers(actions)
        self.assertEqual(len(digest_facts.fetch_facts(3, main, sysm, inv)['actions']), 1)

    def test_accusation_reason_is_joined(self):
        actions = [_action('한참', '고발', '데보라', '한참이 데보라를 고발했다')]
        main, sysm, inv = _managers(actions, system_sheets={
            '고발': [{'일차': '3', '고발자': '한참', '대상': '데보라',
                      '사유': '초소 근처를 서성였다', '처리': ''}]})
        facts = digest_facts.fetch_facts(3, main, sysm, inv)
        self.assertEqual(facts['accusations'][0]['사유'], '초소 근처를 서성였다')

    def test_sheet_failure_is_absorbed_and_reported(self):
        """한 장을 못 읽었다고 보고 전체를 포기하면 사고 난 날 보고가 안 온다."""
        actions = [_action('데보라', '추적', '한참', 'x')]
        main, sysm, inv = _managers(actions)
        original = sysm.get_worksheet_data.side_effect

        def _boom(sheet, use_cache=False):
            if sheet == '고발':
                raise RuntimeError('429')
            return original(sheet, use_cache)

        sysm.get_worksheet_data.side_effect = _boom
        facts = digest_facts.fetch_facts(3, main, sysm, inv)
        self.assertEqual(len(facts['actions']), 1, "다른 절은 살아야 한다")
        self.assertTrue(any('고발' in e for e in facts['errors']))

    def test_quiet_runners(self):
        actions = [_action('데보라', '추적', '한참', 'x')]
        main, sysm, inv = _managers(actions)
        facts = digest_facts.fetch_facts(3, main, sysm, inv)
        self.assertEqual(digest_facts.quiet_runners(facts), ['클라라', '한참', '휴고'])


class ContactGraphTest(unittest.TestCase):
    def _graph(self, actions, roster=None):
        main, sysm, inv = _managers(actions, roster=roster)
        return digest_facts.build_contact_graph(
            digest_facts.fetch_facts(3, main, sysm, inv))

    def test_places_are_not_people(self):
        """조사의 '대상'은 장소다. 관계망에 들어오면 장소가 인물 행세를 한다."""
        graph = self._graph([
            _action('데보라', '조사', '보안 경계', '데보라가 보안 경계의 초소 앞을 살폈다'),
        ])
        self.assertEqual(graph['edges'], [])
        self.assertNotIn('보안 경계', graph['targets'])

    def test_doctor_literal_is_not_a_person(self):
        graph = self._graph([
            _action('한참', '의무실 방문', '의사', '한참이 의무실에서 처치를 받았다'),
        ])
        self.assertEqual(graph['edges'], [])

    def test_self_target_excluded(self):
        """부탁지령수행의 대상은 자기 자신이다."""
        graph = self._graph([
            _action('클라라', '부탁지령수행', '클라라', '클라라가 마쳤다고 알렸다'),
        ])
        self.assertEqual(graph['edges'], [])

    def test_mutual_contact_detected(self):
        graph = self._graph([
            _action('데보라', '추적', '한참', 'a'),
            _action('한참', '고발', '데보라', 'b'),
        ])
        self.assertEqual(graph['mutual'], [('데보라', '한참', ['고발', '추적'])])

    def test_one_way_is_not_mutual(self):
        graph = self._graph([_action('데보라', '추적', '한참', 'a')])
        self.assertEqual(graph['mutual'], [])

    def test_target_counts(self):
        graph = self._graph([
            _action('한참', '고발', '데보라', 'a'),
            _action('클라라', '교류', '데보라', 'b'),
        ])
        self.assertEqual(graph['targets']['데보라'], 2)

    def test_roster_unreadable_still_filters_nonpeople(self):
        """명단을 못 읽어도 장소·'의사'는 걸러야 한다."""
        graph = self._graph([
            _action('데보라', '조사', '보안 경계', 'a'),
            _action('한참', '의무실 방문', '의사', 'b'),
            _action('데보라', '추적', '한참', 'c'),
        ], roster=[])
        self.assertEqual([e['kind'] for e in graph['edges']], ['추적'])


class ReportTest(unittest.TestCase):
    def _report(self, actions, **kw):
        main, sysm, inv = _managers(actions, **kw)
        facts = digest_facts.fetch_facts(3, main, sysm, inv)
        return digest_report.build_report(facts)

    def test_investigation_points_are_not_duplicated(self):
        """같은 장소를 두 번 조사하면 포인트가 양쪽에 다 붙던 버그."""
        actions = [
            _action('데보라', '조사', '보안 경계',
                    '데보라가 보안 경계의 초소 앞을 살폈다', row=3),
            _action('데보라', '조사', '보안 경계',
                    '데보라가 보안 경계의 정문 검문 기록을 살폈다', row=4),
        ]
        report = self._report(actions, inv_log=[
            {'캐릭터명': '데보라', '장소명': '보안 경계', '포인트명': '초소 앞',
             '결과': "'담배' 1개 획득"},
            {'캐릭터명': '데보라', '장소명': '보안 경계', '포인트명': '정문 검문 기록',
             '결과': '3달러 획득'},
        ])
        self.assertEqual(report.count("'담배' 1개 획득"), 1)
        self.assertEqual(report.count('3달러 획득'), 1)

    def test_empty_day(self):
        report = self._report([])
        self.assertIn('사건이 없습니다', report)

    def test_errors_surface_in_the_report(self):
        """못 읽은 시트를 조용히 넘기면 GM이 빈 절을 사실로 착각한다."""
        facts = {'day': 3, 'actions': [], 'by_actor': {}, 'accusations': [],
                 'directives': [], 'votes': [], 'investigations': [],
                 'stats': {}, 'roster': [], 'errors': ["'고발' 시트를 읽지 못했습니다"]}
        report = digest_report.build_report(facts)
        self.assertIn('일부 시트를 읽지 못한', report)
        self.assertIn('고발', report)

    def test_unknown_kind_is_not_dropped(self):
        """새 종류가 조용히 사라지면 안 된다."""
        report = self._report([_action('데보라', '새종류', '한참', '뭔가 했다')])
        self.assertIn('새종류', report)
        self.assertIn('뭔가 했다', report)

    def test_seed_section_is_marked_as_not_fact(self):
        facts = {'day': 3, 'actions': [_action('데보라', '추적', '한참', 'x')],
                 'by_actor': {}, 'accusations': [], 'directives': [], 'votes': [],
                 'investigations': [], 'stats': {}, 'roster': [], 'errors': []}
        report = digest_report.build_report(facts, seeds={'seeds': [
            {'각도': '왜곡', '문구': '어쩌고', '근거': '저쩌고'}]})
        self.assertIn('사실 아님', report)
        self.assertIn('[왜곡] 어쩌고', report)

    def test_vote_tally(self):
        report = self._report([_action('데보라', '추적', '한참', 'x')], system_sheets={
            '투표': [{'일차': '3', '대상': '데보라', '투표자': '한참', '표': '유죄'},
                     {'일차': '3', '대상': '데보라', '투표자': '클라라', '표': '무죄'}]})
        self.assertIn('유죄 1 / 무죄 1', report)


class SlotIntegrationTest(unittest.TestCase):
    """시트에 없는 것들이 보고서에 실려야 한다."""

    def _facts(self, slots, actions=None):
        return {
            'day': 3,
            'actions': actions if actions is not None else [
                _action('데보라', '대화', '한참', '데보라와 한참 비밀 대화를 나눴다')],
            'by_actor': {'데보라': {'kinds': {'대화': [
                _action('데보라', '대화', '한참', '데보라와 한참 비밀 대화를 나눴다')]},
                'stats': {}}},
            'accusations': [], 'directives': [], 'votes': [], 'investigations': [],
            'stats': {}, 'roster': ['데보라', '한참', '클라라', '휴고', '원쥔'],
            'name_by_id': {'deborah': '데보라', 'hancham': '한참',
                           'clara': '클라라', 'wonjun': '원쥔', 'hugo': '휴고'},
            'slots': slots, 'errors': [],
        }

    def test_secondary_talk_partners_appear(self):
        """행동로그엔 최초 상대만 남는다 — 나머지는 @STORY JSON 이 유일한 기록이다."""
        facts = self._facts({'story': {
            '대화': {'deborah': {'상대': ['한참', '클라라', '휴고'], '대화소진': True}},
            '진행중': {}}})
        report = digest_report.build_report(facts)
        self.assertIn('클라라, 휴고', report)
        self.assertIn('시트엔 없음', report)

    def test_doctor_chart_and_transcript(self):
        facts = self._facts({'doctor': {
            '소견': {'hancham': [{'일차': 3, '소견': '불면 호소. 진정제 처방.'}]},
            '진료': {'hancham': {'일차': 3, '턴수': 2, '진행중': False, '차트': '',
                                 '대화록': [{'role': 'user', 'content': '잠을 못 자요'},
                                            {'role': 'assistant', 'content': '언제부터요?'}]}},
            '이력': {'hancham': {'지난방문': {'일차': 1, '건강': 92, '이성': 88}}}}})
        report = digest_report.build_report(facts)
        self.assertIn('불면 호소', report)
        self.assertIn('환자: 잠을 못 자요', report)
        self.assertIn('러셀: 언제부터요?', report)
        self.assertIn('지난 방문(1일차)', report)

    def test_transcript_can_be_disabled(self):
        facts = self._facts({'doctor': {
            '소견': {'hancham': [{'일차': 3, '소견': '불면 호소.'}]},
            '진료': {'hancham': {'턴수': 1, '대화록': [
                {'role': 'user', 'content': '비밀 이야기'}]}},
            '이력': {}}})
        with patch.object(digest_report, '_cfg_bool', return_value=False):
            report = digest_report.build_report(facts)
        self.assertIn('불면 호소', report, "소견은 남아야 한다")
        self.assertNotIn('비밀 이야기', report)

    def test_long_utterance_is_capped(self):
        facts = self._facts({'doctor': {'소견': {}, '이력': {},
            '진료': {'hancham': {'턴수': 1, '대화록': [
                {'role': 'user', 'content': '가' * 2000}]}}}})
        with patch.object(digest_report, '_cfg_int', return_value=100):
            report = digest_report.build_report(facts)
        self.assertIn('···', report)
        self.assertNotIn('가' * 200, report)

    def test_gambling_counts_and_orphan(self):
        facts = self._facts({'bar': {
            '플레이': {'clara': {'슬롯머신': 14, '블랙잭': 3}},
            '지급중단': [{'아이디': 'clara', '베팅': 100, '지급시도': 250}]}})
        report = digest_report.build_report(facts)
        self.assertIn('클라라 — 슬롯머신 14회, 블랙잭 3회', report)
        self.assertIn('배당 지급 중 중단된 판', report)

    def test_gambler_is_not_called_quiet(self):
        """슬롯 17판 돌린 사람을 '조용했다'고 적으면 GM이 헛다리를 짚는다."""
        facts = self._facts({'bar': {'플레이': {'clara': {'슬롯머신': 17}}, '지급중단': []}})
        quiet = digest_facts.quiet_runners(facts)
        self.assertNotIn('클라라', quiet)
        self.assertIn('휴고', quiet, "정말 아무것도 안 한 사람은 남아야 한다")

    def test_doctor_visitor_is_not_called_quiet(self):
        facts = self._facts({'doctor': {
            '소견': {}, '이력': {},
            '진료': {'clara': {'턴수': 1, '대화록': []}}}})
        self.assertNotIn('클라라', digest_facts.quiet_runners(facts))

    def test_unlogged_talk_attempt_surfaced(self):
        """대화를 걸어놓고 안 끝내면 하루치를 쓰고도 시트엔 아무것도 안 남는다."""
        facts = self._facts({'story': {
            '대화': {'wonjun': {'대화소진': True}}, '진행중': {}}})
        report = digest_report.build_report(facts)
        self.assertIn('하려다 만 것', report)
        self.assertIn('원쥔', report)

    def test_logged_talk_is_not_an_attempt(self):
        facts = self._facts({'story': {
            '대화': {'deborah': {'상대': ['한참'], '대화소진': True}}, '진행중': {}}})
        self.assertEqual(digest_facts.unlogged_attempts(facts), [])

    def test_section_numbers_are_sequential(self):
        """절은 비면 통째로 빠진다 — 번호를 소스에 박으면 겹치거나 건너뛴다."""
        facts = self._facts({'bar': {'플레이': {'clara': {'슬롯머신': 2}}, '지급중단': []},
                             'doctor': {'소견': {'hancham': [{'일차': 3, '소견': 'x'}]},
                                        '진료': {}, '이력': {}}})
        report = digest_report.build_report(facts)
        import re
        nums = [int(m) for m in re.findall(r'^(\d+)\. ', report, re.M)]
        self.assertEqual(nums, list(range(1, len(nums) + 1)),
                         f"절 번호가 어긋났다: {nums}")

    def test_unknown_id_falls_back_to_the_id(self):
        """명단에 없는 아이디도 사라지면 안 된다."""
        facts = self._facts({'bar': {'플레이': {'유령': {'슬롯머신': 1}}, '지급중단': []}})
        self.assertIn('유령', digest_report.build_report(facts))

    def test_slot_read_failure_is_reported(self):
        facts = self._facts({})
        facts['errors'] = ['슬롯 로컬 기록을 읽지 못했습니다(OSError)']
        report = digest_report.build_report(facts)
        self.assertIn('일부 시트를 읽지 못한', report)
        self.assertIn('슬롯 로컬 기록', report)


class ChunkTest(unittest.TestCase):
    def test_never_exceeds_limit(self):
        text = "\n".join(f"{i}번째 줄 " + "가" * 60 for i in range(200))
        for chunk in digest_report.chunk_report(text, limit=500):
            self.assertLessEqual(len(chunk), 500)

    def test_reserve_is_subtracted(self):
        """멘션·머리표를 예산에서 안 빼면 딱 맞춘 통이 한도를 넘어 실패한다."""
        text = "\n".join("가" * 50 for _ in range(50))
        for chunk in digest_report.chunk_report(text, limit=500, reserve=100):
            self.assertLessEqual(len(chunk), 400)

    def test_splits_on_line_boundaries(self):
        text = "\n".join(f"줄{i}" for i in range(100))
        chunks = digest_report.chunk_report(text, limit=50)
        for chunk in chunks:
            for line in chunk.split("\n"):
                self.assertRegex(line, r'^줄\d+$', "문장 한복판에서 끊겼다")

    def test_overlong_single_line_is_force_split(self):
        """한 줄이 한도를 넘으면(긴 고발 사유) 그 통이 실패해 타래가 멈춘다."""
        chunks = digest_report.chunk_report("가" * 1000, limit=300)
        self.assertTrue(chunks)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 300)
        self.assertEqual("".join(chunks), "가" * 1000, "강제 분할이 글자를 잃었다")

    def test_nothing_is_lost(self):
        text = "\n".join(f"줄{i}" for i in range(200))
        rejoined = "\n".join(digest_report.chunk_report(text, limit=100))
        for i in range(200):
            self.assertIn(f"줄{i}", rejoined)

    def test_empty_input(self):
        self.assertEqual(digest_report.chunk_report(""), [])
        self.assertEqual(digest_report.chunk_report("   \n  "), [])

    def test_single_chunk_gets_no_page_tag(self):
        self.assertEqual(digest_report.paginate(['본문'], header='[일일보고]'), ['본문'])

    def test_multi_chunk_gets_page_tags(self):
        out = digest_report.paginate(['a', 'b'], header='[일일보고] 3일차')
        self.assertTrue(out[0].startswith('[일일보고] 3일차 (1/2)'))
        self.assertTrue(out[1].startswith('[일일보고] 3일차 (2/2)'))


class SendThreadTest(unittest.TestCase):
    class _Api:
        def __init__(self, fail_at=None, no_id=False):
            self.posts = []
            self.fail_at = fail_at
            self.no_id = no_id

        def status_post(self, status, visibility=None, in_reply_to_id=None):
            self.posts.append({'text': status, 'vis': visibility,
                               'reply_to': in_reply_to_id})
            if self.fail_at and len(self.posts) == self.fail_at:
                raise RuntimeError('422 too long')
            return None if self.no_id else {'id': f'id{len(self.posts)}'}

    def test_threads_by_in_reply_to(self):
        api = self._Api()
        result = daily_digest.send_thread(api, 'NOTICE', ['a', 'b', 'c'])
        self.assertEqual(result['sent'], 3)
        self.assertEqual([p['reply_to'] for p in api.posts], [None, 'id1', 'id2'])
        self.assertEqual(result['root_id'], 'id1')

    def test_all_direct_and_mentioned(self):
        api = self._Api()
        daily_digest.send_thread(api, 'NOTICE', ['a', 'b'])
        self.assertTrue(all(p['vis'] == 'direct' for p in api.posts))
        self.assertTrue(all(p['text'].startswith('@NOTICE ') for p in api.posts))

    def test_stops_on_failure(self):
        """앵커를 잃은 채 계속 쏘면 조각이 흩어져 더 못 읽는다."""
        api = self._Api(fail_at=2)
        result = daily_digest.send_thread(api, 'NOTICE', ['a', 'b', 'c'])
        self.assertEqual(result['sent'], 1)
        self.assertEqual(len(api.posts), 2, "3번째는 시도조차 하지 않아야 한다")

    def test_missing_id_keeps_going(self):
        """id를 못 얻어도 흩어진 조각이라도 보내는 게 낫다(번호표가 있다)."""
        api = self._Api(no_id=True)
        result = daily_digest.send_thread(api, 'NOTICE', ['a', 'b'])
        self.assertEqual(result['sent'], 2)

    def test_no_api(self):
        self.assertEqual(daily_digest.send_thread(None, 'NOTICE', ['a'])['sent'], 0)

    def test_no_recipient(self):
        api = self._Api()
        self.assertEqual(daily_digest.send_thread(api, '', ['a'])['sent'], 0)
        self.assertEqual(len(api.posts), 0)


class RunDigestTest(unittest.TestCase):
    def _cfg(self, **over):
        # 이 묶음은 기존 상세 보고(legacy)를 검증한다 — 브리핑은 test_digest_seeds.py 담당.
        base = {'DIGEST_ENABLED': True, 'DIGEST_AI_SEEDS': False,
                'DIGEST_BRIEF': False, 'DIGEST_LEGACY_REPORT': False,
                'DIGEST_RECIPIENT_ID': 'NOTICE', 'DIGEST_SEED_COUNT': 8,
                'DIGEST_CHUNK_LIMIT': 4800, 'SYSTEM_ADMIN_ID': 'NOTICE'}
        base.update(over)
        return patch.object(daily_digest, '_cfg',
                            side_effect=lambda k, d: base.get(k, d))

    def test_facts_never_reach_the_ai(self):
        """사실 절은 AI를 거치지 않는다 — 환각이 정본을 오염시키면 안 된다."""
        actions = [_action('한참', '고발', '데보라', '한참이 데보라를 고발했다')]
        main, sysm, inv = _managers(actions, system_sheets={
            '고발': [{'일차': '3', '고발자': '한참', '대상': '데보라',
                      '사유': '초소 근처를 서성였다', '처리': ''}]})
        api = SendThreadTest._Api()
        with self._cfg(), patch.object(daily_digest, '_seeds') as seeds:
            seeds.return_value = None
            result = daily_digest.run_daily_digest(main, sysm, api, inv, day=3)
        self.assertIn('초소 근처를 서성였다', result['report'])
        self.assertNotIn('사실 아님', result['report'], "씨앗이 없으면 §7도 없어야 한다")

    def test_ai_failure_does_not_lose_the_facts(self):
        actions = [_action('데보라', '추적', '한참', '데보라가 한참을 뒤쫓았다')]
        main, sysm, inv = _managers(actions)
        api = SendThreadTest._Api()
        with self._cfg(DIGEST_AI_SEEDS=True), \
             patch('utils.ai_client.rumor_seeds', side_effect=RuntimeError('API 죽음')):
            result = daily_digest.run_daily_digest(main, sysm, api, inv, day=3)
        self.assertEqual(result['sent'], result['total'])
        self.assertIn('데보라가 한참을 뒤쫓았다', result['report'])

    def test_seeds_get_facts_only_not_raw_sheets(self):
        """AI에게 시트 원본을 주면 없는 사건을 지어낼 여지가 생긴다."""
        actions = [_action('데보라', '추적', '한참', '데보라가 한참을 뒤쫓았다')]
        main, sysm, inv = _managers(actions)
        api = SendThreadTest._Api()
        with self._cfg(DIGEST_AI_SEEDS=True), \
             patch.object(daily_digest, '_seeds', return_value=None) as seeds:
            daily_digest.run_daily_digest(main, sysm, api, inv, day=3)
        facts_text = seeds.call_args[0][0]
        self.assertIn('데보라가 한참을 뒤쫓았다', facts_text)
        self.assertNotIn('사실 아님', facts_text, "씨앗 절이 씨앗 프롬프트에 들어갔다")

    def test_disabled(self):
        api = SendThreadTest._Api()
        main, sysm, inv = _managers([_action('데보라', '추적', '한참', 'x')])
        with self._cfg(DIGEST_ENABLED=False):
            result = daily_digest.run_daily_digest(main, sysm, api, inv, day=3)
        self.assertEqual(result['sent'], 0)
        self.assertEqual(len(api.posts), 0)

    def test_no_ai_call_on_empty_day(self):
        """사건이 없는 날 AI를 부르면 크레딧만 태운다."""
        main, sysm, inv = _managers([])
        api = SendThreadTest._Api()
        with self._cfg(DIGEST_AI_SEEDS=True), \
             patch.object(daily_digest, '_seeds') as seeds:
            daily_digest.run_daily_digest(main, sysm, api, inv, day=3)
        seeds.assert_not_called()

    def test_reserve_covers_mention_and_header(self):
        """실제 발송 통이 한도를 넘지 않아야 한다(멘션+머리표 포함)."""
        actions = [_action('데보라', '추적', '한참', '가' * 300, row=i)
                   for i in range(40)]
        main, sysm, inv = _managers(actions)
        api = SendThreadTest._Api()
        with self._cfg(DIGEST_CHUNK_LIMIT=1000):
            daily_digest.run_daily_digest(main, sysm, api, inv, day=3)
        self.assertGreater(len(api.posts), 1, "여러 통으로 쪼개졌어야 한다")
        for p in api.posts:
            self.assertLessEqual(len(p['text']), 1000,
                                 "멘션·머리표를 예산에서 빼지 않아 한도를 넘었다")

    def test_writes_nothing_to_sheets(self):
        """일일보고는 읽기 전용이다."""
        actions = [_action('데보라', '추적', '한참', 'x')]
        main, sysm, inv = _managers(actions)
        api = SendThreadTest._Api()
        with self._cfg():
            daily_digest.run_daily_digest(main, sysm, api, inv, day=3)
        for m in (main, sysm, inv):
            m.append_row.assert_not_called()
            m.update_cell.assert_not_called()
            m.batch_update_cells.assert_not_called()


class MentionGuardTest(unittest.TestCase):
    """`mention_guard` 단위 — 무엇을 죽이고 무엇을 살리는가."""

    def test_defang_kills_mention(self):
        self.assertEqual(mention_guard.defang('@avet 서명 없는 지시'),
                         '＠avet 서명 없는 지시')

    def test_defang_kills_remote_mention(self):
        self.assertFalse(mention_guard.has_live_mention(
            mention_guard.defang('@user@other.social 안녕')))

    def test_defang_spares_email_tail(self):
        """앞에 단어문자가 붙으면 멘션이 아니다 — 본문을 필요 이상으로 망가뜨리지 않는다."""
        self.assertEqual(mention_guard.defang('a@b.com'), 'a@b.com')

    def test_defang_all_is_absolute(self):
        self.assertEqual(mention_guard.defang_all('a@b.com @x'), 'a＠b.com ＠x')

    def test_strip_mentions_leaves_title(self):
        self.assertEqual(mention_guard.strip_mentions('@avet 서명 없는 지시'),
                         '서명 없는 지시')

    def test_no_at_is_untouched(self):
        self.assertEqual(mention_guard.defang('평범한 문장'), '평범한 문장')


class DigestMentionSafetyTest(unittest.TestCase):
    """일일보고가 인용문 속 계정 태그로 플레이어를 호출하지 않는다 (2026-07-19 사고).

    막는 지점이 셋이다 — 하나씩 뚫려도 다음이 받는다.
    """

    DIRECTIVE = ('@avet 서명 없는 지시\n보상: 10달러\n\n'
                 '결재 서류 더미 사이에서 쪽지 한 장이 나왔다. 동봉된 성냥갑을 '
                 '묘지 북쪽 세 번째 비석 아래에 두고 오라는 내용이었다.')

    def _facts(self):
        actions = [_action('아베트', '부탁지령수행', '아베트',
                           '지령 결과 보고: @avet 서명 없는 지시 보상: 10달러')]
        main, sysm, inv = _managers(actions, system_sheets={
            '부탁지령': [{'일차': '3', '대상': '아베트', '내용': self.DIRECTIVE,
                          '경중': '2', '보상': '10', '상태': '완료됨',
                          '완료 내용': '@STORY 다녀왔다', '완료 일차': '3'}],
            '고발': [{'일차': '3', '고발자': '한참', '대상': '데보라',
                      '사유': '@deborah 를 언급하며 서성였다', '처리': ''}],
        })
        return digest_facts.fetch_facts(3, main, sysm, inv)

    # ── 1단계: 수집 ──
    def test_facts_are_defanged_at_source(self):
        facts = self._facts()
        self.assertFalse(mention_guard.has_live_mention(facts['directives'][0]['내용']))
        self.assertFalse(mention_guard.has_live_mention(facts['directives'][0]['완료 내용']))
        self.assertFalse(mention_guard.has_live_mention(facts['accusations'][0]['사유']))

    def test_ids_survive_defanging(self):
        """아이디는 defang 대상이 아니다 — '@'를 벗겨 이름을 되짚어야 한다."""
        main, sysm, inv = _managers(
            [_action('데보라', '추적', '한참', 'x')],
            roster=[{'이름': '데보라', '아이디': '@deborah'}])
        facts = digest_facts.fetch_facts(3, main, sysm, inv)
        self.assertEqual(facts['name_by_id'].get('deborah'), '데보라')

    # ── 2단계: 조립 ──
    def test_report_has_no_live_mention(self):
        report = digest_report.build_report(self._facts())
        self.assertFalse(mention_guard.has_live_mention(report), report[:400])

    def test_ai_seeds_are_defanged_too(self):
        """씨앗은 시트를 안 거친다 — 1단계를 통과하지 않으므로 2단계가 받아야 한다."""
        facts = self._facts()
        report = digest_report.build_report(facts, seeds={'seeds': [
            {'각도': '왜곡', '문구': '@avet 이 수상하다', '근거': 'x'}]})
        self.assertFalse(mention_guard.has_live_mention(report))

    # ── 3단계: 발송 ──
    def test_send_defangs_body_but_keeps_recipient(self):
        api = SendThreadTest._Api()
        daily_digest.send_thread(api, 'NOTICE', ['@avet 이 안 죽고 여기까지 왔다'])
        text = api.posts[0]['text']
        self.assertTrue(text.startswith('@NOTICE '), text)
        self.assertNotIn('@avet', text)
        self.assertIn('＠avet', text)
        # 수신자 멘션은 정확히 하나만 살아 있어야 한다
        self.assertEqual(text.count('@'), 1)

    def test_end_to_end_thread_has_one_mention_per_toot(self):
        actions = [_action('아베트', '부탁지령수행', '아베트',
                           '지령 결과 보고: @avet 서명 없는 지시')]
        main, sysm, inv = _managers(actions, system_sheets={
            '부탁지령': [{'일차': '3', '대상': '아베트', '내용': self.DIRECTIVE,
                          '경중': '2', '보상': '10', '상태': '완료됨',
                          '완료 내용': '@STORY 다녀왔다', '완료 일차': '3'}]})
        api = SendThreadTest._Api()
        with RunDigestTest()._cfg():
            daily_digest.run_daily_digest(main, sysm, api, inv, day=3)
        self.assertTrue(api.posts)
        for p in api.posts:
            self.assertEqual(p['text'].count('@'), 1,
                             f"수신자 외의 멘션이 살아 나갔다: {p['text'][:200]}")


class DirectiveBrevityTest(unittest.TestCase):
    """지령 전문을 두 절에 통째로 되풀이할 값어치가 없다."""

    DIRECTIVE = ('@avet 서명 없는 지시\n보상: 10달러\n\n'
                 + '결재 서류 더미 사이에서 쪽지 한 장이 나왔다. ' * 20)

    def _report(self):
        actions = [_action('아베트', '부탁지령수행', '아베트', '아베트가 지령을 마쳤다')]
        main, sysm, inv = _managers(actions, system_sheets={
            '부탁지령': [{'일차': '3', '대상': '아베트', '내용': self.DIRECTIVE,
                          '경중': '2', '보상': '10', '상태': '완료됨',
                          '완료 내용': '다녀왔다. ' * 100, '완료 일차': '3'}]})
        return digest_report.build_report(digest_facts.fetch_facts(3, main, sysm, inv))

    def test_title_is_shown_without_the_account_tag(self):
        report = self._report()
        self.assertIn('지령: 서명 없는 지시', report)

    def test_body_appears_once_and_only_as_an_excerpt(self):
        report = self._report()
        body_lines = [l for l in report.split('\n') if l.strip().startswith('내용:')]
        self.assertEqual(len(body_lines), 1, "지령 본문은 §3에 한 번만 실려야 한다")
        self.assertLessEqual(len(body_lines[0]), 200, "본문이 발췌되지 않았다")
        self.assertTrue(body_lines[0].endswith('…'), "잘렸으면 '…'로 표시되어야 한다")

    def test_actor_section_carries_no_body(self):
        """§1은 훑어보는 절 — 제목만 걸고 본문은 안 싣는다."""
        actor_section = self._report().split('2. 관계망')[0]
        self.assertIn('지령: 서명 없는 지시', actor_section)
        self.assertNotIn('결재 서류', actor_section)

    def test_no_raw_newlines_inside_indented_items(self):
        """개행이 그대로 들어가면 들여쓰기가 깨져 보고서 구조가 무너진다."""
        for line in self._report().split('\n'):
            if not line.strip():
                continue
            self.assertFalse(line.startswith('결재 서류'),
                             f"들여쓰기 밖으로 새어 나온 지령 본문: {line[:60]}")
            self.assertFalse(line.startswith('보상: '),
                             f"들여쓰기 밖으로 새어 나온 지령 본문: {line[:60]}")

    def test_report_is_shorter_than_before(self):
        report = self._report()
        self.assertLess(len(report), len(self.DIRECTIVE) * 2,
                        "지령을 절마다 통째로 실으면 보고서가 지령 길이에 끌려간다")


if __name__ == '__main__':
    unittest.main()

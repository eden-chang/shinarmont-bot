"""소문 브리핑 재구성 단위 테스트 (digest_events · digest_seeds · digest_brief · 파이프라인).

개발안내서 §13 "검증용 기대 결과(4일차)"를 합성 픽스처로 재현한다.
**정확한 점수보다 순위 경향**을 본다(§13). 그리고 §5 제외 규칙이 잡음을 씨앗에서
빼는지, §8 렌더가 포맷·멘션 안전을 지키는지 확인한다.
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils import (daily_digest, digest_events as de, digest_seeds as ds,
                   digest_brief as db, mention_guard)


def act(actor, kind, target, summary='x', when='07-16 21:00', day='4'):
    return {'일시': when, '일차': day, '행위자': actor, '종류': kind,
            '대상': target, '요약': summary, '소문화': ''}


def seed_for(seeds, name):
    """이름이 들어간 씨앗을 찾는다(없으면 None)."""
    for s in seeds:
        if name in s.people:
            return s
    return None


def rank_of(seeds, name):
    for i, s in enumerate(seeds):
        if name in s.people:
            return i
    return None


# --------------------------------------------------------------------------- #
# §13 — 4일차 기대 결과 재현
# --------------------------------------------------------------------------- #
class Day4ExpectationTest(unittest.TestCase):
    """4일차 원본을 새 파이프라인에 넣었을 때의 순위 경향(§13)."""

    def _facts(self):
        actions = [
            # 에블린↔휴고: 비밀 대화 + 상호 추적, 코넬리아→휴고(목격자/집중)
            act('에블린', '대화', '휴고', when='21:00'),
            act('에블린', '추적', '휴고', when='21:10'),
            act('휴고', '추적', '에블린', when='21:20'),
            act('코넬리아', '추적', '휴고', when='21:30'),
            # 에릭↔존: 상호 추적, 모린→존(집중)
            act('에릭', '추적', '존', when='20:00'),
            act('존', '추적', '에릭', when='20:05'),
            act('모린', '추적', '존', when='20:10'),
            # 에두아르도 → 모린 추적
            act('에두아르도', '추적', '모린', when='19:00'),
            # 원쥔·안톤 동선 겹침(같은 포인트)
            act('원쥔', '조사', '보안 경계', when='18:00'),
            act('안톤', '조사', '보안 경계', when='18:30'),
            # 의무실 방문(소견은 슬롯)
            act('에두아르도', '의무실 방문', '의사', when='17:00'),
            act('에릭', '의무실 방문', '의사', when='17:10'),
            act('빌', '의무실 방문', '의사', when='17:20'),
            act('원쥔', '의무실 방문', '의사', when='17:30'),
            # 잡음: 클라라 배급표 교환 지령(일상), 단독 조사
            act('클라라', '부탁지령수행', '클라라', when='16:00'),
            act('데이지', '조사', '병원 대기실', when='15:00'),
        ]
        return {
            'day': 4,
            'actions': actions,
            'investigations': [
                {'캐릭터명': '원쥔', '장소명': '보안 경계', '포인트명': '정문 검문 기록', '결과': '3달러 획득'},
                {'캐릭터명': '안톤', '장소명': '보안 경계', '포인트명': '정문 검문 기록', '결과': '단서 없음'},
                # 데이지의 단독 조사는 결과 없음 → E1
            ],
            'directives': [
                {'대상': '클라라', '내용': '이웃과 배급표를 바꿔 오라',
                 '완료 내용': '다녀왔다', '상태': '완료됨'},
            ],
            'stats': {
                '안톤': {'소지금': '132', '건강': '80', '이성': '60'},
                '에두아르도': {'소지금': '7', '건강': '50', '이성': '40'},
                '휴고': {'소지금': '8', '건강': '60', '이성': '55'},
                '존': {'소지금': '40'}, '에블린': {'소지금': '35'},
                '코넬리아': {'소지금': '30'}, '에릭': {'소지금': '38'},
                '빌': {'소지금': '41'}, '원쥔': {'소지금': '36'},
                '모린': {'소지금': '33'}, '클라라': {'소지금': '39'},
            },
            'roster': ['에블린', '휴고', '코넬리아', '에릭', '존', '모린', '에두아르도',
                       '원쥔', '안톤', '빌', '클라라', '데이지', '엘레노어'],
            'name_by_id': {'edu': '에두아르도', 'eric': '에릭', 'bill': '빌',
                           'wonjun': '원쥔', 'cornelia': '코넬리아'},
            'slots': {
                'story': {'대화': {}, '진행중': {}},
                'doctor': {'소견': {
                    'edu': [{'일차': 4, '소견': '어제 누군가를 찾는다더니 오늘은 검열 탓이라며 진술을 번복했다.'}],
                    'eric': [{'일차': 4, '소견': '그날 밤 기억이 없다며 함구했다.'}],
                    'bill': [{'일차': 4, '소견': '코뮤니즘 소문 조사 얘기에 방어적으로 굴었다.', '처치': '과잉심문'}],
                    'wonjun': [{'일차': 4, '소견': '대장을 열람했다고만 진술.'}],
                }, '진료': {}, '이력': {}},
                'bar': {'플레이': {'cornelia': {'블랙잭': 13}, 'edu': {'블랙잭': 6}}, '지급중단': []},
            },
        }

    def _seeds(self):
        facts = self._facts()
        return ds.extract_seeds(de.normalize(facts), facts)['seeds']

    def test_top_tier_pairs_and_eduardo(self):
        seeds = self._seeds()
        names = [s.subject for s in seeds]
        # 세 최상위 후보가 모두 상위 4위 안에 든다(순위 경향).
        for name in ('에두아르도', '휴고', '에릭'):
            self.assertIsNotNone(seed_for(seeds, name), f"{name} 씨앗이 없다: {names}")
            self.assertLess(rank_of(seeds, name), 4, f"{name} 이 상위권이 아니다: {names}")

    def test_eblin_hugo_is_one_merged_seed(self):
        """비밀 대화 + 상호 추적은 씨앗 1개로 병합된다(§10.4)."""
        seeds = self._seeds()
        s = seed_for(seeds, '휴고')
        self.assertIn('에블린', s.people)
        self.assertIn('휴고', s.people)
        self.assertIn('코넬리아', s.witnesses, "코넬리아가 목격자 후보여야 한다")

    def test_bill_and_wonjun_anton_are_upper(self):
        seeds = self._seeds()
        self.assertIsNotNone(seed_for(seeds, '빌'))
        s = seed_for(seeds, '안톤')
        self.assertIsNotNone(s, "원쥔↔안톤 동선 겹침 씨앗이 있어야 한다")
        self.assertIn('원쥔', s.people)

    def test_cornelia_is_an_independent_seed(self):
        """블랙잭 13회는 임계값 2배 초과 → 독립 씨앗(§6-2)."""
        seeds = self._seeds()
        self.assertIsNotNone(seed_for(seeds, '코넬리아'))

    def test_noise_never_becomes_a_seed(self):
        """§13 반대 목록: 일상 지령·단독 조사는 씨앗에 오르면 안 된다."""
        seeds = self._seeds()
        self.assertIsNone(seed_for(seeds, '클라라'), "배급표 교환 지령은 일상이다")
        self.assertIsNone(seed_for(seeds, '데이지'), "단독 병원 대기실 조사는 이야깃거리가 없다")

    def test_eleanor_is_silent_reference_not_seed(self):
        facts = self._facts()
        res = ds.extract_seeds(de.normalize(facts), facts)
        self.assertIsNone(seed_for(res['seeds'], '엘레노어'))
        self.assertIn('엘레노어', res['reference']['silence'])


# --------------------------------------------------------------------------- #
# §4 정규화
# --------------------------------------------------------------------------- #
class NormalizeTest(unittest.TestCase):
    def _facts(self, actions, **kw):
        base = {'day': 4, 'actions': actions, 'investigations': [], 'directives': [],
                'stats': {}, 'roster': [], 'name_by_id': {}, 'slots': {}}
        base.update(kw)
        return base

    def test_entry_log_never_becomes_event(self):
        """진입 로그는 이벤트로 만들지 않는다(§4). 행동로그엔 애초에 진입이 종류로 없다."""
        events = de.normalize(self._facts([act('A', '조사', '식당')]))
        self.assertTrue(all(e.location != '진입' for e in events))

    def test_talk_is_always_secret(self):
        """이 세계관의 대화는 전부 비밀 대화다."""
        events = de.normalize(self._facts([act('A', '대화', 'B')]))
        talk = next(e for e in events if e.type == de.T_TALK)
        self.assertTrue(talk.secret)

    def test_investigate_target_is_location_not_person(self):
        events = de.normalize(self._facts([act('A', '조사', '보안 경계')]))
        inv = next(e for e in events if e.type == de.T_INVESTIGATE)
        self.assertIsNone(inv.target)
        self.assertEqual(inv.location, '보안 경계')

    def test_investigation_points_are_not_duplicated(self):
        """같은 (행위자,장소)를 두 번 조사해도 포인트 이벤트는 한 벌만."""
        facts = self._facts(
            [act('A', '조사', '보안 경계', when='09:00'),
             act('A', '조사', '보안 경계', when='10:00')],
            investigations=[
                {'캐릭터명': 'A', '장소명': '보안 경계', '포인트명': '초소', '결과': '담배 획득'}])
        invs = [e for e in de.normalize(facts) if e.type == de.T_INVESTIGATE]
        self.assertEqual(sum(1 for e in invs if '담배' in (e.result or '')), 1)

    def test_gambling_count_comes_from_slot(self):
        facts = self._facts([], name_by_id={'u': 'A'},
                            slots={'bar': {'플레이': {'u': {'블랙잭': 6, '슬롯머신': 2}}, '지급중단': []}})
        g = next(e for e in de.normalize(facts) if e.type == de.T_GAMBLE)
        self.assertEqual(g.count, 8)


# --------------------------------------------------------------------------- #
# §5·§6 필터·점수
# --------------------------------------------------------------------------- #
class FilterScoreTest(unittest.TestCase):
    def _facts(self, actions, **kw):
        base = {'day': 4, 'actions': actions, 'investigations': [], 'directives': [],
                'stats': {}, 'roster': [], 'name_by_id': {}, 'slots': {}}
        base.update(kw)
        return base

    def _seeds(self, facts):
        return ds.extract_seeds(de.normalize(facts), facts)['seeds']

    def test_single_investigation_is_not_a_seed(self):
        """E1: 결과 없는 단독 조사는 어떤 신호에도 안 걸린다."""
        self.assertEqual(self._seeds(self._facts([act('A', '조사', '식당')])), [])

    def test_bare_overlap_is_below_threshold(self):
        """I8 단독(+3)은 MIN_SEED_SCORE(4) 미만 — 단일 약신호는 씨앗이 아니다(§10.5)."""
        facts = self._facts(
            [act('A', '조사', '보안 경계'), act('B', '조사', '보안 경계')],
            investigations=[
                {'캐릭터명': 'A', '장소명': '보안 경계', '포인트명': '정문', '결과': ''},
                {'캐릭터명': 'B', '장소명': '보안 경계', '포인트명': '정문', '결과': ''}])
        self.assertEqual(self._seeds(facts), [])

    def test_overlap_lifts_a_seed_when_combined(self):
        """I8은 가산 신호다: 동선 겹침(+3)에 수치 이상(+2)이 붙으면 씨앗이 된다."""
        facts = self._facts(
            [act('A', '조사', '보안 경계'), act('B', '조사', '보안 경계')],
            investigations=[
                {'캐릭터명': 'A', '장소명': '보안 경계', '포인트명': '정문', '결과': ''},
                {'캐릭터명': 'B', '장소명': '보안 경계', '포인트명': '정문', '결과': ''}],
            name_by_id={'u': 'A'},
            slots={'bar': {'플레이': {'u': {'블랙잭': 6}}, '지급중단': []}})
        s = self._seeds(facts)
        self.assertIsNotNone(seed_for(s, 'A'))
        self.assertIn('B', seed_for(s, 'A').people)

    def test_secret_keyword_keeps_directive_while_plain_is_dropped(self):
        """E4: 은밀 키워드가 있으면 지령이 살아 다른 신호와 합쳐 씨앗이 되고,
        없으면 빈 보고 지령은 통째로 빠져 남은 신호가 문턱을 못 넘는다."""
        def facts(body):
            return self._facts(
                [act('A', '부탁지령수행', 'A')],
                directives=[{'대상': 'A', '내용': body, '완료 내용': '@STORY', '상태': '완료됨'}],
                name_by_id={'u': 'A'},
                slots={'bar': {'플레이': {'u': {'블랙잭': 6}}, '지급중단': []}})
        secret = facts('이건 적지 말아 주게. 성냥갑을 두고 오라.')   # 은밀 지령(+3) + 도박(+2)=5
        plain = facts('체스 상대나 해 주게')                          # 지령 탈락 → 도박(+2)만 <4
        self.assertIsNotNone(seed_for(self._seeds(secret), 'A'))
        self.assertIsNone(seed_for(self._seeds(plain), 'A'))

    def test_gambling_below_threshold_is_not_anomaly(self):
        """E5: 임계값(5) 미만 도박은 이상이 아니다."""
        facts = self._facts([], name_by_id={'u': 'A'},
                            slots={'bar': {'플레이': {'u': {'블랙잭': 3}}, '지급중단': []}})
        self.assertEqual(self._seeds(facts), [])

    def test_contradiction_scores_high(self):
        """I3 진술 모순은 +4 — 단독으로도 씨앗 문턱을 넘는다."""
        facts = self._facts(
            [act('A', '의무실 방문', '의사')], name_by_id={'u': 'A'},
            slots={'doctor': {'소견': {'u': [{'일차': 4, '소견': '진술을 번복했다'}]}, '진료': {}, '이력': {}}})
        s = self._seeds(facts)
        self.assertIsNotNone(seed_for(s, 'A'))
        self.assertGreaterEqual(seed_for(s, 'A').score, 4)

    def test_one_person_does_not_dominate(self):
        """§11: union-find 로 한 사람은 한 씨앗에만 — 도배가 구조적으로 막힌다."""
        facts = self._facts([
            act('A', '대화', 'B'), act('B', '추적', 'A'),
            act('A', '추적', 'C'), act('C', '추적', 'A'),
        ])
        seeds = self._seeds(facts)
        appearances = [s for s in seeds if 'A' in s.people]
        self.assertEqual(len(appearances), 1, "A가 여러 씨앗에 흩어지면 안 된다")


# --------------------------------------------------------------------------- #
# §8 렌더러
# --------------------------------------------------------------------------- #
class BriefRenderTest(unittest.TestCase):
    def _res(self, seeds, reference=None, stats=None):
        return {'seeds': seeds, 'reference': reference or {}, 'stats': stats or {}}

    def _seed(self, **kw):
        base = dict(subject='A', people=['A'], score=5, signals=[], evidence=['A가 뭔가 했다'],
                    witnesses=[], angle='')
        base.update(kw)
        return ds.Seed(**base)

    def test_angle_line_omitted_when_empty(self):
        text = db.render_brief(4, self._res([self._seed(angle='')]))
        self.assertNotIn('각도:', text)

    def test_angle_line_present_when_set(self):
        text = db.render_brief(4, self._res([self._seed(angle='밤일을 물으면 말이 바뀐다더라')]))
        self.assertIn('각도: 밤일을 물으면 말이 바뀐다더라', text)

    def test_witness_line_omitted_when_none(self):
        self.assertNotIn('목격자', db.render_brief(4, self._res([self._seed(witnesses=[])])))

    def test_empty_day_is_explicit(self):
        """씨앗 0개인 날도 빈 보고가 아니라 명시한다(§11)."""
        text = db.render_brief(4, self._res([], stats={'incidents': 0}))
        self.assertIn('소문 씨앗 없음', text)

    def test_no_decoration(self):
        text = db.render_brief(4, self._res([self._seed()]))
        self.assertNotIn('**', text)
        self.assertNotIn('#', text)

    def test_brief_defangs_mentions(self):
        """소견·각도의 계정 태그가 그대로 나가면 안 된다."""
        text = db.render_brief(4, self._res([self._seed(evidence=['@avet 이 수상하다'])]))
        self.assertFalse(mention_guard.has_live_mention(text))


# --------------------------------------------------------------------------- #
# 파이프라인(run_daily_digest → 브리핑)
# --------------------------------------------------------------------------- #
class BriefPipelineTest(unittest.TestCase):
    class _Api:
        def __init__(self):
            self.posts = []

        def status_post(self, status, visibility=None, in_reply_to_id=None):
            self.posts.append({'text': status, 'vis': visibility, 'reply_to': in_reply_to_id})
            return {'id': f'id{len(self.posts)}'}

    def _cfg(self, **over):
        base = {'DIGEST_ENABLED': True, 'DIGEST_BRIEF': True, 'DIGEST_AI_SEEDS': False,
                'DIGEST_LEGACY_REPORT': False, 'DIGEST_RECIPIENT_ID': 'NOTICE',
                'DIGEST_BRIEF_LIMIT': 2000, 'SYSTEM_ADMIN_ID': 'NOTICE'}
        base.update(over)
        return patch.object(daily_digest, '_cfg', side_effect=lambda k, d: base.get(k, d))

    def _managers(self, actions):
        from unittest.mock import MagicMock
        sysm = MagicMock()
        sysm.get_worksheet_data.side_effect = lambda s, use_cache=False: (
            actions if s == '행동로그' else [])
        main = MagicMock()
        main.get_worksheet_data.side_effect = lambda s, use_cache=False: (
            [{'이름': n} for n in ('데보라', '한참', '클라라')] if s == '명단' else [])
        inv = MagicMock()
        inv.get_worksheet_data.side_effect = lambda s, use_cache=False: []
        return main, sysm, inv

    def test_brief_is_sent_and_titled(self):
        main, sysm, inv = self._managers([
            act('데보라', '대화', '한참', when='21:00'),
            act('한참', '추적', '데보라', when='21:10')])
        api = self._Api()
        with self._cfg():
            result = daily_digest.run_daily_digest(main, sysm, api, inv, day=4)
        self.assertEqual(result['sent'], result['total'])
        self.assertTrue(api.posts)
        self.assertIn('[소문 브리핑] 4일차', api.posts[0]['text'])

    def test_pipeline_writes_nothing_to_sheets(self):
        main, sysm, inv = self._managers([act('데보라', '추적', '한참')])
        api = self._Api()
        with self._cfg():
            daily_digest.run_daily_digest(main, sysm, api, inv, day=4)
        for m in (main, sysm, inv):
            m.append_row.assert_not_called()
            m.update_cell.assert_not_called()

    def test_no_ai_angle_on_empty_day(self):
        main, sysm, inv = self._managers([])
        api = self._Api()
        with self._cfg(DIGEST_AI_SEEDS=True), \
             patch('utils.ai_client.rumor_angle') as angle:
            daily_digest.run_daily_digest(main, sysm, api, inv, day=4)
        angle.assert_not_called()

    def test_angle_failure_does_not_block_send(self):
        main, sysm, inv = self._managers([
            act('데보라', '대화', '한참', when='21:00'),
            act('한참', '추적', '데보라', when='21:10')])
        api = self._Api()
        with self._cfg(DIGEST_AI_SEEDS=True), \
             patch('utils.ai_client.rumor_angle', side_effect=RuntimeError('API 죽음')):
            result = daily_digest.run_daily_digest(main, sysm, api, inv, day=4)
        self.assertEqual(result['sent'], result['total'])
        self.assertTrue(api.posts)

    def test_ai_angle_with_mention_is_defanged_end_to_end(self):
        """AI 각도가 계정 태그를 뱉어도 발송된 통에는 수신자 멘션 하나만 살아 있어야 한다.

        브리핑 본문은 원문(소견·지령)을 안 싣지만, AI 각도만은 자유 텍스트라
        유일한 멘션 유입 경로다. render(defang) + send(defang_all) 2중으로 막힌다.
        """
        main, sysm, inv = self._managers([
            act('데보라', '대화', '한참', when='21:00'),
            act('한참', '추적', '데보라', when='21:10')])
        api = self._Api()
        with self._cfg(DIGEST_AI_SEEDS=True), \
             patch('utils.ai_client.rumor_angle', return_value='@ghost 이 밤마다 수상하다더라'):
            daily_digest.run_daily_digest(main, sysm, api, inv, day=4)
        self.assertTrue(api.posts)
        for p in api.posts:
            # 수신자 멘션('@NOTICE')만 살아 있어야 한다 — 각도의 @ghost 는 죽어야 한다.
            self.assertEqual(p['text'].count('@'), 1, f"수신자 외 멘션이 살았다: {p['text']}")
            self.assertNotIn('@ghost', p['text'])
            self.assertIn('＠ghost', p['text'], "각도의 태그가 전각으로 무력화되어야 한다")


if __name__ == '__main__':
    unittest.main()

"""utils/slot_reader.py 단위 테스트 — 다른 슬롯의 JSON 읽기.

일일보고는 @SYSTEM 에서 도는데, 소문 재료의 상당수는 다른 슬롯의 로컬 JSON 에만 있다:
@STORY 의 2차 대화 상대, @DOCTOR 의 소견·대화록, @BAR 의 게임별 횟수.
`json_store.slot_path()` 는 현재 프로세스의 슬롯만 가리키므로 경로를 직접 만들어야 한다.

여기서 지켜야 할 성질:
1. 읽기 전용 — 남의 슬롯 파일을 절대 건드리지 않는다.
2. 날짜 필터 — purge_stale 은 @SYSTEM 에서만 도니 다른 슬롯엔 지난 스탬프가 쌓인다.
3. 파일이 없어도 죽지 않는다 — state/ 는 봇이 한 번도 안 돌았으면 아예 없다.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils import slot_reader

TODAY = '2026-07-16'
YESTERDAY = '2026-07-15'


def _stamp(v, day=TODAY):
    return {'v': v, 'd': day}


class _SlotFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='slotreader_')
        self._base = patch.object(slot_reader, '_base_dir', return_value=self.tmp)
        self._base.start()
        self._today = patch.object(slot_reader, '_today_str', return_value=TODAY)
        self._today.start()

    def tearDown(self):
        self._today.stop()
        self._base.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, slot, name, data):
        d = os.path.join(self.tmp, 'state', slot)
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, name)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
        return path


class PathTest(_SlotFixture):
    def test_points_at_the_named_slot(self):
        p = slot_reader.slot_file('DOCTOR', 'doctor_charts.json')
        self.assertEqual(p, os.path.join(self.tmp, 'state', 'DOCTOR', 'doctor_charts.json'))

    def test_does_not_follow_current_slot(self):
        """slot_path() 와 달리 config.BOT_NAME 에 끌려가면 안 된다."""
        from config.settings import config
        with patch.object(config, 'BOT_NAME', 'SYSTEM'):
            p = slot_reader.slot_file('BAR', 'game_state.json')
        self.assertIn(os.path.join('state', 'BAR'), p)


class MissingFileTest(_SlotFixture):
    def test_absent_file_is_empty_not_an_error(self):
        """state/ 는 봇이 한 번도 안 돌았으면 아예 없다."""
        errors = []
        self.assertEqual(slot_reader.read_json(
            slot_reader.slot_file('DOCTOR', 'nope.json'), errors), {})
        self.assertEqual(errors, [], "없는 파일은 오류가 아니다")

    def test_corrupt_file_is_absorbed_and_reported(self):
        path = os.path.join(self.tmp, 'state', 'BAR')
        os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, 'game_state.json'), 'w', encoding='utf-8') as f:
            f.write('{ 깨진 json')
        errors = []
        self.assertEqual(slot_reader.read_game_state('BAR', TODAY, errors), {})
        self.assertTrue(errors)

    def test_non_dict_toplevel_ignored(self):
        self.write('BAR', 'game_state.json', [])  # type: ignore[arg-type]
        self.assertEqual(slot_reader.read_game_state('BAR', TODAY), {})

    def test_all_slots_missing(self):
        facts = slot_reader.fetch_slot_facts(day=3)
        self.assertEqual(facts['story']['대화'], {})
        self.assertEqual(facts['doctor']['소견'], {})
        self.assertEqual(facts['bar']['플레이'], {})


class DateStampTest(_SlotFixture):
    def test_yesterday_is_filtered_out(self):
        """purge_stale 은 @SYSTEM 에서만 돈다 — 남의 슬롯엔 지난 스탬프가 쌓인다."""
        self.write('BAR', 'game_state.json', {
            'clara': {'오늘슬롯': _stamp(9, YESTERDAY), '오늘블랙잭': _stamp(2)},
        })
        plays = slot_reader.read_bar(TODAY)['플레이']
        self.assertEqual(plays, {'clara': {'블랙잭': 2}}, "어제 슬롯 기록이 살아남았다")

    def test_unstamped_keys_survive(self):
        """의무실방문횟수 같은 누적 키는 스탬프가 없다."""
        self.write('DOCTOR', 'game_state.json', {'hancham': {'의무실방문횟수': 4}})
        hist = slot_reader.read_doctor(day=3, today=TODAY)['이력']
        self.assertEqual(hist['hancham']['누적방문'], 4)

    def test_no_today_means_no_filter(self):
        self.write('BAR', 'game_state.json', {'clara': {'오늘슬롯': _stamp(9, YESTERDAY)}})
        self.assertEqual(slot_reader.read_bar(today=None)['플레이'],
                         {'clara': {'슬롯머신': 9}})


class StoryTest(_SlotFixture):
    def test_secondary_partners_recovered(self):
        """행동로그엔 최초 상대만 남는다. 나머지는 이 JSON 이 유일한 기록이다."""
        self.write('STORY', 'game_state.json', {
            'deborah': {'오늘대화상대': _stamp('한참, 클라라, 휴고')},
        })
        talks = slot_reader.read_story(TODAY)['대화']
        self.assertEqual(talks['deborah']['상대'], ['한참', '클라라', '휴고'])

    def test_partner_list_tolerates_spacing(self):
        self.write('STORY', 'game_state.json', {
            'deborah': {'오늘대화상대': _stamp('한참,클라라 ,  휴고 ,')},
        })
        self.assertEqual(slot_reader.read_story(TODAY)['대화']['deborah']['상대'],
                         ['한참', '클라라', '휴고'])

    def test_consumed_flags(self):
        self.write('STORY', 'game_state.json', {
            'wonjun': {'오늘대화여부': _stamp(1), '오늘고발': _stamp(1)},
        })
        entry = slot_reader.read_story(TODAY)['대화']['wonjun']
        self.assertTrue(entry['대화소진'])
        self.assertTrue(entry['고발소진'])

    def test_live_sessions(self):
        self.write('STORY', 'talk_sessions.json', {
            'deborah': {'primary_partner_name': '한참',
                        'per_partner': {'한참': 3, '클라라': 2}, 'total': 5},
        })
        live = slot_reader.read_story(TODAY)['진행중']['deborah']
        self.assertEqual(live['상대별횟수'], {'한참': 3, '클라라': 2})
        self.assertEqual(live['총횟수'], 5)


class DoctorTest(_SlotFixture):
    def test_only_todays_notes(self):
        self.write('DOCTOR', 'doctor_charts.json', {
            'hancham': [{'일차': 2, '소견': '어제'}, {'일차': 3, '소견': '오늘'}],
        })
        notes = slot_reader.read_doctor(day=3, today=TODAY)['소견']['hancham']
        self.assertEqual([n['소견'] for n in notes], ['오늘'])

    def test_day_compare_tolerates_types(self):
        self.write('DOCTOR', 'doctor_charts.json', {
            'hancham': [{'일차': '3.0', '소견': '오늘'}],
        })
        self.assertIn('hancham', slot_reader.read_doctor(day=3, today=TODAY)['소견'])

    def test_transcript_is_read(self):
        self.write('DOCTOR', 'doctor_sessions.json', {
            'hancham': {'day': 3, 'turns': 2, 'finalized': True, 'active': False,
                        'history': [{'role': 'user', 'content': '잠을 못 자요'}]},
        })
        visit = slot_reader.read_doctor(day=3, today=TODAY)['진료']['hancham']
        self.assertEqual(visit['대화록'][0]['content'], '잠을 못 자요')
        self.assertFalse(visit['진행중'])

    def test_other_day_visit_excluded(self):
        self.write('DOCTOR', 'doctor_sessions.json', {
            'ghost': {'day': 2, 'history': [], 'finalized': True},
        })
        self.assertEqual(slot_reader.read_doctor(day=3, today=TODAY)['진료'], {})

    def test_last_visit_snapshot(self):
        """관리 시트는 이미 덮어썼다 — 그때의 수치는 여기에만 있다."""
        self.write('DOCTOR', 'game_state.json', {
            'hancham': {'의무실지난방문': {'일차': 1, '건강': 92, '이성': 88}},
        })
        hist = slot_reader.read_doctor(day=3, today=TODAY)['이력']['hancham']
        self.assertEqual(hist['지난방문']['건강'], 92)


class BarTest(_SlotFixture):
    def test_per_game_counts(self):
        self.write('BAR', 'game_state.json', {
            'clara': {'오늘슬롯': _stamp(14), '오늘블랙잭': _stamp(3), '오늘크랩스': _stamp(0)},
        })
        self.assertEqual(slot_reader.read_bar(TODAY)['플레이'],
                         {'clara': {'슬롯머신': 14, '블랙잭': 3}})

    def test_orphan_payout_surfaced(self):
        """지급 도중 죽은 판 = 재화 사고. GM 이 알아야 한다."""
        self.write('BAR', 'blackjack_sessions.json', {
            'clara': {'bet': 100, 'payout_attempted': 250},
            'hugo': {'bet': 10, 'payout_attempted': None},
        })
        orphans = slot_reader.read_bar(TODAY)['지급중단']
        self.assertEqual(len(orphans), 1)
        self.assertEqual(orphans[0]['아이디'], 'clara')

    def test_garbage_count_does_not_crash(self):
        self.write('BAR', 'game_state.json', {'clara': {'오늘슬롯': _stamp('이상한값')}})
        self.assertEqual(slot_reader.read_bar(TODAY)['플레이'], {})


class ReadOnlyTest(_SlotFixture):
    def test_never_writes_to_foreign_slots(self):
        """남의 스토어에 쓰면 그 슬롯이 그 사이 쓴 걸 전부 날린다."""
        paths = [
            self.write('STORY', 'game_state.json', {'deborah': {'오늘대화상대': _stamp('한참')}}),
            self.write('STORY', 'talk_sessions.json', {'deborah': {'total': 1}}),
            self.write('DOCTOR', 'doctor_charts.json', {'hancham': [{'일차': 3, '소견': 'x'}]}),
            self.write('DOCTOR', 'doctor_sessions.json', {'hancham': {'day': 3, 'history': []}}),
            self.write('DOCTOR', 'game_state.json', {'hancham': {'의무실방문횟수': 1}}),
            self.write('BAR', 'game_state.json', {'clara': {'오늘슬롯': _stamp(3)}}),
            self.write('BAR', 'blackjack_sessions.json', {'clara': {'bet': 1}}),
        ]
        before = {p: (os.path.getmtime(p), open(p, encoding='utf-8').read()) for p in paths}

        slot_reader.fetch_slot_facts(day=3)

        for p in paths:
            mtime, content = before[p]
            self.assertEqual(open(p, encoding='utf-8').read(), content,
                             f"{os.path.basename(p)} 내용이 바뀌었다")
            self.assertEqual(os.path.getmtime(p), mtime,
                             f"{os.path.basename(p)} 이 다시 쓰였다")

    def test_town_is_not_read(self):
        """@TOWN 은 로컬 JSON 에 고유한 게 없다 — 조사 데이터는 전부 시트에 있다."""
        facts = slot_reader.fetch_slot_facts(day=3)
        self.assertNotIn('town', facts)


class FetchAllTest(_SlotFixture):
    def test_one_broken_file_does_not_kill_the_rest(self):
        self.write('BAR', 'game_state.json', {'clara': {'오늘슬롯': _stamp(3)}})
        d = os.path.join(self.tmp, 'state', 'DOCTOR')
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, 'doctor_charts.json'), 'w', encoding='utf-8') as f:
            f.write('깨짐')

        facts = slot_reader.fetch_slot_facts(day=3)
        self.assertEqual(facts['bar']['플레이'], {'clara': {'슬롯머신': 3}},
                         "한 파일이 깨졌다고 다른 슬롯까지 잃으면 안 된다")
        self.assertTrue(facts['errors'])


if __name__ == '__main__':
    unittest.main()

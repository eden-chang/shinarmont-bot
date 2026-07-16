"""utils/rumor.py 단위 테스트 (페이크 매니저 주입, DM 전송기 스텁)."""

import os
import sys
import random
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from utils import rumor


class FakeSheet:
    """get_worksheet_data / append_row / batch_update_cells 페이크."""

    def __init__(self, sheets):
        # sheets: {name: [header_list, desc_list, *data_rows]}
        self._sheets = sheets
        self.appended = {}       # name -> list of appended value-lists
        self.batch_calls = {}    # name -> list of (row,col,value)

    def get_worksheet_data(self, name, use_cache=False):
        raw = self._sheets.get(name)
        if not raw or len(raw) < 3:
            return []
        header = raw[0]
        records = []
        for idx, row in enumerate(raw[2:]):
            if not any(row):
                continue
            rec = dict(zip(header, row))
            rec['_row_number'] = idx + 3
            records.append(rec)
        return records

    def append_row(self, name, values):
        self.appended.setdefault(name, []).append(values)
        return True

    def batch_update_cells(self, name, updates):
        self.batch_calls.setdefault(name, []).extend(updates)
        # 메모리 시트에도 반영(같은 실행 내 재조회 정확성)
        raw = self._sheets.get(name)
        for row, col, value in updates:
            while len(raw) <= row - 1:
                raw.append([''] * len(raw[0]))
            line = raw[row - 1]
            while len(line) < col:
                line.append('')
            line[col - 1] = value
        return True


class RumorTestBase(unittest.TestCase):
    def setUp(self):
        random.seed(1234)
        self._sent = []
        # dm_sender.queue_dm 스텁
        import utils.dm_sender as dm_sender
        self._orig_queue = dm_sender.queue_dm
        dm_sender.queue_dm = lambda rid, msg: self._sent.append((rid, msg))

    def tearDown(self):
        import utils.dm_sender as dm_sender
        dm_sender.queue_dm = self._orig_queue


class SendWeeklyRumorTest(RumorTestBase):
    def _managers(self, rumor_state='확정'):
        system = FakeSheet({
            '소문': [
                ['일차', '출처', '내용', '상태'],
                ['설명', '설명', '설명', '설명'],
                ['3', '수동', '누군가 밤에 사라졌다', rumor_state],
            ],
            '행동로그': [
                ['일시', '일차', '종류', '행위자', '대상', '요약', '상세', '능력치변동', '소문화'],
                ['설명'] * 9,
                ['2026-07-19', '3', '교류', '가', '나', '누군가 밤에 사라졌다', '', '', ''],
            ],
        })
        base = FakeSheet({
            '명단': [
                ['아이디', '이름'],
                ['설명', '설명'],
                ['u1', '가나'],
                ['u2', '다라'],
                ['u3', '마바'],
                ['u4', '사아'],
                ['u5', '자차'],
            ],
            '관리': [
                ['아이디', '이성'],
                ['설명', '설명'],
                ['u1', '80'],
                ['u2', '10'],
                ['u3', '50'],
                ['u4', '90'],
                ['u5', '30'],
            ],
        })
        return system, base

    def test_confirmed_rumor_sent_and_state_updated(self):
        system, base = self._managers()
        result = rumor.send_weekly_rumor(base, system, api=None)
        self.assertEqual(result['confirmed'], 1)
        self.assertEqual(result['sent'], 1)
        self.assertGreaterEqual(result['recipients'], 3)
        self.assertLessEqual(result['recipients'], 5)
        # 발송된 DM 개수 == recipients
        self.assertEqual(len(self._sent), result['recipients'])
        # 접두 톤 포함
        for _rid, msg in self._sent:
            self.assertTrue(msg.startswith(rumor.RUMOR_PREFIX))
        # 소문 상태 -> 발송 (4열)
        updates = system.batch_calls.get('소문', [])
        self.assertTrue(any(col == 4 and val == '발송' for _r, col, val in updates))
        # 행동로그 소문화=O (9열)
        log_updates = system.batch_calls.get('행동로그', [])
        self.assertTrue(any(col == 9 and val == 'O' for _r, col, val in log_updates))

    def test_no_confirmed_no_send(self):
        system, base = self._managers(rumor_state='후보')
        result = rumor.send_weekly_rumor(base, system, api=None)
        self.assertEqual(result['confirmed'], 0)
        self.assertEqual(result['sent'], 0)
        self.assertEqual(len(self._sent), 0)

    def test_none_system_manager_safe(self):
        result = rumor.send_weekly_rumor(None, None, api=None)
        self.assertEqual(result['sent'], 0)


class ProposeCandidatesTest(RumorTestBase):
    def _system(self, existing_content=None):
        rumor_rows = [
            ['일차', '출처', '내용', '상태'],
            ['설명', '설명', '설명', '설명'],
        ]
        if existing_content:
            rumor_rows.append(['1', '자동', existing_content, '후보'])
        return FakeSheet({
            '소문': rumor_rows,
            '행동로그': [
                ['일시', '일차', '종류', '행위자', '대상', '요약', '상세', '능력치변동', '소문화'],
                ['설명'] * 9,
                ['2026-07-17', '1', '추적', '가', '나', '수상한 발자국 발견', '', '', ''],
                ['2026-07-17', '1', '고발', '다', '라', '거짓 증언 정황', '', '', ''],
                ['2026-07-18', '2', '교류', '마', '바', '이미 소문난 사건', '', '', 'O'],  # 소문화 표시됨
            ],
        })

    def test_appends_candidates(self):
        system = self._system()
        result = rumor.propose_candidates(system, sample_min=1, sample_max=2)
        # 소문화 미표시 후보 2건 존재
        self.assertEqual(result['candidates_available'], 2)
        self.assertGreaterEqual(result['appended'], 1)
        appended = system.appended.get('소문', [])
        for row in appended:
            # 일차, 출처, 내용, 상태, 원본행 (5열)
            self.assertEqual(len(row), 5)
            self.assertEqual(row[1], '자동')   # 출처
            self.assertEqual(row[3], '후보')   # 상태
            # 원본행이 있어야 GM이 '내용'을 다듬어도 역링크가 살아 있다.
            self.assertIsInstance(row[4], int, "원본행(행동로그 행 번호)이 비었다")

    def test_dedup_existing_content(self):
        system = self._system(existing_content='수상한 발자국 발견')
        result = rumor.propose_candidates(system, sample_min=5, sample_max=5)
        # 중복 1건 제외 -> 1건만 후보 대상
        self.assertEqual(result['candidates_available'], 1)
        appended = system.appended.get('소문', [])
        self.assertEqual(len(appended), 1)
        self.assertEqual(appended[0][2], '거짓 증언 정황')

    def test_none_system_manager_safe(self):
        result = rumor.propose_candidates(None)
        self.assertEqual(result['appended'], 0)


if __name__ == '__main__':
    unittest.main()

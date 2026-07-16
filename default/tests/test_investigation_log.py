"""utils/investigation_log.py — 로그 시트 단위 테스트."""

import os
import re
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from tests.investigation_fixtures import investigation_sheets, log_row
from utils.investigation_log import (
    ENTRY_RESULT, KST, append_entry, append_investigation,
    count_today_investigations, format_summary, last_location_today, now_stamp,
    parse_stamp,
)

TODAY = datetime.now(KST)
YESTERDAY = TODAY - timedelta(days=1)


def stamp(dt, hour=12, minute=0):
    return dt.strftime('%m.%d ') + f'{hour:02d}:{minute:02d}'


class StampTest(unittest.TestCase):
    def test_now_stamp_format(self):
        """MM.DD HH:MM (예: 07.20 21:14)"""
        self.assertRegex(now_stamp(), r'^\d{2}\.\d{2} \d{2}:\d{2}$')

    def test_now_stamp_uses_given_time(self):
        dt = KST.localize(datetime(2026, 7, 20, 21, 14))
        self.assertEqual(now_stamp(dt), '07.20 21:14')

    def test_parse_roundtrip(self):
        parsed = parse_stamp('07.20 21:14')
        self.assertIsNotNone(parsed)
        self.assertEqual((parsed.month, parsed.day, parsed.hour, parsed.minute),
                         (7, 20, 21, 14))

    def test_parse_assumes_game_year(self):
        """연도가 없으므로 GAME_START_DATE의 연도(2026)로 가정한다."""
        self.assertEqual(parse_stamp('07.20 21:14').year, 2026)

    def test_parse_garbage_returns_none(self):
        self.assertIsNone(parse_stamp('어제'))
        self.assertIsNone(parse_stamp(''))
        self.assertIsNone(parse_stamp(None))


class AppendTest(unittest.TestCase):
    def setUp(self):
        self.fake = investigation_sheets()

    def test_append_entry_leaves_point_blank(self):
        self.assertTrue(append_entry(self.fake, '한참', '중앙 광장'))
        name, values = self.fake.appended[0]
        self.assertEqual(name, '로그')
        self.assertEqual(values[1:], ['한참', '중앙 광장', '', ENTRY_RESULT])
        self.assertRegex(values[0], r'^\d{2}\.\d{2} \d{2}:\d{2}$')

    def test_append_investigation_records_summary(self):
        append_investigation(self.fake, '한참', '연구소', '전압계', '이성 -2')
        _, values = self.fake.appended[0]
        self.assertEqual(values[1:], ['한참', '연구소', '전압계', '이성 -2'])

    def test_append_investigation_without_changes(self):
        append_investigation(self.fake, '한참', '연구소', '전압계', '')
        _, values = self.fake.appended[0]
        self.assertEqual(values[4], '조사')

    def test_write_failure_returns_false(self):
        self.fake.write_ok = False
        self.assertFalse(append_entry(self.fake, '한참', '중앙 광장'))

    def test_no_manager_is_safe(self):
        self.assertFalse(append_entry(None, '한참', '중앙 광장'))


class CountTest(unittest.TestCase):
    """§5.2 일일 조사 횟수 판정 — 성공한 [조사]만 센다."""

    def _fake(self, rows):
        return investigation_sheets(logs=rows)

    def test_counts_only_investigations(self):
        fake = self._fake([
            log_row(stamp(TODAY), '한참', '중앙 광장', '', ENTRY_RESULT),
            log_row(stamp(TODAY), '한참', '중앙 광장', '수위계', '조사'),
            log_row(stamp(TODAY), '한참', '중앙 광장', '게시판', '담배 1 획득'),
        ])
        self.assertEqual(count_today_investigations(fake, '한참'), 2)

    def test_entry_rows_not_counted(self):
        fake = self._fake([
            log_row(stamp(TODAY), '한참', '중앙 광장', '', ENTRY_RESULT),
            log_row(stamp(TODAY), '한참', '연구소', '', ENTRY_RESULT),
        ])
        self.assertEqual(count_today_investigations(fake, '한참'), 0)

    def test_yesterday_not_counted(self):
        fake = self._fake([
            log_row(stamp(YESTERDAY), '한참', '중앙 광장', '수위계', '조사'),
            log_row(stamp(TODAY), '한참', '중앙 광장', '게시판', '조사'),
        ])
        self.assertEqual(count_today_investigations(fake, '한참'), 1)

    def test_other_characters_not_counted(self):
        fake = self._fake([
            log_row(stamp(TODAY), '다른이', '중앙 광장', '수위계', '조사'),
            log_row(stamp(TODAY), '한참', '중앙 광장', '게시판', '조사'),
        ])
        self.assertEqual(count_today_investigations(fake, '한참'), 1)

    def test_empty_log(self):
        self.assertEqual(count_today_investigations(self._fake([]), '한참'), 0)

    def test_unparseable_stamp_not_counted(self):
        fake = self._fake([log_row('언젠가', '한참', '중앙 광장', '수위계', '조사')])
        self.assertEqual(count_today_investigations(fake, '한참'), 0)


class RestoreTest(unittest.TestCase):
    """§5.1 재시작 시 현재 위치 복원."""

    def _fake(self, rows):
        return investigation_sheets(logs=rows)

    def test_returns_latest_entry(self):
        fake = self._fake([
            log_row(stamp(TODAY, 10), '한참', '중앙 광장', '', ENTRY_RESULT),
            log_row(stamp(TODAY, 11), '한참', '연구소', '', ENTRY_RESULT),
        ])
        self.assertEqual(last_location_today(fake, '한참'), '연구소')

    def test_investigation_rows_do_not_move_location(self):
        """조사 기록에도 장소명이 있지만 위치는 진입 기록으로만 정한다."""
        fake = self._fake([
            log_row(stamp(TODAY, 10), '한참', '연구소', '', ENTRY_RESULT),
            log_row(stamp(TODAY, 11), '한참', '연구소', '전압계', '조사'),
        ])
        self.assertEqual(last_location_today(fake, '한참'), '연구소')

    def test_yesterday_entry_not_restored(self):
        """일차가 바뀌면 위치는 초기화된다."""
        fake = self._fake([log_row(stamp(YESTERDAY), '한참', '연구소', '', ENTRY_RESULT)])
        self.assertIsNone(last_location_today(fake, '한참'))

    def test_other_character_ignored(self):
        fake = self._fake([log_row(stamp(TODAY), '다른이', '연구소', '', ENTRY_RESULT)])
        self.assertIsNone(last_location_today(fake, '한참'))

    def test_no_entries(self):
        self.assertIsNone(last_location_today(self._fake([]), '한참'))


class SummaryTest(unittest.TestCase):
    def test_joins_changes(self):
        self.assertEqual(format_summary(['재화 -5', '이성 +1']), '재화 -5, 이성 +1')

    def test_single(self):
        self.assertEqual(format_summary(['담배 1 획득']), '담배 1 획득')

    def test_empty_is_investigation(self):
        self.assertEqual(format_summary([]), '조사')
        self.assertEqual(format_summary(None), '조사')


if __name__ == '__main__':
    unittest.main()

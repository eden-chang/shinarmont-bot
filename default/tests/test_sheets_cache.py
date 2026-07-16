"""utils/sheets_operations.py 데이터/헤더 캐시 검증.

get_worksheet_data(use_cache=True)가 실제로 API 호출(get_all_values)을 줄이고,
쓰기(append/update/batch) 후 무효화되며, use_cache=False는 항상 최신을 읽는지 확인.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils.sheets_operations import SheetsManager


class _FakeWorksheet:
    def __init__(self):
        self.row_count = 10
        self.get_all_values_calls = 0
        self.row_values_calls = 0
        self._values = [
            ['아이디', '이름', '소지금'],       # 1행 헤더
            ['설명', '설명', '설명'],            # 2행 설명
            ['alice', '엘리스', '100'],          # 3행~ 데이터
            ['bob', '밥', '50'],
        ]

    def get_all_values(self):
        self.get_all_values_calls += 1
        return self._values

    def row_values(self, n):
        self.row_values_calls += 1
        return self._values[n - 1]

    def append_row(self, values):
        pass

    def update_cell(self, r, c, v):
        pass

    def batch_update(self, cells, value_input_option='RAW'):
        pass


def _manager(ws):
    m = SheetsManager.__new__(SheetsManager)   # __init__(연결) 우회
    m.purpose = None            # 크레덴셜 풀 미사용(기존 동작) — __init__과 동일하게 맞춘다
    m._credential_name = None
    m._worksheets_cache = {}
    m._data_cache = {}
    m._header_cache = {}
    m._header_warned = set()
    m._data_cache_ttl = 90.0
    m.get_worksheet = lambda name, use_cache=True: ws   # 워크시트 조회 스텁
    return m


class SheetsDataCacheTest(unittest.TestCase):
    def test_use_cache_true_reads_api_once(self):
        ws = _FakeWorksheet()
        m = _manager(ws)
        r1 = m.get_worksheet_data('상점', use_cache=True)
        r2 = m.get_worksheet_data('상점', use_cache=True)
        self.assertEqual(ws.get_all_values_calls, 1)   # 두 번째는 캐시
        self.assertEqual(r1, r2)
        self.assertEqual(len(r1), 2)

    def test_use_cache_false_always_reads(self):
        ws = _FakeWorksheet()
        m = _manager(ws)
        m.get_worksheet_data('관리', use_cache=False)
        m.get_worksheet_data('관리', use_cache=False)
        self.assertEqual(ws.get_all_values_calls, 2)   # 매번 최신

    def test_returned_copy_does_not_corrupt_cache(self):
        ws = _FakeWorksheet()
        m = _manager(ws)
        r1 = m.get_worksheet_data('상점', use_cache=True)
        r1[0]['소지금'] = '999999'          # 반환본을 변형해도
        r2 = m.get_worksheet_data('상점', use_cache=True)
        self.assertEqual(r2[0]['소지금'], '100')   # 캐시는 오염되지 않음

    def test_write_invalidates_cache(self):
        ws = _FakeWorksheet()
        m = _manager(ws)
        m.get_worksheet_data('상점', use_cache=True)      # 캐시 적재(호출 1)
        m.batch_update_cells('상점', [(3, 3, 200)])        # 쓰기 → 무효화
        m.get_worksheet_data('상점', use_cache=True)      # 다시 API(호출 2)
        self.assertEqual(ws.get_all_values_calls, 2)

    def test_header_cached(self):
        ws = _FakeWorksheet()
        m = _manager(ws)
        h1 = m.get_header('관리')
        h2 = m.get_header('관리')
        self.assertEqual(h1, ['아이디', '이름', '소지금'])
        self.assertEqual(ws.row_values_calls, 1)          # 두 번째는 캐시

    def test_clear_cache_resets_all(self):
        ws = _FakeWorksheet()
        m = _manager(ws)
        m.get_worksheet_data('상점', use_cache=True)
        m.get_header('관리')
        m.clear_cache()
        self.assertEqual(m._data_cache, {})
        self.assertEqual(m._header_cache, {})


if __name__ == '__main__':
    unittest.main()


class LooseWorksheetMatchTest(unittest.TestCase):
    """시트명에 섞인 공백 때문에 기능이 죽지 않아야 한다.

    실측으로 확인된 사례:
      - 시스템 시트의 `'추적기록 '`(뒤 공백) → 코드는 `'추적기록'`을 찾아 [추적]이 통째로 실패
      - 조사 시트의 `'외곽  시험장'`(공백 2개)
    """

    class _FakeWs:
        def __init__(self, title):
            self.title = title

    class _FakeSpreadsheet:
        def __init__(self, titles):
            self._ws = [LooseWorksheetMatchTest._FakeWs(t) for t in titles]

        def worksheet(self, name):
            import gspread
            for w in self._ws:
                if w.title == name:
                    return w
            raise gspread.exceptions.WorksheetNotFound(name)

        def worksheets(self):
            return list(self._ws)

    def _manager(self, titles):
        m = SheetsManager.__new__(SheetsManager)
        m.purpose = None
        m._credential_name = None
        m._worksheets_cache = {}
        m._header_warned = set()
        m._spreadsheet = self._FakeSpreadsheet(titles)
        return m

    def test_exact_name_still_wins(self):
        m = self._manager(['추적기록'])
        self.assertEqual(m.get_worksheet('추적기록').title, '추적기록')

    def test_trailing_space_in_sheet_name_is_matched(self):
        m = self._manager(['추적기록 '])
        self.assertEqual(m.get_worksheet('추적기록').title, '추적기록 ')

    def test_doubled_inner_space_is_matched(self):
        m = self._manager(['외곽  시험장'])
        self.assertEqual(m.get_worksheet('외곽 시험장').title, '외곽  시험장')

    def test_truly_missing_sheet_still_raises(self):
        """관용 매칭이 '없는 시트'를 덮어 주면 안 된다."""
        m = self._manager(['추적기록'])
        with self.assertRaises(Exception):
            m.get_worksheet('없는시트')


class HeaderWhitespaceFallbackTest(unittest.TestCase):
    """헤더 공백 폴백 — 시트명보다 조용히 위험하다.

    헤더가 '소지금 '이면 `row.get('소지금')`이 None을 돌려주고 **소지금이 0으로 읽힌다**.
    예외도 안 나서 아무도 모른다. GM 시트에서 실제로 반복된 문제라 전반에 폴백을 건다.
    """

    class _Ws:
        def __init__(self, header, rows):
            self._values = [header, ['설명'] * len(header)] + rows
            self.row_count = len(self._values) + 10

        def get_all_values(self):
            return [list(r) for r in self._values]

        def row_values(self, n):
            return list(self._values[n - 1])

    def _manager(self, header, rows):
        m = SheetsManager.__new__(SheetsManager)
        m.purpose = None
        m._credential_name = None
        m._worksheets_cache = {}
        m._data_cache = {}
        m._header_cache = {}
        m._header_warned = set()
        m._data_cache_ttl = 90.0
        ws = self._Ws(header, rows)
        m.get_worksheet = lambda name, use_cache=True: ws
        return m

    def test_trailing_space_header_is_readable(self):
        m = self._manager(['이름', '소지금 '], [['한참', '10']])
        row = m.get_worksheet_data('관리')[0]
        self.assertEqual(row['소지금'], '10', "헤더 뒤 공백 때문에 소지금이 사라지면 안 된다.")

    def test_leading_space_header_is_readable(self):
        m = self._manager([' 이름', '소지금'], [['한참', '10']])
        self.assertEqual(m.get_worksheet_data('관리')[0]['이름'], '한참')

    def test_doubled_inner_space_header_is_readable(self):
        m = self._manager(['조사  포인트'], [['수위계']])
        self.assertEqual(m.get_worksheet_data('중앙 광장')[0]['조사 포인트'], '수위계')

    def test_nbsp_header_is_readable(self):
        """NBSP 등 유니코드 공백도 str.split()이 처리한다."""
        m = self._manager([' 소지금 '], [['10']])
        self.assertEqual(m.get_worksheet_data('관리')[0]['소지금'], '10')

    def test_column_order_preserved_for_index_derivation(self):
        """열 번호는 행 키 순서에서 나온다 → 정규화가 순서를 바꾸면 안 된다."""
        m = self._manager(['이름', '아이디', '소지금 ', '출석'], [['한참', 'u1', '10', '']])
        row = m.get_worksheet_data('관리')[0]
        keys = [k for k in row.keys() if k != '_row_number']
        self.assertEqual(keys, ['이름', '아이디', '소지금', '출석'])
        self.assertEqual(keys.index('소지금') + 1, 3)

    def test_clean_header_unchanged(self):
        m = self._manager(['이름', '소지금'], [['한참', '10']])
        row = m.get_worksheet_data('관리')[0]
        self.assertEqual(sorted(k for k in row if k != '_row_number'), ['소지금', '이름'])

    def test_get_header_normalizes_too(self):
        """get_header와 get_worksheet_data가 다른 이름을 주면 열 번호가 어긋난다."""
        m = self._manager(['이름', '소지금 '], [['한참', '10']])
        self.assertEqual(m.get_header('관리'), ['이름', '소지금'])

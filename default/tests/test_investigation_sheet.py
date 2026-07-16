"""utils/investigation_sheet.py — 판정 코어 단위 테스트."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from tests.investigation_fixtures import (
    entry_row, exception_row, investigation_sheets, point_row,
)
from config.settings import config
from utils.investigation_sheet import (
    COL_LOCATION, COL_POINT, InvestigationDataError, InvestigationRepo,
    accessible_point_rows, config_int, detect_version_conflicts, display_name,
    find_exception, is_available, normalize_name, parse_roles, pick_variant,
    role_matches, visible_point_names,
)


class ConfigIntTest(unittest.TestCase):
    """`int(getattr(...) or default)` 관용구의 0-falsy 함정 회귀 테스트."""

    def tearDown(self):
        for key in ('_T_INT',):
            if hasattr(type(config), key):
                delattr(type(config), key)

    def test_zero_is_preserved(self):
        """0은 유효한 값이다. 개방 시각 0시나 일일 횟수 0(비활성화)이 무시되면 안 된다."""
        type(config)._T_INT = 0
        self.assertEqual(config_int('_T_INT', 21), 0)

    def test_missing_uses_default(self):
        self.assertEqual(config_int('_T_MISSING', 21), 21)

    def test_none_uses_default(self):
        type(config)._T_INT = None
        self.assertEqual(config_int('_T_INT', 21), 21)

    def test_empty_string_uses_default(self):
        type(config)._T_INT = ''
        self.assertEqual(config_int('_T_INT', 21), 21)

    def test_numeric_string_parsed(self):
        type(config)._T_INT = '3'
        self.assertEqual(config_int('_T_INT', 21), 3)

    def test_garbage_uses_default(self):
        type(config)._T_INT = '아무거나'
        self.assertEqual(config_int('_T_INT', 21), 21)


class SheetContractTest(unittest.TestCase):
    """프로덕션 코드의 대전제를 고정한다.

    열 번호를 '읽어 온 행의 키 순서'에서 산출하는 코드가 여러 곳이다
    (Actor.column_index, daily_counter._col_index_from_row,
     attendance._column_index, investigation_open._availability_column).
    이게 성립하려면 **모든 행이 모든 헤더 키를 헤더 순서대로** 가져야 한다.
    실제 SheetsManager는 `dict(zip(header, row))` + gspread의 행 패딩으로 이를 보장한다.
    """

    def test_rows_carry_every_header_key_in_order(self):
        fake = investigation_sheets(locations={'X': [point_row('수위계')]})
        rows = fake.get_worksheet_data('X')
        keys = [k for k in rows[0].keys() if k != '_row_number']
        self.assertEqual(keys, list(point_row('x').keys()),
                         "행 키 순서 = 헤더 순서여야 열 번호 산출이 성립한다.")

    def test_trailing_empty_cells_still_present_as_keys(self):
        """마지막 컬럼이 비어 있어도 키는 존재해야 한다(패딩).

        키가 빠지면 그 컬럼을 '없음'으로 오판한다 — 출석 컬럼이 마지막이라 특히 위험.
        """
        fake = investigation_sheets(locations={'X': [point_row('수위계', money='')]})
        row = fake.get_worksheet_data('X')[0]
        self.assertIn('재화 증감', row)
        self.assertEqual(row['재화 증감'], '')

    def test_row_numbers_start_at_three(self):
        """1행 헤더 / 2행 설명 / 3행부터 데이터."""
        fake = investigation_sheets(locations={'X': [point_row('a'), point_row('b')]})
        self.assertEqual([r['_row_number'] for r in fake.get_worksheet_data('X')], [3, 4])

    def test_failed_read_returns_empty_not_raise(self):
        """실제 매니저는 장애 시에도 예외 없이 []를 준다(safe_execute)."""
        fake = investigation_sheets(locations={'X': [point_row('a')]})
        fake.fail_sheets.add('X')
        self.assertEqual(fake.get_worksheet_data('X'), [])


class NormalizeNameTest(unittest.TestCase):
    def test_removes_all_whitespace(self):
        self.assertEqual(normalize_name('중앙 광장'), '중앙광장')
        self.assertEqual(normalize_name('  중앙광장  '), '중앙광장')

    def test_collapses_repeated_inner_spaces(self):
        """실제 시트에 '외곽  시험장'(공백 2개)이 존재한다."""
        self.assertEqual(normalize_name('외곽  시험장'), normalize_name('외곽 시험장'))

    def test_display_name_keeps_inner_spacing(self):
        self.assertEqual(display_name('  중앙 광장 '), '중앙 광장')

    def test_none_is_empty(self):
        self.assertEqual(normalize_name(None), '')


class AvailabilityTest(unittest.TestCase):
    def test_exact_string_only(self):
        self.assertTrue(is_available({'현재 조사 가능': '가능'}))
        self.assertTrue(is_available({'현재 조사 가능': ' 가능 '}))

    def test_blank_is_unavailable(self):
        self.assertFalse(is_available({'현재 조사 가능': ''}))
        self.assertFalse(is_available({}))

    def test_other_values_are_unavailable(self):
        self.assertFalse(is_available({'현재 조사 가능': '불가능'}))
        self.assertFalse(is_available({'현재 조사 가능': 'O'}))

    def test_legacy_column_supported(self):
        """구 시트가 '현재 진입 가능'으로 만들어진 경우."""
        self.assertTrue(is_available({'현재 진입 가능': '가능'}))


class RoleTest(unittest.TestCase):
    def test_parses_multi_select_chips(self):
        row = {'조사 가능 직군': '연구자, 기술공, 군인'}
        self.assertEqual(parse_roles(row), ['연구자', '기술공', '군인'])

    def test_matches_ignoring_spaces(self):
        row = {'조사 가능 직군': '연구자,  기술공'}
        self.assertTrue(role_matches(row, '기술공'))
        self.assertTrue(role_matches(row, ' 기술공 '))
        self.assertFalse(role_matches(row, '가족'))

    def test_empty_role_never_matches(self):
        self.assertFalse(role_matches({'조사 가능 직군': '연구자'}, ''))

    def test_no_role_column(self):
        self.assertEqual(parse_roles({}), [])


class AccessTest(unittest.TestCase):
    """가이드 §2.2 직군 판정 + §2.3 예외 접근 부여."""

    def setUp(self):
        self.rows = [
            point_row('전압계', roles='연구자', available=True),
            point_row('야금 실험대', roles='연구자, 기술공', available=True),
            point_row('진료 대장', roles='연구자', available=True),
            point_row('닫힌 곳', roles='연구자', available=False),
        ]

    def test_role_filter(self):
        names = visible_point_names(self.rows, [], '한참', '연구소', '기술공')
        self.assertEqual(names, ['야금 실험대'])

    def test_unavailable_never_shown(self):
        names = visible_point_names(self.rows, [], '한참', '연구소', '연구자')
        self.assertNotIn('닫힌 곳', names)

    def test_exception_grants_access(self):
        """가족 직군 간호사A가 예외로 진료 대장 접근."""
        exceptions = [exception_row('간호사A', '연구소', '진료 대장')]
        names = visible_point_names(self.rows, exceptions, '간호사A', '연구소', '가족')
        self.assertEqual(names, ['진료 대장'])

    def test_exception_cannot_open_closed_point(self):
        """§2.3-4: 개방 여부가 항상 우선한다."""
        exceptions = [exception_row('간호사A', '연구소', '닫힌 곳')]
        names = visible_point_names(self.rows, exceptions, '간호사A', '연구소', '가족')
        self.assertEqual(names, [])

    def test_exception_scoped_to_location(self):
        exceptions = [exception_row('간호사A', '광산', '진료 대장')]
        names = visible_point_names(self.rows, exceptions, '간호사A', '연구소', '가족')
        self.assertEqual(names, [])

    def test_exception_scoped_to_character(self):
        exceptions = [exception_row('다른사람', '연구소', '진료 대장')]
        names = visible_point_names(self.rows, exceptions, '간호사A', '연구소', '가족')
        self.assertEqual(names, [])

    def test_no_access_returns_empty(self):
        self.assertEqual(visible_point_names(self.rows, [], '한참', '연구소', '가족'), [])

    def test_point_filter_matches_ignoring_spaces(self):
        rows = accessible_point_rows(
            self.rows, [], '한참', '연구소', '연구자', point='야금실험대')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['조사 포인트'], '야금 실험대')


class VariantTest(unittest.TestCase):
    """§2.2 변주: 같은 이름·같은 오픈 일자의 '가능' 행이 둘 이상."""

    def setUp(self):
        self.rows = [
            point_row('급수대 줄', available=True, text='문구A'),
            point_row('급수대 줄', available=True, text='문구B'),
        ]

    def test_listed_once(self):
        names = visible_point_names(self.rows, [], '한참', '중앙 광장', '연구자')
        self.assertEqual(names, ['급수대 줄'])

    def test_pick_variant_covers_all(self):
        import random
        random.seed(3)
        seen = set()
        for _ in range(80):
            row = pick_variant(self.rows)
            seen.add(row['조사 시 문구'])
        self.assertEqual(seen, {'문구A', '문구B'})

    def test_pick_variant_single(self):
        row = pick_variant([self.rows[0]])
        self.assertEqual(row['조사 시 문구'], '문구A')

    def test_pick_variant_empty(self):
        self.assertIsNone(pick_variant([]))

    def test_variant_pick_only_from_accessible(self):
        """§2.2: 조사 시 무작위 선택은 접근 가능한 행들 중에서만."""
        rows = [
            point_row('부검실', roles='연구자', available=True, text='연구자용'),
            point_row('부검실', roles='군인', available=True, text='군인용'),
        ]
        picked = accessible_point_rows(rows, [], '한참', '연구소', '연구자', point='부검실')
        self.assertEqual([r['조사 시 문구'] for r in picked], ['연구자용'])


class VersionConflictTest(unittest.TestCase):
    """§2.1 부가 규칙: 버전 중복 경고 (변주는 경고 대상이 아니다)."""

    def test_variants_are_not_conflicts(self):
        rows = [
            point_row('급수대 줄', day='1주-4일차', available=True),
            point_row('급수대 줄', day='1주-4일차', available=True),
        ]
        self.assertEqual(detect_version_conflicts(rows, COL_POINT), [])

    def test_different_open_days_conflict(self):
        rows = [
            point_row('부검실', day='1주-4일차', available=True),
            point_row('부검실', day='3주-16일차', available=True),
        ]
        conflicts = detect_version_conflicts(rows, COL_POINT)
        self.assertEqual(len(conflicts), 1)
        name, days = conflicts[0]
        self.assertEqual(name, '부검실')
        self.assertEqual(days, ['1주-4일차', '3주-16일차'])

    def test_unavailable_rows_ignored(self):
        rows = [
            point_row('부검실', day='1주-4일차', available=False),
            point_row('부검실', day='3주-16일차', available=True),
        ]
        self.assertEqual(detect_version_conflicts(rows, COL_POINT), [])

    def test_entry_sheet_conflicts(self):
        rows = [
            entry_row('중앙 광장', day='1주-4일차', available=True),
            entry_row('중앙 광장', day='2주-8일차', available=True),
        ]
        self.assertEqual(len(detect_version_conflicts(rows, COL_LOCATION)), 1)


class FindExceptionTest(unittest.TestCase):
    def test_matches_ignoring_spaces(self):
        exceptions = [exception_row('간호사A', '연구소', '진료 대장', '특수')]
        found = find_exception(exceptions, '간호사A', '연구소', '진료대장')
        self.assertIsNotNone(found)
        self.assertEqual(found['조사 시 문구'], '특수')

    def test_missing_returns_none(self):
        self.assertIsNone(find_exception([], '간호사A', '연구소', '진료 대장'))


class RepoTest(unittest.TestCase):
    def setUp(self):
        self.fake = investigation_sheets(
            entry=[
                entry_row('중앙 광장', available=True),
                entry_row('중앙 광장', day='2주-8일차', available=False),
                entry_row('연구소', available=False),
            ],
            locations={'중앙 광장': [point_row('수위계', available=True)]},
        )
        self.repo = InvestigationRepo(self.fake)

    def test_known_locations_deduplicated(self):
        self.assertEqual(self.repo.known_locations(), ['중앙 광장', '연구소'])

    def test_location_exists_ignores_spaces(self):
        self.assertTrue(self.repo.location_exists('중앙광장'))
        self.assertFalse(self.repo.location_exists('없는곳'))

    def test_canonical_location_returns_sheet_spelling(self):
        self.assertEqual(self.repo.canonical_location('중앙광장'), '중앙 광장')
        self.assertIsNone(self.repo.canonical_location('없는곳'))

    def test_open_entry_rows_only_available(self):
        self.assertEqual(len(self.repo.open_entry_rows('중앙 광장')), 1)
        self.assertEqual(self.repo.open_entry_rows('연구소'), [])

    def test_excluded_template_sheet_not_a_location(self):
        from config.settings import config
        template = config.INVESTIGATION_EXCLUDED_SHEETS[0]
        self.fake.set('진입', [entry_row('중앙 광장'), entry_row(template)],
                      header=list(entry_row('x').keys()))
        self.assertEqual(InvestigationRepo(self.fake).known_locations(), ['중앙 광장'])

    def test_empty_entry_sheet_raises(self):
        """SheetsManager는 장애 시에도 []를 주므로 빈 진입 시트는 장애로 취급한다."""
        self.fake.fail_sheets.add('진입')
        with self.assertRaises(InvestigationDataError):
            self.repo.entry_rows()

    def test_empty_location_sheet_raises(self):
        self.fake.fail_sheets.add('중앙 광장')
        with self.assertRaises(InvestigationDataError):
            self.repo.point_rows('중앙 광장')

    def test_empty_exception_sheet_is_fine(self):
        """예외가 하나도 없는 것은 정상이다."""
        self.assertEqual(self.repo.exception_rows(), [])


if __name__ == '__main__':
    unittest.main()

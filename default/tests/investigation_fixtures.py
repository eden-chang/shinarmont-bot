"""조사 봇 테스트용 공통 픽스처.

MagicMock 대신 동작하는 가짜 시트 매니저(FakeSheets)를 쓴다.
개방 스케줄러처럼 '쓰기 결과'를 검증해야 하는 테스트가 많아, 쓰기를 실제로 반영하는
페이크가 mock 호출 인자 비교보다 훨씬 읽기 쉽다.

FakeSheets는 실제 SheetsManager의 계약을 따른다:
- get_worksheet_data(name, use_cache=False) -> List[Dict], 3행부터 데이터, `_row_number` 포함
- **실패 시 예외를 던지지 않고 [] 반환** (safe_execute(fallback_return=[]))
- append_row(name, values) -> bool
- batch_update_cells(name, [(row, col, value), ...]) -> bool
"""

import os
import sys
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

ROLES_ALL = '연구자, 기술공, 군인, 행정관, 가족'


# --------------------------------------------------------------------------- #
# 행 빌더
# --------------------------------------------------------------------------- #
def entry_row(location, day='1주-4일차', text=None, available=False):
    """`진입` 시트 행."""
    return {
        '현재 조사 가능': '가능' if available else '',
        '조사 오픈 일자': day,
        '장소명': location,
        '진입 시 문구': text if text is not None else f'{location}에 들어선다.',
    }


def point_row(name, day='1주-4일차', roles=ROLES_ALL, available=False, text=None,
              item='', count='', stat='', value='', money=''):
    """장소 시트 행."""
    return {
        '현재 조사 가능': '가능' if available else '',
        '조사 오픈 일자': day,
        '조사 가능 직군': roles,
        '조사 포인트': name,
        '조사 시 문구': text if text is not None else f'{name}을(를) 살펴본다.',
        '획득 아이템': item,
        '개수': count,
        '변동 스탯': stat,
        '수치': value,
        '재화 증감': money,
    }


def exception_row(character, location, point, text='특수 문구'):
    """`예외` 시트 행."""
    return {
        '캐릭터명': character,
        '장소명': location,
        '포인트명': point,
        '조사 시 문구': text,
    }


def log_row(stamp, character, location, point='', result='진입'):
    """`로그` 시트 행."""
    return {
        '일시': stamp,
        '캐릭터명': character,
        '장소명': location,
        '포인트명': point,
        '결과': result,
    }


def mgmt_row(name='한참', user_id='alice', role='연구자', health=100, sanity=100,
             money=10, inventory='', today_count=0, attendance=''):
    """`관리` 시트 행 (기본 스프레드시트).

    **컬럼명·순서를 실제 시트에 맞춘다**(2026-07-16 실측):
        이름 / 아이디 / 직군 / 추적 / 조사 / 소지금 / 출석 / 소지품 / 건강 / 이성
    열 번호를 행 키 순서에서 산출하는 코드가 많아 순서까지 같아야 한다.
    """
    return {
        '이름': name,
        '아이디': user_id,
        '직군': role,
        '추적': 0,
        '조사': today_count,
        '소지금': money,
        '출석': attendance,
        '소지품': inventory,
        '건강': health,
        '이성': sanity,
    }


# --------------------------------------------------------------------------- #
# 가짜 시트 매니저
# --------------------------------------------------------------------------- #
class FakeSheets:
    """SheetsManager의 최소 동작 구현."""

    def __init__(self, sheets: Optional[Dict[str, List[Dict[str, Any]]]] = None):
        # name -> rows (각 행에 _row_number 부여, 3행부터)
        self.sheets: Dict[str, List[Dict[str, Any]]] = {}
        # name -> 헤더. 빈 시트에도 append/batch_update가 동작하도록 별도 보관한다.
        self.headers: Dict[str, List[str]] = {}
        for name, rows in (sheets or {}).items():
            self.set(name, rows)
        self.appended: List[Tuple[str, List[Any]]] = []
        self.reads: List[Tuple[str, bool]] = []
        self.write_ok = True
        self.fail_sheets: set = set()

    def set(self, name: str, rows: List[Dict[str, Any]],
            header: Optional[List[str]] = None) -> None:
        stored = []
        for i, row in enumerate(rows):
            r = dict(row)
            r['_row_number'] = i + 3
            stored.append(r)
        self.sheets[name] = stored
        if header:
            self.headers[name] = list(header)
        elif rows:
            self.headers[name] = [k for k in rows[0].keys() if k != '_row_number']

    # -- SheetsManager 계약 ------------------------------------------------
    def get_worksheet_data(self, name: str, use_cache: bool = False) -> List[Dict[str, Any]]:
        """실제 SheetsManager와 같은 계약으로 행을 돌려준다.

        중요: 실제 구현은 `dict(zip(headers, row_values))`라서 **행 딕셔너리의 키 순서가
        곧 헤더 순서**이며, gspread의 `get_all_values()`가 행을 헤더 길이까지 패딩해 주므로
        모든 행이 **모든 헤더 키를 갖는다**(값은 빈 문자열).
        프로덕션 코드가 열 번호를 행 키에서 산출하므로 이 계약이 깨지면 전부 오작동한다.
        페이크도 반드시 같은 계약을 지켜야 한다 → 여기서 강제한다.
        """
        self.reads.append((name, use_cache))
        if name in self.fail_sheets:
            return []  # 실제 매니저는 실패해도 예외 없이 []를 반환한다

        header = self._header(name)
        rows = []
        for stored in self.sheets.get(name, []):
            row = {h: stored.get(h, '') for h in header}   # 헤더 길이까지 패딩
            row['_row_number'] = stored['_row_number']
            rows.append(row)
        return rows

    def append_row(self, name: str, values: List[Any]) -> bool:
        if not self.write_ok:
            return False
        self.appended.append((name, list(values)))
        rows = self.sheets.setdefault(name, [])
        header = self._header(name)
        if header:
            row = dict(zip(header, values))
            row['_row_number'] = len(rows) + 3
            rows.append(row)
        return True

    def batch_update_cells(self, name: str, updates: List[Tuple[int, int, Any]]) -> bool:
        if not self.write_ok:
            return False
        header = self._header(name)
        for row_number, col, value in updates:
            for row in self.sheets.get(name, []):
                if row.get('_row_number') == row_number:
                    if 1 <= col <= len(header):
                        row[header[col - 1]] = value
                    break
        return True

    def update_cell(self, name: str, row: int, col: int, value: Any) -> bool:
        return self.batch_update_cells(name, [(row, col, value)])

    def _header(self, name: str) -> List[str]:
        if name in self.headers:
            return self.headers[name]
        rows = self.sheets.get(name) or []
        if not rows:
            return []
        return [k for k in rows[0].keys() if k != '_row_number']

    # -- 테스트 헬퍼 -------------------------------------------------------
    def value(self, name: str, row_number: int, column: str) -> Any:
        for row in self.sheets.get(name, []):
            if row.get('_row_number') == row_number:
                return row.get(column)
        return None

    def column(self, name: str, column: str) -> List[Any]:
        return [r.get(column) for r in self.sheets.get(name, [])]


def investigation_sheets(entry=None, locations=None, exceptions=None, logs=None) -> FakeSheets:
    """조사 스프레드시트 페이크 구성."""
    fake = FakeSheets()
    fake.set('진입', entry or [], header=list(entry_row('x').keys()))
    fake.set('예외', exceptions or [], header=list(exception_row('c', 'l', 'p').keys()))
    fake.set('로그', logs or [], header=['일시', '캐릭터명', '장소명', '포인트명', '결과'])
    for name, rows in (locations or {}).items():
        fake.set(name, rows, header=list(point_row('x').keys()))
    return fake


def main_sheets(rows=None, shop=None) -> FakeSheets:
    """기본 스프레드시트(관리/상점) 페이크 구성."""
    return FakeSheets({
        '관리': rows if rows is not None else [mgmt_row()],
        '상점': shop or [],
    })

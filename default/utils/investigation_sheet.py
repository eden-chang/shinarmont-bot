"""
utils/investigation_sheet.py — 조사 스프레드시트 접근 및 판정 코어

`진입` / 장소명 / `예외` 워크시트를 읽고, 가이드(docs/조사봇_개발_가이드.md)의
진입·조사 판정 규칙을 구현한다. 명령어 모듈은 이 레이어만 사용한다.

핵심 규칙:
- 모든 시트는 1행 헤더 / 2행 설명 / 3행부터 데이터 (SheetsManager.get_worksheet_data가 처리)
- '현재 조사 가능'이 정확히 '가능'일 때만 가능. 빈칸은 불가능 (§2 공통 주의사항)
- 이름 비교는 공백을 전부 제거한 문자열끼리 수행한다.
  실제 시트에 `외곽  시험장`처럼 공백이 겹친 시트명이 존재하므로 필수다.
- 버전: 같은 이름의 행이 오픈 일자별로 여러 개 존재할 수 있다 (일차별 문구 교체)
- 변주: 같은 이름·같은 오픈 일자의 '가능' 행이 둘 이상이면 조사 시 무작위 선택

진입 판정 (§2.1) — 캐릭터 X가 장소 P에 들어갈 수 있는 조건 (둘 다 충족):
  1. 진입 시트에 P의 '현재 조사 가능 = 가능' 행이 존재
  2. P의 장소 시트에 X가 조사 가능한 포인트가 1개 이상 존재
장소 단위의 직군 제한은 존재하지 않는다. 진입 가능 여부는 포인트 접근성에서 파생된다.
"""

import os
import random
import re
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

try:
    from utils.logging_config import logger
except ImportError:  # pragma: no cover - VM 폴백
    import logging
    logger = logging.getLogger('utils.investigation_sheet')

try:
    from config.settings import config
except ImportError:  # pragma: no cover
    config = None


# --------------------------------------------------------------------------- #
# 컬럼 상수
# --------------------------------------------------------------------------- #
AVAILABLE_VALUE = '가능'

COL_AVAILABLE = '현재 조사 가능'
# 구 시트 호환: 진입 시트가 '현재 진입 가능'으로 만들어진 적이 있다.
COL_AVAILABLE_LEGACY = '현재 진입 가능'
COL_OPEN_DAY = '조사 오픈 일자'
COL_LOCATION = '장소명'
COL_ENTRY_TEXT = '진입 시 문구'

COL_ROLES = '조사 가능 직군'
COL_POINT = '조사 포인트'
COL_POINT_TEXT = '조사 시 문구'
COL_ITEM = '획득 아이템'
COL_ITEM_COUNT = '개수'
COL_STAT = '변동 스탯'
COL_STAT_VALUE = '수치'
COL_MONEY = '재화 증감'

COL_EXC_CHARACTER = '캐릭터명'
COL_EXC_LOCATION = '장소명'
COL_EXC_POINT = '포인트명'
COL_EXC_TEXT = '조사 시 문구'

# 직군 다중 선택 칩 구분자 (쉼표 / 슬래시 / 중점 / 줄바꿈 모두 허용)
_ROLE_SPLIT_RE = re.compile(r'[,/·\n]+')

_WHITESPACE_RE = re.compile(r'\s+')


# --------------------------------------------------------------------------- #
# 설정 헬퍼
# --------------------------------------------------------------------------- #
def config_int(key: str, default: int) -> int:
    """config의 정수 설정을 안전하게 읽는다.

    `int(getattr(config, key, default) or default)` 관용구를 쓰면 안 된다.
    0이 falsy라서 `0 or 21 == 21`이 되어 버린다 — 개방 시각 0시나
    일일 조사 횟수 0(조사 비활성화) 같은 유효한 설정이 조용히 무시된다.
    """
    raw = getattr(config, key, default)
    if raw is None or raw == '':
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning(f"[조사] 설정 {key}={raw!r}을(를) 정수로 읽을 수 없어 {default} 사용")
        return default


# --------------------------------------------------------------------------- #
# 이름 정규화
# --------------------------------------------------------------------------- #
def normalize_name(value: Any) -> str:
    """이름 비교용 정규화: 모든 공백 제거.

    "중앙광장"으로 입력해도 "중앙 광장"에 매칭되고,
    시트명 "외곽  시험장"(공백 2개)도 "외곽 시험장"과 같게 취급된다.
    """
    if value is None:
        return ''
    return _WHITESPACE_RE.sub('', str(value))


def display_name(value: Any) -> str:
    """출력용 이름: 앞뒤 공백만 제거(내부 공백은 시트 표기 그대로 유지)."""
    if value is None:
        return ''
    return str(value).strip()


# --------------------------------------------------------------------------- #
# 행 판정 헬퍼
# --------------------------------------------------------------------------- #
def is_available(row: Dict[str, Any]) -> bool:
    """'현재 조사 가능'이 정확히 '가능'인지. 빈칸·기타 값은 전부 불가능."""
    raw = row.get(COL_AVAILABLE)
    if raw is None:
        raw = row.get(COL_AVAILABLE_LEGACY)
    return str(raw or '').strip() == AVAILABLE_VALUE


def open_day_of(row: Dict[str, Any]) -> str:
    """'조사 오픈 일자' 값(표기 그대로, trim)."""
    return display_name(row.get(COL_OPEN_DAY))


def parse_roles(row: Dict[str, Any]) -> List[str]:
    """'조사 가능 직군' 다중 선택 칩을 정규화된 직군 리스트로 파싱."""
    raw = row.get(COL_ROLES)
    if raw is None:
        return []
    tokens = _ROLE_SPLIT_RE.split(str(raw))
    return [normalize_name(t) for t in tokens if normalize_name(t)]


def role_matches(row: Dict[str, Any], role: str) -> bool:
    """캐릭터 직군이 이 행의 '조사 가능 직군'에 포함되는지."""
    if not role:
        return False
    return normalize_name(role) in parse_roles(row)


# --------------------------------------------------------------------------- #
# 예외 시트
# --------------------------------------------------------------------------- #
def find_exception(
    exceptions: Sequence[Dict[str, Any]],
    character: str,
    location: str,
    point: str,
) -> Optional[Dict[str, Any]]:
    """(캐릭터, 장소, 포인트)에 해당하는 예외 행을 반환. 없으면 None.

    예외는 문구 대체 + 접근 부여만 한다. 아이템·스탯·재화는 기본 행 값을 따른다 (§2.3).
    같은 키의 행이 둘 이상이면 첫 행을 쓰되, GM이 의도를 알 수 없게 되므로 경고를 남긴다.
    """
    c, l, p = normalize_name(character), normalize_name(location), normalize_name(point)
    if not (c and l and p):
        return None

    matches = [
        row for row in exceptions or []
        if normalize_name(row.get(COL_EXC_CHARACTER)) == c
        and normalize_name(row.get(COL_EXC_LOCATION)) == l
        and normalize_name(row.get(COL_EXC_POINT)) == p
    ]
    if not matches:
        return None

    if len(matches) > 1:
        # 변주(무작위 선택)가 아니다. 예외는 한 조합에 하나여야 한다.
        try:
            from utils.investigation_notify import notify_admin_once
            notify_admin_once(
                f"duplicate-exception:{c}:{l}:{p}",
                f"⚠️ 예외 시트에 ({character} / {location} / {point}) 행이 "
                f"{len(matches)}개 있습니다. 첫 행만 적용하니 중복을 정리해 주세요.",
            )
        except Exception:  # 알림 실패가 판정을 막지 않는다
            logger.warning(
                f"[조사] 예외 중복: {character}/{location}/{point} ({len(matches)}건)")
    return matches[0]


def exception_points_in(
    exceptions: Sequence[Dict[str, Any]],
    character: str,
    location: str,
) -> set:
    """이 캐릭터가 이 장소에서 예외로 접근 가능한 포인트명(정규화) 집합."""
    c, l = normalize_name(character), normalize_name(location)
    if not (c and l):
        return set()
    return {
        normalize_name(row.get(COL_EXC_POINT))
        for row in exceptions or []
        if normalize_name(row.get(COL_EXC_CHARACTER)) == c
        and normalize_name(row.get(COL_EXC_LOCATION)) == l
        and normalize_name(row.get(COL_EXC_POINT))
    }


# --------------------------------------------------------------------------- #
# 포인트 접근 판정
# --------------------------------------------------------------------------- #
def accessible_point_rows(
    point_rows: Sequence[Dict[str, Any]],
    exceptions: Sequence[Dict[str, Any]],
    character: str,
    location: str,
    role: str,
    point: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """캐릭터가 지금 조사할 수 있는 포인트 행들을 반환.

    조건: '현재 조사 가능 = 가능' AND (직군 포함 OR 예외 행 존재).
    '현재 조사 가능 = 불가능'인 포인트는 예외가 있어도 조사할 수 없다 (§2.3-4: 개방 여부 우선).

    Args:
        point: 지정하면 그 포인트명(공백 무시 비교)의 행만 반환.
    """
    exc_points = exception_points_in(exceptions, character, location)
    wanted = normalize_name(point) if point else None

    result: List[Dict[str, Any]] = []
    for row in point_rows or []:
        name = normalize_name(row.get(COL_POINT))
        if not name:
            continue
        if wanted is not None and name != wanted:
            continue
        if not is_available(row):
            continue
        if role_matches(row, role) or name in exc_points:
            result.append(row)
    return result


def visible_point_names(
    point_rows: Sequence[Dict[str, Any]],
    exceptions: Sequence[Dict[str, Any]],
    character: str,
    location: str,
    role: str,
) -> List[str]:
    """목록에 표시할 포인트명(중복 제거, 시트 순서 유지, 표기 그대로).

    같은 포인트명의 '가능' 행 중 하나라도 접근 가능하면 표시한다 (§2.2 직군 판정).
    """
    rows = accessible_point_rows(point_rows, exceptions, character, location, role)
    seen: set = set()
    names: List[str] = []
    for row in rows:
        key = normalize_name(row.get(COL_POINT))
        if key in seen:
            continue
        seen.add(key)
        names.append(display_name(row.get(COL_POINT)))
    return names


def pick_variant(rows: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """변주 행들 중 하나를 무작위 선택 (§2.2 변주 규칙)."""
    if not rows:
        return None
    if len(rows) == 1:
        return rows[0]
    return random.choice(list(rows))


# --------------------------------------------------------------------------- #
# 버전 중복 경고 (§2.1 부가 규칙)
# --------------------------------------------------------------------------- #
def detect_version_conflicts(
    rows: Sequence[Dict[str, Any]],
    name_column: str,
) -> List[Tuple[str, List[str]]]:
    """같은 이름의 '가능' 행들이 서로 다른 오픈 일자를 갖는 경우를 찾아 반환.

    변주(같은 이름·같은 오픈 일자)는 정상이므로 경고 대상이 아니다.
    버전 전환 누락 감지용 — 동작은 유지하되 관리자에게 경고한다.

    Returns:
        [(이름, [오픈 일자, ...]), ...]
    """
    by_name: Dict[str, Dict[str, str]] = {}
    for row in rows or []:
        if not is_available(row):
            continue
        key = normalize_name(row.get(name_column))
        if not key:
            continue
        by_name.setdefault(key, {})[open_day_of(row)] = display_name(row.get(name_column))

    conflicts: List[Tuple[str, List[str]]] = []
    for key, days in by_name.items():
        if len(days) > 1:
            label = next(iter(days.values()))
            conflicts.append((label, sorted(days.keys())))
    return conflicts


# --------------------------------------------------------------------------- #
# 리포지토리
# --------------------------------------------------------------------------- #
class InvestigationRepo:
    """조사 스프레드시트 읽기 래퍼.

    캐시 정책 (가이드 §1):
    - 목록/판정용 대량 읽기는 캐시 사용(TTL은 config.INVESTIGATION_CACHE_TTL).
    - '현재 조사 가능'처럼 정합성이 중요한 값은 명령 처리 시점에 fresh=True로 재검증한다.
    """

    def __init__(self, sheets_manager):
        self.sheets = sheets_manager

    # -- 시트명 -------------------------------------------------------------
    @property
    def entry_sheet(self) -> str:
        return getattr(config, 'INVESTIGATION_ENTRY_SHEET', '진입')

    @property
    def exception_sheet(self) -> str:
        return getattr(config, 'INVESTIGATION_EXCEPTION_SHEET', '예외')

    # -- 저수준 읽기 --------------------------------------------------------
    def _read(
        self,
        worksheet: str,
        fresh: bool,
        allow_empty: bool = True,
    ) -> List[Dict[str, Any]]:
        """워크시트를 읽는다.

        주의: SheetsManager.get_worksheet_data는 실패해도 예외를 던지지 않고 []를 돌려준다
        (safe_execute(fallback_return=[])). 즉 '시트 없음'·'API 장애'·'진짜 빈 시트'가
        전부 []로 뭉개진다.

        그래서 비어 있으면 안 되는 시트(`진입`, 장소 시트)는 allow_empty=False로 읽어
        빈 결과를 장애로 취급한다. 이렇게 하지 않으면 Sheets 장애 중에 러너에게
        "이곳에는 현재 조사할 수 있는 것이 없다."(= 장소가 닫혔다)라고 거짓말을 하게 된다.
        빈 결과가 정상인 시트(`예외`)는 allow_empty=True로 읽는다.
        """
        if self.sheets is None:
            raise InvestigationDataError('조사 시트 매니저가 연결되지 않았습니다.')
        try:
            rows = self.sheets.get_worksheet_data(worksheet, use_cache=not fresh) or []
        except Exception as e:
            logger.error(f"[조사] '{worksheet}' 워크시트 조회 실패: {e}", exc_info=True)
            raise InvestigationDataError(f"'{worksheet}' 워크시트를 읽을 수 없습니다.") from e

        if not rows and not allow_empty:
            raise InvestigationDataError(
                f"'{worksheet}' 워크시트가 비어 있습니다. "
                f"시트가 없거나 읽기에 실패했을 수 있습니다."
            )
        return rows

    # -- 진입 시트 ----------------------------------------------------------
    def entry_rows(self, fresh: bool = False) -> List[Dict[str, Any]]:
        """진입 시트 행들. 이 시트가 비는 것은 정상 상태가 아니므로 장애로 취급한다."""
        return self._read(self.entry_sheet, fresh, allow_empty=False)

    def known_locations(self, entry_rows: Optional[Sequence[Dict[str, Any]]] = None) -> List[str]:
        """진입 시트에 등재된 모든 장소명 (중복 제거, 표기 그대로).

        이 목록이 장소 시트의 화이트리스트다. 템플릿 시트가 장소로 잡히는 것을 막는다 (§2.2).
        '가능' 여부와 무관하게 '이 마을에 존재하는 장소'를 뜻한다.
        """
        rows = entry_rows if entry_rows is not None else self.entry_rows()
        seen: set = set()
        names: List[str] = []
        excluded = {
            normalize_name(s) for s in getattr(config, 'INVESTIGATION_EXCLUDED_SHEETS', [])
        }
        for row in rows:
            key = normalize_name(row.get(COL_LOCATION))
            if not key or key in seen or key in excluded:
                continue
            seen.add(key)
            names.append(display_name(row.get(COL_LOCATION)))
        return names

    def location_exists(
        self,
        location: str,
        entry_rows: Optional[Sequence[Dict[str, Any]]] = None,
    ) -> bool:
        """진입 시트에 이 장소명이 존재하는지 (공백 무시 비교)."""
        rows = entry_rows if entry_rows is not None else self.entry_rows()
        target = normalize_name(location)
        return any(normalize_name(r.get(COL_LOCATION)) == target for r in rows)

    def open_entry_rows(
        self,
        location: str,
        entry_rows: Optional[Sequence[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """이 장소의 '현재 조사 가능 = 가능' 진입 행들 (변주 지원)."""
        rows = entry_rows if entry_rows is not None else self.entry_rows()
        target = normalize_name(location)
        return [
            r for r in rows
            if normalize_name(r.get(COL_LOCATION)) == target and is_available(r)
        ]

    def canonical_location(
        self,
        location: str,
        entry_rows: Optional[Sequence[Dict[str, Any]]] = None,
    ) -> Optional[str]:
        """입력된 장소명을 진입 시트 표기(장소 시트명과 일치)로 변환. 없으면 None."""
        rows = entry_rows if entry_rows is not None else self.entry_rows()
        target = normalize_name(location)
        for row in rows:
            if normalize_name(row.get(COL_LOCATION)) == target:
                return display_name(row.get(COL_LOCATION))
        return None

    # -- 장소 시트 ----------------------------------------------------------
    def point_rows(self, location: str, fresh: bool = False) -> List[Dict[str, Any]]:
        """장소 시트의 포인트 행들.

        호출 전에 location이 진입 시트에 있는 이름인지 확인할 것(화이트리스트).
        진입 시트에 등재된 장소의 시트가 비는 것은 정상 상태가 아니므로 장애로 취급한다.
        """
        return self._read(display_name(location), fresh, allow_empty=False)

    # -- 예외 시트 ----------------------------------------------------------
    def exception_rows(self, fresh: bool = False) -> List[Dict[str, Any]]:
        """예외 시트 행들. 예외가 하나도 없는 것은 정상이므로 빈 리스트를 허용한다."""
        return self._read(self.exception_sheet, fresh, allow_empty=True)


class InvestigationDataError(Exception):
    """조사 시트 읽기 실패 (사용자에게는 일시 오류 문구로 응답)."""

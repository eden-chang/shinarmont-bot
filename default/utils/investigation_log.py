"""
utils/investigation_log.py — 조사 `로그` 시트 (가이드 §2.4 / §5)

컬럼: 일시(MM.DD HH:MM, KST) / 캐릭터명 / 장소명 / 포인트명 / 결과

봇은 이 시트에 **추가(append)만** 한다. 수정과 삭제는 하지 않는다.
성공한 진입·조사만 기록한다. 실패는 기록하지 않는다(관리자용 로그 파일에만 남긴다).

이 시트는 단순 기록처가 아니라 두 가지 판정의 기준(single source of truth)이다:
- 일일 조사 횟수 (§5.2): 당일 조사 기록 수를 세어 판정
- 현재 위치 복원 (§5.1): 재시작 시 당일 최근 진입 기록에서 복원 (별도 저장소 불필요)

연도: 일시에 연도가 없으므로 파싱 시 GAME_START_DATE의 연도(기본 2026)로 가정한다.
이벤트가 7~8월 안에 끝나므로 연도 경계 문제는 없다.
"""

import os
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

import pytz

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

try:
    from utils.logging_config import logger
except ImportError:  # pragma: no cover
    import logging
    logger = logging.getLogger('utils.investigation_log')

try:
    from config.settings import config
except ImportError:  # pragma: no cover
    config = None

from utils.investigation_sheet import normalize_name, display_name

KST = pytz.timezone('Asia/Seoul')

COL_TIME = '일시'
COL_CHARACTER = '캐릭터명'
COL_LOCATION = '장소명'
COL_POINT = '포인트명'
COL_RESULT = '결과'

COLUMNS = [COL_TIME, COL_CHARACTER, COL_LOCATION, COL_POINT, COL_RESULT]

# 진입 기록의 '결과' 값. 조사 기록과 구분하는 표지.
ENTRY_RESULT = '진입'
# 변동이 전혀 없는 조사의 '결과' 값 (§5.4)
NO_CHANGE_RESULT = '조사'

_TIME_FORMAT = '%m.%d %H:%M'
# 파싱은 연도를 앞에 붙여서 한다. 연도 없는 strptime은 3.15에서 동작이 바뀔 예정이고
# 윤년 2월 29일을 파싱하지 못한다(DeprecationWarning).
_PARSE_FORMAT = '%Y.%m.%d %H:%M'


def _log_sheet() -> str:
    return getattr(config, 'INVESTIGATION_LOG_SHEET', '로그')


def _assumed_year() -> int:
    """일시 파싱에 사용할 연도. GAME_START_DATE에서 추출, 실패 시 2026."""
    raw = str(getattr(config, 'GAME_START_DATE', '') or '')
    for sep in ('.', '-', '/'):
        head = raw.split(sep)[0].strip()
        if len(head) == 4 and head.isdigit():
            return int(head)
    return 2026


def now_stamp(now: Optional[datetime] = None) -> str:
    """현재 KST 시각을 'MM.DD HH:MM'으로 포맷 (예: 07.20 21:14)."""
    dt = now or datetime.now(KST)
    return dt.strftime(_TIME_FORMAT)


def parse_stamp(raw: Any) -> Optional[datetime]:
    """'MM.DD HH:MM' 문자열을 KST datetime으로 파싱. 실패 시 None."""
    text = str(raw or '').strip()
    if not text:
        return None
    try:
        naive = datetime.strptime(f"{_assumed_year()}.{text}", _PARSE_FORMAT)
    except (ValueError, TypeError):
        return None
    return KST.localize(naive)


def _read(sheets_manager) -> List[Dict[str, Any]]:
    """로그 시트 전체를 읽는다. 판정에 쓰이므로 항상 미캐시."""
    if sheets_manager is None:
        return []
    try:
        return sheets_manager.get_worksheet_data(_log_sheet(), use_cache=False) or []
    except Exception as e:
        logger.error(f"[조사] 로그 시트 조회 실패: {e}", exc_info=True)
        return []


def _append(sheets_manager, character: str, location: str, point: str, result: str) -> bool:
    if sheets_manager is None:
        logger.warning("[조사] 로그 시트 매니저 없음 - 기록 생략")
        return False
    values = [now_stamp(), display_name(character), display_name(location),
              display_name(point), result]
    ok = sheets_manager.append_row(_log_sheet(), values)
    if not ok:
        logger.error(f"[조사] 로그 기록 실패: {values}")
    return ok


def append_entry(sheets_manager, character: str, location: str) -> bool:
    """진입 기록 추가: 일시 / 캐릭터명 / 장소명 / (포인트명 빈 칸) / '진입'."""
    return _append(sheets_manager, character, location, '', ENTRY_RESULT)


def append_investigation(
    sheets_manager,
    character: str,
    location: str,
    point: str,
    summary: str,
) -> bool:
    """조사 기록 추가: 일시 / 캐릭터명 / 장소명 / 포인트명 / 변동 요약."""
    return _append(sheets_manager, character, location, point,
                   summary or NO_CHANGE_RESULT)


def _rows_for(rows: List[Dict[str, Any]], character: str) -> List[Dict[str, Any]]:
    target = normalize_name(character)
    return [r for r in rows if normalize_name(r.get(COL_CHARACTER)) == target]


def _is_today(row: Dict[str, Any], today) -> bool:
    stamp = parse_stamp(row.get(COL_TIME))
    return stamp is not None and stamp.date() == today


def _is_investigation(row: Dict[str, Any]) -> bool:
    """조사 기록인지 (진입 기록은 포인트명이 빈 칸이고 결과가 '진입')."""
    return bool(display_name(row.get(COL_POINT))) and \
        display_name(row.get(COL_RESULT)) != ENTRY_RESULT


def count_today_investigations(
    sheets_manager,
    character: str,
    since: Optional[datetime] = None,
) -> int:
    """당일(KST 달력 날짜 = 게임 일차) 성공한 조사 기록 수 (§5.2).

    진입·장소 목록·실패한 조사는 세지 않는다.

    Args:
        since: 주어지면 이 시각 이후의 기록만 센다.
            오픈일 횟수 보정(INVESTIGATION_OPEN_ADJUST='reset', §8)에서
            21:00 개방 이후의 조사만 세어 사실상 카운터를 리셋하는 데 쓴다.
    """
    today = datetime.now(KST).date()
    rows = _rows_for(_read(sheets_manager), character)

    count = 0
    for row in rows:
        if not _is_investigation(row):
            continue
        stamp = parse_stamp(row.get(COL_TIME))
        if stamp is None or stamp.date() != today:
            continue
        if since is not None and stamp < since:
            continue
        count += 1
    return count


def last_location_today(sheets_manager, character: str) -> Optional[str]:
    """당일 가장 최근 진입 기록의 장소명 (§5.1 재시작 복원용).

    로그 시트는 append-only이므로 시트 순서가 곧 시간 순서다.
    일차가 바뀌면 위치가 초기화되어야 하므로 당일 기록만 본다.
    """
    today = datetime.now(KST).date()
    rows = _rows_for(_read(sheets_manager), character)
    for row in reversed(rows):
        if not _is_today(row, today):
            continue
        if display_name(row.get(COL_RESULT)) == ENTRY_RESULT:
            name = display_name(row.get(COL_LOCATION))
            if name:
                return name
    return None


def format_summary(changes: List[str]) -> str:
    """변동 요약 문자열 생성 (§5.4).

    예: "담배 1 획득", "이성 +2", "재화 -5, 이성 +1". 변동이 없으면 "조사".
    """
    cleaned = [c for c in (changes or []) if c]
    if not cleaned:
        return NO_CHANGE_RESULT
    return ', '.join(cleaned)

"""
utils/daily_counter.py — 관리 시트 일일 카운터 유틸

`관리` 시트의 GM 트래킹 카운터(`추적`, `조사`)를 **증가**시킵니다.
컬럼명은 config(TRACK_COUNTER_COLUMN / INVESTIGATION_COUNTER_COLUMN)로 바꾼다.
- `check_and_inc_sheet(sheets_manager, user_id, column, limit)`:
  현재값이 한도 미만이면 +1 하고 True, 한도 이상이면 그대로 False.
- `dec_sheet(...)`: 후속 처리 실패 시 되돌리기.

**0시 리셋은 이 모듈의 책임이 아니다.** `추적`/`조사`/`출석` 컬럼은 별도 스케줄러 봇이
00:00에 초기화한다(2026-07-16 운영 결정). 리셋 주체가 둘이면 서로의 결과를 덮어쓰고
실패해도 누구 탓인지 알 수 없다 — 이 봇은 세기만 하고 지우지 않는다.

동시성: 사용자별 락(`get_lock_manager`)으로 read-modify-write를 보호합니다.
`관리` 시트는 항상 `use_cache=False`로 조회합니다.
"""

import os
import sys
from typing import Any, Dict, List, Optional, Tuple

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

try:
    from config.settings import config
    from utils.logging_config import logger
    from utils.lock_manager import get_lock_manager
except ImportError:
    import logging
    logger = logging.getLogger('utils.daily_counter')
    from utils.lock_manager import get_lock_manager


MANAGEMENT_SHEET = '관리'
ID_COLUMN = '아이디'
def _parse_int(raw: Any) -> int:
    """카운터 셀 값을 정수로 파싱(빈 값/파싱 실패 시 0)."""
    try:
        text = str(raw).strip()
        if not text:
            return 0
        return int(float(text))
    except (ValueError, TypeError):
        return 0


def _find_user_row(data: List[Dict[str, Any]], user_id: str) -> Optional[Dict[str, Any]]:
    """관리 시트 데이터에서 아이디가 일치하는 행 반환."""
    for row in data:
        if str(row.get(ID_COLUMN, '')).strip() == str(user_id).strip():
            return row
    return None


def _col_index_from_row(row: Dict[str, Any], column: str) -> Optional[int]:
    """행 딕셔너리의 키 순서(= 시트 열 순서)에서 컬럼의 1-indexed 열 번호 반환.

    get_worksheet_data가 `dict(zip(header, row))`로 만들므로 키 순서는 헤더 순서와 같다.
    이미 읽은 행에서 산출해 별도 헤더 조회(API 호출)를 없앤다.
    """
    header = [k for k in row.keys() if k != '_row_number']
    try:
        return header.index(column) + 1
    except ValueError:
        logger.warning(f"[daily_counter] 관리 시트에 '{column}' 컬럼 없음")
        return None


def check_and_inc_sheet(sheets_manager, user_id: str, column: str, limit: int) -> bool:
    """
    `관리` 시트의 카운터 컬럼을 확인하고, 한도 미만이면 +1.

    Args:
        sheets_manager: 기본 시트 매니저(관리 시트 접근).
        user_id: 사용자 아이디.
        column: 카운터 컬럼명(예: '오늘추적', '오늘조사').
        limit: 일일 허용 한도(현재값이 이 값 이상이면 증가 불가).

    Returns:
        bool: 증가에 성공하면 True, 한도 초과/오류면 False.
    """
    if sheets_manager is None:
        logger.warning("[daily_counter] sheets_manager 없음 - 카운터 증가 불가")
        return False

    lock_manager = get_lock_manager()
    with lock_manager.acquire_lock(str(user_id), timeout=10.0) as acquired:
        if not acquired:
            logger.warning(f"[daily_counter] 락 획득 실패: user={user_id}, col={column}")
            return False

        # 락 내부 재조회(stale 방지), 관리 시트는 항상 use_cache=False
        try:
            data = sheets_manager.get_worksheet_data(MANAGEMENT_SHEET, use_cache=False)
        except Exception as e:
            logger.error(f"[daily_counter] 관리 시트 조회 실패: {e}")
            return False

        user_row = _find_user_row(data, user_id)
        if user_row is None:
            logger.info(f"[daily_counter] 관리 시트에 사용자 {user_id} 행 없음")
            return False

        if column not in user_row:
            logger.warning(f"[daily_counter] 관리 시트에 '{column}' 컬럼 없음")
            return False

        current = _parse_int(user_row.get(column))
        if current >= limit:
            logger.debug(
                f"[daily_counter] 한도 도달: user={user_id}, {column}={current}/{limit}"
            )
            return False

        col_index = _col_index_from_row(user_row, column)
        if col_index is None:
            return False

        row_index = user_row.get('_row_number')
        if not row_index:
            logger.warning(f"[daily_counter] 행 번호 없음: user={user_id}")
            return False

        ok = sheets_manager.update_cell(MANAGEMENT_SHEET, row_index, col_index, current + 1)
        if not ok:
            logger.error(
                f"[daily_counter] 셀 업데이트 실패: user={user_id}, {column} "
                f"row={row_index}, col={col_index}"
            )
            return False

        logger.info(
            f"[daily_counter] {column} 증가: user={user_id}, {current} -> {current + 1} "
            f"(한도 {limit})"
        )
        return True


def dec_sheet(sheets_manager, user_id: str, column: str, floor: int = 0) -> bool:
    """카운터 컬럼을 1 감소(하한 floor)한다 — 소비 후 실패 시 롤백용.

    check_and_inc_sheet와 동일하게 사용자별 락 안에서 재조회→감소한다.
    호출측은 이 함수를 자신의 사용자 락 '밖'에서 불러야 한다(락 비재진입).

    Returns:
        bool: 감소(또는 이미 floor라 변화 없음)에 성공하면 True.
    """
    if sheets_manager is None:
        return False

    lock_manager = get_lock_manager()
    with lock_manager.acquire_lock(str(user_id), timeout=10.0) as acquired:
        if not acquired:
            logger.warning(f"[daily_counter] 롤백 락 획득 실패: user={user_id}, col={column}")
            return False

        try:
            data = sheets_manager.get_worksheet_data(MANAGEMENT_SHEET, use_cache=False)
        except Exception as e:
            logger.error(f"[daily_counter] 롤백 조회 실패: {e}")
            return False

        user_row = _find_user_row(data, user_id)
        if user_row is None or column not in user_row:
            return False

        current = _parse_int(user_row.get(column))
        new_val = max(floor, current - 1)
        if new_val == current:
            return True

        col_index = _col_index_from_row(user_row, column)
        row_index = user_row.get('_row_number')
        if col_index is None or not row_index:
            return False

        ok = sheets_manager.update_cell(MANAGEMENT_SHEET, row_index, col_index, new_val)
        if ok:
            logger.info(f"[daily_counter] {column} 롤백: user={user_id}, {current} -> {new_val}")
        else:
            logger.error(f"[daily_counter] {column} 롤백 실패: user={user_id}")
        return ok

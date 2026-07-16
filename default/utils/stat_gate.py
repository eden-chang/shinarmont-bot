"""
utils/stat_gate.py — 능력치 게이트 / 이성 문구 발송 / 일일 감소

시너몬트 봇 공통 인프라 유틸(코딩_계획 §5).

제공 함수:
- is_hospitalized(user_row) -> bool
    건강 <= HEALTH_HOSPITALIZE_THRESHOLD 이면 입원(파생, 무저장).
- apply_sanity_messages(sheets_manager, system_sheets_manager, api, user_id)
    관리 시트에서 이성/이름을 읽고, 시스템 '이성' 시트의 문구를
    임계값별로 1회씩 DM 발송(락 기반 check-and-set, 발송여부 sticky).
- daily_decay_all(sheets_manager, system_sheets_manager, api)
    관리 시트 전 캐릭터 건강/이성을 일일 감소(하한 0)한 뒤
    각 캐릭터에 대해 apply_sanity_messages를 실행.

시트 접근 규칙:
- 관리(건강/이성/이름/아이디) = 기본 sheets_manager, 항상 use_cache=False.
- 이성(이름/이성문구1/문구1 발송여부/이성문구2/문구2 발송여부) = system_sheets_manager.
"""

import os
import sys
from typing import Any, Dict, List, Optional, Tuple, Union

# 경로 설정 (VM 환경 대응)
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

try:
    from config.settings import config
    from utils.logging_config import logger
    from utils.lock_manager import get_lock_manager
    from utils.dm_sender import queue_dm
    from utils.store_helpers import invalidate_user_cache
except ImportError:  # pragma: no cover - VM 환경 폴백
    import logging
    logger = logging.getLogger('stat_gate')

    import importlib.util
    _config_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'settings.py')
    _spec = importlib.util.spec_from_file_location("settings", _config_path)
    _settings = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_settings)
    config = _settings.config

    from utils.lock_manager import get_lock_manager

    def queue_dm(receiver_id, message):  # type: ignore
        logger.error("DM 전송기 미초기화 - queue_dm 폴백")

    def invalidate_user_cache():  # type: ignore
        return False


# 관리/이성 시트 컬럼명 상수
MGMT_SHEET = '관리'
SANITY_SHEET = '이성'
COL_NAME = '이름'
COL_ID = '아이디'
COL_HEALTH = '건강'
COL_SANITY = '이성'

SANITY_MSG_COLS = [
    # (임계값 config 키, 문구 컬럼, 발송여부 컬럼, 기본 임계)
    ('SANITY_MSG1_THRESHOLD', '이성문구1', '문구1 발송여부', 60),
    ('SANITY_MSG2_THRESHOLD', '이성문구2', '문구2 발송여부', 30),
]


def _to_int(raw: Any, default: Optional[int] = None) -> Optional[int]:
    """시트 셀 값을 정수로 변환. 실패 시 default 반환."""
    try:
        return int(float(raw))
    except (ValueError, TypeError):
        return default


def _row_keys(row: Dict[str, Any]) -> List[str]:
    """행 딕셔너리에서 헤더 순서 키 목록(‘_row_number’ 제외)."""
    return [k for k in row.keys() if k != '_row_number']


def is_hospitalized(user_row: Union[Dict[str, Any], int, float, str]) -> bool:
    """
    입원(파생) 여부.

    건강 <= HEALTH_HOSPITALIZE_THRESHOLD 이면 True.
    - user_row 가 dict 이면 '건강' 컬럼을 읽는다(없으면 100으로 간주).
    - user_row 가 숫자/숫자문자열이면 건강 수치로 간주한다.
    파싱 불가하면 False(입원 아님)로 처리한다.
    """
    threshold = getattr(config, 'HEALTH_HOSPITALIZE_THRESHOLD', 20)

    if isinstance(user_row, dict):
        health_raw = user_row.get(COL_HEALTH, 100)
    else:
        health_raw = user_row

    health = _to_int(health_raw, default=None)
    if health is None:
        return False
    return health <= threshold


def _find_row_by(rows: List[Dict[str, Any]], column: str, value: str) -> Optional[Dict[str, Any]]:
    target = str(value).strip()
    for row in rows:
        if str(row.get(column, '')).strip() == target:
            return row
    return None


def _resolve_character(sheets_manager, user_id: str) -> Optional[Tuple[str, str, int]]:
    """
    관리 시트에서 user_id(아이디 우선, 없으면 이름)로 캐릭터를 찾아
    (이름, DM 수신 아이디, 이성) 튜플 반환. 없으면 None.
    """
    try:
        mgmt = sheets_manager.get_worksheet_data(MGMT_SHEET, use_cache=False)
    except Exception as e:
        logger.warning(f"[stat_gate] 관리 시트 조회 실패: {e}")
        return None

    row = _find_row_by(mgmt, COL_ID, user_id)
    if row is None:
        # user_id 가 이름으로 전달된 경우 폴백
        row = _find_row_by(mgmt, COL_NAME, user_id)
    if row is None:
        return None

    name = str(row.get(COL_NAME, '')).strip()
    recipient = str(row.get(COL_ID, '')).strip() or str(user_id).strip()
    sanity = _to_int(row.get(COL_SANITY, 100), default=100)
    if not name:
        return None
    return name, recipient, sanity


def apply_sanity_messages(sheets_manager, system_sheets_manager, api, user_id: str) -> None:
    """
    이성 임계 문구 자동 발송(락 기반 check-and-set).

    관리 시트에서 대상 캐릭터의 이름/이성을 확보한 뒤,
    시스템 '이성' 시트에서 이름으로 행을 찾아 임계값별 문구를
    발송여부가 공란일 때 1회 DM 발송하고 발송여부='O'로 표시한다.
    """
    if sheets_manager is None or system_sheets_manager is None:
        return

    resolved = _resolve_character(sheets_manager, user_id)
    if resolved is None:
        return
    name, recipient, sanity = resolved

    lock_manager = get_lock_manager()
    with lock_manager.acquire_lock(f"sanity_msg:{recipient}", timeout=10.0) as acquired:
        if not acquired:
            logger.debug(f"[stat_gate] 이성 문구 락 획득 실패: {recipient}")
            return
        _apply_sanity_messages_locked(system_sheets_manager, name, sanity, recipient)


def _apply_sanity_messages_locked(system_sheets_manager, name: str, sanity: int, recipient: str) -> None:
    """락 내부: '이성' 시트를 재조회하여 문구 발송/발송여부 갱신(원자적 batch)."""
    try:
        rows = system_sheets_manager.get_worksheet_data(SANITY_SHEET, use_cache=False)
    except Exception as e:
        logger.warning(f"[stat_gate] '이성' 시트 조회 실패: {e}")
        return

    srow = _find_row_by(rows, COL_NAME, name)
    if srow is None:
        logger.debug(f"[stat_gate] '이성' 시트에 '{name}' 행 없음 - 문구 생략")
        return

    header = _row_keys(srow)
    row_index = srow.get('_row_number')
    if row_index is None:
        return

    # 발송여부 플래그를 '먼저' 기록(성공 확인)한 뒤에 DM을 큐잉한다.
    # 순서를 뒤집으면 플래그 기록 실패 시 다음 스윕에서 같은 문구가 중복 발송된다.
    pending: List[Tuple[str, str]] = []          # (phrase, flag_col)
    updates: List[Tuple[int, int, Any]] = []
    for cfg_key, phrase_col, flag_col, default_th in SANITY_MSG_COLS:
        threshold = getattr(config, cfg_key, default_th)
        if sanity > threshold:
            continue
        if flag_col not in header:
            # 발송여부 컬럼이 없으면 재발송 방지 불가 → 발송 생략
            logger.debug(f"[stat_gate] '이성' 시트에 '{flag_col}' 컬럼 없음 - 생략")
            continue
        already_sent = str(srow.get(flag_col, '')).strip()
        phrase = str(srow.get(phrase_col, '')).strip()
        if already_sent or not phrase:
            continue
        updates.append((row_index, header.index(flag_col) + 1, 'O'))
        pending.append((phrase, flag_col))

    if not pending:
        return

    ok = system_sheets_manager.batch_update_cells(SANITY_SHEET, updates)
    if not ok:
        # 플래그 기록 실패 → 발송 보류(다음 검사에서 한 번만 발송되도록)
        logger.warning(f"[stat_gate] 이성 문구 발송여부 기록 실패 - 발송 보류: {name}({recipient})")
        return

    for phrase, flag_col in pending:
        queue_dm(recipient, phrase)
        logger.info(f"[stat_gate] 이성 문구 발송: {name}({recipient}) '{flag_col}' (이성={sanity})")


def _decay_value(raw: Any, decay: int) -> Optional[int]:
    """현재값에서 decay 만큼 감소(하한 0). 파싱 불가하면 None(스킵)."""
    cur = _to_int(raw, default=None)
    if cur is None:
        return None
    new_value = cur - decay
    if new_value < 0:
        new_value = 0
    return new_value


def daily_decay_all(sheets_manager, system_sheets_manager, api) -> Dict[str, int]:
    """
    관리 시트 전 캐릭터의 건강/이성을 일일 감소(하한 0)하고,
    변동이 발생한 캐릭터에 대해 이성 문구 발송 검사를 수행한다.

    동시성: 각 캐릭터를 그 사용자 락 안에서 재조회→감소→반영한다(명령어와 같은 락).
    스케줄러 스윕이 lock 없이 배치로 쓰면 명령어의 lock→재조회→쓰기와 경합해
    갱신이 유실될 수 있으므로, 사용자별로 직렬화한다(00시 저부하라 사용자당 1회 재조회 허용).

    Returns:
        dict: {'updated': 변동 캐릭터 수}
    """
    result = {'updated': 0}
    if sheets_manager is None:
        return result

    try:
        mgmt = sheets_manager.get_worksheet_data(MGMT_SHEET, use_cache=False)
    except Exception as e:
        logger.error(f"[stat_gate] 일일 감소 - 관리 시트 조회 실패: {e}")
        return result

    if not mgmt:
        return result

    health_decay = getattr(config, 'DAILY_HEALTH_DECAY', 5)
    sanity_decay = getattr(config, 'DAILY_SANITY_DECAY', 5)

    # 대상 목록만 뽑는다(값은 각자 락 안에서 최신으로 재조회).
    targets: List[Tuple[str, str]] = []  # (lock_key, affected_id)
    for row in mgmt:
        if row.get('_row_number') is None:
            continue
        name = str(row.get(COL_NAME, '')).strip()
        uid = str(row.get(COL_ID, '')).strip()
        if not name and not uid:
            continue
        # 명령어는 user_id(=아이디)로 락하므로 아이디 우선. 이성 문구 DM도 아이디 기준.
        targets.append((uid or name, uid or name))

    lock_manager = get_lock_manager()
    any_change = False
    affected_ids: List[str] = []

    for lock_key, affected_id in targets:
        try:
            changed = _decay_one_user(sheets_manager, lock_manager, lock_key, health_decay, sanity_decay)
        except Exception as e:
            logger.warning(f"[stat_gate] 일일 감소 실패({lock_key}): {e}")
            changed = False
        if changed:
            any_change = True
            result['updated'] += 1
            affected_ids.append(affected_id)

    if any_change:
        invalidate_user_cache()

    for identifier in affected_ids:
        try:
            apply_sanity_messages(sheets_manager, system_sheets_manager, api, identifier)
        except Exception as e:
            logger.warning(f"[stat_gate] 이성 문구 검사 실패({identifier}): {e}")

    return result


def _decay_one_user(sheets_manager, lock_manager, lock_key: str,
                    health_decay: int, sanity_decay: int) -> bool:
    """한 캐릭터를 그 사용자 락 안에서 재조회→감소→반영. 변동 있으면 True."""
    with lock_manager.acquire_lock(str(lock_key), timeout=10.0) as acquired:
        if not acquired:
            logger.warning(f"[stat_gate] 일일 감소 락 획득 실패: {lock_key}")
            return False

        try:
            data = sheets_manager.get_worksheet_data(MGMT_SHEET, use_cache=False)
        except Exception as e:
            logger.error(f"[stat_gate] 일일 감소 재조회 실패({lock_key}): {e}")
            return False

        row = None
        for r in data or []:
            if (str(r.get(COL_ID, '')).strip() == str(lock_key)
                    or str(r.get(COL_NAME, '')).strip() == str(lock_key)):
                row = r
                break
        if row is None:
            return False

        row_index = row.get('_row_number')
        if row_index is None:
            return False

        keys = _row_keys(row)
        updates: List[Tuple[int, int, Any]] = []
        if COL_HEALTH in keys:
            new_h = _decay_value(row.get(COL_HEALTH), health_decay)
            if new_h is not None:
                updates.append((row_index, keys.index(COL_HEALTH) + 1, new_h))
        if COL_SANITY in keys:
            new_s = _decay_value(row.get(COL_SANITY), sanity_decay)
            if new_s is not None:
                updates.append((row_index, keys.index(COL_SANITY) + 1, new_s))

        if not updates:
            return False

        ok = sheets_manager.batch_update_cells(MGMT_SHEET, updates)
        if not ok:
            logger.warning(f"[stat_gate] 일일 감소 반영 실패: {lock_key}")
            return False
        return True

    result['updated'] = len(affected_ids)
    return result

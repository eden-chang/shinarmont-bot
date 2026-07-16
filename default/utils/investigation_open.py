"""
utils/investigation_open.py — 조사 자동 개방 (가이드 §6)

조사 오픈 일자의 각 일차에 해당하는 날짜 21:00 KST에 실행된다.

실행 순서 (한 번의 개방 작업 안에서):
1. 장소 시트: '조사 오픈 일자'가 그 일차인 행의 '현재 조사 가능'을 '가능'으로.
   같은 시트에 같은 포인트명의 다른 오픈 일자 행이 '가능'이면 '불가능'으로 (포인트 버전 전환).
2. 진입 시트: 같은 규칙을 장소명 기준으로 적용.

필수 규칙:
- **단방향 원칙**: 무언가를 '불가능'으로 바꾸는 유일한 경우는 버전 전환이며,
  이번 일차에 새 버전 행이 생긴 **이름에 한해서만** 적용한다. 그 외에는 켜기만 한다.
- **운영 수동 조작 존중**: 운영진이 아무 때나 수동으로 켜고 닫을 수 있고 스케줄러가
  이를 되돌리면 안 된다. 따라서 각 오픈 일자는 정확히 그 시각에 **한 번만** 실행하며
  주기적 동기화(매시간 강제 대조 등)를 하지 않는다.
- **멱등성**: 중복 실행되어도 결과가 같다(이미 가능인 행은 건너뛴다).
- **놓친 실행**: 기동 시 '지나간 오픈 일시 중 실행 기록이 없는 것'을 감지한다.
  보정 실행은 운영진이 그 사이 수동으로 닫은 행을 되켤 위험이 있으므로
  기본값은 관리자에게 알리기만 하고 실행하지 않는다
  (config.INVESTIGATION_MISSED_OPEN_CONFIRM).

변주 세트(같은 이름·같은 오픈 일자)는 함께 켜지고 함께 유지된다.
버전 전환은 오픈 일자가 **다른** 행만 끈다.
"""

import json
import os
import sys
import tempfile
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import pytz

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

try:
    from utils.logging_config import logger
except ImportError:  # pragma: no cover
    import logging
    logger = logging.getLogger('utils.investigation_open')

try:
    from config.settings import config
except ImportError:  # pragma: no cover
    config = None

from utils.investigation_notify import notify_admin, notify_admin_once
from utils.investigation_sheet import (
    AVAILABLE_VALUE, COL_AVAILABLE, COL_AVAILABLE_LEGACY, COL_LOCATION, COL_POINT,
    InvestigationDataError, InvestigationRepo, config_int, display_name, is_available,
    normalize_name, open_day_of,
)

KST = pytz.timezone('Asia/Seoul')

_state_lock = threading.Lock()


def unavailable_value() -> str:
    """'불가능'으로 되돌릴 때 쓸 값 (§6.1 버전 전환).

    GM 드롭다운의 선택지와 맞춰야 시트에 경고 표시가 생기지 않는다.
    판정은 "'가능'이 아니면 전부 불가능"이라 어떤 값이든 동작에는 지장이 없다.
    """
    return str(getattr(config, 'INVESTIGATION_UNAVAILABLE_VALUE', '불가능') or '불가능')


# 하위 호환(테스트/외부 참조용). 값 변경은 config로 한다.
UNAVAILABLE_VALUE = '불가능'


# --------------------------------------------------------------------------- #
# 실행 기록 (멱등성·놓친 실행 감지)
# --------------------------------------------------------------------------- #
def _state_path() -> str:
    raw = getattr(config, 'INVESTIGATION_OPEN_STATE_FILE', 'state/investigation_opens.json')
    if os.path.isabs(raw):
        return raw
    base = getattr(config, 'BASE_DIR', None)
    return os.path.join(str(base), raw) if base else raw


def load_state() -> Dict[str, str]:
    """{일차 라벨: 실행 시각 ISO} 형태의 실행 기록."""
    path = _state_path()
    with _state_lock:
        if not os.path.exists(path):
            return {}
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError) as e:
            logger.error(f"[조사 개방] 실행 기록 읽기 실패 ({path}): {e}")
            return {}


def _save_state(state: Dict[str, str]) -> None:
    """임시파일 + rename으로 원자적 저장."""
    path = _state_path()
    with _state_lock:
        try:
            directory = os.path.dirname(path) or '.'
            os.makedirs(directory, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=directory, suffix='.tmp')
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as f:
                    json.dump(state, f, ensure_ascii=False, indent=1)
                os.replace(tmp, path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        except OSError as e:
            logger.error(f"[조사 개방] 실행 기록 저장 실패 ({path}): {e}")


def mark_executed(label: str, when: Optional[datetime] = None) -> None:
    state = load_state()
    state[label] = (when or datetime.now(KST)).isoformat()
    _save_state(state)


def is_executed(label: str) -> bool:
    return label in load_state()


# --------------------------------------------------------------------------- #
# 일정
# --------------------------------------------------------------------------- #
def scheduled_opens() -> List[Tuple[str, datetime]]:
    """config의 (일차 라벨, 개방 일시 KST) 목록. 시각순 정렬."""
    hour = config_int('INVESTIGATION_OPEN_HOUR', 21)
    minute = config_int('INVESTIGATION_OPEN_MINUTE', 0)

    result: List[Tuple[str, datetime]] = []
    for label, date_str in getattr(config, 'INVESTIGATION_OPEN_SCHEDULE', []) or []:
        try:
            day = datetime.strptime(str(date_str).strip(), '%Y-%m-%d')
        except (ValueError, TypeError):
            logger.error(f"[조사 개방] 잘못된 개방 날짜 형식: {label}={date_str} - 건너뜀")
            continue
        result.append((label, KST.localize(day.replace(hour=hour, minute=minute))))
    result.sort(key=lambda x: x[1])
    return result


def pending_opens(now: Optional[datetime] = None) -> List[Tuple[str, datetime]]:
    """이미 지나간 개방 일시 중 실행 기록이 없는 것 (§6.3-4)."""
    current = now or datetime.now(KST)
    executed = load_state()
    return [
        (label, when) for label, when in scheduled_opens()
        if when <= current and label not in executed
    ]


def open_time_passed_today(now: Optional[datetime] = None) -> Optional[datetime]:
    """오늘이 개방일이고 개방 시각이 이미 지났으면 그 시각을 반환. 아니면 None.

    오픈일 조사 횟수 보정(§8)에서 '개방 이후'를 판정하는 데 쓴다.
    """
    current = now or datetime.now(KST)
    for _, when in scheduled_opens():
        if when.date() == current.date() and when <= current:
            return when
    return None


# --------------------------------------------------------------------------- #
# 개방 로직
# --------------------------------------------------------------------------- #
def _availability_column(rows: List[Dict[str, Any]]) -> Optional[Tuple[str, int]]:
    """'현재 조사 가능' 컬럼명과 1-indexed 열 번호를 행 키 순서에서 산출."""
    for row in rows:
        header = [k for k in row.keys() if k != '_row_number']
        for name in (COL_AVAILABLE, COL_AVAILABLE_LEGACY):
            if name in header:
                return name, header.index(name) + 1
        return None
    return None


def plan_sheet(
    rows: List[Dict[str, Any]],
    name_column: str,
    day_label: str,
) -> Tuple[List[Tuple[int, int, Any]], int, int]:
    """한 시트의 개방 계획을 세운다 (시트 쓰기 없음 — 순수 계산).

    Returns:
        (updates, 켠 행 수, 끈 행 수)
    """
    target = normalize_name(day_label)
    if not rows or not target:
        return [], 0, 0

    column = _availability_column(rows)
    if column is None:
        return [], 0, 0
    _, col_index = column

    # 이번 일차에 새 행이 있는 이름들 — 버전 전환은 이 이름들에만 적용한다
    opening_names = {
        normalize_name(r.get(name_column))
        for r in rows
        if normalize_name(open_day_of(r)) == target and normalize_name(r.get(name_column))
    }
    if not opening_names:
        return [], 0, 0

    updates: List[Tuple[int, int, Any]] = []
    opened = closed = 0

    for row in rows:
        name = normalize_name(row.get(name_column))
        row_number = row.get('_row_number')
        if not name or not row_number:
            continue

        if normalize_name(open_day_of(row)) == target:
            # 이번 일차 행 → 켠다 (변주 세트는 전부 함께 켜진다)
            if is_available(row):
                continue  # 멱등성: 이미 가능이면 건너뜀
            updates.append((row_number, col_index, AVAILABLE_VALUE))
            opened += 1
        elif name in opening_names and is_available(row):
            # 같은 이름의 이전 버전이 켜져 있다 → 끈다 (버전 전환)
            updates.append((row_number, col_index, unavailable_value()))
            closed += 1

    return updates, opened, closed


def run_open(
    investigation_sheets_manager,
    day_label: str,
    api=None,
    record: bool = True,
) -> Dict[str, int]:
    """한 일차의 조사 개방을 실행한다 (§6.1).

    Args:
        record: True면 실행 기록을 남긴다(중복 실행 방지). 수동 재실행 시 False.

    Returns:
        {'opened': n, 'closed': n, 'sheets': n}
    """
    if investigation_sheets_manager is None:
        logger.error("[조사 개방] 조사 시트 매니저 없음 - 개방 생략")
        return {'opened': 0, 'closed': 0, 'sheets': 0,
                'failures': ['조사 시트 매니저 없음']}

    logger.info(f"[조사 개방] '{day_label}' 개방 시작")
    repo = InvestigationRepo(investigation_sheets_manager)
    total_opened = total_closed = touched = 0
    failures: List[str] = []

    try:
        entry_rows = repo.entry_rows(fresh=True)
    except InvestigationDataError as e:
        logger.error(f"[조사 개방] 진입 시트 조회 실패 - 개방 중단: {e}")
        notify_admin(f"'{day_label}' 개방 실패 — 진입 시트를 읽을 수 없습니다: {e}", api=api)
        # 실행 기록을 남기지 않으므로 재기동 시 handle_missed_opens가 다시 잡는다
        return {'opened': 0, 'closed': 0, 'sheets': 0, 'failures': ['진입 시트 읽기 실패']}

    # 1) 장소 시트 (진입 시트의 장소명이 화이트리스트 — 템플릿 시트 제외)
    for location in repo.known_locations(entry_rows):
        try:
            rows = repo.point_rows(location, fresh=True)
        except InvestigationDataError as e:
            logger.error(f"[조사 개방] '{location}' 시트 조회 실패 - 건너뜀: {e}")
            failures.append(f"'{location}' 읽기 실패")
            notify_admin(
                f"'{day_label}' 개방 중 '{location}' 시트를 읽지 못해 건너뛰었습니다.", api=api)
            continue

        updates, opened, closed = plan_sheet(rows, COL_POINT, day_label)
        if not updates:
            continue
        if investigation_sheets_manager.batch_update_cells(display_name(location), updates):
            total_opened += opened
            total_closed += closed
            touched += 1
            logger.info(f"[조사 개방] '{location}': 개방 {opened}건, 이전 버전 종료 {closed}건")
        else:
            logger.error(f"[조사 개방] '{location}' 시트 쓰기 실패")
            failures.append(f"'{location}' 쓰기 실패")
            notify_admin(f"'{day_label}' 개방 중 '{location}' 시트 쓰기에 실패했습니다.", api=api)

    # 2) 진입 시트 버전 전환
    updates, opened, closed = plan_sheet(entry_rows, COL_LOCATION, day_label)
    if updates:
        entry_sheet = repo.entry_sheet
        if investigation_sheets_manager.batch_update_cells(entry_sheet, updates):
            total_opened += opened
            total_closed += closed
            touched += 1
            logger.info(f"[조사 개방] 진입 시트: 개방 {opened}건, 이전 버전 종료 {closed}건")
        else:
            logger.error("[조사 개방] 진입 시트 쓰기 실패")
            failures.append("진입 시트 쓰기 실패")
            notify_admin(f"'{day_label}' 개방 중 진입 시트 쓰기에 실패했습니다.", api=api)

    # 실행 기록은 **전부 성공했을 때만** 남긴다.
    # 일부라도 실패했는데 '실행 완료'로 찍으면 handle_missed_opens가 영영 감지하지 못해
    # 특정 장소만 닫힌 채로 이벤트가 굴러간다. 개방은 멱등하므로 재실행이 안전하다.
    if record and not failures:
        mark_executed(day_label)

    # 5) 개방 완료 후 관리자 DM (§6.3-5)
    if failures:
        notify_admin(
            f"⚠️ {day_label} 조사 개방이 부분 실패했습니다 ({', '.join(failures)}).\n"
            f"성공: 개방 {total_opened}건 / 종료 {total_closed}건.\n"
            f"실행 기록을 남기지 않았으니 원인을 고친 뒤 [조사 개방/{day_label}]로 "
            f"재실행하거나 봇을 재기동해 주세요(개방은 멱등합니다).",
            api=api,
        )
    else:
        notify_admin(
            f"{day_label} 조사 {total_opened}건 개방 완료"
            + (f" (이전 버전 {total_closed}건 종료)" if total_closed else ""),
            api=api,
        )
    logger.info(
        f"[조사 개방] '{day_label}' 완료 — 개방 {total_opened}, 종료 {total_closed}, "
        f"시트 {touched}개, 실패 {len(failures)}건"
    )
    return {
        'opened': total_opened,
        'closed': total_closed,
        'sheets': touched,
        'failures': failures,
    }


# --------------------------------------------------------------------------- #
# 놓친 실행 처리 (§6.3-4)
# --------------------------------------------------------------------------- #
def handle_missed_opens(investigation_sheets_manager, api=None) -> List[str]:
    """기동 시 놓친 개방을 감지한다.

    기본값(INVESTIGATION_MISSED_OPEN_CONFIRM=True)은 관리자에게 알리기만 하고
    실행하지 않는다. 보정 실행은 운영진이 그 사이 수동으로 닫은 행을 되켤 수 있기 때문이다.

    Returns:
        감지된 일차 라벨 목록.
    """
    missed = pending_opens()
    if not missed:
        return []

    labels = [label for label, _ in missed]
    confirm = bool(getattr(config, 'INVESTIGATION_MISSED_OPEN_CONFIRM', True))

    if confirm:
        # 같은 목록에 대한 알림은 프로세스당 1회. 미실행 일차를 방치하면
        # 재기동할 때마다 같은 DM이 날아가 알림 피로가 쌓인다.
        notify_admin_once(
            f"missed-opens:{','.join(labels)}",
            f"⚠️ 실행되지 않은 조사 개방이 있습니다: {', '.join(labels)}\n"
            f"봇이 해당 시각에 꺼져 있었던 것으로 보입니다. 수동으로 닫아 둔 행을 "
            f"되켤 수 있어 자동 실행하지 않았습니다. 확인 후 개방하려면 "
            f"[조사 개방/{labels[0]}] 명령을 사용하거나 "
            f"INVESTIGATION_MISSED_OPEN_CONFIRM=False로 두고 재기동해 주세요.",
            api=api,
        )
        logger.warning(f"[조사 개방] 놓친 개방 감지(알림만): {labels}")
        return labels

    logger.warning(f"[조사 개방] 놓친 개방 보정 실행: {labels}")
    for label, _ in missed:
        run_open(investigation_sheets_manager, label, api=api)
    return labels

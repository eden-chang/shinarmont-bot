"""
소문(rumor) 유틸리티

AUTO(스케줄러) 잡용 소문 처리 유틸.
- ``send_weekly_rumor``: '소문' 시트의 ``상태=확정`` 행을 무작위 수신자(RUMOR_RECIPIENTS 3~5명)
  에게 DM 발송하고 ``상태=발송``으로 갱신. 수신자 이성이 낮으면 distortion.apply로 왜곡.
  '미확인 정보' 톤 접두를 붙인다.
- ``propose_candidates``: '행동로그'의 소문화 미표시 항목을 무작위 표본으로 '소문' 시트에
  후보(상태=후보, 출처=자동)로 적재한다.

'소문' 시트 컬럼(5열): 일차, 출처, 내용, 상태, 원본행
'행동로그' 컬럼(7열): 일시, 일차, 행위자, 종류, 대상, 요약, 소문화

'원본행'은 봇이 후보를 적재할 때 남기는 행동로그 행 번호다. GM은 건드리지 않는다.
이게 있어야 GM이 '내용'을 다듬어도 원본 로그를 되짚어 소문화 표시를 할 수 있다.

docs/코딩_계획.md §5(유틸 계약)·§6(AUTO), docs/스프레드시트_구성.md '소문' 흐름 참조.
"""

import os
import sys
import random
from typing import Any, Dict, List, Optional, Tuple

# 경로 설정 (VM 환경 대응)
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

try:
    from config.settings import config
except ImportError:  # pragma: no cover - VM 폴백
    config = None

try:
    from utils.logging_config import logger
except ImportError:  # pragma: no cover
    import logging
    logger = logging.getLogger('rumor')

from utils import mention_guard

try:
    from utils.lock_manager import get_lock_manager
except ImportError:  # pragma: no cover
    get_lock_manager = None


# 시트/컬럼 상수
RUMOR_SHEET = '소문'
ACTION_LOG_SHEET = '행동로그'
ROSTER_SHEET = '명단'
MANAGEMENT_SHEET = '관리'

RUMOR_COL_DAY = '일차'
RUMOR_COL_SOURCE = '출처'
RUMOR_COL_CONTENT = '내용'
RUMOR_COL_STATE = '상태'
# 후보를 뽑아온 행동로그의 행 번호. GM이 '내용'을 다듬어도 원본을 잃지 않게 하는 끈이다.
# 문자열 대조만 쓰던 시절엔 GM이 문구를 손보는 순간 역링크가 끊겨, 그 사건이
# 소문화 표시를 못 받고 매주 후보로 다시 올라왔다(2026-07-16 수정).
RUMOR_COL_SOURCE_ROW = '원본행'

STATE_CANDIDATE = '후보'
STATE_CONFIRMED = '확정'
STATE_SENT = '발송'
SOURCE_AUTO = '자동'

# '미확인 정보' 톤 접두
RUMOR_PREFIX = "[미확인 정보] 마을에 확인되지 않은 소문이 돌고 있습니다.\n\n"


def _cfg(key: str, default: Any) -> Any:
    """config 값 안전 조회."""
    return getattr(config, key, default) if config is not None else default


def _queue_dm(receiver_id: str, message: str) -> None:
    """dm_sender.queue_dm 편의 함수 래핑(지연 임포트)."""
    try:
        from utils.dm_sender import queue_dm
        queue_dm(receiver_id, message)
    except Exception as e:  # pragma: no cover - 전송기 미초기화 등
        logger.error(f"[소문] DM 대기열 추가 실패: {receiver_id} -> {e}")


def _apply_distortion(text: str, sanity: Optional[int], candidates: List[str]) -> str:
    """distortion.apply로 이성 낮은 수신자용 왜곡 적용. 실패/미도입 시 원문 반환."""
    if sanity is None:
        return text
    try:
        from utils.distortion import apply as distort_apply
    except Exception:
        return text
    try:
        distorted, _changes = distort_apply(text, sanity, candidates=candidates or None)
        return distorted if distorted else text
    except Exception as e:  # pragma: no cover
        logger.warning(f"[소문] 왜곡 적용 실패 - 원문 사용: {e}")
        return text


def _header_of(row: Dict[str, Any]) -> List[str]:
    """get_worksheet_data 레코드에서 헤더(컬럼명) 순서 파생."""
    return [k for k in row.keys() if k != '_row_number']


def _col_index(header: List[str], name: str) -> Optional[int]:
    """컬럼명 -> 1-based 열 번호. 없으면 None."""
    try:
        return header.index(name) + 1
    except ValueError:
        return None


def _load_roster(sheets_manager) -> List[Dict[str, str]]:
    """명단에서 {'아이디','이름'} 목록 로드."""
    if sheets_manager is None:
        return []
    try:
        data = sheets_manager.get_worksheet_data(ROSTER_SHEET, use_cache=True)
    except Exception as e:
        logger.warning(f"[소문] 명단 조회 실패: {e}")
        return []
    roster = []
    for row in data:
        uid = str(row.get('아이디', '')).strip()
        name = str(row.get('이름', '')).strip()
        if uid:
            roster.append({'아이디': uid, '이름': name})
    return roster


def _load_sanity_map(sheets_manager) -> Dict[str, int]:
    """관리 시트에서 {아이디: 이성(int)} 매핑 로드. 실패 시 빈 dict."""
    result: Dict[str, int] = {}
    if sheets_manager is None:
        return result
    try:
        data = sheets_manager.get_worksheet_data(MANAGEMENT_SHEET, use_cache=False)
    except Exception as e:
        logger.warning(f"[소문] 관리 시트 조회 실패 - 왜곡 생략: {e}")
        return result
    for row in data:
        uid = str(row.get('아이디', '')).strip()
        if not uid:
            continue
        raw = str(row.get('이성', '')).strip()
        try:
            result[uid] = int(float(raw)) if raw != '' else None
        except (ValueError, TypeError):
            result[uid] = None
    return result


def _pick_recipients(roster: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """RUMOR_RECIPIENTS_MIN~MAX 범위에서 무작위 수신자 선정."""
    lo = int(_cfg('RUMOR_RECIPIENTS_MIN', 3))
    hi = int(_cfg('RUMOR_RECIPIENTS_MAX', 5))
    if lo > hi:
        lo, hi = hi, lo
    if not roster:
        return []
    count = random.randint(lo, hi)
    count = min(count, len(roster))
    return random.sample(roster, count)


def _source_log_rows(rumor: Dict[str, Any], content: str,
                     log_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """이 소문의 출처가 된 행동로그 행들을 찾는다.

    1순위 — '원본행'(후보 적재 때 봇이 남긴 행 번호). GM이 '내용'을 아무리 다듬어도
            끊기지 않는다. 문서가 GM에게 시키는 일이 바로 다듬기다.
    2순위 — 일차+요약 문자열 대조. '원본행' 열이 없는 시트, 원본행 없이 GM이 직접
            추가한 수동 소문, 열 추가 이전의 옛 행을 위한 폴백이다.

    폴백에서 여러 행이 맞으면 전부 표시한다 — 같은 날 같은 문장이면 어느 쪽이
    출처인지 가릴 방법이 없고, 남겨두면 다음 주에 또 후보로 올라온다.
    """
    raw = str(rumor.get(RUMOR_COL_SOURCE_ROW, '')).strip()
    if raw:
        try:
            target = int(float(raw))
        except (ValueError, TypeError):
            target = None
        if target is not None:
            for lr in log_rows:
                if lr.get('_row_number') == target:
                    # 이미 표시된 행이면 다시 쓰지 않는다(쿼터).
                    return [] if str(lr.get('소문화', '')).strip() else [lr]
            logger.warning(
                f"[소문] 원본행 {target} 을(를) 행동로그에서 찾지 못했다 - 문자열 대조로 폴백"
            )

    rumor_day = str(rumor.get(RUMOR_COL_DAY, '')).strip()
    matched = []
    for lr in log_rows:
        if str(lr.get('소문화', '')).strip():
            continue
        if str(lr.get('요약', '')).strip() != content:
            continue
        if rumor_day and str(lr.get('일차', '')).strip() != rumor_day:
            continue
        matched.append(lr)
    return matched


def send_weekly_rumor(sheets_manager, system_sheets_manager, api=None) -> Dict[str, Any]:
    """
    '소문' 시트의 확정 소문을 무작위 수신자에게 발송하고 상태를 갱신한다.

    인자 순서는 코드베이스 공통 규약 (sheets_manager, system_sheets_manager, api) 을 따른다.
    (스케줄러 run_weekly_rumor 및 main.py 배선과 일치.)

    Args:
        sheets_manager: 기본 시트 매니저(명단/관리).
        system_sheets_manager: 시스템 시트 매니저(소문/행동로그).
        api: 미사용(시그니처 호환용). DM은 전역 dm_sender 사용.

    Returns:
        dict: {'confirmed', 'sent', 'recipients', 'failed'} 요약.
    """
    summary = {'confirmed': 0, 'sent': 0, 'recipients': 0, 'failed': 0}

    if system_sheets_manager is None:
        logger.warning("[소문] system_sheets_manager 없음 - 발송 생략")
        return summary

    lock_cm = None
    if get_lock_manager is not None:
        lock_cm = get_lock_manager().acquire_lock('__rumor_weekly__', timeout=10.0)

    def _run() -> Dict[str, Any]:
        try:
            rows = system_sheets_manager.get_worksheet_data(RUMOR_SHEET, use_cache=False)
        except Exception as e:
            logger.warning(f"[소문] 소문 시트 조회 실패: {e}")
            return summary

        confirmed = [r for r in rows
                     if str(r.get(RUMOR_COL_STATE, '')).strip() == STATE_CONFIRMED]
        summary['confirmed'] = len(confirmed)
        if not confirmed:
            logger.info("[소문] 확정 상태 소문 없음")
            return summary

        roster = _load_roster(sheets_manager)
        if not roster:
            logger.warning("[소문] 수신 가능한 명단이 없어 발송 생략")
            return summary

        sanity_map = _load_sanity_map(sheets_manager)
        candidate_names = [r['이름'] for r in roster if r.get('이름')]

        # 행동로그 소문화 표시용 데이터(1회 로드)
        log_rows = []
        log_sohwa_col = None
        try:
            log_rows = system_sheets_manager.get_worksheet_data(ACTION_LOG_SHEET, use_cache=False)
            if log_rows:
                log_sohwa_col = _col_index(_header_of(log_rows[0]), '소문화')
        except Exception as e:
            logger.debug(f"[소문] 행동로그 조회 실패(소문화 표시 생략): {e}")

        state_updates: List[Tuple[int, int, Any]] = []
        log_updates: List[Tuple[int, int, Any]] = []

        for rumor in confirmed:
            # 소문 문구는 GM이 일일보고에서 발췌해 쓴다. 원문에 계정 태그가 섞여 있으면
            # 소문 DM이 그 계정을 호출한다 — 일일보고와 같은 사고다(mention_guard 참조).
            content = mention_guard.defang(str(rumor.get(RUMOR_COL_CONTENT, '')).strip())
            row_number = rumor.get('_row_number')
            if not content or row_number is None:
                continue

            header = _header_of(rumor)
            state_col = _col_index(header, RUMOR_COL_STATE)
            if state_col is None:
                logger.warning("[소문] '상태' 컬럼을 찾을 수 없어 발송 생략")
                continue

            recipients = _pick_recipients(roster)
            for r in recipients:
                sanity = sanity_map.get(r['아이디'])
                body = _apply_distortion(content, sanity, candidate_names)
                _queue_dm(r['아이디'], RUMOR_PREFIX + body)
                summary['recipients'] += 1

            state_updates.append((row_number, state_col, STATE_SENT))
            summary['sent'] += 1

            # 행동로그 소문화=O 표시
            if log_sohwa_col is not None:
                for lr in _source_log_rows(rumor, content, log_rows):
                    lr_row = lr.get('_row_number')
                    if lr_row is not None:
                        log_updates.append((lr_row, log_sohwa_col, 'O'))
                        lr['소문화'] = 'O'  # 같은 실행 내 중복 표시 방지

        if state_updates:
            try:
                system_sheets_manager.batch_update_cells(RUMOR_SHEET, state_updates)
            except Exception as e:
                logger.error(f"[소문] 상태 갱신 실패: {e}")
                summary['failed'] += 1
        if log_updates:
            try:
                system_sheets_manager.batch_update_cells(ACTION_LOG_SHEET, log_updates)
            except Exception as e:
                logger.warning(f"[소문] 행동로그 소문화 표시 실패: {e}")

        logger.info(
            f"[소문] 발송 완료: 확정 {summary['confirmed']}건 중 {summary['sent']}건, "
            f"수신 {summary['recipients']}회"
        )
        return summary

    if lock_cm is not None:
        with lock_cm as acquired:
            if not acquired:
                logger.warning("[소문] 락 획득 실패 - 발송 생략")
                return summary
            return _run()
    return _run()


def propose_candidates(system_sheets_manager,
                       sample_min: Optional[int] = None,
                       sample_max: Optional[int] = None) -> Dict[str, Any]:
    """
    '행동로그'의 소문화 미표시 항목을 무작위 표본으로 '소문' 시트에 후보 적재한다.
    (출처=자동, 상태=후보). 이미 소문 시트에 존재하는 내용은 건너뛴다.

    Args:
        system_sheets_manager: 시스템 시트 매니저(행동로그/소문).
        sample_min: 표본 최소 개수(기본 config.RUMOR_SAMPLE_MIN).
        sample_max: 표본 최대 개수(기본 config.RUMOR_SAMPLE_MAX).

    Returns:
        dict: {'candidates_available', 'appended'} 요약.
    """
    summary = {'candidates_available': 0, 'appended': 0}

    if system_sheets_manager is None:
        logger.warning("[소문] system_sheets_manager 없음 - 후보 적재 생략")
        return summary

    lo = int(sample_min if sample_min is not None else _cfg('RUMOR_SAMPLE_MIN', 3))
    hi = int(sample_max if sample_max is not None else _cfg('RUMOR_SAMPLE_MAX', 5))
    if lo > hi:
        lo, hi = hi, lo

    lock_cm = None
    if get_lock_manager is not None:
        lock_cm = get_lock_manager().acquire_lock('__rumor_propose__', timeout=10.0)

    def _run() -> Dict[str, Any]:
        try:
            log_rows = system_sheets_manager.get_worksheet_data(ACTION_LOG_SHEET, use_cache=False)
        except Exception as e:
            logger.warning(f"[소문] 행동로그 조회 실패: {e}")
            return summary

        # 소문화 미표시 + 요약 존재 행만 후보 대상
        pool = []
        for lr in log_rows:
            if str(lr.get('소문화', '')).strip():
                continue
            summ = str(lr.get('요약', '')).strip()
            if summ:
                pool.append(lr)

        # 기존 소문 내용(중복 방지용)
        existing = set()
        try:
            for r in system_sheets_manager.get_worksheet_data(RUMOR_SHEET, use_cache=False):
                c = str(r.get(RUMOR_COL_CONTENT, '')).strip()
                if c:
                    existing.add(c)
        except Exception as e:
            logger.debug(f"[소문] 기존 소문 조회 실패(중복검사 생략): {e}")

        pool = [lr for lr in pool
                if str(lr.get('요약', '')).strip() not in existing]
        summary['candidates_available'] = len(pool)
        if not pool:
            logger.info("[소문] 적재할 후보 없음")
            return summary

        count = random.randint(lo, hi)
        count = min(count, len(pool))
        chosen = random.sample(pool, count)

        for lr in chosen:
            day = str(lr.get('일차', '')).strip()
            content = str(lr.get('요약', '')).strip()
            source_row = lr.get('_row_number')
            try:
                # 소문 컬럼 순서: 일차, 출처, 내용, 상태, 원본행
                # 원본행이 있어야 GM이 내용을 다듬어도 역링크가 살아 있다.
                ok = system_sheets_manager.append_row(
                    RUMOR_SHEET,
                    [day, SOURCE_AUTO, content, STATE_CANDIDATE,
                     source_row if source_row is not None else ''],
                )
                if ok:
                    summary['appended'] += 1
            except Exception as e:
                logger.warning(f"[소문] 후보 적재 실패: {content[:20]} -> {e}")

        logger.info(f"[소문] 후보 적재 완료: {summary['appended']}건")
        return summary

    if lock_cm is not None:
        with lock_cm as acquired:
            if not acquired:
                logger.warning("[소문] 락 획득 실패 - 후보 적재 생략")
                return summary
            return _run()
    return _run()

"""
행동로그(시스템 시트) append 헬퍼 (action_log)

docs/코딩_계획.md §1.3 / §5 유틸 계약:
- 행동로그 컬럼(**7열**, 순서 고정):
    [일시, 일차, 행위자, 종류, 대상, 요약, 소문화]
  이 헬퍼가 열 순서를 단일 소스로 보장한다. **시트 헤더와 반드시 같아야 한다.**
- 기록 대상(종류) = 의무실 방문 · 조사 · 추적 · 부탁지령수행 · 고발 · 대화 · 교류
  (기록 여부는 호출측 책임. 종류 문자열은 아래 KIND_* 상수를 쓴다.)

시그니처:
- append(system_sheets_manager, kind, actor, target, summary)
    일시 = SheetsManager.get_current_time(), 일차 = game_day.current_day(),
    소문화 = 공란. system_sheets_manager 가 None 이면 조용히 skip(로그만).
- recent(system_sheets_manager, days=None, kind=None) -> list
    행동로그 데이터를 조회(get_worksheet_data). days/kind 로 필터.
- mark_rumored(system_sheets_manager, row_number) -> bool
    해당 행의 '소문화' 열을 'O' 로 표시.
"""

import os
import sys

# 경로 설정 (VM 환경 대응)
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

try:
    from utils.logging_config import logger
except ImportError:
    import logging
    logger = logging.getLogger('action_log')

try:
    from utils.sheets_operations import SheetsManager
except ImportError:
    SheetsManager = None

try:
    from utils import game_day
except ImportError:
    game_day = None


# 행동로그 시트 이름 및 7열 헤더(순서 고정, 단일 소스 — 시트 헤더와 일치해야 한다)
SHEET_NAME = '행동로그'
COLUMNS = ['일시', '일차', '행위자', '종류', '대상', '요약', '소문화']

# 종류 값. 문자열을 각 명령어에 흩뿌리면 오타가 나도 아무도 모른다(시트에만 남는다).
KIND_INFIRMARY = '의무실 방문'
KIND_INVESTIGATE = '조사'
KIND_TRACK = '추적'
KIND_REPORT = '부탁지령수행'
KIND_ACCUSE = '고발'
KIND_TALK = '대화'
KIND_EXCHANGE = '교류'

KINDS = (
    KIND_INFIRMARY, KIND_INVESTIGATE, KIND_TRACK,
    KIND_REPORT, KIND_ACCUSE, KIND_TALK, KIND_EXCHANGE,
)
# 소문화 열 인덱스(1-indexed). COLUMNS 에서 파생한다 —
# 9열→7열 마이그레이션 때 이 상수만 9로 남아 빈 열에 O를 찍고 있었다(2026-07-16 수정).
# 숫자를 손으로 적으면 헤더가 바뀔 때마다 같은 사고가 난다.
RUMOR_COL = COLUMNS.index('소문화') + 1


def _now() -> str:
    """현재 KST 시간 문자열."""
    try:
        if SheetsManager is not None:
            return SheetsManager.get_current_time()
    except Exception:
        pass
    # 폴백
    from datetime import datetime
    import pytz
    return datetime.now(pytz.timezone('Asia/Seoul')).strftime('%Y-%m-%d %H:%M:%S')


def _current_day() -> int:
    """현재 게임 일차."""
    try:
        if game_day is not None:
            return game_day.current_day()
    except Exception as e:
        logger.warning(f"일차 계산 실패, 1일차로 폴백: {e}")
    return 1


def append(system_sheets_manager, kind, actor, target, summary) -> bool:
    """
    행동로그에 1행 추가.

    **7열 순서(고정, 시트 헤더와 일치해야 한다)**:
        [일시, 일차, 행위자, 종류, 대상, 요약, 소문화]

    - 일시: get_current_time()
    - 일차: game_day.current_day()
    - 소문화: 공란(''), 이후 소문 확정 시 mark_rumored 로 'O' 표시

    2026-07-16 개편 — 왜 9열에서 7열이 됐나:
      · 코드가 9열을 쓰는데 시트 헤더는 7열이라 **모든 값이 한 칸씩 밀려 있었다.**
        `소문화` 칸에 `상세`가 들어가 소문 후보 판정이 "이미 소문냄"으로 걸러졌다
        (= 소문 기능이 통째로 죽어 있었다).
      · `상세`·`능력치변동`은 **아무도 읽지 않았다**(쓰기 전용). 능력치는 `관리` 시트가
        원본이고, 상세는 요약과 겹친다. 읽는 쪽이 쓰는 열만 남겼다:
          - 소문(`utils/rumor`)      → 요약 · 소문화
          - 의사 최근 행적(`infirmary`) → 행위자 · 요약
          - GM 눈으로 훑기            → 일시 · 일차 · 종류 · 대상

    **요약에 수치를 섞지 말 것.** 요약이 그대로 소문 문구가 된다
    ("데보라가 이성 -3" 같은 소문은 세계관을 깬다).

    Args:
        system_sheets_manager: 시스템 시트 매니저. None 이면 조용히 skip(로그만).
        kind: 종류 (의무실 방문/조사/추적/부탁지령수행/고발/대화/교류).
        actor: 행위자
        target: 대상
        summary: 요약 (소문 문구가 될 수 있으므로 서술문으로)

    Returns:
        bool: append 성공 여부. skip 시 False.
    """
    if system_sheets_manager is None:
        logger.debug(f"[행동로그] system_sheets_manager None - skip (종류={kind}, 행위자={actor})")
        return False

    row = [
        _now(),                     # 일시
        _current_day(),             # 일차
        '' if actor is None else str(actor),      # 행위자
        '' if kind is None else str(kind),        # 종류
        '' if target is None else str(target),    # 대상
        '' if summary is None else str(summary),  # 요약
        '',                         # 소문화(공란)
    ]

    try:
        success = system_sheets_manager.append_row(SHEET_NAME, row)
        if not success:
            logger.warning(f"[행동로그] append 실패 (종류={kind}, 행위자={actor}, 대상={target})")
        return bool(success)
    except Exception as e:
        logger.error(f"[행동로그] append 예외 (종류={kind}): {e}")
        return False


def recent(system_sheets_manager, days=None, kind=None) -> list:
    """
    행동로그 데이터를 조회(필터 옵션).

    Args:
        system_sheets_manager: 시스템 시트 매니저. None 이면 [] 반환.
        days: 최근 N일차만 반환(현재 일차 - days + 1 이상). None 이면 전체.
        kind: 특정 종류만 반환. None 이면 전체.

    Returns:
        list: 행동로그 레코드(dict) 리스트. _row_number 포함.
    """
    if system_sheets_manager is None:
        return []

    try:
        records = system_sheets_manager.get_worksheet_data(SHEET_NAME)
    except Exception as e:
        logger.error(f"[행동로그] 조회 실패: {e}")
        return []

    if not records:
        return []

    result = records

    # 종류 필터
    if kind is not None:
        result = [r for r in result if str(r.get('종류', '')).strip() == str(kind)]

    # 일차 필터 (최근 days 일차)
    if days is not None:
        try:
            today = _current_day()
            min_day = today - int(days) + 1
            filtered = []
            for r in result:
                raw = str(r.get('일차', '')).strip()
                try:
                    d = int(float(raw))
                except (ValueError, TypeError):
                    continue
                if d >= min_day:
                    filtered.append(r)
            result = filtered
        except Exception as e:
            logger.warning(f"[행동로그] 일차 필터 실패, 필터 미적용: {e}")

    return result


def mark_rumored(system_sheets_manager, row_number) -> bool:
    """
    해당 행동로그 행의 '소문화' 열을 'O' 로 표시.

    Args:
        system_sheets_manager: 시스템 시트 매니저. None 이면 False.
        row_number: 시트 실제 행 번호(1-indexed). recent() 레코드의 _row_number 사용.

    Returns:
        bool: 성공 여부.
    """
    if system_sheets_manager is None:
        logger.debug("[행동로그] mark_rumored: system_sheets_manager None - skip")
        return False

    try:
        row = int(row_number)
    except (ValueError, TypeError):
        logger.warning(f"[행동로그] mark_rumored: 잘못된 row_number={row_number!r}")
        return False

    try:
        success = system_sheets_manager.update_cell(SHEET_NAME, row, RUMOR_COL, 'O')
        if not success:
            logger.warning(f"[행동로그] mark_rumored 실패 (행={row})")
        return bool(success)
    except Exception as e:
        logger.error(f"[행동로그] mark_rumored 예외 (행={row}): {e}")
        return False

"""
게임 일차 계산 모듈 (game_day)

KST(한국 표준시) 기준 오늘 날짜와 config.GAME_START_DATE(형식 YYYY.MM.DD)를 비교해
현재 게임 일차(day)를 계산합니다.

계약 (docs/코딩_계획.md §5 유틸 계약):
- current_day() -> int
    ((오늘 - GAME_START_DATE).days + 1) 를 KST 기준으로 계산.
    시작일 당일이면 1, 그 다음날이면 2 ...
    시작일 이전이면 최소 1로 방어(0 또는 음수 반환 금지).
    GAME_START_DATE 파싱 실패 시 1을 반환.
"""

from datetime import datetime, date

import pytz

from config.settings import config
from utils.logging_config import logger

# KST 타임존
_KST = pytz.timezone('Asia/Seoul')

# 지원 날짜 형식 (YYYY.MM.DD 우선, 관용적으로 - / 도 허용)
_DATE_FORMATS = ["%Y.%m.%d", "%Y-%m-%d", "%Y/%m/%d"]


def _parse_start_date(date_str: str):
    """
    GAME_START_DATE 문자열을 date 객체로 파싱.

    Args:
        date_str: 시작일 문자열 (예: "2026.07.17")

    Returns:
        Optional[date]: 파싱된 date 객체, 실패 시 None
    """
    if not date_str:
        return None

    date_str = str(date_str).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(date_str, fmt).date()
        except (ValueError, TypeError):
            continue
    return None


def current_day() -> int:
    """
    현재 게임 일차를 반환.

    KST 기준 오늘 날짜와 config.GAME_START_DATE의 차이(일수)에 1을 더해 계산합니다.
    시작일 당일이면 1을 반환하며, 시작일 이전이거나 파싱에 실패하면 최소 1을 반환합니다.

    Returns:
        int: 게임 일차 (>= 1)
    """
    start_date = _parse_start_date(getattr(config, 'GAME_START_DATE', ''))
    if start_date is None:
        logger.warning(
            "GAME_START_DATE 파싱 실패 (값=%r) - 일차를 1로 처리",
            getattr(config, 'GAME_START_DATE', None),
        )
        return 1

    today_kst: date = datetime.now(_KST).date()
    day = (today_kst - start_date).days + 1

    # 시작일 이전이면 최소 1로 방어
    if day < 1:
        return 1
    return day

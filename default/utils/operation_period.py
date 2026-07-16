"""
봇 가동 기간 관리 모듈
KST 기준으로 봇의 가동 기간을 확인하고, 만료 시 처리를 담당합니다.
"""

import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from utils.logging_config import logger

# KST 타임존 (UTC+9)
KST = timezone(timedelta(hours=9))

# 전역 만료 플래그 (스레드 안전)
_expired_flag = threading.Event()


def is_expired() -> bool:
    """봇 가동 기간이 만료되었는지 확인 (전역 플래그)"""
    return _expired_flag.is_set()


def set_expired() -> None:
    """봇 가동 기간 만료 플래그 설정"""
    _expired_flag.set()


_DATE_FORMATS = ["%Y.%m.%d", "%Y-%m-%d", "%Y/%m/%d"]


def _parse_date(date_str: str) -> Optional[datetime]:
    """
    여러 날짜 형식을 시도하여 파싱

    지원 형식: YYYY.MM.DD, YYYY-MM-DD, YYYY/MM/DD (한 자리 월/일도 허용)
    """
    date_str = date_str.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(date_str, fmt).replace(tzinfo=KST)
        except ValueError:
            continue
    return None


def parse_operation_dates(start_str: str, end_str: str) -> Tuple[Optional[datetime], Optional[datetime]]:
    """
    가동 기간 문자열을 파싱하여 datetime 객체로 변환

    Args:
        start_str: 시작일 문자열 (예: "2026.02.01", "2026-02-01", "2026/02/01")
        end_str: 종료일 문자열 (예: "2026.02.27")

    Returns:
        Tuple[Optional[datetime], Optional[datetime]]: (시작일 00:00 KST, 종료일 다음날 00:00 KST)
    """
    if not start_str or not end_str:
        return None, None

    start_date = _parse_date(start_str)
    end_date = _parse_date(end_str)

    if start_date is None or end_date is None:
        logger.error(
            f"가동 기간 날짜 파싱 실패 (지원 형식: YYYY.MM.DD) "
            f"(start={start_str}, end={end_str})"
        )
        return None, None

    # 종료일 다음날 0시 = 종료일 23:59:59까지 가동
    end_date = end_date + timedelta(days=1)
    return start_date, end_date


def check_operation_period(start_str: str, end_str: str) -> Tuple[bool, str]:
    """
    현재 시각이 가동 기간 내인지 확인

    Args:
        start_str: 시작일 문자열
        end_str: 종료일 문자열

    Returns:
        Tuple[bool, str]: (가동 가능 여부, 상태 메시지)
    """
    if not start_str or not end_str:
        # 가동 기간 미설정 시 항상 가동
        return True, "가동 기간 미설정 (무제한)"

    start_date, end_date = parse_operation_dates(start_str, end_str)
    if start_date is None or end_date is None:
        return True, "가동 기간 파싱 실패 (무제한으로 가동)"

    now_kst = datetime.now(KST)

    if now_kst < start_date:
        return False, f"가동 시작일 이전입니다 (시작일: {start_str})"
    elif now_kst >= end_date:
        return False, f"가동 기간이 만료되었습니다 (종료일: {end_str})"
    else:
        remaining = end_date - now_kst
        days = remaining.days
        hours = remaining.seconds // 3600
        return True, f"가동 중 (남은 기간: {days}일 {hours}시간)"


def is_before_operation_start(start_str: str) -> bool:
    """
    현재 시각이 가동 시작일 이전인지 확인

    가동 시작일이 설정되어 있고 현재 시각(KST)이 그 이전이면 True.
    시작일이 미설정이거나 파싱 실패면 '제한 없음'으로 보고 False를 반환합니다.

    Args:
        start_str: 시작일 문자열

    Returns:
        bool: 가동 시작일 이전이면 True, 그 외에는 False
    """
    if not start_str:
        return False

    start_date = _parse_date(start_str)
    if start_date is None:
        return False

    return datetime.now(KST) < start_date


def get_seconds_until_expiry(end_str: str) -> Optional[float]:
    """
    종료일까지 남은 시간(초) 반환

    Args:
        end_str: 종료일 문자열

    Returns:
        Optional[float]: 남은 초 (이미 만료면 0 이하, 미설정이면 None)
    """
    if not end_str:
        return None

    end_date = _parse_date(end_str)
    if end_date is None:
        return None

    # 종료일 다음날 0시까지
    end_date = end_date + timedelta(days=1)
    now_kst = datetime.now(KST)
    return (end_date - now_kst).total_seconds()


def post_expiry_toot(api) -> bool:
    """
    가동 만료 툿 게시 (unlisted)

    Args:
        api: 마스토돈 API 인스턴스

    Returns:
        bool: 게시 성공 여부
    """
    expiry_message = (
        "자동봇 가동 기간이 만료되어 가동을 종료합니다. "
        "본 자동봇은 한참 커미션으로 진행되었습니다. "
        "https://crepe.cm/@longwhile/lw5w0ofg"
    )

    try:
        api.status_post(
            status=expiry_message,
            visibility='unlisted'
        )
        logger.info("가동 만료 툿 게시 완료")
        return True
    except Exception as e:
        logger.error(f"가동 만료 툿 게시 실패: {e}")
        return False


class OperationPeriodMonitor:
    """
    가동 기간 모니터링 스레드

    종료일에 도달하면 만료 툿을 게시하고 봇 종료를 트리거합니다.
    """

    def __init__(self, api, end_str: str, stream_manager=None):
        """
        Args:
            api: 마스토돈 API 인스턴스
            end_str: 종료일 문자열
            stream_manager: StreamManager 인스턴스 (종료 트리거용)
        """
        self.api = api
        self.end_str = end_str
        self.stream_manager = stream_manager
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        """모니터링 시작"""
        seconds_left = get_seconds_until_expiry(self.end_str)
        if seconds_left is None:
            logger.info("가동 기간 미설정 - 모니터링 생략")
            return

        if seconds_left <= 0:
            logger.warning("이미 가동 기간이 만료됨 - 즉시 만료 처리")
            self._handle_expiry()
            return

        logger.info(f"가동 기간 모니터링 시작 (만료까지 {seconds_left:.0f}초)")
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """모니터링 중지"""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

    def _monitor_loop(self) -> None:
        """모니터링 루프 - 60초마다 만료 확인"""
        while not self._stop_event.is_set():
            seconds_left = get_seconds_until_expiry(self.end_str)
            if seconds_left is not None and seconds_left <= 0:
                logger.info("가동 기간 만료 감지!")
                self._handle_expiry()
                return

            # 60초 대기 (중간에 stop 가능)
            self._stop_event.wait(timeout=60)

    def _handle_expiry(self) -> None:
        """만료 처리: 툿 게시 → 플래그 설정 → 봇 종료"""
        # 1. 전역 만료 플래그 설정 (명령어 차단 시작)
        set_expired()
        logger.info("가동 만료 플래그 설정 완료 - 모든 명령어 차단")

        # 2. 만료 툿 게시
        post_expiry_toot(self.api)

        # 3. 봇 종료 트리거
        if self.stream_manager:
            logger.info("스트리밍 중지 요청 (가동 만료)")
            self.stream_manager.stop_streaming()

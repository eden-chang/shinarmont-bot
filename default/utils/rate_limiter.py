"""
Rate Limiter for Google Sheets API
멀티 봇 환경에서 API 호출 속도를 제한합니다.
"""

import time
import logging
from collections import deque
from typing import Optional, Deque
from dataclasses import dataclass
from threading import Lock

logger = logging.getLogger(__name__)


@dataclass
class RateLimitConfig:
    """Rate Limit 설정"""
    max_requests: int       # 최대 요청 수
    time_window: float      # 시간 윈도우 (초)
    burst_limit: int = 0    # 버스트 제한 (0이면 max_requests와 동일)

    def __post_init__(self):
        if self.burst_limit == 0:
            self.burst_limit = self.max_requests


class RateLimiter:
    """
    토큰 버킷 기반 Rate Limiter

    Google Sheets API의 할당량을 초과하지 않도록 요청 속도를 제한합니다.

    Google Sheets API v4 할당량:
    - 읽기: 분당 300회
    - 쓰기: 분당 300회
    - 프로젝트당: 분당 500회
    """

    def __init__(
        self,
        max_requests_per_minute: int = 100,
        burst_limit: Optional[int] = None
    ):
        """
        Args:
            max_requests_per_minute: 분당 최대 요청 수
            burst_limit: 순간 최대 요청 수 (None이면 max_requests_per_minute과 동일)
        """
        self.config = RateLimitConfig(
            max_requests=max_requests_per_minute,
            time_window=60.0,  # 1분
            burst_limit=burst_limit or max_requests_per_minute
        )

        # 요청 타임스탬프 기록 (deque는 thread-safe)
        self.request_times: Deque[float] = deque()

        # 스레드 안전성을 위한 락
        self.lock = Lock()

        logger.info(
            f"RateLimiter initialized: "
            f"{self.config.max_requests} req/min, "
            f"burst={self.config.burst_limit}"
        )

    def acquire(self, blocking: bool = True, timeout: Optional[float] = None) -> bool:
        """
        Rate limit 체크 및 토큰 획득

        Args:
            blocking: True면 허용될 때까지 대기
            timeout: 최대 대기 시간 (초), None이면 무제한

        Returns:
            bool: 요청 허용 여부
        """
        start_time = time.time()

        with self.lock:
            while True:
                # 현재 시간
                now = time.time()

                # 시간 윈도우 밖의 요청 제거
                self._cleanup_old_requests(now)

                # 현재 요청 수 확인
                current_requests = len(self.request_times)

                # 허용 가능한지 확인
                if current_requests < self.config.max_requests:
                    # 요청 기록
                    self.request_times.append(now)
                    logger.debug(
                        f"Request allowed: {current_requests + 1}/{self.config.max_requests}"
                    )
                    return True

                # 블로킹 모드가 아니면 즉시 반환
                if not blocking:
                    logger.debug(
                        f"Request denied (non-blocking): "
                        f"{current_requests}/{self.config.max_requests}"
                    )
                    return False

                # 타임아웃 체크
                if timeout is not None:
                    elapsed = time.time() - start_time
                    if elapsed >= timeout:
                        logger.warning(
                            f"Request timeout: waited {elapsed:.1f}s, "
                            f"current={current_requests}/{self.config.max_requests}"
                        )
                        return False

                # 다음 요청 가능 시간 계산
                wait_time = self._calculate_wait_time(now)

                if wait_time > 0:
                    logger.debug(
                        f"Rate limit reached, waiting {wait_time:.2f}s "
                        f"({current_requests}/{self.config.max_requests})"
                    )
                    time.sleep(wait_time)
                else:
                    # 약간의 대기 (CPU 과사용 방지)
                    time.sleep(0.1)

    def _cleanup_old_requests(self, now: float) -> None:
        """시간 윈도우 밖의 요청 제거"""
        cutoff_time = now - self.config.time_window

        while self.request_times and self.request_times[0] < cutoff_time:
            self.request_times.popleft()

    def _calculate_wait_time(self, now: float) -> float:
        """다음 요청까지 대기 시간 계산"""
        if not self.request_times:
            return 0.0

        # 가장 오래된 요청
        oldest_request = self.request_times[0]

        # 윈도우가 열리는 시간
        window_opens_at = oldest_request + self.config.time_window

        # 대기 시간
        wait_time = max(0.0, window_opens_at - now)

        return wait_time

    def get_current_usage(self) -> dict:
        """
        현재 사용량 반환

        Returns:
            dict: 사용량 정보
        """
        with self.lock:
            now = time.time()
            self._cleanup_old_requests(now)

            current_requests = len(self.request_times)
            usage_percent = (current_requests / self.config.max_requests) * 100

            return {
                'current_requests': current_requests,
                'max_requests': self.config.max_requests,
                'usage_percent': usage_percent,
                'time_window': self.config.time_window,
                'requests_available': self.config.max_requests - current_requests
            }

    def reset(self) -> None:
        """Rate limiter 리셋 (테스트용)"""
        with self.lock:
            self.request_times.clear()
            logger.info("RateLimiter reset")

    def __enter__(self):
        """컨텍스트 매니저 진입"""
        if not self.acquire():
            raise RuntimeError("Rate limit exceeded")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """컨텍스트 매니저 종료"""
        return False


class SheetsRateLimiter:
    """
    Google Sheets API 전용 Rate Limiter

    읽기와 쓰기에 대해 별도의 rate limit을 관리합니다.
    """

    def __init__(
        self,
        read_rpm: int = 100,
        write_rpm: int = 100,
        global_rpm: int = 150
    ):
        """
        Args:
            read_rpm: 읽기 분당 최대 요청
            write_rpm: 쓰기 분당 최대 요청
            global_rpm: 전체 분당 최대 요청
        """
        self.read_limiter = RateLimiter(max_requests_per_minute=read_rpm)
        self.write_limiter = RateLimiter(max_requests_per_minute=write_rpm)
        self.global_limiter = RateLimiter(max_requests_per_minute=global_rpm)

        logger.info(
            f"SheetsRateLimiter initialized: "
            f"read={read_rpm}, write={write_rpm}, global={global_rpm} req/min"
        )

    def acquire_read(self, blocking: bool = True, timeout: Optional[float] = None) -> bool:
        """
        읽기 요청 토큰 획득

        Args:
            blocking: 대기 여부
            timeout: 최대 대기 시간

        Returns:
            bool: 허용 여부
        """
        # 글로벌 제한 먼저 확인
        if not self.global_limiter.acquire(blocking=blocking, timeout=timeout):
            return False

        # 읽기 제한 확인
        if not self.read_limiter.acquire(blocking=blocking, timeout=timeout):
            return False

        return True

    def acquire_write(self, blocking: bool = True, timeout: Optional[float] = None) -> bool:
        """
        쓰기 요청 토큰 획득

        Args:
            blocking: 대기 여부
            timeout: 최대 대기 시간

        Returns:
            bool: 허용 여부
        """
        # 글로벌 제한 먼저 확인
        if not self.global_limiter.acquire(blocking=blocking, timeout=timeout):
            return False

        # 쓰기 제한 확인
        if not self.write_limiter.acquire(blocking=blocking, timeout=timeout):
            return False

        return True

    def get_status(self) -> dict:
        """
        전체 상태 반환

        Returns:
            dict: 상태 정보
        """
        return {
            'read': self.read_limiter.get_current_usage(),
            'write': self.write_limiter.get_current_usage(),
            'global': self.global_limiter.get_current_usage()
        }


# 전역 Sheets Rate Limiter 인스턴스
_sheets_rate_limiter: Optional[SheetsRateLimiter] = None


def get_sheets_rate_limiter() -> SheetsRateLimiter:
    """
    전역 Sheets Rate Limiter 반환

    Returns:
        SheetsRateLimiter: 전역 인스턴스
    """
    global _sheets_rate_limiter

    if _sheets_rate_limiter is None:
        # 환경 변수에서 설정 읽기
        import os

        read_rpm = int(os.getenv('SHEETS_READ_RPM', '100'))
        write_rpm = int(os.getenv('SHEETS_WRITE_RPM', '100'))
        global_rpm = int(os.getenv('SHEETS_GLOBAL_RPM', '150'))

        _sheets_rate_limiter = SheetsRateLimiter(
            read_rpm=read_rpm,
            write_rpm=write_rpm,
            global_rpm=global_rpm
        )

    return _sheets_rate_limiter

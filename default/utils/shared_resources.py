"""
공유 리소스 관리자
멀티 봇 환경에서 Google Sheets 접근을 안전하게 관리합니다.
"""

import logging
from typing import Optional, Any, Callable
from contextlib import contextmanager
from functools import wraps

from .resource_lock import ResourceLock, acquire_resource_lock
from .rate_limiter import get_sheets_rate_limiter, SheetsRateLimiter

logger = logging.getLogger(__name__)


class SharedResourceManager:
    """
    공유 리소스 관리자

    멀티 봇 환경에서 Google Sheets에 대한 동시 접근을 제어합니다.
    - 파일 락으로 동시 쓰기 방지
    - Rate limiting으로 API 할당량 관리
    """

    def __init__(
        self,
        enable_locking: bool = True,
        enable_rate_limiting: bool = True,
        lock_dir: str = 'locks',
        lock_timeout: float = 30.0
    ):
        """
        Args:
            enable_locking: 락 사용 여부
            enable_rate_limiting: Rate limiting 사용 여부
            lock_dir: 락 파일 디렉토리
            lock_timeout: 락 획득 타임아웃 (초)
        """
        self.enable_locking = enable_locking
        self.enable_rate_limiting = enable_rate_limiting
        self.lock_dir = lock_dir
        self.lock_timeout = lock_timeout

        # Rate limiter 초기화
        if self.enable_rate_limiting:
            self.rate_limiter = get_sheets_rate_limiter()
        else:
            self.rate_limiter = None

        logger.info(
            f"SharedResourceManager initialized: "
            f"locking={enable_locking}, rate_limiting={enable_rate_limiting}"
        )

    @contextmanager
    def access_resource(
        self,
        resource_name: str,
        operation: str = 'read',
        timeout: Optional[float] = None
    ):
        """
        리소스 접근 컨텍스트 매니저

        Args:
            resource_name: 리소스 이름 (예: 'sheets_명단')
            operation: 작업 종류 ('read' 또는 'write')
            timeout: 락/rate limit 타임아웃 (None이면 기본값 사용)

        Yields:
            None

        Example:
            with resource_manager.access_resource('sheets_명단', 'write'):
                sheets_manager.update_data(...)
        """
        lock_timeout = timeout if timeout is not None else self.lock_timeout
        rate_timeout = timeout if timeout is not None else 30.0

        lock = None

        try:
            # 1. Rate limiting 체크
            if self.enable_rate_limiting and self.rate_limiter:
                if operation == 'write':
                    if not self.rate_limiter.acquire_write(blocking=True, timeout=rate_timeout):
                        raise RuntimeError(
                            f"Rate limit exceeded for write operation on {resource_name}"
                        )
                else:
                    if not self.rate_limiter.acquire_read(blocking=True, timeout=rate_timeout):
                        raise RuntimeError(
                            f"Rate limit exceeded for read operation on {resource_name}"
                        )

                logger.debug(f"Rate limit passed: {operation} on {resource_name}")

            # 2. 쓰기 작업이면 락 획득
            if self.enable_locking and operation == 'write':
                lock = ResourceLock(
                    resource_name=resource_name,
                    lock_dir=self.lock_dir,
                    timeout=lock_timeout
                )

                if not lock.acquire(blocking=True):
                    raise RuntimeError(
                        f"Failed to acquire lock for {resource_name} "
                        f"(timeout={lock_timeout}s)"
                    )

                logger.debug(f"Lock acquired: {resource_name}")

            # 3. 리소스 접근 허용
            yield

        finally:
            # 4. 락 해제
            if lock and lock.acquired:
                lock.release()
                logger.debug(f"Lock released: {resource_name}")

    def protected_read(
        self,
        resource_name: str,
        func: Callable,
        *args,
        **kwargs
    ) -> Any:
        """
        보호된 읽기 작업

        Args:
            resource_name: 리소스 이름
            func: 실행할 함수
            *args: 함수 인자
            **kwargs: 함수 키워드 인자

        Returns:
            Any: 함수 실행 결과
        """
        with self.access_resource(resource_name, operation='read'):
            return func(*args, **kwargs)

    def protected_write(
        self,
        resource_name: str,
        func: Callable,
        *args,
        **kwargs
    ) -> Any:
        """
        보호된 쓰기 작업

        Args:
            resource_name: 리소스 이름
            func: 실행할 함수
            *args: 함수 인자
            **kwargs: 함수 키워드 인자

        Returns:
            Any: 함수 실행 결과
        """
        with self.access_resource(resource_name, operation='write'):
            return func(*args, **kwargs)

    def get_status(self) -> dict:
        """
        리소스 관리자 상태 반환

        Returns:
            dict: 상태 정보
        """
        status = {
            'locking_enabled': self.enable_locking,
            'rate_limiting_enabled': self.enable_rate_limiting,
            'lock_timeout': self.lock_timeout
        }

        if self.rate_limiter:
            status['rate_limiter'] = self.rate_limiter.get_status()

        return status


# 전역 리소스 관리자 인스턴스
_resource_manager: Optional[SharedResourceManager] = None


def get_resource_manager() -> SharedResourceManager:
    """
    전역 리소스 관리자 반환

    Returns:
        SharedResourceManager: 전역 인스턴스
    """
    global _resource_manager

    if _resource_manager is None:
        # 환경 변수에서 설정 읽기
        import os

        enable_locking = os.getenv('ENABLE_RESOURCE_LOCKING', 'True').lower() == 'true'
        enable_rate_limiting = os.getenv('ENABLE_RATE_LIMITING', 'True').lower() == 'true'
        lock_timeout = float(os.getenv('RESOURCE_LOCK_TIMEOUT', '30.0'))

        _resource_manager = SharedResourceManager(
            enable_locking=enable_locking,
            enable_rate_limiting=enable_rate_limiting,
            lock_timeout=lock_timeout
        )

    return _resource_manager


def protected_sheets_operation(operation: str = 'read', resource_suffix: str = 'default'):
    """
    데코레이터: Sheets 작업 보호

    Args:
        operation: 'read' 또는 'write'
        resource_suffix: 리소스 이름 접미사

    Example:
        @protected_sheets_operation('write', 'roster')
        def update_user_data(self, user_id, data):
            # 보호된 쓰기 작업
            ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            resource_manager = get_resource_manager()
            resource_name = f"sheets_{resource_suffix}"

            with resource_manager.access_resource(resource_name, operation=operation):
                return func(*args, **kwargs)

        return wrapper
    return decorator

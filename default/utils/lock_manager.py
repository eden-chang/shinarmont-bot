"""
동시성 제어를 위한 락 매니저
사용자별 아이템 사용 시 경쟁 조건(race condition)을 방지합니다.
"""

import threading
import time
from typing import Dict, Optional
from contextlib import contextmanager

try:
    from utils.logging_config import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)


class LockManager:
    """사용자별 락을 관리하는 클래스"""

    def __init__(self, lock_timeout: float = 30.0):
        """
        LockManager 초기화

        Args:
            lock_timeout: 락 타임아웃 시간 (초)
        """
        self._locks: Dict[str, threading.Lock] = {}
        self._lock_creation_lock = threading.Lock()
        self._lock_timeout = lock_timeout
        self._lock_acquire_times: Dict[str, float] = {}

    def _get_lock(self, user_id: str) -> threading.Lock:
        """
        사용자 ID에 대한 락 가져오기 (없으면 생성)

        Args:
            user_id: 사용자 ID

        Returns:
            threading.Lock: 사용자별 락
        """
        with self._lock_creation_lock:
            if user_id not in self._locks:
                self._locks[user_id] = threading.Lock()
            return self._locks[user_id]

    @contextmanager
    def acquire_lock(self, user_id: str, timeout: Optional[float] = None):
        """
        사용자별 락 획득 (컨텍스트 매니저)

        Args:
            user_id: 사용자 ID
            timeout: 락 획득 타임아웃 (None이면 기본값 사용)

        Yields:
            bool: 락 획득 성공 여부

        Example:
            with lock_manager.acquire_lock(user_id) as acquired:
                if acquired:
                    # 작업 수행
                    pass
                else:
                    # 락 획득 실패 처리
                    pass
        """
        lock = self._get_lock(user_id)
        timeout = timeout or self._lock_timeout

        # 락 획득 시도
        acquired = lock.acquire(timeout=timeout)

        if acquired:
            self._lock_acquire_times[user_id] = time.time()
            logger.debug(f"락 획득 성공: {user_id}")

        try:
            yield acquired
        finally:
            if acquired:
                # 락 보유 시간 로깅
                if user_id in self._lock_acquire_times:
                    hold_time = time.time() - self._lock_acquire_times[user_id]
                    logger.debug(f"락 해제: {user_id} (보유 시간: {hold_time:.2f}초)")
                    del self._lock_acquire_times[user_id]

                lock.release()

    def is_locked(self, user_id: str) -> bool:
        """
        특정 사용자의 락 상태 확인

        Args:
            user_id: 사용자 ID

        Returns:
            bool: 락 획득 여부
        """
        if user_id not in self._locks:
            return False

        lock = self._locks[user_id]
        # 락을 획득할 수 있으면 즉시 해제하고 False 반환
        if lock.acquire(blocking=False):
            lock.release()
            return False
        return True

    def cleanup_old_locks(self, max_age: float = 3600.0):
        """
        오래된 락 정리 (메모리 누수 방지)

        Args:
            max_age: 락 최대 유지 시간 (초, 기본 1시간)
        """
        current_time = time.time()
        with self._lock_creation_lock:
            users_to_remove = []

            for user_id, lock in self._locks.items():
                # 락이 잠겨있지 않고, 획득 시간이 없거나 오래된 경우
                if not lock.locked():
                    acquire_time = self._lock_acquire_times.get(user_id, 0)
                    if current_time - acquire_time > max_age:
                        users_to_remove.append(user_id)

            # 오래된 락 제거
            for user_id in users_to_remove:
                del self._locks[user_id]
                if user_id in self._lock_acquire_times:
                    del self._lock_acquire_times[user_id]

            if users_to_remove:
                logger.debug(f"오래된 락 {len(users_to_remove)}개 정리 완료")

    def get_stats(self) -> Dict[str, int]:
        """
        락 매니저 통계 반환

        Returns:
            Dict: 통계 정보
        """
        with self._lock_creation_lock:
            locked_count = sum(1 for lock in self._locks.values() if lock.locked())
            return {
                'total_locks': len(self._locks),
                'locked_count': locked_count,
                'unlocked_count': len(self._locks) - locked_count
            }


# 전역 락 매니저 인스턴스
_global_lock_manager = None


def get_lock_manager() -> LockManager:
    """전역 LockManager 인스턴스 반환"""
    global _global_lock_manager
    if _global_lock_manager is None:
        _global_lock_manager = LockManager()
    return _global_lock_manager

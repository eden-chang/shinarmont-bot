"""
파일 기반 리소스 락 관리
멀티 봇 환경에서 Google Sheets 접근을 동기화합니다.
"""

import os
import time
import logging
from pathlib import Path
from typing import Optional
from contextlib import contextmanager

logger = logging.getLogger(__name__)


class ResourceLock:
    """
    파일 기반 리소스 락

    멀티 프로세스 환경에서 공유 리소스(Google Sheets)에 대한
    동시 접근을 제어합니다.
    """

    def __init__(
        self,
        resource_name: str,
        lock_dir: str = 'locks',
        timeout: float = 30.0,
        check_interval: float = 0.1
    ):
        """
        Args:
            resource_name: 리소스 이름 (예: 'sheets_명단')
            lock_dir: 락 파일을 저장할 디렉토리
            timeout: 락 획득 최대 대기 시간 (초)
            check_interval: 락 확인 주기 (초)
        """
        self.resource_name = resource_name
        self.lock_dir = Path(lock_dir)
        self.timeout = timeout
        self.check_interval = check_interval

        # 락 파일 경로
        self.lock_file = self.lock_dir / f"{resource_name}.lock"

        # 락 디렉토리 생성
        self.lock_dir.mkdir(parents=True, exist_ok=True)

        # 현재 프로세스 정보
        self.process_id = os.getpid()
        self.acquired = False

    def acquire(self, blocking: bool = True) -> bool:
        """
        락 획득

        Args:
            blocking: True면 락을 획득할 때까지 대기, False면 즉시 반환

        Returns:
            bool: 락 획득 성공 여부
        """
        start_time = time.time()

        while True:
            # 락 파일이 없으면 생성
            try:
                # 배타적 생성 (존재하면 실패)
                with open(self.lock_file, 'x') as f:
                    f.write(f"{self.process_id}\n{time.time()}")

                self.acquired = True
                logger.debug(f"Lock acquired: {self.resource_name} by PID {self.process_id}")
                return True

            except FileExistsError:
                # 락 파일이 이미 존재함
                if not blocking:
                    return False

                # 타임아웃 확인
                elapsed = time.time() - start_time
                if elapsed >= self.timeout:
                    logger.warning(
                        f"Lock timeout: {self.resource_name} "
                        f"(waited {elapsed:.1f}s, timeout={self.timeout}s)"
                    )
                    # 오래된 락 파일 정리 시도
                    if self._is_stale_lock():
                        logger.warning(f"Removing stale lock: {self.resource_name}")
                        self._force_release()
                        continue
                    return False

                # 대기
                time.sleep(self.check_interval)

            except Exception as e:
                logger.error(f"Lock acquire error: {self.resource_name} - {e}")
                return False

    def release(self) -> bool:
        """
        락 해제

        Returns:
            bool: 락 해제 성공 여부
        """
        if not self.acquired:
            logger.warning(f"Attempting to release unacquired lock: {self.resource_name}")
            return False

        try:
            # 락 파일 삭제
            if self.lock_file.exists():
                self.lock_file.unlink()
                logger.debug(f"Lock released: {self.resource_name} by PID {self.process_id}")

            self.acquired = False
            return True

        except Exception as e:
            logger.error(f"Lock release error: {self.resource_name} - {e}")
            return False

    def _is_stale_lock(self, max_age: float = 60.0) -> bool:
        """
        오래된 락인지 확인

        Args:
            max_age: 락이 유효한 최대 시간 (초)

        Returns:
            bool: 오래된 락이면 True
        """
        try:
            if not self.lock_file.exists():
                return False

            # 락 파일 읽기
            with open(self.lock_file, 'r') as f:
                lines = f.readlines()

            if len(lines) < 2:
                return True  # 형식이 잘못됨

            # PID와 타임스탬프 추출
            pid = int(lines[0].strip())
            timestamp = float(lines[1].strip())

            # 프로세스가 살아있는지 확인
            if not self._is_process_running(pid):
                logger.warning(f"Lock held by dead process PID {pid}")
                return True

            # 락 생성 시간 확인
            age = time.time() - timestamp
            if age > max_age:
                logger.warning(f"Lock is too old: {age:.1f}s (max={max_age}s)")
                return True

            return False

        except Exception as e:
            logger.error(f"Error checking stale lock: {e}")
            return True  # 에러 시 안전하게 오래된 것으로 간주

    def _is_process_running(self, pid: int) -> bool:
        """
        프로세스가 실행 중인지 확인

        Args:
            pid: 프로세스 ID

        Returns:
            bool: 실행 중이면 True
        """
        try:
            # Windows
            if os.name == 'nt':
                import psutil
                return psutil.pid_exists(pid)
            # Unix/Linux
            else:
                os.kill(pid, 0)
                return True
        except (ImportError, ProcessLookupError, PermissionError):
            return False
        except Exception:
            return True  # 확인할 수 없으면 안전하게 살아있다고 간주

    def _force_release(self) -> None:
        """강제로 락 해제 (주의해서 사용)"""
        try:
            if self.lock_file.exists():
                self.lock_file.unlink()
                logger.warning(f"Forcefully released lock: {self.resource_name}")
        except Exception as e:
            logger.error(f"Force release failed: {e}")

    def __enter__(self):
        """컨텍스트 매니저 진입"""
        if not self.acquire():
            raise RuntimeError(f"Failed to acquire lock: {self.resource_name}")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """컨텍스트 매니저 종료"""
        self.release()
        return False

    def __del__(self):
        """소멸자 - 락이 획득된 상태로 객체가 소멸되면 해제"""
        if self.acquired:
            logger.warning(
                f"Lock {self.resource_name} was not released properly, "
                f"releasing in destructor"
            )
            self.release()


@contextmanager
def acquire_resource_lock(
    resource_name: str,
    timeout: float = 30.0,
    lock_dir: str = 'locks'
):
    """
    리소스 락 획득 컨텍스트 매니저 (편의 함수)

    Args:
        resource_name: 리소스 이름
        timeout: 타임아웃 (초)
        lock_dir: 락 디렉토리

    Yields:
        ResourceLock: 락 객체

    Example:
        with acquire_resource_lock('sheets_명단'):
            # 보호된 리소스에 접근
            sheets_manager.update_data(...)
    """
    lock = ResourceLock(resource_name, lock_dir=lock_dir, timeout=timeout)

    try:
        if not lock.acquire():
            raise RuntimeError(f"Failed to acquire lock: {resource_name}")
        yield lock
    finally:
        lock.release()


def cleanup_stale_locks(lock_dir: str = 'locks', max_age: float = 60.0) -> int:
    """
    오래된 락 파일 정리

    Args:
        lock_dir: 락 디렉토리
        max_age: 락 최대 유효 시간 (초)

    Returns:
        int: 정리된 락 파일 개수
    """
    lock_path = Path(lock_dir)

    if not lock_path.exists():
        return 0

    cleaned = 0

    try:
        for lock_file in lock_path.glob("*.lock"):
            try:
                # 락 파일 읽기
                with open(lock_file, 'r') as f:
                    lines = f.readlines()

                if len(lines) < 2:
                    # 잘못된 형식
                    lock_file.unlink()
                    cleaned += 1
                    logger.info(f"Removed malformed lock: {lock_file.name}")
                    continue

                # PID와 타임스탬프
                pid = int(lines[0].strip())
                timestamp = float(lines[1].strip())

                # 프로세스 확인
                if os.name == 'nt':
                    try:
                        import psutil
                        process_running = psutil.pid_exists(pid)
                    except ImportError:
                        process_running = True  # psutil 없으면 확인 불가
                else:
                    try:
                        os.kill(pid, 0)
                        process_running = True
                    except ProcessLookupError:
                        process_running = False
                    except PermissionError:
                        process_running = True

                # 프로세스가 죽었거나 락이 오래됨
                age = time.time() - timestamp

                if not process_running or age > max_age:
                    lock_file.unlink()
                    cleaned += 1
                    logger.info(
                        f"Removed stale lock: {lock_file.name} "
                        f"(process_running={process_running}, age={age:.1f}s)"
                    )

            except Exception as e:
                logger.error(f"Error cleaning lock {lock_file}: {e}")

    except Exception as e:
        logger.error(f"Error during lock cleanup: {e}")

    return cleaned

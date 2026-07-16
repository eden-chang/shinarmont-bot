"""
프로세스 헬스체크 유틸리티
봇 프로세스의 건강 상태를 모니터링합니다.
"""

import psutil
import logging
from typing import Dict, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class HealthStatus(Enum):
    """헬스체크 상태"""
    HEALTHY = "healthy"
    WARNING = "warning"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


@dataclass
class HealthCheckResult:
    """헬스체크 결과"""
    status: HealthStatus
    message: str
    details: Dict = None

    def __post_init__(self):
        if self.details is None:
            self.details = {}

    def is_healthy(self) -> bool:
        """건강한 상태인지 확인"""
        return self.status == HealthStatus.HEALTHY

    def needs_restart(self) -> bool:
        """재시작이 필요한지 확인"""
        return self.status == HealthStatus.CRITICAL


class ProcessHealthChecker:
    """
    프로세스 헬스체크 수행 클래스

    CPU, 메모리, 응답성 등을 종합적으로 체크합니다.
    """

    def __init__(
        self,
        memory_warning_mb: int = 500,
        memory_critical_mb: int = 1000,
        cpu_warning_percent: float = 80.0,
        cpu_critical_percent: float = 95.0
    ):
        """
        Args:
            memory_warning_mb: 메모리 경고 임계값 (MB)
            memory_critical_mb: 메모리 위험 임계값 (MB)
            cpu_warning_percent: CPU 경고 임계값 (%)
            cpu_critical_percent: CPU 위험 임계값 (%)
        """
        self.memory_warning_mb = memory_warning_mb
        self.memory_critical_mb = memory_critical_mb
        self.cpu_warning_percent = cpu_warning_percent
        self.cpu_critical_percent = cpu_critical_percent

        logger.info("ProcessHealthChecker 초기화 완료")
        logger.info(f"  메모리 임계값: {memory_warning_mb}MB (경고), {memory_critical_mb}MB (위험)")
        logger.info(f"  CPU 임계값: {cpu_warning_percent}% (경고), {cpu_critical_percent}% (위험)")

    def check_process(self, pid: Optional[int]) -> HealthCheckResult:
        """
        프로세스 헬스체크 수행

        Args:
            pid: 프로세스 ID

        Returns:
            HealthCheckResult: 헬스체크 결과
        """
        if pid is None:
            return HealthCheckResult(
                status=HealthStatus.UNKNOWN,
                message="프로세스 ID가 없습니다.",
                details={}
            )

        try:
            # psutil로 프로세스 정보 가져오기
            process = psutil.Process(pid)

            # 프로세스 존재 여부 확인
            if not process.is_running():
                return HealthCheckResult(
                    status=HealthStatus.CRITICAL,
                    message="프로세스가 실행 중이 아닙니다.",
                    details={'pid': pid}
                )

            # 메모리 사용량 체크
            memory_check = self._check_memory(process)

            # CPU 사용량 체크 (1초간 측정)
            cpu_check = self._check_cpu(process)

            # 전체 상태 결정
            overall_status = self._determine_overall_status(memory_check, cpu_check)

            # 상세 정보 수집
            details = {
                'pid': pid,
                'memory_mb': memory_check[2],
                'memory_percent': memory_check[3],
                'cpu_percent': cpu_check[2],
                'memory_status': memory_check[0].value,
                'cpu_status': cpu_check[0].value,
                'num_threads': process.num_threads(),
                'status': process.status()
            }

            # 메시지 구성
            message_parts = []
            if memory_check[0] != HealthStatus.HEALTHY:
                message_parts.append(memory_check[1])
            if cpu_check[0] != HealthStatus.HEALTHY:
                message_parts.append(cpu_check[1])

            if not message_parts:
                message = "정상"
            else:
                message = ", ".join(message_parts)

            return HealthCheckResult(
                status=overall_status,
                message=message,
                details=details
            )

        except psutil.NoSuchProcess:
            return HealthCheckResult(
                status=HealthStatus.CRITICAL,
                message="프로세스를 찾을 수 없습니다.",
                details={'pid': pid}
            )
        except psutil.AccessDenied:
            return HealthCheckResult(
                status=HealthStatus.WARNING,
                message="프로세스 정보 접근 권한이 없습니다.",
                details={'pid': pid}
            )
        except Exception as e:
            logger.error(f"헬스체크 중 예외 발생: {e}")
            return HealthCheckResult(
                status=HealthStatus.UNKNOWN,
                message=f"헬스체크 실패: {str(e)}",
                details={'pid': pid, 'error': str(e)}
            )

    def _check_memory(self, process: psutil.Process) -> Tuple[HealthStatus, str, float, float]:
        """
        메모리 사용량 체크

        Returns:
            Tuple[HealthStatus, str, float, float]: (상태, 메시지, 메모리 MB, 메모리 %)
        """
        try:
            memory_info = process.memory_info()
            memory_mb = memory_info.rss / 1024 / 1024  # MB로 변환
            memory_percent = process.memory_percent()

            if memory_mb >= self.memory_critical_mb:
                return (
                    HealthStatus.CRITICAL,
                    f"메모리 사용량 위험 ({memory_mb:.1f}MB)",
                    memory_mb,
                    memory_percent
                )
            elif memory_mb >= self.memory_warning_mb:
                return (
                    HealthStatus.WARNING,
                    f"메모리 사용량 높음 ({memory_mb:.1f}MB)",
                    memory_mb,
                    memory_percent
                )
            else:
                return (
                    HealthStatus.HEALTHY,
                    "메모리 정상",
                    memory_mb,
                    memory_percent
                )

        except Exception as e:
            logger.error(f"메모리 체크 실패: {e}")
            return (
                HealthStatus.UNKNOWN,
                f"메모리 체크 실패: {str(e)}",
                0.0,
                0.0
            )

    def _check_cpu(self, process: psutil.Process) -> Tuple[HealthStatus, str, float]:
        """
        CPU 사용량 체크

        Returns:
            Tuple[HealthStatus, str, float]: (상태, 메시지, CPU %)
        """
        try:
            # 1초간 CPU 사용량 측정
            cpu_percent = process.cpu_percent(interval=1.0)

            if cpu_percent >= self.cpu_critical_percent:
                return (
                    HealthStatus.CRITICAL,
                    f"CPU 사용량 위험 ({cpu_percent:.1f}%)",
                    cpu_percent
                )
            elif cpu_percent >= self.cpu_warning_percent:
                return (
                    HealthStatus.WARNING,
                    f"CPU 사용량 높음 ({cpu_percent:.1f}%)",
                    cpu_percent
                )
            else:
                return (
                    HealthStatus.HEALTHY,
                    "CPU 정상",
                    cpu_percent
                )

        except Exception as e:
            logger.error(f"CPU 체크 실패: {e}")
            return (
                HealthStatus.UNKNOWN,
                f"CPU 체크 실패: {str(e)}",
                0.0
            )

    def _determine_overall_status(
        self,
        memory_check: Tuple[HealthStatus, str, float, float],
        cpu_check: Tuple[HealthStatus, str, float]
    ) -> HealthStatus:
        """
        전체 헬스 상태 결정

        Args:
            memory_check: 메모리 체크 결과
            cpu_check: CPU 체크 결과

        Returns:
            HealthStatus: 전체 상태
        """
        memory_status = memory_check[0]
        cpu_status = cpu_check[0]

        # 하나라도 CRITICAL이면 전체가 CRITICAL
        if memory_status == HealthStatus.CRITICAL or cpu_status == HealthStatus.CRITICAL:
            return HealthStatus.CRITICAL

        # 하나라도 WARNING이면 전체가 WARNING
        if memory_status == HealthStatus.WARNING or cpu_status == HealthStatus.WARNING:
            return HealthStatus.WARNING

        # 하나라도 UNKNOWN이면 전체가 UNKNOWN
        if memory_status == HealthStatus.UNKNOWN or cpu_status == HealthStatus.UNKNOWN:
            return HealthStatus.UNKNOWN

        # 둘 다 HEALTHY면 전체가 HEALTHY
        return HealthStatus.HEALTHY

    def get_process_info(self, pid: Optional[int]) -> Optional[Dict]:
        """
        프로세스 상세 정보 조회

        Args:
            pid: 프로세스 ID

        Returns:
            Optional[Dict]: 프로세스 정보 (없으면 None)
        """
        if pid is None:
            return None

        try:
            process = psutil.Process(pid)

            return {
                'pid': pid,
                'name': process.name(),
                'status': process.status(),
                'cpu_percent': process.cpu_percent(interval=0.1),
                'memory_mb': process.memory_info().rss / 1024 / 1024,
                'memory_percent': process.memory_percent(),
                'num_threads': process.num_threads(),
                'create_time': process.create_time(),
                'cmdline': ' '.join(process.cmdline())
            }

        except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
            logger.warning(f"프로세스 정보 조회 실패 (PID={pid}): {e}")
            return None
        except Exception as e:
            logger.error(f"프로세스 정보 조회 중 예외 (PID={pid}): {e}")
            return None


# 전역 헬스체커 인스턴스
_health_checker: Optional[ProcessHealthChecker] = None


def get_health_checker() -> ProcessHealthChecker:
    """
    전역 헬스체커 인스턴스 반환

    Returns:
        ProcessHealthChecker: 헬스체커 인스턴스
    """
    global _health_checker
    if _health_checker is None:
        _health_checker = ProcessHealthChecker()
    return _health_checker


def check_process_health(pid: Optional[int]) -> HealthCheckResult:
    """
    프로세스 헬스체크 수행 (편의 함수)

    Args:
        pid: 프로세스 ID

    Returns:
        HealthCheckResult: 헬스체크 결과
    """
    checker = get_health_checker()
    return checker.check_process(pid)

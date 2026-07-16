"""
API 재시도 유틸리티 모듈
모든 API 호출에 대해 오류 발생 시 1분 간격으로 3번 재시도하는 기능을 제공합니다.
"""

import time
import functools
from typing import Any, Callable, Optional, Tuple, Union
from utils.logging_config import logger


def api_retry(max_retries: int = 3, delay_seconds: int = 60):
    """
    API 호출 재시도 데코레이터
    
    Args:
        max_retries: 최대 재시도 횟수 (기본값: 3)
        delay_seconds: 재시도 간격 (초, 기본값: 60)
    
    Returns:
        데코레이터 함수
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            last_exception = None
            
            for attempt in range(max_retries + 1):  # +1은 첫 시도 포함
                try:
                    result = func(*args, **kwargs)
                    if attempt > 0:
                        logger.info(f"API 호출 성공 (재시도 {attempt}번째): {func.__name__}")
                    return result
                    
                except Exception as e:
                    last_exception = e
                    
                    if attempt < max_retries:
                        logger.warning(
                            f"API 호출 실패 (시도 {attempt + 1}/{max_retries + 1}): {func.__name__} - {str(e)[:100]}"
                        )
                        logger.info(f"{delay_seconds}초 후 재시도...")
                        time.sleep(delay_seconds)
                    else:
                        logger.error(
                            f"API 호출 최종 실패: {func.__name__} - {str(e)}"
                        )
            
            # 모든 재시도 실패 시 마지막 예외 발생
            raise last_exception
            
        return wrapper
    return decorator


def api_retry_with_backoff(max_retries: int = 3, base_delay: int = 60, backoff_factor: float = 1.0):
    """
    백오프(backoff) 기능이 있는 API 재시도 데코레이터
    재시도할 때마다 대기 시간이 점진적으로 증가합니다.
    
    Args:
        max_retries: 최대 재시도 횟수 (기본값: 3)
        base_delay: 기본 대기 시간 (초, 기본값: 60)
        backoff_factor: 백오프 배수 (기본값: 1.0, 고정 간격)
    
    Returns:
        데코레이터 함수
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            last_exception = None
            
            for attempt in range(max_retries + 1):
                try:
                    result = func(*args, **kwargs)
                    if attempt > 0:
                        logger.info(f"API 호출 성공 (재시도 {attempt}번째): {func.__name__}")
                    return result
                    
                except Exception as e:
                    last_exception = e
                    
                    if attempt < max_retries:
                        # 백오프 계산
                        delay = base_delay * (backoff_factor ** attempt)
                        logger.warning(
                            f"API 호출 실패 (시도 {attempt + 1}/{max_retries + 1}): {func.__name__} - {str(e)[:100]}"
                        )
                        logger.info(f"{delay:.1f}초 후 재시도...")
                        time.sleep(delay)
                    else:
                        logger.error(
                            f"API 호출 최종 실패: {func.__name__} - {str(e)}"
                        )
            
            raise last_exception
            
        return wrapper
    return decorator


class APIRetryManager:
    """API 재시도 관리 클래스"""
    
    def __init__(self, default_max_retries: int = 3, default_delay: int = 60):
        """
        APIRetryManager 초기화
        
        Args:
            default_max_retries: 기본 최대 재시도 횟수
            default_delay: 기본 대기 시간 (초)
        """
        self.default_max_retries = default_max_retries
        self.default_delay = default_delay
        self.retry_stats = {
            'total_calls': 0,
            'successful_calls': 0,
            'failed_calls': 0,
            'retry_attempts': 0
        }
    
    def execute_with_retry(self, func: Callable, *args, max_retries: Optional[int] = None, 
                          delay: Optional[int] = None, **kwargs) -> Any:
        """
        함수를 재시도 로직과 함께 실행
        
        Args:
            func: 실행할 함수
            *args: 함수 인자
            max_retries: 최대 재시도 횟수 (None이면 기본값 사용)
            delay: 대기 시간 (None이면 기본값 사용)
            **kwargs: 함수 키워드 인자
            
        Returns:
            함수 실행 결과
        """
        retries = max_retries if max_retries is not None else self.default_max_retries
        wait_time = delay if delay is not None else self.default_delay
        last_exception = None
        
        self.retry_stats['total_calls'] += 1
        
        for attempt in range(retries + 1):
            try:
                result = func(*args, **kwargs)
                self.retry_stats['successful_calls'] += 1
                if attempt > 0:
                    logger.info(f"API 호출 성공 (재시도 {attempt}번째): {func.__name__}")
                return result
                
            except Exception as e:
                last_exception = e
                
                if attempt < retries:
                    self.retry_stats['retry_attempts'] += 1
                    logger.warning(
                        f"API 호출 실패 (시도 {attempt + 1}/{retries + 1}): {func.__name__} - {str(e)[:100]}"
                    )
                    logger.info(f"{wait_time}초 후 재시도...")
                    time.sleep(wait_time)
                else:
                    self.retry_stats['failed_calls'] += 1
                    logger.error(
                        f"API 호출 최종 실패: {func.__name__} - {str(e)}"
                    )
        
        raise last_exception
    
    def get_stats(self) -> dict:
        """재시도 통계 반환"""
        stats = self.retry_stats.copy()
        
        if stats['total_calls'] > 0:
            stats['success_rate'] = (stats['successful_calls'] / stats['total_calls']) * 100
            stats['retry_rate'] = (stats['retry_attempts'] / stats['total_calls']) * 100
        else:
            stats['success_rate'] = 0
            stats['retry_rate'] = 0
            
        return stats
    
    def reset_stats(self):
        """통계 초기화"""
        self.retry_stats = {
            'total_calls': 0,
            'successful_calls': 0,
            'failed_calls': 0,
            'retry_attempts': 0
        }
        logger.info("API 재시도 통계 초기화")


# 전역 재시도 매니저 인스턴스
_global_retry_manager = APIRetryManager()


def get_retry_manager() -> APIRetryManager:
    """전역 재시도 매니저 반환"""
    return _global_retry_manager


def execute_api_call(func: Callable, *args, **kwargs) -> Any:
    """
    편의 함수: API 호출을 재시도 로직과 함께 실행
    
    Args:
        func: 실행할 함수
        *args: 함수 인자
        **kwargs: 함수 키워드 인자
        
    Returns:
        함수 실행 결과
    """
    return _global_retry_manager.execute_with_retry(func, *args, **kwargs)


# 특정 예외 타입에 대한 재시도 필터
def should_retry_exception(exception: Exception) -> bool:
    """
    예외 타입에 따라 재시도 여부 결정
    
    Args:
        exception: 발생한 예외
        
    Returns:
        bool: 재시도 여부
    """
    # 네트워크 관련 예외는 재시도
    network_exceptions = [
        'ConnectionError',
        'TimeoutError', 
        'HTTPError',
        'MastodonNetworkError',
        'MastodonAPIError'
    ]
    
    exception_name = type(exception).__name__
    
    # HTTP 5xx 에러는 재시도
    if hasattr(exception, 'response') and hasattr(exception.response, 'status_code'):
        status_code = exception.response.status_code
        if 500 <= status_code < 600:
            return True
    
    # 네트워크 관련 예외는 재시도
    if any(error_type in exception_name for error_type in network_exceptions):
        return True
    
    # 에러 메시지에서 재시도 가능한 키워드 확인
    error_message = str(exception).lower()
    retry_keywords = ['timeout', 'connection', 'network', 'server error', '503', '502', '500']
    
    if any(keyword in error_message for keyword in retry_keywords):
        return True
    
    return False


def smart_api_retry(max_retries: int = 3, delay_seconds: int = 60):
    """
    스마트 API 재시도 데코레이터
    예외 타입에 따라 재시도 여부를 결정합니다.
    
    Args:
        max_retries: 최대 재시도 횟수
        delay_seconds: 재시도 간격
        
    Returns:
        데코레이터 함수
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            last_exception = None
            
            for attempt in range(max_retries + 1):
                try:
                    result = func(*args, **kwargs)
                    if attempt > 0:
                        logger.info(f"API 호출 성공 (재시도 {attempt}번째): {func.__name__}")
                    return result
                    
                except Exception as e:
                    last_exception = e
                    
                    # 재시도 가능한 예외인지 확인
                    if not should_retry_exception(e):
                        logger.info(f"재시도하지 않는 예외 타입: {type(e).__name__} - {func.__name__}")
                        raise e
                    
                    if attempt < max_retries:
                        logger.warning(
                            f"API 호출 실패 (시도 {attempt + 1}/{max_retries + 1}): {func.__name__} - {str(e)[:100]}"
                        )
                        logger.info(f"{delay_seconds}초 후 재시도...")
                        time.sleep(delay_seconds)
                    else:
                        logger.error(
                            f"API 호출 최종 실패: {func.__name__} - {str(e)}"
                        )
            
            raise last_exception
            
        return wrapper
    return decorator
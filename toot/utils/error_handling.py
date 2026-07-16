"""
utils/error_handling.py

에러 처리 모듈
툿수 모니터링 봇의 예외와 에러 처리를 담당합니다.
"""

import functools
import os
import sys
import time
import traceback
from typing import Any, Callable, Optional, Type, Union, Tuple
from dataclasses import dataclass
from enum import Enum

# VM 환경 대응
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
sys.path.append(project_root)

try:
    from config.settings import config
    from utils.logging_config import logger, toot_logger
except ImportError:
    # 임포트 실패 시 기본 로거
    import logging
    logger = logging.getLogger('error_handling')
    toot_logger = None


# 커스텀 예외 클래스들
class TootMonitorException(Exception):
    """툿수 모니터링 봇 관련 기본 예외 클래스"""
    
    def __init__(self, message: str, error_code: str = None, context: dict = None):
        super().__init__(message)
        self.message = message
        self.error_code = error_code or 'UNKNOWN_ERROR'
        self.context = context or {}
    
    def __str__(self):
        return self.message
    
    def get_user_message(self) -> str:
        """사용자에게 표시할 메시지 반환"""
        return self.message


class SheetAccessError(TootMonitorException):
    """Google Sheets 접근 관련 오류"""
    
    def __init__(self, message: str = None, worksheet: str = None, operation: str = None):
        if message is None:
            message = config.get_error_message('GOOGLE_SHEETS_ERROR')
        
        super().__init__(
            message=message,
            error_code='SHEET_ACCESS_ERROR',
            context={'worksheet': worksheet, 'operation': operation}
        )
        self.worksheet = worksheet
        self.operation = operation


class MastodonAPIError(TootMonitorException):
    """마스토돈 API 관련 오류"""
    
    def __init__(self, message: str = None, api_operation: str = None, status_code: int = None):
        if message is None:
            message = config.get_error_message('MASTODON_API_ERROR')
        
        super().__init__(
            message=message,
            error_code='MASTODON_API_ERROR',
            context={'api_operation': api_operation, 'status_code': status_code}
        )
        self.api_operation = api_operation
        self.status_code = status_code


class UserNotFoundError(TootMonitorException):
    """사용자를 찾을 수 없는 오류"""
    
    def __init__(self, user_id: str):
        super().__init__(
            message=config.get_error_message('USER_NOT_FOUND'),
            error_code='USER_NOT_FOUND',
            context={'user_id': user_id}
        )
        self.user_id = user_id


class TootCountError(TootMonitorException):
    """툿수 계산 관련 오류"""
    
    def __init__(self, message: str, user_id: str = None, old_count: int = None, new_count: int = None):
        super().__init__(
            message=message,
            error_code='TOOT_COUNT_ERROR',
            context={'user_id': user_id, 'old_count': old_count, 'new_count': new_count}
        )
        self.user_id = user_id
        self.old_count = old_count
        self.new_count = new_count


class RewardSystemError(TootMonitorException):
    """재화 지급 시스템 관련 오류"""
    
    def __init__(self, message: str, user_id: str = None, reward_amount: int = None):
        super().__init__(
            message=message,
            error_code='REWARD_SYSTEM_ERROR',
            context={'user_id': user_id, 'reward_amount': reward_amount}
        )
        self.user_id = user_id
        self.reward_amount = reward_amount


class ConfigurationError(TootMonitorException):
    """설정 관련 오류"""
    
    def __init__(self, message: str, setting_name: str = None):
        super().__init__(
            message=message,
            error_code='CONFIGURATION_ERROR',
            context={'setting_name': setting_name}
        )
        self.setting_name = setting_name


# 에러 처리 결과 타입
@dataclass
class ErrorHandlingResult:
    """에러 처리 결과"""
    success: bool
    result: Any = None
    error: Optional[Exception] = None
    retry_count: int = 0
    duration: float = 0.0
    
    @property
    def failed(self) -> bool:
        """실패 여부"""
        return not self.success


class ErrorSeverity(Enum):
    """에러 심각도"""
    LOW = "low"          # 로그만 기록
    MEDIUM = "medium"    # 로그 + 재시도
    HIGH = "high"        # 로그 + 재시도 + 관리자 알림
    CRITICAL = "critical" # 시스템 종료 고려


class ErrorHandler:
    """에러 처리 담당 클래스"""
    
    @staticmethod
    def handle_api_error(error: Exception, max_retries: int = None) -> ErrorHandlingResult:
        """
        API 관련 에러 처리 (Google Sheets, Mastodon 등)
        
        Args:
            error: 발생한 예외
            max_retries: 최대 재시도 횟수
            
        Returns:
            ErrorHandlingResult: 처리 결과
        """
        max_retries = max_retries or config.MAX_RETRIES
        
        # Google Sheets API 에러
        if 'gspread' in str(type(error)) or 'APIError' in str(type(error)):
            if any(code in str(error) for code in ['500', '503', 'Internal error', 'quota']):
                return ErrorHandlingResult(
                    success=False,
                    error=SheetAccessError(operation="API 호출"),
                )
            else:
                return ErrorHandlingResult(
                    success=False,
                    error=SheetAccessError(f"시트 API 오류: {str(error)}"),
                )
        
        # 마스토돈 API 에러
        if 'mastodon' in str(type(error)).lower() or 'MastodonError' in str(type(error)):
            status_code = getattr(error, 'response', {}).get('status_code', None)
            return ErrorHandlingResult(
                success=False,
                error=MastodonAPIError(f"마스토돈 API 오류: {str(error)}", status_code=status_code)
            )
        
        # 기타 API 에러
        return ErrorHandlingResult(
            success=False,
            error=TootMonitorException(f"API 오류: {str(error)}")
        )
    
    @staticmethod
    def get_error_severity(error: Exception) -> ErrorSeverity:
        """
        에러의 심각도를 결정합니다.
        
        Args:
            error: 예외 객체
            
        Returns:
            ErrorSeverity: 에러 심각도
        """
        if isinstance(error, UserNotFoundError):
            return ErrorSeverity.LOW
        
        if isinstance(error, (TootCountError, ConfigurationError)):
            return ErrorSeverity.MEDIUM
        
        if isinstance(error, (SheetAccessError, RewardSystemError)):
            return ErrorSeverity.HIGH
        
        if isinstance(error, MastodonAPIError):
            return ErrorSeverity.CRITICAL
        
        return ErrorSeverity.MEDIUM


def safe_execute(
    operation_func: Callable,
    max_retries: int = None,
    fallback_return: Any = None,
    error_handler: Callable[[Exception], ErrorHandlingResult] = None
) -> ErrorHandlingResult:
    """
    안전한 작업 실행을 위한 래퍼 함수
    
    Args:
        operation_func: 실행할 함수
        max_retries: 최대 재시도 횟수
        fallback_return: 실패 시 반환할 기본값
        error_handler: 커스텀 에러 핸들러
        
    Returns:
        ErrorHandlingResult: 실행 결과
    """
    max_retries = max_retries or config.MAX_RETRIES
    last_error = None
    start_time = time.time()
    
    for attempt in range(max_retries):
        try:
            result = operation_func()
            duration = time.time() - start_time
            return ErrorHandlingResult(
                success=True, 
                result=result, 
                retry_count=attempt,
                duration=duration
            )
            
        except Exception as e:
            last_error = e
            
            # API 에러인 경우 재시도 조건 확인
            if _is_retryable_error(e) and attempt < max_retries - 1:
                wait_time = min(config.BASE_WAIT_TIME ** (attempt + 1), config.MAX_WAIT_TIME)
                logger.warning(f"재시도 {attempt + 1}/{max_retries} - {wait_time}초 대기: {str(e)}")
                time.sleep(wait_time)
                continue
            else:
                # 재시도하지 않는 에러의 경우 즉시 중단
                break
    
    # 모든 재시도 실패 또는 재시도하지 않는 에러
    duration = time.time() - start_time
    
    if error_handler:
        result = error_handler(last_error)
    else:
        result = ErrorHandler.handle_api_error(last_error, max_retries)
    
    result.retry_count = max_retries
    result.duration = duration
    
    return result


def _is_retryable_error(error: Exception) -> bool:
    """재시도 가능한 에러인지 확인"""
    error_str = str(error).lower()
    
    # Google Sheets API 재시도 가능 에러
    if any(code in error_str for code in ['500', '503', 'internal error', 'quota', 'timeout']):
        return True
    
    # 마스토돈 API 재시도 가능 에러
    if any(code in error_str for code in ['429', 'rate limit', 'timeout', '502', '503']):
        return True
    
    # 네트워크 관련 에러
    if any(msg in error_str for msg in ['connection', 'network', 'timeout']):
        return True
    
    return False


def retry_on_error(max_retries: int = None, fallback_return: Any = None):
    """
    에러 발생 시 자동 재시도하는 데코레이터
    
    Args:
        max_retries: 최대 재시도 횟수
        fallback_return: 실패 시 반환할 기본값
        
    Returns:
        데코레이터 함수
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            def operation():
                return func(*args, **kwargs)
            
            result = safe_execute(
                operation_func=operation,
                max_retries=max_retries,
                fallback_return=fallback_return
            )
            
            if result.success:
                return result.result
            else:
                if fallback_return is not None:
                    return fallback_return
                else:
                    raise result.error
        
        return wrapper
    return decorator


def log_error_context(error: Exception, context: dict = None) -> None:
    """
    에러와 컨텍스트를 함께 로깅합니다.
    
    Args:
        error: 발생한 예외
        context: 추가 컨텍스트 정보
    """
    error_context = {}
    
    # 기본 에러 정보
    error_context['error_type'] = type(error).__name__
    error_context['error_message'] = str(error)
    
    # TootMonitorException의 경우 추가 정보
    if isinstance(error, TootMonitorException):
        error_context['error_code'] = error.error_code
        error_context.update(error.context)
    
    # 제공된 컨텍스트 추가
    if context:
        error_context.update(context)
    
    # 심각도에 따른 로깅
    severity = ErrorHandler.get_error_severity(error)
    
    if severity == ErrorSeverity.CRITICAL:
        logger.critical(f"치명적 오류 발생: {error}", exc_info=True)
    elif severity == ErrorSeverity.HIGH:
        logger.error(f"심각한 오류 발생: {error}", exc_info=config.DEBUG_MODE)
    elif severity == ErrorSeverity.MEDIUM:
        logger.warning(f"경고 수준 오류 발생: {error}")
    else:
        logger.info(f"일반 오류 발생: {error}")
    
    # 툿수 로거에 컨텍스트와 함께 기록
    if toot_logger:
        toot_logger.log_error_with_context(error, error_context)


# 컨텍스트 매니저
class ErrorContext:
    """에러 처리를 위한 컨텍스트 매니저"""
    
    def __init__(self, operation: str, user_id: str = None, **context):
        self.operation = operation
        self.user_id = user_id
        self.context = context
        self.error_occurred = False
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self.error_occurred = True
            
            # 컨텍스트 정보 구성
            error_context = {
                'operation': self.operation,
                'user_id': self.user_id,
                **self.context
            }
            
            # 에러 로깅
            log_error_context(exc_val, error_context)
            
            # 관리자 알림이 필요한 경우
            if _should_notify_admin(exc_val):
                _send_admin_notification(exc_val, error_context)
        
        return False  # 예외를 다시 발생시킴
    
    def add_context(self, **kwargs):
        """런타임에 컨텍스트 추가"""
        self.context.update(kwargs)


def _should_notify_admin(error: Exception) -> bool:
    """관리자에게 알림을 보내야 하는지 확인"""
    severity = ErrorHandler.get_error_severity(error)
    return severity in [ErrorSeverity.HIGH, ErrorSeverity.CRITICAL] and config.NOTIFICATION_ENABLED


def _send_admin_notification(error: Exception, context: dict):
    """관리자에게 알림 전송 (구현은 추후)"""
    # TODO: 마스토돈 DM 또는 다른 방식으로 관리자 알림
    logger.info(f"관리자 알림 필요: {type(error).__name__} - {str(error)}")


# 특화된 에러 핸들러들
class SheetErrorHandler:
    """Google Sheets 전용 에러 핸들러"""
    
    @staticmethod
    def handle_worksheet_not_found(worksheet_name: str) -> SheetAccessError:
        """워크시트를 찾을 수 없는 경우"""
        return SheetAccessError(
            message=f"'{worksheet_name}' 시트를 찾을 수 없습니다.",
            worksheet=worksheet_name,
            operation="worksheet_access"
        )
    
    @staticmethod
    def handle_data_not_found(worksheet_name: str) -> SheetAccessError:
        """데이터를 찾을 수 없는 경우"""
        return SheetAccessError(
            message=config.get_error_message('SHEET_ACCESS_ERROR'),
            worksheet=worksheet_name,
            operation="data_access"
        )
    
    @staticmethod
    def handle_api_quota_exceeded() -> SheetAccessError:
        """API 할당량 초과"""
        return SheetAccessError(
            message="Google Sheets API 할당량이 초과되었습니다. 잠시 후 다시 시도해 주세요.",
            operation="api_quota"
        )


class MastodonErrorHandler:
    """마스토돈 API 전용 에러 핸들러"""
    
    @staticmethod
    def handle_rate_limit() -> MastodonAPIError:
        """API 요청 제한 초과"""
        return MastodonAPIError(
            message="마스토돈 API 요청 제한을 초과했습니다. 잠시 후 다시 시도합니다.",
            api_operation="rate_limit",
            status_code=429
        )
    
    @staticmethod
    def handle_user_not_found(user_id: str) -> MastodonAPIError:
        """사용자를 찾을 수 없음"""
        return MastodonAPIError(
            message=f"마스토돈에서 사용자를 찾을 수 없습니다: {user_id}",
            api_operation="user_lookup"
        )
    
    @staticmethod
    def handle_network_error() -> MastodonAPIError:
        """네트워크 오류"""
        return MastodonAPIError(
            message="마스토돈 서버에 연결할 수 없습니다. 네트워크 상태를 확인해주세요.",
            api_operation="network"
        )


# 전역 예외 핸들러 (main.py에서 사용)
def setup_global_exception_handler():
    """전역 예외 핸들러 설정"""
    import sys
    
    def handle_exception(exc_type, exc_value, exc_traceback):
        """처리되지 않은 예외 핸들러"""
        if issubclass(exc_type, KeyboardInterrupt):
            # Ctrl+C는 정상적으로 처리
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        
        # 예상치 못한 예외 로깅
        logger.critical(
            f"처리되지 않은 예외 발생: {exc_type.__name__}: {exc_value}",
            exc_info=(exc_type, exc_value, exc_traceback)
        )
    
    sys.excepthook = handle_exception


# 편의 함수들
def create_sheet_error(worksheet: str = None, operation: str = None) -> SheetAccessError:
    """시트 접근 에러 생성"""
    return SheetAccessError(worksheet=worksheet, operation=operation)


def create_mastodon_error(api_operation: str = None, status_code: int = None) -> MastodonAPIError:
    """마스토돈 API 에러 생성"""
    return MastodonAPIError(api_operation=api_operation, status_code=status_code)


def create_user_not_found_error(user_id: str) -> UserNotFoundError:
    """사용자 없음 에러 생성"""
    return UserNotFoundError(user_id)


def create_toot_count_error(message: str, user_id: str = None, old_count: int = None, new_count: int = None) -> TootCountError:
    """툿수 계산 에러 생성"""
    return TootCountError(message, user_id, old_count, new_count)


def create_reward_error(message: str, user_id: str = None, reward_amount: int = None) -> RewardSystemError:
    """재화 지급 에러 생성"""
    return RewardSystemError(message, user_id, reward_amount)


# 에러 체크 함수들
def is_retryable_error(error: Exception) -> bool:
    """재시도 가능한 에러인지 확인"""
    return _is_retryable_error(error)


def is_user_error(error: Exception) -> bool:
    """사용자 관련 오류인지 확인"""
    return isinstance(error, UserNotFoundError)


def is_system_error(error: Exception) -> bool:
    """시스템 오류인지 확인"""
    return isinstance(error, (SheetAccessError, MastodonAPIError, ConfigurationError))


def get_user_friendly_message(error: Exception) -> str:
    """사용자에게 친화적인 에러 메시지 반환"""
    if isinstance(error, TootMonitorException):
        return error.get_user_message()
    
    # 일반적인 예외의 경우
    return config.get_error_message('TEMPORARY_ERROR')


# 에러 통계 수집
class ErrorStats:
    """에러 통계 수집 클래스"""
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.error_counts = {}
            cls._instance.total_errors = 0
        return cls._instance
    
    def record_error(self, error: Exception):
        """에러 발생 기록"""
        error_type = type(error).__name__
        self.error_counts[error_type] = self.error_counts.get(error_type, 0) + 1
        self.total_errors += 1
    
    def get_stats(self) -> dict:
        """에러 통계 반환"""
        return {
            'total_errors': self.total_errors,
            'error_counts': dict(self.error_counts),
            'most_common': max(self.error_counts.items(), key=lambda x: x[1]) if self.error_counts else None
        }
    
    def reset_stats(self):
        """통계 초기화"""
        self.error_counts.clear()
        self.total_errors = 0


# 모듈 레벨 인스턴스
error_stats = ErrorStats()


# 테스트 함수
def test_error_handling():
    """에러 처리 시스템 테스트"""
    logger.info("에러 처리 시스템 테스트 시작")
    
    # 안전한 실행 테스트
    def test_operation():
        return "성공!"
    
    result = safe_execute(test_operation)
    logger.info(f"정상 작업 테스트: {result.success}, 결과: {result.result}")
    
    # 에러 발생 테스트
    def error_operation():
        raise ValueError("테스트 에러")
    
    result = safe_execute(error_operation, max_retries=2)
    logger.info(f"에러 작업 테스트: {result.success}, 에러: {result.error}")
    
    # 컨텍스트 매니저 테스트
    try:
        with ErrorContext("테스트 작업", user_id="test_user"):
            raise TootCountError("테스트 툿수 에러", "test_user", 100, 200)
    except:
        pass
    
    logger.info("에러 처리 시스템 테스트 완료")


if __name__ == "__main__":
    test_error_handling()
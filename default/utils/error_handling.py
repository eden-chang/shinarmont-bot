"""
에러 처리 모듈
애플리케이션 전반의 예외와 에러 처리를 담당합니다.
"""

import functools
import os
import sys
import time
import traceback
from typing import Any, Callable, Optional, Type, Union, Tuple
from dataclasses import dataclass
from enum import Enum

# 경로 설정 (VM 환경 대응)
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

try:
    from gspread.exceptions import APIError
    from config.settings import config
except ImportError:
    # VM 환경에서 임포트 실패 시 폴백
    import importlib.util
    
    # gspread 임포트
    try:
        from gspread.exceptions import APIError
    except ImportError:
        # gspread가 없는 경우 더미 클래스
        class APIError(Exception):
            pass
    
    # config 로드
    config_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'settings.py')
    spec = importlib.util.spec_from_file_location("settings", config_path)
    settings_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(settings_module)
    config = settings_module.config


# 커스텀 예외 클래스들
class BotException(Exception):
    """봇 관련 기본 예외 클래스"""
    
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


class SheetAccessError(BotException):
    """Google Sheets 접근 관련 오류"""
    
    def __init__(self, message: str = None, worksheet: str = None, operation: str = None):
        if message is None:
            message = config.get_error_message('TEMPORARY_ERROR')
        
        super().__init__(
            message=message,
            error_code='SHEET_ACCESS_ERROR',
            context={'worksheet': worksheet, 'operation': operation}
        )
        self.worksheet = worksheet
        self.operation = operation


class UserNotFoundError(BotException):
    """사용자를 찾을 수 없는 오류"""
    
    def __init__(self, user_id: str):
        super().__init__(
            message=config.get_error_message('USER_NOT_FOUND'),
            error_code='USER_NOT_FOUND',
            context={'user_id': user_id}
        )
        self.user_id = user_id


class UserValidationError(BotException):
    """사용자 검증 관련 오류"""
    
    def __init__(self, user_id: str, validation_type: str):
        if validation_type == 'id_check':
            message = config.get_error_message('USER_ID_CHECK_FAILED')
        elif validation_type == 'name_invalid':
            message = config.get_error_message('USER_NAME_INVALID')
        else:
            message = config.get_error_message('TEMPORARY_ERROR')
        
        super().__init__(
            message=message,
            error_code=f'USER_VALIDATION_{validation_type.upper()}',
            context={'user_id': user_id, 'validation_type': validation_type}
        )
        self.user_id = user_id
        self.validation_type = validation_type


class CommandError(BotException):
    """명령어 처리 관련 오류"""
    
    def __init__(self, message: str, command: str = None, user_id: str = None):
        super().__init__(
            message=message,
            error_code='COMMAND_ERROR',
            context={'command': command, 'user_id': user_id}
        )
        self.command = command
        self.user_id = user_id


class DiceError(CommandError):
    """다이스 관련 오류"""
    
    def __init__(self, message: str, dice_expression: str = None):
        super().__init__(
            message=message,
            command=f'dice/{dice_expression}' if dice_expression else 'dice'
        )
        self.dice_expression = dice_expression


class CardError(CommandError):
    """카드 관련 오류"""
    
    def __init__(self, message: str, card_count: int = None):
        super().__init__(
            message=message,
            command=f'card/{card_count}' if card_count else 'card'
        )
        self.card_count = card_count


class FortuneError(CommandError):
    """운세 관련 오류"""
    
    def __init__(self, message: str = None):
        if message is None:
            message = "운세를 가져오는 중 오류가 발생했습니다."
        super().__init__(message=message, command='fortune')



class MastodonError(BotException):
    """마스토돈 API 관련 오류"""
    
    def __init__(self, message: str, api_operation: str = None):
        super().__init__(
            message=message,
            error_code='MASTODON_ERROR',
            context={'api_operation': api_operation}
        )
        self.api_operation = api_operation


# 에러 처리 결과 타입
@dataclass
class ErrorHandlingResult:
    """에러 처리 결과"""
    success: bool
    result: Any = None
    error: Optional[Exception] = None
    user_message: Optional[str] = None
    retry_count: int = 0
    
    @property
    def should_notify_user(self) -> bool:
        """사용자에게 알림을 보내야 하는지 여부"""
        return self.user_message is not None


class ErrorSeverity(Enum):
    """에러 심각도"""
    LOW = "low"          # 로그만 기록
    MEDIUM = "medium"    # 로그 + 사용자 알림
    HIGH = "high"        # 로그 + 사용자 알림 + 관리자 알림
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
        
        if isinstance(error, APIError):
            # Google Sheets API 에러
            if any(code in str(error) for code in ['500', '503', 'Internal error']):
                return ErrorHandlingResult(
                    success=False,
                    error=SheetAccessError(operation="API 호출"),
                    user_message=config.get_error_message('TEMPORARY_ERROR')
                )
            else:
                return ErrorHandlingResult(
                    success=False,
                    error=SheetAccessError(f"시트 API 오류: {str(error)}"),
                    user_message=config.get_error_message('TEMPORARY_ERROR')
                )
        
        # 기타 API 에러
        return ErrorHandlingResult(
            success=False,
            error=BotException(f"API 오류: {str(error)}"),
            user_message=config.get_error_message('TEMPORARY_ERROR')
        )
    
    @staticmethod
    def handle_user_error(error: Exception, user_id: str) -> ErrorHandlingResult:
        """
        사용자 관련 에러 처리
        
        Args:
            error: 발생한 예외
            user_id: 사용자 ID
            
        Returns:
            ErrorHandlingResult: 처리 결과
        """
        if isinstance(error, (UserNotFoundError, UserValidationError)):
            return ErrorHandlingResult(
                success=False,
                error=error,
                user_message=error.get_user_message()
            )
        
        return ErrorHandlingResult(
            success=False,
            error=BotException(f"사용자 처리 오류: {str(error)}", context={'user_id': user_id}),
            user_message=config.get_error_message('TEMPORARY_ERROR')
        )
    
    @staticmethod
    def handle_command_error(error: Exception, command: str, user_id: str) -> ErrorHandlingResult:
        """
        명령어 처리 에러 핸들링
        
        Args:
            error: 발생한 예외
            command: 실행된 명령어
            user_id: 사용자 ID
            
        Returns:
            ErrorHandlingResult: 처리 결과
        """
        if isinstance(error, (DiceError, CardError, FortuneError)):
            return ErrorHandlingResult(
                success=False,
                error=error,
                user_message=error.get_user_message()
            )
        
        if isinstance(error, CommandError):
            return ErrorHandlingResult(
                success=False,
                error=error,
                user_message=error.get_user_message()
            )
        
        # 예상치 못한 명령어 에러
        return ErrorHandlingResult(
            success=False,
            error=CommandError(
                message=f"명령어 처리 중 예상치 못한 오류: {str(error)}",
                command=command,
                user_id=user_id
            ),
            user_message=config.get_error_message('TEMPORARY_ERROR')
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
        if isinstance(error, (UserNotFoundError, DiceError, CardError)):
            return ErrorSeverity.LOW
        
        if isinstance(error, (UserValidationError, CommandError, FortuneError)):
            return ErrorSeverity.MEDIUM
        
        if isinstance(error, SheetAccessError):
            return ErrorSeverity.HIGH
        
        if isinstance(error, MastodonError):
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
    
    for attempt in range(max_retries):
        try:
            result = operation_func()
            return ErrorHandlingResult(success=True, result=result)
            
        except Exception as e:
            last_error = e
            
            # API 에러인 경우 재시도 조건 확인
            if isinstance(e, APIError) and any(code in str(e) for code in ['500', '503', 'Internal error']):
                if attempt < max_retries - 1:  # 마지막 시도가 아닌 경우
                    wait_time = config.BASE_WAIT_TIME ** (attempt + 1)
                    time.sleep(wait_time)
                    continue
            else:
                # 재시도하지 않는 에러의 경우 즉시 중단
                break
    
    # 모든 재시도 실패 또는 재시도하지 않는 에러
    if error_handler:
        return error_handler(last_error)
    else:
        return ErrorHandler.handle_api_error(last_error, max_retries)


def retry_on_api_error(max_retries: int = None, fallback_return: Any = None):
    """
    API 에러 발생 시 자동 재시도하는 데코레이터
    
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


def handle_user_command_errors(func: Callable) -> Callable:
    """
    사용자 명령어 처리 에러를 핸들링하는 데코레이터
    
    Args:
        func: 명령어 처리 함수
        
    Returns:
        래핑된 함수
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs) -> Tuple[str, bool]:
        try:
            return func(*args, **kwargs), True
            
        except (UserNotFoundError, UserValidationError, DiceError, CardError, FortuneError) as e:
            # 예상된 사용자 에러 - 사용자 메시지 반환
            return e.get_user_message(), False
            
        except Exception as e:
            # 예상치 못한 에러 - 일반적인 에러 메시지 반환
            return config.get_error_message('TEMPORARY_ERROR'), False
    
    return wrapper


def log_error_context(error: Exception, context: dict = None) -> None:
    """
    에러와 컨텍스트를 함께 로깅합니다.
    
    Args:
        error: 발생한 예외
        context: 추가 컨텍스트 정보
    """
    from utils.logging_config import bot_logger
    
    error_context = {}
    
    # 기본 에러 정보
    error_context['error_type'] = type(error).__name__
    error_context['error_message'] = str(error)
    
    # BotException의 경우 추가 정보
    if isinstance(error, BotException):
        error_context['error_code'] = error.error_code
        error_context.update(error.context)
    
    # 제공된 컨텍스트 추가
    if context:
        error_context.update(context)
    
    # 심각도에 따른 로깅
    severity = ErrorHandler.get_error_severity(error)
    
    if severity == ErrorSeverity.CRITICAL:
        bot_logger.logger.critical(f"치명적 오류 발생", exc_info=True)
    elif severity == ErrorSeverity.HIGH:
        bot_logger.logger.error(f"심각한 오류 발생", exc_info=config.DEBUG_MODE)
    elif severity == ErrorSeverity.MEDIUM:
        bot_logger.logger.warning(f"경고 수준 오류 발생")
    else:
        bot_logger.logger.info(f"일반 오류 발생")
    
    # 컨텍스트 정보 로깅
    bot_logger.log_error_with_context(error, error_context)


# 편의 함수들
def create_user_not_found_error(user_id: str) -> UserNotFoundError:
    """사용자 없음 에러 생성"""
    return UserNotFoundError(user_id)


def create_dice_error(message: str, dice_expression: str = None) -> DiceError:
    """다이스 에러 생성"""
    return DiceError(message, dice_expression)


def create_card_error(message: str, card_count: int = None) -> CardError:
    """카드 에러 생성"""
    return CardError(message, card_count)


def create_sheet_error(worksheet: str = None, operation: str = None) -> SheetAccessError:
    """시트 접근 에러 생성"""
    return SheetAccessError(worksheet=worksheet, operation=operation)


# 에러 체크 함수들
def is_retryable_error(error: Exception) -> bool:
    """재시도 가능한 에러인지 확인"""
    if isinstance(error, APIError):
        return any(code in str(error) for code in ['500', '503', 'Internal error'])
    return False


def is_user_error(error: Exception) -> bool:
    """사용자 입력 오류인지 확인"""
    return isinstance(error, (UserNotFoundError, UserValidationError, DiceError, CardError, CommandError))


def is_system_error(error: Exception) -> bool:
    """시스템 오류인지 확인"""
    return isinstance(error, (SheetAccessError, MastodonError))


def should_notify_admin(error: Exception) -> bool:
    """관리자에게 알림을 보내야 하는지 확인"""
    severity = ErrorHandler.get_error_severity(error)
    return severity in [ErrorSeverity.HIGH, ErrorSeverity.CRITICAL]


def get_user_friendly_message(error: Exception) -> str:
    """사용자에게 친화적인 에러 메시지 반환"""
    if isinstance(error, BotException):
        return error.get_user_message()
    
    if isinstance(error, APIError):
        return config.get_error_message('TEMPORARY_ERROR')
    
    # 일반적인 예외의 경우
    return config.get_error_message('TEMPORARY_ERROR')


# 컨텍스트 매니저
class ErrorContext:
    """에러 처리를 위한 컨텍스트 매니저"""
    
    def __init__(self, operation: str, user_id: str = None, command: str = None, **context):
        self.operation = operation
        self.user_id = user_id
        self.command = command
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
                'command': self.command,
                **self.context
            }
            
            # 에러 로깅
            log_error_context(exc_val, error_context)
            
            # 사용자 에러가 아닌 경우에만 관리자 알림 고려
            if not is_user_error(exc_val) and should_notify_admin(exc_val):
                from utils.logging_config import log_critical
                log_critical(f"관리자 알림 필요: {self.operation} 중 {type(exc_val).__name__} 발생")
        
        return False  # 예외를 다시 발생시킴
    
    def add_context(self, **kwargs):
        """런타임에 컨텍스트 추가"""
        self.context.update(kwargs)


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
            message=config.get_error_message('DATA_NOT_FOUND'),
            worksheet=worksheet_name,
            operation="data_access"
        )
    
    @staticmethod
    def handle_api_quota_exceeded() -> SheetAccessError:
        """API 할당량 초과"""
        return SheetAccessError(
            message="API 할당량이 초과되었습니다. 잠시 후 다시 시도해 주세요.",
            operation="api_quota"
        )


class DiceErrorHandler:
    """다이스 명령어 전용 에러 핸들러"""
    
    @staticmethod
    def handle_invalid_format(dice_expression: str) -> DiceError:
        """잘못된 다이스 형식"""
        return DiceError(
            message=config.get_error_message('DICE_FORMAT_ERROR'),
            dice_expression=dice_expression
        )
    
    @staticmethod
    def handle_count_limit_exceeded(count: int) -> DiceError:
        """다이스 개수 제한 초과"""
        return DiceError(
            message=config.get_error_message('DICE_COUNT_LIMIT'),
            dice_expression=f"{count}d*"
        )
    
    @staticmethod
    def handle_sides_limit_exceeded(sides: int) -> DiceError:
        """다이스 면수 제한 초과"""
        return DiceError(
            message=config.get_error_message('DICE_SIDES_LIMIT'),
            dice_expression=f"*d{sides}"
        )


class CardErrorHandler:
    """카드 명령어 전용 에러 핸들러"""
    
    @staticmethod
    def handle_invalid_count(count_str: str) -> CardError:
        """잘못된 카드 개수"""
        return CardError(
            message=config.get_error_message('CARD_NUMBER_ERROR')
        )
    
    @staticmethod
    def handle_count_out_of_range(count: int) -> CardError:
        """카드 개수 범위 초과"""
        return CardError(
            message=config.get_error_message('CARD_COUNT_ERROR'),
            card_count=count
        )
    
    @staticmethod
    def handle_missing_count() -> CardError:
        """카드 개수 누락"""
        return CardError(
            message=config.get_error_message('CARD_FORMAT_ERROR')
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
        from utils.logging_config import log_critical
        log_critical(
            f"처리되지 않은 예외 발생: {exc_type.__name__}: {exc_value}",
            exc_info=(exc_type, exc_value, exc_traceback)
        )
    
    sys.excepthook = handle_exception


# 유틸리티 함수들
def format_error_for_user(error: Exception, include_details: bool = False) -> str:
    """사용자에게 표시할 에러 메시지 포맷팅"""
    base_message = get_user_friendly_message(error)
    
    if include_details and config.DEBUG_MODE and isinstance(error, BotException):
        if error.error_code:
            base_message += f"\n(오류 코드: {error.error_code})"
    
    return base_message


def create_error_report(error: Exception, context: dict = None) -> dict:
    """에러 리포트 생성 (관리자용)"""
    report = {
        'timestamp': time.time(),
        'error_type': type(error).__name__,
        'error_message': str(error),
        'severity': ErrorHandler.get_error_severity(error).value,
        'is_retryable': is_retryable_error(error),
        'traceback': traceback.format_exc() if config.DEBUG_MODE else None
    }
    
    if isinstance(error, BotException):
        report['error_code'] = error.error_code
        report['error_context'] = error.context
    
    if context:
        report['additional_context'] = context
    
    return report


# 에러 통계 (선택사항)
class ErrorStats:
    """에러 통계 수집 클래스"""
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.error_counts = {}
        return cls._instance
    
    def record_error(self, error: Exception):
        """에러 발생 기록"""
        error_type = type(error).__name__
        self.error_counts[error_type] = self.error_counts.get(error_type, 0) + 1
    
    def get_stats(self) -> dict:
        """에러 통계 반환"""
        return dict(self.error_counts)
    
    def reset_stats(self):
        """통계 초기화"""
        self.error_counts.clear()


# 모듈 레벨 인스턴스
error_stats = ErrorStats()
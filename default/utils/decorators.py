"""
명령어 개발을 위한 유용한 데코레이터들
보일러플레이트 코드를 줄이고 개발 편의성을 높입니다.
"""

import os
import sys
from functools import wraps
from typing import Callable, Any

# 경로 설정
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

try:
    from utils.logging_config import logger
    from utils.error_handling import CommandError
    from commands.base_command import CommandContext, CommandResponse
except ImportError as e:
    import logging
    logger = logging.getLogger('decorators')


def handle_command_errors(func: Callable) -> Callable:
    """
    명령어 에러 자동 처리 데코레이터
    
    사용법:
        @handle_command_errors
        def execute(self, context: CommandContext) -> CommandResponse:
            # 명령어 로직
            return CommandResponse.create_success("완료")
    
    자동 처리:
        - CommandError: 사용자에게 표시
        - 일반 Exception: 로그 기록 후 일반 에러 메시지
    """
    @wraps(func)
    def wrapper(self, context: CommandContext) -> CommandResponse:
        try:
            return func(self, context)
        except CommandError as e:
            # 비즈니스 예외: 사용자에게 표시
            return CommandResponse.create_error(str(e), error=e)
        except Exception as e:
            # 시스템 예외: 로그 기록
            logger.error(f"{self.__class__.__name__} 실행 오류: {e}", exc_info=True)
            return CommandResponse.create_error(
                "처리 중 오류가 발생했습니다.",
                error=e
            )
    return wrapper


def validate_keywords(min_length: int = None, max_length: int = None) -> Callable:
    """
    키워드 개수 검증 데코레이터
    
    사용법:
        @validate_keywords(min_length=2, max_length=3)
        def execute(self, context: CommandContext) -> CommandResponse:
            # keywords는 이미 검증됨
            ...
    
    Args:
        min_length: 최소 키워드 개수
        max_length: 최대 키워드 개수
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(self, context: CommandContext) -> CommandResponse:
            keywords = context.keywords
            
            # 최소 길이 검증
            if min_length is not None and len(keywords) < min_length:
                command_name = keywords[0] if keywords else "명령어"
                return CommandResponse.create_error(
                    f"입력 형식이 올바르지 않습니다. 최소 {min_length}개의 인자가 필요합니다."
                )
            
            # 최대 길이 검증
            if max_length is not None and len(keywords) > max_length:
                command_name = keywords[0] if keywords else "명령어"
                return CommandResponse.create_error(
                    f"입력 형식이 올바르지 않습니다. 최대 {max_length}개의 인자만 허용됩니다."
                )
            
            return func(self, context)
        
        return wrapper
    return decorator


def log_execution(func: Callable) -> Callable:
    """
    명령어 실행 로깅 데코레이터
    
    사용법:
        @log_execution
        def execute(self, context: CommandContext) -> CommandResponse:
            ...
    
    자동으로 실행 로그를 기록합니다.
    """
    @wraps(func)
    def wrapper(self, context: CommandContext) -> CommandResponse:
        logger.info(f"{self.__class__.__name__} 실행: {context.keywords}")
        result = func(self, context)
        
        if result.is_successful():
            logger.info(f"{self.__class__.__name__} 성공")
        else:
            logger.warning(f"{self.__class__.__name__} 실패: {result.message}")
        
        return result
    
    return wrapper



"""
BaseCommand - 새로운 명령어 기본 클래스
기존 BaseCommand를 완전히 새로운 아키텍처로 교체

모든 명령어는 이 클래스를 상속받아 구현합니다.
"""

import os
import sys
import time
import logging
from typing import Optional, Any, Dict, List, Set
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum

# 경로 설정
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

try:
    from utils.sheets_operations import SheetsManager
    from models.command_result import CommandResult, CommandType
except ImportError:
    # 임포트 실패 시 더미 클래스
    SheetsManager = None
    CommandResult = None
    CommandType = None

logger = logging.getLogger(__name__)


class InstanceScope(Enum):
    """인스턴스 생명주기 스코프"""
    SINGLETON = "singleton"     # 하나의 인스턴스만 유지 (기본값)
    PROTOTYPE = "prototype"     # 매번 새 인스턴스 생성
    REQUEST = "request"         # 요청당 하나의 인스턴스


@dataclass
class CommandContext:
    """
    명령어 실행 컨텍스트
    
    모든 명령어 실행에 필요한 정보를 담고 있습니다.
    """
    user_id: str                        # 사용자 ID
    user_name: str = ""                 # 사용자 이름
    original_text: str = ""             # 원본 텍스트
    keywords: List[str] = None          # 명령어 키워드들
    request_id: str = None              # 요청 ID (REQUEST 스코프용)
    metadata: Dict[str, Any] = None     # 추가 메타데이터
    
    def __post_init__(self):
        if self.keywords is None:
            self.keywords = []
        if self.metadata is None:
            self.metadata = {}
        if not self.user_name:
            self.user_name = self.user_id
        
        # 실행 관련 정보
        self.execution_start_time = None
        self.additional_data = {}
    
    def get_keyword(self, index: int, default: str = "") -> str:
        """특정 위치의 키워드 반환"""
        return self.keywords[index] if index < len(self.keywords) else default
    
    def has_keyword(self, keyword: str) -> bool:
        """키워드 포함 여부 확인"""
        return keyword.lower() in [k.lower() for k in self.keywords]
    
    def add_metadata(self, key: str, value: Any) -> None:
        """메타데이터 추가"""
        self.metadata[key] = value
    
    def get_metadata(self, key: str, default: Any = None) -> Any:
        """메타데이터 조회"""
        return self.metadata.get(key, default)
    
    def add_data(self, key: str, value: Any) -> None:
        """추가 데이터 저장"""
        self.additional_data[key] = value
    
    def get_data(self, key: str, default: Any = None) -> Any:
        """추가 데이터 조회"""
        return self.additional_data.get(key, default)


@dataclass
class CommandResponse:
    """
    명령어 실행 응답
    
    명령어 실행 결과를 표준화된 형태로 반환합니다.
    """
    success: bool                       # 성공 여부
    message: str                        # 응답 메시지
    data: Any = None                    # 추가 데이터
    error: Optional[Exception] = None   # 오류 정보
    
    @classmethod
    def create_success(cls, message: str, data: Any = None) -> 'CommandResponse':
        """성공 응답 생성"""
        return cls(success=True, message=message, data=data)
    
    @classmethod
    def create_error(cls, message: str, error: Exception = None, data: Any = None) -> 'CommandResponse':
        """오류 응답 생성"""
        return cls(success=False, message=message, error=error, data=data)
    
    def is_successful(self) -> bool:
        """성공 여부 확인"""
        return self.success
    
    def get_message(self) -> str:
        """메시지 반환"""
        return self.message


class BaseCommand(ABC):
    """
    새로운 BaseCommand
    
    모든 명령어의 기본 클래스입니다. 
    기존의 복잡한 인터페이스를 간소화했습니다.
    """
    
    # 클래스 레벨 설정 (서브클래스에서 오버라이드 가능)
    instance_scope: InstanceScope = InstanceScope.SINGLETON
    
    # 명령어 메타데이터 (데코레이터가 없을 때 사용)
    command_name: str = ""
    command_aliases: List[str] = []
    command_description: str = ""
    command_category: str = "기타"
    command_examples: List[str] = []
    admin_only: bool = False
    enabled: bool = True
    priority: int = 0
    requires_sheets: bool = True
    requires_api: bool = False
    
    def __init__(
        self,
        sheets_manager: SheetsManager = None, # type: ignore
        api: Any = None,
        **kwargs
    ):
        """
        BaseCommand 초기화
        
        Args:
            sheets_manager: Google Sheets 관리자
            api: 마스토돈 API 인스턴스
            **kwargs: 추가 의존성들
        """
        # 의존성 저장
        self.sheets_manager = sheets_manager
        self.api = api
        self.dependencies = kwargs
        
        # 메타데이터 (팩토리에서 주입됨)
        self._metadata = None
        self._factory = None
        
        # 실행 통계
        self._execution_count = 0
        self._total_execution_time = 0.0
        self._last_execution_time = None
        self._last_execution_success = None
        
        # 캐시 (명령어별로 필요시 사용)
        self._cache = {}
        
        logger.debug(f"{self.__class__.__name__} 초기화 완료")
    
    def execute(self, context: CommandContext) -> CommandResponse:
        """
        명령어 실행

        새로운 명령어는 이 메서드를 직접 구현하거나,
        레거시 명령어는 _execute_command를 구현하면 됩니다.

        Args:
            context: 명령어 실행 컨텍스트

        Returns:
            CommandResponse: 실행 결과
        """
        # 레거시 패턴 지원: _execute_command가 구현되어 있으면 사용
        if hasattr(self, '_execute_command') and callable(getattr(self, '_execute_command')):
            try:
                # User 객체 생성
                from models.user import User, create_empty_user

                try:
                    user = User(id=context.user_id, name=context.user_name)
                except Exception as e:
                    logger.warning("User 객체 생성 실패 (id=%s): %s", context.user_id, e)
                    user = create_empty_user(context.user_id)

                # 레거시 _execute_command 호출
                result = self._execute_command(user, context.keywords)

                # 결과를 CommandResponse로 변환
                if isinstance(result, tuple):
                    # (message, data) 튜플 형식
                    message = result[0] if len(result) > 0 else ""
                    data = result[1] if len(result) > 1 else None
                    return CommandResponse.success(message, data)
                elif isinstance(result, str):
                    # 문자열만 반환
                    return CommandResponse.success(result)
                elif isinstance(result, CommandResponse):
                    # 이미 CommandResponse
                    return result
                else:
                    # 기타 타입
                    return CommandResponse.success(str(result))

            except Exception as e:
                logger.error(f"레거시 명령어 실행 중 오류: {e}", exc_info=True)
                return CommandResponse.error(f"명령어 실행 중 오류가 발생했습니다: {str(e)}")

        # execute가 오버라이드되지 않고 _execute_command도 없으면 에러
        raise NotImplementedError(
            f"{self.__class__.__name__}은 execute() 또는 _execute_command()를 구현해야 합니다."
        )
    
    def validate_context(self, context: CommandContext) -> Optional[str]:
        """
        컨텍스트 유효성 검증

        서브클래스에서 오버라이드하여 추가 검증 가능

        Args:
            context: 검증할 컨텍스트

        Returns:
            str: 오류 메시지 (유효하면 None)
        """
        if not context:
            return "실행 컨텍스트가 없습니다."

        if not context.user_id:
            return "사용자 ID가 없습니다."

        if not context.keywords:
            return "키워드가 없습니다."

        # 관리자 전용 명령어 권한 체크
        if self.is_admin_only():
            try:
                from config.settings import config
                admin_ids_str = config.SYSTEM_ADMIN_ID

                # 쉼표로 구분된 관리자 ID 리스트 (대소문자 무시)
                admin_ids = [aid.strip().lower() for aid in admin_ids_str.split(',') if aid.strip()]

                user_id_lower = (context.user_id or '').lower()
                if not user_id_lower or user_id_lower not in admin_ids:
                    return "⛔ 이 명령어는 시스템 관리자만 사용할 수 있습니다."
            except Exception as e:
                logger.warning(f"관리자 권한 체크 실패: {e}")
                return "관리자 권한을 확인할 수 없습니다."

        # 의존성 확인
        if self.requires_sheets and not self.sheets_manager:
            return "Google Sheets 연결이 필요합니다."

        if self.requires_api and not self.api:
            return "Mastodon API 연결이 필요합니다."

        return None
    
    def pre_execute(self, context: CommandContext) -> Optional[CommandResponse]:
        """
        실행 전 처리

        서브클래스에서 오버라이드하여 전처리 로직 추가 가능

        Args:
            context: 실행 컨텍스트

        Returns:
            CommandResponse: 오류가 있으면 응답 반환, 없으면 None
        """
        # 컨텍스트 유효성 검증
        validation_error = self.validate_context(context)
        if validation_error:
            return CommandResponse.create_error(validation_error)

        return None

    def post_execute(self, context: CommandContext, response: CommandResponse) -> CommandResponse:
        """
        실행 후 처리

        서브클래스에서 오버라이드하여 후처리 로직 추가 가능

        Args:
            context: 실행 컨텍스트
            response: 실행 결과

        Returns:
            CommandResponse: 최종 응답
        """
        return response

    def execute_with_lifecycle(self, context: CommandContext) -> CommandResponse:
        """
        라이프사이클을 포함한 명령어 실행

        이 메서드가 실제로 호출되는 진입점입니다.
        메트릭은 finally 블록에서 항상 기록되어 예외 시에도 누락되지 않습니다.

        Args:
            context: 실행 컨텍스트

        Returns:
            CommandResponse: 실행 결과
        """
        start_time = time.time()
        executed = False
        success = False
        try:
            # 전처리
            pre_result = self.pre_execute(context)
            if pre_result:
                return pre_result

            # 메인 실행
            executed = True
            response = self.execute(context)
            success = response.is_successful()

            # 후처리
            return self.post_execute(context, response)

        except Exception as e:
            logger.error(f"명령어 실행 중 오류: {self.__class__.__name__} - {e}", exc_info=True)
            return CommandResponse.create_error(
                "명령어 실행 중 오류가 발생했습니다.",
                error=e
            )
        finally:
            if executed:
                execution_time = time.time() - start_time
                self._total_execution_time += execution_time
                self._last_execution_time = execution_time
                self._execution_count += 1
                self._last_execution_success = success
    
    def get_help_text(self) -> str:
        """
        도움말 텍스트 반환
        
        Returns:
            str: 도움말 텍스트
        """
        if self._metadata:
            help_text = self._metadata.description
            if self._metadata.examples:
                help_text += f"\n예시: {', '.join(self._metadata.examples)}"
            return help_text
        
        # 메타데이터가 없으면 클래스 속성 사용
        help_text = self.command_description or "도움말이 없습니다."
        if self.command_examples:
            help_text += f"\n예시: {', '.join(self.command_examples)}"
        
        return help_text
    
    def get_command_name(self) -> str:
        """명령어 이름 반환"""
        if self._metadata:
            return self._metadata.name
        return self.command_name or self.__class__.__name__.lower().replace('command', '')
    
    def get_aliases(self) -> List[str]:
        """별칭 목록 반환"""
        if self._metadata:
            return self._metadata.aliases
        return self.command_aliases
    
    def get_category(self) -> str:
        """카테고리 반환"""
        if self._metadata:
            return self._metadata.category
        return self.command_category
    
    def is_admin_only(self) -> bool:
        """관리자 전용 명령어인지 확인"""
        if self._metadata:
            return self._metadata.admin_only
        return self.admin_only
    
    def is_enabled(self) -> bool:
        """활성화 여부 확인"""
        if self._metadata:
            return self._metadata.enabled
        return self.enabled
    
    def get_execution_stats(self) -> Dict[str, Any]:
        """실행 통계 반환"""
        avg_time = 0.0
        if self._execution_count > 0:
            avg_time = self._total_execution_time / self._execution_count
        
        return {
            'execution_count': self._execution_count,
            'total_execution_time': round(self._total_execution_time, 3),
            'average_execution_time': round(avg_time, 3),
            'last_execution_time': round(self._last_execution_time, 3) if self._last_execution_time else None,
            'last_execution_success': self._last_execution_success,
            'success_rate': self._calculate_success_rate()
        }
    
    def _calculate_success_rate(self) -> float:
        """성공률 계산 (간단한 구현)"""
        # 더 정확한 성공률을 원한다면 모든 실행 결과를 기록해야 함
        if self._last_execution_success is not None:
            return 100.0 if self._last_execution_success else 0.0
        return 0.0
    
    def reset_stats(self) -> None:
        """통계 초기화"""
        self._execution_count = 0
        self._total_execution_time = 0.0
        self._last_execution_time = None
        self._last_execution_success = None
        logger.debug(f"{self.__class__.__name__} 통계 초기화")
    
    def cache_get(self, key: str, default: Any = None) -> Any:
        """캐시에서 값 조회"""
        return self._cache.get(key, default)
    
    def cache_set(self, key: str, value: Any) -> None:
        """캐시에 값 저장"""
        self._cache[key] = value
    
    def cache_clear(self) -> None:
        """캐시 클리어"""
        self._cache.clear()
        logger.debug(f"{self.__class__.__name__} 캐시 클리어")
    
    def post_create_init(self) -> None:
        """
        생성 후 초기화 (팩토리에서 호출)
        
        서브클래스에서 추가 초기화가 필요하면 오버라이드
        """
        logger.debug(f"{self.__class__.__name__} 생성 후 초기화")
    
    def cleanup(self) -> None:
        """
        정리 작업 (팩토리에서 호출)
        
        서브클래스에서 정리가 필요하면 오버라이드
        """
        self.cache_clear()
        logger.debug(f"{self.__class__.__name__} 정리 작업 완료")
    
    def __str__(self) -> str:
        """문자열 표현"""
        command_name = self.get_command_name()
        return f"{command_name} (실행: {self._execution_count}회)"
    
    def __repr__(self) -> str:
        """개발자용 문자열 표현"""
        return f"{self.__class__.__name__}(name={self.get_command_name()}, enabled={self.is_enabled()})"


# 레거시 호환성을 위한 유틸리티 함수들

def create_command_context(
    user_id: str,
    keywords: List[str],
    user_name: str = "",
    original_text: str = "",
    request_id: str = None,
    **metadata
) -> CommandContext:
    """
    명령어 컨텍스트 생성 헬퍼
    
    Args:
        user_id: 사용자 ID
        keywords: 키워드 목록
        user_name: 사용자 이름
        original_text: 원본 텍스트
        request_id: 요청 ID
        **metadata: 추가 메타데이터
        
    Returns:
        CommandContext: 생성된 컨텍스트
    """
    return CommandContext(
        user_id=user_id,
        user_name=user_name,
        keywords=keywords,
        original_text=original_text,
        request_id=request_id,
        metadata=metadata
    )


class LegacyCommandAdapter:
    """
    기존 BaseCommand 스타일을 새 인터페이스로 변환하는 어댑터
    
    점진적 마이그레이션을 위해 사용됩니다.
    기존에 execute(user, keywords) 형태로 구현된 명령어들을 지원합니다.
    """
    
    def __init__(self, legacy_command: Any):
        """
        어댑터 초기화
        
        Args:
            legacy_command: 기존 명령어 인스턴스
        """
        self.legacy_command = legacy_command
        self._metadata = None
        self._factory = None
        
        # BaseCommand의 속성들을 위임
        self.sheets_manager = getattr(legacy_command, 'sheets_manager', None)
        self.api = getattr(legacy_command, 'api', None)
    
    def execute(self, context: CommandContext) -> CommandResponse:
        """기존 명령어를 새 인터페이스로 실행"""
        try:
            # 기존 User 객체 생성
            from models.user import User, create_empty_user
            
            try:
                user = User(id=context.user_id, name=context.user_name)
            except Exception as e:
                logger.warning("User 객체 생성 실패 (id=%s): %s", context.user_id, e)
                user = create_empty_user(context.user_id)

            # 기존 방식으로 실행
            result = self.legacy_command.execute(user, context.keywords)
            
            # CommandResult를 CommandResponse로 변환
            if hasattr(result, 'is_successful') and callable(result.is_successful):
                if result.is_successful():
                    return CommandResponse.create_success(
                        result.get_user_message(),
                        data=getattr(result, 'result_data', None)
                    )
                else:
                    return CommandResponse.create_error(
                        result.get_user_message(),
                        error=getattr(result, 'error', None)
                    )
            else:
                # 단순 문자열 결과인 경우
                return CommandResponse.create_success(str(result))
                
        except Exception as e:
            logger.error(f"레거시 명령어 실행 실패: {e}")
            return CommandResponse.create_error(
                "명령어 실행 중 오류가 발생했습니다.",
                error=e
            )
    
    def get_help_text(self) -> str:
        """도움말 텍스트 반환"""
        if hasattr(self.legacy_command, 'get_help_text'):
            return self.legacy_command.get_help_text()
        return "도움말이 없습니다."
    
    def __getattr__(self, name):
        """속성 위임"""
        return getattr(self.legacy_command, name)


def create_legacy_adapter(legacy_command: Any) -> LegacyCommandAdapter:
    """
    레거시 명령어 어댑터 생성
    
    Args:
        legacy_command: 기존 명령어 인스턴스
        
    Returns:
        LegacyCommandAdapter: 어댑터 인스턴스
    """
    return LegacyCommandAdapter(legacy_command)


def convert_response_to_command_result(
    response: CommandResponse,
    command_type: 'CommandType', # type: ignore
    user_id: str,
    user_name: str,
    original_command: str,
    execution_time: float = None
) -> 'CommandResult': # type: ignore
    """
    CommandResponse를 CommandResult로 변환 (기존 시스템 호환성)
    
    Args:
        response: 명령어 응답
        command_type: 명령어 타입
        user_id: 사용자 ID
        user_name: 사용자 이름
        original_command: 원본 명령어
        execution_time: 실행 시간
        
    Returns:
        CommandResult: 변환된 결과
    """
    if not CommandResult:
        # CommandResult를 import할 수 없는 경우 더미 객체 반환
        class DummyCommandResult:
            def __init__(self, message: str, success: bool):
                self.message = message
                self.success = success
            
            def is_successful(self):
                return self.success
            
            def get_user_message(self):
                return self.message
        
        return DummyCommandResult(response.message, response.success)
    
    if response.success:
        return CommandResult.success(
            command_type=command_type,
            user_id=user_id,
            user_name=user_name,
            original_command=original_command,
            message=response.message,
            result_data=response.data,
            execution_time=execution_time
        )
    else:
        return CommandResult.error(
            command_type=command_type,
            user_id=user_id,
            user_name=user_name,
            original_command=original_command,
            error=response.error or Exception(response.message),
            execution_time=execution_time
        )


# 전역 레지스트리 참조 (순환 임포트 방지)
class CommandRegistry:
    """명령어 레지스트리 인터페이스 (실제 구현은 registry.py에 있음)"""
    pass


# 명령어 클래스들을 위한 편의 기능들
class CommandCategory:
    """명령어 카테고리 상수"""
    DICE = "다이스"
    CARD = "카드" 
    GAME = "게임"
    INVENTORY = "인벤토리"
    ITEM = "아이템"
    HELP = "도움말"
    OBSERVE = "관찰"
    ACTION = "이상행동"
    UTILITY = "유틸리티"
    ADMIN = "관리자"
    SYSTEM = "시스템"
    OTHER = "기타"

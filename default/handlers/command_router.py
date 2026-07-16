"""
새로운 명령어 라우터 - 개선된 버전
기존 command_router.py를 새로운 아키텍처로 완전히 교체하고 피드백을 반영한 개선 버전
"""

import os
import sys
import re
import time
import uuid
from typing import List, Optional, Dict, Any, Tuple, Protocol, Union
from abc import ABC, abstractmethod
from utils.log_sanitizer import sanitize_log_input

# 경로 설정
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

try:
    from commands.registry import get_registry, CommandRegistry
    from commands.factory import get_factory, CommandFactory, create_command_context
    from commands.base_command import BaseCommand, CommandContext, CommandResponse, LegacyCommandAdapter
    from models.dynamic_command_types import get_command_type, get_type_manager
    from models.command_result import CommandResult
    from utils.logging_config import logger, LogFormatter, should_log_debug, log_command_result
    from utils.sheets_operations import SheetsManager
    from commands.default.custom_command import get_custom_command_manager, execute_custom_command, is_custom_command
    from config.settings import config
    IMPORTS_AVAILABLE = True
except ImportError as e:
    # 임포트 실패 시 폴백
    import logging
    logger = logging.getLogger('command_router')
    logger.warning(f"모듈 임포트 실패: {e}")
    
    # 더미 클래스들
    class CommandRegistry:
        pass
    
    class CommandFactory:
        pass
    
    # 커스텀 명령어 관련 폴백
    def get_custom_command_manager():
        return None
    
    def execute_custom_command(cmd, user_name=""):
        return None
    
    def is_custom_command(cmd):
        return False
    
    IMPORTS_AVAILABLE = False

# "X변경" 또는 "X 변경" 형식만 허용 (X는 1~10자의 한글/영문/공백)
_STAT_CHANGE_PATTERN = re.compile(r'^[가-힣a-zA-Z\s]{1,10}\s?변경$')

# 공통 결과 클래스들 (DRY 위반 해결)
class CommandResultProtocol(Protocol):
    """CommandResult 프로토콜 (타입 안정성 보장)"""
    
    def is_successful(self) -> bool:
        """성공 여부 반환"""
        ...
    
    def get_user_message(self) -> str:
        """사용자 메시지 반환"""
        ...


class FallbackCommandResult:
    """CommandResult를 import할 수 없을 때 사용하는 폴백 결과"""
    
    def __init__(self, success: bool, message: str, user_id: str = "", execution_time: float = 0.0):
        self.success = success
        self.message = message
        self.user_id = user_id
        self.execution_time = execution_time
        
    def is_successful(self) -> bool:
        """성공 여부 반환"""
        return self.success
        
    def get_user_message(self) -> str:
        """사용자 메시지 반환"""
        return self.message


class ErrorResult:
    """에러 결과 전용 클래스"""
    
    def __init__(self, message: str, user_id: str = ""):
        self.message = message
        self.success = False
        self.user_id = user_id
    
    def is_successful(self) -> bool:
        """항상 False 반환"""
        return False
    
    def get_user_message(self) -> str:
        """에러 메시지 반환"""
        return self.message


class ModernCommandRouter:
    """
    새로운 명령어 라우터 - 개선된 버전
    
    레지스트리와 팩토리를 사용하여 동적으로 명령어를 처리합니다.
    기존의 하드코딩된 라우팅 로직을 완전히 제거했습니다.
    """
    
    def __init__(self, sheets_manager: 'SheetsManager' = None, api=None, **additional_deps):
        """
        ModernCommandRouter 초기화

        Args:
            sheets_manager: Google Sheets 관리자
            api: 마스토돈 API 인스턴스
            **additional_deps: 추가 의존성 (예: investigation_sheets_manager)
        """
        self.sheets_manager = sheets_manager
        self.api = api
        self._additional_deps = additional_deps

        # 레지스트리와 팩토리 초기화 (import 가능한 경우만)
        if IMPORTS_AVAILABLE:
            self.registry = get_registry()
            self.factory = get_factory()
            self.type_manager = get_type_manager()

            # 의존성 설정 (추가 의존성 포함)
            self.factory.configure_dependencies(
                sheets_manager=sheets_manager,
                mastodon_api=api,
                **additional_deps,
            )
            
            # 초기화 수행
            self._initialize()
        else:
            self.registry = None
            self.factory = None
            self.type_manager = None
            logger.warning("의존성 임포트 실패 - 제한된 모드로 실행")
        
        logger.info("ModernCommandRouter 초기화 완료")
    
    def _initialize(self) -> None:
        """라우터 초기화"""
        if not IMPORTS_AVAILABLE or not self.registry:
            logger.warning("[초기화] 의존성 임포트 실패로 초기화 생략")
            return

        try:
            logger.info("[초기화] ModernCommandRouter 초기화 시작")

            # 명령어 발견
            discovered_count = self.registry.discover_commands()
            logger.info(f"[초기화] 명령어 발견 완료: {discovered_count}개")

            # CommandType 동기화
            if self.type_manager:
                self.type_manager.sync_with_registry(self.registry)
                logger.info("[초기화] CommandType 동기화 완료")

            # 싱글톤 인스턴스 미리 생성
            # 주의: sheets_manager가 없어도 일부 명령어는 생성 가능
            if self.factory:
                logger.debug(f"[초기화] 싱글톤 인스턴스 생성 시작 (sheets_manager={'있음' if self.sheets_manager else '없음'})")
                singleton_results = self.factory.create_all_singleton_instances()
                success_count = sum(singleton_results.values())
                total_count = len(singleton_results)

                if success_count < total_count:
                    failed_count = total_count - success_count
                    logger.warning(f"[초기화] 싱글톤 인스턴스 일부 생성 실패: {success_count}/{total_count} (실패={failed_count})")
                    logger.debug(f"[초기화] sheets_manager 상태: {'있음' if self.sheets_manager else '없음'}, api 상태: {'있음' if self.api else '없음'}")
                else:
                    logger.info(f"[초기화] 싱글톤 인스턴스 생성 완료: {success_count}/{total_count}")

            logger.info("[초기화] ModernCommandRouter 초기화 완료")

        except Exception as e:
            logger.error(f"[초기화] 라우터 초기화 중 예외 발생: {e}", exc_info=True)
    
    def route_command(
        self,
        user_id: str,
        keywords: List[str],
        context: Dict[str, Any] = None
    ) -> CommandResultProtocol:
        """
        명령어 라우팅 및 실행

        Args:
            user_id: 사용자 ID
            keywords: 명령어 키워드들
            context: 실행 컨텍스트 (기존 호환성)

        Returns:
            CommandResultProtocol: 명령어 실행 결과 (타입 안정성 보장)
        """
        start_time = time.time()

        try:
            # 1. 입력 검증
            if not keywords:
                logger.warning("[라우팅] 명령어가 비어있음")
                return self._create_error_result(user_id, "명령어가 없습니다.")

            # 1.5. 첫 번째 키워드 추출
            first_keyword = keywords[0].strip()
            full_command = '/'.join(keywords)

            if should_log_debug():
                logger.debug(LogFormatter.command(user_id, full_command, True, details="명령어 처리 시작"))

            # 1.6. 무시할 키워드 확인 (다른 봇과 충돌 방지)
            if self._should_ignore_command(first_keyword):
                if should_log_debug():
                    logger.debug(f"무시된 명령어: {first_keyword} (다른 봇용)")
                return self._create_silent_ignore_result(user_id)

            # 2. 의존성 확인
            if not IMPORTS_AVAILABLE or not self.factory:
                logger.error("[라우팅] 명령어 시스템 초기화 실패 - IMPORTS_AVAILABLE={}, factory={}".format(
                    IMPORTS_AVAILABLE, self.factory is not None
                ))
                return self._create_error_result(
                    user_id,
                    "명령어 시스템이 초기화되지 않았습니다. 관리자에게 문의해주세요."
                )

            # 3. 시스템 명령어 먼저 확인 (커스텀 명령어보다 우선순위 높음)
            first_keyword_lower = first_keyword.lower()

            # 스탯 변경 명령어 자동 라우팅 (예: "이성 변경", "체력변경" → "변경")
            stat_change_keyword = None
            if not is_custom_command(first_keyword):
                if _STAT_CHANGE_PATTERN.match(first_keyword.strip()):
                    stat_change_keyword = "변경"

            command_instance = self.factory.create_command_by_keyword(stat_change_keyword if stat_change_keyword else first_keyword_lower)

            if command_instance:
                # 시스템 명령어 발견
                command_class_name = command_instance.__class__.__name__

                if should_log_debug():
                    logger.debug(f"시스템 명령어 매칭: {first_keyword} → {command_class_name}")

                # 5. 실행 컨텍스트 생성
                execution_context = self._create_execution_context(
                    user_id, keywords, context
                )

                # 6. 명령어 실행
                response = self._execute_command(command_instance, execution_context)

                # 7. 응답을 CommandResult로 변환
                execution_time = time.time() - start_time
                command_result = self._convert_to_command_result(
                    response, first_keyword_lower, user_id, keywords, execution_time
                )

                # 통일된 포맷으로 로그 출력
                log_command_result(
                    user_id=user_id,
                    command=full_command,
                    success=command_result.is_successful(),
                    duration=execution_time,
                    result_length=len(command_result.get_user_message()) if command_result.get_user_message() else 0
                )
                return command_result

            # 3.5. investigate 봇은 시스템 명령어(진입/조사/장소 목록) 외 커스텀 포함 모든 명령어 조용히 무시
            if IMPORTS_AVAILABLE and getattr(config, 'BOT_TYPE', '').lower() == 'investigate':
                if should_log_debug():
                    logger.debug(f"[조사 봇] 조사 외 명령어 무시: {first_keyword}")
                return self._create_silent_ignore_result(user_id)

            # 3.6. 레지스트리에 등록된 키워드지만 봇 타입 필터로 걸러진 경우:
            #      커스텀 명령어 fallback이 시스템 명령어를 덮어쓰지 못하도록 silent-ignore.
            #      (예: 상점 봇에 [장소 목록] 멘션 → 커스텀 시트에 있다 해도 처리하지 않음)
            if self.registry and self.registry.is_keyword_registered(first_keyword):
                if should_log_debug():
                    logger.debug(f"등록된 명령어지만 현재 봇 타입에서 필터링됨 - 무시: {first_keyword}")
                return self._create_silent_ignore_result(user_id)

            # 4. 커스텀 명령어 확인
            if is_custom_command(first_keyword):
                if should_log_debug():
                    logger.debug(f"커스텀 명령어 매칭: {first_keyword}")

                try:
                    # 사용자 이름 추출 (컨텍스트에서 가져오기)
                    user_name = context.get('user_name', user_id) if context else user_id

                    # 커스텀 명령어 실행 (텍스트와 이미지 파일명 반환)
                    custom_result, image_filename = execute_custom_command(first_keyword, user_name)

                    if custom_result:
                        # 커스텀 명령어 성공
                        execution_time = time.time() - start_time
                        log_command_result(
                            user_id=user_id,
                            command=first_keyword,
                            success=True,
                            duration=execution_time,
                            result_length=len(custom_result) if custom_result else 0
                        )
                        return self._create_custom_command_result(
                            user_id, first_keyword, custom_result, execution_time, image_filename
                        )
                    else:
                        # 커스텀 명령어는 있지만 결과가 없는 경우
                        logger.warning(f"커스텀 명령어 결과 없음: {first_keyword}")
                        return self._create_error_result(
                            user_id, f"[{first_keyword}] 명령어 실행 결과가 없습니다."
                        )
                except Exception as e:
                    logger.error("커스텀 명령어 처리 오류: %s - %s", sanitize_log_input(first_keyword), e)
                    return self._create_error_result(
                        user_id, f"커스텀 명령어 처리 중 오류가 발생했습니다: {str(e)}"
                    )

            # 5. 시스템 명령어도 커스텀 명령어도 아닌 경우
            execution_time = time.time() - start_time

            log_command_result(
                user_id=user_id,
                command=full_command,
                success=False,
                duration=execution_time,
                result_length=0
            )
            return self._create_error_result(
                user_id,
                f"[{first_keyword}] 명령어를 찾을 수 없습니다.\n사용 가능한 명령어는 [도움말]을 참고해 주세요."
            )

        except Exception as e:
            execution_time = time.time() - start_time
            logger.error(LogFormatter.command(user_id, '/'.join(keywords) if keywords else "unknown", False, execution_time, f"예외: {str(e)}"), exc_info=True)
            return self._create_error_result(
                user_id, f"명령어 처리 중 오류가 발생했습니다: {str(e)}"
            )
    
    def _create_execution_context(
        self, 
        user_id: str, 
        keywords: List[str], 
        legacy_context: Dict[str, Any] = None
    ) -> 'CommandContext':
        """실행 컨텍스트 생성"""
        # 기본 정보
        user_name = user_id  # 기본값
        original_text = ""
        metadata = {}
        
        # 레거시 컨텍스트에서 정보 추출
        if legacy_context:
            original_text = legacy_context.get('original_text', '')
            user_name = legacy_context.get('user_name', user_id)
            
            # 추가 메타데이터 복사
            for key, value in legacy_context.items():
                if key not in ['original_text', 'user_name', 'user_id']:
                    metadata[key] = value
        
        # 요청 ID 생성
        request_id = str(uuid.uuid4())[:8]
        
        # import 가능한 경우 실제 컨텍스트 생성
        if IMPORTS_AVAILABLE:
            return create_command_context(
                user_id=user_id,
                keywords=keywords,
                user_name=user_name,
                original_text=original_text,
                request_id=request_id,
                **metadata
            )
        else:
            # 더미 컨텍스트
            class DummyContext:
                def __init__(self):
                    self.user_id = user_id
                    self.keywords = keywords
                    self.user_name = user_name
                    self.original_text = original_text
                    self.request_id = request_id
                    self.metadata = metadata
            
            return DummyContext()
    
    def _execute_command(
        self,
        command_instance: Any,
        context: 'CommandContext'
    ) -> 'CommandResponse':
        """명령어 실행"""
        command_class_name = command_instance.__class__.__name__

        try:
            # BaseCommand인지 확인
            if hasattr(command_instance, '__class__') and issubclass(command_instance.__class__, BaseCommand):
                # 새로운 방식: execute_with_lifecycle 사용
                logger.debug(f"[실행] BaseCommand 방식으로 실행: {command_class_name}")
                return command_instance.execute_with_lifecycle(context)

            elif hasattr(command_instance, 'execute'):
                # 레거시 어댑터 확인
                if isinstance(command_instance, LegacyCommandAdapter):
                    logger.debug(f"[실행] 레거시 어댑터로 실행: {command_class_name}")
                    return command_instance.execute(context)

                # 기존 BaseCommand 스타일 감지
                import inspect
                execute_method = getattr(command_instance, 'execute')
                sig = inspect.signature(execute_method)
                params = list(sig.parameters.keys())

                if len(params) > 2:  # self, user, keywords
                    # 레거시 방식: 어댑터 생성
                    logger.debug(f"[실행] 레거시 명령어 어댑터 생성: {command_class_name} (파라미터={len(params)}개)")
                    if IMPORTS_AVAILABLE:
                        from commands.base_command import create_legacy_adapter
                        adapter = create_legacy_adapter(command_instance)
                        return adapter.execute(context)
                    else:
                        # import 실패 시 더미 응답
                        logger.error(f"[실행] 레거시 어댑터 생성 실패: import 불가")
                        class DummyResponse:
                            def __init__(self):
                                self.success = False
                                self.message = "레거시 명령어 어댑터를 사용할 수 없습니다."
                        return DummyResponse()
                else:
                    # 새로운 방식이지만 BaseCommand를 상속받지 않은 경우
                    logger.debug(f"[실행] 직접 execute 호출: {command_class_name}")
                    return command_instance.execute(context)

            else:
                logger.error(f"[실행] execute 메서드 없음: {command_class_name}")
                class ErrorResponse:
                    def __init__(self):
                        self.success = False
                        self.message = "명령어 인스턴스에 execute 메서드가 없습니다."
                return ErrorResponse()

        except Exception as e:
            logger.error(f"[실행] 명령어 실행 중 예외 발생: {command_class_name} | 오류: {e}", exc_info=True)
            class ErrorResponse:
                def __init__(self, error: Exception):
                    self.success = False
                    self.message = "명령어 실행 중 오류가 발생했습니다."
                    self.error = error

            return ErrorResponse(e)
    
    def _convert_to_command_result(
        self,
        response: 'CommandResponse',
        command_keyword: str,
        user_id: str,
        keywords: List[str],
        execution_time: float
    ) -> CommandResultProtocol:
        """CommandResponse를 CommandResult로 변환 (타입 안정성 개선)"""
        if not IMPORTS_AVAILABLE:
            # import 실패 시 폴백 결과 반환
            return FallbackCommandResult(
                success=getattr(response, 'success', False),
                message=getattr(response, 'message', '알 수 없는 오류'),
                user_id=user_id,
                execution_time=execution_time
            )
        
        try:
            # CommandType 결정
            command_type = get_command_type(command_keyword)
            if not command_type:
                from models.dynamic_command_types import DynamicCommandType
                command_type = DynamicCommandType.UNKNOWN
            
            # 원본 명령어 문자열 생성
            original_command = f"[{'/'.join(keywords)}]"
            
            # CommandResult 생성
            from commands.base_command import convert_response_to_command_result
            return convert_response_to_command_result(
                response=response,
                command_type=command_type,
                user_id=user_id,
                user_name=user_id,  # 기본값
                original_command=original_command,
                execution_time=execution_time
            )
            
        except Exception as e:
            logger.error(f"CommandResult 변환 실패: {e}")
            # 폴백: 직접 생성
            return FallbackCommandResult(
                success=getattr(response, 'success', False),
                message=getattr(response, 'message', '알 수 없는 오류'),
                user_id=user_id,
                execution_time=execution_time
            )
    
    def _should_ignore_command(self, keyword: str) -> bool:
        """
        명령어를 무시해야 하는지 확인 (정확한 매칭)

        멀티 봇 모드에서는 BOT_IGNORED_KEYWORDS도 확인합니다.

        Args:
            keyword: 첫 번째 키워드

        Returns:
            bool: 무시해야 하면 True
        """
        if not IMPORTS_AVAILABLE:
            return False

        try:
            # config.should_ignore_keyword() 사용 (멀티 봇 지원)
            return config.should_ignore_keyword(keyword)

        except Exception as e:
            logger.warning(f"무시 키워드 확인 실패: {e}")
            return False

    def _create_silent_ignore_result(self, user_id: str) -> CommandResultProtocol:
        """조용히 무시하는 결과 생성 (다른 봇이 처리하도록) - 빈 메시지 반환"""
        # 무조건 빈 메시지 반환 (FallbackCommandResult 사용)
        return FallbackCommandResult(
            success=False,
            message="",  # 빈 메시지로 전송 자체를 막음
            user_id=user_id,
            execution_time=0.0
        )

    def _create_error_result(
        self,
        user_id: str,
        error_message: str
    ) -> CommandResultProtocol:
        """에러 결과 생성 (통합된 방식)"""
        if not IMPORTS_AVAILABLE:
            return ErrorResult(error_message, user_id)

        try:
            from models.dynamic_command_types import DynamicCommandType
            from models.command_result import CommandResult

            return CommandResult.error(
                command_type=DynamicCommandType.UNKNOWN,
                user_id=user_id,
                user_name=user_id,
                original_command="[UNKNOWN]",
                error=Exception(error_message)
            )
        except Exception:
            # 완전 폴백
            return ErrorResult(error_message, user_id)
    
    def _create_custom_command_result(
        self,
        user_id: str,
        command_keyword: str,
        custom_message: str,
        execution_time: float,
        image_filename: Optional[str] = None
    ) -> CommandResultProtocol:
        """커스텀 명령어 결과 생성 (이미지 파일명 포함)"""
        if not IMPORTS_AVAILABLE:
            result = FallbackCommandResult(
                success=True,
                message=custom_message,
                user_id=user_id,
                execution_time=execution_time
            )
            # 이미지 파일명을 속성으로 추가
            if image_filename:
                result.image_filename = image_filename
            return result

        try:
            from models.dynamic_command_types import DynamicCommandType
            from models.command_result import CommandResult

            # 메타데이터에 이미지 파일명 추가
            metadata = {}
            if image_filename:
                metadata['image_filename'] = image_filename

            return CommandResult.success(
                command_type=DynamicCommandType.CUSTOM,
                user_id=user_id,
                user_name=user_id,
                original_command=f"[{command_keyword}]",
                message=custom_message,
                execution_time=execution_time,
                **metadata
            )
        except Exception as e:
            logger.warning(f"커스텀 명령어 결과 생성 실패, 폴백 사용: {e}")
            result = FallbackCommandResult(
                success=True,
                message=custom_message,
                user_id=user_id,
                execution_time=execution_time
            )
            # 이미지 파일명을 속성으로 추가
            if image_filename:
                result.image_filename = image_filename
            return result
    
    def get_available_commands(self) -> List[Dict[str, Any]]:
        """사용 가능한 명령어 목록 반환 (커스텀 명령어 포함)"""
        commands = []
        
        # 1. 일반 명령어 추가
        if IMPORTS_AVAILABLE and self.registry:
            try:
                for name, registered_command in self.registry.get_enabled_commands().items():
                    metadata = registered_command.metadata
                    
                    commands.append({
                        'name': metadata.name,
                        'aliases': metadata.aliases,
                        'description': metadata.description,
                        'category': metadata.category,
                        'examples': metadata.examples,
                        'admin_only': metadata.admin_only,
                        'keywords': metadata.get_all_keywords()
                    })
            except Exception as e:
                logger.error(f"일반 명령어 목록 조회 실패: {e}")
        
        # 2. 커스텀 명령어 추가
        try:
            custom_manager = get_custom_command_manager()
            if custom_manager:
                custom_commands = custom_manager.get_available_commands()
                
                for custom_cmd in custom_commands:
                    commands.append({
                        'name': custom_cmd,
                        'aliases': [],
                        'description': '커스텀 명령어',
                        'category': '커스텀',
                        'examples': [f'[{custom_cmd}]'],
                        'admin_only': False,
                        'keywords': [custom_cmd]
                    })
        except Exception as e:
            logger.error(f"커스텀 명령어 목록 조회 실패: {e}")
        
        # 카테고리별로 정렬
        commands.sort(key=lambda x: (x['category'], x['name']))
        return commands
    
    def reload_all_commands(self) -> Dict[str, Any]:
        """모든 명령어 재로드"""
        if not IMPORTS_AVAILABLE:
            logger.error("[재로드] 의존성 임포트 실패로 재로드 불가")
            return {
                'success': False,
                'error': '명령어 시스템이 초기화되지 않았습니다',
                'message': '의존성 임포트 실패'
            }

        try:
            logger.info("[재로드] 명령어 재로드 시작")

            # 팩토리 인스턴스 정리
            if self.factory:
                logger.debug("[재로드] 팩토리 인스턴스 정리 중")
                self.factory.cleanup_all_instances()

            # 레지스트리 재로드
            discovered_count = 0
            if self.registry:
                logger.debug("[재로드] 레지스트리 재로드 중")
                discovered_count = self.registry.reload_commands()
                logger.info(f"[재로드] 명령어 재발견 완료: {discovered_count}개")

            # CommandType 동기화
            if self.type_manager:
                logger.debug("[재로드] CommandType 동기화 중")
                self.type_manager.sync_with_registry(self.registry)

            # 싱글톤 인스턴스 재생성
            success_count = 0
            total_count = 0
            if self.sheets_manager and self.factory:
                logger.debug("[재로드] 싱글톤 인스턴스 재생성 중")
                singleton_results = self.factory.create_all_singleton_instances()
                success_count = sum(singleton_results.values())
                total_count = len(singleton_results)
                logger.info(f"[재로드] 싱글톤 인스턴스 재생성: {success_count}/{total_count}")

            result = {
                'success': True,
                'discovered_commands': discovered_count,
                'singleton_instances': f"{success_count}/{total_count}",
                'message': f"명령어 재로드 완료: {discovered_count}개 발견"
            }

            logger.info(f"[재로드] 완료 - 발견={discovered_count}개, 싱글톤={success_count}/{total_count}")
            return result

        except Exception as e:
            logger.error(f"[재로드] 명령어 재로드 중 예외 발생: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'message': '명령어 재로드 실패'
            }
    
    def validate_all_systems(self) -> Dict[str, Any]:
        """모든 시스템 유효성 검증"""
        if not IMPORTS_AVAILABLE:
            return {
                'overall_valid': False,
                'errors': ['의존성 임포트 실패'],
                'warnings': [],
                'message': '시스템 검증 불가'
            }
        
        validation_result = {
            'overall_valid': True,
            'registry_validation': {},
            'factory_validation': {},
            'type_manager_validation': {},
            'errors': [],
            'warnings': []
        }
        
        try:
            # 레지스트리 검증
            if self.registry:
                registry_result = self.registry.validate_all_commands()
                validation_result['registry_validation'] = registry_result
                if not registry_result.get('valid', True):
                    validation_result['overall_valid'] = False
                    validation_result['errors'].extend(registry_result.get('errors', []))
            
            # 팩토리 검증
            if self.factory:
                factory_result = self.factory.validate_dependencies()
                validation_result['factory_validation'] = factory_result
                if not factory_result.get('valid', True):
                    validation_result['overall_valid'] = False
                    validation_result['errors'].extend(factory_result.get('errors', []))
            
            # 타입 매니저 검증
            if self.type_manager:
                from models.dynamic_command_types import validate_command_types
                type_result = validate_command_types()
                validation_result['type_manager_validation'] = type_result
                if not type_result.get('valid', True):
                    validation_result['overall_valid'] = False
                    validation_result['errors'].extend(type_result.get('errors', []))
                
                # 모든 경고 수집
                for result in [registry_result, factory_result, type_result]:
                    validation_result['warnings'].extend(result.get('warnings', []))
        
        except Exception as e:
            validation_result['overall_valid'] = False
            validation_result['errors'].append(f"검증 중 오류: {e}")
        
        return validation_result
    
    def health_check(self) -> Dict[str, Any]:
        """라우터 상태 확인"""
        health_status = {
            'status': 'healthy',
            'errors': [],
            'warnings': [],
            'components': {}
        }
        
        # 기본 임포트 상태 확인
        if not IMPORTS_AVAILABLE:
            health_status['status'] = 'error'
            health_status['errors'].append("의존성 임포트 실패")
            return health_status
        
        try:
            # 레지스트리 상태
            if self.registry:
                try:
                    registry_commands = len(self.registry.get_all_commands())
                    health_status['components']['registry'] = {
                        'status': 'healthy',
                        'commands_count': registry_commands
                    }
                except Exception as e:
                    health_status['errors'].append(f"레지스트리 오류: {e}")
                    health_status['components']['registry'] = {'status': 'error', 'error': str(e)}
            else:
                health_status['warnings'].append("레지스트리가 없음")
            
            # 팩토리 상태
            if self.factory:
                try:
                    factory_stats = self.factory.get_instance_statistics()
                    health_status['components']['factory'] = {
                        'status': 'healthy',
                        'instances': factory_stats.get('total_instances', 0)
                    }
                except Exception as e:
                    health_status['errors'].append(f"팩토리 오류: {e}")
                    health_status['components']['factory'] = {'status': 'error', 'error': str(e)}
            else:
                health_status['warnings'].append("팩토리가 없음")
            
            # 의존성 상태
            deps_status = 'healthy'
            if not self.sheets_manager:
                health_status['warnings'].append("Google Sheets 연결 없음")
                deps_status = 'warning'
            
            if not self.api:
                health_status['warnings'].append("Mastodon API 연결 없음")
                deps_status = 'warning'
            
            health_status['components']['dependencies'] = {'status': deps_status}
            
            # 전체 상태 결정
            if health_status['errors']:
                health_status['status'] = 'error'
            elif health_status['warnings']:
                health_status['status'] = 'warning'
        
        except Exception as e:
            health_status['status'] = 'error'
            health_status['errors'].append(f"상태 확인 중 오류: {e}")
        
        return health_status


# SimpleCommandRouter 제거 (deprecated)
# 기존 코드에서 SimpleCommandRouter를 사용하는 경우 ModernCommandRouter로 직접 교체 필요


# 전역 라우터 인스턴스 관리
_global_router: Optional[ModernCommandRouter] = None


def get_command_router() -> ModernCommandRouter:
    """
    전역 명령어 라우터 반환

    주의: initialize_command_router()를 먼저 호출해야 합니다.
    호출하지 않은 경우 의존성 없이 라우터가 생성됩니다.
    """
    global _global_router
    if _global_router is None:
        logger.warning("전역 라우터가 초기화되지 않았습니다. 의존성 없이 생성합니다.")
        logger.warning("먼저 initialize_command_router(sheets_manager, api)를 호출하세요!")
        _global_router = ModernCommandRouter()
    return _global_router


def initialize_command_router(
    sheets_manager: 'SheetsManager',
    api=None,
    **additional_deps,
) -> ModernCommandRouter:
    """
    명령어 라우터 초기화

    Args:
        sheets_manager: Google Sheets 관리자
        api: 마스토돈 API 인스턴스
        **additional_deps: 추가 의존성 (예: investigation_sheets_manager)

    Returns:
        ModernCommandRouter: 초기화된 라우터
    """
    global _global_router
    _global_router = ModernCommandRouter(sheets_manager, api, **additional_deps)
    logger.info("전역 ModernCommandRouter 초기화 완료")
    return _global_router


def route_command(
    user_id: str, 
    keywords: List[str], 
    context: Dict[str, Any] = None
) -> CommandResultProtocol:
    """
    편의 함수: 명령어 라우팅 실행
    
    Args:
        user_id: 사용자 ID
        keywords: 키워드 리스트
        context: 실행 컨텍스트
        
    Returns:
        CommandResultProtocol: 실행 결과 (타입 안정성 보장)
    """
    router = get_command_router()
    return router.route_command(user_id, keywords, context)


def parse_command_from_text(text: str) -> List[str]:
    """
    텍스트에서 명령어 키워드 추출 (첫 [] 블록을 '/' 로 분할)

    Args:
        text: 분석할 텍스트 (예: "[교류/3] @상대" 또는 "[추적/이름]")

    Returns:
        List[str]: 추출된 키워드들 (예: ['교류', '3'] 또는 ['추적', '이름'])
    """
    # BBCode 스타일 포맷팅 태그 제거 ([color:hex], [/color], [bg:hex], [/bg])
    text = re.sub(r'\[/?(color|bg)(:[0-9a-fA-F]{3,8})?\]', '', text)

    # 모든 [] 패턴 찾기
    matches = re.findall(r'\[([^\]]+)\]', text)
    if not matches:
        return []
    
    # 첫 번째 매치만 사용
    keywords_str = matches[0]

    # 일반적인 경우: / 기준으로 분할
    keywords = [keyword.strip() for keyword in keywords_str.split('/')]
    
    # 빈 키워드 제거
    keywords = [keyword for keyword in keywords if keyword]
    
    return keywords


def validate_command_format(text: str) -> Tuple[bool, str]:
    """
    명령어 형식 유효성 검사 (개선된 검증)
    
    Args:
        text: 검사할 텍스트
        
    Returns:
        Tuple[bool, str]: (유효성, 메시지)
    """
    # 기본 [] 패턴 확인
    if '[' not in text or ']' not in text:
        return False, "명령어는 [명령어] 형식으로 입력해야 합니다."
    
    # [] 위치 확인
    start_pos = text.find('[')
    end_pos = text.find(']')
    
    if start_pos >= end_pos:
        return False, "명령어 형식이 올바르지 않습니다. [명령어] 순서를 확인해주세요."
    
    # 중첩된 대괄호 확인
    bracket_content = text[start_pos:end_pos+1]
    if bracket_content.count('[') > 1 or bracket_content.count(']') > 1:
        return False, "중첩된 대괄호는 사용할 수 없습니다."
    
    # 키워드 추출 시도
    keywords = parse_command_from_text(text)
    if not keywords:
        return False, "명령어가 비어있습니다."
    
    # 키워드 길이 확인
    if any(len(keyword) > 50 for keyword in keywords):
        return False, "명령어가 너무 깁니다. (최대 50자)"
    
    return True, "올바른 명령어 형식입니다."


# 편의 함수들
def get_available_commands() -> List[Dict[str, Any]]:
    """사용 가능한 명령어 목록 반환 (편의 함수)"""
    router = get_command_router()
    return router.get_available_commands()


def reload_all_commands() -> Dict[str, Any]:
    """모든 명령어 재로드 (편의 함수)"""
    router = get_command_router()
    return router.reload_all_commands()


def validate_all_systems() -> Dict[str, Any]:
    """모든 시스템 검증 (편의 함수)"""
    router = get_command_router()
    return router.validate_all_systems()


def get_router_health() -> Dict[str, Any]:
    """라우터 상태 확인 (편의 함수)"""
    router = get_command_router()
    return router.health_check()


# 마이그레이션 가이드만 제공 (실제 테스트 코드는 별도 파일로 분리 필요)
def get_migration_guide() -> str:
    """
    라우터 마이그레이션 가이드 반환
    
    Returns:
        str: 마이그레이션 가이드 텍스트
    """
    return """
    === 라우터 마이그레이션 가이드 ===
    
    기존 사용법:
    from handlers.command_router import CommandRouter
    router = CommandRouter(sheets_manager, api)
    result = router.route_command(user_id, keywords, context)
    
    새로운 사용법:
    from handlers.command_router import initialize_command_router
    router = initialize_command_router(sheets_manager, api)  # 한 번만 호출
    result = router.route_command(user_id, keywords, context)  # 동일한 인터페이스
    
    또는 편의 함수 사용:
    from handlers.command_router import route_command
    result = route_command(user_id, keywords, context)
    
    주요 변경사항:
    1. SimpleCommandRouter 제거 (deprecated) - ModernCommandRouter 직접 사용
    2. 하드코딩된 명령어 라우팅 제거
    3. 자동 명령어 발견 시스템
    4. 동적 CommandType 관리
    5. 개선된 오류 처리 및 타입 안정성
    6. 기존 BaseCommand와 새 BaseCommand 모두 지원
    7. 통계 기능 제거 (불필요한 복잡성 제거)
    8. DRY 원칙 준수 및 코드 정리
    
    주의사항:
    - SimpleCommandRouter는 더 이상 지원되지 않습니다
    - 테스트 코드는 별도 파일(tests/test_command_router.py)로 분리하세요
    - 타입 힌트 개선으로 더 안전한 코드 작성 가능
    
    === 마이그레이션 완료 ===
    """


# 개발자를 위한 유틸리티
def show_router_info() -> None:
    """
    라우터 기본 정보 출력 (개발용)
    
    실제 테스트 코드는 tests/test_command_router.py로 분리하세요
    """
    try:
        router = get_command_router()
        
        print("=== ModernCommandRouter 정보 ===")
        print(f"임포트 상태: {'✅ 정상' if IMPORTS_AVAILABLE else '❌ 실패'}")
        
        # 상태 확인
        health = router.health_check()
        print(f"전체 상태: {health['status']}")
        
        if health['errors']:
            print("오류:")
            for error in health['errors'][:3]:  # 최대 3개만
                print(f"  - {error}")
        
        if health['warnings']:
            print("경고:")
            for warning in health['warnings'][:3]:  # 최대 3개만
                print(f"  - {warning}")
        
        # 명령어 목록
        commands = router.get_available_commands()
        if commands:
            print(f"\n등록된 명령어: {len(commands)}개")
            # 카테고리별 그룹화
            categories = {}
            for cmd in commands:
                category = cmd['category']
                if category not in categories:
                    categories[category] = []
                categories[category].append(cmd['name'])
            
            for category, cmd_names in categories.items():
                print(f"  {category}: {', '.join(cmd_names[:3])}" + 
                      (f" 외 {len(cmd_names)-3}개" if len(cmd_names) > 3 else ""))
        else:
            print("\n등록된 명령어: 없음")
        
        print("\n=== 정보 출력 완료 ===")
        
    except Exception as e:
        print(f"라우터 정보 출력 실패: {e}")


# 호환성 유지를 위한 알리아스
CommandRouter = ModernCommandRouter  # 기존 코드 호환성
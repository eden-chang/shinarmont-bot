"""
명령어 팩토리 - 개선된 버전
명령어 인스턴스 생성 및 의존성 주입을 담당
"""

import os
import sys
import logging
import time
import inspect
import threading
from typing import Dict, Any, Optional, Type, TypeVar, Union, Callable, List
from dataclasses import dataclass, field
from enum import Enum
from abc import ABC, abstractmethod

# 경로 설정
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

try:
    from commands.registry import CommandRegistry, RegisteredCommand, get_registry
    from commands.base_command import CommandContext, BaseCommand, create_command_context
    from utils.sheets_operations import SheetsManager
    IMPORTS_AVAILABLE = True
except ImportError as e:
    # 임포트 실패 시 더미 클래스
    logging.getLogger(__name__).warning(f"팩토리 의존성 임포트 실패: {e}")
    
    class CommandRegistry:
        pass
    
    class RegisteredCommand:
        pass
    
    class SheetsManager:
        pass
    
    class CommandContext:
        pass
    
    class BaseCommand:
        pass
    
    def get_registry():
        return None
    
    def create_command_context(*args, **kwargs):
        return None
    
    IMPORTS_AVAILABLE = False

logger = logging.getLogger(__name__)

# should_log_debug 헬퍼 함수 (logging_config에서 임포트 시도)
try:
    from utils.logging_config import should_log_debug
except ImportError:
    def should_log_debug():
        return False  # 임포트 실패 시 기본적으로 DEBUG 로그 비활성화

T = TypeVar('T')
CommandType = TypeVar('CommandType')


class InstanceScope(Enum):
    """인스턴스 스코프 (생명주기)"""
    SINGLETON = "singleton"     # 하나의 인스턴스 (기본값)
    PROTOTYPE = "prototype"     # 매번 새 인스턴스
    REQUEST = "request"         # 요청당 하나의 인스턴스


@dataclass
class DependencyConfig:
    """의존성 설정 (개선됨)"""
    sheets_manager: Optional[SheetsManager] = None
    mastodon_api: Optional[Any] = None
    additional_deps: Dict[str, Any] = field(default_factory=dict)
    auto_inject: bool = True  # 자동 의존성 주입 여부
    
    def has_sheets_manager(self) -> bool:
        """Sheets 관리자 존재 여부"""
        return self.sheets_manager is not None
    
    def has_mastodon_api(self) -> bool:
        """Mastodon API 존재 여부"""
        return self.mastodon_api is not None
    
    def get_dependency(self, name: str, default: Any = None) -> Any:
        """추가 의존성 조회"""
        return self.additional_deps.get(name, default)
    
    def add_dependency(self, name: str, value: Any) -> None:
        """추가 의존성 설정"""
        self.additional_deps[name] = value


@dataclass
class InstanceInfo:
    """인스턴스 정보"""
    instance: Any
    created_at: float
    scope: InstanceScope
    command_name: str
    request_id: Optional[str] = None
    access_count: int = 0
    last_access: float = field(default_factory=time.time)
    
    def mark_access(self) -> None:
        """접근 표시"""
        self.access_count += 1
        self.last_access = time.time()
    
    @property
    def age_seconds(self) -> float:
        """생성 후 경과 시간 (초)"""
        return time.time() - self.created_at
    
    @property
    def idle_seconds(self) -> float:
        """마지막 접근 후 경과 시간 (초)"""
        return time.time() - self.last_access


class InstanceCreationStrategy(ABC):
    """인스턴스 생성 전략 인터페이스"""
    
    @abstractmethod
    def can_create(self, command_class: Type, dependency_config: DependencyConfig) -> bool:
        """인스턴스 생성 가능 여부"""
        pass
    
    @abstractmethod
    def create_instance(self, command_class: Type, dependency_config: DependencyConfig) -> Any:
        """인스턴스 생성"""
        pass


class BaseCommandStrategy(InstanceCreationStrategy):
    """BaseCommand 생성 전략"""
    
    def can_create(self, command_class: Type, dependency_config: DependencyConfig) -> bool:
        """BaseCommand 상속 여부 확인"""
        if not IMPORTS_AVAILABLE:
            return False
        
        try:
            return issubclass(command_class, BaseCommand)
        except TypeError:
            return False
    
    def create_instance(self, command_class: Type, dependency_config: DependencyConfig) -> Any:
        """BaseCommand 인스턴스 생성"""
        return command_class(
            sheets_manager=dependency_config.sheets_manager,
            api=dependency_config.mastodon_api,
            **dependency_config.additional_deps
        )


class LegacyCommandStrategy(InstanceCreationStrategy):
    """레거시 명령어 생성 전략"""
    
    def can_create(self, command_class: Type, dependency_config: DependencyConfig) -> bool:
        """생성 가능 여부 (항상 가능)"""
        return True
    
    def create_instance(self, command_class: Type, dependency_config: DependencyConfig) -> Any:
        """레거시 명령어 인스턴스 생성 (여러 시그니처 지원)"""
        try:
            # 시그니처 분석
            sig = inspect.signature(command_class.__init__)
            params = list(sig.parameters.keys())[1:]  # self 제외
            
            if len(params) == 0:
                # 인수 없는 생성자
                return command_class()
            elif len(params) == 1:
                # 단일 인수 (보통 sheets_manager)
                return command_class(dependency_config.sheets_manager)
            elif len(params) == 2:
                # 두 인수 (sheets_manager, api)
                return command_class(
                    dependency_config.sheets_manager,
                    dependency_config.mastodon_api
                )
            else:
                # 여러 인수 (키워드 인수 포함)
                kwargs = {}
                for param_name in params:
                    if param_name in ['sheets_manager', 'sheet_manager']:
                        kwargs[param_name] = dependency_config.sheets_manager
                    elif param_name in ['api', 'mastodon_api']:
                        kwargs[param_name] = dependency_config.mastodon_api
                    elif param_name in dependency_config.additional_deps:
                        kwargs[param_name] = dependency_config.additional_deps[param_name]
                
                return command_class(**kwargs)
                
        except Exception as e:
            logger.debug(f"시그니처 기반 생성 실패, 기본 방식 시도: {e}")
            
            # 폴백: 기본 방식들 시도
            creation_attempts = [
                lambda: command_class(dependency_config.sheets_manager, dependency_config.mastodon_api),
                lambda: command_class(dependency_config.sheets_manager),
                lambda: command_class(),
            ]
            
            for attempt in creation_attempts:
                try:
                    return attempt()
                except TypeError:
                    continue
                except Exception:
                    continue
            
            # 최후 시도: 키워드 인수만
            try:
                return command_class(
                    sheets_manager=dependency_config.sheets_manager,
                    api=dependency_config.mastodon_api
                )
            except Exception:
                pass
            
            raise RuntimeError(f"명령어 인스턴스 생성 실패: {command_class.__name__}")


class CommandFactory:
    """
    명령어 팩토리 - 개선된 버전
    
    명령어 인스턴스를 생성하고 의존성을 주입합니다.
    다양한 생성자 패턴과 인스턴스 스코프를 지원합니다.
    """
    
    def __init__(self, registry: Optional[CommandRegistry] = None):
        """
        팩토리 초기화
        
        Args:
            registry: 명령어 레지스트리 (None이면 전역 레지스트리 사용)
        """
        self.registry = registry or get_registry()
        self.dependency_config = DependencyConfig()

        # 인스턴스 저장소
        self._singleton_instances: Dict[str, InstanceInfo] = {}
        self._request_instances: Dict[str, Dict[str, InstanceInfo]] = {}  # request_id -> command_name -> instance
        self._instance_lock = threading.Lock()

        # 생성 전략들
        self._creation_strategies: List[InstanceCreationStrategy] = [
            BaseCommandStrategy(),
            LegacyCommandStrategy()  # 폴백 전략 (마지막)
        ]
        
        # 정리 설정
        self._max_request_age = 600   # 10분
        self._max_idle_time = 300     # 5분
        self._last_cleanup = time.time()
        self._cleanup_interval = 60   # 1분
        self._max_cache_size = 1000   # 최대 캐시 크기
        
        # 통계
        self._creation_count = 0
        self._error_count = 0
        
        logger.info("CommandFactory 초기화 완료")
    
    def configure_dependencies(
        self,
        sheets_manager: Optional[SheetsManager] = None,
        mastodon_api: Optional[Any] = None,
        auto_inject: bool = True,
        **additional_deps
    ) -> None:
        """
        의존성 설정 (개선됨)
        
        Args:
            sheets_manager: Google Sheets 관리자
            mastodon_api: 마스토돈 API 인스턴스
            auto_inject: 자동 의존성 주입 여부
            **additional_deps: 추가 의존성들
        """
        self.dependency_config = DependencyConfig(
            sheets_manager=sheets_manager,
            mastodon_api=mastodon_api,
            additional_deps=additional_deps,
            auto_inject=auto_inject
        )
        
        logger.info("의존성 설정 완료")
        logger.debug("설정된 의존성: sheets_manager=%s, mastodon_api=%s, auto_inject=%s, additional=%d",
                     sheets_manager is not None, mastodon_api is not None,
                     auto_inject, len(additional_deps))
    
    def create_command_instance(
        self,
        command_name: str,
        request_id: Optional[str] = None,
        force_new: bool = False
    ) -> Optional[Any]:
        """
        명령어 인스턴스 생성 (개선됨)
        
        Args:
            command_name: 명령어 이름
            request_id: 요청 ID (REQUEST 스코프용)
            force_new: 강제로 새 인스턴스 생성
            
        Returns:
            명령어 인스턴스 또는 None
        """
        if not IMPORTS_AVAILABLE:
            logger.error("필수 의존성이 없어 인스턴스를 생성할 수 없습니다.")
            return None
        
        try:
            # 등록된 명령어 조회
            registered_command = self.registry.get_command_by_name(command_name)
            if not registered_command:
                logger.warning(f"등록되지 않은 명령어: {command_name}")
                return None
            
            # 비활성화된 명령어 체크
            if not registered_command.metadata.enabled:
                logger.info(f"비활성화된 명령어: {command_name}")
                return None
            
            # 의존성 체크
            if not self._check_dependencies(registered_command):
                logger.warning(f"의존성 요구사항 미충족: {command_name}")
                return None
            
            # 스코프에 따른 인스턴스 관리
            scope = self._get_instance_scope(registered_command)
            
            if scope == InstanceScope.SINGLETON and not force_new:
                return self._get_singleton_instance(registered_command)
            elif scope == InstanceScope.REQUEST and request_id and not force_new:
                return self._get_request_instance(registered_command, request_id)
            else:
                return self._create_new_instance(registered_command, request_id)
        
        except Exception as e:
            self._error_count += 1
            logger.error(f"명령어 인스턴스 생성 실패: {command_name} - {e}")
            return None
        finally:
            # 주기적 정리 실행
            self._periodic_cleanup()
    
    def create_command_by_keyword(
        self,
        keyword: str,
        request_id: Optional[str] = None,
        force_new: bool = False
    ) -> Optional[Any]:
        """
        키워드로 명령어 인스턴스 생성
        
        Args:
            keyword: 키워드
            request_id: 요청 ID
            force_new: 강제로 새 인스턴스 생성
            
        Returns:
            명령어 인스턴스 또는 None
        """
        if not keyword:
            return None
        
        registered_command = self.registry.get_command_by_keyword(keyword)
        if not registered_command:
            if should_log_debug():
                logger.debug(f"키워드에 해당하는 명령어 없음: {keyword}")
            return None
        
        return self.create_command_instance(
            registered_command.metadata.name,
            request_id,
            force_new
        )
    
    def _check_dependencies(self, registered_command: RegisteredCommand) -> bool:
        """의존성 요구사항 확인"""
        metadata = registered_command.metadata
        
        # Sheets 의존성 체크
        if metadata.requires_sheets and not self.dependency_config.has_sheets_manager():
            logger.debug(f"명령어 '{metadata.name}'에 Sheets 의존성 필요하지만 없음")
            return False
        
        # API 의존성 체크
        if metadata.requires_api and not self.dependency_config.has_mastodon_api():
            logger.debug(f"명령어 '{metadata.name}'에 API 의존성 필요하지만 없음")
            return False
        
        return True
    
    def _get_instance_scope(self, registered_command: RegisteredCommand) -> InstanceScope:
        """명령어의 인스턴스 스코프 결정"""
        # 클래스 속성에서 스코프 확인
        if hasattr(registered_command.command_class, 'instance_scope'):
            scope = registered_command.command_class.instance_scope
            if isinstance(scope, InstanceScope):
                return scope
        
        # 메타데이터에서 스코프 확인
        if hasattr(registered_command.metadata, 'instance_scope'):
            scope = registered_command.metadata.instance_scope
            if isinstance(scope, InstanceScope):
                return scope
        
        # 기본값
        return InstanceScope.SINGLETON
    
    def _get_singleton_instance(self, registered_command: RegisteredCommand) -> Optional[Any]:
        """싱글톤 인스턴스 반환 (thread-safe, double-checked locking)"""
        command_name = registered_command.metadata.name

        # Fast path: lock 내에서 존재 여부만 확인
        with self._instance_lock:
            if command_name in self._singleton_instances:
                instance_info = self._singleton_instances[command_name]
                instance_info.mark_access()
                return instance_info.instance

        # 인스턴스 생성은 lock 밖에서 (deadlock 방지)
        instance = self._create_new_instance(registered_command)

        # lock 재획득 후 다시 확인 (다른 스레드가 먼저 생성했을 수 있음)
        with self._instance_lock:
            if command_name in self._singleton_instances:
                instance_info = self._singleton_instances[command_name]
                instance_info.mark_access()
                # 중복 생성된 인스턴스 정리
                if instance and hasattr(instance, 'cleanup'):
                    try:
                        instance.cleanup()
                    except Exception:
                        pass
                return instance_info.instance

            if instance:
                instance_info = InstanceInfo(
                    instance=instance,
                    created_at=time.time(),
                    scope=InstanceScope.SINGLETON,
                    command_name=command_name
                )
                self._singleton_instances[command_name] = instance_info
                logger.debug(f"싱글톤 인스턴스 생성: {command_name}")

            return instance
    
    def _get_request_instance(
        self,
        registered_command: RegisteredCommand,
        request_id: str
    ) -> Optional[Any]:
        """요청별 인스턴스 반환 (thread-safe, double-checked locking)"""
        command_name = registered_command.metadata.name

        # Fast path: lock 내에서 존재 여부 확인 + eviction
        with self._instance_lock:
            if len(self._request_instances) > self._max_cache_size:
                self._force_evict_oldest()

            if (request_id in self._request_instances and
                    command_name in self._request_instances[request_id]):
                instance_info = self._request_instances[request_id][command_name]
                instance_info.mark_access()
                return instance_info.instance

        # 인스턴스 생성은 lock 밖에서 (deadlock 방지)
        instance = self._create_new_instance(registered_command, request_id)

        # lock 재획득 후 다시 확인
        with self._instance_lock:
            if request_id not in self._request_instances:
                self._request_instances[request_id] = {}

            if command_name in self._request_instances[request_id]:
                instance_info = self._request_instances[request_id][command_name]
                instance_info.mark_access()
                if instance and hasattr(instance, 'cleanup'):
                    try:
                        instance.cleanup()
                    except Exception:
                        pass
                return instance_info.instance

            if instance:
                instance_info = InstanceInfo(
                    instance=instance,
                    created_at=time.time(),
                    scope=InstanceScope.REQUEST,
                    command_name=command_name,
                    request_id=request_id
                )
                self._request_instances[request_id][command_name] = instance_info
                logger.debug(f"요청별 인스턴스 생성: {command_name} (request: {request_id})")

            return instance
    
    def _force_evict_oldest(self) -> None:
        """가장 오래된 request_id의 20%를 강제 evict (lock 내부에서 호출)"""
        evict_count = max(1, len(self._request_instances) // 5)
        # 가장 오래된 request부터 정렬
        sorted_requests = sorted(
            self._request_instances.items(),
            key=lambda item: min(
                (info.created_at for info in item[1].values()),
                default=0
            )
        )
        evicted = 0
        for request_id, instances in sorted_requests[:evict_count]:
            for instance_info in instances.values():
                if hasattr(instance_info.instance, 'cleanup'):
                    try:
                        instance_info.instance.cleanup()
                    except Exception:
                        pass
            del self._request_instances[request_id]
            evicted += 1
        logger.info(f"캐시 크기 초과로 {evicted}개 요청 강제 evict (현재: {len(self._request_instances)})")

    def _create_new_instance(
        self,
        registered_command: RegisteredCommand,
        request_id: Optional[str] = None
    ) -> Optional[Any]:
        """새 인스턴스 생성 (전략 패턴 사용)"""
        command_class = registered_command.command_class
        
        # 적절한 생성 전략 찾기
        for strategy in self._creation_strategies:
            if strategy.can_create(command_class, self.dependency_config):
                try:
                    instance = strategy.create_instance(command_class, self.dependency_config)
                    
                    # 후처리 (BaseCommand의 경우)
                    if hasattr(instance, 'post_create_init'):
                        try:
                            instance.post_create_init()
                        except Exception as e:
                            logger.warning(f"post_create_init 실행 실패: {e}")
                    
                    self._creation_count += 1
                    logger.debug(f"인스턴스 생성 성공: {command_class.__name__} (전략: {strategy.__class__.__name__})")
                    return instance
                    
                except Exception as e:
                    logger.warning(f"전략 {strategy.__class__.__name__}으로 인스턴스 생성 실패: {e}")
                    continue
        
        logger.error(f"모든 전략으로 인스턴스 생성 실패: {command_class.__name__}")
        return None
    
    def create_all_singleton_instances(self) -> Dict[str, bool]:
        """
        모든 싱글톤 스코프 명령어의 인스턴스 미리 생성
        
        Returns:
            Dict[str, bool]: 명령어별 생성 성공 여부
        """
        results = {}
        enabled_commands = self.registry.get_enabled_commands()
        
        for command_name, registered_command in enabled_commands.items():
            try:
                scope = self._get_instance_scope(registered_command)
                if scope == InstanceScope.SINGLETON:
                    instance = self.create_command_instance(command_name)
                    results[command_name] = instance is not None
                else:
                    results[command_name] = True  # 싱글톤이 아니므로 성공으로 처리
            except Exception as e:
                logger.error(f"싱글톤 인스턴스 생성 실패: {command_name} - {e}")
                results[command_name] = False
        
        success_count = sum(results.values())
        total_count = len(results)
        logger.info(f"싱글톤 인스턴스 일괄 생성 완료: {success_count}/{total_count}")
        
        return results
    
    def cleanup_request_instances(self, request_id: str) -> int:
        """
        특정 요청의 인스턴스들 정리 (thread-safe)

        Args:
            request_id: 요청 ID

        Returns:
            int: 정리된 인스턴스 수
        """
        with self._instance_lock:
            if request_id not in self._request_instances:
                return 0

            instances = self._request_instances[request_id]
            count = len(instances)

            # cleanup 메서드 호출 (있는 경우)
            for instance_info in instances.values():
                if hasattr(instance_info.instance, 'cleanup'):
                    try:
                        instance_info.instance.cleanup()
                    except Exception as e:
                        logger.warning(f"인스턴스 cleanup 실패: {e}")

            del self._request_instances[request_id]
            logger.debug(f"요청 인스턴스 정리 완료: {request_id} ({count}개)")

            return count
    
    def cleanup_all_instances(self) -> Dict[str, int]:
        """
        모든 인스턴스 정리 (thread-safe)

        Returns:
            Dict[str, int]: 정리 결과
        """
        with self._instance_lock:
            result = {
                'singleton_instances': 0,
                'request_instances': 0,
                'total_requests': len(self._request_instances)
            }

            # 싱글톤 인스턴스 정리
            for instance_info in self._singleton_instances.values():
                if hasattr(instance_info.instance, 'cleanup'):
                    try:
                        instance_info.instance.cleanup()
                    except Exception as e:
                        logger.warning(f"싱글톤 cleanup 실패: {e}")

            result['singleton_instances'] = len(self._singleton_instances)
            self._singleton_instances.clear()

            # 요청별 인스턴스 정리 (lock 내부이므로 직접 처리)
            for request_id, instances in list(self._request_instances.items()):
                for instance_info in instances.values():
                    if hasattr(instance_info.instance, 'cleanup'):
                        try:
                            instance_info.instance.cleanup()
                        except Exception as e:
                            logger.warning(f"인스턴스 cleanup 실패: {e}")
                result['request_instances'] += len(instances)

            self._request_instances.clear()

        logger.info(f"모든 인스턴스 정리 완료: {result}")
        return result
    
    def _periodic_cleanup(self) -> None:
        """주기적 정리 실행 (thread-safe)"""
        current_time = time.time()

        if current_time - self._last_cleanup < self._cleanup_interval:
            return

        self._last_cleanup = current_time

        with self._instance_lock:
            # 오래된 요청 인스턴스 정리
            expired_requests = []
            for request_id, instances in list(self._request_instances.items()):
                # 요청 내 모든 인스턴스 확인
                oldest_instance = min(instances.values(), key=lambda x: x.created_at, default=None)
                if oldest_instance and oldest_instance.age_seconds > self._max_request_age:
                    expired_requests.append(request_id)
                else:
                    # 유휴 인스턴스 확인
                    idle_instances = [
                        name for name, info in list(instances.items())
                        if info.idle_seconds > self._max_idle_time
                    ]
                    for name in idle_instances:
                        if hasattr(instances[name].instance, 'cleanup'):
                            try:
                                instances[name].instance.cleanup()
                            except Exception as e:
                                logger.debug(f"유휴 인스턴스 cleanup 실패: {e}")
                        del instances[name]

        # 만료된 요청 정리 (lock 밖에서 - cleanup_request_instances는 자체 처리)
        for request_id in expired_requests:
            self.cleanup_request_instances(request_id)

        if expired_requests:
            logger.debug(f"주기적 정리 완료: {len(expired_requests)}개 요청 정리됨")
    
    def validate_dependencies(self) -> Dict[str, Any]:
        """의존성 유효성 검증"""
        validation_result = {
            'valid': True,
            'errors': [],
            'warnings': [],
            'dependency_status': {},
            'command_compatibility': {}
        }
        
        # 의존성 상태 확인
        validation_result['dependency_status'] = {
            'sheets_manager': self.dependency_config.has_sheets_manager(),
            'mastodon_api': self.dependency_config.has_mastodon_api(),
            'additional_deps': len(self.dependency_config.additional_deps),
            'auto_inject': self.dependency_config.auto_inject
        }
        
        # 명령어별 의존성 호환성 확인
        enabled_commands = self.registry.get_enabled_commands()
        
        for command_name, registered_command in enabled_commands.items():
            try:
                compatible = self._check_dependencies(registered_command)
                validation_result['command_compatibility'][command_name] = compatible
                
                if not compatible:
                    metadata = registered_command.metadata
                    missing_deps = []
                    
                    if metadata.requires_sheets and not self.dependency_config.has_sheets_manager():
                        missing_deps.append('sheets_manager')
                    
                    if metadata.requires_api and not self.dependency_config.has_mastodon_api():
                        missing_deps.append('mastodon_api')
                    
                    if missing_deps:
                        validation_result['warnings'].append(
                            f"명령어 '{command_name}' 의존성 부족: {', '.join(missing_deps)}"
                        )
            
            except Exception as e:
                validation_result['errors'].append(f"명령어 '{command_name}' 검증 실패: {e}")
                validation_result['valid'] = False
        
        # 전체 유효성 결정
        incompatible_count = sum(1 for compatible in validation_result['command_compatibility'].values() 
                               if not compatible)
        
        if incompatible_count > 0:
            validation_result['warnings'].append(
                f"의존성 요구사항을 만족하지 않는 명령어: {incompatible_count}개"
            )
        
        return validation_result
    
    def get_instance_statistics(self) -> Dict[str, Any]:
        """인스턴스 통계 반환"""
        current_time = time.time()
        
        # 싱글톤 통계
        singleton_stats = {
            'total': len(self._singleton_instances),
            'average_age': 0.0,
            'oldest_age': 0.0,
            'total_access_count': 0
        }
        
        if self._singleton_instances:
            ages = [info.age_seconds for info in self._singleton_instances.values()]
            access_counts = [info.access_count for info in self._singleton_instances.values()]
            
            singleton_stats['average_age'] = sum(ages) / len(ages)
            singleton_stats['oldest_age'] = max(ages)
            singleton_stats['total_access_count'] = sum(access_counts)
        
        # 요청별 통계
        request_stats = {
            'total_requests': len(self._request_instances),
            'total_instances': sum(len(instances) for instances in self._request_instances.values()),
            'average_instances_per_request': 0.0
        }
        
        if self._request_instances:
            instance_counts = [len(instances) for instances in self._request_instances.values()]
            request_stats['average_instances_per_request'] = sum(instance_counts) / len(instance_counts)
        
        return {
            'creation_count': self._creation_count,
            'error_count': self._error_count,
            'success_rate': (self._creation_count / max(self._creation_count + self._error_count, 1)) * 100,
            'singleton_stats': singleton_stats,
            'request_stats': request_stats,
            'last_cleanup': self._last_cleanup,
            'cleanup_interval': self._cleanup_interval,
            'dependency_config': {
                'has_sheets': self.dependency_config.has_sheets_manager(),
                'has_api': self.dependency_config.has_mastodon_api(),
                'additional_deps': len(self.dependency_config.additional_deps),
                'auto_inject': self.dependency_config.auto_inject
            }
        }
    
    def get_instance_info(self, command_name: str, request_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """특정 인스턴스 정보 반환"""
        # 싱글톤 확인
        if command_name in self._singleton_instances:
            info = self._singleton_instances[command_name]
            return {
                'scope': 'singleton',
                'created_at': info.created_at,
                'age_seconds': info.age_seconds,
                'access_count': info.access_count,
                'last_access': info.last_access,
                'idle_seconds': info.idle_seconds,
                'class_name': info.instance.__class__.__name__
            }
        
        # 요청별 확인
        if request_id and request_id in self._request_instances:
            if command_name in self._request_instances[request_id]:
                info = self._request_instances[request_id][command_name]
                return {
                    'scope': 'request',
                    'request_id': request_id,
                    'created_at': info.created_at,
                    'age_seconds': info.age_seconds,
                    'access_count': info.access_count,
                    'last_access': info.last_access,
                    'idle_seconds': info.idle_seconds,
                    'class_name': info.instance.__class__.__name__
                }
        
        return None
    
    def health_check(self) -> Dict[str, Any]:
        """팩토리 상태 확인"""
        health_status = {
            'status': 'healthy',
            'errors': [],
            'warnings': []
        }
        
        try:
            # 기본 의존성 확인
            if not IMPORTS_AVAILABLE:
                health_status['errors'].append("필수 의존성 임포트 실패")
                health_status['status'] = 'error'
                return health_status
            
            # 레지스트리 상태 확인
            if not self.registry:
                health_status['errors'].append("명령어 레지스트리가 없음")
                health_status['status'] = 'error'
            
            # 의존성 검증
            validation = self.validate_dependencies()
            if not validation['valid']:
                health_status['errors'].extend(validation['errors'])
                health_status['status'] = 'error'
            
            if validation['warnings']:
                health_status['warnings'].extend(validation['warnings'])
                if health_status['status'] == 'healthy':
                    health_status['status'] = 'warning'
            
            # 인스턴스 상태 확인
            stats = self.get_instance_statistics()
            
            # 오류율 확인
            if stats['error_count'] > 0:
                error_rate = (stats['error_count'] / (stats['creation_count'] + stats['error_count'])) * 100
                if error_rate > 20:  # 20% 이상 오류율
                    health_status['warnings'].append(f"높은 오류율: {error_rate:.1f}%")
                    if health_status['status'] == 'healthy':
                        health_status['status'] = 'warning'
            
            # 과도한 인스턴스 확인
            total_instances = (stats['singleton_stats']['total'] + 
                             stats['request_stats']['total_instances'])
            if total_instances > 100:  # 임계값
                health_status['warnings'].append(f"과도한 인스턴스 수: {total_instances}개")
                if health_status['status'] == 'healthy':
                    health_status['status'] = 'warning'
            
            # 통계 정보 추가
            health_status['statistics'] = stats
            
        except Exception as e:
            health_status['errors'].append(f"상태 확인 중 오류: {e}")
            health_status['status'] = 'error'
        
        return health_status


# 전역 팩토리 인스턴스
_global_factory: Optional[CommandFactory] = None


def get_factory() -> CommandFactory:
    """전역 팩토리 반환"""
    global _global_factory
    if _global_factory is None:
        _global_factory = CommandFactory()
    return _global_factory


def initialize_factory(
    sheets_manager: Optional[SheetsManager] = None,
    mastodon_api: Optional[Any] = None,
    **additional_deps
) -> CommandFactory:
    """
    팩토리 초기화
    
    Args:
        sheets_manager: Google Sheets 관리자
        mastodon_api: 마스토돈 API 인스턴스
        **additional_deps: 추가 의존성들
        
    Returns:
        CommandFactory: 초기화된 팩토리
    """
    factory = get_factory()
    factory.configure_dependencies(
        sheets_manager=sheets_manager,
        mastodon_api=mastodon_api,
        **additional_deps
    )
    
    logger.info("전역 CommandFactory 초기화 완료")
    return factory


def create_command_instance(
    command_name: str,
    request_id: Optional[str] = None,
    force_new: bool = False
) -> Optional[Any]:
    """
    편의 함수: 명령어 인스턴스 생성
    
    Args:
        command_name: 명령어 이름
        request_id: 요청 ID
        force_new: 강제로 새 인스턴스 생성
        
    Returns:
        명령어 인스턴스 또는 None
    """
    factory = get_factory()
    return factory.create_command_instance(command_name, request_id, force_new)


def create_command_by_keyword(
    keyword: str,
    request_id: Optional[str] = None,
    force_new: bool = False
) -> Optional[Any]:
    """
    편의 함수: 키워드로 명령어 인스턴스 생성
    
    Args:
        keyword: 키워드
        request_id: 요청 ID
        force_new: 강제로 새 인스턴스 생성
        
    Returns:
        명령어 인스턴스 또는 None
    """
    factory = get_factory()
    return factory.create_command_by_keyword(keyword, request_id, force_new)


# BaseCommand 호환성을 위한 함수들
def create_command_context(
    user_id: str,
    keywords: List[str],
    user_name: str = "",
    original_text: str = "",
    request_id: Optional[str] = None,
    **metadata
) -> Optional[CommandContext]:
    """
    명령어 컨텍스트 생성 헬퍼 (BaseCommand용)
    
    Args:
        user_id: 사용자 ID
        keywords: 키워드 목록
        user_name: 사용자 이름
        original_text: 원본 텍스트
        request_id: 요청 ID
        **metadata: 추가 메타데이터
        
    Returns:
        CommandContext: 생성된 컨텍스트 또는 None
    """
    if not IMPORTS_AVAILABLE:
        return None
    
    try:
        from commands.base_command import create_command_context
        return create_command_context(
            user_id=user_id,
            keywords=keywords,
            user_name=user_name,
            original_text=original_text,
            request_id=request_id,
            **metadata
        )
    except Exception as e:
        logger.error(f"CommandContext 생성 실패: {e}")
        return None


# 개발자를 위한 유틸리티
def debug_factory() -> str:
    """팩토리 디버그 정보 출력 (개발용)"""
    try:
        factory = get_factory()
        stats = factory.get_instance_statistics()
        health = factory.health_check()
        
        debug_info = []
        debug_info.append("=== CommandFactory 디버그 정보 ===")
        
        # 기본 통계
        debug_info.append(f"생성된 인스턴스: {stats['creation_count']}개")
        debug_info.append(f"생성 오류: {stats['error_count']}개")
        debug_info.append(f"성공률: {stats['success_rate']:.1f}%")
        
        # 싱글톤 통계
        singleton_stats = stats['singleton_stats']
        debug_info.append(f"\n싱글톤 인스턴스:")
        debug_info.append(f"  총 개수: {singleton_stats['total']}개")
        debug_info.append(f"  평균 나이: {singleton_stats['average_age']:.1f}초")
        debug_info.append(f"  총 접근 수: {singleton_stats['total_access_count']}회")
        
        # 요청별 통계
        request_stats = stats['request_stats']
        debug_info.append(f"\n요청별 인스턴스:")
        debug_info.append(f"  총 요청: {request_stats['total_requests']}개")
        debug_info.append(f"  총 인스턴스: {request_stats['total_instances']}개")
        debug_info.append(f"  평균 인스턴스/요청: {request_stats['average_instances_per_request']:.1f}개")
        
        # 상태 확인
        debug_info.append(f"\n전체 상태: {health['status']}")
        
        # 의존성 상태
        dep_config = stats['dependency_config']
        debug_info.append(f"\n의존성 상태:")
        debug_info.append(f"  Sheets Manager: {'✅' if dep_config['has_sheets'] else '❌'}")
        debug_info.append(f"  Mastodon API: {'✅' if dep_config['has_api'] else '❌'}")
        debug_info.append(f"  추가 의존성: {dep_config['additional_deps']}개")
        debug_info.append(f"  자동 주입: {'✅' if dep_config['auto_inject'] else '❌'}")
        
        # 주요 경고/오류
        if health['warnings']:
            debug_info.append(f"\n경고:")
            for warning in health['warnings'][:3]:
                debug_info.append(f"  - {warning}")
        
        if health['errors']:
            debug_info.append(f"\n오류:")
            for error in health['errors'][:3]:
                debug_info.append(f"  - {error}")
        
        debug_info.append("=== 디버그 정보 완료 ===")
        return "\n".join(debug_info)
        
    except Exception as e:
        return f"디버그 정보 생성 실패: {e}"


# 마이그레이션 가이드
def get_factory_migration_guide() -> str:
    """
    팩토리 마이그레이션 가이드 반환
    
    Returns:
        str: 마이그레이션 가이드 텍스트
    """
    return """
    === CommandFactory 마이그레이션 가이드 ===
    
    주요 개선사항:
    1. 전략 패턴 기반 인스턴스 생성 (BaseCommand vs Legacy)
    2. 다양한 생성자 시그니처 자동 지원
    3. 인스턴스 생명주기 관리 (싱글톤, 프로토타입, 요청별)
    4. 자동 의존성 검증 및 주입
    5. 메모리 누수 방지를 위한 자동 정리
    6. 상세한 인스턴스 통계 및 상태 모니터링
    
    기존 사용법:
    factory = CommandFactory(registry)
    factory.configure_dependencies(sheets_manager, api)
    instance = factory.create_command_instance("dice")
    
    새로운 기능:
    # 전역 팩토리 사용
    factory = get_factory()
    initialize_factory(sheets_manager, api)
    
    # 키워드 기반 생성
    instance = create_command_by_keyword("dice", request_id="req_123")
    
    # 강제 새 인스턴스
    instance = create_command_instance("dice", force_new=True)
    
    # 의존성 검증
    validation = factory.validate_dependencies()
    if not validation['valid']:
        print("의존성 문제:", validation['errors'])
    
    # 인스턴스 정보 조회
    info = factory.get_instance_info("dice")
    print(f"접근 횟수: {info['access_count']}")
    
    # 상태 확인
    health = factory.health_check()
    print(f"팩토리 상태: {health['status']}")
    
    # 정리 작업
    factory.cleanup_request_instances("req_123")
    factory.cleanup_all_instances()
    
    인스턴스 스코프 설정:
    class MyCommand(BaseCommand):
        instance_scope = InstanceScope.PROTOTYPE  # 매번 새 인스턴스
        
        def execute(self, context):
            return CommandResponse.create_success("결과")
    
    주요 변경사항:
    - 모든 생성자 패턴 자동 지원
    - 메모리 누수 방지 자동 정리
    - 상세한 오류 진단 및 통계
    - BaseCommand와 레거시 명령어 모두 지원
    
    === 마이그레이션 완료 ===
    """


# 모듈이 직접 실행될 때 테스트 (개선됨)
if __name__ == "__main__":
    # 로깅 설정
    logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')
    
    print("=== CommandFactory 테스트 (개선 버전) ===")
    
    # 팩토리 초기화
    factory = get_factory()
    print(f"\n팩토리 초기화: {'✅ 성공' if factory else '❌ 실패'}")
    
    # 의존성 상태 (초기)
    print(f"\n초기 의존성 상태:")
    validation = factory.validate_dependencies()
    print(f"  유효성: {'✅ 통과' if validation['valid'] else '❌ 실패'}")
    print(f"  경고: {len(validation['warnings'])}개")
    
    # 가상 의존성 설정 (테스트용)
    print(f"\n의존성 설정 (테스트용)...")
    factory.configure_dependencies(
        sheets_manager=None,  # 실제 환경에서는 SheetsManager 인스턴스
        mastodon_api=None,    # 실제 환경에서는 Mastodon API 인스턴스
        test_mode=True
    )
    
    # 의존성 재검증
    validation = factory.validate_dependencies()
    print(f"설정 후 유효성: {'✅ 통과' if validation['valid'] else '❌ 실패'}")
    
    # 인스턴스 생성 테스트 (가상)
    print(f"\n인스턴스 생성 테스트:")
    print("  실제 명령어가 등록되어 있어야 테스트 가능")
    print("  가상 테스트: create_command_instance('dice')")
    
    # 통계 확인
    print(f"\n팩토리 통계:")
    stats = factory.get_instance_statistics()
    print(f"  생성된 인스턴스: {stats['creation_count']}개")
    print(f"  생성 오류: {stats['error_count']}개")
    print(f"  성공률: {stats['success_rate']:.1f}%")
    print(f"  싱글톤: {stats['singleton_stats']['total']}개")
    print(f"  요청별: {stats['request_stats']['total_instances']}개")
    
    # 상태 확인
    print(f"\n상태 확인:")
    health = factory.health_check()
    print(f"  전체 상태: {health['status']}")
    if health['warnings']:
        print(f"  경고: {health['warnings'][0]}")
    if health['errors']:
        print(f"  오류: {health['errors'][0]}")
    
    # 디버그 정보
    print(f"\n" + debug_factory())
    
    print(f"\n=== 테스트 완료 ===")
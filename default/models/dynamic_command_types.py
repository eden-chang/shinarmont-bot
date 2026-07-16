"""
동적 CommandType 관리 - 개선된 버전
명령어 등록에 따라 자동으로 CommandType을 생성하고 관리
"""

import os
import sys
import logging
from typing import Dict, Set, Type, Any, Optional, List, Union
from enum import Enum, EnumMeta
from dataclasses import dataclass
from collections import defaultdict

# 경로 설정
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

logger = logging.getLogger(__name__)


class DynamicEnumMeta(EnumMeta):
    """동적 Enum 메타클래스 - 개선된 버전"""
    
    def __call__(cls, value):
        """Enum 값 호출 시 동적 생성 지원 (안전한 예외 처리)"""
        try:
            return super().__call__(value)
        except ValueError as ve:
            # 존재하지 않는 값이면 동적으로 생성 시도
            if hasattr(cls, '_create_dynamic_member'):
                try:
                    return cls._create_dynamic_member(value)
                except Exception as e:
                    logger.warning(f"동적 멤버 생성 실패 ({value}): {e}")
                    # 원본 ValueError를 다시 발생시킴
                    raise ve
            raise ve


class DynamicCommandType(Enum, metaclass=DynamicEnumMeta):
    """
    동적 CommandType Enum - 개선된 버전

    명령어 등록에 따라 자동으로 타입이 추가됩니다.
    Python 3.11+ 호환성 및 안전성 개선.
    """

    # Enum 멤버로 인식되지 않도록 _ignore_ 사용
    _ignore_ = ['_dynamic_members', '_creation_count']

    # 기본 타입들 (기존 호환성)
    DICE = "dice"
    CARD = "card"
    FORTUNE = "fortune"
    CUSTOM = "custom"
    HELP = "help"
    FAVOR = "favor"
    GOBAL = "gobal"
    JABEK = "jabek"
    UNKNOWN = "unknown"
    
    def __new__(cls, value):
        """새 인스턴스 생성"""
        obj = object.__new__(cls)
        obj._value_ = value
        return obj
    
    @classmethod
    def _create_dynamic_member(cls, value: str) -> 'DynamicCommandType':
        """
        동적으로 새 멤버 생성 (Python 3.11+ 호환성 개선)
        
        Args:
            value: 생성할 값
            
        Returns:
            DynamicCommandType: 생성된 동적 멤버
            
        Raises:
            ValueError: 잘못된 값이거나 생성 실패 시
        """
        if not value or not isinstance(value, str):
            raise ValueError(f"유효하지 않은 CommandType 값: {value}")
        
        value_lower = value.lower()
        
        # 이미 존재하는 동적 멤버 확인
        if value_lower in cls._dynamic_members:
            return cls._dynamic_members[value_lower]
        
        # 기본 멤버와 중복 확인
        for member in cls:
            if member.value == value_lower:
                return member
        
        try:
            # 새 멤버 생성 (Enum에 직접 추가하지 않고 별도 관리)
            new_member = object.__new__(cls)
            new_member._name_ = value.upper()
            new_member._value_ = value_lower
            
            # 동적 멤버 딕셔너리에만 추가 (Enum 오염 방지)
            cls._dynamic_members[value_lower] = new_member
            cls._creation_count += 1
            
            logger.debug(f"동적 CommandType 생성: {value.upper()} = {value_lower}")
            return new_member
            
        except Exception as e:
            logger.error(f"동적 멤버 생성 실패 ({value}): {e}")
            raise ValueError(f"CommandType '{value}' 생성 실패: {e}")
    
    @classmethod
    def add_command_type(cls, name: str) -> 'DynamicCommandType':
        """
        새 명령어 타입 추가 (중복 확인 최적화)
        
        Args:
            name: 명령어 이름
            
        Returns:
            DynamicCommandType: 생성되거나 기존 타입
        """
        if not name or not isinstance(name, str):
            return cls.UNKNOWN
        
        name_lower = name.lower()
        
        # 기존 멤버에서 먼저 확인 (빠른 조회)
        for member in cls:
            if member.value == name_lower:
                return member
        
        # 동적 멤버에서 확인
        if name_lower in cls._dynamic_members:
            return cls._dynamic_members[name_lower]
        
        # 새로 생성
        return cls._create_dynamic_member(name_lower)
    
    @classmethod
    def get_all_types(cls) -> Dict[str, 'DynamicCommandType']:
        """모든 타입 반환 (정적 + 동적)"""
        result = {}
        
        # 기본 멤버들
        for member in cls:
            result[member.value] = member
        
        # 동적 멤버들 (기본 멤버와 중복되지 않는 것만)
        for value, member in cls._dynamic_members.items():
            if value not in result:
                result[value] = member
        
        return result
    
    @classmethod
    def exists(cls, name: str) -> bool:
        """
        타입이 존재하는지 확인 (효율적인 검색)
        
        Args:
            name: 확인할 이름
            
        Returns:
            bool: 존재 여부
        """
        if not name or not isinstance(name, str):
            return False
        
        name_lower = name.lower()
        
        # 기본 멤버 확인
        for member in cls:
            if member.value == name_lower:
                return True
        
        # 동적 멤버 확인
        return name_lower in cls._dynamic_members
    
    @classmethod
    def remove_dynamic_type(cls, name: str) -> bool:
        """
        동적 타입 제거 (안전한 제거)
        
        Args:
            name: 제거할 이름
            
        Returns:
            bool: 제거 성공 여부
        """
        if not name or not isinstance(name, str):
            return False
        
        name_lower = name.lower()
        
        # 기본 타입은 제거할 수 없음
        for member in cls:
            if member.value == name_lower:
                logger.warning(f"기본 타입은 제거할 수 없습니다: {name}")
                return False
        
        if name_lower in cls._dynamic_members:
            try:
                # 동적 멤버에서 제거
                del cls._dynamic_members[name_lower]
                logger.debug(f"동적 CommandType 제거: {name}")
                return True
            except KeyError:
                logger.warning(f"이미 제거된 동적 타입: {name}")
                return False
        
        return False
    
    @classmethod
    def get_dynamic_types(cls) -> Dict[str, 'DynamicCommandType']:
        """동적 타입들만 반환"""
        return cls._dynamic_members.copy()
    
    @classmethod
    def get_static_types(cls) -> Dict[str, 'DynamicCommandType']:
        """정적(기본) 타입들만 반환"""
        result = {}
        for member in cls:
            result[member.value] = member
        return result
    
    @classmethod
    def get_creation_stats(cls) -> Dict[str, int]:
        """생성 통계 반환"""
        return {
            'static_count': len(list(cls)),
            'dynamic_count': len(cls._dynamic_members),
            'total_created': cls._creation_count,
            'total_available': len(list(cls)) + len(cls._dynamic_members)
        }


# Enum 클래스 정의 후 동적 멤버 관리 변수 초기화
# _ignore_로 Enum 멤버화를 방지했으므로 여기서 안전하게 추가 가능
DynamicCommandType._dynamic_members = {}
DynamicCommandType._creation_count = 0


class CommandTypeManager:
    """
    CommandType 관리자 - 개선된 버전
    
    명령어 레지스트리와 연동하여 CommandType을 자동 관리합니다.
    효율적인 매핑 관리 및 예외 처리 개선.
    """
    
    def __init__(self):
        """매니저 초기화"""
        self._command_type_mapping: Dict[str, DynamicCommandType] = {}
        self._reverse_mapping: Dict[DynamicCommandType, Set[str]] = defaultdict(set)
        self._initialized = False
        self._sync_count = 0
        
        logger.info("CommandTypeManager 초기화 완료")
    
    def register_command_types(self, command_names: Set[str]) -> Dict[str, bool]:
        """
        명령어 이름들로부터 CommandType 등록 (결과 반환 개선)
        
        Args:
            command_names: 명령어 이름 집합
            
        Returns:
            Dict[str, bool]: 명령어별 등록 성공 여부
        """
        if not command_names:
            return {}
            
        logger.info(f"CommandType 등록 시작: {len(command_names)}개")
        
        results = {}
        for command_name in command_names:
            try:
                results[command_name] = self._register_single_command_type(command_name)
            except (ValueError, TypeError) as e:
                logger.warning(f"명령어 '{command_name}' 등록 실패: {e}")
                results[command_name] = False
            except Exception as e:
                logger.error(f"명령어 '{command_name}' 등록 중 예상치 못한 오류: {e}")
                results[command_name] = False
        
        success_count = sum(results.values())
        logger.info(f"CommandType 등록 완료: {success_count}/{len(command_names)}개")
        return results
    
    def _register_single_command_type(self, command_name: str) -> bool:
        """
        단일 명령어 타입 등록 (효율성 개선)
        
        Args:
            command_name: 명령어 이름
            
        Returns:
            bool: 등록 성공 여부
            
        Raises:
            ValueError: 잘못된 명령어 이름
            TypeError: 타입 오류
        """
        if not command_name or not isinstance(command_name, str):
            raise ValueError(f"유효하지 않은 명령어 이름: {command_name}")
            
        command_name = command_name.strip().lower()
        if not command_name:
            raise ValueError("빈 명령어 이름")
        
        # 이미 매핑되어 있으면 스킵
        if command_name in self._command_type_mapping:
            return False
        
        # 타입 존재 여부 확인 후 생성 (중복 확인 최적화)
        command_type = None
        
        if DynamicCommandType.exists(command_name):
            # 기존 타입 사용 - get_command_type 메서드 사용
            try:
                command_type = DynamicCommandType(command_name)
            except ValueError:
                # 동적 멤버에서 찾기
                all_types = DynamicCommandType.get_all_types()
                for type_name, cmd_type in all_types.items():
                    if type_name == command_name and hasattr(cmd_type, 'value'):
                        command_type = cmd_type
                        break
        
        if command_type is None:
            # 새 타입 생성
            try:
                command_type = DynamicCommandType.add_command_type(command_name)
            except ValueError as e:
                logger.error(f"CommandType 생성 실패 ({command_name}): {e}")
                raise
        
        # 매핑 추가
        self._command_type_mapping[command_name] = command_type
        self._reverse_mapping[command_type].add(command_name)
        
        logger.debug(f"CommandType 매핑: {command_name} -> {command_type.value}")
        return True
    
    def get_command_type(self, command_name: str) -> Optional[DynamicCommandType]:
        """
        명령어 이름으로 CommandType 반환
        
        Args:
            command_name: 명령어 이름
            
        Returns:
            DynamicCommandType: 해당 타입 또는 None
        """
        if not command_name or not isinstance(command_name, str):
            return None
            
        return self._command_type_mapping.get(command_name.lower())
    
    def get_command_names(self, command_type: DynamicCommandType) -> Set[str]:
        """
        CommandType으로 명령어 이름들 반환
        
        Args:
            command_type: 명령어 타입
            
        Returns:
            Set[str]: 명령어 이름 집합
        """
        if not command_type:
            return set()
        return self._reverse_mapping.get(command_type, set()).copy()
    
    def unregister_command_type(self, command_name: str) -> bool:
        """
        명령어 타입 등록 해제 (안전한 제거)
        
        Args:
            command_name: 명령어 이름
            
        Returns:
            bool: 성공 여부
        """
        if not command_name or not isinstance(command_name, str):
            return False
            
        command_name = command_name.lower()
        
        if command_name not in self._command_type_mapping:
            return False
        
        try:
            command_type = self._command_type_mapping[command_name]
            
            # 매핑에서 제거
            del self._command_type_mapping[command_name]
            
            # 역매핑에서 제거
            if command_type in self._reverse_mapping:
                self._reverse_mapping[command_type].discard(command_name)
                
                # 더 이상 참조하는 명령어가 없으면 동적 타입 제거 고려
                if not self._reverse_mapping[command_type]:
                    del self._reverse_mapping[command_type]
                    # 동적 타입이면 제거
                    if command_type.value in DynamicCommandType._dynamic_members:
                        DynamicCommandType.remove_dynamic_type(command_name)
            
            logger.debug(f"CommandType 등록 해제: {command_name}")
            return True
            
        except KeyError as e:
            logger.warning(f"매핑 제거 중 키 오류 ({command_name}): {e}")
            return False
        except Exception as e:
            logger.error(f"등록 해제 중 오류 ({command_name}): {e}")
            return False
    
    def sync_with_registry(self, registry) -> Dict[str, Any]:
        """
        명령어 레지스트리와 동기화 (결과 반환 개선)
        
        Args:
            registry: CommandRegistry 인스턴스
            
        Returns:
            Dict[str, Any]: 동기화 결과
        """
        sync_result = {
            'success': False,
            'registry_commands': 0,
            'new_commands': [],
            'removed_commands': [],
            'errors': []
        }
        
        try:
            # 레지스트리에서 명령어 이름 집합 가져오기
            registry_commands = set()
            
            if hasattr(registry, 'get_all_command_names'):
                registry_commands = set(registry.get_all_command_names())
            elif hasattr(registry, 'get_command_types'):
                registry_commands = set(registry.get_command_types())
            elif hasattr(registry, 'get_all_commands'):
                # 명령어 객체에서 이름 추출 시도
                try:
                    commands = registry.get_all_commands()
                    registry_commands = set(commands.keys()) if isinstance(commands, dict) else set()
                except Exception as e:
                    sync_result['errors'].append(f"명령어 추출 실패: {e}")
            else:
                sync_result['errors'].append("레지스트리에서 명령어 목록을 가져올 수 없습니다.")
                return sync_result
            
            sync_result['registry_commands'] = len(registry_commands)
            
            if not registry_commands:
                logger.warning("레지스트리에서 명령어를 찾을 수 없습니다.")
                return sync_result
            
            # 현재 등록된 명령어들과 비교
            current_commands = set(self._command_type_mapping.keys())
            
            # 새로 추가된 명령어들 등록
            new_commands = registry_commands - current_commands
            if new_commands:
                logger.info(f"새 명령어 타입 등록: {new_commands}")
                register_results = self.register_command_types(new_commands)
                sync_result['new_commands'] = [
                    cmd for cmd, success in register_results.items() if success
                ]
            
            # 제거된 명령어들 정리
            removed_commands = current_commands - registry_commands
            removed_list = []
            for command_name in removed_commands:
                if self.unregister_command_type(command_name):
                    removed_list.append(command_name)
                    logger.info(f"명령어 타입 제거: {command_name}")
            sync_result['removed_commands'] = removed_list
            
            self._initialized = True
            self._sync_count += 1
            sync_result['success'] = True
            
        except AttributeError as e:
            sync_result['errors'].append(f"레지스트리 속성 오류: {e}")
            logger.error(f"레지스트리 동기화 실패 (속성 오류): {e}")
        except Exception as e:
            sync_result['errors'].append(f"동기화 중 오류: {e}")
            logger.error(f"레지스트리 동기화 실패: {e}")
        
        return sync_result
    
    def get_statistics(self) -> Dict[str, Any]:
        """통계 정보 반환 (개선됨)"""
        dynamic_types = [
            cmd_type for cmd_type in self._reverse_mapping.keys()
            if cmd_type.value in DynamicCommandType._dynamic_members
        ]
        
        static_types = [
            cmd_type for cmd_type in self._reverse_mapping.keys()
            if cmd_type.value not in DynamicCommandType._dynamic_members
        ]
        
        # DynamicCommandType 생성 통계 추가
        creation_stats = DynamicCommandType.get_creation_stats()
        
        return {
            'total_mapped_commands': len(self._command_type_mapping),
            'total_types': len(self._reverse_mapping),
            'dynamic_types': len(dynamic_types),
            'static_types': len(static_types),
            'initialized': self._initialized,
            'sync_count': self._sync_count,
            'orphaned_dynamic_types': len(DynamicCommandType._dynamic_members) - len(dynamic_types),
            'creation_stats': creation_stats
        }
    
    def get_all_mappings(self) -> Dict[str, str]:
        """모든 매핑 정보 반환 (디버그용)"""
        return {
            name: cmd_type.value 
            for name, cmd_type in self._command_type_mapping.items()
        }
    
    def clear_all_dynamic_types(self) -> Dict[str, int]:
        """
        모든 동적 타입 클리어 (결과 상세화)
        
        Returns:
            Dict[str, int]: 클리어 결과
        """
        # 동적 타입들만 찾아서 제거
        dynamic_commands = []
        for command_name, command_type in self._command_type_mapping.items():
            if command_type.value in DynamicCommandType._dynamic_members:
                dynamic_commands.append(command_name)
        
        removed_count = 0
        failed_count = 0
        
        for command_name in dynamic_commands:
            try:
                if self.unregister_command_type(command_name):
                    removed_count += 1
                else:
                    failed_count += 1
            except Exception as e:
                logger.error(f"동적 타입 제거 실패 ({command_name}): {e}")
                failed_count += 1
        
        logger.info(f"동적 CommandType {removed_count}개 클리어 완료 (실패: {failed_count}개)")
        return {
            'total_found': len(dynamic_commands),
            'removed': removed_count,
            'failed': failed_count
        }
    
    def get_type_usage(self) -> Dict[str, int]:
        """각 타입별 사용 빈도 반환"""
        usage = {}
        for cmd_type, commands in self._reverse_mapping.items():
            usage[cmd_type.value] = len(commands)
        return usage
    
    def is_initialized(self) -> bool:
        """초기화 완료 여부 반환"""
        return self._initialized
    
    def get_sync_count(self) -> int:
        """동기화 횟수 반환"""
        return self._sync_count


# 전역 매니저 인스턴스
_global_type_manager: Optional[CommandTypeManager] = None


def get_type_manager() -> CommandTypeManager:
    """전역 타입 매니저 반환"""
    global _global_type_manager
    if _global_type_manager is None:
        _global_type_manager = CommandTypeManager()
    return _global_type_manager


def get_command_type(command_name: str) -> DynamicCommandType:
    """
    명령어 이름으로 CommandType 반환 (편의 함수, 안전성 개선)
    
    Args:
        command_name: 명령어 이름
        
    Returns:
        DynamicCommandType: 해당 타입 또는 UNKNOWN
    """
    if not command_name or not isinstance(command_name, str):
        return DynamicCommandType.UNKNOWN
        
    try:
        manager = get_type_manager()
        cmd_type = manager.get_command_type(command_name)
        return cmd_type if cmd_type else DynamicCommandType.UNKNOWN
    except Exception as e:
        logger.warning(f"CommandType 조회 실패 ({command_name}): {e}")
        return DynamicCommandType.UNKNOWN


def register_command_types_from_registry(registry) -> Dict[str, Any]:
    """
    레지스트리에서 CommandType들을 등록 (편의 함수, 결과 반환)
    
    Args:
        registry: CommandRegistry 인스턴스
        
    Returns:
        Dict[str, Any]: 동기화 결과
    """
    try:
        manager = get_type_manager()
        return manager.sync_with_registry(registry)
    except Exception as e:
        logger.error(f"레지스트리 동기화 실패: {e}")
        return {
            'success': False,
            'error': str(e),
            'registry_commands': 0,
            'new_commands': [],
            'removed_commands': []
        }


def add_command_type(name: str) -> DynamicCommandType:
    """
    새 CommandType 추가 (편의 함수, 안전성 개선)
    
    Args:
        name: 명령어 이름
        
    Returns:
        DynamicCommandType: 생성된 타입
    """
    if not name or not isinstance(name, str):
        logger.warning(f"유효하지 않은 CommandType 이름: {name}")
        return DynamicCommandType.UNKNOWN
        
    try:
        manager = get_type_manager()
        manager._register_single_command_type(name)
        return DynamicCommandType.add_command_type(name)
    except (ValueError, TypeError) as e:
        logger.error(f"CommandType 추가 실패 ({name}): {e}")
        return DynamicCommandType.UNKNOWN
    except Exception as e:
        logger.error(f"CommandType 추가 중 예상치 못한 오류 ({name}): {e}")
        return DynamicCommandType.UNKNOWN


def remove_command_type(name: str) -> bool:
    """
    CommandType 제거 (편의 함수)
    
    Args:
        name: 명령어 이름
        
    Returns:
        bool: 성공 여부
    """
    if not name or not isinstance(name, str):
        return False
    
    try:
        manager = get_type_manager()
        return manager.unregister_command_type(name)
    except Exception as e:
        logger.error(f"CommandType 제거 실패 ({name}): {e}")
        return False


# 기존 코드와의 호환성을 위한 별칭
CommandType = DynamicCommandType


@dataclass
class CommandTypeInfo:
    """CommandType 정보 (개선됨)"""
    name: str
    value: str
    is_dynamic: bool
    command_count: int
    commands: List[str]
    created_at: Optional[str] = None  # 생성 시점 (동적 타입용)
    
    @classmethod
    def from_command_type(cls, cmd_type: DynamicCommandType, manager: CommandTypeManager) -> 'CommandTypeInfo':
        """CommandType에서 정보 생성"""
        command_names = manager.get_command_names(cmd_type)
        is_dynamic = cmd_type.value in DynamicCommandType._dynamic_members
        
        return cls(
            name=cmd_type.name,
            value=cmd_type.value,
            is_dynamic=is_dynamic,
            command_count=len(command_names),
            commands=sorted(list(command_names))
        )


def get_all_command_type_info() -> List[CommandTypeInfo]:
    """
    모든 CommandType 정보 반환
    
    Returns:
        List[CommandTypeInfo]: CommandType 정보 리스트
    """
    try:
        manager = get_type_manager()
        all_types = DynamicCommandType.get_all_types()
        
        result = []
        for cmd_type in all_types.values():
            # 실제로 매핑된 명령어가 있는 타입만 포함
            if manager.get_command_names(cmd_type):
                info = CommandTypeInfo.from_command_type(cmd_type, manager)
                result.append(info)
        
        # 이름순 정렬
        result.sort(key=lambda x: x.name)
        return result
        
    except Exception as e:
        logger.error(f"CommandType 정보 조회 실패: {e}")
        return []


def validate_command_types() -> Dict[str, Any]:
    """
    CommandType 유효성 검증 (예외 처리 개선)
    
    Returns:
        Dict[str, Any]: 검증 결과
    """
    validation_result = {
        'valid': True,
        'errors': [],
        'warnings': [],
        'statistics': {},
        'orphaned_types': [],
        'type_usage': {}
    }
    
    try:
        manager = get_type_manager()
        
        # 통계 수집
        try:
            validation_result['statistics'] = manager.get_statistics()
            validation_result['type_usage'] = manager.get_type_usage()
        except Exception as e:
            validation_result['warnings'].append(f"통계 수집 실패: {e}")
        
        # 매핑 일관성 확인
        try:
            mappings = manager.get_all_mappings()
            for command_name, type_value in mappings.items():
                # CommandType에서 해당 값이 존재하는지 확인
                if not DynamicCommandType.exists(type_value):
                    validation_result['errors'].append(
                        f"명령어 '{command_name}'의 타입 '{type_value}'가 존재하지 않습니다."
                    )
                    validation_result['valid'] = False
        except Exception as e:
            validation_result['errors'].append(f"매핑 검증 실패: {e}")
            validation_result['valid'] = False
        
        # 고아 타입 확인 (매핑되지 않은 동적 타입)
        try:
            all_dynamic_types = DynamicCommandType.get_dynamic_types()
            mapped_dynamic_types = set()
            
            for cmd_type in manager._reverse_mapping.keys():
                if cmd_type.value in all_dynamic_types:
                    mapped_dynamic_types.add(cmd_type.value)
            
            orphaned_types = set(all_dynamic_types.keys()) - mapped_dynamic_types
            if orphaned_types:
                validation_result['orphaned_types'] = list(orphaned_types)
                validation_result['warnings'].append(
                    f"사용되지 않는 동적 타입 {len(orphaned_types)}개: {', '.join(orphaned_types)}"
                )
        except Exception as e:
            validation_result['warnings'].append(f"고아 타입 검사 실패: {e}")
        
        # 빈 타입 확인 (명령어가 매핑되지 않은 타입)
        try:
            empty_types = []
            for cmd_type, commands in manager._reverse_mapping.items():
                if not commands:
                    empty_types.append(cmd_type.value)
            
            if empty_types:
                validation_result['warnings'].append(
                    f"빈 타입 {len(empty_types)}개: {', '.join(empty_types)}"
                )
        except Exception as e:
            validation_result['warnings'].append(f"빈 타입 검사 실패: {e}")
        
    except Exception as e:
        validation_result['errors'].append(f"검증 중 예상치 못한 오류: {e}")
        validation_result['valid'] = False
        logger.error(f"CommandType 검증 실패: {e}")
    
    return validation_result


def cleanup_orphaned_types() -> Dict[str, Any]:
    """
    고아 동적 타입들 정리 (결과 상세화)
    
    Returns:
        Dict[str, Any]: 정리 결과
    """
    cleanup_result = {
        'success': False,
        'found_orphaned': 0,
        'removed': 0,
        'failed': 0,
        'errors': []
    }
    
    try:
        validation = validate_command_types()
        orphaned_types = validation.get('orphaned_types', [])
        cleanup_result['found_orphaned'] = len(orphaned_types)
        
        if not orphaned_types:
            cleanup_result['success'] = True
            return cleanup_result
        
        removed_count = 0
        failed_count = 0
        
        for type_name in orphaned_types:
            try:
                if DynamicCommandType.remove_dynamic_type(type_name):
                    removed_count += 1
                else:
                    failed_count += 1
            except Exception as e:
                cleanup_result['errors'].append(f"타입 '{type_name}' 제거 실패: {e}")
                failed_count += 1
        
        cleanup_result['removed'] = removed_count
        cleanup_result['failed'] = failed_count
        cleanup_result['success'] = True
        
        if removed_count > 0:
            logger.info(f"고아 동적 타입 {removed_count}개 정리 완료")
        
        return cleanup_result
        
    except Exception as e:
        cleanup_result['errors'].append(f"정리 중 오류: {e}")
        logger.error(f"고아 타입 정리 실패: {e}")
        return cleanup_result


def initialize_command_types() -> Dict[str, Any]:
    """
    CommandType 시스템 초기화 (결과 반환 개선)
    
    Returns:
        Dict[str, Any]: 초기화 결과
    """
    init_result = {
        'success': False,
        'basic_types_verified': 0,
        'missing_types': [],
        'manager_created': False,
        'errors': []
    }
    
    logger.info("CommandType 시스템 초기화 시작")
    
    try:
        # 전역 매니저 생성
        manager = get_type_manager()
        init_result['manager_created'] = True
        
        # 기본 타입들 확인
        basic_types = ['dice', 'card', 'fortune', 'help', 'custom', 'favor', 'gobal', 'jabek', 'unknown']
        missing_types = []
        verified_count = 0
        
        for type_name in basic_types:
            try:
                if DynamicCommandType.exists(type_name):
                    verified_count += 1
                else:
                    missing_types.append(type_name)
            except Exception as e:
                init_result['errors'].append(f"기본 타입 '{type_name}' 확인 실패: {e}")
        
        init_result['basic_types_verified'] = verified_count
        init_result['missing_types'] = missing_types
        
        if missing_types:
            logger.warning(f"기본 타입 누락: {missing_types}")
        else:
            logger.info("모든 기본 타입 확인 완료")
        
        init_result['success'] = True
        logger.info("CommandType 시스템 초기화 완료")
        
    except Exception as e:
        init_result['errors'].append(f"초기화 중 오류: {e}")
        logger.error(f"CommandType 시스템 초기화 실패: {e}")
    
    return init_result


def get_command_type_summary() -> Dict[str, Any]:
    """
    CommandType 시스템 요약 정보 (예외 처리 개선)
    
    Returns:
        Dict[str, Any]: 요약 정보
    """
    try:
        manager = get_type_manager()
        
        # 각 정보를 안전하게 수집
        stats = {}
        usage = {}
        validation = {}
        
        try:
            stats = manager.get_statistics()
        except Exception as e:
            logger.warning(f"통계 수집 실패: {e}")
        
        try:
            usage = manager.get_type_usage()
        except Exception as e:
            logger.warning(f"사용량 수집 실패: {e}")
        
        try:
            validation = validate_command_types()
        except Exception as e:
            logger.warning(f"검증 실패: {e}")
        
        # 타입 정보 수집
        all_types = {}
        static_types = {}
        dynamic_types = {}
        
        try:
            all_types = DynamicCommandType.get_all_types()
            static_types = DynamicCommandType.get_static_types()
            dynamic_types = DynamicCommandType.get_dynamic_types()
        except Exception as e:
            logger.warning(f"타입 정보 수집 실패: {e}")
        
        return {
            'system_status': {
                'initialized': manager.is_initialized(),
                'sync_count': manager.get_sync_count(),
                'total_types': len(all_types),
                'static_types': len(static_types),
                'dynamic_types': len(dynamic_types)
            },
            'statistics': stats,
            'type_usage': usage,
            'validation': {
                'valid': validation.get('valid', False),
                'error_count': len(validation.get('errors', [])),
                'warning_count': len(validation.get('warnings', [])),
                'orphaned_types': len(validation.get('orphaned_types', []))
            },
            'available_types': list(all_types.keys()) if all_types else []
        }
        
    except Exception as e:
        logger.error(f"요약 정보 생성 실패: {e}")
        return {
            'error': str(e),
            'system_status': {'initialized': False},
            'available_types': []
        }


def reset_command_type_system() -> Dict[str, Any]:
    """
    CommandType 시스템 완전 리셋 (새로운 기능)
    
    Returns:
        Dict[str, Any]: 리셋 결과
    """
    reset_result = {
        'success': False,
        'dynamic_types_cleared': 0,
        'mappings_cleared': 0,
        'reinitialized': False,
        'errors': []
    }
    
    try:
        manager = get_type_manager()
        
        # 동적 타입 모두 클리어
        try:
            clear_result = manager.clear_all_dynamic_types()
            reset_result['dynamic_types_cleared'] = clear_result.get('removed', 0)
        except Exception as e:
            reset_result['errors'].append(f"동적 타입 클리어 실패: {e}")
        
        # 매니저 상태 리셋
        try:
            reset_result['mappings_cleared'] = len(manager._command_type_mapping)
            manager._command_type_mapping.clear()
            manager._reverse_mapping.clear()
            manager._initialized = False
            manager._sync_count = 0
        except Exception as e:
            reset_result['errors'].append(f"매니저 리셋 실패: {e}")
        
        # 재초기화
        try:
            init_result = initialize_command_types()
            reset_result['reinitialized'] = init_result.get('success', False)
        except Exception as e:
            reset_result['errors'].append(f"재초기화 실패: {e}")
        
        reset_result['success'] = not reset_result['errors']
        
        if reset_result['success']:
            logger.info("CommandType 시스템 완전 리셋 완료")
        else:
            logger.warning(f"CommandType 시스템 리셋 중 오류 발생: {reset_result['errors']}")
        
        return reset_result
        
    except Exception as e:
        reset_result['errors'].append(f"리셋 중 예상치 못한 오류: {e}")
        logger.error(f"CommandType 시스템 리셋 실패: {e}")
        return reset_result


# 개발자를 위한 유틸리티
def debug_command_type_system() -> str:
    """
    CommandType 시스템 디버그 정보 출력 (개발용)
    
    Returns:
        str: 디버그 정보 문자열
    """
    try:
        summary = get_command_type_summary()
        validation = validate_command_types()
        
        debug_info = []
        debug_info.append("=== CommandType 시스템 디버그 정보 ===")
        
        # 시스템 상태
        system_status = summary.get('system_status', {})
        debug_info.append(f"초기화됨: {system_status.get('initialized', False)}")
        debug_info.append(f"동기화 횟수: {system_status.get('sync_count', 0)}")
        debug_info.append(f"총 타입 수: {system_status.get('total_types', 0)}")
        debug_info.append(f"정적 타입: {system_status.get('static_types', 0)}")
        debug_info.append(f"동적 타입: {system_status.get('dynamic_types', 0)}")
        
        # 검증 상태
        debug_info.append(f"\n검증 상태: {'✅ 유효' if validation.get('valid') else '❌ 무효'}")
        if validation.get('errors'):
            debug_info.append("오류:")
            for error in validation['errors'][:3]:
                debug_info.append(f"  - {error}")
        
        if validation.get('warnings'):
            debug_info.append("경고:")
            for warning in validation['warnings'][:3]:
                debug_info.append(f"  - {warning}")
        
        # 사용량 정보
        usage = summary.get('type_usage', {})
        if usage:
            debug_info.append(f"\n타입 사용량 (상위 5개):")
            sorted_usage = sorted(usage.items(), key=lambda x: x[1], reverse=True)
            for type_name, count in sorted_usage[:5]:
                debug_info.append(f"  {type_name}: {count}개 명령어")
        
        debug_info.append("=== 디버그 정보 완료 ===")
        return "\n".join(debug_info)
        
    except Exception as e:
        return f"디버그 정보 생성 실패: {e}"


# 마이그레이션 가이드
def get_migration_guide() -> str:
    """
    CommandType 시스템 마이그레이션 가이드 반환
    
    Returns:
        str: 마이그레이션 가이드 텍스트
    """
    return """
    === DynamicCommandType 마이그레이션 가이드 ===
    
    주요 개선사항:
    1. Python 3.11+ 호환성 개선 (Enum 오염 방지)
    2. 예외 처리 구체화 (ValueError, TypeError, KeyError 분리)
    3. 중복 확인 최적화 (exists() 먼저 확인)
    4. 결과 반환 상세화 (모든 주요 함수에서 Dict 반환)
    5. 안전한 매핑 관리 (defaultdict 사용)
    6. 동기화 결과 추적 (sync_count 추가)
    
    기존 사용법:
    command_type = add_command_type("weather")  # 성공/실패 불명확
    
    새로운 사용법:
    command_type = add_command_type("weather")  # 실패 시 UNKNOWN 반환
    if command_type != DynamicCommandType.UNKNOWN:
        # 성공 처리
    
    결과 확인 개선:
    sync_result = register_command_types_from_registry(registry)
    if sync_result['success']:
        print(f"새로 추가된 명령어: {sync_result['new_commands']}")
        print(f"제거된 명령어: {sync_result['removed_commands']}")
    
    시스템 관리 개선:
    # 전체 상태 확인
    summary = get_command_type_summary()
    
    # 시스템 리셋 (필요시)
    reset_result = reset_command_type_system()
    
    # 디버그 정보 출력
    debug_info = debug_command_type_system()
    print(debug_info)
    
    === 마이그레이션 완료 ===
    """


# 모듈이 직접 실행될 때 테스트 (개선됨)
if __name__ == "__main__":
    # 로깅 설정
    logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')
    
    print("=== DynamicCommandType 테스트 (개선 버전) ===")
    
    # 시스템 초기화
    init_result = initialize_command_types()
    print(f"\n초기화 결과: {init_result}")
    
    # 기본 타입들 확인
    print("\n기본 타입들:")
    static_types = DynamicCommandType.get_static_types()
    for type_value, cmd_type in static_types.items():
        print(f"  {cmd_type.name} = {type_value}")
    
    # 동적 타입 추가 테스트
    print("\n동적 타입 추가 테스트:")
    test_types = ['weather', 'music', 'news']
    for type_name in test_types:
        new_type = add_command_type(type_name)
        success = "✅" if new_type != DynamicCommandType.UNKNOWN else "❌"
        print(f"  {success} {type_name} -> {new_type.value}")
    
    # 통계 확인
    print("\n시스템 요약:")
    summary = get_command_type_summary()
    system_status = summary.get('system_status', {})
    for key, value in system_status.items():
        print(f"  {key}: {value}")
    
    # 검증 실행
    print("\n시스템 검증:")
    validation = validate_command_types()
    print(f"  유효성: {'✅ 통과' if validation['valid'] else '❌ 실패'}")
    if validation['warnings']:
        print(f"  경고 {len(validation['warnings'])}개: {validation['warnings'][:2]}")
    if validation['errors']:
        print(f"  오류 {len(validation['errors'])}개: {validation['errors'][:2]}")
    
    # 디버그 정보
    print("\n" + debug_command_type_system())
    
    print("\n=== 테스트 완료 ===")
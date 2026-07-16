"""
도움말 명령어 구현 - 개선된 BaseCommand 아키텍처
Google Sheets에서 도움말 정보를 가져와 표시하는 명령어 클래스입니다.
"""

import os
import sys
import re
import time
from typing import List, Optional, Dict, Any, Union, TYPE_CHECKING

if TYPE_CHECKING:
    pass
from dataclasses import dataclass

# 경로 설정 (VM 환경 대응)
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

# 의존성 임포트
try:
    from config.settings import config
    from utils.logging_config import logger
    from utils.cache_manager import bot_cache
    from commands.base_command import BaseCommand, CommandContext, CommandResponse
    from commands.registry import register_command
    from models.command_result import HelpResult, create_help_result
    DUMMY_MODE = False
except ImportError as e:
    import logging
    logger = logging.getLogger('help_command')
    logger.error(f"필수 모듈 임포트 실패: {e}")
    raise


@dataclass
class CachedHelpData:
    """캐시된 도움말 데이터 (TTL 포함)"""
    items: List[Dict[str, str]]
    cached_at: float  # Unix timestamp
    ttl_seconds: int = 10800  # 3시간 (3 * 60 * 60)
    
    @property
    def is_expired(self) -> bool:
        """캐시가 만료되었는지 확인"""
        return time.time() - self.cached_at > self.ttl_seconds
    
    @property
    def remaining_time(self) -> int:
        """남은 캐시 시간 (초)"""
        remaining = self.ttl_seconds - (time.time() - self.cached_at)
        return max(0, int(remaining))
    
    @property
    def age_minutes(self) -> int:
        """캐시 생성 후 경과 시간 (분)"""
        return int((time.time() - self.cached_at) / 60)


@dataclass
class HelpItem:
    """도움말 항목 데이터 클래스"""
    command: str
    description: str
    
    def __post_init__(self):
        self.command = self.command.strip()
        self.description = self.description.strip()
    
    @property
    def is_valid(self) -> bool:
        """유효한 도움말 항목인지 확인"""
        return bool(self.command and self.description)
    
    @property
    def formatted_command(self) -> str:
        """대괄호가 포함된 형식의 명령어 반환"""
        if not self.command.startswith('[') or not self.command.endswith(']'):
            return f"[{self.command}]"
        return self.command
    
    def matches_keyword(self, keyword: str) -> bool:
        """키워드와 매칭되는지 확인"""
        if not keyword:
            return False
        keyword_lower = keyword.lower().strip()
        return (keyword_lower in self.command.lower() or 
                keyword_lower in self.description.lower())


class HelpDataLoader:
    """도움말 데이터 로딩 및 캐싱 관리 (TTL 지원)"""
    
    CACHE_TTL_SECONDS = 3600  # 1시간
    CACHE_KEY = "help_items_with_ttl"
    
    def __init__(self, sheets_manager=None, cache_manager=None):
        self.sheets_manager = sheets_manager
        self.cache_manager = cache_manager or bot_cache
    
    def load_help_items(self) -> List[HelpItem]:
        """도움말 항목 로드 (TTL 기반 캐시 우선, 시트 후순위)"""
        # 캐시에서 먼저 조회 (TTL 검증 포함)
        cached_items = self._load_from_cache_with_ttl()
        if cached_items:
            logger.debug(f"캐시에서 도움말 항목 로드: {len(cached_items)}개")
            return cached_items
        
        # 시트에서 로드
        sheet_items = self._load_from_sheet()
        if sheet_items:
            logger.info(f"시트에서 도움말 항목 로드: {len(sheet_items)}개")
            self._save_to_cache_with_ttl(sheet_items)
            return sheet_items
        
        logger.warning("도움말 항목을 로드할 수 없음 - 빈 리스트 반환")
        return []
    
    def _load_from_cache_with_ttl(self) -> Optional[List[HelpItem]]:
        """TTL 검증을 포함한 캐시에서 도움말 항목 로드"""
        try:
            # 기존 캐시 데이터 조회
            cached_data = self._get_cached_data()
            if not cached_data:
                logger.debug("캐시에 데이터가 없음")
                return None
            
            # TTL 검증
            if cached_data.is_expired:
                logger.info(f"캐시 만료됨 (생성 후 {cached_data.age_minutes}분 경과), 새로 로드 필요.")
                self._clear_cache()
                return None
            
            logger.debug(f"캐시 유효함 (남은 시간: {cached_data.remaining_time // 60}분)")
            
            # 데이터 변환
            return [HelpItem(item.get('명령어', ''), item.get('설명', '')) 
                   for item in cached_data.items]
                   
        except Exception as e:
            logger.warning(f"TTL 캐시 조회 실패: {e}")
            return None
    
    def _get_cached_data(self) -> Optional[CachedHelpData]:
        """캐시에서 TTL 데이터 조회"""
        try:
            # 새로운 TTL 키로 조회
            if hasattr(self.cache_manager, 'get'):
                cached_data = self.cache_manager.get(self.CACHE_KEY)
            else:
                # 기존 방식 fallback
                cached_data = getattr(self.cache_manager, 'command_cache', {}).get(self.CACHE_KEY)
            
            if cached_data and isinstance(cached_data, dict):
                return CachedHelpData(
                    items=cached_data.get('items', []),
                    cached_at=cached_data.get('cached_at', 0),
                    ttl_seconds=cached_data.get('ttl_seconds', self.CACHE_TTL_SECONDS)
                )
            
            return None
            
        except Exception as e:
            logger.debug(f"캐시 데이터 조회 실패: {e}")
            return None
    
    def _load_from_cache(self) -> Optional[List[HelpItem]]:
        """기존 캐시 조회 방식 (하위 호환성)"""
        try:
            cached_data = self.cache_manager.get_help_items()
            if cached_data:
                return [HelpItem(item.get('명령어', ''), item.get('설명', '')) 
                       for item in cached_data]
        except Exception as e:
            logger.debug(f"기존 캐시 조회 실패: {e}")
        return None
    
    def _load_from_sheet(self, sheet_name: Optional[str] = None) -> Optional[List[HelpItem]]:
        """시트에서 도움말 항목 로드

        Args:
            sheet_name: 도움말 시트 이름 (None이면 기본 시트 사용)
        """
        if not self.sheets_manager:
            logger.debug("시트 매니저가 없음")
            return None

        try:
            raw_items = self.sheets_manager.get_help_items(sheet_name=sheet_name)
            if not raw_items:
                logger.warning("시트에 도움말 데이터가 없음")
                return None

            help_items = []
            for item in raw_items:
                help_item = HelpItem(
                    command=item.get('명령어', ''),
                    description=item.get('설명', '')
                )
                if help_item.is_valid:
                    help_items.append(help_item)
                else:
                    logger.debug(f"유효하지 않은 도움말 항목 스킵: {item}")

            return help_items if help_items else None

        except Exception as e:
            logger.error(f"시트에서 도움말 항목 로드 실패: {e}")
            return None
    
    def _save_to_cache_with_ttl(self, help_items: List[HelpItem]) -> None:
        """TTL과 함께 캐시에 도움말 항목 저장"""
        try:
            cache_data = {
                'items': [{'명령어': item.command, '설명': item.description} 
                         for item in help_items],
                'cached_at': time.time(),
                'ttl_seconds': self.CACHE_TTL_SECONDS
            }
            
            # 새로운 TTL 방식으로 저장
            if hasattr(self.cache_manager, 'set'):
                self.cache_manager.set(self.CACHE_KEY, cache_data)
            elif hasattr(self.cache_manager, 'command_cache'):
                self.cache_manager.command_cache.set(self.CACHE_KEY, cache_data)
            
            # 기존 방식도 병행 (하위 호환성)
            try:
                self.cache_manager.cache_help_items(cache_data['items'])
            except Exception as e:
                logger.debug("기존 캐시 저장 실패 (무시): %s", e)
            
            logger.info(f"도움말 항목을 TTL 캐시에 저장함 ({len(help_items)}개, TTL: {self.CACHE_TTL_SECONDS // 3600}시간)")
            
        except Exception as e:
            logger.warning(f"TTL 캐시 저장 실패: {e}")
    
    def _save_to_cache(self, help_items: List[HelpItem]) -> None:
        """기존 캐시 저장 방식 (하위 호환성)"""
        try:
            cache_data = [{'명령어': item.command, '설명': item.description} 
                         for item in help_items]
            self.cache_manager.cache_help_items(cache_data)
            logger.debug("도움말 항목을 기존 캐시에 저장함")
        except Exception as e:
            logger.warning(f"기존 캐시 저장 실패: {e}")
    
    def _clear_cache(self) -> bool:
        """캐시 삭제"""
        try:
            cleared = False
            
            # TTL 캐시 삭제
            if hasattr(self.cache_manager, 'delete'):
                cleared = self.cache_manager.delete(self.CACHE_KEY) or cleared
            elif hasattr(self.cache_manager, 'command_cache'):
                if self.cache_manager.command_cache.exists(self.CACHE_KEY):
                    cleared = self.cache_manager.command_cache.delete(self.CACHE_KEY) or cleared
            
            # 기존 캐시도 삭제
            if hasattr(self.cache_manager, 'command_cache'):
                if self.cache_manager.command_cache.exists("help_items"):
                    cleared = self.cache_manager.command_cache.delete("help_items") or cleared
            
            if cleared:
                logger.debug("도움말 캐시 삭제됨")
            
            return cleared
            
        except Exception as e:
            logger.warning(f"캐시 삭제 실패: {e}")
            return False
    
    def get_cache_status(self) -> Dict[str, Any]:
        """캐시 상태 정보 반환"""
        try:
            cached_data = self._get_cached_data()
            if not cached_data:
                return {
                    'cached': False,
                    'message': '캐시에 데이터가 없습니다.'
                }
            
            return {
                'cached': True,
                'expired': cached_data.is_expired,
                'cached_at': cached_data.cached_at,
                'age_minutes': cached_data.age_minutes,
                'remaining_minutes': cached_data.remaining_time // 60,
                'total_ttl_hours': cached_data.ttl_seconds // 3600,
                'items_count': len(cached_data.items),
                'message': f"캐시됨 ({cached_data.age_minutes}분 전, 남은 시간: {cached_data.remaining_time // 60}분)"
            }
            
        except Exception as e:
            logger.error(f"캐시 상태 조회 실패: {e}")
            return {
                'cached': False,
                'error': str(e),
                'message': '캐시 상태 조회 실패'
            }
    
    def refresh_cache(self) -> Dict[str, Any]:
        """캐시 새로고침 (강제)"""
        try:
            # 기존 캐시 삭제
            cache_cleared = self._clear_cache()
            
            # 시트에서 새로 로드
            new_items = self._load_from_sheet()
            item_count = len(new_items) if new_items else 0
            
            if new_items:
                self._save_to_cache_with_ttl(new_items)
            
            return {
                'success': True,
                'cache_cleared': cache_cleared,
                'new_items_count': item_count,
                'ttl_hours': self.CACHE_TTL_SECONDS // 3600,
                'message': f"도움말 캐시 새로고침 완료 ({item_count}개 항목, TTL: {self.CACHE_TTL_SECONDS // 3600}시간)"
            }
            
        except Exception as e:
            logger.error(f"캐시 새로고침 실패: {e}")
            return {
                'success': False,
                'error': str(e)
            }


class HelpTextGenerator:
    """도움말 텍스트 생성 유틸리티"""
    
    # 기본 도움말 내용
    DEFAULT_HELP = """자동봇 도움말

자동봇 답변의 공개 범위는 명령어를 포함한 멘션의 공개 범위를 따릅니다.

사용 가능한 명령어:

주사위 명령어:
[nDm] - m면체 주사위를 n개 굴립니다.
[nDm<k] - m면체 주사위를 n개 굴리고, k 미만이면 성공합니다.
[nDm>k] - m면체 주사위를 n개 굴리고, k 초과면 성공합니다.

카드 명령어:
[카드뽑기] - 트럼프 카드 1장을 뽑습니다.
[카드뽑기/n장] - 트럼프 카드를 n장 뽑습니다.

기타 명령어:
[도움말] - 이 도움말을 보여줍니다.
"""
    
    @classmethod
    def generate_help_text(cls, help_items: List[HelpItem]) -> str:
        """도움말 텍스트 생성"""
        if not help_items:
            logger.info("도움말 항목 없음, 기본 도움말 사용")
            return cls.DEFAULT_HELP
        
        # 유효한 항목들만 필터링
        valid_items = [item for item in help_items if item.is_valid]
        
        if not valid_items:
            logger.warning("유효한 도움말 항목 없음, 기본 도움말 사용")
            return cls.DEFAULT_HELP
        
        # 도움말 텍스트 구성
        header = (
            "자동봇 도움말\n\n"
            "자동봇 답변의 공개 범위는 명령어를 포함한 멘션의 공개 범위를 따릅니다.\n\n"
            "사용 가능한 명령어:\n\n"
        )
        
        # 명령어 목록 생성
        command_lines = []
        for item in valid_items:
            command_lines.append(f"{item.formatted_command} - {item.description}")
        
        content = "\n".join(command_lines)
        
        return header + content
    
    @classmethod
    def count_commands_in_text(cls, help_text: str) -> int:
        """도움말 텍스트에서 명령어 개수 계산 (정규식 사용)"""
        if not help_text:
            return 0
        
        # [명령어] - 설명 패턴을 찾는 정규식
        pattern = r'\[([^\]]+)\]\s*-\s*.+'
        matches = re.findall(pattern, help_text, re.MULTILINE)
        
        return len(matches)
    
    @classmethod
    def extract_commands_from_text(cls, help_text: str) -> List[str]:
        """도움말 텍스트에서 명령어들 추출"""
        if not help_text:
            return []
        
        # [명령어] 패턴 찾기
        pattern = r'\[([^\]]+)\]'
        matches = re.findall(pattern, help_text)
        
        # 중복 제거하고 정리
        commands = []
        for match in matches:
            command = match.strip()
            if command and command not in commands:
                commands.append(command)
        
        return commands


class HelpValidator:
    """도움말 데이터 유효성 검증"""
    
    def __init__(self, data_loader: HelpDataLoader):
        self.data_loader = data_loader
    
    def validate_help_data(self) -> Dict[str, Any]:
        """도움말 데이터 유효성 검증"""
        results = {
            'valid': True,
            'errors': [],
            'warnings': [],
            'info': {}
        }
        
        try:
            # 시트 데이터 검증
            self._validate_sheet_data(results)
            
            # 캐시 상태 검증
            self._validate_cache_status(results)
            
            # 기본 도움말 검증
            self._validate_default_help(results)
            
            # 오류가 있으면 유효하지 않음
            if results['errors']:
                results['valid'] = False
            
        except Exception as e:
            logger.error(f"도움말 데이터 검증 중 예외 발생: {e}")
            results['valid'] = False
            results['errors'].append(f"검증 중 오류: {str(e)}")
        
        return results
    
    def _validate_sheet_data(self, results: Dict[str, Any]) -> None:
        """시트 데이터 검증"""
        if not self.data_loader.sheets_manager:
            results['warnings'].append("시트 매니저가 없습니다. 기본 도움말을 사용합니다.")
            results['info']['will_use_default'] = True
            return
        
        try:
            help_items = self.data_loader._load_from_sheet()
            if not help_items:
                results['warnings'].append("시트에 도움말 데이터가 없습니다.")
                results['info']['will_use_default'] = True
            else:
                results['info']['sheet_items_count'] = len(help_items)
                results['info']['will_use_default'] = False
                
                # 중복 명령어 확인
                commands = [item.command for item in help_items]
                duplicates = [cmd for cmd in set(commands) if commands.count(cmd) > 1]
                if duplicates:
                    results['warnings'].append(f"중복된 명령어: {', '.join(duplicates)}")
                
                # 대괄호 형식 확인
                invalid_format = [item.command for item in help_items 
                                if not (item.command.startswith('[') and item.command.endswith(']'))]
                if invalid_format:
                    results['warnings'].append(f"대괄호 형식이 아닌 명령어: {', '.join(invalid_format[:3])}...")
        
        except Exception as e:
            results['errors'].append(f"시트 데이터 로드 실패: {str(e)}")
            results['info']['will_use_default'] = True
    
    def _validate_cache_status(self, results: Dict[str, Any]) -> None:
        """캐시 상태 검증"""
        try:
            cache_status = self.data_loader.get_cache_status()
            results['info']['cache_available'] = cache_status.get('cached', False)
            results['info']['cache_expired'] = cache_status.get('expired', False)
            
            if cache_status.get('cached'):
                results['info']['cache_age_minutes'] = cache_status.get('age_minutes', 0)
                results['info']['cache_remaining_minutes'] = cache_status.get('remaining_minutes', 0)
                results['info']['cached_items_count'] = cache_status.get('items_count', 0)
                
        except Exception as e:
            logger.debug(f"캐시 상태 확인 실패: {e}")
            results['info']['cache_available'] = False
    
    def _validate_default_help(self, results: Dict[str, Any]) -> None:
        """기본 도움말 검증"""
        try:
            default_command_count = HelpTextGenerator.count_commands_in_text(
                HelpTextGenerator.DEFAULT_HELP
            )
            results['info']['default_command_count'] = default_command_count
        except Exception as e:
            logger.warning(f"기본 도움말 검증 실패: {e}")
            results['warnings'].append("기본 도움말 검증 실패")


@dataclass
class DummyHelpResult:
    """HelpResult가 없을 때 사용할 더미 결과 클래스"""
    help_text: str
    command_count: int


@register_command(
    name="도움말",
    aliases=["help", "헬프"],
    description="사용 가능한 명령어 도움말 표시",
    category="시스템",
    examples=["[도움말]", "[help]", "[헬프]"],
    requires_sheets=True,
    requires_api=False
)
class HelpCommand(BaseCommand):
    """
    도움말 명령어 클래스
    
    Google Sheets의 '도움말' 시트에서 명령어 정보를 가져와 표시합니다.
    
    지원하는 형식:
    - [도움말] : 모든 명령어 도움말 표시
    - [help] : 영문 도움말 명령어
    - [헬프] : 도움말 별칭
    """
    
    def __init__(self, sheets_manager=None, api=None, bot_config=None, **kwargs):
        super().__init__(sheets_manager, api, **kwargs)
        self.bot_config = bot_config
        self.data_loader = HelpDataLoader(sheets_manager, bot_cache)
        self.validator = HelpValidator(self.data_loader)
    
    def execute(self, context: CommandContext) -> CommandResponse: # type: ignore
        """도움말 명령어 실행"""
        try:
            # 봇별 도움말 시트 이름 가져오기
            sheet_name = None
            if self.bot_config and hasattr(self.bot_config, 'help_sheet'):
                sheet_name = self.bot_config.help_sheet
                if sheet_name:
                    logger.info(f"봇별 도움말 시트 사용: {sheet_name}")

            # 도움말 항목 로드 (봇별 시트 사용)
            help_items = self.data_loader._load_from_sheet(sheet_name=sheet_name)
            if not help_items:
                # 시트에서 로드 실패 시 캐시나 기본값 사용
                help_items = self.data_loader.load_help_items()

            # 도움말 텍스트 생성
            help_text = HelpTextGenerator.generate_help_text(help_items)

            # 명령어 개수 계산
            command_count = HelpTextGenerator.count_commands_in_text(help_text)

            # 결과 객체 생성
            if not DUMMY_MODE:
                try:
                    help_result = create_help_result(help_text, command_count)
                except Exception as e:
                    logger.warning(f"HelpResult 생성 실패, 더미 객체 사용: {e}")
                    help_result = DummyHelpResult(help_text, command_count)
            else:
                help_result = DummyHelpResult(help_text, command_count)

            logger.info(f"도움말 명령어 실행 완료: {command_count}개 명령어 (시트: {sheet_name or '기본'})")
            return CommandResponse.create_success(help_text, data=help_result)

        except Exception as e:
            logger.error(f"도움말 명령어 실행 실패: {e}")
            return CommandResponse.create_error(
                f"도움말을 불러오는 중 오류가 발생했습니다: {str(e)}",
                error=e
            )
    
    def validate_context(self, context: CommandContext) -> Optional[str]: # type: ignore
        """컨텍스트 유효성 검증 (오버라이드)"""
        # 기본 검증
        base_validation = super().validate_context(context)
        if base_validation:
            return base_validation
        
        # 도움말은 특별한 추가 검증이 필요하지 않음
        return None
    
    def get_help_statistics(self) -> Dict[str, Any]:
        """도움말 통계 정보 반환"""
        try:
            help_items = self.data_loader.load_help_items()
            help_text = HelpTextGenerator.generate_help_text(help_items)
            
            # 시트 항목 수 확인
            sheet_items = self.data_loader._load_from_sheet()
            sheet_items_count = len(sheet_items) if sheet_items else 0
            
            # 캐시 상태 확인
            cache_status = self.data_loader.get_cache_status()
            
            stats = {
                'total_help_items': len(help_items),
                'sheet_items_count': sheet_items_count,
                'using_default_help': sheet_items_count == 0,
                'command_count_in_help': HelpTextGenerator.count_commands_in_text(help_text),
                'cache_status': cache_status
            }
            
            # 캐시 관련 정보 추가
            if cache_status.get('cached'):
                stats.update({
                    'cache_available': True,
                    'cache_expired': cache_status.get('expired', False),
                    'cache_age_minutes': cache_status.get('age_minutes', 0),
                    'cache_remaining_minutes': cache_status.get('remaining_minutes', 0)
                })
            else:
                stats['cache_available'] = False
            
            return stats
            
        except Exception as e:
            logger.error(f"도움말 통계 조회 실패: {e}")
            return {'error': str(e)}
    
    def get_available_help_commands(self) -> List[str]:
        """도움말에서 사용 가능한 명령어 목록 추출"""
        try:
            help_items = self.data_loader.load_help_items()
            commands = []
            
            for item in help_items:
                if item.is_valid:
                    # 대괄호 제거
                    command = item.command
                    if command.startswith('[') and command.endswith(']'):
                        command = command[1:-1]
                    commands.append(command)
            
            return commands
        except Exception as e:
            logger.error(f"도움말 명령어 목록 추출 실패: {e}")
            return []
    
    def search_help_by_keyword(self, keyword: str) -> List[HelpItem]:
        """키워드로 도움말 항목 검색"""
        try:
            help_items = self.data_loader.load_help_items()
            if not keyword:
                return help_items
            
            return [item for item in help_items if item.matches_keyword(keyword)]
            
        except Exception as e:
            logger.error(f"도움말 검색 실패: {e}")
            return []
    
    def validate_help_data(self) -> Dict[str, Any]:
        """도움말 데이터 유효성 검증"""
        return self.validator.validate_help_data()
    
    def refresh_help_cache(self) -> Dict[str, Any]:
        """도움말 캐시 새로고침"""
        return self.data_loader.refresh_cache()

    @staticmethod
    def get_supported_keywords() -> List[str]:
        """지원 키워드 (대표 우선)"""
        return ['도움말', 'help', '헬프']


# 유틸리티 함수들
def is_help_command(keyword: str) -> bool:
    """키워드가 도움말 명령어인지 확인"""
    if not keyword:
        return False
    
    keyword = keyword.lower().strip()
    return keyword in ['도움말', 'help', '헬프']


def generate_simple_help(commands_info: List[Dict[str, str]]) -> str:
    """간단한 도움말 텍스트 생성"""
    if not commands_info:
        return "사용 가능한 명령어가 없습니다."
    
    help_items = []
    for info in commands_info:
        command = info.get('command', '').strip()
        description = info.get('description', '').strip()
        
        if command and description:
            help_item = HelpItem(command, description)
            help_items.append(help_item)
    
    if not help_items:
        return "유효한 명령어 정보가 없습니다."
    
    return HelpTextGenerator.generate_help_text(help_items)


def create_help_command(sheets_manager=None, api=None) -> HelpCommand:
    """도움말 명령어 인스턴스 생성"""
    return HelpCommand(sheets_manager=sheets_manager, api=api)
"""
상점 명령어 공통 기반 클래스
모든 상점 관련 명령어(구매, 양도, 상점, 가방, 아이템 설명, 소지금 관리)의 공통 코드를 제공합니다.
"""

import os
import sys
from abc import abstractmethod
from typing import List, Tuple, Any, Optional, Dict

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

try:
    from utils.logging_config import logger
    from utils.error_handling import CommandError, SheetAccessError
    from utils.store_helpers import (
        find_column_by_header,
        find_user_row,
        extract_price_info,
        safe_parse_inventory,
        invalidate_user_cache,
        load_item_data,
        load_user_data,
    )
    from commands.base_command import BaseCommand, CommandContext, CommandResponse
    from models.user import User, create_empty_user
except ImportError as e:
    import logging
    logger = logging.getLogger('base_store_command')
    logger.error(f"필수 모듈 임포트 실패: {e}")
    raise


class BaseStoreCommand(BaseCommand):
    """
    상점 명령어 공통 기반 클래스

    공통 기능:
    - execute() 보일러플레이트 (User 생성, 예외 처리)
    - 워크시트 컬럼/행 탐색 래퍼 메서드
    - 아이템/사용자 데이터 로드 래퍼 메서드
    - 인벤토리 파싱 래퍼 메서드
    - 캐시 무효화 래퍼 메서드
    """

    # 서브클래스에서 오버라이드하여 에러 메시지를 커스터마이즈
    _sheet_error_suffix: str = ""
    _generic_error_prefix: str = "명령어 실행 중"

    def execute(self, context: CommandContext) -> CommandResponse:
        """
        공통 execute 보일러플레이트.
        서브클래스는 _execute_command_logic()만 구현하면 됩니다.
        """
        try:
            try:
                user = User(id=context.user_id, name=context.user_name)
            except Exception as e:
                logger.warning("User 객체 생성 실패 (id=%s): %s", context.user_id, e)
                user = create_empty_user(context.user_id)

            result = self._execute_command_logic(user, context.keywords)

            if isinstance(result, CommandResponse):
                return result
            if isinstance(result, tuple) and len(result) == 2:
                message, data = result
                return CommandResponse.create_success(message, data=data)
            return CommandResponse.create_success(str(result))

        except SheetAccessError as e:
            logger.error(f"Google Sheets API 오류{self._sheet_error_suffix}: {e}", exc_info=True)
            return CommandResponse.create_error(
                f"일시적인 시스템 오류가 발생했습니다. 잠시 후 다시 시도해주세요.\n"
                f"(Google Sheets API 연결 문제)",
                error=e
            )
        except CommandError as e:
            return CommandResponse.create_error(str(e), error=e)
        except Exception as e:
            logger.error(f"{self._generic_error_prefix} 오류: {e}", exc_info=True)
            return CommandResponse.create_error(
                "명령어 실행 중 오류가 발생했습니다.",
                error=e
            )

    @abstractmethod
    def _execute_command_logic(self, user: User, keywords: List[str]):
        """서브클래스에서 구현할 명령어 로직"""

    # ── 워크시트 컬럼/행 탐색 래퍼 ──

    def _find_id_column(self, worksheet=None) -> Optional[int]:
        """관리 워크시트에서 '아이디' 컬럼 번호 찾기"""
        if worksheet is None:
            worksheet = self.sheets_manager.get_worksheet('관리')
        return find_column_by_header(worksheet, '아이디')

    def _find_money_column(self, worksheet=None) -> Optional[int]:
        """관리 워크시트에서 '소지금' 컬럼 번호 찾기"""
        if worksheet is None:
            worksheet = self.sheets_manager.get_worksheet('관리')
        return find_column_by_header(worksheet, '소지금')

    def _find_inventory_column(self, worksheet=None) -> Optional[int]:
        """관리 워크시트에서 '소지품' 컬럼 번호 찾기"""
        if worksheet is None:
            worksheet = self.sheets_manager.get_worksheet('관리')
        return find_column_by_header(worksheet, '소지품')

    def _find_user_row(self, user_id: str, worksheet=None, id_col: Optional[int] = None) -> Optional[int]:
        """사용자의 시트 행 번호 찾기"""
        if worksheet is None:
            if not self.sheets_manager:
                return None
            worksheet = self.sheets_manager.get_worksheet('관리')
        return find_user_row(worksheet, user_id, id_col)

    # ── 데이터 로드 래퍼 ──

    def _load_item_data(self) -> List[Dict[str, Any]]:
        """아이템 데이터 로드"""
        return load_item_data(self.sheets_manager)

    def _load_user_data(self) -> List[Dict[str, Any]]:
        """명단 데이터 로드"""
        return load_user_data(self.sheets_manager)

    # ── 인벤토리/캐시 래퍼 ──

    def _parse_inventory(self, inventory_str: str) -> Dict[str, int]:
        """인벤토리 문자열 파싱 (None 안전)"""
        return safe_parse_inventory(inventory_str)

    def _invalidate_user_cache(self):
        """사용자 데이터 캐시 무효화"""
        invalidate_user_cache()

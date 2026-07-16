"""
캐시 리셋 명령어 구현
특정 워크시트의 캐시를 무효화하고 새로 로드하는 명령어입니다.
사용법: [캐시 리셋/워크시트명] 또는 [캐시리셋/워크시트명]
"""

import os
import sys
import time
from typing import List, Optional, Dict, Any

# 경로 설정
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

try:
    import gspread
    from config.settings import config
    from utils.logging_config import logger
    from utils.error_handling import SheetAccessError
    from utils.cache_manager import bot_cache
    from commands.base_command import BaseCommand, CommandContext, CommandResponse
    from commands.registry import register_command
except ImportError as e:
    import logging
    logger = logging.getLogger('cache_reset_command')
    raise ImportError(f"필수 모듈 임포트 실패: {e}")


@register_command(
    name="캐시 리셋",
    aliases=["캐시리셋", "캐시 초기화", "캐시초기화"],
    description="특정 워크시트의 캐시를 리셋하고 최신 데이터를 로드합니다. (관리자 전용)",
    examples=[
        "[캐시 리셋/도움말] - 도움말 캐시 리셋",
        "[캐시리셋/명단] - 명단 캐시 리셋",
        "[캐시 리셋/운세] - 운세 캐시 리셋",
        "[캐시리셋/커스텀] - 커스텀 명령어 캐시 리셋",
        "[캐시 리셋/상점] - 상점 캐시 리셋",
        "[캐시 리셋/전체] - 모든 캐시 리셋"
    ],
    category="관리",
    admin_only=True,
    requires_sheets=True
)
class CacheResetCommand(BaseCommand):
    """캐시 리셋 명령어 클래스"""

    # 워크시트별 캐시 매핑 (전용 리셋 로직이 있는 시트)
    WORKSHEET_CACHE_MAP = {
        '도움말': 'help',
        '명단': 'roster',
        '운세': 'fortune',
        '커스텀': 'custom',
        '상점': 'shop',
        '전체': 'all'
    }

    def __init__(self, sheets_manager=None, api=None, **kwargs):
        """CacheResetCommand 초기화 (정규화)"""
        super().__init__(sheets_manager=sheets_manager, api=api, **kwargs)
        self.cache_manager = bot_cache
        # 보조 스프레드시트 매니저들 — 각자 자기 _data_cache(TTL 캐시)를 갖는다.
        # bot_cache만 비우면 이 캐시들은 남으므로 [캐시 리셋/전체]에서 함께 비운다.
        self.investigation_sheets_manager = kwargs.get('investigation_sheets_manager')
        self.system_sheets_manager = kwargs.get('system_sheets_manager')

    def _reset_sheet_managers(self) -> List[str]:
        """스프레드시트 매니저들의 워크시트/데이터/헤더 캐시를 비운다.

        `bot_cache`(명단·도움말·상점 등)와 별개로, SheetsManager 인스턴스마다
        `get_worksheet_data(use_cache=True)`용 TTL 캐시를 따로 들고 있다.
        조사 시트는 TTL이 길어(INVESTIGATION_CACHE_TTL) GM이 시트를 고친 뒤
        즉시 반영하려면 이쪽을 비워야 한다.
        """
        results = []
        for label, manager in [
            ('기본', self.sheets_manager),
            ('시스템', self.system_sheets_manager),
            ('조사', self.investigation_sheets_manager),
        ]:
            if manager is None:
                continue
            try:
                manager.clear_cache()
                results.append(f"• {label} 스프레드시트 캐시: ✅ 초기화")
            except Exception as e:
                results.append(f"• {label} 스프레드시트 캐시: ❌ 실패 ({e})")
        return results

    def execute(self, context: CommandContext) -> CommandResponse:
        """
        캐시 리셋 명령어 실행

        Args:
            context: 명령어 실행 컨텍스트

        Returns:
            CommandResponse: 실행 결과
        """
        try:
            # 1. 워크시트명 추출
            if len(context.keywords) < 2:
                return CommandResponse.create_error(
                    "워크시트명을 지정해주세요.\n"
                    "사용법: [캐시 리셋/워크시트명]\n"
                    "예: [캐시 리셋/도움말], [캐시리셋/명단], [캐시 리셋/전체]"
                )

            worksheet_name = context.keywords[1].strip()

            # 2. 캐시 리셋 및 재로드
            if worksheet_name == '전체':
                result_msg = self._reset_all_caches()
            elif worksheet_name in self.WORKSHEET_CACHE_MAP:
                # 전용 리셋 로직이 있는 하드코딩된 시트
                cache_type = self.WORKSHEET_CACHE_MAP[worksheet_name]
                result_msg = self._reset_specific_cache(cache_type, worksheet_name)
            else:
                # 하드코딩되지 않은 시트 → 범용 캐시 리셋
                result_msg = self._reset_generic_worksheet_cache(worksheet_name)

            logger.info(f"캐시 리셋 완료: {worksheet_name} ({context.user_id})")

            return CommandResponse.create_success(result_msg)

        except SheetAccessError as e:
            logger.error(f"Google Sheets API 오류: {e}", exc_info=True)
            return CommandResponse.create_error(
                "일시적인 시스템 오류가 발생했습니다. 잠시 후 다시 시도해주세요.\n"
                "(Google Sheets API 연결 문제)",
                error=e
            )
        except Exception as e:
            logger.error(f"캐시 리셋 중 오류: {e}", exc_info=True)
            return CommandResponse.create_error(f"캐시 리셋 중 오류가 발생했습니다: {str(e)}", error=e)

    def _reset_specific_cache(self, cache_type: str, worksheet_name: str) -> str:
        """
        특정 캐시를 리셋하고 재로드

        Args:
            cache_type: 캐시 타입 (help, roster, fortune, custom)
            worksheet_name: 워크시트명

        Returns:
            str: 결과 메시지
        """
        start_time = time.time()

        try:
            if cache_type == 'help':
                return self._reset_help_cache()
            elif cache_type == 'roster':
                return self._reset_roster_cache()
            elif cache_type == 'fortune':
                return self._reset_fortune_cache()
            elif cache_type == 'custom':
                return self._reset_custom_cache()
            elif cache_type == 'shop':
                return self._reset_shop_cache()
            else:
                return f"알 수 없는 캐시 타입: {cache_type}"

        except Exception as e:
            logger.error(f"캐시 리셋 실패 ({cache_type}): {e}")
            return f"{worksheet_name} 캐시 리셋 실패: {str(e)}"

    def _reset_help_cache(self) -> str:
        """도움말 캐시 리셋"""
        # 기존 캐시 삭제
        old_cached = self.cache_manager.command_cache.delete("help_items")
        self.cache_manager.command_cache.delete("help_items_with_ttl")

        # 새 데이터 로드
        if self.sheets_manager:
            try:
                help_items = self.sheets_manager.get_help_items()

                # TTL 포함 캐시 저장
                cache_data = {
                    'items': help_items,
                    'cached_at': time.time(),
                    'ttl_seconds': 3600  # 1시간
                }
                self.cache_manager.command_cache.set("help_items_with_ttl", cache_data)
                self.cache_manager.cache_help_items(help_items)

                return f"도움말 캐시 리셋에 성공했습니다."
            except Exception as e:
                logger.error(f"도움말 데이터 로드 실패: {e}")
                return f"⚠️ 도움말 캐시 삭제 완료, 데이터 로드 실패: {str(e)}"
        else:
            return "⚠️ 도움말 캐시 삭제 완료, sheets_manager 없음"

    def _reset_roster_cache(self) -> str:
        """명단 캐시 리셋"""
        # 기존 캐시 삭제
        self.cache_manager.invalidate_roster_data()
        self.cache_manager.invalidate_all_users_data()

        # 사용자 캐시도 삭제 (user: 접두사)
        user_keys = [k for k in self.cache_manager.user_cache.get_keys() if k.startswith('user:')]
        for key in user_keys:
            self.cache_manager.user_cache.delete(key)

        # 새 데이터 로드
        if self.sheets_manager:
            try:
                roster_data = self.sheets_manager.get_roster_data(use_cache=False)

                # TTL 포함 명단 데이터 캐시
                self.cache_manager.cache_roster_data(roster_data)

                # all_users_data 캐시는 사용하지 않음 (표준 캐시로 일원화)

                return f"명단 캐시 리셋에 성공했습니다."
            except Exception as e:
                logger.error(f"명단 데이터 로드 실패: {e}")
                return f"⚠️ 명단 캐시 삭제 완료, 데이터 로드 실패: {str(e)}"
        else:
            return "⚠️ 명단 캐시 삭제 완료, sheets_manager 없음"

    def _reset_fortune_cache(self) -> str:
        """운세 캐시 리셋"""
        # 운세 문구 캐시 삭제
        self.cache_manager.command_cache.delete("fortune_phrases")
        self.cache_manager.command_cache.delete("fortune_phrases_with_ttl")

        # 오늘의 운세 캐시 삭제 (fortune: 접두사)
        fortune_keys = [k for k in self.cache_manager.user_cache.get_keys() if k.startswith('fortune:')]
        deleted_count = 0
        for key in fortune_keys:
            if self.cache_manager.user_cache.delete(key):
                deleted_count += 1

        # 새 데이터 로드
        if self.sheets_manager:
            try:
                fortune_phrases = self.sheets_manager.get_fortune_phrases()

                # TTL 포함 캐시 저장 (1시간)
                ttl_seconds = 3600
                self.cache_manager.cache_fortune_phrases(fortune_phrases, ttl_seconds=ttl_seconds)

                return f"운세 캐시 리셋에 성공했습니다."
            except Exception as e:
                logger.error(f"운세 데이터 로드 실패: {e}")
                return f"⚠️ 운세 캐시 삭제 완료, 데이터 로드 실패: {str(e)}"
        else:
            return f"⚠️ 운세 캐시 삭제 완료 ({deleted_count}개), sheets_manager 없음"

    def _reset_custom_cache(self) -> str:
        """커스텀 명령어 캐시 리셋"""
        try:
            # CustomCommandManager를 통한 캐시 무효화 (올바른 방법)
            from commands.default.custom_command import get_custom_command_manager

            custom_manager = get_custom_command_manager()
            if custom_manager:
                # 캐시 무효화 (general_cache에서 삭제)
                invalidate_result = custom_manager.invalidate_cache()

                if not invalidate_result:
                    logger.warning("커스텀 캐시 무효화가 False를 반환했지만 계속 진행")

                # 새 데이터 강제 로드 (공개 메서드 사용)
                # get_available_commands()는 내부적으로 _get_custom_commands()를 호출하여
                # 캐시가 비어있으면 시트에서 자동으로 로드함
                commands = custom_manager.get_available_commands()

                logger.info(f"커스텀 캐시 리셋 완료: {len(commands)}개 명령어 로드됨")
                return f"커스텀 캐시 리셋에 성공했습니다."
            else:
                return "⚠️ 커스텀 명령어 관리자를 찾을 수 없습니다."

        except Exception as e:
            logger.error(f"커스텀 명령어 캐시 리셋 실패: {e}", exc_info=True)
            return f"⚠️ 커스텀 명령어 캐시 리셋 실패: {str(e)}"

    def _reset_shop_cache(self) -> str:
        """상점 캐시 리셋"""
        # 기존 캐시 삭제
        self.cache_manager.command_cache.delete("item_data")
        self.cache_manager.command_cache.delete("shop_items")

        # 새 데이터 로드
        if self.sheets_manager:
            try:
                shop_data = self.sheets_manager.get_worksheet_data('상점')

                # 캐시 저장
                self.cache_manager.cache_item_data(shop_data, ttl_seconds=config.CACHE_TTL)

                return f"상점 캐시 리셋에 성공했습니다."
            except Exception as e:
                logger.error(f"상점 데이터 로드 실패: {e}")
                return f"⚠️ 상점 캐시 삭제 완료, 데이터 로드 실패: {str(e)}"
        else:
            return "⚠️ 상점 캐시 삭제 완료, sheets_manager 없음"

    def _reset_generic_worksheet_cache(self, worksheet_name: str) -> str:
        """
        하드코딩되지 않은 워크시트의 캐시를 범용으로 리셋

        Args:
            worksheet_name: 워크시트명

        Returns:
            str: 결과 메시지
        """
        # 워크시트 존재 여부 확인
        if self.sheets_manager:
            try:
                self.sheets_manager.spreadsheet.worksheet(worksheet_name)
            except gspread.exceptions.WorksheetNotFound:
                return "존재하지 않는 워크시트입니다. 시트를 다시 확인해 주세요."
            except Exception as e:
                logger.error(f"워크시트 '{worksheet_name}' 접근 중 오류: {e}")
                return f"워크시트 확인 중 오류가 발생했습니다: {str(e)}"

        # sheet_cache에서 해당 워크시트 캐시 삭제
        cache_key = f"sheet:{worksheet_name}"
        self.cache_manager.sheet_cache.delete(cache_key)

        # general_cache에서 해당 워크시트 관련 캐시 삭제
        general_keys = self.cache_manager.general_cache.get_keys(worksheet_name)
        for key in general_keys:
            self.cache_manager.general_cache.delete(key)

        # command_cache에서 해당 워크시트 관련 캐시 삭제
        command_keys = self.cache_manager.command_cache.get_keys(worksheet_name)
        for key in command_keys:
            self.cache_manager.command_cache.delete(key)

        # 새 데이터 로드하여 캐시 갱신
        if self.sheets_manager:
            try:
                data = self.sheets_manager.get_worksheet_data(worksheet_name)
                self.cache_manager.cache_worksheet_data(worksheet_name, data)
                return f"{worksheet_name} 캐시 리셋에 성공했습니다."
            except Exception as e:
                logger.error(f"{worksheet_name} 데이터 로드 실패: {e}")
                return f"⚠️ {worksheet_name} 캐시 삭제 완료, 데이터 로드 실패: {str(e)}"
        else:
            return f"⚠️ {worksheet_name} 캐시 삭제 완료, sheets_manager 없음"

    def _reset_all_caches(self) -> str:
        """모든 캐시 리셋"""
        results = []

        # 하드코딩된 시트 캐시 리셋
        for cache_type, worksheet_name in [
            ('help', '도움말'),
            ('roster', '명단'),
            ('fortune', '운세'),
            ('custom', '커스텀'),
            ('shop', '상점')
        ]:
            try:
                result = self._reset_specific_cache(cache_type, worksheet_name)
                first_line = result.split('\n')[0]
                results.append(f"• {worksheet_name}: {first_line}")
            except Exception as e:
                results.append(f"• {worksheet_name}: ❌ 실패 ({str(e)})")

        # sheet_cache에 있는 동적 워크시트 캐시도 모두 리셋
        hardcoded_sheet_keys = {
            f"sheet:{name}" for name in self.WORKSHEET_CACHE_MAP.keys()
            if name != '전체'
        }
        # roster_data_with_ttl, all_users_data 등 전용 키도 제외
        known_sheet_cache_keys = hardcoded_sheet_keys | {'roster_data_with_ttl', 'all_users_data'}

        all_sheet_keys = self.cache_manager.sheet_cache.get_keys()
        dynamic_keys = [k for k in all_sheet_keys if k not in known_sheet_cache_keys]

        for key in dynamic_keys:
            self.cache_manager.sheet_cache.delete(key)
            if key.startswith('sheet:'):
                sheet_name = key[len('sheet:'):]
                results.append(f"• {sheet_name}: 캐시 삭제 완료")

        # 스프레드시트 매니저별 TTL 캐시(조사 시트 포함)도 비운다
        manager_results = self._reset_sheet_managers()
        results.extend(manager_results)

        lines = ["전체 캐시 리셋에 성공했습니다."]
        if manager_results:
            lines.extend(manager_results)
        return "\n".join(lines)

    def get_supported_keywords(self) -> List[str]:
        """
        지원되는 모든 키워드 목록 반환 (첫 번째가 대표 키워드)
        """
        return ['캐시 리셋', '캐시리셋', 'cache reset']


# 명령어 인스턴스 생성 함수
def create_cache_reset_command(sheets_manager=None, **kwargs):
    """캐시 리셋 명령어 인스턴스 생성"""
    return CacheResetCommand(sheets_manager=sheets_manager, **kwargs)

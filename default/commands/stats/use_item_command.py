"""
아이템 사용 명령어 구현
사용자의 소지품에서 아이템을 차감하고 스탯을 변화시킵니다.
"""

import os
import sys
import json
import ast
import time
import re
import random
from typing import List, Dict, Any, Optional, Tuple

# 경로 설정 (VM 환경 대응)
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

try:
    from config.settings import config
    from utils.logging_config import logger
    from utils.error_handling import CommandError, SheetAccessError
    from utils.cache_manager import bot_cache
    from utils.korean_utils import add_eul_reul, format_korean
    from utils.store_helpers import parse_inventory_string, serialize_inventory
    from utils.lock_manager import get_lock_manager
    from utils.dice_parser import (
        is_dice_expression as dice_is_expression,
        parse_and_roll_dice as dice_parse_and_roll,
    )
    from commands.base_command import BaseCommand, CommandContext, CommandResponse
    from commands.registry import register_command
    from models.user import User, create_empty_user
except ImportError as e:
    import logging
    logger = logging.getLogger('use_item')
    logger.error(f"필수 모듈 임포트 실패: {e}")
    raise


@register_command(
    name="사용",
    aliases=["use", "사용하기"],
    description="아이템을 사용합니다.",
    category="아이템",
    examples=["[사용/사과]", "[사용/반지]"],
    requires_sheets=True,
    requires_api=False
)
class UseItemCommand(BaseCommand):
    """
    아이템 사용 명령어 클래스

    소지품에서 아이템을 차감하고 스탯을 변화시킵니다.

    지원하는 형식:
    - [사용/아이템명] : 해당 아이템 1개 사용
    """

    @staticmethod
    def get_supported_keywords() -> List[str]:
        """지원 키워드 (대표 우선)"""
        return ['사용', 'use', '사용하기']

    def __init__(self, sheets_manager=None, api=None, **kwargs):
        """
        UseItemCommand 초기화

        Args:
            sheets_manager: Google Sheets 관리자
            api: 마스토돈 API 인스턴스
            **kwargs: 추가 의존성
        """
        super().__init__(sheets_manager, api, **kwargs)
        logger.debug(f"UseItemCommand 초기화 완료: sheets_manager={self.sheets_manager is not None}")

    def execute(self, context: CommandContext) -> CommandResponse:
        """
        아이템 사용 명령어 실행

        Args:
            context: 명령어 실행 컨텍스트

        Returns:
            CommandResponse: 실행 결과
        """
        # 동시성 제어: 사용자별 락 획득
        lock_manager = get_lock_manager()

        with lock_manager.acquire_lock(context.user_id, timeout=10.0) as acquired:
            if not acquired:
                return CommandResponse.create_error(
                    "다른 아이템 사용 처리가 진행 중입니다. 잠시 후 다시 시도해 주세요."
                )

            try:
                # 1. 키워드 파싱
                item_name = self._parse_use_keywords(context.keywords)

                # 2. 명단에서 사용자 확인
                user_info = self._validate_user(context.user_id)
                if not user_info:
                    return CommandResponse.create_error(
                        "명단에서 사용자 정보를 찾을 수 없습니다. 명단에 등록되어 있는지 확인해 주세요."
                    )

                # 3. 상점 캐시에서 아이템 찾기 (띄어쓰기 무시)
                item_info = self._find_item_in_shop(item_name)
                if not item_info:
                    # 조사 처리
                    item_with_josa = format_korean("{item}{은는}", item=item_name)
                    error_msg = f"'{item_with_josa}' 등록되지 않은 아이템입니다."
                    return CommandResponse.create_error(error_msg)

                # 4. 사용 가능 여부 확인
                if not self._check_item_usable(item_info):
                    item_with_josa = format_korean("{item}{은는}", item=item_info['original_name'])
                    error_msg = f"'{item_with_josa}' 사용할 수 없는 아이템입니다."
                    return CommandResponse.create_error(error_msg)

                # 5. 관리 워크시트에서 사용자 데이터 조회
                user_data = self._get_user_management_data(context.user_id)
                if not user_data:
                    return CommandResponse.create_error(
                        f"{user_info['name']} 님의 정보를 관리 워크시트에서 찾을 수 없습니다."
                    )

                # 6. 소지품에 아이템이 있는지 확인 (정규화된 이름으로)
                has_item, inventory, matched_item_name = self._check_inventory_has_item(
                    user_data, item_info['original_name']
                )
                if not has_item:
                    item_with_josa = format_korean("{item}{을를}", item=item_info['original_name'])
                    error_msg = f"'{item_with_josa}' 소지하고 있지 않습니다."
                    return CommandResponse.create_error(error_msg)

                # 7. 스탯과 헤더 검증
                is_valid, stat_name, stat_value, dice_expr, error_detail = self._validate_stat_and_header(
                    item_info, user_data['header_row']
                )
                if not is_valid:
                    return CommandResponse.create_error(
                        f"관리 혹은 상점 워크시트 형식에 오류가 있습니다.\n"
                        f"상세: {error_detail}\n"
                        f"운영진에게 문의하세요."
                    )

                # 다이스 표현식이면 굴리기
                dice_detail = None
                if dice_expr:
                    try:
                        stat_value, dice_detail = self._parse_and_roll_dice(dice_expr)
                        logger.debug(f"다이스 굴림 결과: {dice_detail}")
                    except Exception as e:
                        logger.error(f"다이스 굴림 중 오류: {e}", exc_info=True)
                        return CommandResponse.create_error(
                            f"다이스 굴림 중 오류가 발생했습니다: {str(e)}"
                        )

                # 8. 스탯 변화 계산
                current_stat, new_stat = self._calculate_stat_change(user_data, stat_name, stat_value)

                # 9. 시트 업데이트 (트랜잭션: 소지품 차감 + 스탯 변경)
                success = self._update_user_data_transactional(
                    context.user_id,
                    user_data,
                    matched_item_name,  # 실제 인벤토리에 있는 이름 사용
                    inventory,
                    stat_name,
                    new_stat
                )

                if not success:
                    return CommandResponse.create_error(
                        "아이템 사용 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."
                    )

                # 10. 캐시 무효화
                self._invalidate_management_cache(context.user_id)

                # 11. 결과 메시지 생성
                message = self._format_result_message(
                    item_info['original_name'],
                    item_info.get('usage_message', ''),
                    stat_name,
                    stat_value,
                    new_stat,
                    dice_detail
                )

                return CommandResponse.create_success(message, data={
                    'user_id': context.user_id,
                    'user_name': user_info['name'],
                    'item_name': item_info['original_name'],
                    'stat_name': stat_name,
                    'stat_change': stat_value,
                    'new_stat': new_stat
                })

            except SheetAccessError as e:
                # Google Sheets API 오류
                logger.error(f"Google Sheets API 오류: {e}", exc_info=True)
                return CommandResponse.create_error(
                    "일시적인 시스템 오류가 발생했습니다. 잠시 후 다시 시도해주세요.\n"
                    "(Google Sheets API 연결 문제)\n"
                    "※ 아이템 사용 처리 중 오류가 발생한 경우 관리자에게 문의해주세요.",
                    error=e
                )
            except CommandError as e:
                # 비즈니스 예외
                return CommandResponse.create_error(str(e), error=e)
            except Exception as e:
                # 시스템 예외
                logger.error(f"아이템 사용 명령어 실행 오류: {e}", exc_info=True)
                return CommandResponse.create_error(
                    "아이템 사용 중 오류가 발생했습니다.",
                    error=e
                )

    def _parse_use_keywords(self, keywords: List[str]) -> str:
        """
        사용 키워드 파싱

        Args:
            keywords: 키워드 리스트

        Returns:
            str: 아이템명

        Raises:
            CommandError: 파싱 실패
        """
        if len(keywords) < 2:
            raise CommandError("사용할 아이템명을 입력해 주세요.\n사용법: [사용/아이템명]")

        item_name = keywords[1].strip()
        if not item_name:
            raise CommandError("아이템명이 비어있습니다.")

        return item_name

    def _normalize_item_name(self, item_name: str) -> str:
        """
        아이템명 정규화 (띄어쓰기 제거)

        Args:
            item_name: 원본 아이템명

        Returns:
            str: 정규화된 아이템명 (띄어쓰기 제거)
        """
        return item_name.replace(' ', '').strip()

    def _find_item_in_shop(self, item_name: str) -> Optional[Dict[str, Any]]:
        """
        상점 캐시에서 아이템 찾기 (띄어쓰기 무시 매칭)

        Args:
            item_name: 검색할 아이템명

        Returns:
            Optional[Dict]: 아이템 정보 또는 None
        """
        try:
            # 상점 데이터 조회 (캐시 우선)
            shop_data = bot_cache.get_item_data()
            if not shop_data:
                shop_data = self.sheets_manager.get_worksheet_data('상점', use_cache=True)
                if shop_data:
                    bot_cache.cache_item_data(shop_data, ttl_seconds=config.CACHE_TTL)

            if not shop_data:
                logger.warning("상점 데이터가 없습니다.")
                return None

            # 정규화된 아이템명으로 매칭
            normalized_input = self._normalize_item_name(item_name)

            for row in shop_data:
                shop_item_name = str(row.get('아이템명', '')).strip()
                normalized_shop_name = self._normalize_item_name(shop_item_name)

                if normalized_input == normalized_shop_name:
                    item_info = {
                        'original_name': shop_item_name,  # 원본 이름
                        'price': row.get('가격'),
                        'description': row.get('설명'),
                        'usage_message': row.get('사용문구', ''),  # 사용문구
                        'stat': row.get('스탯'),
                        'value': row.get('수치')
                    }
                    logger.debug(f"상점에서 아이템 찾음: {shop_item_name}, 스탯={item_info['stat']}, 수치={item_info['value']}")
                    return item_info

            logger.debug(f"상점에서 아이템을 찾지 못함: {item_name}")
            return None

        except Exception as e:
            logger.error(f"상점 조회 중 오류: {e}", exc_info=True)
            return None

    def _check_item_usable(self, item_info: Dict[str, Any]) -> bool:
        """
        아이템 사용 가능 여부 확인

        Args:
            item_info: 아이템 정보

        Returns:
            bool: 사용 가능 여부
        """
        stat = str(item_info.get('stat', '')).strip()

        if stat == '사용 불가':
            return False

        return True

    def _validate_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        """
        명단에서 사용자 확인

        Args:
            user_id: 사용자 ID

        Returns:
            Optional[Dict]: 사용자 정보 {'name': str, 'suffix': str} 또는 None
        """
        try:
            # 명단 캐시에서 사용자 조회
            roster_data = self.sheets_manager.get_roster_data(use_cache=True)

            if not roster_data:
                logger.warning("명단 데이터가 없습니다.")
                return None

            for row in roster_data:
                if str(row.get('아이디', '')).strip() == user_id:
                    user_name = str(row.get('이름', user_id)).strip()
                    suffix_val = str(row.get('은는', '은')).strip()
                    suffix = suffix_val if suffix_val in ['은', '는'] else '은'

                    logger.debug(f"명단에서 사용자 {user_id} 찾음: {user_name}")
                    return {
                        'name': user_name,
                        'suffix': suffix
                    }

            logger.info(f"명단에서 사용자 {user_id}를 찾지 못함")
            return None

        except Exception as e:
            logger.error(f"명단 조회 중 오류: {e}", exc_info=True)
            return None

    def _get_user_management_data(self, user_id: str) -> Optional[Dict[str, Any]]:
        """
        관리 워크시트에서 사용자 데이터 조회

        Args:
            user_id: 사용자 ID

        Returns:
            Optional[Dict]: 사용자 데이터 {'row_index': int, 'header_row': list, 'data': dict} 또는 None
        """
        try:
            # 관리 워크시트 조회 (캐시 사용 안 함)
            management_data = self.sheets_manager.get_worksheet_data('관리', use_cache=False)

            if not management_data:
                logger.warning("관리 워크시트 데이터가 없습니다.")
                return None

            # 헤더행 추출
            worksheet = self.sheets_manager.get_worksheet('관리')
            header_row = worksheet.row_values(1)

            # 사용자 행 찾기
            # _row_number는 get_worksheet_data()가 심어준 실제 시트 행 번호
            # (빈 행이 중간에 있어도 정확한 행 번호를 보장)
            for row in management_data:
                if str(row.get('아이디', '')).strip() == user_id:
                    actual_row = row['_row_number']
                    logger.debug(f"관리 워크시트에서 사용자 {user_id} 찾음 (행: {actual_row})")
                    return {
                        'row_index': actual_row,
                        'header_row': header_row,
                        'data': row
                    }

            logger.warning(f"관리 워크시트에서 사용자 {user_id}를 찾지 못함")
            return None

        except Exception as e:
            logger.error(f"관리 워크시트 조회 중 오류: {e}", exc_info=True)
            return None

    def _check_inventory_has_item(self, user_data: Dict[str, Any], item_name: str) -> Tuple[bool, Dict[str, int], str]:
        """
        소지품에 아이템이 1개 이상 있는지 확인 (정규화된 이름으로 매칭)

        Args:
            user_data: 사용자 데이터
            item_name: 아이템명 (원본)

        Returns:
            Tuple[bool, Dict, str]: (보유 여부, 인벤토리 딕셔너리, 실제 매칭된 아이템명)
        """
        try:
            # 소지품 파싱
            inventory_str = str(user_data['data'].get('소지품', '')).strip()

            # 파싱 시도
            try:
                inventory = self._parse_dict_value(inventory_str)
            except Exception as parse_error:
                logger.error(f"소지품 파싱 실패: inventory_str='{inventory_str}', error={parse_error}")
                raise CommandError(
                    f"소지품 데이터 형식이 올바르지 않습니다.\n"
                    f"관리 워크시트의 소지품 컬럼을 확인해주세요."
                )

            # 정규화된 이름으로 매칭
            normalized_search = self._normalize_item_name(item_name)

            for inv_item_name, count in inventory.items():
                normalized_inv = self._normalize_item_name(inv_item_name)

                if normalized_search == normalized_inv and count >= 1:
                    logger.debug(f"소지품에 {inv_item_name} {count}개 보유 (정규화 매칭)")
                    return True, inventory, inv_item_name  # 실제 인벤토리에 있는 이름 반환

            logger.debug(f"소지품에 {item_name} 없음 (정규화 매칭 실패)")
            return False, inventory, ""

        except CommandError:
            raise
        except Exception as e:
            logger.error(f"소지품 확인 중 오류: {e}", exc_info=True)
            raise CommandError(f"소지품 확인 중 오류가 발생했습니다: {str(e)}")

    def _parse_dict_value(self, value_str: str) -> Dict[str, int]:
        """
        딕셔너리 문자열 파싱 (store_helpers 공통 함수 사용, None 안전 처리)

        Args:
            value_str: 딕셔너리 문자열

        Returns:
            Dict[str, int]: 파싱된 딕셔너리
        """
        result = parse_inventory_string(value_str)
        if result is None:
            logger.warning(f"인벤토리 파싱 실패, 빈 인벤토리로 처리: {value_str[:50]}...")
            return {}
        return result

    def _is_dice_expression(self, value_str: str) -> bool:
        """다이스 표현식 여부 확인 (`utils.dice_parser` 래퍼)."""
        return dice_is_expression(value_str)

    def _parse_and_roll_dice(self, dice_expression: str) -> Tuple[int, str]:
        """다이스 표현식을 굴려 결과를 반환 (`utils.dice_parser` 래퍼)."""
        return dice_parse_and_roll(dice_expression)

    def _validate_stat_and_header(self, item_info: Dict[str, Any], header_row: List[str]) -> Tuple[bool, str, int, Optional[str], str]:
        """
        스탯과 헤더 검증 (상세 오류 메시지 포함)

        Args:
            item_info: 아이템 정보
            header_row: 헤더 행

        Returns:
            Tuple[bool, str, int, Optional[str], str]: (유효성, 스탯명, 수치, 다이스표현식, 오류상세)
        """
        try:
            stat_name = str(item_info.get('stat', '')).strip()
            value_str = str(item_info.get('value', '')).strip()

            # 스탯명 검증
            if not stat_name:
                error_detail = "상점 시트의 '스탯' 컬럼이 비어있습니다."
                logger.error(error_detail)
                return False, "오류", 0, None, error_detail

            # 수치 검증
            if not value_str:
                error_detail = f"상점 시트의 '수치' 컬럼이 비어있습니다. (스탯: {stat_name})"
                logger.error(error_detail)
                return False, "오류", 0, None, error_detail

            # 다이스 표현식 여부 확인
            is_dice = self._is_dice_expression(value_str)

            if is_dice:
                # 다이스면 일단 0으로 반환 (실제 굴림은 나중에)
                # 헤더에 스탯 존재 여부 확인
                if stat_name not in header_row:
                    error_detail = f"관리 시트 헤더에 '{stat_name}' 컬럼이 존재하지 않습니다."
                    logger.error(error_detail)
                    return False, "오류", 0, None, error_detail

                logger.debug(f"스탯 검증 완료 (다이스): {stat_name} {value_str}")
                return True, stat_name, 0, value_str, ""  # (유효성, 스탯명, 임시값, 다이스표현식, 오류상세)
            else:
                # 고정값
                try:
                    # 디버깅: 파싱 전 값 로깅
                    logger.debug(f"스탯 수치 파싱 시도: value_str='{value_str}' (type: {type(value_str).__name__})")
                    stat_value = int(float(value_str))
                    logger.debug(f"스탯 수치 파싱 성공: stat_value={stat_value} (type: {type(stat_value).__name__})")
                except (ValueError, TypeError) as e:
                    error_detail = f"상점 시트의 '수치' 컬럼 값을 숫자로 변환할 수 없습니다. (값: '{value_str}', 오류: {e})"
                    logger.error(error_detail)
                    return False, "오류", 0, None, error_detail

                # 헤더에 스탯 존재 여부 확인
                if stat_name not in header_row:
                    error_detail = f"관리 시트 헤더에 '{stat_name}' 컬럼이 존재하지 않습니다."
                    logger.error(error_detail)
                    return False, "오류", 0, None, error_detail

                logger.debug(f"스탯 검증 완료: {stat_name} {stat_value:+d}")
                return True, stat_name, stat_value, None, ""

        except Exception as e:
            error_detail = f"스탯 검증 중 예상치 못한 오류: {str(e)}"
            logger.error(f"{error_detail}", exc_info=True)
            return False, "오류", 0, None, error_detail

    def _calculate_stat_change(self, user_data: Dict[str, Any], stat_name: str, stat_value: int) -> Tuple[int, int]:
        """
        스탯 변화 계산

        Args:
            user_data: 사용자 데이터
            stat_name: 스탯명
            stat_value: 변화량

        Returns:
            Tuple[int, int]: (현재 스탯, 새로운 스탯)
        """
        try:
            # 현재 스탯 값
            current_stat_raw = user_data['data'].get(stat_name, 0)
            logger.debug(f"시트에서 읽은 현재 스탯: {stat_name}='{current_stat_raw}' (type: {type(current_stat_raw).__name__})")

            try:
                current_stat = int(float(current_stat_raw))
                logger.debug(f"현재 스탯 파싱 성공: {current_stat} (type: {type(current_stat).__name__})")
            except (ValueError, TypeError) as e:
                logger.warning(f"현재 스탯 파싱 실패: '{current_stat_raw}', 오류={e}. 0으로 설정")
                current_stat = 0

            # 새로운 스탯 값
            logger.debug(f"스탯 변화량: {stat_value} (type: {type(stat_value).__name__})")
            new_stat = current_stat + stat_value
            logger.debug(f"스탯 변화 계산 완료: {stat_name} {current_stat} + ({stat_value}) = {new_stat}")

            return current_stat, new_stat

        except Exception as e:
            logger.error(f"스탯 계산 중 오류: {e}", exc_info=True)
            return 0, 0

    def _update_user_data_transactional(
        self,
        user_id: str,
        user_data: Dict[str, Any],
        item_name: str,
        inventory: Dict[str, int],
        stat_name: str,
        new_stat: int
    ) -> bool:
        """
        사용자 데이터 트랜잭션 업데이트 (소지품 차감 + 스탯 변경)
        batch_update를 사용하여 원자적으로 업데이트

        Args:
            user_id: 사용자 ID
            user_data: 사용자 데이터
            item_name: 아이템명 (실제 인벤토리에 있는 이름)
            inventory: 현재 인벤토리
            stat_name: 스탯명
            new_stat: 새로운 스탯 값

        Returns:
            bool: 업데이트 성공 여부
        """
        try:
            # 소지품에서 아이템 1개 차감
            new_inventory = inventory.copy()
            new_inventory[item_name] -= 1
            if new_inventory[item_name] <= 0:
                del new_inventory[item_name]

            # 단순 텍스트 형식으로 변환
            inventory_str = serialize_inventory(new_inventory)

            # 컬럼 인덱스 찾기
            header_row = user_data['header_row']
            row_index = user_data['row_index']

            # 소지품 컬럼
            try:
                inventory_col = header_row.index('소지품') + 1
            except ValueError:
                logger.error("소지품 컬럼을 찾을 수 없습니다.")
                return False

            # 스탯 컬럼
            try:
                stat_col = header_row.index(stat_name) + 1
            except ValueError:
                logger.error(f"스탯 컬럼 '{stat_name}'을 찾을 수 없습니다.")
                return False

            # batch update로 트랜잭션 업데이트
            return self._batch_update_with_retry(
                row_index,
                inventory_col,
                inventory_str,
                stat_col,
                new_stat
            )

        except Exception as e:
            logger.error(f"사용자 데이터 업데이트 준비 중 오류: {e}", exc_info=True)
            return False

    def _batch_update_with_retry(
        self,
        row_index: int,
        inventory_col: int,
        inventory_str: str,
        stat_col: int,
        new_stat: int
    ) -> bool:
        """
        배치 업데이트로 여러 셀을 원자적으로 업데이트 (재시도 포함)

        재시도 규칙: 10초 -> 20초 -> 20초

        Args:
            row_index: 행 인덱스
            inventory_col: 소지품 컬럼
            inventory_str: 소지품 직렬화 문자열
            stat_col: 스탯 컬럼
            new_stat: 새로운 스탯 값

        Returns:
            bool: 업데이트 성공 여부
        """
        retry_delays = [10, 20, 20]  # 초 단위

        for i, delay in enumerate(retry_delays):
            try:
                logger.debug(f"배치 업데이트 시도 {i+1}/{len(retry_delays)}")

                # batch_update로 한 번에 업데이트 (트랜잭션)
                updates = [
                    (row_index, inventory_col, inventory_str),
                    (row_index, stat_col, new_stat)
                ]

                logger.debug(
                    f"배치 업데이트 준비: row={row_index}, "
                    f"inventory_col={inventory_col}, stat_col={stat_col}"
                )

                success = self.sheets_manager.batch_update_cells('관리', updates)

                if success:
                    logger.info(f"배치 업데이트 성공: 행 {row_index}")
                    return True
                else:
                    # 실패하면 대기 후 재시도
                    if i < len(retry_delays) - 1:
                        logger.warning(f"배치 업데이트 실패, {delay}초 후 재시도...")
                        time.sleep(delay)
                        continue
                    else:
                        logger.error("모든 재시도 실패")
                        return False

            except Exception as e:
                logger.error(f"배치 업데이트 중 예외 발생 (시도 {i+1}): {e}", exc_info=True)
                if i < len(retry_delays) - 1:
                    logger.warning(f"{delay}초 후 재시도...")
                    time.sleep(delay)
                    continue
                else:
                    # 마지막 시도도 실패
                    logger.error("모든 재시도 실패 (예외)")
                    return False

        return False

    def _invalidate_management_cache(self, user_id: str):
        """
        관리 시트 캐시 무효화

        Args:
            user_id: 사용자 ID
        """
        try:
            # 관리 시트 데이터는 캐시하지 않지만, 관련 캐시가 있다면 무효화
            # 명단 캐시는 건드리지 않음 (명단은 변경되지 않았으므로)
            logger.debug(f"관리 시트 캐시 무효화 (사용자: {user_id})")

            # 사용자별 캐시가 있다면 삭제
            bot_cache.command_cache.delete(f"user_management_{user_id}")

        except Exception as e:
            logger.warning(f"캐시 무효화 중 오류 (무시): {e}")

    def _format_result_message(
        self,
        item_name: str,
        usage_message: str,
        stat_name: str,
        stat_value: int,
        new_stat: int,
        dice_detail: Optional[str] = None
    ) -> str:
        """
        결과 메시지 생성

        Args:
            item_name: 아이템명
            usage_message: 사용문구
            stat_name: 스탯명
            stat_value: 변화량
            new_stat: 새로운 스탯 값
            dice_detail: 다이스 상세 정보 (선택)

        Returns:
            str: 포맷된 메시지
        """
        message_parts = []

        # 사용문구가 있으면 맨 위에 출력, 없으면 기본 메시지
        if usage_message and usage_message.strip():
            message_parts.append(usage_message.strip())
        else:
            # 사용문구가 없으면 기본 메시지
            item_with_particle = add_eul_reul(item_name)
            message_parts.append(f"{item_with_particle} 사용했다.")

        message_parts.append("")  # 빈 줄 추가

        # 아이템 소모 정보
        message_parts.append(f"➭ {item_name} 소모")

        # 스탯 변화 정보
        if dice_detail:
            message_parts.append(f"➭ {stat_name} {dice_detail}")
        else:
            # 고정값
            if stat_value > 0:
                message_parts.append(f"➭ {stat_name} +{stat_value}")
            else:
                message_parts.append(f"➭ {stat_name} {stat_value}")

        # 현재 스탯 정보
        message_parts.append(f"➭ 현재 {stat_name} {new_stat}")

        return '\n'.join(message_parts)


# 유틸리티 함수

def is_use_item_command(keyword: str) -> bool:
    """
    키워드가 아이템 사용 명령어인지 확인

    Args:
        keyword: 확인할 키워드

    Returns:
        bool: 아이템 사용 명령어 여부
    """
    if not keyword:
        return False

    keyword = keyword.lower().strip()
    return keyword in ['사용', 'use', '사용하기']


def create_use_item_command(sheets_manager=None) -> UseItemCommand:
    """
    아이템 사용 명령어 인스턴스 생성

    Args:
        sheets_manager: Google Sheets 관리자

    Returns:
        UseItemCommand: 아이템 사용 명령어 인스턴스
    """
    return UseItemCommand(sheets_manager)

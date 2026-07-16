"""
스탯 변경 관리자 명령어 구현
캐릭터의 스탯을 변경하는 관리자 전용 명령어입니다.
"""

import os
import sys
import time
from typing import List, Dict, Any, Optional, Tuple

# 경로 설정 (VM 환경 대응)
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

try:
    from config.settings import config
    from utils.logging_config import logger
    from utils.error_handling import CommandError, SheetAccessError
    from commands.base_command import BaseCommand, CommandContext, CommandResponse
    from commands.registry import register_command
except ImportError as e:
    import logging
    logger = logging.getLogger('stat_admin_command')
    logger.error(f"필수 모듈 임포트 실패: {e}")
    raise


@register_command(
    name="스탯 변경",
    aliases=["stat", "스탯변경", "stat change", "변경"],
    description="캐릭터의 스탯을 변경합니다. (관리자 전용)",
    category="관리자",
    examples=["[최대 체력 변경/+10/릴리]", "[정신력 변경/-5/철수]", "[감화도 변경/3/영희]"],
    requires_sheets=True,
    requires_api=False,
    admin_only=True
)
class StatAdminCommand(BaseCommand):
    """
    스탯 변경 관리자 명령어 클래스

    캐릭터의 스탯을 변경하는 관리자 전용 명령어입니다.

    지원하는 형식:
    - [스탯명 변경/수치/캐릭터명] : 해당 캐릭터의 스탯 변경
    - [최대 체력 변경/+10/릴리] : 릴리의 최대 체력 +10
    - [정신력변경/3/영희] : 영희의 정신력 +3 (띄어쓰기 없어도 OK)
    """

    @staticmethod
    def get_supported_keywords() -> List[str]:
        """지원 키워드 (대표 우선)"""
        return ['스탯 변경', 'stat', '스탯변경', 'stat change', '변경']

    def __init__(self, sheets_manager=None, api=None, **kwargs):
        """
        StatAdminCommand 초기화

        Args:
            sheets_manager: Google Sheets 관리자
            api: 마스토돈 API 인스턴스
            **kwargs: 추가 의존성
        """
        super().__init__(sheets_manager, api, **kwargs)
        logger.debug(f"StatAdminCommand 초기화 완료: sheets_manager={self.sheets_manager is not None}")

    def execute(self, context: CommandContext) -> CommandResponse:
        """
        스탯 변경 명령어 실행

        Args:
            context: 명령어 실행 컨텍스트

        Returns:
            CommandResponse: 실행 결과
        """
        try:
            # 1. 키워드 파싱
            parsed = self._parse_stat_change_keywords(context.keywords)
            stat_name = parsed['stat_name']
            value = parsed['value']
            character_name = parsed['character_name']

            logger.debug(f"스탯 변경 요청: {character_name}의 {stat_name} {value:+d}")

            # 2. "전원" 처리 분기
            if character_name == "전원":
                return self._process_all_characters(stat_name, value)
            else:
                return self._process_single_character(character_name, stat_name, value)

        except SheetAccessError as e:
            # Google Sheets API 오류
            logger.error(f"Google Sheets API 오류: {e}", exc_info=True)
            return CommandResponse.create_error(
                "일시적인 시스템 오류가 발생했습니다. 잠시 후 다시 시도해주세요.\n"
                "(Google Sheets API 연결 문제)",
                error=e
            )
        except CommandError as e:
            # 비즈니스 예외
            return CommandResponse.create_error(str(e), error=e)
        except Exception as e:
            # 시스템 예외
            logger.error(f"스탯 변경 명령어 실행 오류: {e}", exc_info=True)
            return CommandResponse.create_error(
                "스탯 변경 중 오류가 발생했습니다.",
                error=e
            )

    def _process_single_character(
        self,
        character_name: str,
        stat_name: str,
        value: int
    ) -> CommandResponse:
        """
        단일 캐릭터의 스탯 변경 처리

        Args:
            character_name: 캐릭터명
            stat_name: 스탯명
            value: 변경값

        Returns:
            CommandResponse: 실행 결과
        """
        # 1. 명단에서 캐릭터 확인
        character_info = self._validate_character(character_name)
        if not character_info:
            return CommandResponse.create_error(
                f"'{character_name}' 캐릭터를 명단에서 찾을 수 없습니다."
            )

        # 2. 관리 워크시트에서 캐릭터 데이터 조회
        character_data = self._get_character_management_data(character_info['name'])
        if not character_data:
            return CommandResponse.create_error(
                f"'{character_info['name']}' 캐릭터를 관리 워크시트에서 찾을 수 없습니다."
            )

        # 3. 헤더에서 스탯 컬럼 찾기
        stat_column_result = self._find_stat_column(stat_name, character_data['header_row'])
        if not stat_column_result:
            return CommandResponse.create_error(
                f"'{stat_name}' 스탯을 관리 워크시트에서 찾을 수 없습니다."
            )

        stat_column_index, stat_column_name = stat_column_result

        # 4. 현재 스탯 값 + 변경값 계산
        old_value, new_value = self._calculate_new_stat(
            character_data,
            stat_column_name,
            value
        )

        # 5. 시트 업데이트
        success = self._update_character_stat(
            character_data,
            stat_column_index,
            new_value
        )

        if not success:
            return CommandResponse.create_error(
                "스탯 변경 처리 중 오류가 발생했습니다. 2분 후 다시 시도해 주세요."
            )

        # 6. 결과 메시지 생성
        message = self._format_result_message(
            character_info['name'],
            stat_column_name,
            old_value,
            new_value,
            value
        )

        return CommandResponse.create_success(message, data={
            'character_name': character_info['name'],
            'stat_name': stat_column_name,
            'old_value': old_value,
            'new_value': new_value,
            'change_value': value
        })

    def _process_all_characters(
        self,
        stat_name: str,
        value: int
    ) -> CommandResponse:
        """
        전원(모든 캐릭터)의 스탯 변경 처리

        Args:
            stat_name: 스탯명
            value: 변경값

        Returns:
            CommandResponse: 실행 결과
        """
        try:
            # 1. 명단에서 모든 캐릭터 가져오기
            roster_data = self.sheets_manager.get_roster_data(use_cache=True)
            if not roster_data:
                return CommandResponse.create_error("명단 데이터를 가져올 수 없습니다.")

            # 2. 관리 워크시트 헤더 조회 (스탯 컬럼 찾기용)
            worksheet = self.sheets_manager.get_worksheet('관리')
            header_row = worksheet.row_values(1)

            stat_column_result = self._find_stat_column(stat_name, header_row)
            if not stat_column_result:
                return CommandResponse.create_error(
                    f"'{stat_name}' 스탯을 관리 워크시트에서 찾을 수 없습니다."
                )

            stat_column_index, stat_column_name = stat_column_result

            # 3. 모든 캐릭터에 대해 스탯 변경
            success_count = 0
            failed_count = 0
            results = []

            for row in roster_data:
                character_name = str(row.get('이름', '')).strip()
                if not character_name:
                    continue

                try:
                    # 캐릭터 데이터 조회
                    character_data = self._get_character_management_data(character_name)
                    if not character_data:
                        logger.warning(f"'{character_name}' 캐릭터를 관리 워크시트에서 찾을 수 없음")
                        failed_count += 1
                        continue

                    # 스탯 값 계산
                    old_value, new_value = self._calculate_new_stat(
                        character_data,
                        stat_column_name,
                        value
                    )

                    # 스탯 업데이트
                    success = self._update_character_stat(
                        character_data,
                        stat_column_index,
                        new_value
                    )

                    if success:
                        success_count += 1
                        results.append({
                            'name': character_name,
                            'old_value': old_value,
                            'new_value': new_value
                        })
                        logger.info(f"'{character_name}' 스탯 변경 성공: {old_value} → {new_value}")
                    else:
                        failed_count += 1
                        logger.warning(f"'{character_name}' 스탯 변경 실패")

                except Exception as e:
                    failed_count += 1
                    logger.error(f"'{character_name}' 처리 중 오류: {e}")

            # 4. 결과 메시지 생성
            if success_count == 0:
                return CommandResponse.create_error(
                    f"전원의 {stat_column_name} 변경에 실패했습니다."
                )

            change_str = f"+{value}" if value > 0 else f"{value}"
            message = f"""전원의 {stat_column_name}을 변경했습니다.
➭ 성공: {success_count}명 ({change_str})"""

            if failed_count > 0:
                message += f"\n➭ 실패: {failed_count}명"

            return CommandResponse.create_success(message, data={
                'stat_name': stat_column_name,
                'change_value': value,
                'success_count': success_count,
                'failed_count': failed_count,
                'results': results
            })

        except Exception as e:
            logger.error(f"전원 스탯 변경 중 오류: {e}", exc_info=True)
            return CommandResponse.create_error(
                f"전원 스탯 변경 중 오류가 발생했습니다: {str(e)}"
            )

    def _parse_stat_change_keywords(self, keywords: List[str]) -> Dict[str, Any]:
        """
        스탯 변경 키워드 파싱

        Args:
            keywords: 키워드 리스트

        Returns:
            Dict: {'stat_name': str, 'value': int, 'character_name': str}

        Raises:
            CommandError: 파싱 실패
        """
        if len(keywords) < 3:
            raise CommandError(
                "스탯 변경 명령어 형식이 올바르지 않습니다.\n"
                "사용법: [스탯명 변경/수치/캐릭터명]\n"
                "예시: [최대 체력 변경/+10/릴리]"
            )

        # 스탯명 파싱
        stat_name_raw = keywords[0].strip()
        stat_name = self._extract_stat_name(stat_name_raw)

        # 수치 파싱
        value_str = keywords[1].strip()
        value = self._parse_value(value_str)

        # 캐릭터명 파싱 (앞뒤 공백 제거)
        character_name = keywords[2].strip()

        if not stat_name:
            raise CommandError("스탯명이 비어있습니다.")

        if not character_name:
            raise CommandError("캐릭터명이 비어있습니다.")

        logger.debug(f"파싱 결과: stat_name={stat_name}, value={value}, character_name={character_name}")

        return {
            'stat_name': stat_name,
            'value': value,
            'character_name': character_name
        }

    def _extract_stat_name(self, stat_name_raw: str) -> str:
        """
        스탯명에서 "변경" 제거

        Args:
            stat_name_raw: 원본 스탯명 (예: "최대 체력 변경", "정신력변경")

        Returns:
            str: 정제된 스탯명 (예: "최대 체력", "정신력")
        """
        stat_name = stat_name_raw.strip()

        # " 변경" 또는 "변경"으로 끝나면 제거
        if stat_name.endswith(' 변경'):
            stat_name = stat_name[:-3]
        elif stat_name.endswith('변경'):
            stat_name = stat_name[:-2]

        return stat_name.strip()

    def _parse_value(self, value_str: str) -> int:
        """
        수치 파싱

        Args:
            value_str: 수치 문자열 (예: "+10", "-5", "3")

        Returns:
            int: 파싱된 수치

        Raises:
            CommandError: 파싱 실패
        """
        value_str = value_str.strip()

        try:
            # int()가 +/- 기호를 자동으로 처리
            value = int(value_str)
            return value
        except ValueError:
            raise CommandError(
                "수치 형식이 올바르지 않습니다. 숫자를 입력해 주세요."
            )

    def _normalize_for_matching(self, text: str) -> str:
        """
        매칭용 텍스트 정규화 (띄어쓰기 제거)

        Args:
            text: 원본 텍스트

        Returns:
            str: 정규화된 텍스트
        """
        return text.replace(' ', '').strip()

    def _validate_character(self, character_name: str) -> Optional[Dict[str, Any]]:
        """
        명단에서 캐릭터 확인 (띄어쓰기 무시)

        Args:
            character_name: 캐릭터명

        Returns:
            Optional[Dict]: 캐릭터 정보 {'name': str, 'user_id': str} 또는 None
        """
        try:
            # 명단 캐시에서 조회
            roster_data = self.sheets_manager.get_roster_data(use_cache=True)

            if not roster_data:
                logger.warning("명단 데이터가 없습니다.")
                return None

            # 정규화된 이름으로 매칭
            normalized_input = self._normalize_for_matching(character_name)

            for row in roster_data:
                roster_name = str(row.get('이름', '')).strip()
                normalized_roster = self._normalize_for_matching(roster_name)

                if normalized_input == normalized_roster:
                    logger.debug(f"명단에서 캐릭터 찾음: {roster_name}")
                    return {
                        'name': roster_name,  # 원본 이름
                        'user_id': str(row.get('아이디', '')).strip()
                    }

            logger.info(f"명단에서 캐릭터 {character_name}를 찾지 못함")
            return None

        except Exception as e:
            logger.error(f"명단 조회 중 오류: {e}", exc_info=True)
            return None

    def _get_character_management_data(self, character_name: str) -> Optional[Dict[str, Any]]:
        """
        관리 워크시트에서 캐릭터 데이터 조회

        Args:
            character_name: 캐릭터명 (원본)

        Returns:
            Optional[Dict]: 캐릭터 데이터 {'row_index': int, 'header_row': list, 'data': dict, 'name': str} 또는 None
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

            # 정규화된 이름으로 캐릭터 찾기
            # _row_number는 get_worksheet_data()가 심어준 실제 시트 행 번호
            # (빈 행이 중간에 있어도 정확한 행 번호를 보장)
            normalized_input = self._normalize_for_matching(character_name)

            for row in management_data:
                row_name = str(row.get('이름', '')).strip()
                normalized_row = self._normalize_for_matching(row_name)

                if normalized_input == normalized_row:
                    actual_row = row['_row_number']
                    logger.debug(f"관리 워크시트에서 캐릭터 찾음: {row_name} (행: {actual_row})")
                    return {
                        'row_index': actual_row,
                        'header_row': header_row,
                        'data': row,
                        'name': row_name
                    }

            logger.warning(f"관리 워크시트에서 캐릭터 {character_name}를 찾지 못함")
            return None

        except Exception as e:
            logger.error(f"관리 워크시트 조회 중 오류: {e}", exc_info=True)
            return None

    def _find_stat_column(self, stat_name: str, header_row: List[str]) -> Optional[Tuple[int, str]]:
        """
        헤더에서 스탯 컬럼 찾기 (띄어쓰기 무시)

        Args:
            stat_name: 스탯명
            header_row: 헤더 행

        Returns:
            Optional[Tuple[int, str]]: (컬럼 인덱스, 원본 헤더명) 또는 None
        """
        # 정규화된 스탯명
        normalized_stat = self._normalize_for_matching(stat_name)

        for i, header in enumerate(header_row):
            header_str = str(header).strip()
            normalized_header = self._normalize_for_matching(header_str)

            if normalized_stat == normalized_header:
                logger.debug(f"헤더에서 스탯 찾음: {header_str} (컬럼: {i+1})")
                return (i + 1, header_str)  # (컬럼 인덱스, 원본 헤더명)

        logger.warning(f"헤더에서 스탯 {stat_name}를 찾지 못함")
        return None

    def _calculate_new_stat(
        self,
        character_data: Dict[str, Any],
        stat_column_name: str,
        value: int
    ) -> Tuple[int, int]:
        """
        새로운 스탯 값 계산

        Args:
            character_data: 캐릭터 데이터
            stat_column_name: 스탯 컬럼명
            value: 변경값

        Returns:
            Tuple[int, int]: (현재 값, 새로운 값)
        """
        try:
            # 현재 스탯 값
            current_stat = character_data['data'].get(stat_column_name, 0)
            try:
                current_stat = int(float(current_stat))
            except (ValueError, TypeError):
                current_stat = 0

            # 새로운 스탯 값
            new_stat = current_stat + value

            logger.debug(f"스탯 계산: {current_stat} + {value} = {new_stat}")
            return (current_stat, new_stat)

        except Exception as e:
            logger.error(f"스탯 계산 중 오류: {e}", exc_info=True)
            return (0, value)

    def _update_character_stat(
        self,
        character_data: Dict[str, Any],
        stat_column_index: int,
        new_value: int
    ) -> bool:
        """
        캐릭터 스탯 업데이트 (재시도 로직)

        재시도 규칙: 10초 -> 20초 -> 20초

        Args:
            character_data: 캐릭터 데이터
            stat_column_index: 스탯 컬럼 인덱스
            new_value: 새로운 값

        Returns:
            bool: 업데이트 성공 여부
        """
        row_index = character_data['row_index']
        retry_delays = [10, 20, 20]  # 초 단위

        for i, delay in enumerate(retry_delays):
            try:
                logger.debug(f"업데이트 시도 {i+1}/{len(retry_delays)}")

                success = self.sheets_manager.update_cell(
                    '관리', row_index, stat_column_index, new_value
                )

                if success:
                    logger.info(f"스탯 업데이트 성공: 행 {row_index}, 컬럼 {stat_column_index}, 값 {new_value}")
                    return True
                else:
                    # 실패하면 대기 후 재시도
                    if i < len(retry_delays) - 1:
                        logger.warning(f"업데이트 실패, {delay}초 후 재시도...")
                        time.sleep(delay)
                        continue
                    else:
                        logger.error("모든 재시도 실패")
                        return False

            except Exception as e:
                logger.error(f"업데이트 중 예외 발생 (시도 {i+1}): {e}")
                if i < len(retry_delays) - 1:
                    logger.warning(f"{delay}초 후 재시도...")
                    time.sleep(delay)
                    continue
                else:
                    logger.error("모든 재시도 실패 (예외)")
                    return False

        return False

    def _format_result_message(
        self,
        character_name: str,
        stat_name: str,
        old_value: int,
        new_value: int,
        change_value: int
    ) -> str:
        """
        결과 메시지 생성

        Args:
            character_name: 캐릭터명
            stat_name: 스탯명
            old_value: 이전 값
            new_value: 새로운 값
            change_value: 변경값

        Returns:
            str: 포맷된 메시지
        """
        # 변화량 표시
        if change_value > 0:
            change_str = f"+{change_value}"
        else:
            change_str = f"{change_value}"

        message = f"""{character_name}의 {stat_name}을 변경했습니다.
➭ {old_value} → {new_value} ({change_str})"""

        return message


# 유틸리티 함수

def is_stat_admin_command(keyword: str) -> bool:
    """
    키워드가 스탯 변경 명령어인지 확인

    Args:
        keyword: 확인할 키워드

    Returns:
        bool: 스탯 변경 명령어 여부
    """
    if not keyword:
        return False

    keyword = keyword.lower().strip()
    return keyword in ['스탯 변경', 'stat', '스탯변경', 'stat change', '변경']


def create_stat_admin_command(sheets_manager=None) -> StatAdminCommand:
    """
    스탯 변경 명령어 인스턴스 생성

    Args:
        sheets_manager: Google Sheets 관리자

    Returns:
        StatAdminCommand: 스탯 변경 명령어 인스턴스
    """
    return StatAdminCommand(sheets_manager)

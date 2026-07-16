"""
상태 확인 명령어 구현
사용자의 스탯, 인벤토리, 소지금을 표시합니다.
"""

import os
import sys
import json
import ast
from typing import List, Dict, Any, Optional, Tuple

# 경로 설정 (VM 환경 대응)
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

try:
    from config.settings import config
    from utils.logging_config import logger
    from utils.error_handling import CommandError, SheetAccessError
    from utils.store_helpers import parse_inventory_string
    from commands.base_command import BaseCommand, CommandContext, CommandResponse
    from commands.registry import register_command
    from models.user import User, create_empty_user
except ImportError as e:
    import logging
    logger = logging.getLogger('peek_status')
    logger.error(f"필수 모듈 임포트 실패: {e}")
    raise


@register_command(
    name="상태 확인",
    aliases=["상태", "스탯", "status"],
    description="현재 상태를 확인합니다.",
    category="스탯",
    examples=["[상태 확인]", "[상태]"],
    requires_sheets=True,
    requires_api=False
)
class PeekStatusCommand(BaseCommand):
    """
    상태 확인 명령어 클래스

    .env의 PEEK_STATUS가 True일 때만 활성화되며,
    PEEK_HEADER에 지정된 헤더들만 표시합니다.

    지원하는 형식:
    - [상태 확인] : 현재 상태 확인
    - [상태] : 현재 상태 확인
    - [스탯] : 현재 상태 확인
    """

    @staticmethod
    def get_supported_keywords() -> List[str]:
        """지원 키워드 (대표 우선)"""
        return ['상태 확인', '상태', '스탯', 'status']

    def __init__(self, sheets_manager=None, api=None, **kwargs):
        """
        PeekStatusCommand 초기화

        Args:
            sheets_manager: Google Sheets 관리자
            api: 마스토돈 API 인스턴스
            **kwargs: 추가 의존성
        """
        super().__init__(sheets_manager, api, **kwargs)
        logger.debug(f"PeekStatusCommand 초기화 완료: sheets_manager={self.sheets_manager is not None}")

    def execute(self, context: CommandContext) -> CommandResponse:
        """
        상태 확인 명령어 실행

        Args:
            context: 명령어 실행 컨텍스트

        Returns:
            CommandResponse: 실행 결과
        """
        try:
            # 1. PEEK_STATUS 설정 확인
            if not self._check_enabled():
                return CommandResponse.create_error(
                    "상태 확인 기능이 비활성화되어 있습니다."
                )

            # 2. PEEK_HEADER 파싱
            headers = self._parse_peek_headers()
            if not headers:
                return CommandResponse.create_error(
                    "PEEK_HEADER 설정이 비어있습니다. .env 파일을 확인해주세요."
                )

            # 3. 명단에서 사용자 확인
            user_info = self._validate_user(context.user_id)
            if not user_info:
                return CommandResponse.create_error(
                    f"명단에서 사용자 정보를 찾을 수 없습니다. 명단에 등록되어 있는지 확인해 주세요."
                )

            # 4. 관리 워크시트에서 상태 조회
            status_data = self._get_user_status(context.user_id, headers)
            if not status_data:
                return CommandResponse.create_error(
                    f"{user_info['name']} 님의 상태 정보를 관리 워크시트에서 찾을 수 없습니다."
                )

            # 5. 최종 메시지 생성
            message = self._format_status_output(
                user_name=user_info['name'],
                status_data=status_data,
                headers=headers
            )

            # 6. 응답 반환
            return CommandResponse.create_success(message, data={
                'user_name': user_info['name'],
                'user_id': context.user_id,
                'status': status_data
            })

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
            logger.error(f"상태 확인 명령어 실행 오류: {e}", exc_info=True)
            return CommandResponse.create_error(
                "상태 확인 중 오류가 발생했습니다.",
                error=e
            )

    def _check_enabled(self) -> bool:
        """
        PEEK_STATUS 설정 확인

        Returns:
            bool: 활성화 여부
        """
        peek_status = getattr(config, 'PEEK_STATUS', 'False')

        # "True" 문자열이거나 True 값이면 활성화
        if peek_status in ['True', True, 'true', '1']:
            logger.debug("PEEK_STATUS 활성화됨")
            return True

        logger.debug(f"PEEK_STATUS 비활성화됨: {peek_status}")
        return False

    def _parse_peek_headers(self) -> List[str]:
        """
        PEEK_HEADER를 파싱하여 리스트로 반환

        Returns:
            List[str]: 헤더 리스트
        """
        peek_header = getattr(config, 'PEEK_HEADER', '')

        if not peek_header:
            logger.warning("PEEK_HEADER가 설정되지 않음")
            return []

        # 쉼표로 분리하고 공백 제거
        headers = [h.strip() for h in peek_header.split(',') if h.strip()]

        logger.debug(f"PEEK_HEADER 파싱 결과: {headers}")
        return headers

    def _validate_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        """
        명단 캐시에서 사용자 확인

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

    def _get_user_status(self, user_id: str, headers: List[str]) -> Optional[Dict[str, Any]]:
        """
        관리 워크시트에서 사용자 상태 조회

        Args:
            user_id: 사용자 ID
            headers: 조회할 헤더 리스트

        Returns:
            Optional[Dict]: 상태 데이터 {header: value} 또는 None
        """
        try:
            # 관리 워크시트 조회 (1회 API 호출, 캐시 사용 안 함)
            management_data = self.sheets_manager.get_worksheet_data('관리', use_cache=False)

            if not management_data:
                logger.warning("관리 워크시트 데이터가 없습니다.")
                return None

            for row in management_data:
                if str(row.get('아이디', '')).strip() == user_id:
                    # headers에 지정된 열만 추출
                    status = {}
                    for header in headers:
                        status[header] = row.get(header, '')

                    logger.debug(f"사용자 {user_id} 상태 조회 완료: {list(status.keys())}")
                    return status

            logger.warning(f"관리 워크시트에서 사용자 {user_id}를 찾지 못함")
            return None

        except Exception as e:
            logger.error(f"관리 워크시트 조회 중 오류: {e}", exc_info=True)
            return None

    def _determine_value_type(self, value: Any) -> str:
        """
        값의 타입 판별 (int/dict/str/empty)

        Args:
            value: 판별할 값

        Returns:
            str: 'int', 'dict', 'str', 'empty' 중 하나
        """
        # 빈 값 체크
        if value is None or str(value).strip() == '':
            return 'empty'

        value_str = str(value).strip()

        # dict 타입 체크 (JSON 형태: {})
        if value_str.startswith('{') and value_str.endswith('}'):
            # 빈 딕셔너리인지 확인
            if value_str in ['{}', '{ }']:
                return 'empty'
            return 'dict'

        # int 타입 체크 (숫자로 변환 가능한지)
        try:
            int(float(value_str))
            return 'int'
        except (ValueError, TypeError):
            pass

        # 단순 텍스트 인벤토리 형식 체크 ("아이템: 개수, ..." 패턴)
        if ':' in value_str:
            parts = value_str.split(',')
            # 최소 1개 항목이 "이름: 숫자" 패턴인지 확인
            for part in parts[:3]:  # 앞 3개만 샘플 체크
                part = part.strip()
                if ':' in part:
                    _, count_str = part.rsplit(':', 1)
                    try:
                        int(count_str.strip())
                        return 'dict'
                    except (ValueError, TypeError):
                        pass

        # 나머지는 문자열
        return 'str'

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

    def _format_value(self, header: str, value: Any, value_type: str) -> str:
        """
        값을 타입에 맞게 포맷팅

        Args:
            header: 헤더 이름
            value: 값
            value_type: 값 타입 ('int', 'dict', 'str', 'empty')

        Returns:
            str: 포맷된 문자열
        """
        if value_type == 'empty':
            return ''

        elif value_type == 'int':
            # "체력 3" 형식
            try:
                int_value = int(float(value))
                return f"{header} {int_value}"
            except (ValueError, TypeError):
                logger.warning(f"int 변환 실패: {header}={value}")
                return ''

        elif value_type == 'str':
            # "혈액형 O" 형식
            return f"{header} {value}"

        elif value_type == 'dict':
            # 딕셔너리는 헤더만 반환 (리스트는 별도 처리)
            return f"{header}"

        return ''

    def _format_status_output(
        self,
        user_name: str,
        status_data: Dict[str, Any],
        headers: List[str]
    ) -> str:
        """
        최종 출력 메시지 생성

        출력 구조:
        상태 확인

        건강 100
        이성 100

        소지금
        13달러

        소지품
        ·  사과 1개
        ·  바나나 2개

        Args:
            user_name: 사용자 이름
            status_data: 상태 데이터 {header: value}
            headers: 헤더 순서 리스트

        Returns:
            str: 포맷된 메시지
        """
        result_parts = ["상태 확인"]

        stat_values: List[str] = []              # 체력, 정신력 등 단순 스탯
        money_value: Optional[Tuple[str, int]] = None  # 소지금
        dict_values: List[Tuple[str, Any]] = []  # 소지품 등 딕셔너리

        for header in headers:
            value = status_data.get(header)
            value_type = self._determine_value_type(value)

            if value_type == 'empty':
                continue

            if value_type == 'dict':
                dict_values.append((header, value))
            elif header == '소지금' and value_type == 'int':
                try:
                    money_value = (header, int(float(value)))
                except (ValueError, TypeError):
                    logger.warning(f"소지금 변환 실패: {value}")
            else:
                formatted = self._format_value(header, value, value_type)
                if formatted:
                    stat_values.append(formatted)

        # 1. 일반 스탯 (체력, 정신력 등) - 연속 출력
        if stat_values:
            result_parts.append("")
            result_parts.extend(stat_values)

        # 2. 소지금 블록
        # 소지품과 달리 항목이 하나뿐이라 불릿을 붙이지 않는다. 단위는 숫자에 붙여 쓴다("13달러").
        if money_value is not None:
            label, amount = money_value
            currency = getattr(config, 'CURRENCY', '')
            result_parts.append("")
            result_parts.append(label)
            result_parts.append(f"{amount}{currency}")

        # 3. 딕셔너리 값 블록 (소지품 등)
        for header, value in dict_values:
            result_parts.append("")
            result_parts.append(header)

            dict_data = self._parse_dict_value(value)
            if dict_data:
                for item_name, count in dict_data.items():
                    result_parts.append(f"·  {item_name} {count}개")
            else:
                result_parts.append("·  (없음)")

        return '\n'.join(result_parts)


# 유틸리티 함수들

def is_peek_status_command(keyword: str) -> bool:
    """
    키워드가 상태 확인 명령어인지 확인

    Args:
        keyword: 확인할 키워드

    Returns:
        bool: 상태 확인 명령어 여부
    """
    if not keyword:
        return False

    keyword = keyword.lower().strip()
    return keyword in ['상태 확인', '상태', '스탯', 'status']


def create_peek_status_command(sheets_manager=None) -> PeekStatusCommand:
    """
    상태 확인 명령어 인스턴스 생성

    Args:
        sheets_manager: Google Sheets 관리자

    Returns:
        PeekStatusCommand: 상태 확인 명령어 인스턴스
    """
    return PeekStatusCommand(sheets_manager)

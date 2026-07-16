"""
아이템 설명 명령어 구현
Google Sheets의 아이템 정보에서 특정 아이템의 설명을 조회하는 명령어 클래스입니다.
"""

import os
import sys
import re
from typing import List, Tuple, Any, Optional, Dict

# 경로 설정 (VM 환경 대응)
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

try:
    from config.settings import config
    from utils.logging_config import logger
    from utils.error_handling import CommandError
    from utils.cache_manager import bot_cache
    from utils.store_helpers import extract_price_info
    from commands.store.base_store_command import BaseStoreCommand
    from commands.registry import register_command
    from models.user import User
except ImportError as e:
    import logging
    logger = logging.getLogger('item_description_command')
    logger.error(f"필수 모듈 임포트 실패: {e}")
    raise


@register_command(
    name="아이템 설명",
    aliases=["아이템설명", "설명", "아이템 정보", "아이템정보", "아이템 조회", "아이템조회", "item description", "itemdescription"],
    description="상점에서 아이템의 설명과 가격 정보를 조회합니다.",
    category="상점",
    examples=["[아이템 설명/사과]"],
    requires_sheets=True,
    requires_api=False
)
class ItemDescriptionCommand(BaseStoreCommand):
    """
    아이템 설명 명령어 클래스

    Google Sheets의 '상점' 시트에서 특정 아이템의 정보를 조회하여 설명을 표시합니다.

    지원하는 형식:
    - [아이템 설명/아이템명] : 해당 아이템의 설명 조회
    - [아이템설명/아이템명] : 별칭 사용 가능
    """

    _sheet_error_suffix = ""
    _generic_error_prefix = "아이템 정보 조회 중"

    @staticmethod
    def get_supported_keywords() -> List[str]:
        """지원 키워드 (대표 우선)"""
        return ['아이템 설명', '아이템설명', '아이템 정보', '아이템정보', '아이템 조회', '아이템조회']

    def __init__(self, sheets_manager=None, api=None, **kwargs):
        """
        ItemDescriptionCommand 초기화

        Args:
            sheets_manager: Google Sheets 관리자
            api: 마스토돈 API 인스턴스
            **kwargs: 추가 의존성
        """
        super().__init__(sheets_manager, api, **kwargs)
        logger.debug(f"ItemDescriptionCommand 초기화 완료: sheets_manager={self.sheets_manager is not None}")

    def _execute_command_logic(self, user: User, keywords: List[str]) -> Tuple[str, Dict[str, Any]]:
        """
        아이템 설명 명령어 실행

        Args:
            user: 사용자 객체
            keywords: 키워드 리스트 ([아이템 설명, 아이템명])

        Returns:
            Tuple[str, ItemDescriptionResult]: (결과 메시지, 아이템 설명 결과 객체)

        Raises:
            CommandError: 아이템 조회 실패
        """
        # 키워드 파싱
        item_name = self._parse_item_description_keywords(keywords)

        # 아이템 정보 조회
        item_info = self._get_item_info(item_name)
        if not item_info:
            raise CommandError(f"'{item_name}' 아이템을 찾을 수 없습니다. 상점 목록을 확인해 주세요.")

        # 메시지/페이로드 구성
        currency_unit = item_info.get('currency_unit') or (config.CURRENCY if item_info['price'] is not None else None)
        message = self._build_item_description_message(
            item_name=item_info['name'],
            price=item_info['price'],
            currency_unit=currency_unit,
            description=item_info['description'],
            stat=item_info.get('stat'),
            value=item_info.get('value')
        )
        payload = {
            'item_name': item_info['name'],
            'price': item_info['price'],
            'description': item_info['description'],
            'currency_unit': currency_unit,
            'stat': item_info.get('stat'),
            'value': item_info.get('value')
        }
        return message, payload

    def _is_dice_expression(self, value_str: str) -> bool:
        """
        주어진 문자열이 주사위 표현식인지 확인

        지원 형식:
        - ndm: 1d6, 2d10
        - ndm+k: 1d6+3, 2d6+5
        - ndm-k: 1d6-2
        - -(ndm+k): -(1d6+3), -(2d6+5)

        Args:
            value_str: 검증할 문자열

        Returns:
            bool: 주사위 표현식 여부
        """
        if not value_str or not isinstance(value_str, str):
            return False

        value_str = value_str.strip()

        # -(ndm+k) 형식 검사
        if value_str.startswith('-(') and value_str.endswith(')'):
            inner = value_str[2:-1]  # '-(1d6+3)' -> '1d6+3'
            dice_pattern = re.compile(r'^\d+[dD]\d+([\+\-]\d+)?$')
            return bool(dice_pattern.match(inner))
        else:
            # ndm, ndm+k, ndm-k 형식 검사
            dice_pattern = re.compile(r'^\d+[dD]\d+([\+\-]\d+)?$')
            return bool(dice_pattern.match(value_str))

    def _build_item_description_message(
        self,
        item_name: str,
        price: Optional[int],
        currency_unit: Optional[str],
        description: str,
        stat: Optional[str] = None,
        value: Optional[str] = None
    ) -> str:
        """
        아이템 설명 메시지 생성

        Args:
            item_name: 아이템명
            price: 가격
            currency_unit: 화폐 단위
            description: 설명
            stat: 스탯명 (선택)
            value: 수치 (선택)

        Returns:
            str: 포맷된 메시지
        """
        # 비매품인 경우 (가격이 없거나 0인 경우)
        if price is None or price == 0 or currency_unit is None:
            base_message = f"{item_name}(비매품): {description}"
        else:
            # 가격이 있는 경우
            base_message = f"{item_name}({price}{currency_unit}): {description}"

        # 사용 불가인 경우
        if stat == '사용 불가':
            return base_message + " [명령어 사용 불가]"

        # 스탯과 수치가 있으면 [사용 시 ...] 추가
        if stat and value:
            try:
                # 주사위 표현식인지 확인
                if self._is_dice_expression(value):
                    # 주사위 표현식은 그대로 표시
                    stat_info = f" [사용 시 {stat} {value}]"
                    return base_message + stat_info

                # 일반 수치인 경우 정수로 변환
                value_int = int(float(value))

                # +/- 기호 처리
                if value_int > 0:
                    stat_info = f" [사용 시 {stat} +{value_int}]"
                else:
                    stat_info = f" [사용 시 {stat} {value_int}]"

                return base_message + stat_info
            except (ValueError, TypeError):
                # 수치 파싱 실패 시 기본 메시지만 반환
                logger.warning(f"스탯 수치 파싱 실패: {value}")
                return base_message

        # 기본 메시지 (스탯/수치 없음)
        return base_message
    
    def _parse_item_description_keywords(self, keywords: List[str]) -> str:
        """
        아이템 설명 키워드 파싱
        
        Args:
            keywords: 키워드 리스트
            
        Returns:
            str: 아이템명
            
        Raises:
            CommandError: 파싱 실패
        """
        if len(keywords) < 2:
            raise CommandError("아이템명을 입력해 주세요. 사용법: [아이템 설명/아이템명]")
        
        item_name = keywords[1].strip()
        if not item_name:
            raise CommandError("아이템명이 비어있습니다.")
        
        return item_name
        
    def _get_item_info(self, item_name: str) -> Optional[Dict[str, Any]]:
        """
        아이템 정보 조회 (상점 → 가챠 → 특수템 순서로 검색)
        
        Args:
            item_name: 아이템명
            
        Returns:
            Optional[Dict]: 아이템 정보 {'name': str, 'price': int, 'description': str, 'currency_unit': str, 'source': str} 또는 None
        """
        item_name_lower = item_name.lower()
        
        # 1순위: 상점 탭 검색
        shop_result = self._search_in_shop(item_name_lower)
        if shop_result:
            return shop_result
        
        # 2순위: 가챠 탭 검색
        gacha_result = self._search_in_gacha(item_name_lower)
        if gacha_result:
            return gacha_result
        
        # 3순위: 특수템 탭 검색
        special_result = self._search_in_special_items(item_name_lower)
        if special_result:
            return special_result
        
        return None

    def _search_in_shop(self, item_name_lower: str) -> Optional[Dict[str, Any]]:
        """상점 탭에서 아이템 검색"""
        try:
            shop_data_list = self._load_shop_data()

            for item_data in shop_data_list:
                stored_name = str(item_data.get('아이템명', '')).strip()
                if stored_name.lower() == item_name_lower:
                    # 동적 키 검색: 가격 컬럼
                    price_key, price, currency_from_price = extract_price_info(item_data)

                    # 설명 가져오기
                    description = str(item_data.get('설명', '')).strip()
                    if not description:
                        description = "설명이 없습니다."

                    # 스탯과 수치 가져오기
                    stat = str(item_data.get('스탯', '')).strip()
                    value = str(item_data.get('수치', '')).strip()

                    return {
                        'name': stored_name,
                        'price': price,
                        'description': description,
                        'currency_unit': currency_from_price,
                        'stat': stat if stat else None,
                        'value': value if value else None,
                        'source': '상점'
                    }
        except Exception as e:
            logger.error(f"상점 탭 검색 실패: {e}")

        return None

    def _load_shop_data(self) -> List[Dict[str, Any]]:
        """
        상점 워크시트 데이터 로드 (캐시 우선)
        
        Returns:
            List[Dict]: 상점 데이터
        """
        cached = bot_cache.get_item_data()
        if cached:
            logger.debug("캐시에서 상점 데이터 로드")
            return cached

        try:
            if self.sheets_manager:
                worksheet_name = config.get_worksheet_name('SHOP') or '상점'
                data = self.sheets_manager.get_worksheet_data(worksheet_name)
                if data:
                    bot_cache.cache_item_data(data, ttl_seconds=config.CACHE_TTL)
                    logger.debug(f"시트에서 상점 데이터 로드: {len(data)}개")
                    return data
        except Exception as e:
            logger.warning(f"상점 데이터 로드 실패: {e}")

        return []

    def _load_gacha_data(self) -> List[Dict[str, Any]]:
        """
        가챠 워크시트 데이터 로드

        Returns:
            List[Dict]: 가챠 데이터
        """
        # 가챠 워크시트가 없으면 빈 리스트 반환
        try:
            if self.sheets_manager:
                data = self.sheets_manager.get_worksheet_data('가챠', use_cache=True)
                if data:
                    logger.debug(f"가챠 데이터 로드: {len(data)}개")
                    return data
        except Exception as e:
            logger.debug(f"가챠 워크시트 없음 또는 로드 실패: {e}")

        return []

    def _load_special_items_data(self) -> List[Dict[str, Any]]:
        """
        특수템 워크시트 데이터 로드

        Returns:
            List[Dict]: 특수템 데이터
        """
        # 특수템 워크시트가 없으면 빈 리스트 반환
        try:
            if self.sheets_manager:
                data = self.sheets_manager.get_worksheet_data('특수템', use_cache=True)
                if data:
                    logger.debug(f"특수템 데이터 로드: {len(data)}개")
                    return data
        except Exception as e:
            logger.debug(f"특수템 워크시트 없음 또는 로드 실패: {e}")

        return []

    def _search_in_gacha(self, item_name_lower: str) -> Optional[Dict[str, Any]]:
        """가챠 탭에서 아이템 검색 (가격 없음)"""
        try:
            gacha_data_list = self._load_gacha_data()

            for item_data in gacha_data_list:
                stored_name = str(item_data.get('아이템명', '')).strip()
                if stored_name.lower() == item_name_lower:
                    # 설명 가져오기
                    description = str(item_data.get('설명', '')).strip()
                    if not description:
                        description = "설명이 없습니다."

                    return {
                        'name': stored_name,
                        'price': None,  # 가챠템은 가격 없음
                        'description': description,
                        'currency_unit': None,
                        'stat': None,
                        'value': None,
                        'source': '가챠'
                    }
        except Exception as e:
            logger.error(f"가챠 탭 검색 실패: {e}")

        return None

    def _search_in_special_items(self, item_name_lower: str) -> Optional[Dict[str, Any]]:
        """특수템 탭에서 아이템 검색 (가격 없음)"""
        try:
            special_data_list = self._load_special_items_data()

            for item_data in special_data_list:
                stored_name = str(item_data.get('아이템명', '')).strip()
                if stored_name.lower() == item_name_lower:
                    # 설명 가져오기
                    description = str(item_data.get('설명', '')).strip()
                    if not description:
                        description = "설명이 없습니다."

                    return {
                        'name': stored_name,
                        'price': None,  # 특수템은 가격 없음
                        'description': description,
                        'currency_unit': None,
                        'stat': None,
                        'value': None,
                        'source': '특수템'
                    }
        except Exception as e:
            logger.error(f"특수템 탭 검색 실패: {e}")

        return None
    
# 아이템 설명 관련 유틸리티 함수들
def is_item_description_command(keyword: str) -> bool:
    """
    키워드가 아이템 설명 명령어인지 확인
    
    Args:
        keyword: 확인할 키워드
        
    Returns:
        bool: 아이템 설명 명령어 여부
    """
    if not keyword:
        return False
    
    keyword = keyword.lower().strip()
    return keyword in ['아이템 설명', '아이템설명', 'item description', 'item info']


def parse_item_description_command(keywords: List[str]) -> str:
    """
    아이템 설명 명령어 파싱 (독립 함수)
    
    Args:
        keywords: 키워드 리스트
        
    Returns:
        str: 아이템명
        
    Raises:
        ValueError: 파싱 실패
    """
    if len(keywords) < 2:
        raise ValueError("아이템명이 필요합니다.")
    
    item_name = keywords[1].strip()
    if not item_name:
        raise ValueError("아이템명이 비어있습니다.")
    
    return item_name


def format_item_description(item_name: str, price: int, description: str, currency_unit: str = "포인트") -> str:
    """
    아이템 설명 포맷팅 (독립 함수)
    
    Args:
        item_name: 아이템명
        price: 가격
        description: 설명
        currency_unit: 화폐 단위
        
    Returns:
        str: 포맷된 아이템 설명
    """
    formatted_price = f"{price:,}"
    return f"{item_name}({formatted_price}{currency_unit}) : {description}"


def search_item_by_partial_name(item_data_list: List[Dict], partial_name: str) -> List[Dict]:
    """
    부분 이름으로 아이템 검색 (독립 함수)
    
    Args:
        item_data_list: 아이템 데이터 리스트
        partial_name: 부분 이름
        
    Returns:
        List[Dict]: 매칭되는 아이템들
    """
    partial_name_lower = partial_name.lower()
    matches = []
    
    for item_data in item_data_list:
        item_name = str(item_data.get('아이템명', '')).strip()
        if partial_name_lower in item_name.lower():
            matches.append(item_data)
    
    return matches


def group_items_by_currency(item_data_list: List[Dict]) -> Dict[str, List[Dict]]:
    """
    화폐 단위별로 아이템 그룹화 (독립 함수)
    
    Args:
        item_data_list: 아이템 데이터 리스트
        
    Returns:
        Dict: {화폐단위: [아이템들]} 형태
    """
    grouped = {}
    import re
    
    for item_data in item_data_list:
        # 가격 키에서 화폐 단위 추출
        currency_unit = "포인트"  # 기본값
        for key in item_data.keys():
            if '가격' in key and '(' in key and ')' in key:
                match = re.search(r'가격\s*\(([^)]+)\)', key)
                if match:
                    currency_unit = match.group(1).strip()
                break
        
        if currency_unit not in grouped:
            grouped[currency_unit] = []
        grouped[currency_unit].append(item_data)
    
    return grouped


# 아이템 설명 명령어 인스턴스 생성 함수
def create_item_description_command(sheets_manager=None) -> ItemDescriptionCommand:
    """
    아이템 설명 명령어 인스턴스 생성
    
    Args:
        sheets_manager: Google Sheets 관리자
        
    Returns:
        ItemDescriptionCommand: 아이템 설명 명령어 인스턴스
    """
    return ItemDescriptionCommand(sheets_manager)
"""
상점 명령어 구현
Google Sheets에서 아이템 목록을 가져와 상점을 표시하는 명령어 클래스입니다.
"""

import os
import sys
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
    logger = logging.getLogger('shop_command')
    logger.error(f"필수 모듈 임포트 실패: {e}")
    raise


@register_command(
    name="상점",
    aliases=["아이템 목록", "아이템목록", "상점 목록", "상점목록", "shop"],
    description="구매 가능한 아이템 목록을 표시합니다.",
    category="상점",
    examples=["[상점]"],
    requires_sheets=True,
    requires_api=False
)
class ShopCommand(BaseStoreCommand):
    """
    상점 명령어 클래스

    Google Sheets의 '상점' 시트에서 구매 가능한 아이템 목록을 가져와 표시합니다.

    지원하는 형식:
    - [상점] : 구매 가능한 아이템 목록 표시
    - [아이템 목록] : 구매 가능한 아이템 목록 표시
    """

    _sheet_error_suffix = ""
    _generic_error_prefix = "상점 조회 중"

    @staticmethod
    def get_supported_keywords() -> List[str]:
        """지원 키워드 (대표 우선)"""
        return ['상점', '아이템 목록', '아이템목록', '상점 목록', '상점목록']

    def __init__(self, sheets_manager=None, api=None, **kwargs):
        """
        ShopCommand 초기화

        Args:
            sheets_manager: Google Sheets 관리자
            api: 마스토돈 API 인스턴스
            **kwargs: 추가 의존성
        """
        super().__init__(sheets_manager, api, **kwargs)
        logger.debug(f"ShopCommand 초기화 완료: sheets_manager={self.sheets_manager is not None}")

    def _execute_command_logic(self, user: User, keywords: List[str]) -> Tuple[str, List[Dict[str, Any]]]:
        """
        상점 명령어 실행
        
        Args:
            user: 사용자 객체
            keywords: 키워드 리스트 ([상점] 또는 [아이템 목록])
            
        Returns:
            Tuple[str, ShopResult]: (결과 메시지, 상점 결과 객체)
            
        Raises:
            CommandError: 아이템 데이터 로드 실패
        """
        # 아이템 목록 조회
        shop_items = self._get_shop_items()
        
        if not shop_items:
            raise CommandError("현재 상점에 판매중인 아이템이 없습니다.")
        
        # 가격이 0인 아이템 필터링 (비매품)
        shop_items = [item for item in shop_items if item.get('price', 0) > 0]
        
        if not shop_items:
            raise CommandError("구매 가능한 아이템이 없습니다. (모든 아이템이 비매품입니다)")
        
        # 화폐 단위 조회
        currency_unit = config.CURRENCY
        
        # 메시지/페이로드 구성
        message = self._build_shop_message(shop_items, currency_unit)
        return message, shop_items

    def _build_shop_message(self, items: List[Dict[str, Any]], currency_fallback: str) -> str:
        if not items:
            return "현재 상점에 판매중인 아이템이 없습니다."
        lines = ["구매 가능한 아이템 목록\n"]
        for item in items[:50]:
            name = item.get('name', '알 수 없음')
            price = item.get('price', 0)
            desc = item.get('description', '') or '설명 없음'
            unit = item.get('currency_unit') or currency_fallback
            lines.append(f"- {name} ({price:,}{unit}) : {desc}")
        if len(items) > 50:
            lines.append(f"... 외 {len(items) - 50}개")
        return "\n".join(lines)
    
    def _get_shop_items(self) -> List[Dict[str, Any]]:
        """
        상점 아이템 목록 조회 (동적 키 검색 적용)
        
        Returns:
            List[Dict]: 아이템 정보 리스트 [{'name': str, 'price': int, 'description': str, 'currency_unit': str}]
        """
        # 아이템 데이터 로드
        item_data_list = self._load_item_data()
        
        if not item_data_list:
            logger.warning("아이템 데이터가 없습니다.")
            return []
        
        shop_items = []
        
        # 각 아이템 정보 처리
        for item_data in item_data_list:
            try:
                item_name = str(item_data.get('아이템명', '')).strip()
                description = str(item_data.get('설명', '')).strip()
                
                # 아이템 이름이 없으면 스킵
                if not item_name:
                    continue
                
                price_key, _, currency_from_price = extract_price_info(item_data)
                
                # 가격 파싱 및 비매품 필터링
                if price_key:
                    price_str = str(item_data.get(price_key, '0')).strip()
                    # '비매품' 표기 시 상점 목록에서 제외 (설명/양도에는 영향 없음)
                    if '비매품' in price_str:
                        logger.debug(f"비매품 아이템 제외: {item_name}")
                        continue
                    try:
                        price = int(float(price_str))
                    except (ValueError, TypeError):
                        logger.warning(f"아이템 '{item_name}'의 가격 파싱 실패: {price_str}")
                        price = 0
                else:
                    logger.warning(f"아이템 '{item_name}'의 가격 컬럼을 찾을 수 없습니다.")
                    price = 0
                
                # 설명이 없으면 기본 설명
                if not description:
                    description = "설명이 없습니다."
                
                shop_items.append({
                    'name': item_name,
                    'price': price,
                    'description': description,
                    'currency_unit': currency_from_price  # 각 아이템의 화폐 단위
                })
                
            except Exception as e:
                logger.warning(f"아이템 데이터 처리 실패: {item_data} -> {e}")
                continue
        
        return shop_items
    
    def _load_item_data(self) -> List[Dict[str, str]]:
        """
        아이템 데이터 로드 (캐시 우선, 시트 후순위)
        
        Returns:
            List[Dict]: 아이템 데이터 리스트
        """
        # 캐시에서 먼저 조회
        cached_data = bot_cache.get_item_data()
        if cached_data:
            logger.debug("캐시에서 아이템 데이터 로드")
            return cached_data
        
        # 시트에서 로드
        try:
            if self.sheets_manager:
                # '상점' 워크시트 직접 조회 (캐시 정책은 상위 캐시로 관리)
                worksheet_name = config.get_worksheet_name('SHOP') or '상점'
                item_data = self.sheets_manager.get_worksheet_data(worksheet_name)
                if item_data:
                    # 캐시에 저장
                    bot_cache.cache_item_data(item_data, ttl_seconds=config.CACHE_TTL)
                    logger.debug(f"시트에서 아이템 데이터 로드: {len(item_data)}개")
                    return item_data
        except Exception as e:
            logger.warning(f"시트에서 아이템 데이터 로드 실패: {e}")
        
        # 빈 리스트 반환
        logger.info("아이템 데이터 없음")
        return []
    
# 상점 관련 유틸리티 함수들
def is_shop_command(keyword: str) -> bool:
    """
    키워드가 상점 명령어인지 확인
    
    Args:
        keyword: 확인할 키워드
        
    Returns:
        bool: 상점 명령어 여부
    """
    if not keyword:
        return False
    
    keyword = keyword.lower().strip()
    return keyword in ['상점', '아이템 목록', '아이템목록', '상점목록']


def format_item_display(item: Dict[str, Any], fallback_currency: str = "포인트") -> str:
    """
    아이템 표시 형식 생성 (개별 화폐 단위 지원)
    
    Args:
        item: 아이템 정보 {'name': str, 'price': int, 'description': str, 'currency_unit': str}
        fallback_currency: 폴백 화폐 단위
        
    Returns:
        str: 포맷된 아이템 문자열
    """
    name = item.get('name', '알 수 없는 아이템')
    price = item.get('price', 0)
    description = item.get('description', '설명이 없습니다.')
    currency_unit = item.get('currency_unit') or fallback_currency
    
    return f"{name} ({price}{currency_unit}) : {description}"


def calculate_total_shop_value(items: List[Dict[str, Any]]) -> int:
    """
    상점 전체 아이템 가치 계산
    
    Args:
        items: 아이템 리스트
        
    Returns:
        int: 총 가치
    """
    return sum(item.get('price', 0) for item in items)


def group_items_by_currency(items: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """
    화폐 단위별로 아이템 그룹화
    
    Args:
        items: 아이템 리스트
        
    Returns:
        Dict: {화폐단위: [아이템들]} 형태
    """
    grouped = {}
    for item in items:
        currency = item.get('currency_unit', '포인트')
        if currency not in grouped:
            grouped[currency] = []
        grouped[currency].append(item)
    return grouped


# 상점 명령어 인스턴스 생성 함수
def create_shop_command(sheets_manager=None) -> ShopCommand:
    """
    상점 명령어 인스턴스 생성
    
    Args:
        sheets_manager: Google Sheets 관리자
        
    Returns:
        ShopCommand: 상점 명령어 인스턴스
    """
    return ShopCommand(sheets_manager)
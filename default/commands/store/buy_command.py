"""
구매 명령어 구현 (가챠템 지원 버전)
Google Sheets의 아이템 정보를 바탕으로 구매 기능을 제공하는 명령어 클래스입니다.
특별 아이템(송충이, 별사탕)은 가챠템 컬럼에 저장됩니다.
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
    from utils.korean_utils import add_eul_reul
    from utils.store_helpers import serialize_inventory, extract_price_info
    from utils.lock_manager import get_lock_manager
    from commands.store.base_store_command import BaseStoreCommand
    from commands.registry import register_command
    from models.user import User
except ImportError as e:
    import logging
    logger = logging.getLogger('buy_command')
    logger.error(f"필수 모듈 임포트 실패: {e}")
    raise


@register_command(
    name="구매",
    aliases=["구입", "사기", "살게", "buy"],
    description="상점에서 아이템을 구매합니다.",
    category="상점",
    examples=["[구매/사과]", "[구매/사과/2]"],
    requires_sheets=True,
    requires_api=False
)
class BuyCommand(BaseStoreCommand):
    """
    구매 명령어 클래스
    
    Google Sheets의 '상점' 시트에서 아이템 정보를 확인하고,
    '명단' 시트의 사용자 재화와 인벤토리를 업데이트합니다.
    
    특별 처리:
    - '송충이', '별사탕' 아이템은 '가챠템' 컬럼에 저장
    - 다른 아이템들은 '인벤토리' 컬럼에 저장
    
    지원하는 형식:
    - [구매/아이템명] : 해당 아이템 1개 구매
    - [구매/아이템명/개수] : 해당 아이템 지정 개수 구매
    """
    
    # 가챠템으로 분류될 특별 아이템들
    GACHA_ITEMS = {'송충이', '별사탕'}

    # BaseStoreCommand 에러 메시지 커스터마이즈
    _sheet_error_suffix = " (구매)"
    _generic_error_prefix = "구매 처리 중"

    def get_supported_keywords(self) -> List[str]:
        """
        지원되는 모든 키워드 목록 반환

        Returns:
            List[str]: 지원되는 키워드 목록
        """
        return ['구매', '구입', '사기', '살게']

    def __init__(self, sheets_manager=None, api=None, **kwargs):
        """
        BuyCommand 초기화

        Args:
            sheets_manager: Google Sheets 관리자
            api: 마스토돈 API 인스턴스
            **kwargs: 추가 의존성
        """
        super().__init__(sheets_manager, api, **kwargs)
        logger.debug(f"BuyCommand 초기화 완료: sheets_manager={self.sheets_manager is not None}")

    def _execute_command_logic(self, user: User, keywords: List[str]) -> Tuple[str, Dict[str, Any]]:
        """
        구매 명령어 실행
        
        Args:
            user: 사용자 객체
            keywords: 키워드 리스트 ([구매, 아이템명] 또는 [구매, 아이템명, 개수])
            
        Returns:
            Tuple[str, BuyResult]: (결과 메시지, 구매 결과 객체)
            
        Raises:
            CommandError: 구매 처리 실패
        """
        # 키워드 파싱
        parsed_data = self._parse_buy_keywords(keywords)
        item_name = parsed_data['item_name']
        quantity = parsed_data['quantity']

        # 아이템 정보 확인 (Lock 밖에서 - 아이템 정보는 변경되지 않음)
        item_info = self._get_item_info(item_name)
        if not item_info:
            raise CommandError(f"'{item_name}' 아이템을 찾을 수 없습니다. 상점 목록을 확인해 주세요.")

        # 비매품 구매 방지
        if item_info.get('price', 1) == 0:
            raise CommandError(f"'{item_name}'은(는) 비매품으로 구매할 수 없습니다.")

        # Lock 획득 후 사용자 정보 조회 및 구매 처리
        lock_manager = get_lock_manager()
        with lock_manager.acquire_lock(user.id, timeout=10.0) as acquired:
            if not acquired:
                raise CommandError("다른 거래가 처리 중입니다. 잠시 후 다시 시도해 주세요.")

            # Lock 내부에서 사용자 정보 조회 (stale read 방지)
            user_info = self._get_user_info(user.id)
            if not user_info:
                raise CommandError(f"{user.get_display_name()} 님의 정보를 찾을 수 없습니다. 명단에 등록되어 있는지 확인해 주세요.")

            # 필요한 금액 계산
            total_cost = item_info['price'] * quantity
            current_money = user_info['money']

            # 잔액 확인
            if current_money < total_cost:
                currency_unit = config.CURRENCY
                raise CommandError(f"돈이 부족합니다. 현재 소지금은 {current_money:,}{currency_unit}입니다.")

            # 구매 처리 (시트 업데이트)
            success = self._process_purchase(
                user.id,
                user_info,
                item_name,
                quantity,
                total_cost
            )

            if not success:
                raise CommandError("구매 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.")

        currency_unit = config.CURRENCY
        remaining = current_money - total_cost
        # 메시지 생성 (가챠템 여부 포함)
        message = self._build_buy_message(
            user_name=user_info['name'],
            item_name=item_name,
            quantity=quantity,
            unit_price=item_info['price'],
            total_cost=total_cost,
            remaining=remaining,
            currency_unit=currency_unit,
            is_gacha=self._is_gacha_item(item_name)
        )
        payload = {
            'user_name': user_info['name'],
            'user_id': user.id,
            'item_name': item_name,
            'quantity': quantity,
            'unit_price': item_info['price'],
            'total_cost': total_cost,
            'remaining_money': remaining,
            'currency_unit': currency_unit
        }
        return message, payload

    def _build_buy_message(
        self,
        user_name: str,
        item_name: str,
        quantity: int,
        unit_price: int,
        total_cost: int,
        remaining: int,
        currency_unit: str,
        is_gacha: bool
    ) -> str:
        # 헤더: {아이템명}{을를} 구매했습니다.
        item_with_particle = add_eul_reul(item_name)
        header = f"{item_with_particle} 구매했습니다."
        # 상세 라인들 (천 단위 구분자 없이 예시 형식 유지)
        line1 = f"➭ -{total_cost}{currency_unit}"
        line2 = f"➭ {item_name} {quantity}개 획득"
        line3 = f"➭ 잔액 {remaining}{currency_unit}"
        return "\n\n" + "\n".join([header, line1, line2, line3])
    
    def _is_gacha_item(self, item_name: str) -> bool:
        """
        아이템이 가챠템인지 확인
        
        Args:
            item_name: 아이템명
            
        Returns:
            bool: 가챠템 여부
        """
        return item_name.strip() in self.GACHA_ITEMS
    
    def _parse_buy_keywords(self, keywords: List[str]) -> Dict[str, Any]:
        """
        구매 키워드 파싱
        
        Args:
            keywords: 키워드 리스트
            
        Returns:
            Dict: {'item_name': str, 'quantity': int}
            
        Raises:
            CommandError: 파싱 실패
        """
        if len(keywords) < 2:
            raise CommandError("구매할 아이템명을 입력해 주세요.\n사용법: [구매/아이템명] 또는 [구매/아이템명/개수]")
        
        item_name = keywords[1].strip()
        if not item_name:
            raise CommandError("아이템명이 비어있습니다.")
        
        # 개수 파싱
        quantity = 1  # 기본값
        if len(keywords) >= 3:
            quantity_str = keywords[2].strip()
            if quantity_str:
                try:
                    quantity = int(quantity_str)
                    if quantity <= 0:
                        raise CommandError("구매 개수는 1개 이상이어야 합니다.")
                    if quantity > 999:  # 최대 구매 제한
                        raise CommandError("한 번에 최대 999개까지만 구매할 수 있습니다.")
                except ValueError:
                    raise CommandError(f"올바른 개수를 입력해 주세요. 입력값: '{quantity_str}'")
        
        return {
            'item_name': item_name,
            'quantity': quantity
        }
    
    def _get_item_info(self, item_name: str) -> Optional[Dict[str, Any]]:
        """
        아이템 정보 조회
        
        Args:
            item_name: 아이템명
            
        Returns:
            Optional[Dict]: 아이템 정보 {'name': str, 'price': int, 'description': str, 'currency_unit': str} 또는 None
        """
        # 아이템 데이터 로드
        item_data_list = self._load_item_data()
        
        if not item_data_list:
            logger.warning("아이템 데이터가 없습니다.")
            return None
        
        # 아이템명으로 검색 (대소문자 구분 없음)
        item_name_lower = item_name.lower()
        
        for item_data in item_data_list:
            stored_name = str(item_data.get('아이템명', '')).strip()
            if stored_name.lower() == item_name_lower:
                try:
                    price_key, _, currency_from_price = extract_price_info(item_data)

                    if price_key:
                        price_str = str(item_data.get(price_key, '0')).strip()
                        price = int(float(price_str))
                    else:
                        logger.warning(f"아이템 '{stored_name}'의 가격 컬럼을 찾을 수 없습니다.")
                        price = 0
                    
                    return {
                        'name': stored_name,  # 원본 이름 사용
                        'price': price,
                        'description': str(item_data.get('설명', '')).strip(),
                        'currency_unit': currency_from_price  # 가격 헤더에서 추출한 화폐 단위
                    }
                except (ValueError, TypeError) as e:
                    logger.warning(f"아이템 '{stored_name}' 가격 파싱 실패: {item_data.get(price_key, '알 수 없음')} -> {e}")
                    return None
        
        return None
    
    def _get_user_info(self, user_id: str) -> Optional[Dict[str, Any]]:
        """
        사용자 정보 조회 (명단 캐시 확인 + 관리 워크시트 실시간 조회)
        
        Args:
            user_id: 사용자 ID
            
        Returns:
            Optional[Dict]: 사용자 정보 {'name': str, 'money': int, 'inventory': dict} 또는 None
        """
        # 1) 명단 캐시에서 사용자 확인 및 이름 조회
        roster = self._load_user_data()
        if not roster:
            logger.warning("명단 데이터가 없습니다.")
            return None

        user_name = None
        for row in roster:
            if str(row.get('아이디', '')).strip() == user_id:
                user_name = str(row.get('이름', user_id)).strip()
                break

        if not user_name:
            logger.info(f"명단에서 사용자 {user_id}를 찾지 못함")
            return None

        # 2) 관리 워크시트에서 소지금/소지품 조회 (캐시 사용 안 함)
        try:
            management_rows = self.sheets_manager.get_worksheet_data('관리', use_cache=False)
        except Exception as e:
            logger.error(f"관리 워크시트 조회 중 오류: {e}", exc_info=True)
            return None

        for row in management_rows:
            if str(row.get('아이디', '')).strip() == user_id:
                # 소지금
                raw_money = row.get('소지금', 0)
                try:
                    money = int(float(str(raw_money).strip())) if str(raw_money).strip() else 0
                except (ValueError, TypeError):
                    money = 0
                logger.debug(f"사용자 {user_id}: 관리 워크시트에서 소지금 조회 -> {money}")

                # 소지품
                inventory_str = str(row.get('소지품', '')).strip()
                inventory = self._parse_inventory(inventory_str)
                logger.debug(f"사용자 {user_id}: 관리 워크시트에서 소지품 조회 -> {inventory}")

                return {
                    'name': user_name,
                    'money': money,
                    'inventory': inventory
                }

        logger.warning(f"관리 워크시트에서 사용자 {user_id}를 찾지 못함 (명단에는 존재)")
        return None

    def _process_purchase(self, user_id: str, user_info: Dict, item_name: str, quantity: int, total_cost: int) -> bool:
        """
        구매 처리 (시트 업데이트) - 가챠템 지원
        
        Args:
            user_id: 사용자 ID
            user_info: 사용자 정보
            item_name: 아이템명
            quantity: 구매 개수
            total_cost: 총 비용
            
        Returns:
            bool: 처리 성공 여부
        """
        try:
            # 새로운 재화 계산
            new_money = user_info['money'] - total_cost

            # 모든 아이템은 '관리' 워크시트의 '소지품' 컬럼에 저장
            new_inventory = user_info['inventory'].copy()
            if item_name in new_inventory:
                new_inventory[item_name] += quantity
            else:
                new_inventory[item_name] = quantity

            logger.info(f"구매 처리: {user_id} -> {item_name} {quantity}개, 차감 {total_cost}")

            # 시트 업데이트 (관리 워크시트)
            success = self._update_user_data(user_id, new_money, new_inventory)
            
            if success:
                # 캐시 무효화
                self._invalidate_user_cache()
                logger.info(f"구매 처리 완료: {user_id} -> {item_name} {quantity}개, 잔액: {new_money}")
            
            return success
            
        except Exception as e:
            logger.error(f"구매 처리 실패: {user_id} -> {e}")
            return False
    
    def _update_user_data(self, user_id: str, new_money: int, new_inventory: Dict[str, int]) -> bool:
        """
        사용자 데이터를 시트에 업데이트 (가챠템 지원)
        
        Args:
            user_id: 사용자 ID
            new_money: 새로운 재화
            new_inventory: 새로운 인벤토리
            new_gacha_items: 새로운 가챠템
            
        Returns:
            bool: 업데이트 성공 여부
        """
        try:
            if not self.sheets_manager:
                logger.error("시트 매니저가 없습니다.")
                return False
            
            # 사용자 행 찾기 (관리 워크시트, 아이디=B열)
            user_row = self._find_user_row(user_id)
            if user_row is None:
                logger.error(f"사용자 행을 찾을 수 없습니다: {user_id}")
                return False
            
            # 딕셔너리를 단순 텍스트 형식으로 변환
            inventory_str = serialize_inventory(new_inventory)

            # 시트/컬럼 정보 (관리 워크시트)
            worksheet_name = '관리'

            money_column = self._find_money_column()
            if money_column is None:
                logger.error("소지금 컬럼을 찾을 수 없습니다 (관리).")
                return False

            inventory_column = self._find_inventory_column()
            if inventory_column is None:
                logger.error("소지품 컬럼을 찾을 수 없습니다 (관리).")
                return False

            # 원자적 배치 업데이트 (돈 차감 + 인벤토리 추가를 한 번에)
            updates = [
                (user_row, money_column, new_money),
                (user_row, inventory_column, inventory_str),
            ]
            return self.sheets_manager.batch_update_cells(worksheet_name, updates)
            
        except Exception as e:
            logger.error(f"사용자 데이터 업데이트 실패: {user_id} -> {e}")
            return False
    
    def _find_gacha_column(self) -> Optional[int]:
        """더 이상 사용하지 않음 (관리 워크시트에는 가챠템 컬럼 없음)"""
        return None

# 구매 관련 유틸리티 함수들
def is_buy_command(keyword: str) -> bool:
    """
    키워드가 구매 명령어인지 확인
    
    Args:
        keyword: 확인할 키워드
        
    Returns:
        bool: 구매 명령어 여부
    """
    if not keyword:
        return False
    
    keyword = keyword.lower().strip()
    return keyword in ['구매', '구입', 'buy']


def parse_buy_command(keywords: List[str]) -> Tuple[str, int]:
    """
    구매 명령어 파싱 (독립 함수)
    
    Args:
        keywords: 키워드 리스트
        
    Returns:
        Tuple[str, int]: (아이템명, 개수)
        
    Raises:
        ValueError: 파싱 실패
    """
    if len(keywords) < 2:
        raise ValueError("아이템명이 필요합니다.")
    
    item_name = keywords[1].strip()
    if not item_name:
        raise ValueError("아이템명이 비어있습니다.")
    
    quantity = 1
    if len(keywords) >= 3:
        try:
            quantity = int(keywords[2].strip())
            if quantity <= 0:
                raise ValueError("개수는 1개 이상이어야 합니다.")
        except ValueError:
            raise ValueError("올바른 개수를 입력해주세요.")
    
    return item_name, quantity


def is_gacha_item(item_name: str) -> bool:
    """
    아이템이 가챠템인지 확인 (독립 함수)
    
    Args:
        item_name: 아이템명
        
    Returns:
        bool: 가챠템 여부
    """
    return item_name.strip() in BuyCommand.GACHA_ITEMS


def calculate_total_cost(unit_price: int, quantity: int) -> int:
    """
    총 비용 계산
    
    Args:
        unit_price: 단가
        quantity: 개수
        
    Returns:
        int: 총 비용
    """
    return unit_price * quantity


def format_money(amount: int, currency_unit: str = "포인트") -> str:
    """
    금액 포맷팅 (천 단위 콤마)
    
    Args:
        amount: 금액
        currency_unit: 화폐 단위
        
    Returns:
        str: 포맷된 금액
    """
    return f"{amount:,} {currency_unit}"


# 구매 명령어 인스턴스 생성 함수
def create_buy_command(sheets_manager=None) -> BuyCommand:
    """
    구매 명령어 인스턴스 생성
    
    Args:
        sheets_manager: Google Sheets 관리자
        
    Returns:
        BuyCommand: 구매 명령어 인스턴스
    """
    return BuyCommand(sheets_manager)
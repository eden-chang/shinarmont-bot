"""
양도 명령어 구현
Google Sheets의 아이템 정보를 바탕으로 아이템 양도 기능을 제공하는 명령어 클래스입니다.
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
    from utils.korean_utils import add_eul_reul, add_eun_neun, get_last_char, has_final_consonant
    from utils.store_helpers import serialize_inventory, extract_price_info
    from utils.lock_manager import get_lock_manager
    from commands.store.base_store_command import BaseStoreCommand
    from commands.registry import register_command
    from models.user import User
except ImportError as e:
    import logging
    logger = logging.getLogger('transfer_command')
    logger.error(f"필수 모듈 임포트 실패: {e}")
    raise


@register_command(
    name="양도",
    aliases=["선물", "주기", "전달", "transfer", "gift"],
    description="아이템 또는 화폐를 다른 캐릭터에게 양도합니다.",
    category="상점",
    examples=["[양도/사과/테스트]", "[양도/100/테스트]"],
    requires_sheets=True,
    requires_api=False
)
class TransferCommand(BaseStoreCommand):
    """
    양도 명령어 클래스

    Google Sheets의 '상점' 시트에서 아이템 정보를 확인하고,
    '명단' 시트의 사용자 인벤토리를 업데이트하여 아이템을 양도합니다.

    지원하는 형식:
    - [양도/아이템명/캐릭터명] : 해당 아이템을 지정 캐릭터에게 양도
    - [선물/아이템명/캐릭터명] : 해당 아이템을 지정 캐릭터에게 선물
    """

    _sheet_error_suffix = " (양도)"
    _generic_error_prefix = "아이템 양도 처리 중"

    @staticmethod
    def get_supported_keywords() -> List[str]:
        """지원 키워드 (대표 우선)"""
        return ['양도', '선물', '주기', '전달']

    def __init__(self, sheets_manager=None, api=None, **kwargs):
        """
        TransferCommand 초기화

        Args:
            sheets_manager: Google Sheets 관리자
            api: 마스토돈 API 인스턴스
            **kwargs: 추가 의존성
        """
        super().__init__(sheets_manager, api, **kwargs)
        logger.debug(f"TransferCommand 초기화 완료: sheets_manager={self.sheets_manager is not None}")

    def _execute_command_logic(self, user: User, keywords: List[str]) -> Tuple[str, Dict[str, Any]]:
        """
        양도 명령어 실행 (아이템/화폐 자동 감지)

        Args:
            user: 사용자 객체
            keywords: 키워드 리스트 ([양도, 아이템명/금액, 캐릭터명])

        Returns:
            Tuple[str, TransferResult]: (결과 메시지, 양도 결과 객체)

        Raises:
            CommandError: 양도 처리 실패
        """
        # 키워드 파싱 (아이템/화폐 자동 감지)
        parsed_data = self._parse_transfer_keywords(keywords)
        transfer_type = parsed_data.get('type', 'item')

        # 타입에 따라 프리미엄 기능 체크 및 분기
        if transfer_type == 'money':
            if not config.PREMIUM_TRANSFER_MONEY_ENABLED:
                raise CommandError("소지금 양도 기능은 현재 비활성화되어 있습니다.")
            return self._execute_money_transfer(user, parsed_data)
        else:
            if not config.PREMIUM_TRANSFER_ITEM_ENABLED:
                raise CommandError("아이템 양도 기능은 현재 비활성화되어 있습니다.")
            return self._execute_item_transfer(user, parsed_data)
    
    def _execute_item_transfer(self, user: User, parsed_data: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        """
        아이템 양도 실행

        Args:
            user: 사용자 객체
            parsed_data: 파싱된 데이터

        Returns:
            Tuple[str, Dict]: (결과 메시지, 양도 결과 객체)
        """
        item_name = parsed_data['item_name']
        character_name = parsed_data['character_name']

        # 아이템 정보 확인 (을/를 조사 포함)
        item_info = self._get_item_info_with_particle(item_name)
        if not item_info:
            raise CommandError(f"'{item_name}' 아이템을 찾을 수 없습니다. 상점 목록을 확인해 주세요.")

        # 양도자(명령어 사용자) 정보 조회
        giver_info = self._get_user_info_by_id(user.id)
        if not giver_info:
            raise CommandError(f"{user.get_display_name()} 님의 정보를 찾을 수 없습니다. 명단에 등록되어 있는지 확인해 주세요.")

        # 수령자(캐릭터명) 정보 조회
        receiver_info = self._get_user_info_by_name(character_name)
        if not receiver_info:
            raise CommandError(f"'{character_name}' 캐릭터를 찾을 수 없습니다. 명단에 등록되어 있는지 확인해 주세요.")

        # 자기 자신에게 양도 방지
        if user.id == receiver_info['아이디']:
            raise CommandError("자기 자신에게는 아이템을 양도할 수 없습니다.")

        # 아이템명 유효성 확인
        if not item_name or item_name.strip() == '':
            raise CommandError("아이템명이 비어있습니다.")

        # 데드락 방지: ID 정렬 순서로 Lock 획득
        lock_manager = get_lock_manager()
        lock_ids = sorted([user.id, receiver_info['아이디']])

        with lock_manager.acquire_lock(lock_ids[0], timeout=10.0) as acquired1:
            if not acquired1:
                raise CommandError("다른 거래가 처리 중입니다. 잠시 후 다시 시도해 주세요.")
            with lock_manager.acquire_lock(lock_ids[1], timeout=10.0) as acquired2:
                if not acquired2:
                    raise CommandError("다른 거래가 처리 중입니다. 잠시 후 다시 시도해 주세요.")

                # Lock 내부에서 양도자 정보 재조회 (stale read 방지)
                giver_info = self._get_user_info_by_id(user.id)
                if not giver_info:
                    raise CommandError(f"{user.get_display_name()} 님의 정보를 찾을 수 없습니다.")

                # 양도자 인벤토리에 아이템 보유 재확인
                if not self._check_item_ownership(giver_info, item_name):
                    item_particle = add_eul_reul(item_name)
                    raise CommandError(f"{item_particle} 보유하고 있지 않습니다.")

                # 양도 처리 (시트 업데이트)
                success = self._process_transfer(
                    giver_info,
                    receiver_info,
                    item_name
                )

                if not success:
                    raise CommandError("양도 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.")
        
        # 양도자의 이/가 조사 결정
        from utils.korean_utils import add_i_ga
        giver_i_ga = add_i_ga(giver_info['이름'])

        # DM 전송 (을/를 조사 사용)
        item_particle = add_eul_reul(item_name)
        dm_sent = self._send_transfer_dm(
            receiver_info,
            giver_info,
            item_name,
            item_particle
        )

        # 메시지/페이로드 구성
        message = self._build_transfer_message(
            receiver_name=receiver_info['이름'],
            item_name=item_name,
            item_particle=item_particle
        )
        payload = {
            'giver_name': giver_info['이름'],
            'giver_id': user.id,
            'receiver_name': receiver_info['이름'],
            'receiver_id': receiver_info['아이디'],
            'item_name': item_name,
            'item_particle': item_particle,
            'dm_sent': dm_sent
        }
        return message, payload
    
    def _parse_transfer_keywords(self, keywords: List[str]) -> Dict[str, Any]:
        """
        양도 키워드 파싱 (아이템/화폐 자동 감지)
        
        Args:
            keywords: 키워드 리스트
            
        Returns:
            Dict: {
                'type': 'item' or 'money',
                'item_name': str or None,
                'amount': int or None,
                'character_name': str
            }
            
        Raises:
            CommandError: 파싱 실패
        """
        if len(keywords) < 3:
            raise CommandError(
                "사용법: [양도/아이템명 또는 금액/캐릭터명]\n"
                "예: [양도/사과/테스트] (아이템 양도)\n"
                f"예: [양도/100/테스트] (화폐 양도)"
            )
        
        second_keyword = keywords[1].strip()
        character_name = keywords[2].strip()
        
        if not character_name:
            raise CommandError("캐릭터명이 비어있습니다.")
        
        # 숫자인지 확인 (화폐 양도)
        if second_keyword.isdigit():
            amount = int(second_keyword)
            if amount <= 0:
                raise CommandError("양도 금액은 1 이상이어야 합니다.")
            
            return {
                'type': 'money',
                'amount': amount,
                'character_name': character_name
            }
        
        # 화폐 단위 포함 여부 확인 (예: "100코인", "500포인트")
        currency = config.CURRENCY
        if second_keyword.endswith(currency):
            amount_str = second_keyword.replace(currency, '').strip()
            if amount_str.isdigit():
                amount = int(amount_str)
                if amount <= 0:
                    raise CommandError("양도 금액은 1 이상이어야 합니다.")
                return {
                    'type': 'money',
                    'amount': amount,
                    'character_name': character_name
                }
        
        # 그 외는 아이템으로 처리
        if not second_keyword:
            raise CommandError("아이템명 또는 금액이 비어있습니다.")
        
        return {
            'type': 'item',
            'item_name': second_keyword,
            'character_name': character_name
        }
    
    def _normalize_item_name(self, item_name: str) -> str:
        """
        아이템명 정규화 (띄어쓰기 + 특수문자 제거)

        Args:
            item_name: 정규화할 아이템명

        Returns:
            str: 정규화된 아이템명 (소문자, 띄어쓰기 제거, 특수문자 제거)
        """
        # 특수문자 제거 (*, ^, !, ?, 등)
        normalized = re.sub(r'[*^!?\s]', '', item_name)
        return normalized.strip().lower()

    def _get_item_info_with_particle(self, item_name: str) -> Optional[Dict[str, Any]]:
        """
        아이템 정보 조회 (을/를 조사 포함)

        Args:
            item_name: 아이템명

        Returns:
            Optional[Dict]: 아이템 정보 {'name': str, 'price': int, 'description': str, '을를': str} 또는 None
        """
        # 아이템 데이터 로드
        item_data_list = self._load_item_data()

        if not item_data_list:
            logger.warning("아이템 데이터가 없습니다.")
            return None

        # 아이템명 정규화 (띄어쓰기 + 특수문자 제거)
        normalized_input = self._normalize_item_name(item_name)

        for item_data in item_data_list:
            stored_name = str(item_data.get('아이템명', '')).strip()
            normalized_stored = self._normalize_item_name(stored_name)

            if normalized_stored == normalized_input:
                # 동적 키 검색: 가격 컬럼
                price_key, _, currency_from_price = extract_price_info(item_data)

                # 가격 파싱 (비매품 처리 포함)
                price = 0
                if price_key:
                    price_str = str(item_data.get(price_key, '0')).strip()
                    # '비매품' 체크 (shop_command.py와 동일한 로직)
                    if '비매품' in price_str:
                        logger.debug(f"비매품 아이템: {stored_name} (양도/사용은 가능)")
                        price = 0  # 비매품은 가격 0으로 처리
                    else:
                        try:
                            price = int(float(price_str))
                        except (ValueError, TypeError):
                            logger.warning(f"아이템 '{stored_name}'의 가격 파싱 실패: {price_str}")
                            price = 0
                else:
                    logger.warning(f"아이템 '{stored_name}'의 가격 컬럼을 찾을 수 없습니다.")

                return {
                    'name': stored_name,  # 원본 이름 사용
                    'price': price,
                    'description': str(item_data.get('설명', '')).strip(),
                    'currency_unit': currency_from_price
                }
        
        return None
    
    def _get_user_info_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        """
        사용자 ID로 사용자 정보 조회 (명단 캐시 + 관리 워크시트)
        
        Args:
            user_id: 사용자 ID
            
        Returns:
            Optional[Dict]: 사용자 정보
        """
        # 1) 명단 캐시에서 사용자 존재/이름/조사 확인
        roster = self._load_user_data()
        if not roster:
            logger.warning("명단 데이터가 없습니다.")
            return None

        name = None
        suffix = '은'
        for row in roster:
            if str(row.get('아이디', '')).strip() == user_id:
                name = str(row.get('이름', user_id)).strip()
                suffix_val = str(row.get('은는', '은')).strip()
                suffix = suffix_val if suffix_val in ['은', '는'] else '은'
                break

        if not name:
            logger.info(f"명단에서 사용자 {user_id}를 찾지 못함")
            return None

        # 2) 관리 워크시트에서 소지품 조회 (캐시 사용 안 함)
        try:
            management_rows = self.sheets_manager.get_worksheet_data('관리', use_cache=False)
        except Exception as e:
            logger.error(f"관리 워크시트 조회 중 오류: {e}", exc_info=True)
            return None

        for row in management_rows:
            if str(row.get('아이디', '')).strip() == user_id:
                inventory_str = str(row.get('소지품', '')).strip()
                inventory = self._parse_inventory(inventory_str)
                money_value = row.get('소지금', 0)  # 소지금도 가져오기
                logger.debug(f"사용자 {user_id} ({name}): 관리 워크시트에서 소지품/소지금 조회 -> {inventory}, {money_value}")
                return {
                    '이름': name,
                    '아이디': user_id,
                    '인벤토리': inventory,
                    '소지금': money_value,  # 소지금 추가
                    '은는': suffix
                }

        logger.warning(f"관리 워크시트에서 사용자 {user_id}를 찾지 못함 (명단에는 존재)")
        return None
    
    def _get_user_info_by_name(self, character_name: str) -> Optional[Dict[str, Any]]:
        """
        캐릭터명으로 사용자 정보 조회 (명단 캐시 + 관리 워크시트)
        
        Args:
            character_name: 캐릭터명
            
        Returns:
            Optional[Dict]: 사용자 정보
        """
        user_data_list = self._load_user_data()
        if not user_data_list:
            return None

        # 캐릭터명 매칭 (대소문자 구분 없음)
        target = None
        character_name_lower = character_name.lower()
        for row in user_data_list:
            stored_name = str(row.get('이름', '')).strip()
            if stored_name.lower() == character_name_lower:
                target = row
                break

        if not target:
            return None

        target_id = str(target.get('아이디', '')).strip()
        suffix = str(target.get('은는', '은')).strip()
        if suffix not in ['은', '는']:
            suffix = '은'

        # 관리 워크시트에서 소지품 조회
        try:
            management_rows = self.sheets_manager.get_worksheet_data('관리', use_cache=False)
        except Exception as e:
            logger.error(f"관리 워크시트 조회 중 오류: {e}")
            return None

        for row in management_rows:
            if str(row.get('아이디', '')).strip() == target_id:
                inventory_str = str(row.get('소지품', '')).strip()
                inventory = self._parse_inventory(inventory_str)
                money_value = row.get('소지금', 0)  # 소지금도 가져오기
                logger.debug(f"사용자 {target_id} ({stored_name}): 관리 워크시트에서 소지품/소지금 조회 -> {inventory}, {money_value}")
                return {
                    '이름': stored_name,
                    '아이디': target_id,
                    '인벤토리': inventory,
                    '소지금': money_value,  # 소지금 추가
                    '은는': suffix
                }

        logger.warning(f"관리 워크시트에서 사용자 {target_id}를 찾지 못함 (명단에는 존재)")
        return None
    
    def _find_actual_item_name(self, inventory: Dict[str, int], item_name: str) -> Optional[str]:
        """
        인벤토리에서 정규화 매칭으로 실제 아이템명 찾기

        Args:
            inventory: 인벤토리 딕셔너리
            item_name: 검색할 아이템명

        Returns:
            Optional[str]: 인벤토리에 저장된 실제 아이템명 또는 None
        """
        normalized_input = self._normalize_item_name(item_name)

        for stored_item in inventory.keys():
            normalized_stored = self._normalize_item_name(stored_item)
            if normalized_stored == normalized_input:
                return stored_item

        return None

    def _check_item_ownership(self, user_info: Dict, item_name: str) -> bool:
        """
        사용자가 아이템을 보유하고 있는지 확인 (정규화 적용)

        Args:
            user_info: 사용자 정보
            item_name: 아이템명

        Returns:
            bool: 보유 여부 (1개 이상)
        """
        inventory = user_info.get('인벤토리', {})
        actual_item_name = self._find_actual_item_name(inventory, item_name)

        if actual_item_name and inventory.get(actual_item_name, 0) >= 1:
            return True

        return False
    
    def _process_transfer(self, giver_info: Dict, receiver_info: Dict, item_name: str) -> bool:
        """
        양도 처리 (시트 업데이트)
        
        Args:
            giver_info: 양도자 정보
            receiver_info: 수령자 정보
            item_name: 아이템명
            
        Returns:
            bool: 처리 성공 여부
        """
        try:
            # 실제 시트에서 재확인 (동시성 체크)
            giver_info_updated = self._get_user_info_by_id(giver_info['아이디'])
            receiver_info_updated = self._get_user_info_by_name(receiver_info['이름'])
            
            if not giver_info_updated or not receiver_info_updated:
                logger.error("사용자 정보 재확인 실패")
                return False
            
            # 아이템 재확인 (동시에 다른 사람이 양도했을 수 있음)
            if not self._check_item_ownership(giver_info_updated, item_name):
                logger.warning(f"양도 실패: 아이템 보유하지 않음. {giver_info['아이디']}, {item_name}")
                return False

            # 양도자 인벤토리에서 실제 아이템명 찾기
            giver_inventory = giver_info_updated['인벤토리'].copy()
            actual_giver_item_name = self._find_actual_item_name(giver_inventory, item_name)

            if not actual_giver_item_name:
                logger.error(f"양도 실패: 인벤토리에서 실제 아이템명을 찾을 수 없음. {item_name}")
                return False

            # 양도자 인벤토리 업데이트 (실제 아이템명 사용)
            current_count = giver_inventory.get(actual_giver_item_name, 0)

            if current_count <= 1:
                # 1개 이하면 인벤토리에서 완전 삭제
                if actual_giver_item_name in giver_inventory:
                    del giver_inventory[actual_giver_item_name]
            else:
                # 1개 감소
                giver_inventory[actual_giver_item_name] = current_count - 1

            # 수령자 인벤토리 업데이트
            # 수령자 인벤토리에 이미 있으면 실제 이름 사용, 없으면 상점에서 가져온 이름 사용
            receiver_inventory = receiver_info_updated['인벤토리'].copy()
            actual_receiver_item_name = self._find_actual_item_name(receiver_inventory, item_name)

            if actual_receiver_item_name:
                # 이미 보유 중이면 기존 이름 사용
                receiver_inventory[actual_receiver_item_name] = receiver_inventory.get(actual_receiver_item_name, 0) + 1
            else:
                # 처음 받는 아이템이면 양도자가 가진 실제 이름 사용
                receiver_inventory[actual_giver_item_name] = receiver_inventory.get(actual_giver_item_name, 0) + 1
            
            # 양도자/수령자 행과 소지품 컬럼 조회
            giver_row = self._find_user_row(giver_info_updated['아이디'])
            receiver_row = self._find_user_row(receiver_info_updated['아이디'])
            inventory_col = self._find_inventory_column()

            if not giver_row or not receiver_row or not inventory_col:
                logger.error("아이템 양도: 행/컬럼 조회 실패")
                return False

            giver_inventory_str = serialize_inventory(giver_inventory)
            receiver_inventory_str = serialize_inventory(receiver_inventory)

            # 원자적 배치 업데이트 (양도자 차감 + 수령자 추가를 한 번에)
            updates = [
                (giver_row, inventory_col, giver_inventory_str),
                (receiver_row, inventory_col, receiver_inventory_str),
            ]
            batch_success = self.sheets_manager.batch_update_cells('관리', updates)

            if batch_success:
                # 캐시 무효화
                self._invalidate_user_cache()
                logger.info(f"양도 처리 완료: {giver_info['아이디']} -> {receiver_info['아이디']} | {item_name}")
                return True
            else:
                logger.error("양도 처리 중 시트 업데이트 실패")
                return False
                
        except Exception as e:
            logger.error(f"양도 처리 실패: {e}")
            return False
    
    def _update_user_inventory(self, user_id: str, new_inventory: Dict[str, int]) -> bool:
        """
        사용자 인벤토리를 '관리' 워크시트에 업데이트
        
        Args:
            user_id: 사용자 ID
            new_inventory: 새로운 인벤토리
            
        Returns:
            bool: 업데이트 성공 여부
        """
        try:
            if not self.sheets_manager:
                logger.error("시트 매니저가 없습니다.")
                return False
            
            # 사용자 행 찾기 (관리 워크시트)
            user_row = self._find_user_row(user_id)
            if user_row is None:
                logger.error(f"사용자 행을 찾을 수 없습니다: {user_id}")
                return False
            
            # 인벤토리를 단순 텍스트 형식으로 변환
            inventory_str = serialize_inventory(new_inventory)
            
            # 관리 워크시트의 '소지품' 컬럼 업데이트
            worksheet_name = '관리'

            inventory_col = self._find_inventory_column()
            if inventory_col is None:
                logger.error("소지품 컬럼을 찾을 수 없습니다 (관리).")
                return False

            success = self.sheets_manager.update_cell(worksheet_name, user_row, inventory_col, inventory_str)
            
            return success
            
        except Exception as e:
            logger.error(f"인벤토리 업데이트 실패: {user_id} -> {e}")
            return False
    
    def _send_transfer_dm(self, receiver_info: Dict, giver_info: Dict, item_name: str, item_particle: str) -> bool:
        """
        양도 알림 DM 전송
        
        Args:
            receiver_info: 수령자 정보
            giver_info: 양도자 정보
            item_name: 아이템명
            item_particle: 아이템 을/를 조사
            
        Returns:
            bool: DM 전송 성공 여부
        """
        try:
            from utils.korean_utils import add_i_ga
            
            # 양도자의 이/가 조사 결정
            giver_i_ga = add_i_ga(giver_info['이름'])
            
            # DM 메시지 구성: "{양도자}{이가} 당신에게 {아이템}{을를} 양도했습니다."
            dm_message = f"{giver_i_ga} 당신에게 {item_particle} 양도했습니다."
            
            # DM 전송 (stream_handler에서 처리하도록 정보만 저장)
            self._store_dm_info(receiver_info['아이디'], dm_message)
            
            logger.info(f"DM 정보 저장 완료: {receiver_info['아이디']} <- {dm_message}")
            return True
            
        except Exception as e:
            logger.error(f"DM 전송 실패: {e}")
            return False
        
    def _store_dm_info(self, receiver_id: str, dm_message: str):
        """
        DM 정보 저장 (실제 전송은 stream_handler에서 처리)
        
        Args:
            receiver_id: 수령자 ID
            dm_message: DM 메시지
        """
        # 임시로 클래스 변수에 저장
        # 실제로는 전역 큐나 다른 방식으로 처리 필요
        if not hasattr(self, '_pending_dms'):
            self._pending_dms = []
        
        import time
        self._pending_dms.append({
            'receiver_id': receiver_id,
            'message': dm_message,
            'timestamp': int(time.time())
        })
    
    def get_pending_dms(self) -> List[Dict]:
        """대기 중인 DM 목록 반환"""
        if hasattr(self, '_pending_dms'):
            pending = self._pending_dms.copy()
            self._pending_dms.clear()
            return pending
        return []

    def _execute_money_transfer(self, user: User, parsed_data: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        """
        화폐 양도 실행

        Args:
            user: 사용자 객체
            parsed_data: 파싱된 데이터

        Returns:
            Tuple[str, Dict]: (결과 메시지, 양도 결과 객체)
        """
        amount = parsed_data['amount']
        character_name = parsed_data['character_name']
        currency_unit = config.CURRENCY

        # 양도자 정보 조회
        giver_info = self._get_user_info_by_id(user.id)
        if not giver_info:
            raise CommandError(f"{user.get_display_name()} 님의 정보를 찾을 수 없습니다.")

        # 수령자 정보 조회
        receiver_info = self._get_user_info_by_name(character_name)
        if not receiver_info:
            raise CommandError(f"'{character_name}' 캐릭터를 찾을 수 없습니다.")

        # 자기 자신 체크
        if user.id == receiver_info['아이디']:
            raise CommandError("자기 자신에게는 양도할 수 없습니다.")

        # 데드락 방지: ID 정렬 순서로 Lock 획득
        lock_manager = get_lock_manager()
        lock_ids = sorted([user.id, receiver_info['아이디']])

        with lock_manager.acquire_lock(lock_ids[0], timeout=10.0) as acquired1:
            if not acquired1:
                raise CommandError("다른 거래가 처리 중입니다. 잠시 후 다시 시도해 주세요.")
            with lock_manager.acquire_lock(lock_ids[1], timeout=10.0) as acquired2:
                if not acquired2:
                    raise CommandError("다른 거래가 처리 중입니다. 잠시 후 다시 시도해 주세요.")

                # Lock 내부에서 잔액 재확인 (stale read 방지)
                giver_info = self._get_user_info_by_id(user.id)
                if not giver_info:
                    raise CommandError(f"{user.get_display_name()} 님의 정보를 찾을 수 없습니다.")

                current_balance = self._parse_money(giver_info.get('소지금', 0))
                if current_balance < amount:
                    raise CommandError(f"소지금이 부족합니다. 현재 보유: {current_balance}{currency_unit}")

                # 화폐 양도 처리
                success = self._process_money_transfer(giver_info, receiver_info, amount)
                if not success:
                    raise CommandError("화폐 양도 처리 중 오류가 발생했습니다.")
        
        # 잔액 계산 (메시지용)
        new_giver_balance = self._parse_money(giver_info.get('소지금', 0)) - amount
        new_receiver_balance = self._parse_money(receiver_info.get('소지금', 0)) + amount
        
        # 결과 메시지 생성
        from utils.korean_utils import add_eul_reul, add_eun_neun, add_i_ga
        
        # 화폐 조사 처리
        currency_eul_reul = add_eul_reul(currency_unit)  # 을/를 조사는 동적 계산
        receiver_eun_neun = add_eun_neun(receiver_info['이름'])  # 수령자의 은/는 조사도 동적 계산
        giver_i_ga = add_i_ga(giver_info['이름'])
        
        # DM 전송
        dm_sent = self._send_money_transfer_dm(
            receiver_info,
            giver_info,
            amount,
            currency_unit,
            currency_eul_reul,
            new_receiver_balance
        )
        
        message = f"{amount} {currency_eul_reul} {receiver_info['이름']}에게 양도했습니다. 현재 소지금은 {new_giver_balance:,} {currency_unit}입니다."
        
        payload = {
            'type': 'money_transfer',
            'giver_name': giver_info['이름'],
            'giver_id': user.id,
            'receiver_name': receiver_info['이름'],
            'receiver_id': receiver_info['아이디'],
            'amount': amount,
            'currency': currency_unit,
            'giver_new_balance': new_giver_balance,
            'receiver_new_balance': new_receiver_balance,
            'dm_sent': dm_sent
        }
        
        return message, payload
    
    def _parse_money(self, money_value: Any) -> int:
        """
        금액 파싱
        
        Args:
            money_value: 금액 값
            
        Returns:
            int: 파싱된 금액
        """
        if isinstance(money_value, (int, float)):
            return int(money_value)
        
        if isinstance(money_value, str):
            # 쉼표 제거 (예: "1,000" → "1000")
            cleaned = money_value.replace(',', '').strip()
            try:
                return int(float(cleaned))
            except (ValueError, TypeError):
                pass
        
        return 0
    
    def _process_money_transfer(self, giver_info: Dict, receiver_info: Dict, amount: int) -> bool:
        """
        화폐 양도 처리 (시트 업데이트)
        
        Args:
            giver_info: 양도자 정보
            receiver_info: 수령자 정보
            amount: 양도 금액
            
        Returns:
            bool: 처리 성공 여부
        """
        try:
            # 실제 시트에서 재확인 (동시성 체크)
            giver_info_updated = self._get_user_info_by_id(giver_info['아이디'])
            receiver_info_updated = self._get_user_info_by_name(receiver_info['이름'])
            
            if not giver_info_updated or not receiver_info_updated:
                logger.error("사용자 정보 재확인 실패")
                return False
            
            # 현재 잔액 재확인
            giver_balance = self._parse_money(giver_info_updated.get('소지금', 0))
            receiver_balance = self._parse_money(receiver_info_updated.get('소지금', 0))
            
            # 잔액 재확인
            if giver_balance < amount:
                logger.warning(f"양도 실패: 잔액 부족. 현재: {giver_balance}, 필요: {amount}")
                return False
            
            # 새 잔액 계산 (음수 방지)
            new_giver_balance = max(0, giver_balance - amount)
            new_receiver_balance = receiver_balance + amount
            
            # 양도자/수령자 행과 소지금 컬럼 조회
            giver_row = self._find_user_row(giver_info_updated['아이디'])
            receiver_row = self._find_user_row(receiver_info_updated['아이디'])
            money_column = self._find_money_column()

            if not giver_row or not receiver_row or not money_column:
                logger.error("화폐 양도: 행/컬럼 조회 실패")
                return False

            # 원자적 배치 업데이트 (양도자 차감 + 수령자 추가를 한 번에)
            updates = [
                (giver_row, money_column, new_giver_balance),
                (receiver_row, money_column, new_receiver_balance),
            ]
            batch_success = self.sheets_manager.batch_update_cells('관리', updates)

            if batch_success:
                # 캐시 무효화
                self._invalidate_user_cache()
                logger.info(f"화폐 양도 처리 완료: {giver_info['아이디']} -> {receiver_info['아이디']} | {amount}")
                return True
            else:
                logger.error("화폐 양도 처리 중 시트 업데이트 실패")
                return False
                
        except Exception as e:
            logger.error(f"화폐 양도 처리 실패: {e}")
            return False
    
    def _update_user_money(self, user_id: str, new_money: int) -> bool:
        """
        사용자 소지금 업데이트
        
        Args:
            user_id: 사용자 ID
            new_money: 새로운 소지금
            
        Returns:
            bool: 업데이트 성공 여부
        """
        try:
            if not self.sheets_manager:
                logger.error("시트 매니저가 없습니다.")
                return False
            
            # 사용자 행 찾기
            user_row = self._find_user_row(user_id)
            if user_row is None:
                logger.error(f"사용자 행을 찾을 수 없습니다: {user_id}")
                return False
            
            # 소지금 컬럼 찾기
            money_column = self._find_money_column()
            if money_column is None:
                logger.error("소지금 컬럼을 찾을 수 없습니다.")
                return False
            
            # 시트 업데이트
            success = self.sheets_manager.update_cell('관리', user_row, money_column, new_money)
            return success
            
        except Exception as e:
            logger.error(f"소지금 업데이트 실패: {user_id} -> {e}")
            return False

    def _send_money_transfer_dm(self, receiver_info: Dict, giver_info: Dict, amount: int, currency_unit: str, particle: str, new_balance: int) -> bool:
        """
        화폐 양도 알림 DM 전송
        
        Args:
            receiver_info: 수령자 정보
            giver_info: 양도자 정보
            amount: 양도된 금액
            currency_unit: 화폐 단위
            particle: 을/를 조사
            new_balance: 수령자의 새로운 잔액
            
        Returns:
            bool: DM 전송 성공 여부
        """
        try:
            from utils.korean_utils import add_i_ga
            
            # 양도자의 이/가 조사 결정
            # add_i_ga 는 **단어+조사 전체**('데보라가')를 돌려준다.
            # 앞에 이름을 또 붙이면 '데보라데보라가'가 되어 DM으로 나간다.
            giver_with_josa = add_i_ga(giver_info['이름'])

            # DM 메시지 구성
            dm_message = f"{giver_with_josa} 당신에게 {amount:,}{currency_unit}{particle} 양도했습니다. 현재 소지금은 {new_balance:,}{currency_unit}입니다."
            
            # DM 전송 (stream_handler에서 처리하도록 정보만 저장)
            self._store_dm_info(receiver_info['아이디'], dm_message)
            
            logger.info(f"DM 정보 저장 완료: {receiver_info['아이디']} <- {dm_message}")
            return True
            
        except Exception as e:
            logger.error(f"DM 전송 실패: {e}")
            return False
    
    def _build_transfer_message(self, receiver_name: str, item_name: str, item_particle: str) -> str:
        return f"{item_particle} {receiver_name}에게 양도했습니다."
    
    def get_help_text(self) -> str:
        """도움말 텍스트 반환 (활성화된 기능만 표시)"""
        currency = config.CURRENCY
        parts = []

        if config.PREMIUM_TRANSFER_ITEM_ENABLED and config.PREMIUM_TRANSFER_MONEY_ENABLED:
            parts.append(f"아이템 또는 {currency}")
        elif config.PREMIUM_TRANSFER_ITEM_ENABLED:
            parts.append("아이템")
        elif config.PREMIUM_TRANSFER_MONEY_ENABLED:
            parts.append(currency)
        else:
            return "양도 기능이 비활성화되어 있습니다."

        return f"[양도/아이템명 또는 금액/캐릭터명] - {'/'.join(parts)}을 다른 캐릭터에게 양도할 수 있습니다."
    
    def get_extended_help(self) -> str:
        """확장 도움말 반환 (활성화된 기능만 표시)"""
        if not config.PREMIUM_TRANSFER_ITEM_ENABLED and not config.PREMIUM_TRANSFER_MONEY_ENABLED:
            return "양도 기능이 비활성화되어 있습니다."

        currency = config.CURRENCY
        help_parts = [self.get_help_text(), ""]

        if config.PREMIUM_TRANSFER_ITEM_ENABLED:
            help_parts.append("📋 아이템 양도:")
            help_parts.append("[양도/반지/테스트] - 반지를 테스트에게 양도")
            help_parts.append("[선물/사과/한참] - 사과를 한참에게 선물")
            help_parts.append("")

        if config.PREMIUM_TRANSFER_MONEY_ENABLED:
            help_parts.append(f"💰 화폐 양도:")
            help_parts.append(f"[양도/100/테스트] - {currency} 100을 테스트에게 양도")
            help_parts.append(f"[양도/500/한참] - {currency} 500을 한참에게 양도")
            help_parts.append("")

        help_parts.append("🎁 특징:")

        if config.PREMIUM_TRANSFER_ITEM_ENABLED and config.PREMIUM_TRANSFER_MONEY_ENABLED:
            help_parts.append(f"• 보유한 아이템 또는 {currency}만 양도할 수 있습니다.")
        elif config.PREMIUM_TRANSFER_ITEM_ENABLED:
            help_parts.append("• 보유한 아이템만 양도할 수 있습니다.")
        elif config.PREMIUM_TRANSFER_MONEY_ENABLED:
            help_parts.append(f"• 보유한 {currency}만 양도할 수 있습니다.")

        help_parts.append("• 양도 후 수령자에게 DM이 전송됩니다.")
        help_parts.append("• 자기 자신에게는 양도할 수 없습니다.")

        if config.PREMIUM_TRANSFER_ITEM_ENABLED:
            help_parts.append("• 아이템 양도 후 0개가 되면 인벤토리에서 삭제됩니다.")
        if config.PREMIUM_TRANSFER_MONEY_ENABLED:
            help_parts.append("• 화폐는 양도자에서 차감되고 수령자에 추가됩니다.")

        return "\n".join(help_parts)
    
    def validate_transfer_system(self) -> Dict[str, Any]:
        """
        양도 시스템 유효성 검증
        
        Returns:
            Dict: 검증 결과
        """
        results = {
            'valid': True,
            'errors': [],
            'warnings': [],
            'info': {}
        }
        
        try:
            # 아이템 데이터 확인
            items = self._load_item_data()
            if not items:
                results['errors'].append("양도 가능한 아이템이 없습니다.")
            else:
                results['info']['available_items'] = len(items)
                
                # 을/를 조사 누락 확인
                missing_particles = [item for item in items if not item.get('을를')]
                if missing_particles:
                    results['warnings'].append(f"을/를 조사가 없는 아이템이 {len(missing_particles)}개 있습니다.")
            
            # 사용자 데이터 확인
            users = self._load_user_data()
            if not users:
                results['errors'].append("등록된 사용자가 없습니다.")
            else:
                results['info']['registered_users'] = len(users)
                
                # 은/는 조사 누락 확인
                missing_eun_neun = [user for user in users if not user.get('은는')]
                if missing_eun_neun:
                    results['warnings'].append(f"은/는 조사가 없는 사용자가 {len(missing_eun_neun)}명 있습니다.")
            
            # 시트 매니저 확인
            if not self.sheets_manager:
                results['errors'].append("시트 매니저가 연결되지 않았습니다.")
            
            if results['errors']:
                results['valid'] = False
            
        except Exception as e:
            results['valid'] = False
            results['errors'].append(f"검증 중 오류: {str(e)}")
        
        return results


# 양도 관련 유틸리티 함수들
def is_transfer_command(keyword: str) -> bool:
    """
    키워드가 양도 명령어인지 확인
    
    Args:
        keyword: 확인할 키워드
        
    Returns:
        bool: 양도 명령어 여부
    """
    if not keyword:
        return False
    
    keyword = keyword.lower().strip()
    return keyword in ['아이템양도', '아이템 양도', '아이템 선물', '아이템선물', '양도', 'transfer', 'gift']


def parse_transfer_command(keywords: List[str]) -> Tuple[str, str]:
    """
    양도 명령어 파싱 (독립 함수)
    
    Args:
        keywords: 키워드 리스트
        
    Returns:
        Tuple[str, str]: (아이템명, 캐릭터명)
        
    Raises:
        ValueError: 파싱 실패
    """
    if len(keywords) < 3:
        raise ValueError("아이템명과 캐릭터명이 필요합니다.")
    
    item_name = keywords[1].strip()
    character_name = keywords[2].strip()
    
    if not item_name:
        raise ValueError("아이템명이 비어있습니다.")
    
    if not character_name:
        raise ValueError("캐릭터명이 비어있습니다.")
    
    return item_name, character_name


# 양도 명령어 인스턴스 생성 함수
def create_transfer_command(sheets_manager=None) -> TransferCommand:
    """
    양도 명령어 인스턴스 생성
    
    Args:
        sheets_manager: Google Sheets 관리자
        
    Returns:
        TransferCommand: 양도 명령어 인스턴스
    """
    return TransferCommand(sheets_manager)
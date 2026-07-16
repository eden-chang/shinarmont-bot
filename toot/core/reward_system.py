"""
core/reward_system.py
재화 지급 시스템 모듈
상점봇 시트와 연동하여 재화를 지급하고 관리합니다.
"""

import os
import sys
import time
from typing import Dict, List, Optional, Tuple, NamedTuple, Any
from dataclasses import dataclass
from datetime import datetime
import pytz

# VM 환경 대응
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
sys.path.append(project_root)

try:
    from config.settings import config
    from utils.error_handling import (
        safe_execute, RewardSystemError, UserNotFoundError,
        ErrorContext, log_error_context
    )
    from utils.logging_config import logger, toot_logger, log_reward_transaction
    from utils.sheets_operations import get_sheets_manager
    from core.toot_counter import TootCountResult
except ImportError as e:
    # 임포트 실패 시 기본 로거
    import logging
    logger = logging.getLogger('reward_system')
    print(f"임포트 실패: {e}")


def _parse_int(value: Any) -> int:
    """시트에서 읽어온 값을 안전하게 정수로 변환"""
    try:
        if value is None:
            return 0
        return int(str(value).replace(',', '').strip())
    except (ValueError, TypeError):
        return 0


@dataclass
class RewardResult:
    """재화 지급 결과"""
    user_id: str
    username: str
    money_given: int
    old_money_balance: int
    new_money_balance: int
    success: bool
    error: Optional[str] = None

    # 하위 호환성을 위한 속성
    @property
    def amount_given(self) -> int:
        """지급된 소지금 (하위 호환성)"""
        return self.money_given

    @property
    def old_balance(self) -> int:
        """이전 소지금 (하위 호환성)"""
        return self.old_money_balance

    @property
    def new_balance(self) -> int:
        """새 소지금 (하위 호환성)"""
        return self.new_money_balance

    @property
    def balance_change(self) -> int:
        """소지금 변화량"""
        return self.new_money_balance - self.old_money_balance


@dataclass
class RewardSummary:
    """재화 지급 요약"""
    total_users: int
    successful_rewards: int
    failed_rewards: int
    total_amount_given: int
    success_rate: float
    errors: List[str]
    timestamp: str


class RewardSystem:
    """재화 지급 시스템 클래스"""
    
    def __init__(self):
        """RewardSystem 초기화"""
        self.sheets_manager = get_sheets_manager()
        self.reward_amount_per_unit = config.REWARD_AMOUNT

        # 헤더 캐싱 (API 호출 최소화)
        self._money_col_cache = None
        self._money_cache_timestamp = 0.0
        # 시트 구조가 변경될 수 있으므로 TTL 적용 (기본 1시간, 설정값 기반)
        cache_minutes = max(config.CACHE_DURATION_MINUTES, 1)
        self._cache_ttl_seconds = cache_minutes * 60

    @property
    def money_col(self) -> Optional[int]:
        """소지금 컬럼 인덱스 (캐싱됨)"""
        if (
            self._money_col_cache is None
            or (time.time() - self._money_cache_timestamp) > self._cache_ttl_seconds
        ):
            self._money_col_cache = self.sheets_manager.get_money_column_index()
            self._money_cache_timestamp = time.time()
            logger.debug(f"소지금 컬럼 캐시 생성/갱신: {self._money_col_cache}")
        return self._money_col_cache

    def invalidate_cache(self):
        """시트 컬럼 캐시 무효화"""
        self._money_col_cache = None
        self._money_cache_timestamp = 0.0
        self.sheets_manager.clear_shop_sheet_cache()
        logger.info("RewardSystem 컬럼 캐시가 무효화되었습니다.")
        
    def give_reward_to_user(self, user_id: str, money_amount: int, reason: str = "툿수 달성") -> RewardResult:
        """
        단일 사용자에게 재화 지급 (소지금)

        Args:
            user_id: 사용자 ID
            money_amount: 지급할 소지금량
            reason: 지급 사유

        Returns:
            RewardResult: 지급 결과
        """
        try:
            with ErrorContext("단일 사용자 재화 지급", user_id=user_id, money=money_amount):
                # 1. 상점봇 시트에서 사용자 찾기
                user_data = self.sheets_manager.find_user_in_shop_sheet(user_id)
                if not user_data:
                    return RewardResult(
                        user_id=user_id,
                        username="Unknown",
                        money_given=0,
                        old_money_balance=0,
                        new_money_balance=0,
                        success=False,
                        error="상점봇 시트에서 사용자를 찾을 수 없습니다."
                    )

                user_info, user_row = user_data
                username = user_info.get('이름', 'Unknown')

                # 2. 현재 소지금 조회
                old_money = self.sheets_manager.get_current_money(user_id)
                if old_money is None:
                    return RewardResult(
                        user_id=user_id,
                        username=username,
                        money_given=0,
                        old_money_balance=0,
                        new_money_balance=0,
                        success=False,
                        error="현재 소지금을 조회할 수 없습니다."
                    )

                # 3. 소지금 지급
                new_money = old_money
                if money_amount > 0:
                    if not self.sheets_manager.update_user_money(user_id, money_amount):
                        return RewardResult(
                            user_id=user_id,
                            username=username,
                            money_given=0,
                            old_money_balance=old_money,
                            new_money_balance=old_money,
                            success=False,
                            error="소지금 업데이트 실패"
                        )
                    new_money = old_money + money_amount

                # 4. 로깅
                if toot_logger and money_amount > 0:
                    toot_logger.log_reward_given(user_id, username, money_amount, new_money)

                # JSON 로그 기록
                log_reward_transaction(user_id, username, money_amount, new_money, reason)

                logger.info(f"재화 지급 성공: {username} ({user_id}) +{money_amount}소지금 → 소지금:{new_money}")

                return RewardResult(
                    user_id=user_id,
                    username=username,
                    money_given=money_amount,
                    old_money_balance=old_money,
                    new_money_balance=new_money,
                    success=True
                )

        except Exception as e:
            error_msg = f"재화 지급 중 오류 발생: {str(e)}"
            logger.error(error_msg)

            return RewardResult(
                user_id=user_id,
                username="Unknown",
                money_given=0,
                old_money_balance=0,
                new_money_balance=0,
                success=False,
                error=error_msg
            )
    
    def give_rewards_batch(self, toot_results: List[TootCountResult]) -> List[RewardResult]:
        """
        툿수 계산 결과를 바탕으로 진짜 배치 재화 지급 (API 호출 최적화)
        
        Args:
            toot_results: 툿수 계산 결과 리스트
            
        Returns:
            List[RewardResult]: 지급 결과 리스트
        """
        try:
            with ErrorContext("배치 재화 지급"):
                # 재화 지급 대상 필터링
                eligible_users = [result for result in toot_results 
                                if result.reward_eligible and result.success and result.reward_amount > 0]
                
                if not eligible_users:
                    logger.info("재화 지급 대상이 없습니다.")
                    return []
                
                logger.info(f"재화 지급 시작: {len(eligible_users)}명 (최적화된 배치 처리)")

                # 🚀 최적화: 상점봇 시트를 한 번만 읽어서 메모리에 캐시
                logger.info("상점봇 시트 데이터 캐시 생성 중...")
                try:
                    all_shop_users = self.sheets_manager.get_shop_users()
                    shop_user_map = {}

                    # 소지금 컬럼 인덱스 (캐시된 프로퍼티 사용)
                    money_col = self.money_col
                    if not money_col:
                        logger.error("소지금 컬럼을 찾을 수 없습니다.")
                        return []

                    logger.info(f"소지금 컬럼: {money_col}")

                    # 사용자 ID와 데이터 매핑 생성 (한 번만 수행)
                    for index, user_data in enumerate(all_shop_users):
                        sheet_user_id = str(user_data.get('아이디', '')).strip()
                        if sheet_user_id:
                            row_number = index + 3  # 헤더 2행이므로 +3
                            shop_user_map[sheet_user_id] = (user_data, row_number)

                    logger.info(f"상점봇 시트 캐시 완료: {len(shop_user_map)}명")
                    
                except Exception as e:
                    logger.error(f"상점봇 시트 캐시 생성 실패: {e}")
                    return []
                
                # 🚀 배치 처리: 캐시된 데이터로 모든 사용자 처리
                reward_results: List[RewardResult] = []
                reward_sources: List[TootCountResult] = []
                batch_updates = []  # 배치 업데이트용 데이터
                toot_updates = []   # 툿수 시트 업데이트용 데이터
                total_amount_scheduled = 0

                # 1단계: 모든 사용자의 업데이트 데이터 준비
                for toot_result in eligible_users:
                    try:
                        user_id_str = str(toot_result.user_id).strip()

                        # 캐시에서 사용자 정보 조회
                        if user_id_str not in shop_user_map:
                            logger.warning(f"캐시에서 사용자 찾을 수 없음: {user_id_str}")
                            reward_results.append(RewardResult(
                                user_id=toot_result.user_id,
                                username=toot_result.username,
                                money_given=0,
                                old_money_balance=0,
                                new_money_balance=0,
                                success=False,
                                error="상점봇 시트 데이터 캐시에 사용자 없음"
                            ))
                            reward_sources.append(toot_result)
                            continue

                        user_info, user_row = shop_user_map[user_id_str]
                        username = user_info.get('이름', 'Unknown')

                        # 현재 소지금 조회 (캐시에서)
                        money_key = None
                        for key in user_info.keys():
                            if '소지금' in key:
                                money_key = key
                                break

                        if not money_key:
                            logger.warning(f"소지금 컬럼 찾을 수 없음: {username}")
                            continue

                        old_money = _parse_int(user_info.get(money_key, 0))
                        new_money = old_money + toot_result.money_reward

                        reward_result = RewardResult(
                            user_id=toot_result.user_id,
                            username=username,
                            money_given=toot_result.money_reward,
                            old_money_balance=old_money,
                            new_money_balance=new_money,
                            success=True
                        )
                        reward_results.append(reward_result)
                        reward_sources.append(toot_result)

                        # 배치 업데이트 데이터 추가
                        result_index = len(reward_results) - 1
                        update_data = {
                            'user_row': user_row,
                            'money_col': money_col,
                            'new_money': new_money,
                            'result_index': result_index,
                            'user_id': user_id_str
                        }

                        batch_updates.append(update_data)
                        total_amount_scheduled += toot_result.money_reward

                    except Exception as e:
                        logger.error(f"사용자 {toot_result.user_id} 데이터 준비 중 예외: {e}")

                        reward_results.append(RewardResult(
                            user_id=toot_result.user_id,
                            username=toot_result.username,
                            money_given=0,
                            old_money_balance=0,
                            new_money_balance=0,
                            success=False,
                            error=str(e)
                        ))
                        reward_sources.append(toot_result)

                # 2단계: 상점봇 시트 배치 업데이트 실행 (소지금 업데이트, 1회 API 호출!)
                if batch_updates:
                    logger.info(f"💰 상점봇 시트 배치 업데이트 실행: {len(batch_updates)}명 (소지금)")
                    update_success, failed_result_indices = self.sheets_manager.batch_update_shop_rewards(batch_updates)

                    if not update_success:
                        logger.error("배치 업데이트 중 실패한 항목이 있습니다.")

                    if failed_result_indices:
                        failed_set = set(failed_result_indices)
                        for idx in failed_set:
                            if 0 <= idx < len(reward_results):
                                reward_results[idx].success = False
                                reward_results[idx].error = "상점봇 시트 업데이트 검증 실패"

                    # 3단계: 툿수 시트의 누적 재화 업데이트 준비 (성공한 사용자만)
                    for result, toot_result in zip(reward_results, reward_sources):
                        if not result.success:
                            continue

                        toot_user_data = self.sheets_manager.find_user_in_toot_sheet(toot_result.user_id)
                        if not toot_user_data:
                            logger.warning(f"툿수 시트에서 사용자 찾기 실패: {toot_result.user_id}")
                            continue

                        toot_user_info, toot_user_row = toot_user_data
                        current_rewards = _parse_int(toot_user_info.get(config.TOOT_SHEET_HEADERS['TOTAL_REWARDS'], 0))
                        current_toots = _parse_int(toot_user_info.get(config.TOOT_SHEET_HEADERS['RECENT_TOOTS'], 0))

                        new_total_rewards = current_rewards + toot_result.money_reward

                        update_payload = {
                            'user_row': toot_user_row,
                            'new_toots': toot_result.new_count,
                        }

                        if toot_result.money_reward > 0:
                            update_payload['new_total_rewards'] = new_total_rewards

                        if (
                            update_payload.get('new_total_rewards') is not None
                            or current_toots != toot_result.new_count
                        ):
                            toot_updates.append(update_payload)

                    # 4단계: 툿수 시트 배치 업데이트 실행 (툿수 현황 및 누적 재화 업데이트, 1회 API 호출!)
                    if toot_updates:
                        logger.info(f"📊 툿수 시트 배치 업데이트 실행: {len(toot_updates)}명 (툿수 현황/누적 재화)")
                        self.sheets_manager.batch_update_toot_sheet(toot_updates)

                    # 로깅 (성공한 사용자만)
                    for result in reward_results:
                        if result.success:
                            log_reward_transaction(result.user_id, result.username, result.money_given, result.new_money_balance, "툿수 달성")
                            logger.info(f"재화 지급: {result.username} +{result.money_given}소지금")

                    time.sleep(1.0)  # 배치 업데이트 후 1초 대기 (기존 2.5초에서 단축)

                successful_results = [r for r in reward_results if r.success]
                failed_results = [r for r in reward_results if not r.success]

                total_money_given = sum(r.money_given for r in successful_results)

                logger.info(
                    "재화 지급 완료: 성공 %d명, 실패 %d명, 소지금 %d (총 지급 예정: %d)",
                    len(successful_results),
                    len(failed_results),
                    total_money_given,
                    total_amount_scheduled,
                )
                
                return reward_results
                
        except Exception as e:
            logger.error(f"배치 재화 지급 실패: {e}")
            return []
    
    def _give_reward_optimized(self, user_id: str, money_amount: int, reason: str, shop_user_map: Dict[str, Tuple[Dict, int]], money_col: int) -> RewardResult:
        """
        캐시된 데이터를 사용한 최적화된 재화 지급 (API 호출 최소화)

        Args:
            user_id: 사용자 ID
            money_amount: 지급할 소지금량
            reason: 지급 사유
            shop_user_map: 캐시된 상점봇 시트 데이터 맵
            money_col: 소지금 컬럼 인덱스

        Returns:
            RewardResult: 지급 결과
        """
        try:
            user_id_str = str(user_id).strip()

            # 캐시된 데이터에서 사용자 정보 조회 (API 호출 없음!)
            if user_id_str not in shop_user_map:
                return RewardResult(
                    user_id=user_id,
                    username="Unknown",
                    money_given=0,
                    old_money_balance=0,
                    new_money_balance=0,
                    success=False,
                    error="상점봇 시트 캐시에서 사용자를 찾을 수 없습니다."
                )

            user_info, user_row = shop_user_map[user_id_str]
            username = user_info.get('이름', 'Unknown')

            # 현재 소지금 조회 (캐시된 데이터에서 직접 읽기, API 호출 없음!)
            money_key = None
            for key in user_info.keys():
                if '소지금' in key:
                    money_key = key
                    break

            if not money_key:
                return RewardResult(
                    user_id=user_id,
                    username=username,
                    money_given=0,
                    old_money_balance=0,
                    new_money_balance=0,
                    success=False,
                    error="소지금 컬럼을 찾을 수 없습니다."
                )

            try:
                old_money = int(str(user_info.get(money_key, 0)).replace(',', ''))
            except (ValueError, TypeError):
                old_money = 0

            new_money = old_money + money_amount

            # 소지금 업데이트
            money_update_success = self.sheets_manager.update_user_money_optimized(user_id, money_amount, user_row, old_money, money_col)

            if money_update_success:
                # 상점 시트 재화 지급이 성공하면 툿수 시트의 누적 재화도 업데이트
                toot_user_data = self.sheets_manager.find_user_in_toot_sheet(user_id)
                if toot_user_data:
                    toot_user_info, toot_user_row = toot_user_data
                    # 누적 재화는 소지금만 카운트 (기존 로직 유지)
                    self.sheets_manager.update_user_total_rewards(toot_user_row, money_amount)
                    logger.debug(f"툿수 시트 누적 재화 업데이트 완료: {username}")

                # 로깅 및 기록
                log_reward_transaction(user_id, username, money_amount, new_money, reason)

                logger.info(f"재화 지급 성공: {username} ({user_id}) +{money_amount}소지금 → {new_money}소지금")

                return RewardResult(
                    user_id=user_id,
                    username=username,
                    money_given=money_amount,
                    old_money_balance=old_money,
                    new_money_balance=new_money,
                    success=True
                )
            else:
                return RewardResult(
                    user_id=user_id,
                    username=username,
                    money_given=0,
                    old_money_balance=old_money,
                    new_money_balance=old_money,
                    success=False,
                    error="상점봇 시트 업데이트 실패"
                )

        except Exception as e:
            error_msg = f"최적화된 재화 지급 중 오류: {str(e)}"
            logger.error(error_msg)

            return RewardResult(
                user_id=user_id,
                username="Unknown",
                money_given=0,
                old_money_balance=0,
                new_money_balance=0,
                success=False,
                error=error_msg
            )
    
    def create_reward_summary(self, reward_results: List[RewardResult]) -> RewardSummary:
        """
        재화 지급 결과 요약 생성
        
        Args:
            reward_results: 재화 지급 결과 리스트
            
        Returns:
            RewardSummary: 지급 요약
        """
        if not reward_results:
            return RewardSummary(
                total_users=0,
                successful_rewards=0,
                failed_rewards=0,
                total_amount_given=0,
                success_rate=0.0,
                errors=[],
                timestamp=datetime.now(pytz.timezone('Asia/Seoul')).isoformat()
            )
        
        successful_results = [r for r in reward_results if r.success]
        failed_results = [r for r in reward_results if not r.success]
        
        total_amount = sum(r.amount_given for r in successful_results)
        success_rate = (len(successful_results) / len(reward_results)) * 100
        
        errors = [r.error for r in failed_results if r.error]
        
        return RewardSummary(
            total_users=len(reward_results),
            successful_rewards=len(successful_results),
            failed_rewards=len(failed_results),
            total_amount_given=total_amount,
            success_rate=success_rate,
            errors=errors,
            timestamp=datetime.now(pytz.timezone('Asia/Seoul')).isoformat()
        )
    
    def validate_reward_eligibility(self, user_id: str) -> Tuple[bool, str]:
        """
        재화 지급 자격 검증
        
        Args:
            user_id: 사용자 ID
            
        Returns:
            Tuple[bool, str]: (자격 여부, 메시지)
        """
        try:
            # 1. 툿수 관리 시트에 등록되어 있는지 확인
            toot_user = self.sheets_manager.find_user_in_toot_sheet(user_id)
            if not toot_user:
                return False, "툿수 관리 시트에 등록되지 않은 사용자입니다."
            
            # 2. 상점봇 시트에 등록되어 있는지 확인
            shop_user = self.sheets_manager.find_user_in_shop_sheet(user_id)
            if not shop_user:
                return False, "상점봇 시트에 등록되지 않은 사용자입니다."
            
            # 3. 소지금 컬럼이 존재하는지 확인
            money_col = self.sheets_manager.get_money_column_index()
            if not money_col:
                return False, "상점봇 시트에서 소지금 컬럼을 찾을 수 없습니다."
            
            return True, "재화 지급 자격이 확인되었습니다."
            
        except Exception as e:
            return False, f"자격 검증 중 오류 발생: {str(e)}"
    
    def get_user_reward_history(self, user_id: str) -> Dict[str, any]:
        """
        사용자의 재화 지급 이력 조회
        
        Args:
            user_id: 사용자 ID
            
        Returns:
            Dict: 재화 지급 이력
        """
        try:
            # 툿수 관리 시트에서 누적 재화 조회
            user_data = self.sheets_manager.find_user_in_toot_sheet(user_id)
            if not user_data:
                return {'error': '사용자를 찾을 수 없습니다.'}
            
            user_info, _ = user_data
            username = user_info.get(config.TOOT_SHEET_HEADERS['NAME'], 'Unknown')
            total_rewards = _parse_int(user_info.get(config.TOOT_SHEET_HEADERS['TOTAL_REWARDS'], 0))
            recent_toots = _parse_int(user_info.get(config.TOOT_SHEET_HEADERS['RECENT_TOOTS'], 0))
            last_update = user_info.get(config.TOOT_SHEET_HEADERS['LAST_UPDATE'], '')
            
            # 상점봇 시트에서 현재 소지금 조회
            current_money = self.sheets_manager.get_current_money(user_id)
            
            return {
                'user_id': user_id,
                'username': username,
                'total_rewards_given': total_rewards,
                'current_money': current_money,
                'recent_toot_count': recent_toots,
                'last_update': last_update,
                'next_reward_at': ((recent_toots // config.TOOTS_PER_REWARD + 1) * config.TOOTS_PER_REWARD),
                'toots_until_next_reward': ((recent_toots // config.TOOTS_PER_REWARD + 1) * config.TOOTS_PER_REWARD) - recent_toots
            }
            
        except Exception as e:
            logger.error(f"사용자 재화 이력 조회 실패: {e}")
            return {'error': str(e)}
    
    def get_system_reward_statistics(self) -> Dict[str, any]:
        """
        시스템 전체 재화 통계 조회
        
        Returns:
            Dict: 시스템 재화 통계
        """
        try:
            # 툿수 관리 시트에서 모든 사용자 조회
            all_users = self.sheets_manager.get_toot_users()
            
            total_users = len(all_users)
            total_rewards_given = 0
            users_with_rewards = 0
            total_toots = 0
            
            for user in all_users:
                rewards = _parse_int(user.get(config.TOOT_SHEET_HEADERS['TOTAL_REWARDS'], 0))
                toots = _parse_int(user.get(config.TOOT_SHEET_HEADERS['RECENT_TOOTS'], 0))
                
                total_rewards_given += rewards
                total_toots += toots
                
                if rewards > 0:
                    users_with_rewards += 1
            
            # 상점봇 시트에서 총 소지금 조회
            shop_users = self.sheets_manager.get_shop_users()
            money_col = self.sheets_manager.get_money_column_index()
            
            total_money_in_circulation = 0
            if money_col:
                worksheet = self.sheets_manager.get_worksheet(self.sheets_manager.shop_sheet, config.SHOP_WORKSHEET_NAME)
                headers = worksheet.row_values(1)
                money_column_name = headers[money_col - 1] if 0 < money_col <= len(headers) else None

                if money_column_name:
                    for user in shop_users:
                        money_value = _parse_int(user.get(money_column_name, 0))
                        total_money_in_circulation += money_value
            
            return {
                'total_users': total_users,
                'users_with_rewards': users_with_rewards,
                'total_rewards_given': total_rewards_given,
                'total_toots_counted': total_toots,
                'total_money_in_circulation': total_money_in_circulation,
                'average_rewards_per_user': total_rewards_given / total_users if total_users > 0 else 0,
                'reward_rate': (users_with_rewards / total_users) * 100 if total_users > 0 else 0,
                'timestamp': datetime.now(pytz.timezone('Asia/Seoul')).isoformat()
            }
            
        except Exception as e:
            logger.error(f"시스템 재화 통계 조회 실패: {e}")
            return {'error': str(e)}
    
    def check_reward_system_health(self) -> Dict[str, any]:
        """
        재화 시스템 상태 확인
        
        Returns:
            Dict: 시스템 상태 정보
        """
        health_status = {
            'healthy': True,
            'issues': [],
            'warnings': [],
            'timestamp': datetime.now(pytz.timezone('Asia/Seoul')).isoformat()
        }
        
        try:
            # 1. 시트 연결 확인
            try:
                toot_users = self.sheets_manager.get_toot_users()
                shop_users = self.sheets_manager.get_shop_users()
            except Exception as e:
                health_status['healthy'] = False
                health_status['issues'].append(f"시트 연결 실패: {str(e)}")
                return health_status
            
            # 2. 소지금 컬럼 확인
            money_col = self.sheets_manager.get_money_column_index()
            if not money_col:
                health_status['healthy'] = False
                health_status['issues'].append("상점봇 시트에서 소지금 컬럼을 찾을 수 없습니다.")
            
            # 3. 사용자 일치성 확인
            toot_user_ids = set(str(user.get(config.TOOT_SHEET_HEADERS['ID'], '')).strip() for user in toot_users)
            shop_user_ids = set(str(user.get('아이디', '')).strip() for user in shop_users)
            
            # 툿수 관리 시트에는 있지만 상점봇 시트에는 없는 사용자
            missing_in_shop = toot_user_ids - shop_user_ids
            if missing_in_shop:
                health_status['warnings'].append(f"상점봇 시트에 없는 사용자: {len(missing_in_shop)}명")
            
            # 상점봇 시트에는 있지만 툿수 관리 시트에는 없는 사용자
            missing_in_toot = shop_user_ids - toot_user_ids
            if missing_in_toot:
                health_status['warnings'].append(f"툿수 관리 시트에 없는 사용자: {len(missing_in_toot)}명")
            
            # 4. 데이터 유효성 확인
            invalid_rewards = 0
            for user in toot_users:
                rewards = user.get(config.TOOT_SHEET_HEADERS['TOTAL_REWARDS'], 0)
                try:
                    _parse_int(rewards)
                except Exception:
                    invalid_rewards += 1
            
            if invalid_rewards > 0:
                health_status['warnings'].append(f"잘못된 재화 데이터: {invalid_rewards}명")
            
            health_status['checks_performed'] = {
                'sheet_connection': True,
                'money_column': money_col is not None,
                'user_consistency': len(missing_in_shop) == 0 and len(missing_in_toot) == 0,
                'data_validity': invalid_rewards == 0
            }
            
            logger.info(f"재화 시스템 상태 확인 완료: {'정상' if health_status['healthy'] else '문제 발견'}")
            
        except Exception as e:
            health_status['healthy'] = False
            health_status['issues'].append(f"상태 확인 중 오류: {str(e)}")
        
        return health_status


# 편의 함수들
def give_reward(user_id: str, money_amount: int, reason: str = "툿수 달성") -> RewardResult:
    """단일 사용자 재화 지급 (편의 함수)"""
    reward_system = RewardSystem()
    return reward_system.give_reward_to_user(user_id, money_amount, reason)


def process_toot_rewards(toot_results: List[TootCountResult]) -> List[RewardResult]:
    """툿수 결과 기반 재화 지급 (편의 함수)"""
    reward_system = RewardSystem()
    return reward_system.give_rewards_batch(toot_results)


def get_user_rewards(user_id: str) -> Dict[str, any]:
    """사용자 재화 정보 조회 (편의 함수)"""
    reward_system = RewardSystem()
    return reward_system.get_user_reward_history(user_id)


def get_reward_statistics() -> Dict[str, any]:
    """시스템 재화 통계 (편의 함수)"""
    reward_system = RewardSystem()
    return reward_system.get_system_reward_statistics()


# 전역 인스턴스 (싱글톤 패턴)
_global_reward_system: Optional[RewardSystem] = None


def get_reward_system() -> RewardSystem:
    """전역 RewardSystem 인스턴스 반환"""
    global _global_reward_system
    if _global_reward_system is None:
        _global_reward_system = RewardSystem()
    return _global_reward_system


# 재화 지급 프로세스 실행 함수
def execute_reward_process(toot_results: List[TootCountResult]) -> Dict[str, any]:
    """
    재화 지급 프로세스 실행
    
    Args:
        toot_results: 툿수 계산 결과 리스트
        
    Returns:
        Dict: 실행 결과
    """
    try:
        logger.info("🎁 재화 지급 프로세스 시작")
        start_time = time.time()
        
        reward_system = get_reward_system()
        
        # 1. 시스템 상태 확인
        health_check = reward_system.check_reward_system_health()
        if not health_check['healthy']:
            logger.error("재화 시스템 상태 확인 실패:")
            for issue in health_check['issues']:
                logger.error(f"  - {issue}")
            return {'success': False, 'error': '시스템 상태 불량', 'health_check': health_check}
        
        if health_check['warnings']:
            logger.warning("재화 시스템 경고:")
            for warning in health_check['warnings']:
                logger.warning(f"  - {warning}")
        
        # 2. 재화 지급 실행
        reward_results = reward_system.give_rewards_batch(toot_results)
        
        # 3. 결과 요약 생성
        summary = reward_system.create_reward_summary(reward_results)
        
        # 실행 시간 계산
        duration = time.time() - start_time
        
        # 4. 로깅
        logger.info(f"✅ 재화 지급 프로세스 완료 (소요시간: {duration:.2f}초)")
        logger.info(f"   - 대상자: {summary.total_users}명")
        logger.info(f"   - 성공: {summary.successful_rewards}명 ({summary.success_rate:.1f}%)")
        logger.info(f"   - 실패: {summary.failed_rewards}명")
        logger.info(f"   - 총 지급: {summary.total_amount_given}재화")
        
        if summary.errors:
            logger.warning("재화 지급 오류:")
            for error in summary.errors:
                logger.warning(f"  - {error}")
        
        return {
            'success': True,
            'duration': duration,
            'summary': summary,
            'reward_results': reward_results,
            'health_check': health_check
        }
        
    except Exception as e:
        logger.error(f"❌ 재화 지급 프로세스 실패: {e}")
        return {
            'success': False,
            'error': str(e)
        }


# 테스트 함수
def test_reward_system():
    """재화 지급 시스템 테스트"""
    try:
        logger.info("재화 지급 시스템 테스트 시작...")
        
        reward_system = get_reward_system()
        
        # 시스템 상태 확인
        health = reward_system.check_reward_system_health()
        logger.info(f"시스템 상태: {'정상' if health['healthy'] else '문제 발견'}")
        
        if health['issues']:
            for issue in health['issues']:
                logger.warning(f"  문제: {issue}")
        
        if health['warnings']:
            for warning in health['warnings']:
                logger.warning(f"  경고: {warning}")
        
        # 시스템 통계 조회
        stats = reward_system.get_system_reward_statistics()
        logger.info(f"시스템 통계: {stats}")
        
        # 사용자 목록에서 첫 번째 사용자로 테스트
        users = reward_system.sheets_manager.get_toot_users()
        if users:
            test_user_id = users[0].get(config.TOOT_SHEET_HEADERS['ID'])
            if test_user_id:
                # 자격 검증 테스트
                eligible, message = reward_system.validate_reward_eligibility(test_user_id)
                logger.info(f"자격 검증 ({test_user_id}): {eligible} - {message}")
                
                # 재화 이력 조회 테스트
                history = reward_system.get_user_reward_history(test_user_id)
                logger.info(f"재화 이력: {history}")
        
        logger.info("재화 지급 시스템 테스트 완료")
        return True
        
    except Exception as e:
        logger.error(f"재화 지급 시스템 테스트 실패: {e}")
        return False


if __name__ == "__main__":
    # 재화 지급 시스템 테스트 실행
    test_reward_system()
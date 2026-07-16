"""
툿수 계산 모듈
마스토돈 사용자들의 툿수를 계산하고 증가량을 추적합니다.
"""

import os
import sys
import time
import logging
from typing import Any, Dict, List, Optional, Tuple, NamedTuple
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
        safe_execute, TootCountError, UserNotFoundError,
        ErrorContext, log_error_context
    )
    from utils.logging_config import logger, toot_logger, log_toot_monitoring_data
    from utils.sheets_operations import get_sheets_manager
    from core.mastodon_api import get_mastodon_manager, get_cached_toot_count
except ImportError as e:
    # 임포트 실패 시 기본 로거
    import logging
    logger = logging.getLogger('toot_counter')
    print(f"임포트 실패: {e}")


@dataclass
class TootCountResult:
    """툿수 계산 결과"""
    user_id: str
    username: str
    old_count: int
    new_count: int
    increase: int
    reward_eligible: bool
    money_reward: int  # 지급할 소지금
    gacha_reward: int  # 지급할 가챠권
    error: Optional[str] = None

    # 하위 호환성을 위한 속성
    @property
    def reward_amount(self) -> int:
        """전체 보상 (하위 호환성: 소지금만)"""
        return self.money_reward

    @property
    def success(self) -> bool:
        """계산 성공 여부"""
        return self.error is None

    @property
    def has_increase(self) -> bool:
        """툿수 증가 여부"""
        return self.increase > 0

    @property
    def total_rewards(self) -> int:
        """총 보상 수 (소지금 + 가챠권)"""
        return self.money_reward + self.gacha_reward


def _parse_int(value: Any) -> int:
    """문자열/숫자 값을 안전하게 정수로 변환"""
    try:
        if value is None:
            return 0
        return int(str(value).replace(',', '').strip())
    except (ValueError, TypeError):
        return 0


class TootCounter:
    """툿수 계산 및 추적 클래스"""
    
    def __init__(self):
        """TootCounter 초기화"""
        self.sheets_manager = get_sheets_manager()
        self.mastodon_manager = get_mastodon_manager()
        self.toots_per_reward = config.TOOTS_PER_REWARD
        self.reward_amount = config.REWARD_AMOUNT
        
    def calculate_single_user_toots(self, user_id: str, force_api_call: bool = False) -> TootCountResult:
        """
        단일 사용자의 툿수 계산
        
        Args:
            user_id: 사용자 ID
            force_api_call: 강제 API 호출 여부 (캐시 무시)
            
        Returns:
            TootCountResult: 계산 결과
        """
        try:
            with ErrorContext("단일 사용자 툿수 계산", user_id=user_id):
                # 1. 툿수 관리 시트에서 사용자 정보 조회
                user_data = self.sheets_manager.find_user_in_toot_sheet(user_id)
                if not user_data:
                    # 사용자 등록 상태 확인
                    registration_status = self.sheets_manager.check_user_registration(user_id)
                    
                    # 더 자세한 오류 메시지 제공
                    if registration_status.get('registered_in_shop', False):
                        error_msg = f"사용자 '{user_id}'는 상점봇 시트에는 등록되어 있지만 툿수 관리 시트에는 등록되지 않았습니다. 툿수 관리 시트에 추가해주세요."
                    else:
                        error_msg = f"사용자 '{user_id}'를 툿수 관리 시트에서 찾을 수 없습니다. 사용자가 시트에 등록되어 있는지 확인해주세요."
                    
                    logger.warning(f"사용자 {user_id} 계산 실패: {error_msg}")
                    logger.warning(f"등록 상태: 툿수시트={registration_status.get('registered_in_toot', False)}, 상점시트={registration_status.get('registered_in_shop', False)}")

                    return TootCountResult(
                        user_id=user_id,
                        username="Unknown",
                        old_count=0,
                        new_count=0,
                        increase=0,
                        reward_eligible=False,
                        money_reward=0,
                        gacha_reward=0,
                        error=error_msg
                    )
                
                user_info, user_row = user_data
                username = user_info.get(config.TOOT_SHEET_HEADERS['NAME'], 'Unknown')
                old_count = _parse_int(user_info.get(config.TOOT_SHEET_HEADERS['RECENT_TOOTS'], 0))
                
                # 2. 마스토돈 API에서 현재 툿수 조회
                new_count = get_cached_toot_count(user_id, force_refresh=force_api_call)
                if new_count is None:
                    return TootCountResult(
                        user_id=user_id,
                        username=username,
                        old_count=old_count,
                        new_count=old_count,  # 실패 시 이전 값 유지
                        increase=0,
                        reward_eligible=False,
                        money_reward=0,
                        gacha_reward=0,
                        error="마스토돈 API에서 툿수를 조회할 수 없습니다."
                    )
                
                # 3. 증가량 계산
                increase = max(0, new_count - old_count)  # 음수 방지

                # 4. 재화 지급 대상 여부 계산 (현재 툿수 기준 절대값 계산)
                # 10툿마다 소지금 1
                current_total_rewards = _parse_int(user_info.get(config.TOOT_SHEET_HEADERS['TOTAL_REWARDS'], 0))

                # 소지금 계산 (10툿마다 1)
                expected_money_rewards = (new_count // config.TOOTS_PER_MONEY_REWARD) * config.MONEY_REWARD_AMOUNT
                missing_money_rewards = max(0, expected_money_rewards - current_total_rewards)

                # 증가량과 관계없이 부족한 재화가 있으면 지급 (절대값 기준)
                reward_eligible = missing_money_rewards > 0
                money_reward = missing_money_rewards
                gacha_reward = 0  # 가챠권 시스템 제거됨

                # 디버그 로깅: 재화 지급 계산 과정
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug(f"재화 계산 - {username}: 툿수={new_count}, 누적소지금={current_total_rewards}, 예상소지금={expected_money_rewards}, 부족소지금={missing_money_rewards}")
                elif reward_eligible:
                    logger.info(f"재화 지급 대상 - {username}: 툿수={new_count}, 소지금={money_reward}")
                
                result = TootCountResult(
                    user_id=user_id,
                    username=username,
                    old_count=old_count,
                    new_count=new_count,
                    increase=increase,
                    reward_eligible=reward_eligible,
                    money_reward=money_reward,
                    gacha_reward=gacha_reward
                )

                # 5. 로깅
                if toot_logger:
                    if money_reward > 0 or gacha_reward > 0:
                        toot_logger.log_toot_check(user_id, username, old_count, new_count, money_reward)
                    else:
                        # 재화 지급이 없는 경우는 debug 레벨로만 로깅
                        logger.debug(f"툿수 변동 (재화 지급 없음) | @{username} | {old_count} → {new_count} (+{increase})")

                # JSON 로그 기록
                log_toot_monitoring_data(user_id, username, old_count, new_count, money_reward)
                
                return result
                
        except Exception as e:
            error_msg = f"툿수 계산 중 오류 발생: {str(e)}"
            logger.error(error_msg)

            return TootCountResult(
                user_id=user_id,
                username="Unknown",
                old_count=0,
                new_count=0,
                increase=0,
                reward_eligible=False,
                money_reward=0,
                gacha_reward=0,
                error=error_msg
            )
    
    def calculate_multiple_users_toots(self, user_ids: List[str] = None, 
                                     force_api_call: bool = False) -> List[TootCountResult]:
        """
        여러 사용자의 툿수 계산 (배치 처리)
        
        Args:
            user_ids: 사용자 ID 리스트 (None인 경우 모든 사용자)
            force_api_call: 강제 API 호출 여부
            
        Returns:
            List[TootCountResult]: 계산 결과 리스트
        """
        try:
            with ErrorContext("다중 사용자 툿수 계산"):
                # 사용자 목록 결정
                if user_ids is None:
                    # 툿수 관리 시트에서 모든 사용자 조회
                    all_users = self.sheets_manager.get_toot_users()
                    user_ids = [str(user.get(config.TOOT_SHEET_HEADERS['ID'], '')).strip()
                              for user in all_users if user.get(config.TOOT_SHEET_HEADERS['ID'])]
                
                if not user_ids:
                    logger.warning("계산할 사용자가 없습니다.")
                    return []
                
                logger.info(f"툿수 계산 시작: {len(user_ids)}명 (배치 처리 모드)")
                
                results = []
                successful_count = 0
                error_count = 0
                
                # 배치 크기 설정 (설정에서 가져옴)
                batch_size = config.BATCH_SIZE
                total_batches = (len(user_ids) + batch_size - 1) // batch_size
                
                for batch_num in range(total_batches):
                    start_idx = batch_num * batch_size
                    end_idx = min(start_idx + batch_size, len(user_ids))
                    batch_user_ids = user_ids[start_idx:end_idx]
                    
                    logger.info(f"배치 {batch_num + 1}/{total_batches} 처리 중: {len(batch_user_ids)}명")
                    
                    # 현재 배치의 사용자들 처리
                    for user_id in batch_user_ids:
                        try:
                            result = self.calculate_single_user_toots(user_id, force_api_call)
                            results.append(result)
                            
                            if result.success:
                                successful_count += 1
                            else:
                                error_count += 1
                                logger.warning(f"사용자 {user_id} 계산 실패: {result.error}")
                            
                            # API 제한을 고려한 딜레이 (설정값 사용)
                            if not force_api_call:  # 캐시 사용 시에는 더 짧은 딜레이
                                time.sleep(config.API_CALL_DELAY_SECONDS * 0.5)  # 캐시 사용 시 절반 딜레이
                            else:
                                time.sleep(config.API_CALL_DELAY_SECONDS)
                                
                        except Exception as e:
                            error_count += 1
                            logger.error(f"사용자 {user_id} 계산 중 예외 발생: {e}")

                            results.append(TootCountResult(
                                user_id=user_id,
                                username="Unknown",
                                old_count=0,
                                new_count=0,
                                increase=0,
                                reward_eligible=False,
                                money_reward=0,
                                gacha_reward=0,
                                error=str(e)
                            ))
                    
                    # 배치 간 휴식 (마지막 배치가 아닌 경우)
                    if batch_num < total_batches - 1:
                        is_before_last = (batch_num == total_batches - 2)
                        wait_minutes = config.FINAL_BATCH_INTERVAL_MINUTES if is_before_last else config.BATCH_INTERVAL_MINUTES
                        logger.info(f"배치 {batch_num + 1} 완료. {wait_minutes}분 휴식 후 다음 배치 진행...")
                        # 인터럽트 가능한 sleep (10초씩 체크)
                        for _ in range(wait_minutes * 6):
                            time.sleep(10)
                
                logger.info(f"툿수 계산 완료: 성공 {successful_count}명, 실패 {error_count}명")
                
                return results
                
        except Exception as e:
            logger.error(f"다중 사용자 툿수 계산 실패: {e}")
            return []
    
    def calculate_and_process_immediate(self, user_ids: List[str] = None, 
                                      force_api_call: bool = False) -> List[TootCountResult]:
        """
        즉시 처리 방식: 3명씩 툿수 확인 → 즉시 재화 지급 → 1분 휴식 → 반복
        
        Args:
            user_ids: 사용자 ID 리스트 (None인 경우 모든 사용자)
            force_api_call: 강제 API 호출 여부
            
        Returns:
            List[TootCountResult]: 계산 결과 리스트
        """
        try:
            with ErrorContext("즉시 처리 방식 툿수 계산 및 재화 지급"):
                # 사용자 목록 결정
                if user_ids is None:
                    # 툿수 관리 시트에서 모든 사용자 조회
                    all_users = self.sheets_manager.get_toot_users()
                    user_ids = [str(user.get(config.TOOT_SHEET_HEADERS['ID'], '')).strip()
                              for user in all_users if user.get(config.TOOT_SHEET_HEADERS['ID'])]
                
                if not user_ids:
                    logger.warning("계산할 사용자가 없습니다.")
                    return []
                
                logger.info(f"즉시 처리 방식 시작: {len(user_ids)}명 (배치 크기: {config.BATCH_SIZE}명)")

                all_results = []
                total_batches = (len(user_ids) + config.BATCH_SIZE - 1) // config.BATCH_SIZE

                # 재화 시스템 초기화
                from core.reward_system import get_reward_system
                reward_system = get_reward_system()

                # 툿수 시트 사용자 맵 캐시 (한 번만 읽기)
                all_toot_users = self.sheets_manager.get_toot_users()
                toot_user_map = {}
                for index, user_data in enumerate(all_toot_users):
                    sheet_user_id = str(user_data.get(config.TOOT_SHEET_HEADERS['ID'], '')).strip()
                    if sheet_user_id:
                        row_number = index + 3
                        toot_user_map[sheet_user_id] = (user_data, row_number)
                logger.info(f"툿수 시트 사용자 맵 캐시 생성: {len(toot_user_map)}명 (API 중복 읽기 방지)")

                for batch_num in range(total_batches):
                    start_idx = batch_num * config.BATCH_SIZE
                    end_idx = min(start_idx + config.BATCH_SIZE, len(user_ids))
                    batch_user_ids = user_ids[start_idx:end_idx]

                    logger.info(f"배치 {batch_num + 1}/{total_batches} 처리 중: {len(batch_user_ids)}명")
                    
                    batch_results = []
                    
                    # 1단계: 현재 배치 사용자들의 툿수 계산
                    for user_id in batch_user_ids:
                        try:
                            result = self.calculate_single_user_toots(user_id, force_api_call)
                            batch_results.append(result)
                            all_results.append(result)
                            
                            # API 제한을 고려한 딜레이
                            if not force_api_call:
                                time.sleep(config.API_CALL_DELAY_SECONDS * 0.5)
                            else:
                                time.sleep(config.API_CALL_DELAY_SECONDS)
                                
                        except Exception as e:
                            logger.error(f"사용자 {user_id} 계산 중 예외 발생: {e}")
                            error_result = TootCountResult(
                                user_id=user_id,
                                username="Unknown",
                                old_count=0,
                                new_count=0,
                                increase=0,
                                reward_eligible=False,
                                money_reward=0,
                                gacha_reward=0,
                                error=str(e)
                            )
                            batch_results.append(error_result)
                            all_results.append(error_result)
                    
                    # 2단계: 현재 배치에서 재화 지급 대상 필터링
                    reward_eligible_users = [r for r in batch_results if r.reward_eligible and r.success and r.total_rewards > 0]

                    if reward_eligible_users:
                        logger.info(f"배치 {batch_num + 1} 재화 지급 대상: {len(reward_eligible_users)}명")

                        # 3단계: 재화 즉시 지급 (내부에서 툿수 시트도 함께 배치 업데이트)
                        reward_results = reward_system.give_rewards_batch(reward_eligible_users)

                        # 지급 결과 로깅
                        successful_rewards = len([r for r in reward_results if r.success])
                        failed_rewards = len([r for r in reward_results if not r.success])
                        total_money = sum(r.money_given for r in reward_results if r.success)

                        logger.info(f"배치 {batch_num + 1} 재화 지급 완료: 성공 {successful_rewards}명, 실패 {failed_rewards}명, 소지금 {total_money}")

                        if failed_rewards > 0:
                            logger.warning(f"배치 {batch_num + 1} 재화 지급 실패:")
                            for result in reward_results:
                                if not result.success:
                                    logger.warning(f"  - {result.username}: {result.error}")
                    else:
                        logger.info(f"배치 {batch_num + 1} 재화 지급 대상 없음")
                        # 툿수 변동이 있는 사용자만 시트 업데이트
                        toot_change_results = [
                            r for r in batch_results
                            if r.success and r.new_count != r.old_count
                        ]
                        if toot_change_results:
                            self.update_toot_sheet_data(toot_change_results, cached_user_map=toot_user_map)
                    
                    # 5단계: 다음 배치 전 휴식 (마지막 배치가 아닌 경우)
                    if batch_num < total_batches - 1:
                        wait_minutes = config.BATCH_INTERVAL_MINUTES
                        logger.info(f"배치 {batch_num + 1} 완료. {wait_minutes}분 휴식 후 다음 배치 진행...")
                        # 인터럽트 가능한 sleep (10초씩 체크)
                        for _ in range(wait_minutes * 6):
                            time.sleep(10)
                            # 여기서 종료 체크 가능
                
                logger.info(f"즉시 처리 방식 완료: 총 {len(all_results)}명 처리")
                
                return all_results
                
        except Exception as e:
            logger.error(f"즉시 처리 방식 실행 실패: {e}")
            return []
    
    def get_users_with_rewards(self, calculation_results: List[TootCountResult]) -> List[TootCountResult]:
        """
        재화 지급 대상 사용자 필터링

        Args:
            calculation_results: 툿수 계산 결과 리스트

        Returns:
            List[TootCountResult]: 재화 지급 대상 사용자들
        """
        reward_users = [result for result in calculation_results if result.reward_eligible and result.success]

        if reward_users:
            logger.info(f"재화 지급 대상: {len(reward_users)}명")
            for result in reward_users:
                logger.info(f"  - {result.username} ({result.user_id}): +{result.increase}툿 → {result.money_reward}소지금, {result.gacha_reward}가챠권")

        return reward_users
    
    def get_statistics(self, calculation_results: List[TootCountResult]) -> Dict[str, any]:
        """
        계산 결과 통계 생성
        
        Args:
            calculation_results: 툿수 계산 결과 리스트
            
        Returns:
            Dict: 통계 정보
        """
        if not calculation_results:
            return {}
        
        successful_results = [r for r in calculation_results if r.success]

        total_increase = sum(r.increase for r in successful_results)
        total_money_rewards = sum(r.money_reward for r in successful_results)
        total_gacha_rewards = sum(r.gacha_reward for r in successful_results)
        users_with_increase = len([r for r in successful_results if r.has_increase])
        users_with_rewards = len([r for r in successful_results if r.reward_eligible])
        
        return {
            'total_users_checked': len(calculation_results),
            'successful_checks': len(successful_results),
            'failed_checks': len(calculation_results) - len(successful_results),
            'users_with_toot_increase': users_with_increase,
            'users_with_rewards': users_with_rewards,
            'total_toot_increase': total_increase,
            'total_money_rewards_to_give': total_money_rewards,
            'total_gacha_rewards_to_give': total_gacha_rewards,
            'total_rewards_to_give': total_money_rewards + total_gacha_rewards,  # 하위 호환성
            'average_toot_increase': total_increase / len(successful_results) if successful_results else 0,
            'success_rate': (len(successful_results) / len(calculation_results)) * 100 if calculation_results else 0,
            'timestamp': datetime.now(pytz.timezone('Asia/Seoul')).isoformat()
        }
    
    def update_toot_sheet_data(self, calculation_results: List[TootCountResult], cached_user_map: Optional[Dict[str, Tuple[Dict, int]]] = None) -> bool:
        """
        툿수 관리 시트 데이터 배치 업데이트 (API 제한 최적화)

        Args:
            calculation_results: 계산 결과 리스트
            cached_user_map: 캐시된 사용자 맵 (optional, 제공되면 중복 읽기 방지)

        Returns:
            bool: 업데이트 성공 여부
        """
        try:
            # 성공한 결과만 필터링
            successful_results = [r for r in calculation_results if r.success]

            if not successful_results:
                logger.info("업데이트할 데이터가 없습니다.")
                return True

            logger.info(f"배치 업데이트 시작: {len(successful_results)}명")

            # 캐시된 데이터 사용 또는 새로 읽기
            if cached_user_map:
                logger.info(f"캐시된 사용자 맵 사용: {len(cached_user_map)}명")
                user_row_map = cached_user_map
            else:
                # 한 번만 모든 사용자 데이터를 읽어서 메모리에 캐시
                logger.info("사용자 데이터 캐시 생성 중...")
                all_users_data = self.sheets_manager.get_toot_users()
                user_row_map = {}

                # 사용자 ID와 행 번호 매핑 생성 (한 번만 수행)
                for index, user_data in enumerate(all_users_data):
                    sheet_user_id = str(user_data.get(config.TOOT_SHEET_HEADERS['ID'], '')).strip()
                    if sheet_user_id:
                        row_number = index + 3  # 헤더 2행이므로 +3 (3행부터 데이터 시작)
                        user_row_map[sheet_user_id] = (user_data, row_number)

                logger.info(f"사용자 데이터 캐시 완료: {len(user_row_map)}명")

            # 배치 업데이트용 데이터 준비
            toot_updates = []
            for result in successful_results:
                user_id_str = str(result.user_id).strip()
                if user_id_str in user_row_map:
                    user_data, user_row = user_row_map[user_id_str]
                    if result.new_count != _parse_int(user_data.get(config.TOOT_SHEET_HEADERS['RECENT_TOOTS'], 0)):
                        toot_updates.append({
                            'user_row': user_row,
                            'new_toots': result.new_count
                        })

            # 배치 업데이트 실행 (툿수 현황만 업데이트, 1회 API 호출!)
            if toot_updates:
                logger.info(f"📊 툿수 시트 배치 업데이트 실행: {len(toot_updates)}명 (툿수 현황)")
                success = self.sheets_manager.batch_update_toot_sheet(toot_updates)

                if success:
                    logger.info(f"툿수 시트 배치 업데이트 완료: {len(toot_updates)}명")
                    return True
                else:
                    logger.error("툿수 시트 배치 업데이트 실패")
                    return False
            else:
                logger.warning("업데이트할 툿수 데이터가 없습니다.")
                return True

        except Exception as e:
            logger.error(f"툿수 시트 배치 업데이트 실패: {e}")
            return False
    
    def _update_batch(self, batch_results: List[TootCountResult]) -> Tuple[int, int]:
        """
        배치 단위 업데이트
        
        Args:
            batch_results: 배치 결과 리스트
            
        Returns:
            Tuple[int, int]: (성공 수, 실패 수)
        """
        success_count = 0
        error_count = 0
        
        for result in batch_results:
            try:
                # 사용자 행 찾기
                user_data = self.sheets_manager.find_user_in_toot_sheet(result.user_id)
                if not user_data:
                    logger.warning(f"시트에서 사용자를 찾을 수 없습니다: {result.user_id}")
                    error_count += 1
                    continue
                
                user_info, user_row = user_data
                
                # 툿수 정보 업데이트
                if self.sheets_manager.update_user_toots(user_row, result.new_count, result.reward_amount):
                    success_count += 1
                    logger.debug(f"시트 업데이트 성공: {result.username} ({result.user_id})")
                else:
                    error_count += 1
                    logger.warning(f"시트 업데이트 실패: {result.username} ({result.user_id})")
                    
            except Exception as e:
                error_count += 1
                logger.error(f"사용자 {result.user_id} 업데이트 중 오류: {e}")
        
        return success_count, error_count
    
    def _update_batch_optimized(self, batch_results: List[TootCountResult], user_row_map: Dict[str, Tuple[Dict, int]]) -> Tuple[int, int]:
        """
        최적화된 배치 단위 업데이트 (캐시된 데이터 사용)
        
        Args:
            batch_results: 배치 결과 리스트
            user_row_map: 사용자 ID -> (데이터, 행번호) 매핑
            
        Returns:
            Tuple[int, int]: (성공 수, 실패 수)
        """
        success_count = 0
        error_count = 0
        
        for result in batch_results:
            try:
                user_id_str = str(result.user_id).strip()
                
                # 캐시된 데이터에서 사용자 정보 조회
                if user_id_str not in user_row_map:
                    logger.warning(f"캐시에서 사용자를 찾을 수 없습니다: {user_id_str}")
                    error_count += 1
                    continue
                
                user_info, user_row = user_row_map[user_id_str]
                
                # 툿수 정보 업데이트
                if self.sheets_manager.update_user_toots(user_row, result.new_count, result.reward_amount):
                    success_count += 1
                    logger.debug(f"시트 업데이트 성공: {result.username} ({result.user_id})")
                else:
                    error_count += 1
                    logger.warning(f"시트 업데이트 실패: {result.username} ({result.user_id})")
                    
            except Exception as e:
                error_count += 1
                logger.error(f"사용자 {result.user_id} 업데이트 중 오류: {e}")
        
        return success_count, error_count
    
    def validate_toot_counts(self, calculation_results: List[TootCountResult]) -> List[str]:
        """
        툿수 계산 결과 검증
        
        Args:
            calculation_results: 계산 결과 리스트
            
        Returns:
            List[str]: 검증 경고 메시지 리스트
        """
        warnings = []
        
        for result in calculation_results:
            if not result.success:
                continue
            
            # 툿수 감소 검증
            if result.increase < 0:
                warnings.append(f"{result.username}: 툿수가 감소했습니다 ({result.old_count} → {result.new_count})")
            
            # 비정상적인 증가량 검증
            if result.increase > config.MAX_NORMAL_TOOT_INCREASE:
                warnings.append(
                    f"{result.username}: 비정상적으로 많은 툿수 증가 "
                    f"(+{result.increase}, 임계값: {config.MAX_NORMAL_TOOT_INCREASE})"
                )
            
            # 새로운 툿수가 0인 경우
            if result.new_count == 0 and result.old_count > 0:
                warnings.append(f"{result.username}: 툿수가 0으로 초기화됨 (이전: {result.old_count})")
        
        return warnings


# 편의 함수들
def calculate_all_users_toots(force_refresh: bool = False) -> List[TootCountResult]:
    """모든 사용자의 툿수 계산 (편의 함수)"""
    counter = TootCounter()
    return counter.calculate_multiple_users_toots(force_api_call=force_refresh)


def calculate_user_toot(user_id: str, force_refresh: bool = False) -> TootCountResult:
    """단일 사용자 툿수 계산 (편의 함수)"""
    counter = TootCounter()
    return counter.calculate_single_user_toots(user_id, force_api_call=force_refresh)


def get_reward_eligible_users(user_ids: List[str] = None) -> List[TootCountResult]:
    """재화 지급 대상 사용자 조회 (편의 함수)"""
    counter = TootCounter()
    results = counter.calculate_multiple_users_toots(user_ids)
    return counter.get_users_with_rewards(results)


# 전역 인스턴스 (싱글톤 패턴)
_global_toot_counter: Optional[TootCounter] = None


def get_toot_counter() -> TootCounter:
    """전역 TootCounter 인스턴스 반환"""
    global _global_toot_counter
    if _global_toot_counter is None:
        _global_toot_counter = TootCounter()
    return _global_toot_counter


# 모니터링 사이클 실행 함수
def run_monitoring_cycle(cycle_number: int = 0) -> Dict[str, any]:
    """
    툿수 모니터링 사이클 실행
    
    Args:
        cycle_number: 사이클 번호
        
    Returns:
        Dict: 실행 결과 통계
    """
    try:
        logger.info(f"📊 툿수 모니터링 사이클 #{cycle_number} 시작")
        start_time = time.time()
        
        counter = get_toot_counter()
        
        # 즉시 처리 방식 사용 여부 확인
        if config.IMMEDIATE_REWARD_PROCESSING:
            # 새로운 즉시 처리 방식: 3명씩 확인 → 즉시 재화 지급 → 1분 휴식
            logger.info("🚀 즉시 처리 방식으로 툿수 계산 및 재화 지급 시작...")
            calculation_results = counter.calculate_and_process_immediate()
            
            if not calculation_results:
                logger.warning("계산할 사용자가 없습니다.")
                return {'success': False, 'error': '계산할 사용자가 없습니다.'}
            
            # 결과 검증 및 통계 생성
            warnings = counter.validate_toot_counts(calculation_results)
            if warnings:
                logger.warning("툿수 계산 검증 경고:")
                for warning in warnings:
                    logger.warning(f"  - {warning}")
            
            stats = counter.get_statistics(calculation_results)
            reward_users = counter.get_users_with_rewards(calculation_results)
            sheet_update_success = True  # 즉시 처리 방식에서는 이미 시트 업데이트 완료
            
        else:
            # 기존 방식: 모든 사용자 확인 후 → 일괄 재화 지급
            logger.info("🔄 기존 배치 처리 모드로 툿수 계산 시작...")
            calculation_results = counter.calculate_multiple_users_toots()
            
            if not calculation_results:
                logger.warning("계산할 사용자가 없습니다.")
                return {'success': False, 'error': '계산할 사용자가 없습니다.'}
            
            # 결과 검증
            warnings = counter.validate_toot_counts(calculation_results)
            if warnings:
                logger.warning("툿수 계산 검증 경고:")
                for warning in warnings:
                    logger.warning(f"  - {warning}")
            
            # 통계 생성
            stats = counter.get_statistics(calculation_results)
            
            # 재화 지급 대상 필터링 (툿수 시트 업데이트 전에!)
            reward_users = counter.get_users_with_rewards(calculation_results)
            
            # 배치 업데이트 전 최종 휴식
            final_wait_minutes = config.FINAL_BATCH_INTERVAL_MINUTES
            logger.info(f"📝 배치 업데이트 준비 중... {final_wait_minutes}분 휴식 후 시트 업데이트 진행")
            # 인터럽트 가능한 sleep (10초씩 체크)
            for _ in range(final_wait_minutes * 6):
                time.sleep(10)
            
            # 툿수 시트 업데이트 (재화 지급 대상 결정 후)
            logger.info("📊 툿수 시트 배치 업데이트 시작...")
            sheet_update_success = counter.update_toot_sheet_data(calculation_results)
        
        # 실행 시간 계산
        duration = time.time() - start_time
        
        # 7. 로깅
        if toot_logger:
            toot_logger.log_monitoring_cycle(
                cycle_number, 
                stats['successful_checks'], 
                stats['users_with_rewards'], 
                stats['failed_checks']
            )
        
        logger.info(f"✅ 모니터링 사이클 #{cycle_number} 완료 (소요시간: {duration:.2f}초)")
        logger.info(f"   - 확인: {stats['successful_checks']}/{stats['total_users_checked']}명")
        logger.info(f"   - 툿수 증가: {stats['users_with_toot_increase']}명 (+{stats['total_toot_increase']}툿)")
        logger.info(f"   - 재화 지급 대상: {stats['users_with_rewards']}명 ({stats['total_rewards_to_give']}재화)")
        
        return {
            'success': True,
            'cycle_number': cycle_number,
            'duration': duration,
            'statistics': stats,
            'reward_users': reward_users,
            'sheet_update_success': sheet_update_success,
            'warnings': warnings
        }
        
    except Exception as e:
        logger.error(f"❌ 모니터링 사이클 #{cycle_number} 실패: {e}")
        return {
            'success': False,
            'cycle_number': cycle_number,
            'error': str(e)
        }


# 테스트 함수
def test_toot_counter():
    """툿수 계산 시스템 테스트"""
    try:
        logger.info("툿수 계산 시스템 테스트 시작...")
        
        counter = get_toot_counter()
        
        # 설정 확인
        logger.info(f"설정: {counter.toots_per_reward}툿마다 {counter.reward_amount}재화 지급")
        
        # 시트 연결 테스트
        users = counter.sheets_manager.get_toot_users()
        logger.info(f"툿수 관리 시트 사용자: {len(users)}명")
        
        if users:
            # 첫 번째 사용자로 테스트
            test_user_id = users[0].get(config.TOOT_SHEET_HEADERS['ID'])
            if test_user_id:
                result = counter.calculate_single_user_toots(test_user_id)
                logger.info(f"테스트 계산 결과: {result}")
        
        # 모니터링 사이클 테스트 실행
        test_result = run_monitoring_cycle(cycle_number=999)  # 테스트 번호
        logger.info(f"테스트 사이클 결과: {test_result['success']}")
        
        logger.info("툿수 계산 시스템 테스트 완료")
        return True
        
    except Exception as e:
        logger.error(f"툿수 계산 시스템 테스트 실패: {e}")
        return False


if __name__ == "__main__":
    # 툿수 계산 시스템 테스트 실행
    test_toot_counter()
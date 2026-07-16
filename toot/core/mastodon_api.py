"""
마스토돈 API 모듈
마스토돈 API와의 모든 상호작용을 담당합니다.
"""

import os
import sys
import time
from typing import Dict, List, Optional, Any, Union
from datetime import datetime
import pytz

# VM 환경 대응
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
sys.path.append(project_root)

try:
    import mastodon
    from mastodon import Mastodon, MastodonError, MastodonAPIError, MastodonNetworkError
except ImportError:
    print("⚠️ Mastodon.py 패키지가 설치되지 않았습니다. pip install Mastodon.py를 실행하세요.")
    sys.exit(1)

try:
    from config.settings import config
    from utils.error_handling import (
        safe_execute, MastodonAPIError as CustomMastodonError, 
        MastodonErrorHandler, ErrorContext, retry_on_error
    )
    from utils.logging_config import logger, toot_logger
except ImportError as e:
    # 임포트 실패 시 기본 로거
    import logging
    logger = logging.getLogger('mastodon_api')
    toot_logger = None
    print(f"임포트 실패: {e}")


class MastodonManager:
    """마스토돈 API 관리 클래스"""
    
    def __init__(self, access_token: str = None, api_base_url: str = None):
        """
        MastodonManager 초기화
        
        Args:
            access_token: 마스토돈 액세스 토큰
            api_base_url: 마스토돈 인스턴스 URL
        """
        self.access_token = access_token or config.MASTODON_ACCESS_TOKEN
        self.api_base_url = api_base_url or config.MASTODON_API_BASE_URL
        self._client = None
        self._bot_account_info = None
        
        if not self.access_token or not self.api_base_url:
            raise CustomMastodonError("마스토돈 API 설정이 누락되었습니다.")
    
    @property
    def client(self) -> Mastodon:
        """마스토돈 클라이언트 (지연 로딩)"""
        if self._client is None:
            self._client = self._create_client()
        return self._client
    
    def _create_client(self) -> Mastodon:
        """
        마스토돈 클라이언트 생성
        
        Returns:
            Mastodon: 인증된 마스토돈 클라이언트
            
        Raises:
            CustomMastodonError: 클라이언트 생성 실패 시
        """
        def client_operation():
            try:
                client = Mastodon(
                    access_token=self.access_token,
                    api_base_url=self.api_base_url,
                    version_check_mode='none'  # 버전 체크 비활성화로 성능 향상
                )
                
                # 연결 테스트
                account_info = client.me()
                self._bot_account_info = account_info

                bot_username = account_info.get('username', 'Unknown')
                logger.debug(f"마스토돈 API 연결 성공 (@{bot_username})")

                return client
                
            except MastodonNetworkError as e:
                raise CustomMastodonError(f"마스토돈 서버에 연결할 수 없습니다: {str(e)}", "network_error")
            except MastodonAPIError as e:
                raise CustomMastodonError(f"마스토돈 API 오류: {str(e)}", "api_error", getattr(e, 'response', {}).get('status_code'))
            except Exception as e:
                raise CustomMastodonError(f"마스토돈 클라이언트 생성 실패: {str(e)}")
        
        with ErrorContext("마스토돈 클라이언트 생성"):
            result = safe_execute(client_operation)
            
            if result.success:
                return result.result
            else:
                raise result.error or CustomMastodonError("클라이언트 생성 실패")
    
    def get_user_statuses_count(self, user_id: str) -> Optional[int]:
        """
        사용자의 툿수 조회
        
        Args:
            user_id: 마스토돈 사용자 ID (acct 형식)
            
        Returns:
            Optional[int]: 툿수 또는 None (조회 실패 시)
        """
        def get_count_operation():
            try:
                # 사용자 ID를 문자열로 변환 (정수가 전달될 수 있음)
                user_id_str = str(user_id)
                
                # 사용자 정보 조회
                if '@' in user_id_str:
                    # acct 형식인 경우 (@username@instance.domain)
                    account = self.client.account_lookup(user_id_str.lstrip('@'))
                else:
                    # username만 있는 경우
                    account = self.client.account_lookup(user_id_str)
                
                statuses_count = account.get('statuses_count', 0)
                
                if toot_logger:
                    toot_logger.log_api_call("Mastodon", f"툿수 조회 ({user_id})", True)
                
                return int(statuses_count)
                
            except MastodonAPIError as e:
                status_code = getattr(e, 'response', {}).get('status_code', 0)
                
                if status_code == 404:
                    raise MastodonErrorHandler.handle_user_not_found(user_id)
                elif status_code == 429:
                    raise MastodonErrorHandler.handle_rate_limit()
                else:
                    raise CustomMastodonError(f"툿수 조회 실패: {str(e)}", "api_error", status_code)
                    
            except MastodonNetworkError as e:
                raise MastodonErrorHandler.handle_network_error()
            except Exception as e:
                raise CustomMastodonError(f"툿수 조회 중 예상치 못한 오류: {str(e)}")
        
        with ErrorContext("툿수 조회", user_id=user_id):
            result = safe_execute(get_count_operation, max_retries=3)
            
            if result.success:
                return result.result
            else:
                if toot_logger:
                    toot_logger.log_api_call("Mastodon", f"툿수 조회 ({user_id})", False, error=str(result.error))
                logger.warning(f"툿수 조회 실패 ({user_id}): {result.error}")
                return None
    
    def get_multiple_users_statuses_count(self, user_ids: List[str]) -> Dict[str, Optional[int]]:
        """
        여러 사용자의 툿수를 배치로 조회 (캐시 최적화)
        
        Args:
            user_ids: 마스토돈 사용자 ID 리스트
            
        Returns:
            Dict[str, Optional[int]]: {user_id: 툿수} 딕셔너리
        """
        # 1. 캐시에서 먼저 조회
        cached_results, uncached_users = toot_cache.get_multiple(user_ids)
        
        # 2. 캐시에 없는 사용자들만 API 호출
        if uncached_users:
            logger.info(f"캐시 미스: {len(uncached_users)}명 API 호출 필요")
            
            api_results = {}
            for user_id in uncached_users:
                try:
                    count = self.get_user_statuses_count(user_id)
                    api_results[user_id] = count
                    
                    # API 제한을 고려한 딜레이 (설정값 사용)
                    time.sleep(config.API_CALL_DELAY_SECONDS)
                    
                except Exception as e:
                    logger.warning(f"사용자 {user_id} 툿수 조회 실패: {e}")
                    api_results[user_id] = None
            
            # 3. API 결과를 캐시에 저장
            toot_cache.set_multiple(api_results)
            
            # 4. 캐시 결과와 API 결과 병합
            cached_results.update(api_results)
            
            logger.info(f"API 호출 완료: {len(uncached_users)}명 중 {sum(1 for v in api_results.values() if v is not None)}명 성공")
        else:
            logger.info(f"모든 사용자({len(user_ids)}명) 캐시에서 조회 완료")
        
        return cached_results
    
    def get_user_info(self, user_id: str) -> Optional[Dict[str, Any]]:
        """
        사용자 정보 조회
        
        Args:
            user_id: 마스토돈 사용자 ID
            
        Returns:
            Optional[Dict]: 사용자 정보 또는 None
        """
        def get_info_operation():
            try:
                # 사용자 ID를 문자열로 변환 (정수가 전달될 수 있음)
                user_id_str = str(user_id)
                
                if '@' in user_id_str:
                    account = self.client.account_lookup(user_id_str.lstrip('@'))
                else:
                    account = self.client.account_lookup(user_id_str)
                
                return {
                    'id': account['id'],
                    'username': account['username'],
                    'acct': account['acct'],
                    'display_name': account['display_name'],
                    'statuses_count': account['statuses_count'],
                    'followers_count': account['followers_count'],
                    'following_count': account['following_count'],
                    'created_at': account['created_at'],
                    'url': account['url']
                }
                
            except MastodonAPIError as e:
                status_code = getattr(e, 'response', {}).get('status_code', 0)
                if status_code == 404:
                    return None
                else:
                    raise CustomMastodonError(f"사용자 정보 조회 실패: {str(e)}")
        
        with ErrorContext("사용자 정보 조회", user_id=user_id):
            result = safe_execute(get_info_operation)
            
            if result.success:
                return result.result
            else:
                logger.warning(f"사용자 정보 조회 실패 ({user_id}): {result.error}")
                return None
    
    def send_notification_toot(self, message: str, visibility: str = 'unlisted') -> bool:
        """
        알림 툿 전송
        
        Args:
            message: 전송할 메시지
            visibility: 공개 범위 ('public', 'unlisted', 'private', 'direct')
            
        Returns:
            bool: 전송 성공 여부
        """
        def send_operation():
            try:
                status = self.client.status_post(
                    status=message,
                    visibility=visibility
                )
                
                logger.info(f"알림 툿 전송 성공: {message[:50]}...")
                return True
                
            except MastodonAPIError as e:
                status_code = getattr(e, 'response', {}).get('status_code', 0)
                
                if status_code == 429:
                    raise MastodonErrorHandler.handle_rate_limit()
                else:
                    raise CustomMastodonError(f"툿 전송 실패: {str(e)}")
        
        with ErrorContext("알림 툿 전송"):
            result = safe_execute(send_operation)
            
            if result.success:
                return True
            else:
                logger.error(f"알림 툿 전송 실패: {result.error}")
                return False
    
    def send_admin_notification(self, message: str) -> bool:
        """
        관리자에게 DM 전송
        
        Args:
            message: 전송할 메시지
            
        Returns:
            bool: 전송 성공 여부
        """
        if not config.SYSTEM_ADMIN_ID:
            logger.debug("시스템 관리자 ID가 설정되지 않음")
            return False
        
        admin_message = f"@{config.SYSTEM_ADMIN_ID} 🚨 시스템 알림\n{message}"
        return self.send_notification_toot(admin_message, visibility='direct')
    
    def get_bot_info(self) -> Optional[Dict[str, Any]]:
        """
        봇 계정 정보 반환
        
        Returns:
            Optional[Dict]: 봇 계정 정보
        """
        if self._bot_account_info:
            return self._bot_account_info
        
        try:
            self._bot_account_info = self.client.me()
            return self._bot_account_info
        except Exception as e:
            logger.error(f"봇 계정 정보 조회 실패: {e}")
            return None
    
    def check_api_health(self) -> Dict[str, Any]:
        """
        API 상태 확인
        
        Returns:
            Dict: API 상태 정보
        """
        health_status = {
            'healthy': False,
            'response_time': None,
            'bot_info': None,
            'error': None,
            'timestamp': datetime.now(pytz.timezone('Asia/Seoul')).isoformat()
        }
        
        try:
            start_time = time.time()
            
            # 간단한 API 호출로 상태 확인
            bot_info = self.client.me()
            
            end_time = time.time()
            response_time = end_time - start_time
            
            health_status.update({
                'healthy': True,
                'response_time': response_time,
                'bot_info': {
                    'username': bot_info.get('username'),
                    'display_name': bot_info.get('display_name'),
                    'statuses_count': bot_info.get('statuses_count')
                }
            })
            
            logger.info(f"API 상태 확인 성공 (응답시간: {response_time:.3f}초)")
            
        except Exception as e:
            health_status['error'] = str(e)
            logger.warning(f"API 상태 확인 실패: {e}")
        
        return health_status
    
    def get_rate_limit_info(self) -> Dict[str, Any]:
        """
        API 제한 정보 조회 (가능한 경우)
        
        Returns:
            Dict: API 제한 정보
        """
        # 마스토돈 API는 직접적인 rate limit 정보를 제공하지 않으므로
        # 간접적으로 추정
        return {
            'estimated_requests_per_hour': 300,  # 일반적인 마스토돈 인스턴스 제한
            'recommended_delay_between_requests': 0.1,  # 초
            'current_status': 'unknown'
        }


# 편의 함수들
def create_mastodon_manager() -> MastodonManager:
    """마스토돈 매니저 생성"""
    return MastodonManager()


def get_user_toot_count(user_id: str) -> Optional[int]:
    """사용자 툿수 조회 (편의 함수)"""
    try:
        manager = get_mastodon_manager()
        return manager.get_user_statuses_count(user_id)
    except Exception as e:
        logger.error(f"툿수 조회 실패: {e}")
        return None


def get_multiple_toot_counts(user_ids: List[str]) -> Dict[str, Optional[int]]:
    """여러 사용자 툿수 조회 (편의 함수)"""
    try:
        manager = get_mastodon_manager()
        return manager.get_multiple_users_statuses_count(user_ids)
    except Exception as e:
        logger.error(f"배치 툿수 조회 실패: {e}")
        return {user_id: None for user_id in user_ids}


def send_system_notification(message: str, to_admin: bool = False) -> bool:
    """시스템 알림 전송 (편의 함수)"""
    try:
        manager = get_mastodon_manager()
        if to_admin:
            return manager.send_admin_notification(message)
        else:
            return manager.send_notification_toot(message)
    except Exception as e:
        logger.error(f"시스템 알림 전송 실패: {e}")
        return False


def check_mastodon_connection() -> bool:
    """마스토돈 연결 상태 확인 (편의 함수)"""
    try:
        manager = get_mastodon_manager()
        health = manager.check_api_health()
        return health['healthy']
    except Exception as e:
        logger.error(f"마스토돈 연결 확인 실패: {e}")
        return False


# 전역 인스턴스 (싱글톤 패턴)
_global_mastodon_manager: Optional[MastodonManager] = None


def get_mastodon_manager() -> MastodonManager:
    """전역 MastodonManager 인스턴스 반환"""
    global _global_mastodon_manager
    if _global_mastodon_manager is None:
        _global_mastodon_manager = MastodonManager()
    return _global_mastodon_manager


# API 요청 최적화를 위한 캐시 클래스
class TootCountCache:
    """툿수 캐시 관리 클래스"""
    
    def __init__(self, cache_duration: int = 1800):  # 30분 캐시로 증가
        """
        Args:
            cache_duration: 캐시 유지 시간 (초)
        """
        self.cache = {}
        self.cache_duration = cache_duration
        self.last_cleanup = time.time()
        self.cleanup_interval = 3600  # 1시간마다 정리
    
    def get(self, user_id: str) -> Optional[int]:
        """캐시에서 툿수 조회"""
        # 주기적 캐시 정리
        self._cleanup_if_needed()
        
        if user_id in self.cache:
            cached_data, timestamp = self.cache[user_id]
            if time.time() - timestamp < self.cache_duration:
                return cached_data
            else:
                # 만료된 캐시 삭제
                del self.cache[user_id]
        return None
    
    def set(self, user_id: str, toot_count: int):
        """캐시에 툿수 저장"""
        self.cache[user_id] = (toot_count, time.time())
    
    def get_multiple(self, user_ids: List[str]) -> Dict[str, Optional[int]]:
        """여러 사용자의 캐시된 툿수 조회"""
        results = {}
        uncached_users = []
        
        for user_id in user_ids:
            cached_count = self.get(user_id)
            if cached_count is not None:
                results[user_id] = cached_count
            else:
                uncached_users.append(user_id)
                results[user_id] = None
        
        logger.debug(f"캐시 히트: {len(user_ids) - len(uncached_users)}/{len(user_ids)}명")
        return results, uncached_users
    
    def set_multiple(self, user_data: Dict[str, int]):
        """여러 사용자의 툿수를 일괄 캐시에 저장"""
        for user_id, toot_count in user_data.items():
            if toot_count is not None:
                self.set(user_id, toot_count)
    
    def _cleanup_if_needed(self):
        """필요시 캐시 정리"""
        current_time = time.time()
        if current_time - self.last_cleanup > self.cleanup_interval:
            self.clear_expired()
            self.last_cleanup = current_time
    
    def clear_expired(self):
        """만료된 캐시 정리"""
        current_time = time.time()
        expired_keys = [
            key for key, (_, timestamp) in self.cache.items()
            if current_time - timestamp >= self.cache_duration
        ]
        
        for key in expired_keys:
            del self.cache[key]
        
        if expired_keys:
            logger.debug(f"만료된 툿수 캐시 정리: {len(expired_keys)}개")
    
    def clear_all(self):
        """모든 캐시 삭제"""
        cache_count = len(self.cache)
        self.cache.clear()
        logger.debug(f"툿수 캐시 전체 정리: {cache_count}개")
    
    def get_stats(self) -> Dict[str, int]:
        """캐시 통계 반환"""
        return {
            'total_cached': len(self.cache),
            'cache_duration': self.cache_duration,
            'cleanup_interval': self.cleanup_interval
        }


# 전역 툿수 캐시 인스턴스 (설정값 사용)
toot_cache = TootCountCache(cache_duration=config.CACHE_DURATION_MINUTES * 60)


def get_cached_toot_count(user_id: str, force_refresh: bool = False) -> Optional[int]:
    """
    캐시를 고려한 툿수 조회
    
    Args:
        user_id: 사용자 ID
        force_refresh: 강제 새로고침 여부
        
    Returns:
        Optional[int]: 툿수 또는 None
    """
    if not force_refresh:
        cached_count = toot_cache.get(user_id)
        if cached_count is not None:
            logger.debug(f"캐시에서 툿수 조회: {user_id} = {cached_count}")
            return cached_count
    
    # 캐시에 없거나 강제 새로고침인 경우 API 호출
    fresh_count = get_user_toot_count(user_id)
    if fresh_count is not None:
        toot_cache.set(user_id, fresh_count)
        logger.debug(f"API에서 툿수 조회 후 캐시 저장: {user_id} = {fresh_count}")
    
    return fresh_count


# 테스트 함수
def test_mastodon_api():
    """마스토돈 API 테스트"""
    try:
        logger.info("마스토돈 API 테스트 시작...")
        
        manager = get_mastodon_manager()
        
        # API 상태 확인
        health = manager.check_api_health()
        logger.info(f"API 상태: {health}")
        
        # 봇 정보 확인
        bot_info = manager.get_bot_info()
        if bot_info:
            logger.info(f"봇 계정: @{bot_info['username']} ({bot_info['statuses_count']}툿)")
        
        # 테스트 툿수 조회 (실제 사용자 ID로 변경 필요)
        # test_count = manager.get_user_statuses_count("test_user@mastodon.social")
        # logger.info(f"테스트 툿수: {test_count}")
        
        # 캐시 테스트
        cache_stats = toot_cache.get_stats()
        logger.info(f"캐시 상태: {cache_stats}")
        
        logger.info("마스토돈 API 테스트 완료")
        return True
        
    except Exception as e:
        logger.error(f"마스토돈 API 테스트 실패: {e}")
        return False


if __name__ == "__main__":
    # 마스토돈 API 테스트 실행
    test_mastodon_api()
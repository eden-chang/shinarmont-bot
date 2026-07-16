"""
마스토돈 예약 봇 마스토돈 API 클라이언트
마스토돈 인스턴스와 통신하여 툿을 포스팅하고 상태를 관리합니다.
"""

import os
import sys
import time
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any, Union
from pathlib import Path
import pytz

# 마스토돈 라이브러리
try:
    from mastodon import Mastodon, MastodonError, MastodonAPIError, MastodonNetworkError
except ImportError:
    print("❌ Mastodon.py 라이브러리가 설치되지 않았습니다.")
    print("pip install Mastodon.py 를 실행하세요.")
    sys.exit(1)

# 프로젝트 루트 경로 설정
current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(current_dir)

try:
    from config.settings import config
    from utils.logging_config import get_logger, log_api_call, log_performance, LogContext
    from utils.datetime_utils import format_datetime_korean, format_time_until
    from utils.validators import validate_toot_content
except ImportError as e:
    print(f"❌ 필수 모듈 임포트 실패: {e}")
    sys.exit(1)

logger = get_logger(__name__)


class MastodonRateLimiter:
    """
    마스토돈 API 호출 제한 관리 클래스
    API 제한을 준수하여 안전한 호출을 보장합니다.
    """
    
    def __init__(self, requests_per_hour: int = 300):
        """
        MastodonRateLimiter 초기화
        
        Args:
            requests_per_hour: 시간당 최대 요청 수
        """
        self.max_requests = requests_per_hour
        self.requests = []  # (timestamp, request_type) 튜플들
        self.last_request_time = 0
        self.min_interval = 1.0  # 최소 요청 간격 (초)
        
        # 요청 타입별 제한
        self.type_limits = {
            'status': 60,  # 시간당 툿 포스팅 제한
            'read': 240,   # 시간당 읽기 요청 제한
            'other': 100   # 기타 요청 제한
        }
    
    def wait_if_needed(self, request_type: str = 'other') -> None:
        """필요시 대기하여 API 제한 준수"""
        current_time = time.time()
        
        # 1시간 이전 요청들 제거
        cutoff_time = current_time - 3600
        self.requests = [(ts, req_type) for ts, req_type in self.requests if ts > cutoff_time]
        
        # 전체 요청 수 제한 체크
        if len(self.requests) >= self.max_requests:
            oldest_request_time = self.requests[0][0]
            wait_time = oldest_request_time + 3600 - current_time + 1
            if wait_time > 0:
                logger.warning(f"전체 API 요청 제한으로 {wait_time:.1f}초 대기 중...")
                time.sleep(wait_time)
        
        # 타입별 요청 수 제한 체크
        type_count = sum(1 for _, req_type in self.requests if req_type == request_type)
        type_limit = self.type_limits.get(request_type, 100)
        
        if type_count >= type_limit:
            # 해당 타입의 가장 오래된 요청 찾기
            type_requests = [(ts, req_type) for ts, req_type in self.requests if req_type == request_type]
            if type_requests:
                oldest_type_time = type_requests[0][0]
                wait_time = oldest_type_time + 3600 - current_time + 1
                if wait_time > 0:
                    logger.warning(f"{request_type} 요청 제한으로 {wait_time:.1f}초 대기 중...")
                    time.sleep(wait_time)
        
        # 최소 간격 체크
        time_since_last = current_time - self.last_request_time
        if time_since_last < self.min_interval:
            wait_time = self.min_interval - time_since_last
            time.sleep(wait_time)
        
        # 현재 요청 기록
        self.last_request_time = time.time()
        self.requests.append((self.last_request_time, request_type))
    
    def get_status(self) -> Dict[str, Any]:
        """현재 상태 반환"""
        current_time = time.time()
        cutoff_time = current_time - 3600
        recent_requests = [req for req in self.requests if req[0] > cutoff_time]
        
        # 타입별 카운트
        type_counts = {}
        for _, req_type in recent_requests:
            type_counts[req_type] = type_counts.get(req_type, 0) + 1
        
        return {
            'total_requests': len(recent_requests),
            'max_requests': self.max_requests,
            'requests_remaining': self.max_requests - len(recent_requests),
            'type_counts': type_counts,
            'type_limits': self.type_limits,
            'last_request_time': self.last_request_time,
            'time_since_last_request': current_time - self.last_request_time
        }


class TootResult:
    """
    툿 포스팅 결과를 나타내는 클래스
    """
    
    def __init__(self, success: bool, toot_id: Optional[str] = None,
                 toot_url: Optional[str] = None, error_message: Optional[str] = None,
                 response_data: Optional[Dict] = None):
        """
        TootResult 초기화
        
        Args:
            success: 성공 여부
            toot_id: 툿 ID
            toot_url: 툿 URL
            error_message: 오류 메시지
            response_data: API 응답 데이터
        """
        self.success = success
        self.toot_id = toot_id
        self.toot_url = toot_url
        self.error_message = error_message
        self.response_data = response_data
        self.timestamp = datetime.now(pytz.timezone('Asia/Seoul'))
    
    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리로 변환"""
        return {
            'success': self.success,
            'toot_id': self.toot_id,
            'toot_url': self.toot_url,
            'error_message': self.error_message,
            'timestamp': self.timestamp.isoformat(),
            'response_data': self.response_data
        }
    
    def __str__(self) -> str:
        """문자열 표현"""
        if self.success:
            return f"✅ 툿 포스팅 성공: {self.toot_url or self.toot_id}"
        else:
            return f"❌ 툿 포스팅 실패: {self.error_message}"


class MastodonClient:
    """
    마스토돈 API 클라이언트 클래스
    마스토돈 인스턴스와 통신하여 툿을 포스팅하고 상태를 관리합니다.
    """
    
    def __init__(self, instance_url: Optional[str] = None,
                 access_token: Optional[str] = None):
        """
        MastodonClient 초기화
        
        Args:
            instance_url: 마스토돈 인스턴스 URL
            access_token: 액세스 토큰
        """
        # 설정 로드
        self.instance_url = instance_url or config.MASTODON_INSTANCE_URL
        self.access_token = access_token or config.MASTODON_ACCESS_TOKEN
        
        # API 클라이언트
        self.mastodon = None
        self.rate_limiter = MastodonRateLimiter(
            requests_per_hour=getattr(config, 'RATE_LIMIT_REQUESTS_PER_HOUR', 300)
        )
        
        # 봇 정보 캐시
        self._bot_info = None
        self._bot_info_cache_time = None
        self._bot_info_cache_duration = 3600  # 1시간
        
        # 통계
        self.stats = {
            'total_attempts': 0,
            'successful_posts': 0,
            'failed_posts': 0,
            'connection_errors': 0,
            'api_errors': 0,
            'last_success_time': None,
            'last_error_time': None,
            'last_error_message': None
        }
        
        logger.info(f"마스토돈 클라이언트 초기화: {self.instance_url}")
    
    @log_performance
    def authenticate(self) -> bool:
        """
        마스토돈 API 인증
        
        Returns:
            bool: 인증 성공 여부
        """
        try:
            logger.info("마스토돈 API 인증 시작...")
            
            # URL 정규화
            if not self.instance_url.startswith(('http://', 'https://')):
                self.instance_url = f"https://{self.instance_url}"
            
            # 마스토돈 클라이언트 생성
            self.mastodon = Mastodon(
                access_token=self.access_token,
                api_base_url=self.instance_url,
                request_timeout=30
            )
            
            # 연결 테스트
            if self._test_connection():
                logger.info("✅ 마스토돈 API 인증 성공")
                return True
            else:
                logger.error("❌ 마스토돈 연결 테스트 실패")
                return False
            
        except MastodonError as e:
            logger.error(f"마스토돈 인증 오류: {e}")
            self.stats['connection_errors'] += 1
            return False
        except Exception as e:
            logger.error(f"인증 중 예상치 못한 오류: {e}")
            self.stats['connection_errors'] += 1
            return False
    
    @log_api_call
    def _test_connection(self) -> bool:
        """연결 테스트"""
        try:
            self.rate_limiter.wait_if_needed('read')
            
            # 현재 사용자 정보 조회
            account = self.mastodon.me()
            
            if account:
                username = account.get('username', 'Unknown')
                display_name = account.get('display_name', '')
                followers_count = account.get('followers_count', 0)
                statuses_count = account.get('statuses_count', 0)
                
                logger.info(f"봇 계정 확인: @{username} ({display_name})")
                logger.info(f"팔로워: {followers_count}명, 툿: {statuses_count}개")
                
                # 봇 정보 캐시
                self._bot_info = account
                self._bot_info_cache_time = time.time()
                
                return True
            else:
                logger.error("계정 정보를 가져올 수 없습니다")
                return False
            
        except MastodonAPIError as e:
            logger.error(f"마스토돈 API 오류: {e}")
            self.stats['api_errors'] += 1
            return False
        except MastodonNetworkError as e:
            logger.error(f"네트워크 오류: {e}")
            self.stats['connection_errors'] += 1
            return False
        except Exception as e:
            logger.error(f"연결 테스트 중 오류: {e}")
            return False
    
    @log_api_call
    @log_performance
    def post_toot(self, content: str, visibility: str = 'unlisted',
                  spoiler_text: Optional[str] = None,
                  content_warning: Optional[str] = None,
                  validate_content: bool = True) -> TootResult:
        """
        툿 포스팅
        
        Args:
            content: 툿 내용
            visibility: 공개 설정 ('unlisted', 'private', 'direct')
            spoiler_text: 스포일러 텍스트
            content_warning: 콘텐츠 경고
            validate_content: 내용 검증 여부
        
        Returns:
            TootResult: 포스팅 결과
        """
        self.stats['total_attempts'] += 1
        
        try:
            with LogContext(f"툿 포스팅") as ctx:
                ctx.log_step("내용 검증 중")
                
                # 내용 검증
                if validate_content:
                    validation_result = validate_toot_content(content)
                    if not validation_result.is_valid:
                        error_msg = f"내용 검증 실패: {validation_result.error_message}"
                        logger.error(error_msg)
                        self.stats['failed_posts'] += 1
                        return TootResult(False, error_message=error_msg)
                    
                    if validation_result.warnings:
                        for warning in validation_result.warnings:
                            logger.warning(f"내용 경고: {warning}")
                    
                    # 정규화된 내용 사용
                    content = validation_result.normalized_value
                
                ctx.log_step("마스토돈 API 호출 준비")
                
                # 인증 확인
                if not self.mastodon:
                    if not self.authenticate():
                        error_msg = "마스토돈 인증 실패"
                        self.stats['failed_posts'] += 1
                        self.stats['last_error_time'] = datetime.now()
                        self.stats['last_error_message'] = error_msg
                        return TootResult(False, error_message=error_msg)
                
                ctx.log_step("API 제한 확인 및 대기")
                
                # Rate limiting 적용
                self.rate_limiter.wait_if_needed('status')
                
                ctx.log_step("툿 포스팅 실행")
                
                # 포스팅 파라미터 준비
                post_params = {
                    'status': content,
                    'visibility': visibility
                }
                
                if spoiler_text:
                    post_params['spoiler_text'] = spoiler_text
                
                if content_warning:
                    post_params['sensitive'] = True
                    post_params['spoiler_text'] = content_warning
                
                # 실제 포스팅
                response = self.mastodon.status_post(**post_params)
                
                ctx.log_step("응답 처리")
                
                # 결과 처리
                if response:
                    toot_id = response.get('id')
                    toot_url = response.get('url')
                    
                    self.stats['successful_posts'] += 1
                    self.stats['last_success_time'] = datetime.now()
                    
                    logger.info(f"✅ 툿 포스팅 성공: {toot_url}")
                    logger.info(f"내용 미리보기: {content[:50]}...")
                    
                    return TootResult(
                        success=True,
                        toot_id=toot_id,
                        toot_url=toot_url,
                        response_data=response
                    )
                else:
                    error_msg = "응답 데이터가 없습니다"
                    self.stats['failed_posts'] += 1
                    self.stats['last_error_time'] = datetime.now()
                    self.stats['last_error_message'] = error_msg
                    return TootResult(False, error_message=error_msg)
        
        except MastodonAPIError as e:
            error_msg = f"마스토돈 API 오류: {e}"
            logger.error(error_msg)
            self.stats['failed_posts'] += 1
            self.stats['api_errors'] += 1
            self.stats['last_error_time'] = datetime.now()
            self.stats['last_error_message'] = error_msg
            return TootResult(False, error_message=error_msg)
        
        except MastodonNetworkError as e:
            error_msg = f"네트워크 오류: {e}"
            logger.error(error_msg)
            self.stats['failed_posts'] += 1
            self.stats['connection_errors'] += 1
            self.stats['last_error_time'] = datetime.now()
            self.stats['last_error_message'] = error_msg
            return TootResult(False, error_message=error_msg)
        
        except Exception as e:
            error_msg = f"예상치 못한 오류: {e}"
            logger.error(error_msg)
            self.stats['failed_posts'] += 1
            self.stats['last_error_time'] = datetime.now()
            self.stats['last_error_message'] = error_msg
            return TootResult(False, error_message=error_msg)
    
    def post_scheduled_toot(self, content: str, scheduled_at: datetime,
                           visibility: str = 'unlisted') -> TootResult:
        """
        예약 툿 포스팅 (즉시 실행)
        
        Args:
            content: 툿 내용
            scheduled_at: 원래 예약 시간 (로깅용)
            visibility: 공개 설정
        
        Returns:
            TootResult: 포스팅 결과
        """
        logger.info(f"예약 툿 실행: {format_datetime_korean(scheduled_at)}")
        
        # 예약 시간 정보를 내용에 추가할지 확인
        content_with_time = content
        
        return self.post_toot(content_with_time, visibility=visibility)
    
    def get_bot_info(self, force_refresh: bool = False) -> Optional[Dict[str, Any]]:
        """
        봇 계정 정보 반환
        
        Args:
            force_refresh: 캐시 무시하고 강제 새로고침
        
        Returns:
            Optional[Dict[str, Any]]: 봇 계정 정보
        """
        current_time = time.time()
        
        # 캐시 확인
        if (not force_refresh and self._bot_info and self._bot_info_cache_time and
            current_time - self._bot_info_cache_time < self._bot_info_cache_duration):
            return self._bot_info
        
        # 새로 조회
        try:
            if not self.mastodon:
                if not self.authenticate():
                    return None
            
            self.rate_limiter.wait_if_needed('read')
            account = self.mastodon.me()
            
            if account:
                self._bot_info = account
                self._bot_info_cache_time = current_time
                return account
            
        except Exception as e:
            logger.error(f"봇 정보 조회 실패: {e}")
        
        return None
    
    def check_connection(self) -> bool:
        """
        연결 상태 확인
        
        Returns:
            bool: 연결 상태
        """
        try:
            if not self.mastodon:
                return self.authenticate()
            
            return self._test_connection()
            
        except Exception as e:
            logger.error(f"연결 확인 실패: {e}")
            return False
    
    def send_notification(self, message: str, mention_admin: bool = False) -> TootResult:
        """
        시스템 알림 툿 전송
        
        Args:
            message: 알림 메시지
            mention_admin: 관리자 멘션 여부
        
        Returns:
            TootResult: 전송 결과
        """
        # 알림 메시지 구성
        notification_content = f"🤖 시스템 알림\n\n{message}"
        
        # 관리자 멘션 추가
        admin_id = getattr(config, 'SYSTEM_ADMIN_ID', None)
        if mention_admin and admin_id:
            notification_content = f"@{admin_id} {notification_content}"
        
        # 시간 정보 추가
        current_time = datetime.now(pytz.timezone('Asia/Seoul'))
        time_str = format_datetime_korean(current_time)
        notification_content += f"\n\n[{time_str}]"
        
        return self.post_toot(
            content=notification_content,
            visibility='direct',  # 공개 타임라인에 노출되지 않도록
            validate_content=False  # 시스템 메시지는 검증 생략
        )
    
    def get_stats(self) -> Dict[str, Any]:
        """통계 정보 반환"""
        rate_limiter_status = self.rate_limiter.get_status()
        
        stats = self.stats.copy()
        
        # 성공률 계산
        if stats['total_attempts'] > 0:
            stats['success_rate'] = (stats['successful_posts'] / stats['total_attempts']) * 100
        else:
            stats['success_rate'] = 0
        
        # 시간 정보 포맷팅
        for time_key in ['last_success_time', 'last_error_time']:
            if stats[time_key]:
                stats[f"{time_key}_formatted"] = format_datetime_korean(stats[time_key])
        
        # Rate limiter 상태 추가
        stats['rate_limiter'] = rate_limiter_status
        
        # 봇 정보 추가
        bot_info = self.get_bot_info()
        if bot_info:
            stats['bot_username'] = bot_info.get('username')
            stats['bot_display_name'] = bot_info.get('display_name')
            stats['bot_statuses_count'] = bot_info.get('statuses_count')
            stats['bot_followers_count'] = bot_info.get('followers_count')
        
        return stats
    
    def __str__(self) -> str:
        """문자열 표현"""
        return f"MastodonClient({self.instance_url})"


# 전역 클라이언트 인스턴스
_mastodon_client: Optional[MastodonClient] = None


def get_mastodon_manager() -> MastodonClient:
    """전역 마스토돈 클라이언트 반환"""
    global _mastodon_client
    
    if _mastodon_client is None:
        _mastodon_client = MastodonClient()
        
        # 즉시 인증 시도
        if not _mastodon_client.authenticate():
            logger.error("마스토돈 클라이언트 초기화 실패")
            raise RuntimeError("마스토돈 인증 실패")
    
    return _mastodon_client


def check_mastodon_connection() -> bool:
    """마스토돈 연결 상태 확인"""
    try:
        client = get_mastodon_manager()
        return client.check_connection()
    except Exception as e:
        logger.error(f"마스토돈 연결 확인 실패: {e}")
        return False


def send_system_notification(message: str, to_admin: bool = False) -> bool:
    """
    시스템 알림 전송
    
    Args:
        message: 알림 메시지
        to_admin: 관리자에게 전송 여부
    
    Returns:
        bool: 전송 성공 여부
    """
    try:
        client = get_mastodon_manager()
        result = client.send_notification(message, mention_admin=to_admin)
        return result.success
    except Exception as e:
        logger.error(f"시스템 알림 전송 실패: {e}")
        return False


def test_mastodon_posting() -> bool:
    """마스토돈 포스팅 테스트"""
    try:
        client = get_mastodon_manager()
        
        # 테스트 메시지
        test_content = f"🧪 마스토돈 봇 테스트\n\n{format_datetime_korean(datetime.now(pytz.timezone('Asia/Seoul')))}"
        
        # 테스트 포스팅
        result = client.post_toot(
            content=test_content,
            visibility='direct'  # 테스트는 direct로
        )
        
        if result.success:
            logger.info(f"✅ 테스트 포스팅 성공: {result.toot_url}")
            return True
        else:
            logger.error(f"❌ 테스트 포스팅 실패: {result.error_message}")
            return False
            
    except Exception as e:
        logger.error(f"마스토돈 포스팅 테스트 실패: {e}")
        return False


if __name__ == "__main__":
    """마스토돈 클라이언트 테스트"""
    print("🧪 마스토돈 클라이언트 테스트 시작...")
    
    try:
        # 클라이언트 초기화
        client = MastodonClient()
        
        # 인증 테스트
        print("🔐 인증 테스트...")
        if client.authenticate():
            print("✅ 인증 성공")
        else:
            print("❌ 인증 실패")
            sys.exit(1)
        
        # 봇 정보 확인
        print("🤖 봇 정보 확인...")
        bot_info = client.get_bot_info()
        if bot_info:
            username = bot_info.get('username', 'Unknown')
            display_name = bot_info.get('display_name', '')
            followers = bot_info.get('followers_count', 0)
            statuses = bot_info.get('statuses_count', 0)
            print(f"  계정: @{username} ({display_name})")
            print(f"  팔로워: {followers}명, 툿: {statuses}개")
        else:
            print("  봇 정보 조회 실패")
        
        # 연결 테스트
        print("🌐 연결 테스트...")
        if client.check_connection():
            print("✅ 연결 정상")
        else:
            print("❌ 연결 실패")
        
        # 포스팅 테스트 (실제로는 주석 처리)
        # print("📝 포스팅 테스트...")
        # result = client.post_toot(
        #     content="🧪 마스토돈 봇 테스트 툿입니다.",
        #     visibility='direct'
        # )
        # print(f"포스팅 결과: {result}")
        
        # 통계 정보
        print("📊 통계 정보:")
        stats = client.get_stats()
        print(f"  총 시도: {stats['total_attempts']}회")
        print(f"  성공/실패: {stats['successful_posts']}/{stats['failed_posts']}")
        print(f"  성공률: {stats['success_rate']:.1f}%")
        
        # Rate limiter 상태
        rate_status = stats['rate_limiter']
        print(f"  API 제한: {rate_status['total_requests']}/{rate_status['max_requests']}")
        
        print("✅ 마스토돈 클라이언트 테스트 완료!")
        
    except Exception as e:
        print(f"❌ 테스트 실패: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
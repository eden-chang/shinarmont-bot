"""
캐시 관리 모듈 (TTL 제거 버전)
애플리케이션의 데이터 캐싱을 담당합니다.
실시간 반영을 위해 TTL을 제거하고 명시적 무효화만 지원합니다.
"""

import os
import sys
import time
import threading
import json
import hashlib
from typing import Any, Optional, Dict, List, Callable, Union
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from functools import wraps

# 경로 설정 (VM 환경 대응)
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

try:
    from config.settings import config
    from utils.logging_config import logger, should_log_debug
except ImportError:
    # VM 환경에서 임포트 실패 시 폴백
    import logging
    logger = logging.getLogger('cache_manager')
    
    # 기본 설정값
    class FallbackConfig:
        DEBUG_MODE = False
    config = FallbackConfig()


@dataclass
class CacheItem:
    """캐시 아이템 데이터 클래스 (TTL 제거)"""
    key: str
    value: Any
    created_at: float
    
    def __post_init__(self):
        pass
    
    @property
    def age(self) -> float:
        """캐시 아이템의 나이 (초)"""
        return time.time() - self.created_at


class CacheManager:
    """캐시 관리자 클래스 (TTL 제거)"""
    
    def __init__(self, max_size: int = 1000):
        """
        CacheManager 초기화
        
        Args:
            max_size: 최대 캐시 아이템 수
        """
        self.max_size = max_size
        self._cache: Dict[str, CacheItem] = {}
        self._lock = threading.RLock()
        
        logger.debug(f"CacheManager 초기화 - 최대 크기: {max_size}")
    
    def get(self, key: str) -> Optional[Any]:
        """
        캐시에서 값 조회
        
        Args:
            key: 캐시 키
            
        Returns:
            Optional[Any]: 캐시된 값 또는 None
        """
        with self._lock:
            if key not in self._cache:
                if should_log_debug():
                    logger.debug(f"캐시 미스: {key}")
                return None

            item = self._cache[key]
            if should_log_debug():
                logger.debug(f"캐시 히트: {key}")
            return item.value
    
    def set(self, key: str, value: Any) -> bool:
        """
        캐시에 값 설정
        
        Args:
            key: 캐시 키
            value: 저장할 값
            
        Returns:
            bool: 설정 성공 여부
        """
        with self._lock:
            # 캐시 크기 제한 확인
            if len(self._cache) >= self.max_size and key not in self._cache:
                self._evict_lru()
            
            # 새 캐시 아이템 생성
            item = CacheItem(
                key=key,
                value=value,
                created_at=time.time()
            )
            
            self._cache[key] = item

            if should_log_debug():
                logger.debug(f"캐시 설정: {key}")
            return True

    def delete(self, key: str) -> bool:
        """
        캐시에서 값 삭제

        Args:
            key: 삭제할 캐시 키

        Returns:
            bool: 삭제 성공 여부
        """
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                if should_log_debug():
                    logger.debug(f"캐시 삭제: {key}")
                return True
            return False

    def clear(self) -> int:
        """
        모든 캐시 삭제

        Returns:
            int: 삭제된 아이템 수
        """
        with self._lock:
            count = len(self._cache)
            self._cache.clear()
            if should_log_debug():
                logger.debug(f"모든 캐시 삭제: {count}개 아이템")
            return count
    
    def exists(self, key: str) -> bool:
        """
        캐시 키 존재 여부 확인
        
        Args:
            key: 확인할 캐시 키
            
        Returns:
            bool: 존재 여부
        """
        with self._lock:
            return key in self._cache
    
    def _evict_lru(self) -> None:
        """LRU 방식으로 캐시 아이템 제거"""
        if not self._cache:
            return
        
        # 가장 오래된 아이템 찾기
        lru_key = min(
            self._cache.keys(),
            key=lambda k: self._cache[k].created_at
        )
        
        del self._cache[lru_key]
        logger.debug(f"LRU 제거: {lru_key}")
    
    
    def get_keys(self, pattern: str = None) -> List[str]:
        """
        캐시 키 목록 반환
        
        Args:
            pattern: 필터링할 패턴 (None이면 모든 키)
            
        Returns:
            List[str]: 캐시 키 목록
        """
        with self._lock:
            keys = list(self._cache.keys())
            if pattern:
                keys = [key for key in keys if pattern in key]
            return keys
    
    def get_size(self) -> int:
        """캐시 크기 반환"""
        return len(self._cache)
    
    def is_full(self) -> bool:
        """캐시가 가득 찼는지 확인"""
        return len(self._cache) >= self.max_size


class BotCacheManager:
    """봇 전용 캐시 관리자 (TTL 제거)"""
    
    def __init__(self):
        """BotCacheManager 초기화"""
        self.general_cache = CacheManager(max_size=500)
        self.user_cache = CacheManager(max_size=200)
        self.sheet_cache = CacheManager(max_size=100)
        self.command_cache = CacheManager(max_size=50)
        
        logger.info("BotCacheManager 초기화 완료 (TTL 제거)")
    
    def cache_user_data(self, user_id: str, user_data: Dict[str, Any]) -> bool:
        """
        사용자 데이터 캐싱
        
        Args:
            user_id: 사용자 ID
            user_data: 사용자 데이터
            
        Returns:
            bool: 캐싱 성공 여부
        """
        cache_key = f"user:{user_id}"
        return self.user_cache.set(cache_key, user_data)
    
    def get_user_data(self, user_id: str) -> Optional[Dict[str, Any]]:
        """
        캐시된 사용자 데이터 조회
        
        Args:
            user_id: 사용자 ID
            
        Returns:
            Optional[Dict]: 사용자 데이터 또는 None
        """
        cache_key = f"user:{user_id}"
        return self.user_cache.get(cache_key)
    
    def cache_all_users_data(self, users_data: List[Dict[str, Any]], ttl=None) -> bool:
        """
        전체 사용자 목록 캐싱
        
        Args:
            users_data: 전체 사용자 데이터 리스트
            
        Returns:
            bool: 캐싱 성공 여부
        """
        return self.sheet_cache.set("all_users_data", users_data)

    def get_all_users_data(self) -> Optional[List[Dict[str, Any]]]:
        """
        캐시된 전체 사용자 목록 조회
        
        Returns:
            Optional[List]: 전체 사용자 데이터 리스트 또는 None
        """
        return self.sheet_cache.get("all_users_data")
    
    
    def cache_item_data(self, item_data: List[Dict], ttl_seconds: int = None) -> bool:
        """
        아이템 데이터 캐싱 (TTL: .env의 CACHE_TTL)

        Args:
            item_data: 아이템 데이터 리스트
            ttl_seconds: 캐시 유지 시간 (초 단위, None이면 config.CACHE_TTL 사용)

        Returns:
            bool: 캐싱 성공 여부
        """
        if ttl_seconds is None:
            ttl_seconds = getattr(config, 'CACHE_TTL', 1800)

        current_time = time.time()
        expire_time = current_time + ttl_seconds

        cache_data = {
            'data': item_data,
            'expire_time': expire_time,
            'cached_at': current_time
        }

        return self.command_cache.set("item_data_with_ttl", cache_data)

    def get_item_data(self) -> Optional[List[Dict]]:
        """
        아이템 데이터 조회 (TTL 확인)

        Returns:
            Optional[List]: 아이템 데이터 리스트 또는 None (만료된 경우)
        """
        cached_item = self.command_cache.get("item_data_with_ttl")

        if cached_item is None:
            return None

        # TTL 확인
        current_time = time.time()
        if current_time > cached_item.get('expire_time', 0):
            # 만료된 캐시 삭제
            self.command_cache.delete("item_data_with_ttl")
            return None

        return cached_item.get('data')
    
    def cache_custom_commands(self, commands: Dict[str, List[str]]) -> bool:
        """
        커스텀 명령어 캐싱 (TTL: .env의 CACHE_TTL)

        Args:
            commands: 커스텀 명령어 딕셔너리

        Returns:
            bool: 캐싱 성공 여부
        """
        current_time = time.time()
        ttl_seconds = getattr(config, 'CACHE_TTL', 1800)
        expire_time = current_time + ttl_seconds

        cache_data = {
            'data': commands,
            'expire_time': expire_time,
            'cached_at': current_time
        }

        return self.command_cache.set("custom_commands_with_ttl", cache_data)

    def get_custom_commands(self) -> Optional[Dict[str, List[str]]]:
        """
        캐시된 커스텀 명령어 조회 (TTL 확인)

        Returns:
            Optional[Dict]: 커스텀 명령어 딕셔너리 또는 None (만료된 경우)
        """
        cached_item = self.command_cache.get("custom_commands_with_ttl")

        if cached_item is None:
            return None

        # TTL 확인
        current_time = time.time()
        if current_time > cached_item.get('expire_time', 0):
            # 만료된 캐시 삭제
            self.command_cache.delete("custom_commands_with_ttl")
            return None

        return cached_item.get('data')
    
    def cache_help_items(self, help_items: List[Dict[str, str]]) -> bool:
        """
        도움말 항목 캐싱 (TTL: .env의 CACHE_TTL)

        Args:
            help_items: 도움말 항목 리스트

        Returns:
            bool: 캐싱 성공 여부
        """
        current_time = time.time()
        ttl_seconds = getattr(config, 'CACHE_TTL', 1800)
        expire_time = current_time + ttl_seconds

        cache_data = {
            'data': help_items,
            'expire_time': expire_time,
            'cached_at': current_time
        }

        return self.command_cache.set("help_items_with_ttl", cache_data)
    
    def get_help_items(self) -> Optional[List[Dict[str, str]]]:
        """
        캐시된 도움말 항목 조회 (TTL 확인)

        Returns:
            Optional[List]: 도움말 항목 리스트 또는 None (만료된 경우)
        """
        cached_item = self.command_cache.get("help_items_with_ttl")

        if cached_item is None:
            return None

        # TTL 확인
        current_time = time.time()
        if current_time > cached_item.get('expire_time', 0):
            # 만료된 캐시 삭제
            self.command_cache.delete("help_items_with_ttl")
            return None

        return cached_item.get('data')
    
    def cache_fortune_phrases(self, phrases: List[str], ttl_seconds: int = None) -> bool:
        """
        운세 문구 캐싱 (TTL 지원)

        Args:
            phrases: 운세 문구 리스트
            ttl_seconds: 캐시 유지 시간 (초 단위, None이면 config.CACHE_TTL 사용)

        Returns:
            bool: 캐싱 성공 여부
        """
        if ttl_seconds is None:
            ttl_seconds = getattr(config, 'CACHE_TTL', 1800)

        current_time = time.time()
        expire_time = current_time + ttl_seconds
        
        cache_data = {
            'data': phrases,
            'expire_time': expire_time,
            'cached_at': current_time
        }
        
        return self.command_cache.set("fortune_phrases_with_ttl", cache_data)
    
    def get_fortune_phrases(self) -> Optional[List[str]]:
        """
        캐시된 운세 문구 조회 (TTL 확인)
        
        Returns:
            Optional[List]: 운세 문구 리스트 또는 None (만료된 경우)
        """
        cached_item = self.command_cache.get("fortune_phrases_with_ttl")
        
        if cached_item is None:
            return None
        
        current_time = time.time()
        if current_time > cached_item.get('expire_time', 0):
            self.command_cache.delete("fortune_phrases_with_ttl")
            return None
        
        return cached_item.get('data')
    
    # BotCacheManager 클래스 내부에 추가할 메서드들

    def cache_today_fortune(self, user_id: str, fortune: str, kst_timezone=None) -> bool:
        """
        오늘의 운세 캐싱 (KST 기준)
        
        Args:
            user_id: 사용자 ID
            fortune: 운세 문구
            kst_timezone: KST 타임존 객체
            
        Returns:
            bool: 캐싱 성공 여부
        """
        from datetime import datetime, timezone, timedelta
        
        if kst_timezone is None:
            kst_timezone = timezone(timedelta(hours=9))
        
        kst_now = datetime.now(kst_timezone)
        today = kst_now.strftime('%Y-%m-%d')
        cache_key = f"fortune:{user_id}:{today}"
        return self.user_cache.set(cache_key, fortune)

    def get_today_fortune(self, user_id: str, kst_timezone=None) -> Optional[str]:
        """
        오늘의 운세 조회 (KST 기준)
        
        Args:
            user_id: 사용자 ID
            kst_timezone: KST 타임존 객체
            
        Returns:
            Optional[str]: 오늘의 운세 또는 None
        """
        from datetime import datetime, timezone, timedelta
        
        if kst_timezone is None:
            kst_timezone = timezone(timedelta(hours=9))
        
        kst_now = datetime.now(kst_timezone)
        today = kst_now.strftime('%Y-%m-%d')
        cache_key = f"fortune:{user_id}:{today}"
        return self.user_cache.get(cache_key)

    def cache_shop_items(self, shop_items: List[Dict[str, Any]]) -> bool:
        """
        상점 아이템 목록 캐싱 (TTL: .env의 CACHE_TTL)

        Args:
            shop_items: 상점 아이템 리스트

        Returns:
            bool: 캐싱 성공 여부
        """
        current_time = time.time()
        ttl_seconds = getattr(config, 'CACHE_TTL', 1800)
        expire_time = current_time + ttl_seconds

        cache_data = {
            'data': shop_items,
            'expire_time': expire_time,
            'cached_at': current_time
        }

        return self.command_cache.set("shop_items_with_ttl", cache_data)

    def get_shop_items(self) -> Optional[List[Dict[str, Any]]]:
        """
        캐시된 상점 아이템 목록 조회 (TTL 확인)

        Returns:
            Optional[List]: 상점 아이템 리스트 또는 None (만료된 경우)
        """
        cached_item = self.command_cache.get("shop_items_with_ttl")

        if cached_item is None:
            return None

        # TTL 확인
        current_time = time.time()
        if current_time > cached_item.get('expire_time', 0):
            # 만료된 캐시 삭제
            self.command_cache.delete("shop_items_with_ttl")
            return None

        return cached_item.get('data')

    def invalidate_today_fortune(self, user_id: str) -> bool:
        """
        특정 사용자의 오늘의 운세 캐시 무효화
        
        Args:
            user_id: 사용자 ID
            
        Returns:
            bool: 무효화 성공 여부
        """
        from datetime import datetime
        today = datetime.now().strftime('%Y-%m-%d')
        cache_key = f"fortune:{user_id}:{today}"
        return self.user_cache.delete(cache_key)

    def invalidate_shop_items(self) -> bool:
        """
        상점 아이템 캐시 무효화

        Returns:
            bool: 무효화 성공 여부
        """
        return self.command_cache.delete("shop_items_with_ttl")

    def cleanup_old_fortunes(self, kst_timezone=None) -> int:
        """
        오래된 운세 캐시 정리 (KST 기준으로 어제 이전 것들)
        
        Args:
            kst_timezone: KST 타임존 객체
            
        Returns:
            int: 정리된 아이템 수
        """
        from datetime import datetime, timezone, timedelta
        
        if kst_timezone is None:
            kst_timezone = timezone(timedelta(hours=9))
        
        kst_now = datetime.now(kst_timezone)
        today = kst_now.strftime('%Y-%m-%d')
        
        fortune_keys = [key for key in self.user_cache.get_keys() if key.startswith('fortune:')]
        cleaned_count = 0
        
        for key in fortune_keys:
            try:
                date_part = key.split(':')[2]
                if date_part < today:
                    if self.user_cache.delete(key):
                        cleaned_count += 1
            except (IndexError, ValueError):
                continue
        
        if cleaned_count > 0:
            logger.info(f"🧹 운세 캐시 정리 완료 | {cleaned_count}개 제거 (KST 기준)")
        
        return cleaned_count

    def cache_worksheet_data(self, worksheet_name: str, data: List[Dict[str, Any]]) -> bool:
        """
        워크시트 데이터 캐싱
        
        Args:
            worksheet_name: 워크시트 이름
            data: 워크시트 데이터
            
        Returns:
            bool: 캐싱 성공 여부
        """
        cache_key = f"sheet:{worksheet_name}"
        return self.sheet_cache.set(cache_key, data)
    
    def get_worksheet_data(self, worksheet_name: str) -> Optional[List[Dict[str, Any]]]:
        """
        캐시된 워크시트 데이터 조회
        
        Args:
            worksheet_name: 워크시트 이름
            
        Returns:
            Optional[List]: 워크시트 데이터 또는 None
        """
        cache_key = f"sheet:{worksheet_name}"
        return self.sheet_cache.get(cache_key)
    
    def cache_roster_data(self, roster_data: List[Dict[str, Any]]) -> bool:
        """
        명단 데이터 캐싱 (TTL: .env의 CACHE_TTL)

        Args:
            roster_data: 명단 데이터 리스트

        Returns:
            bool: 캐싱 성공 여부
        """
        current_time = time.time()
        ttl_seconds = getattr(config, 'CACHE_TTL', 1800)
        expire_time = current_time + ttl_seconds
        
        # 데이터와 만료 시간을 함께 저장
        cache_data = {
            'data': roster_data,
            'expire_time': expire_time,
            'cached_at': current_time
        }
        
        return self.sheet_cache.set("roster_data_with_ttl", cache_data)
    
    def get_roster_data(self) -> Optional[List[Dict[str, Any]]]:
        """
        캐시된 명단 데이터 조회 (1시간 TTL 확인)
        
        Returns:
            Optional[List]: 명단 데이터 또는 None (만료된 경우)
        """
        cached_item = self.sheet_cache.get("roster_data_with_ttl")
        
        if cached_item is None:
            return None
        
        # TTL 확인
        current_time = time.time()
        if current_time > cached_item.get('expire_time', 0):
            # 만료된 캐시 삭제
            self.sheet_cache.delete("roster_data_with_ttl")
            return None
        
        return cached_item.get('data')
    
    def invalidate_user_cache(self, user_id: str = None) -> int:
        """
        사용자 캐시 무효화
        
        Args:
            user_id: 특정 사용자 ID (None이면 모든 사용자)
            
        Returns:
            int: 무효화된 아이템 수
        """
        if user_id:
            cache_key = f"user:{user_id}"
            return 1 if self.user_cache.delete(cache_key) else 0
        else:
            return self.user_cache.clear()
    
    def invalidate_sheet_cache(self, worksheet_name: str = None) -> int:
        """
        시트 캐시 무효화
        
        Args:
            worksheet_name: 특정 워크시트 (None이면 모든 시트)
            
        Returns:
            int: 무효화된 아이템 수
        """
        if worksheet_name:
            cache_key = f"sheet:{worksheet_name}"
            return 1 if self.sheet_cache.delete(cache_key) else 0
        else:
            return self.sheet_cache.clear()
    
    def invalidate_command_cache(self) -> int:
        """
        명령어 관련 캐시 무효화
        
        Returns:
            int: 무효화된 아이템 수
        """
        return self.command_cache.clear()
    
    def invalidate_all_users_data(self) -> bool:
        """
        전체 사용자 데이터 캐시 무효화
        
        Returns:
            bool: 무효화 성공 여부
        """
        return self.sheet_cache.delete("all_users_data")
    
    
    def invalidate_item_data(self) -> bool:
        """
        아이템 데이터 캐시 무효화

        Returns:
            bool: 무효화 성공 여부
        """
        return self.command_cache.delete("item_data_with_ttl")
    
    def invalidate_roster_data(self) -> bool:
        """
        명단 데이터 캐시 무효화
        
        Returns:
            bool: 무효화 성공 여부
        """
        return self.sheet_cache.delete("roster_data_with_ttl")
    
    def get_roster_cache_info(self) -> Dict[str, Any]:
        """
        명단 캐시 상태 정보 반환
        
        Returns:
            Dict: 캐시 상태 정보
        """
        cached_item = self.sheet_cache.get("roster_data_with_ttl")
        
        if cached_item is None:
            return {
                'cached': False,
                'message': '캐시된 명단 데이터가 없습니다'
            }
        
        current_time = time.time()
        expire_time = cached_item.get('expire_time', 0)
        cached_at = cached_item.get('cached_at', 0)
        
        if current_time > expire_time:
            return {
                'cached': False,
                'expired': True,
                'message': '캐시가 만료되었습니다'
            }
        
        remaining_seconds = expire_time - current_time
        age_seconds = current_time - cached_at
        data_count = len(cached_item.get('data', []))
        
        return {
            'cached': True,
            'expired': False,
            'data_count': data_count,
            'age_seconds': round(age_seconds, 2),
            'remaining_seconds': round(remaining_seconds, 2),
            'remaining_hours': round(remaining_seconds / 3600, 2),
            'cached_at': datetime.fromtimestamp(cached_at).strftime('%Y-%m-%d %H:%M:%S'),
            'expires_at': datetime.fromtimestamp(expire_time).strftime('%Y-%m-%d %H:%M:%S'),
            'message': f'{data_count}개 명단 데이터 캐시됨, {round(remaining_seconds/3600, 1)}시간 남음'
        }
    

    def cleanup_all_expired(self) -> None:
        """캐시 정리 (TTL 제거 버전 - 실제로는 아무 작업 하지 않음)"""
        try:
            logger.debug("캐시 정리 호출됨 (TTL 제거 버전 - 실제 정리 없음)")
        except Exception as e:
            logger.error(f"캐시 정리 중 오류 발생: {e}")

# 간단한 캐시 데코레이터
def cache_result(cache_manager: CacheManager):
    """함수 결과를 캐싱하는 간단한 데코레이터"""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            # 간단한 키 생성
            args_str = str(args) + str(sorted(kwargs.items()))
            args_hash = hashlib.md5(args_str.encode()).hexdigest()[:8]
            cache_key = f"{func.__name__}_{args_hash}"
            
            # 캐시 확인 및 실행
            cached_result = cache_manager.get(cache_key)
            if cached_result is not None:
                return cached_result
            
            result = func(*args, **kwargs)
            cache_manager.set(cache_key, result)
            return result
        
        return wrapper
    return decorator


# 전역 캐시 매니저 인스턴스
bot_cache = BotCacheManager()


# 편의 함수들


def clear_all_cache() -> Dict[str, int]:
    """모든 캐시 삭제"""
    return {
        'general': bot_cache.general_cache.clear(),
        'user': bot_cache.user_cache.clear(),
        'sheet': bot_cache.sheet_cache.clear(),
        'command': bot_cache.command_cache.clear()
    }


def invalidate_user_data(user_id: str) -> bool:
    """특정 사용자 캐시 무효화"""
    return bot_cache.invalidate_user_cache(user_id) > 0


def invalidate_sheet_data(worksheet_name: str = None) -> int:
    """시트 데이터 캐시 무효화"""
    return bot_cache.invalidate_sheet_cache(worksheet_name)


def invalidate_all_users_data() -> bool:
    """전체 사용자 데이터 캐시 무효화"""
    return bot_cache.invalidate_all_users_data()


def invalidate_item_data() -> bool:
    """아이템 데이터 캐시 무효화"""
    return bot_cache.invalidate_item_data()


# 캐시 워밍업 함수 (애플리케이션 시작 시 사용)
def warmup_cache(sheets_manager):
    """
    애플리케이션 시작 시 캐시 워밍업
    BOT_TYPE에 따라 필요한 시트를 순차적으로 캐싱합니다.
    각 시트 캐싱 사이에 60초 간격을 둡니다.

    Args:
        sheets_manager: SheetsManager 인스턴스
    """
    try:
        from config.settings import config

        logger.info("=" * 60)
        logger.info("🔥 캐시 워밍업 시작...")
        logger.info(f"⚙️ BOT_TYPE: {config.BOT_TYPE}")
        logger.info("=" * 60)

        # BOT_TYPE에 따라 캐싱할 시트 결정
        if config.BOT_TYPE == 'store':
            cache_sheets = ['명단', '도움말', '운세', '커스텀', '상점']
        else:  # default
            cache_sheets = ['명단', '도움말', '운세', '커스텀']

        def is_rate_limit_error(err: Exception) -> bool:
            msg = str(err).lower()
            return '429' in msg or ('rate' in msg and 'limit' in msg)

        # 각 시트를 순차적으로 캐싱 (10초 간격 재시도, RateLimit 시 60초 대기)
        for i, sheet_name in enumerate(cache_sheets):
            attempt = 0
            max_attempts = max(getattr(config, 'MAX_RETRIES', 5), 1)
            while attempt < max_attempts:
                try:
                    logger.info(f"💾 [{i+1}/{len(cache_sheets)}] '{sheet_name}' 시트 캐싱 중... (시도 #{attempt+1})")

                    if sheet_name == '명단':
                        roster_data = sheets_manager.get_worksheet_data(config.get_worksheet_name('ROSTER'))
                        cache_roster_data(roster_data)
                        logger.info(f"   ✅ '{sheet_name}' 캐싱 완료 | {len(roster_data)}개 항목")

                    elif sheet_name == '도움말':
                        help_items = sheets_manager.get_help_items()
                        bot_cache.cache_help_items(help_items)
                        logger.info(f"   ✅ '{sheet_name}' 캐싱 완료 | {len(help_items)}개 항목")

                    elif sheet_name == '운세':
                        fortune_data = sheets_manager.get_worksheet_data(config.get_worksheet_name('FORTUNE'))
                        fortune_phrases = [str(row.get('문구', '')).strip() for row in fortune_data if row.get('문구', '').strip()]
                        bot_cache.cache_fortune_phrases(fortune_phrases)
                        logger.info(f"   ✅ '{sheet_name}' 캐싱 완료 | {len(fortune_phrases)}개 항목")

                    elif sheet_name == '커스텀':
                        custom_data = sheets_manager.get_worksheet_data(config.get_worksheet_name('CUSTOM'))
                        custom_commands = {}
                        for row in custom_data:
                            command = str(row.get('명령어', '')).strip()
                            phrase = str(row.get('문구', '')).strip()
                            if command and phrase:
                                if command not in custom_commands:
                                    custom_commands[command] = []
                                custom_commands[command].append(phrase)
                        bot_cache.cache_custom_commands(custom_commands)
                        logger.info(f"   ✅ '{sheet_name}' 캐싱 완료 | {len(custom_commands)}개 항목")

                    elif sheet_name == '상점':
                        shop_data = sheets_manager.get_worksheet_data(config.get_worksheet_name('SHOP'))
                        cache_shop_items(shop_data)
                        logger.info(f"   ✅ '{sheet_name}' 캐싱 완료 | {len(shop_data)}개 항목")

                    # 시트 캐싱이 성공하면 다음 시트로 이동 (불필요한 대기 없음)
                    break

                except Exception as e:
                    attempt += 1
                    if is_rate_limit_error(e):
                        logger.warning(f"⚠️ API 제한 감지 (429) | 60초 대기 후 재시도... (시도 {attempt}/{max_attempts})")
                        time.sleep(60)
                    else:
                        logger.warning(f"   ⚠️ '{sheet_name}' 캐싱 실패 | {e}")
                        if attempt < max_attempts:
                            logger.info(f"   ⏳ 10초 대기 후 재시도... (시도 {attempt}/{max_attempts})")
                            time.sleep(10)
                        else:
                            logger.warning(f"   ⛔ 최대 재시도 도달 | 다음 시트로 이동")

        # store 봇 타입인 경우에만 '관리' 시트 확인
        if config.BOT_TYPE == 'store':
            try:
                logger.info("'관리' 워크시트 존재 확인 중...")
                # ErrorContext를 피하기 위해 직접 worksheet() 호출
                try:
                    management_sheet = sheets_manager.spreadsheet.worksheet('관리')
                    # '관리' 시트가 존재하면 1회 조회 (1행: 헤더, 2행: 설명, 3행부터: 데이터)
                    all_values = management_sheet.get_all_values()
                    if len(all_values) >= 3:
                        headers = all_values[0]  # 1행: 헤더
                        data_rows = all_values[2:]  # 3행부터 데이터
                        management_data = []
                        for row_values in data_rows:
                            if any(row_values):  # 빈 행이 아닌 경우
                                record = dict(zip(headers, row_values))
                                management_data.append(record)
                        logger.info(f"   ✅ '관리' 워크시트 확인 완료 | {len(management_data)}개 항목")
                    else:
                        logger.info(f"   ✅ '관리' 워크시트 확인 완료 | 데이터 없음")
                except Exception:
                    logger.info("   ℹ️ '관리' 워크시트 없음 (선택사항)")
            except Exception as e:
                logger.info(f"   ℹ️ '관리' 워크시트 확인 실패 (선택사항) | {e}")

        logger.info("=" * 60)
        logger.info("✅ 캐시 워밍업 완료")
        logger.info("=" * 60)

    except Exception as e:
        logger.error(f"❌ 캐시 워밍업 실패: {e}")
        raise




# 데이터 변경 시 관련 캐시 무효화 도우미 함수들
def on_user_data_changed(user_id: str = None):
    """사용자 데이터 변경 시 호출"""
    if user_id:
        invalidate_user_data(user_id)
        logger.info(f"🗑️ 캐시 무효화 → @{user_id}")
    else:
        bot_cache.invalidate_user_cache()
        invalidate_all_users_data()
        logger.info("전체 사용자 캐시 무효화")


def on_sheet_data_changed(worksheet_name: str = None):
    """시트 데이터 변경 시 호출"""
    invalidate_sheet_data(worksheet_name)
    if worksheet_name:
        logger.info(f"시트 캐시 무효화: {worksheet_name}")
    else:
        logger.info("전체 시트 캐시 무효화")


def on_command_data_changed():
    """커스텀 명령어 등 명령어 관련 데이터 변경 시 호출"""
    bot_cache.invalidate_command_cache()
    logger.info("명령어 캐시 무효화")


def on_item_data_changed():
    """아이템 데이터 변경 시 호출"""
    invalidate_item_data()
    logger.info("아이템 데이터 캐시 무효화")


# 편의 함수들 섹션에 추가

def cache_today_fortune(user_id: str, fortune: str) -> bool:
    """특정 사용자의 오늘의 운세 캐싱"""
    return bot_cache.cache_today_fortune(user_id, fortune)

def get_today_fortune(user_id: str) -> Optional[str]:
    """특정 사용자의 오늘의 운세 조회"""
    return bot_cache.get_today_fortune(user_id)

def cache_shop_items(shop_items: List[Dict[str, Any]]) -> bool:
    """상점 아이템 목록 캐싱"""
    return bot_cache.cache_shop_items(shop_items)

def get_shop_items() -> Optional[List[Dict[str, Any]]]:
    """상점 아이템 목록 조회"""
    return bot_cache.get_shop_items()

def invalidate_today_fortune(user_id: str) -> bool:
    """특정 사용자의 오늘의 운세 캐시 무효화"""
    return bot_cache.invalidate_today_fortune(user_id)

def invalidate_shop_items() -> bool:
    """상점 아이템 캐시 무효화"""
    return bot_cache.invalidate_shop_items()


def cache_roster_data(roster_data: List[Dict[str, Any]]) -> bool:
    """명단 데이터 캐싱 (1시간 TTL)"""
    return bot_cache.cache_roster_data(roster_data)


def get_roster_data() -> Optional[List[Dict[str, Any]]]:
    """캐시된 명단 데이터 조회 (1시간 TTL)"""
    return bot_cache.get_roster_data()


def invalidate_roster_data() -> bool:
    """명단 데이터 캐시 무효화"""
    return bot_cache.invalidate_roster_data()


def get_roster_cache_info() -> Dict[str, Any]:
    """명단 캐시 상태 정보 반환"""
    return bot_cache.get_roster_cache_info()

# 기존 함수들 하단에 추가

def on_shop_data_changed():
    """상점 아이템 데이터 변경 시 호출"""
    invalidate_shop_items()
    logger.info("상점 아이템 캐시 무효화")

def on_fortune_data_changed():
    """운세 문구 데이터 변경 시 호출 (전체 운세 문구 변경 시)"""
    bot_cache.cache_fortune_phrases([])  # 빈 리스트로 초기화하여 재로드 유도
    logger.info("운세 문구 캐시 무효화")
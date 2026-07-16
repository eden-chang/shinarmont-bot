"""
마스토돈 예약 봇 캐시 관리 시스템
시트 데이터의 변경 감지, JSON 캐시 관리, 백업 시스템을 제공합니다.
"""

import os
import sys
import json
import hashlib
import shutil
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any, Union
from pathlib import Path
import pytz

# 프로젝트 루트 경로 설정
current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(current_dir)

try:
    from config.settings import config
    from utils.logging_config import get_logger, log_performance, LogContext
    from utils.datetime_utils import format_datetime_korean, default_parser
except ImportError as e:
    print(f"❌ 필수 모듈 임포트 실패: {e}")
    sys.exit(1)

logger = get_logger(__name__)


class CacheEntry:
    """
    캐시 엔트리를 나타내는 클래스
    개별 툿 데이터의 캐시 정보를 관리합니다.
    """
    
    def __init__(self, row_index: int, date_str: str, time_str: str, 
                 account: str, content: str, content_hash: str, 
                 scheduled_datetime: Optional[datetime] = None,
                 status: str = 'pending', scope: str = '로컬',
                 image_names: Optional[List[str]] = None):
        """
        CacheEntry 초기화
        
        Args:
            row_index: 시트에서의 행 번호
            date_str: 날짜 문자열 (원본)
            time_str: 시간 문자열 (원본)
            account: 계정 이름
            content: 툿 내용
            content_hash: 내용 해시값
            scheduled_datetime: 파싱된 예약 시간
            status: 상태 ('pending', 'posted', 'failed', 'skipped')
            scope: 범위 ('로컬' 또는 '디엠')
            image_names: 이미지 이름 목록 (IMAGE_SETTING=true일 때 사용)
        """
        self.row_index = row_index
        self.date_str = date_str
        self.time_str = time_str
        self.account = account
        self.content = content
        self.content_hash = content_hash
        self.scheduled_datetime = scheduled_datetime
        self.status = status
        self.scope = scope
        self.image_names = image_names if image_names is not None else []
        self.created_at = datetime.now(pytz.timezone('Asia/Seoul'))
        self.updated_at = self.created_at
        self.posted_at = None
        self.error_message = None
        self.retry_count = 0
    
    @classmethod
    def from_toot_data(cls, toot_data) -> 'CacheEntry':
        """
        TootData 객체로부터 CacheEntry 생성
        
        Args:
            toot_data: TootData 객체
        
        Returns:
            CacheEntry: 생성된 캐시 엔트리
        """
        image_names = getattr(toot_data, 'image_names', None) or []
        content_hash = cls.calculate_content_hash(
            toot_data.date_str, 
            toot_data.time_str, 
            toot_data.account,
            toot_data.content,
            image_names=image_names
        )
        
        return cls(
            row_index=toot_data.row_index,
            date_str=toot_data.date_str,
            time_str=toot_data.time_str,
            account=toot_data.account,
            content=toot_data.content,
            content_hash=content_hash,
            scheduled_datetime=toot_data.scheduled_datetime,
            scope=toot_data.scope,
            image_names=image_names
        )
    
    @staticmethod
    def calculate_content_hash(date_str: str, time_str: str, account: str, content: str,
                              image_names: Optional[List[str]] = None) -> str:
        """
        내용 해시값 계산 (이미지 목록 변경 시에도 갱신)
        
        Args:
            date_str: 날짜 문자열
            time_str: 시간 문자열
            account: 계정 이름
            content: 툿 내용
            image_names: 이미지 이름 목록 (선택)
        
        Returns:
            str: SHA256 해시값
        """
        names_part = ",".join(image_names) if image_names else ""
        combined = f"{date_str}|{time_str}|{account}|{content}|{names_part}"
        return hashlib.sha256(combined.encode('utf-8')).hexdigest()[:16]
    
    def update_status(self, status: str, error_message: Optional[str] = None) -> None:
        """
        상태 업데이트
        
        Args:
            status: 새로운 상태
            error_message: 오류 메시지 (있는 경우)
        """
        self.status = status
        self.error_message = error_message
        self.updated_at = datetime.now(pytz.timezone('Asia/Seoul'))
        
        if status == 'posted':
            self.posted_at = self.updated_at
        elif status == 'failed':
            self.retry_count += 1
    
    def get_visibility(self) -> str:
        """범위를 마스토돈 visibility로 변환"""
        if self.scope == '디엠':
            return 'direct'
        elif self.scope == '로컬':
            return 'unlisted'
        else:
            # 기본값은 로컬 (unlisted)
            return 'unlisted'
    
    def is_expired(self, current_time: Optional[datetime] = None) -> bool:
        """
        캐시 엔트리가 만료되었는지 확인
        
        Args:
            current_time: 현재 시간 (None이면 현재 시간 사용)
        
        Returns:
            bool: 만료 여부
        """
        if current_time is None:
            current_time = default_parser.get_current_datetime()
        
        # 이미 포스팅된 것은 만료된 것으로 간주
        if self.status == 'posted':
            return True
        
        # 예약 시간이 과거면 만료
        if self.scheduled_datetime and self.scheduled_datetime < current_time:
            return True
        
        return False
    
    def can_retry(self, max_retries: int = 3) -> bool:
        """
        재시도 가능 여부 확인
        
        Args:
            max_retries: 최대 재시도 횟수
        
        Returns:
            bool: 재시도 가능 여부
        """
        return self.status == 'failed' and self.retry_count < max_retries
    
    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리로 변환"""
        return {
            'row_index': self.row_index,
            'date_str': self.date_str,
            'time_str': self.time_str,
            'account': self.account,
            'content': self.content,
            'content_hash': self.content_hash,
            'scheduled_datetime': self.scheduled_datetime.isoformat() if self.scheduled_datetime else None,
            'status': self.status,
            'scope': self.scope,
            'image_names': self.image_names,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat(),
            'posted_at': self.posted_at.isoformat() if self.posted_at else None,
            'error_message': self.error_message,
            'retry_count': self.retry_count
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'CacheEntry':
        """딕셔너리로부터 객체 생성"""
        entry = cls(
            row_index=data['row_index'],
            date_str=data['date_str'],
            time_str=data['time_str'],
            account=data.get('account', ''),  # 하위 호환성을 위해 기본값 제공
            content=data['content'],
            content_hash=data['content_hash'],
            status=data.get('status', 'pending'),
            scope=data.get('scope', '로컬'),  # 하위 호환성을 위해 기본값 제공
            image_names=data.get('image_names', [])
        )
        
        # 시간 정보 복원
        if data.get('scheduled_datetime'):
            entry.scheduled_datetime = datetime.fromisoformat(data['scheduled_datetime'])
        
        if data.get('created_at'):
            entry.created_at = datetime.fromisoformat(data['created_at'])
        
        if data.get('updated_at'):
            entry.updated_at = datetime.fromisoformat(data['updated_at'])
        
        if data.get('posted_at'):
            entry.posted_at = datetime.fromisoformat(data['posted_at'])
        
        entry.error_message = data.get('error_message')
        entry.retry_count = data.get('retry_count', 0)
        
        return entry
    
    def get_cache_key(self) -> str:
        """캐시 키 생성 (행번호 + 해시)"""
        return f"row_{self.row_index}_{self.content_hash}"
    
    def __str__(self) -> str:
        """문자열 표현"""
        time_str = format_datetime_korean(self.scheduled_datetime) if self.scheduled_datetime else "시간 미정"
        return f"[행{self.row_index}] {self.account} | {time_str} | {self.status} | {self.content[:30]}..."


def format_time_until(target_time: Optional[datetime], current_time: Optional[datetime] = None) -> str:
    """
    두 시간 사이의 남은 시간을 사람이 읽기 쉬운 문자열로 반환합니다.
    """
    if target_time is None:
        return "시간 미정"
    if current_time is None:
        current_time = datetime.now(pytz.timezone('Asia/Seoul'))
    delta = target_time - current_time
    if delta.total_seconds() < 0:
        return "지남"
    days = delta.days
    hours, remainder = divmod(delta.seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    parts = []
    if days > 0:
        parts.append(f"{days}일")
    if hours > 0:
        parts.append(f"{hours}시간")
    if minutes > 0:
        parts.append(f"{minutes}분")
    if not parts:
        return "곧"
    return " ".join(parts) + " 남음"

class CacheManager:
    """
    캐시 관리 시스템
    시트 데이터의 변경 감지 및 JSON 캐시를 관리합니다.
    """
    
    def __init__(self, cache_file_path: Optional[Path] = None,
                 backup_dir_path: Optional[Path] = None):
        """
        CacheManager 초기화
        
        Args:
            cache_file_path: 캐시 파일 경로
            backup_dir_path: 백업 디렉토리 경로
        """
        # 경로 설정
        self.cache_file_path = cache_file_path or config.get_cache_file_path()
        self.backup_dir_path = backup_dir_path or config.get_backup_dir_path()
        
        # 캐시 데이터
        self.cache_entries: Dict[str, CacheEntry] = {}
        self.metadata = {
            'version': '1.0',
            'created_at': None,
            'last_updated': None,
            'last_sync_time': None,
            'sync_count': 0,
            'total_entries': 0,
            'pending_entries': 0,
            'posted_entries': 0,
            'failed_entries': 0
        }
        
        # 설정
        self.backup_retention_days = getattr(config, 'CACHE_BACKUP_RETENTION_DAYS', 30)
        self.auto_cleanup_enabled = getattr(config, 'CACHE_AUTO_CLEANUP', True)
        self.max_retry_attempts = getattr(config, 'MAX_RETRY_ATTEMPTS', 3)
        
        # 통계
        self.stats = {
            'cache_hits': 0,
            'cache_misses': 0,
            'cache_updates': 0,
            'backup_count': 0,
            'cleanup_count': 0,
            'load_count': 0,
            'save_count': 0
        }
        
        logger.info(f"캐시 매니저 초기화: {self.cache_file_path}")
        
        # 초기 로드
        self.load_cache()
    
    @log_performance
    def load_cache(self) -> bool:
        """
        캐시 파일 로드
        
        Returns:
            bool: 로드 성공 여부
        """
        try:
            if not self.cache_file_path.exists():
                logger.info("캐시 파일이 없습니다. 새로 생성합니다.")
                self._initialize_empty_cache()
                return True
            
            with open(self.cache_file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # 메타데이터 로드
            self.metadata.update(data.get('metadata', {}))
            
            # 캐시 엔트리 로드
            entries_data = data.get('entries', {})
            self.cache_entries = {}
            
            for key, entry_data in entries_data.items():
                try:
                    entry = CacheEntry.from_dict(entry_data)
                    self.cache_entries[key] = entry
                except Exception as e:
                    logger.warning(f"캐시 엔트리 로드 실패 (키: {key}): {e}")
            
            self._update_metadata_stats()
            self.stats['load_count'] += 1
            
            logger.info(f"캐시 로드 완료: {len(self.cache_entries)}개 엔트리")
            return True
            
        except json.JSONDecodeError as e:
            logger.error(f"캐시 파일 JSON 파싱 오류: {e}")
            return self._handle_corrupted_cache()
        except Exception as e:
            logger.error(f"캐시 로드 실패: {e}")
            return self._handle_corrupted_cache()
    
    def _handle_corrupted_cache(self) -> bool:
        """손상된 캐시 파일 처리"""
        logger.warning("손상된 캐시 파일을 백업하고 새로 생성합니다.")
        
        try:
            # 손상된 파일 백업
            if self.cache_file_path.exists():
                corrupt_backup = self.backup_dir_path / f"corrupted_cache_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
                shutil.copy2(self.cache_file_path, corrupt_backup)
                logger.info(f"손상된 캐시 파일 백업: {corrupt_backup}")
            
            # 새로운 캐시 초기화
            self._initialize_empty_cache()
            return True
            
        except Exception as e:
            logger.error(f"손상된 캐시 파일 처리 실패: {e}")
            return False
    
    def _initialize_empty_cache(self) -> None:
        """빈 캐시 초기화"""
        self.cache_entries = {}
        self.metadata = {
            'version': '1.0',
            'created_at': datetime.now(pytz.timezone('Asia/Seoul')).isoformat(),
            'last_updated': None,
            'last_sync_time': None,
            'sync_count': 0,
            'total_entries': 0,
            'pending_entries': 0,
            'posted_entries': 0,
            'failed_entries': 0
        }
        self.save_cache()

    def clear_cache(self) -> bool:
        """
        캐시 파일과 백업 디렉토리를 삭제합니다.
        """
        try:
            if self.cache_file_path.exists():
                os.remove(self.cache_file_path)
                logger.info(f"✅ 캐시 파일 삭제: {self.cache_file_path}")
            
            # 백업 디렉토리도 정리하려면 아래 주석 해제
            # if self.backup_dir_path.exists() and self.backup_dir_path.is_dir():
            #     shutil.rmtree(self.backup_dir_path)
            #     logger.info(f"✅ 백업 디렉토리 삭제: {self.backup_dir_path}")

            self._initialize_empty_cache() # 빈 캐시로 초기화하여 메모리 상태도 정리
            return True
        except Exception as e:
            logger.error(f"❌ 캐시 파일 삭제 실패: {e}")
            return False
    
    @log_performance
    def save_cache(self, create_backup: bool = True) -> bool:
        """
        캐시 파일 저장
        
        Args:
            create_backup: 백업 생성 여부
        
        Returns:
            bool: 저장 성공 여부
        """
        try:
            with LogContext("캐시 저장") as ctx:
                
                if create_backup and self.cache_file_path.exists():
                    ctx.log_step("기존 캐시 백업")
                    self._create_backup()
                
                ctx.log_step("메타데이터 업데이트")
                self._update_metadata_stats()
                self.metadata['last_updated'] = datetime.now(pytz.timezone('Asia/Seoul')).isoformat()
                
                ctx.log_step("JSON 데이터 준비")
                # 캐시 엔트리를 딕셔너리로 변환
                entries_data = {}
                for key, entry in self.cache_entries.items():
                    entries_data[key] = entry.to_dict()
                
                # 전체 데이터 구성
                cache_data = {
                    'metadata': self.metadata,
                    'entries': entries_data
                }
                
                ctx.log_step("파일 쓰기")
                # 임시 파일에 먼저 쓰고 원자적으로 이동
                temp_file = self.cache_file_path.with_suffix('.tmp')
                
                with open(temp_file, 'w', encoding='utf-8') as f:
                    json.dump(cache_data, f, ensure_ascii=False, indent=2)
                
                # 원자적 이동
                temp_file.replace(self.cache_file_path)
                
                self.stats['save_count'] += 1
                logger.debug(f"캐시 저장 완료: {len(self.cache_entries)}개 엔트리")
                return True
                
        except Exception as e:
            logger.error(f"캐시 저장 실패: {e}")
            return False
    
    def _create_backup(self) -> bool:
        """캐시 백업 생성"""
        try:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            backup_file = self.backup_dir_path / f"cache_backup_{timestamp}.json"
            
            shutil.copy2(self.cache_file_path, backup_file)
            self.stats['backup_count'] += 1
            
            logger.debug(f"캐시 백업 생성: {backup_file}")
            
            # 오래된 백업 정리
            if self.auto_cleanup_enabled:
                self._cleanup_old_backups()
            
            return True
            
        except Exception as e:
            logger.error(f"캐시 백업 생성 실패: {e}")
            return False
    
    def _cleanup_old_backups(self) -> None:
        """오래된 백업 파일 정리"""
        try:
            cutoff_date = datetime.now() - timedelta(days=self.backup_retention_days)
            
            for backup_file in self.backup_dir_path.glob("cache_backup_*.json"):
                if backup_file.stat().st_mtime < cutoff_date.timestamp():
                    backup_file.unlink()
                    self.stats['cleanup_count'] += 1
                    logger.debug(f"오래된 백업 삭제: {backup_file}")
                    
        except Exception as e:
            logger.error(f"백업 정리 실패: {e}")
    
    def sync_with_sheet_data(self, toot_data_list: List) -> Tuple[bool, Dict[str, Any]]:
        """
        시트 데이터와 캐시 동기화
        
        Args:
            toot_data_list: 시트에서 조회한 TootData 목록
        
        Returns:
            Tuple[bool, Dict[str, Any]]: (변경 여부, 변경 통계)
        """
        with LogContext("캐시 동기화") as ctx:
            ctx.log_step("변경 감지 시작")
            
            # 현재 시트 데이터를 캐시 엔트리로 변환
            new_entries = {}
            for toot_data in toot_data_list:
                if toot_data.is_valid:  # 유효한 데이터만 캐시
                    entry = CacheEntry.from_toot_data(toot_data)
                    cache_key = entry.get_cache_key()
                    new_entries[cache_key] = entry
            
            ctx.log_step("변경사항 분석")
            
            # 변경 통계
            changes = {
                'added': [],      # 새로 추가된 엔트리
                'updated': [],    # 내용이 변경된 엔트리
                'removed': [],    # 제거된 엔트리
                'unchanged': []   # 변경되지 않은 엔트리
            }
            
            # 기존 캐시와 비교
            existing_keys = set(self.cache_entries.keys())
            new_keys = set(new_entries.keys())
            
            # 새로 추가된 엔트리
            for key in new_keys - existing_keys:
                new_entry = new_entries[key]
                self.cache_entries[key] = new_entry
                changes['added'].append(new_entry)
                self.stats['cache_misses'] += 1
            
            # 제거된 엔트리 (더 이상 시트에 없음)
            for key in existing_keys - new_keys:
                removed_entry = self.cache_entries[key]
                # 아직 포스팅되지 않은 것만 제거 (포스팅된 것은 기록 보존)
                if removed_entry.status != 'posted':
                    changes['removed'].append(removed_entry)
                    del self.cache_entries[key]
            
            # 공통 엔트리 - 내용 변경 확인
            for key in existing_keys & new_keys:
                existing_entry = self.cache_entries[key]
                new_entry = new_entries[key]
                
                # 내용 해시 비교
                if existing_entry.content_hash != new_entry.content_hash:
                    # 내용이 변경됨 - 기존 상태는 유지하되 내용만 업데이트
                    existing_entry.content = new_entry.content
                    existing_entry.content_hash = new_entry.content_hash
                    existing_entry.date_str = new_entry.date_str
                    existing_entry.time_str = new_entry.time_str
                    existing_entry.scheduled_datetime = new_entry.scheduled_datetime
                    existing_entry.image_names = new_entry.image_names
                    existing_entry.updated_at = datetime.now(pytz.timezone('Asia/Seoul'))
                    
                    # 이미 포스팅된 것이 변경되면 경고
                    if existing_entry.status == 'posted':
                        logger.warning(f"이미 포스팅된 툿이 변경되었습니다: 행 {existing_entry.row_index}")
                    
                    changes['updated'].append(existing_entry)
                    self.stats['cache_updates'] += 1
                else:
                    changes['unchanged'].append(existing_entry)
                    self.stats['cache_hits'] += 1
            
            ctx.log_step("동기화 완료")
            
            # 변경이 있었는지 확인
            has_changes = bool(changes['added'] or changes['updated'] or changes['removed'])
            
            if has_changes:
                # 메타데이터 업데이트
                self.metadata['last_sync_time'] = datetime.now(pytz.timezone('Asia/Seoul')).isoformat()
                self.metadata['sync_count'] += 1
                
                # 캐시 저장
                self.save_cache()
                
                logger.info(f"캐시 동기화 완료: 추가 {len(changes['added'])}개, "
                           f"수정 {len(changes['updated'])}개, 삭제 {len(changes['removed'])}개")
            else:
                logger.debug("캐시 동기화: 변경사항 없음")
            
            return has_changes, changes
    
    def get_pending_entries(self, current_time: Optional[datetime] = None) -> List[CacheEntry]:
        """
        대기 중인 엔트리 목록 반환
        
        Args:
            current_time: 현재 시간 (None이면 현재 시간 사용)
        
        Returns:
            List[CacheEntry]: 대기 중인 엔트리 목록 (시간순 정렬)
        """
        if current_time is None:
            current_time = default_parser.get_current_datetime()
        
        pending_entries = [
            entry for entry in self.cache_entries.values()
            if entry.status == 'pending' and 
               entry.scheduled_datetime and 
               entry.scheduled_datetime > current_time
        ]
        
        # 예약 시간순 정렬
        pending_entries.sort(key=lambda e: e.scheduled_datetime)
        
        return pending_entries
    
    def get_due_entries(self, current_time: Optional[datetime] = None,
                       buffer_minutes: int = 1) -> List[CacheEntry]:
        """
        실행 시간이 된 엔트리 목록 반환
        
        Args:
            current_time: 현재 시간
            buffer_minutes: 버퍼 시간 (분) - 이 시간만큼 일찍 실행
        
        Returns:
            List[CacheEntry]: 실행할 엔트리 목록
        """
        if current_time is None:
            current_time = default_parser.get_current_datetime()
        
        # 버퍼 시간 적용
        execution_time = current_time + timedelta(minutes=buffer_minutes)
        
        due_entries = [
            entry for entry in self.cache_entries.values()
            if entry.status == 'pending' and 
               entry.scheduled_datetime and 
               entry.scheduled_datetime <= execution_time
        ]
        
        # 예약 시간순 정렬
        due_entries.sort(key=lambda e: e.scheduled_datetime)
        
        return due_entries
    
    def get_retry_candidates(self, current_time: Optional[datetime] = None,
                            retry_delay_minutes: int = 30) -> List[CacheEntry]:
        """
        재시도 가능한 실패 엔트리 목록 반환
        
        Args:
            current_time: 현재 시간
            retry_delay_minutes: 재시도 대기 시간 (분)
        
        Returns:
            List[CacheEntry]: 재시도 가능한 엔트리 목록
        """
        if current_time is None:
            current_time = default_parser.get_current_datetime()
        
        retry_cutoff = current_time - timedelta(minutes=retry_delay_minutes)
        
        retry_entries = [
            entry for entry in self.cache_entries.values()
            if entry.can_retry(self.max_retry_attempts) and
               entry.updated_at < retry_cutoff
        ]
        
        # 업데이트 시간순 정렬 (오래된 것부터)
        retry_entries.sort(key=lambda e: e.updated_at)
        
        return retry_entries
    
    def update_entry_status(self, cache_key: str, status: str, 
                           error_message: Optional[str] = None) -> bool:
        """
        엔트리 상태 업데이트
        
        Args:
            cache_key: 캐시 키
            status: 새로운 상태
            error_message: 오류 메시지 (있는 경우)
        
        Returns:
            bool: 업데이트 성공 여부
        """
        if cache_key not in self.cache_entries:
            logger.error(f"캐시 키를 찾을 수 없습니다: {cache_key}")
            return False
        
        entry = self.cache_entries[cache_key]
        old_status = entry.status
        
        entry.update_status(status, error_message)
        
        logger.debug(f"엔트리 상태 업데이트: {cache_key} | {old_status} -> {status}")
        
        # 주요 상태 변경은 즉시 저장
        if status in ['posted', 'failed']:
            self.save_cache(create_backup=False)
        
        return True
    
    def cleanup_expired_entries(self, current_time: Optional[datetime] = None) -> int:
        """
        만료된 엔트리 정리
        
        Args:
            current_time: 현재 시간
        
        Returns:
            int: 정리된 엔트리 수
        """
        if current_time is None:
            current_time = default_parser.get_current_datetime()
        
        expired_keys = []
        
        for key, entry in self.cache_entries.items():
            if entry.is_expired(current_time):
                expired_keys.append(key)
        
        # 만료된 엔트리 제거
        for key in expired_keys:
            del self.cache_entries[key]
        
        if expired_keys:
            self.save_cache(create_backup=False)
            logger.info(f"만료된 캐시 엔트리 {len(expired_keys)}개 정리 완료")
        
        return len(expired_keys)
    
    def _update_metadata_stats(self) -> None:
        """메타데이터 통계 업데이트"""
        status_counts = {'pending': 0, 'posted': 0, 'failed': 0, 'skipped': 0}
        
        for entry in self.cache_entries.values():
            status = entry.status
            if status in status_counts:
                status_counts[status] += 1
        
        self.metadata['total_entries'] = len(self.cache_entries)
        self.metadata['pending_entries'] = status_counts['pending']
        self.metadata['posted_entries'] = status_counts['posted']
        self.metadata['failed_entries'] = status_counts['failed']
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """캐시 통계 정보 반환"""
        current_time = default_parser.get_current_datetime()
        
        # 기본 통계
        stats = self.stats.copy()
        stats.update(self.metadata)
        
        # 실시간 통계
        pending_entries = self.get_pending_entries(current_time)
        due_entries = self.get_due_entries(current_time)
        retry_entries = self.get_retry_candidates(current_time)
        
        stats.update({
            'current_pending': len(pending_entries),
            'current_due': len(due_entries),
            'current_retry_candidates': len(retry_entries),
            'cache_hit_rate': (stats['cache_hits'] / max(stats['cache_hits'] + stats['cache_misses'], 1)) * 100,
            'cache_file_size': self.cache_file_path.stat().st_size if self.cache_file_path.exists() else 0,
            'backup_count': len(list(self.backup_dir_path.glob("cache_backup_*.json")))
        })
        
        return stats
    
    def export_cache_summary(self) -> Dict[str, Any]:
        """캐시 요약 정보 내보내기 (디버깅용)"""
        summary = {
            'metadata': self.metadata,
            'statistics': self.get_cache_stats(),
            'entries_by_status': {},
            'upcoming_entries': [],
            'recent_failures': []
        }
        
        # 상태별 엔트리 그룹화
        for status in ['pending', 'posted', 'failed', 'skipped']:
            entries_with_status = [
                {
                    'row_index': entry.row_index,
                    'scheduled_datetime': entry.scheduled_datetime.isoformat() if entry.scheduled_datetime else None,
                    'content_preview': entry.content[:50] + '...' if len(entry.content) > 50 else entry.content,
                    'updated_at': entry.updated_at.isoformat(),
                    'retry_count': entry.retry_count,
                    'error_message': entry.error_message
                }
                for entry in self.cache_entries.values()
                if entry.status == status
            ]
            summary['entries_by_status'][status] = entries_with_status
        
        # 다가오는 예약 (향후 24시간)
        current_time = default_parser.get_current_datetime()
        next_24h = current_time + timedelta(hours=24)
        
        upcoming = [
            {
                'row_index': entry.row_index,
                'scheduled_datetime': entry.scheduled_datetime.isoformat(),
                'content_preview': entry.content[:50] + '...' if len(entry.content) > 50 else entry.content,
                'time_until': format_time_until(entry.scheduled_datetime, current_time)
            }
            for entry in self.cache_entries.values()
            if (entry.status == 'pending' and 
                entry.scheduled_datetime and 
                current_time < entry.scheduled_datetime <= next_24h)
        ]
        upcoming.sort(key=lambda x: x['scheduled_datetime'])
        summary['upcoming_entries'] = upcoming[:10]  # 최대 10개
        
        # 최근 실패 (지난 24시간)
        past_24h = current_time - timedelta(hours=24)
        
        recent_failures = [
            {
                'row_index': entry.row_index,
                'scheduled_datetime': entry.scheduled_datetime.isoformat() if entry.scheduled_datetime else None,
                'content_preview': entry.content[:50] + '...' if len(entry.content) > 50 else entry.content,
                'error_message': entry.error_message,
                'retry_count': entry.retry_count,
                'updated_at': entry.updated_at.isoformat()
            }
            for entry in self.cache_entries.values()
            if (entry.status == 'failed' and 
                entry.updated_at >= past_24h)
        ]
        recent_failures.sort(key=lambda x: x['updated_at'], reverse=True)
        summary['recent_failures'] = recent_failures[:10]  # 최대 10개
        
        return summary
    
    def __str__(self) -> str:
        """문자열 표현"""
        return f"CacheManager({len(self.cache_entries)} entries, {self.cache_file_path})"


# 전역 캐시 매니저 인스턴스
_cache_manager: Optional[CacheManager] = None


def get_cache_manager() -> CacheManager:
    """전역 캐시 매니저 반환"""
    global _cache_manager
    
    if _cache_manager is None:
        _cache_manager = CacheManager()
    
    return _cache_manager


def test_cache_system() -> bool:
    """캐시 시스템 테스트"""
    try:
        logger.info("캐시 시스템 테스트 시작...")
        
        # 테스트용 캐시 매니저 생성
        import tempfile
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_cache = Path(temp_dir) / "test_cache.json"
            temp_backup = Path(temp_dir) / "backup"
            temp_backup.mkdir()
            
            cache_mgr = CacheManager(temp_cache, temp_backup)
            
            # 테스트 데이터 생성
            from datetime import datetime
            import pytz
            
            test_entries = []
            for i in range(3):
                entry = CacheEntry(
                    row_index=i + 2,
                    date_str="내일",
                    time_str=f"{14 + i}:00",
                    account="NOTICE",
                    content=f"테스트 툿 {i + 1}번입니다.",
                    content_hash=CacheEntry.calculate_content_hash("내일", f"{14 + i}:00", "NOTICE", f"테스트 툿 {i + 1}번입니다."),
                    scheduled_datetime=datetime.now(pytz.timezone('Asia/Seoul')) + timedelta(hours=i + 1),
                    scope="로컬"
                )
                test_entries.append(entry)
                cache_mgr.cache_entries[entry.get_cache_key()] = entry
            
            # 저장 테스트
            if not cache_mgr.save_cache():
                logger.error("캐시 저장 실패")
                return False
            
            # 로드 테스트
            cache_mgr2 = CacheManager(temp_cache, temp_backup)
            if len(cache_mgr2.cache_entries) != 3:
                logger.error("캐시 로드 실패")
                return False
            
            # 상태 업데이트 테스트
            first_key = list(cache_mgr2.cache_entries.keys())[0]
            cache_mgr2.update_entry_status(first_key, 'posted')
            
            # 통계 테스트
            stats = cache_mgr2.get_cache_stats()
            logger.info(f"테스트 통계: {stats['total_entries']}개 엔트리")
            
            logger.info("✅ 캐시 시스템 테스트 성공")
            return True
            
    except Exception as e:
        logger.error(f"❌ 캐시 시스템 테스트 실패: {e}")
        return False


if __name__ == "__main__":
    """캐시 매니저 테스트"""
    print("🧪 캐시 매니저 테스트 시작...")
    
    try:
        # 캐시 매니저 초기화
        cache_manager = CacheManager()
        
        # 캐시 로드 테스트
        print("📁 캐시 로드 테스트...")
        if cache_manager.load_cache():
            print("✅ 캐시 로드 성공")
        else:
            print("❌ 캐시 로드 실패")
        
        # 통계 정보
        print("📊 캐시 통계:")
        stats = cache_manager.get_cache_stats()
        print(f"  총 엔트리: {stats['total_entries']}개")
        print(f"  대기중: {stats['pending_entries']}개")
        print(f"  완료: {stats['posted_entries']}개")
        print(f"  실패: {stats['failed_entries']}개")
        print(f"  캐시 적중률: {stats.get('cache_hit_rate', 0):.1f}%")
        
        # 캐시 요약
        print("📋 캐시 요약:")
        summary = cache_manager.export_cache_summary()
        
        for status, entries in summary['entries_by_status'].items():
            if entries:
                print(f"  {status}: {len(entries)}개")
                for entry in entries[:3]:  # 처음 3개만 출력
                    print(f"    - 행{entry['row_index']}: {entry['content_preview']}")
        
        if summary['upcoming_entries']:
            print("  다가오는 예약:")
            for entry in summary['upcoming_entries'][:3]:
                print(f"    - {entry['time_until']}: {entry['content_preview']}")
        
        # 시스템 테스트
        print("🔧 시스템 테스트...")
        if test_cache_system():
            print("✅ 시스템 테스트 성공")
        else:
            print("❌ 시스템 테스트 실패")
        
        print("✅ 캐시 매니저 테스트 완료!")
        
    except Exception as e:
        print(f"❌ 테스트 실패: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
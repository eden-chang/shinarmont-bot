"""
마스토돈 예약 봇 스케줄러
20분 간격 시트 동기화와 예약 툿 실행을 관리합니다.
"""

import os
import sys
import time
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any, Callable
import pytz
from concurrent.futures import ThreadPoolExecutor, Future
import signal

# 스케줄링 라이브러리
try:
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.interval import IntervalTrigger
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR
    from apscheduler.executors.pool import ThreadPoolExecutor as APSThreadPoolExecutor
except ImportError:
    print("❌ APScheduler 라이브러리가 설치되지 않았습니다.")
    print("pip install APScheduler 를 실행하세요.")
    sys.exit(1)

# 프로젝트 루트 경로 설정
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(current_dir)

try:
    from config.settings import config
    from utils.logging_config import get_logger, LogContext, log_performance
    from utils.datetime_utils import (
        is_sync_time, get_next_sync_time, format_datetime_korean, 
        format_time_until, default_parser
    )
    from core.sheets_client import get_sheets_manager
    from core.mastodon_client import get_mastodon_manager, send_system_notification
    from core.cache_manager import get_cache_manager, CacheEntry
except ImportError as e:
    print(f"❌ 필수 모듈 임포트 실패: {e}")
    sys.exit(1)

logger = get_logger(__name__)


class SchedulerStats:
    """
    스케줄러 통계를 관리하는 클래스
    """
    
    def __init__(self):
        """SchedulerStats 초기화"""
        self.start_time = datetime.now(pytz.timezone('Asia/Seoul'))
        self.stats = {
            'sync_cycles': 0,
            'successful_syncs': 0,
            'failed_syncs': 0,
            'total_toots_posted': 0,
            'successful_posts': 0,
            'failed_posts': 0,
            'last_sync_time': None,
            'last_sync_duration': 0,
            'last_post_time': None,
            'average_sync_duration': 0,
            'longest_sync_duration': 0,
            'sync_durations': [],
            'errors': []
        }
        self.lock = threading.Lock()
    
    def record_sync_start(self) -> datetime:
        """동기화 시작 기록"""
        with self.lock:
            self.stats['sync_cycles'] += 1
            return datetime.now(pytz.timezone('Asia/Seoul'))
    
    def record_sync_end(self, start_time: datetime, success: bool, error: Optional[str] = None):
        """동기화 종료 기록"""
        end_time = datetime.now(pytz.timezone('Asia/Seoul'))
        duration = (end_time - start_time).total_seconds()
        
        with self.lock:
            if success:
                self.stats['successful_syncs'] += 1
            else:
                self.stats['failed_syncs'] += 1
                if error:
                    self.stats['errors'].append({
                        'timestamp': end_time.isoformat(),
                        'type': 'sync_error',
                        'message': error
                    })
            
            self.stats['last_sync_time'] = end_time.isoformat()
            self.stats['last_sync_duration'] = duration
            
            # 동기화 시간 통계 업데이트
            self.stats['sync_durations'].append(duration)
            if len(self.stats['sync_durations']) > 100:  # 최근 100개만 유지
                self.stats['sync_durations'] = self.stats['sync_durations'][-100:]
            
            self.stats['average_sync_duration'] = sum(self.stats['sync_durations']) / len(self.stats['sync_durations'])
            self.stats['longest_sync_duration'] = max(self.stats['sync_durations'])
    
    def record_toot_attempt(self, success: bool, error: Optional[str] = None):
        """툿 포스팅 시도 기록"""
        with self.lock:
            self.stats['total_toots_posted'] += 1
            
            if success:
                self.stats['successful_posts'] += 1
                self.stats['last_post_time'] = datetime.now(pytz.timezone('Asia/Seoul')).isoformat()
            else:
                self.stats['failed_posts'] += 1
                if error:
                    self.stats['errors'].append({
                        'timestamp': datetime.now(pytz.timezone('Asia/Seoul')).isoformat(),
                        'type': 'post_error',
                        'message': error
                    })
    
    def get_stats(self) -> Dict[str, Any]:
        """통계 정보 반환"""
        with self.lock:
            current_time = datetime.now(pytz.timezone('Asia/Seoul'))
            uptime = current_time - self.start_time
            
            stats = self.stats.copy()
            stats.update({
                'start_time': self.start_time.isoformat(),
                'current_time': current_time.isoformat(),
                'uptime_seconds': uptime.total_seconds(),
                'uptime_formatted': self._format_uptime(uptime),
                'success_rate': self._calculate_success_rate(),
                'post_success_rate': self._calculate_post_success_rate(),
                'recent_errors': self.stats['errors'][-10:] if self.stats['errors'] else []
            })
            
            return stats
    
    def _format_uptime(self, uptime: timedelta) -> str:
        """가동 시간 포맷팅"""
        total_seconds = int(uptime.total_seconds())
        days = total_seconds // 86400
        hours = (total_seconds % 86400) // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        
        if days > 0:
            return f"{days}일 {hours}시간 {minutes}분 {seconds}초"
        elif hours > 0:
            return f"{hours}시간 {minutes}분 {seconds}초"
        elif minutes > 0:
            return f"{minutes}분 {seconds}초"
        else:
            return f"{seconds}초"
    
    def _calculate_success_rate(self) -> float:
        """동기화 성공률 계산"""
        total = self.stats['successful_syncs'] + self.stats['failed_syncs']
        if total == 0:
            return 100.0
        return (self.stats['successful_syncs'] / total) * 100
    
    def _calculate_post_success_rate(self) -> float:
        """포스팅 성공률 계산"""
        total = self.stats['successful_posts'] + self.stats['failed_posts']
        if total == 0:
            return 100.0
        return (self.stats['successful_posts'] / total) * 100


class TootScheduler:
    """
    마스토돈 예약 툿 스케줄러
    20분 간격으로 시트를 동기화하고 예약된 툿을 실행합니다.
    """
    
    def __init__(self):
        """TootScheduler 초기화"""
        # 의존성
        self.sheets_manager = None
        self.mastodon_manager = None
        self.cache_manager = None
        
        # 스케줄러
        self.scheduler = None
        self.background_scheduler = None
        
        # 설정
        self.sync_interval_minutes = getattr(config, 'SYNC_INTERVAL_MINUTES', 20)
        self.timezone = getattr(config, 'TIMEZONE', pytz.timezone('Asia/Seoul'))
        self.max_concurrent_posts = getattr(config, 'MAX_CONCURRENT_POSTS', 3)
        self.post_retry_delay = getattr(config, 'POST_RETRY_DELAY_MINUTES', 30)
        
        # 상태
        self.is_running = False
        self.stats = SchedulerStats()
        self.last_health_check = None
        self.shutdown_event = threading.Event()
        
        # 스레드 풀
        self.executor = ThreadPoolExecutor(max_workers=self.max_concurrent_posts)
        
        logger.info(f"툿 스케줄러 초기화: {self.sync_interval_minutes}분 간격")
    
    def initialize_dependencies(self) -> bool:
        """의존성 초기화"""
        try:
            with LogContext("의존성 초기화") as ctx:
                ctx.log_step("시트 매니저 초기화")
                self.sheets_manager = get_sheets_manager()
                
                ctx.log_step("마스토돈 매니저 초기화")
                self.mastodon_manager = get_mastodon_manager()
                
                ctx.log_step("캐시 매니저 초기화")
                self.cache_manager = get_cache_manager()
                
                logger.info("✅ 모든 의존성 초기화 완료")
                return True
                
        except Exception as e:
            logger.error(f"❌ 의존성 초기화 실패: {e}")
            return False
    
    def setup_scheduler(self, use_background: bool = False) -> bool:
        """
        스케줄러 설정
        
        Args:
            use_background: 백그라운드 스케줄러 사용 여부
        
        Returns:
            bool: 설정 성공 여부
        """
        try:
            # 스케줄러 타입 선택
            if use_background:
                self.background_scheduler = BackgroundScheduler(
                    timezone=self.timezone,
                    executors={'default': APSThreadPoolExecutor(max_workers=5)}
                )
                scheduler = self.background_scheduler
            else:
                self.scheduler = BlockingScheduler(
                    timezone=self.timezone,
                    executors={'default': APSThreadPoolExecutor(max_workers=5)}
                )
                scheduler = self.scheduler
            
            # 이벤트 리스너 추가
            scheduler.add_listener(self._job_executed, EVENT_JOB_EXECUTED)
            scheduler.add_listener(self._job_error, EVENT_JOB_ERROR)
            
            # 주요 작업 스케줄링
            self._schedule_sync_job(scheduler)
            self._schedule_toot_execution_job(scheduler)
            self._schedule_maintenance_jobs(scheduler)
            
            logger.info("✅ 스케줄러 설정 완료")
            return True
            
        except Exception as e:
            logger.error(f"❌ 스케줄러 설정 실패: {e}")
            return False
    
    def _schedule_sync_job(self, scheduler):
        """시트 동기화 작업 스케줄링"""
        # 20분 간격 동기화 (0분, 20분, 40분)
        sync_trigger = CronTrigger(
            minute='0,20,40',
            timezone=self.timezone
        )
        
        scheduler.add_job(
            func=self.sync_with_sheets,
            trigger=sync_trigger,
            id='sync_sheets',
            name='시트 동기화',
            max_instances=1,
            replace_existing=True
        )
        
        logger.info("📊 시트 동기화 작업 스케줄링 완료 (0, 20, 40분)")
    
    def _schedule_toot_execution_job(self, scheduler):
        """툿 실행 작업 스케줄링"""
        # 매분 실행할 툿 확인
        execution_trigger = CronTrigger(
            second='0',  # 매분 0초에 실행
            timezone=self.timezone
        )
        
        scheduler.add_job(
            func=self.execute_scheduled_toots,
            trigger=execution_trigger,
            id='execute_toots',
            name='예약 툿 실행',
            max_instances=3,  # 동시 실행 가능
            replace_existing=True
        )
        
        logger.info("⏰ 툿 실행 작업 스케줄링 완료 (매분)")
    
    def _schedule_maintenance_jobs(self, scheduler):
        """유지보수 작업 스케줄링"""
        # 매시간 헬스체크
        health_trigger = CronTrigger(
            minute='5',  # 매시간 5분에 실행
            timezone=self.timezone
        )
        
        scheduler.add_job(
            func=self.health_check,
            trigger=health_trigger,
            id='health_check',
            name='시스템 헬스체크',
            max_instances=1,
            replace_existing=True
        )
        
        # 일일 정리 작업
        cleanup_trigger = CronTrigger(
            hour='3',  # 매일 새벽 3시
            minute='0',
            timezone=self.timezone
        )
        
        scheduler.add_job(
            func=self.daily_cleanup,
            trigger=cleanup_trigger,
            id='daily_cleanup',
            name='일일 정리',
            max_instances=1,
            replace_existing=True
        )
        
        logger.info("🔧 유지보수 작업 스케줄링 완료")
    
    @log_performance
    def sync_with_sheets(self) -> bool:
        """
        시트와 동기화 수행
        
        Returns:
            bool: 동기화 성공 여부
        """
        start_time = self.stats.record_sync_start()
        
        try:
            with LogContext("시트 동기화") as ctx:
                ctx.log_step("최신 시트 데이터 조회")
                
                # 시트에서 미래 툿들 조회
                toot_data_list = self.sheets_manager.get_future_toots(force_refresh=True)
                
                ctx.log_step(f"{len(toot_data_list)}개 툿 데이터 조회 완료")
                
                ctx.log_step("캐시와 동기화")
                
                # 캐시와 동기화
                has_changes, changes = self.cache_manager.sync_with_sheet_data(toot_data_list)
                
                if has_changes:
                    added_count = len(changes['added'])
                    updated_count = len(changes['updated'])
                    removed_count = len(changes['removed'])
                    
                    ctx.log_step(f"변경사항 적용: 추가 {added_count}, 수정 {updated_count}, 삭제 {removed_count}")
                    
                    logger.info(f"📊 시트 동기화 완료 - 변경사항: +{added_count} ~{updated_count} -{removed_count}")
                    
                    # 중요한 변경사항이 있으면 알림
                    if added_count > 0:
                        next_toot = self.cache_manager.get_pending_entries()
                        if next_toot:
                            next_time = format_datetime_korean(next_toot[0].scheduled_datetime)
                            logger.info(f"다음 예약: {next_time}")
                else:
                    ctx.log_step("변경사항 없음")
                    logger.debug("📊 시트 동기화 완료 - 변경사항 없음")
                
                # 만료된 엔트리 정리
                ctx.log_step("만료된 엔트리 정리")
                cleaned_count = self.cache_manager.cleanup_expired_entries()
                if cleaned_count > 0:
                    ctx.log_step(f"{cleaned_count}개 만료 엔트리 정리 완료")
                
                self.stats.record_sync_end(start_time, True)
                return True
                
        except Exception as e:
            error_msg = f"시트 동기화 실패: {e}"
            logger.error(error_msg)
            self.stats.record_sync_end(start_time, False, error_msg)
            return False
    
    @log_performance
    def execute_scheduled_toots(self) -> int:
        """
        예약된 툿들 실행
        
        Returns:
            int: 실행된 툿 수
        """
        try:
            current_time = default_parser.get_current_datetime()
            
            # 실행할 툿들 조회 (1분 버퍼)
            due_entries = self.cache_manager.get_due_entries(
                current_time=current_time,
                buffer_minutes=1
            )
            
            if not due_entries:
                logger.debug("⏰ 실행할 예약 툿이 없습니다")
                return 0
            
            logger.info(f"⏰ {len(due_entries)}개 툿 실행 시작")
            
            # 동시 실행을 위한 Future 목록
            futures = []
            
            for entry in due_entries:
                # 이미 처리중이거나 완료된 툿은 건너뛰기
                if entry.status != 'pending':
                    continue
                
                # 상태를 실행중으로 변경
                self.cache_manager.update_entry_status(
                    entry.get_cache_key(), 
                    'executing'
                )
                
                # 비동기 실행
                future = self.executor.submit(self._execute_single_toot, entry)
                futures.append((future, entry))
            
            # 결과 수집
            executed_count = 0
            for future, entry in futures:
                try:
                    success = future.result(timeout=60)  # 60초 타임아웃
                    if success:
                        executed_count += 1
                except Exception as e:
                    logger.error(f"툿 실행 실패 (행 {entry.row_index}): {e}")
                    self.cache_manager.update_entry_status(
                        entry.get_cache_key(),
                        'failed',
                        str(e)
                    )
                    self.stats.record_toot_attempt(False, str(e))
            
            if executed_count > 0:
                logger.info(f"✅ {executed_count}개 툿 실행 완료")
            
            return executed_count
            
        except Exception as e:
            logger.error(f"예약 툿 실행 중 오류: {e}")
            return 0
    
    def _execute_single_toot(self, entry: CacheEntry) -> bool:
        """
        개별 툿 실행
        
        Args:
            entry: 실행할 캐시 엔트리
        
        Returns:
            bool: 실행 성공 여부
        """
        try:
            logger.info(f"🚀 툿 실행: 행 {entry.row_index} | {entry.account} | {format_datetime_korean(entry.scheduled_datetime)}")
            
            # 마스토돈에 포스팅 (계정별)
            result = self.mastodon_manager.post_scheduled_toot(
                content=entry.content,
                account_name=entry.account,
                scheduled_at=entry.scheduled_datetime,
                visibility='direct'
            )
            
            if result.success:
                # 성공 처리
                self.cache_manager.update_entry_status(
                    entry.get_cache_key(),
                    'posted'
                )
                
                logger.info(f"✅ {entry.account} 툿 포스팅 성공: {result.toot_url}")
                logger.info(f"내용: {entry.content[:100]}...")
                
                self.stats.record_toot_attempt(True)
                return True
            else:
                # 실패 처리
                error_msg = result.error_message
                self.cache_manager.update_entry_status(
                    entry.get_cache_key(),
                    'failed',
                    error_msg
                )
                
                logger.error(f"❌ {entry.account} 툿 포스팅 실패 (행 {entry.row_index}): {error_msg}")
                self.stats.record_toot_attempt(False, error_msg)
                return False
                
        except Exception as e:
            error_msg = f"툿 실행 중 예외: {e}"
            logger.error(error_msg)
            
            self.cache_manager.update_entry_status(
                entry.get_cache_key(),
                'failed',
                error_msg
            )
            
            self.stats.record_toot_attempt(False, error_msg)
            return False
    
    def health_check(self) -> Dict[str, Any]:
        """
        시스템 헬스체크 수행
        
        Returns:
            Dict[str, Any]: 헬스체크 결과
        """
        try:
            with LogContext("시스템 헬스체크") as ctx:
                health_result = {
                    'timestamp': datetime.now(self.timezone).isoformat(),
                    'overall_healthy': True,
                    'components': {},
                    'warnings': [],
                    'errors': []
                }
                
                ctx.log_step("마스토돈 연결 확인")
                mastodon_healthy = self.mastodon_manager.check_connection()
                health_result['components']['mastodon'] = mastodon_healthy
                if not mastodon_healthy:
                    health_result['errors'].append("마스토돈 연결 실패")
                    health_result['overall_healthy'] = False
                
                ctx.log_step("시트 연결 확인")
                try:
                    validation = self.sheets_manager.validate_sheet_structure()
                    sheets_healthy = validation['valid']
                    health_result['components']['sheets'] = sheets_healthy
                    if not sheets_healthy:
                        health_result['errors'].extend(validation['errors'])
                        health_result['overall_healthy'] = False
                    if validation['warnings']:
                        health_result['warnings'].extend(validation['warnings'])
                except Exception as e:
                    health_result['components']['sheets'] = False
                    health_result['errors'].append(f"시트 검증 실패: {e}")
                    health_result['overall_healthy'] = False
                
                ctx.log_step("캐시 상태 확인")
                cache_stats = self.cache_manager.get_cache_stats()
                cache_healthy = cache_stats['total_entries'] >= 0  # 기본 동작 확인
                health_result['components']['cache'] = cache_healthy
                
                # 캐시 통계 추가
                health_result['cache_stats'] = {
                    'total_entries': cache_stats['total_entries'],
                    'pending_entries': cache_stats['pending_entries'],
                    'current_due': cache_stats.get('current_due', 0)
                }
                
                ctx.log_step("다음 예약 확인")
                next_entries = self.cache_manager.get_pending_entries()
                if next_entries:
                    next_entry = next_entries[0]
                    health_result['next_scheduled'] = {
                        'datetime': next_entry.scheduled_datetime.isoformat(),
                        'time_until': format_time_until(next_entry.scheduled_datetime),
                        'content_preview': next_entry.content[:50] + '...'
                    }
                
                # 최근 성능 확인
                stats = self.stats.get_stats()
                if stats['success_rate'] < 90:
                    health_result['warnings'].append(f"동기화 성공률 낮음: {stats['success_rate']:.1f}%")
                
                if stats['post_success_rate'] < 95:
                    health_result['warnings'].append(f"포스팅 성공률 낮음: {stats['post_success_rate']:.1f}%")
                
                self.last_health_check = health_result
                
                if health_result['overall_healthy']:
                    logger.info("💚 시스템 헬스체크 정상")
                else:
                    logger.warning("💛 시스템 헬스체크에서 문제 발견")
                    for error in health_result['errors']:
                        logger.error(f"  - {error}")
                
                return health_result
                
        except Exception as e:
            logger.error(f"헬스체크 실패: {e}")
            return {
                'timestamp': datetime.now(self.timezone).isoformat(),
                'overall_healthy': False,
                'error': str(e)
            }
    
    def daily_cleanup(self) -> Dict[str, int]:
        """
        일일 정리 작업 수행
        
        Returns:
            Dict[str, int]: 정리 결과
        """
        try:
            with LogContext("일일 정리") as ctx:
                cleanup_result = {
                    'expired_entries': 0,
                    'old_backups': 0,
                    'log_files': 0
                }
                
                ctx.log_step("만료된 캐시 엔트리 정리")
                cleanup_result['expired_entries'] = self.cache_manager.cleanup_expired_entries()
                
                ctx.log_step("오래된 백업 파일 정리")
                # 캐시 매니저 내부에서 자동으로 처리됨
                
                ctx.log_step("로그 파일 정리")
                # 로그 로테이션은 로깅 시스템에서 자동 처리됨
                
                # 통계 리셋 (주간 통계는 보존)
                if datetime.now(self.timezone).weekday() == 0:  # 월요일
                    ctx.log_step("주간 통계 리셋")
                    # 필요시 구현
                
                logger.info(f"🧹 일일 정리 완료: 캐시 {cleanup_result['expired_entries']}개")
                
                return cleanup_result
                
        except Exception as e:
            logger.error(f"일일 정리 실패: {e}")
            return {'error': str(e)}
    
    def start(self) -> None:
        """스케줄러 시작 (블로킹)"""
        if not self.initialize_dependencies():
            raise RuntimeError("의존성 초기화 실패")
        
        if not self.setup_scheduler(use_background=False):
            raise RuntimeError("스케줄러 설정 실패")
        
        logger.info("🚀 툿 스케줄러 시작 (블로킹 모드)")
        
        try:
            self.is_running = True
            
            # 초기 동기화
            logger.info("🔄 초기 시트 동기화 실행...")
            self.sync_with_sheets()
            
            # 스케줄러 시작
            self.scheduler.start()
            
        except KeyboardInterrupt:
            logger.info("👋 사용자 요청으로 스케줄러를 중지합니다")
        except Exception as e:
            logger.error(f"💥 스케줄러 실행 중 오류: {e}")
            raise
        finally:
            self.stop()
    
    def start_background(self) -> bool:
        """
        백그라운드 스케줄러 시작
        
        Returns:
            bool: 시작 성공 여부
        """
        try:
            if not self.initialize_dependencies():
                return False
            
            if not self.setup_scheduler(use_background=True):
                return False
            
            logger.info("🚀 툿 스케줄러 시작 (백그라운드 모드)")
            
            self.is_running = True
            
            # 초기 동기화
            logger.info("🔄 초기 시트 동기화 실행...")
            self.sync_with_sheets()
            
            # 백그라운드 스케줄러 시작
            self.background_scheduler.start()
            
            return True
            
        except Exception as e:
            logger.error(f"백그라운드 스케줄러 시작 실패: {e}")
            return False
    
    def stop(self) -> None:
        """스케줄러 중지"""
        logger.info("🛑 툿 스케줄러 중지 중...")
        
        self.is_running = False
        self.shutdown_event.set()
        
        # 스케줄러 중지
        if self.scheduler and self.scheduler.running:
            self.scheduler.shutdown(wait=False)
        
        if self.background_scheduler and self.background_scheduler.running:
            self.background_scheduler.shutdown(wait=False)
        
        # 스레드 풀 종료
        self.executor.shutdown(wait=True, timeout=30)
        
        logger.info("✅ 툿 스케줄러 중지 완료")
    
    def stop_background(self) -> None:
        """백그라운드 스케줄러 중지"""
        self.stop()
    
    def get_status(self) -> Dict[str, Any]:
        """스케줄러 상태 반환"""
        stats = self.stats.get_stats()
        
        # 스케줄러 상태 추가
        scheduler_status = {
            'is_running': self.is_running,
            'scheduler_type': 'background' if self.background_scheduler else 'blocking',
            'jobs_count': 0,
            'next_jobs': []
        }
        
        # 활성 스케줄러에서 작업 정보 가져오기
        active_scheduler = self.background_scheduler or self.scheduler
        if active_scheduler:
            scheduler_status['jobs_count'] = len(active_scheduler.get_jobs())
            
            # 다음 작업들
            next_jobs = []
            for job in active_scheduler.get_jobs():
                next_run = job.next_run_time
                if next_run:
                    next_jobs.append({
                        'id': job.id,
                        'name': job.name,
                        'next_run': next_run.isoformat(),
                        'next_run_in': format_time_until(next_run.replace(tzinfo=self.timezone))
                    })
            
            # 다음 실행 시간순 정렬
            next_jobs.sort(key=lambda x: x['next_run'])
            scheduler_status['next_jobs'] = next_jobs[:5]  # 상위 5개만
        
        # 캐시 상태
        cache_status = {}
        if self.cache_manager:
            cache_stats = self.cache_manager.get_cache_stats()
            cache_status = {
                'total_entries': cache_stats['total_entries'],
                'pending_entries': cache_stats['pending_entries'],
                'current_due': cache_stats.get('current_due', 0),
                'next_scheduled': None
            }
            
            # 다음 예약 정보
            next_entries = self.cache_manager.get_pending_entries()
            if next_entries:
                next_entry = next_entries[0]
                cache_status['next_scheduled'] = {
                    'datetime': next_entry.scheduled_datetime.isoformat(),
                    'time_until': format_time_until(next_entry.scheduled_datetime),
                    'content_preview': next_entry.content[:50] + '...'
                }
        
        # 전체 상태 조합
        status = {
            **stats,
            'scheduler': scheduler_status,
            'cache': cache_status,
            'last_health_check': self.last_health_check
        }
        
        return status
    
    def _job_executed(self, event):
        """작업 실행 완료 이벤트 핸들러"""
        job_id = event.job_id
        logger.debug(f"📋 작업 완료: {job_id}")
    
    def _job_error(self, event):
        """작업 오류 이벤트 핸들러"""
        job_id = event.job_id
        exception = event.exception
        logger.error(f"💥 작업 오류: {job_id} - {exception}")
        
        # 오류 통계 기록
        self.stats.stats['errors'].append({
            'timestamp': datetime.now(self.timezone).isoformat(),
            'type': 'job_error',
            'job_id': job_id,
            'message': str(exception)
        })


# 전역 스케줄러 인스턴스
_scheduler: Optional[TootScheduler] = None
_background_scheduler: Optional[TootScheduler] = None


def get_scheduler() -> TootScheduler:
    """전역 스케줄러 반환 (블로킹용)"""
    global _scheduler
    
    if _scheduler is None:
        _scheduler = TootScheduler()
    
    return _scheduler


def get_background_scheduler() -> TootScheduler:
    """전역 백그라운드 스케줄러 반환"""
    global _background_scheduler
    
    if _background_scheduler is None:
        _background_scheduler = TootScheduler()
    
    return _background_scheduler


def run_scheduler_daemon() -> int:
    """
    스케줄러 데몬 실행
    
    Returns:
        int: 종료 코드
    """
    try:
        scheduler = get_scheduler()
        scheduler.start()
        return 0
    except KeyboardInterrupt:
        logger.info("👋 사용자 요청으로 데몬을 종료합니다")
        return 0
    except Exception as e:
        logger.error(f"💥 데몬 실행 실패: {e}")
        return 1


def validate_scheduler_config() -> Tuple[bool, List[str]]:
    """
    스케줄러 설정 검증
    
    Returns:
        Tuple[bool, List[str]]: (유효 여부, 오류 목록)
    """
    errors = []
    
    # 동기화 간격 확인
    sync_interval = getattr(config, 'SYNC_INTERVAL_MINUTES', 20)
    if not isinstance(sync_interval, int) or sync_interval < 1 or sync_interval > 60:
        errors.append(f"SYNC_INTERVAL_MINUTES가 유효하지 않습니다: {sync_interval}")
    
    # 20분이 아닌 경우 경고
    if sync_interval != 20:
        logger.warning(f"권장 동기화 간격은 20분입니다 (현재: {sync_interval}분)")
    
    # 시간대 확인
    try:
        timezone = getattr(config, 'TIMEZONE', pytz.timezone('Asia/Seoul'))
        if not isinstance(timezone, pytz.BaseTzInfo):
            errors.append("TIMEZONE 설정이 유효하지 않습니다")
    except Exception as e:
        errors.append(f"TIMEZONE 설정 오류: {e}")
    
    # 최대 동시 포스팅 수 확인
    max_concurrent = getattr(config, 'MAX_CONCURRENT_POSTS', 3)
    if not isinstance(max_concurrent, int) or max_concurrent < 1 or max_concurrent > 10:
        errors.append(f"MAX_CONCURRENT_POSTS가 유효하지 않습니다: {max_concurrent}")
    
    return len(errors) == 0, errors


def test_scheduler() -> bool:
    """스케줄러 테스트"""
    try:
        logger.info("🧪 스케줄러 테스트 시작...")
        
        # 설정 검증
        is_valid, errors = validate_scheduler_config()
        if not is_valid:
            logger.error("스케줄러 설정 검증 실패:")
            for error in errors:
                logger.error(f"  - {error}")
            return False
        
        # 스케줄러 인스턴스 생성
        scheduler = TootScheduler()
        
        # 의존성 초기화 테스트
        if not scheduler.initialize_dependencies():
            logger.error("의존성 초기화 실패")
            return False
        
        # 스케줄러 설정 테스트 (백그라운드)
        if not scheduler.setup_scheduler(use_background=True):
            logger.error("스케줄러 설정 실패")
            return False
        
        # 헬스체크 테스트
        health_result = scheduler.health_check()
        if not health_result['overall_healthy']:
            logger.warning("헬스체크에서 문제 발견:")
            for error in health_result.get('errors', []):
                logger.warning(f"  - {error}")
        
        # 동기화 테스트
        sync_result = scheduler.sync_with_sheets()
        if not sync_result:
            logger.error("시트 동기화 테스트 실패")
            return False
        
        # 통계 확인
        status = scheduler.get_status()
        logger.info(f"스케줄러 상태: {status['scheduler']['jobs_count']}개 작업 등록")
        
        # 정리
        scheduler.stop()
        
        logger.info("✅ 스케줄러 테스트 성공")
        return True
        
    except Exception as e:
        logger.error(f"❌ 스케줄러 테스트 실패: {e}")
        return False


if __name__ == "__main__":
    """스케줄러 테스트 실행"""
    print("🧪 마스토돈 툿 스케줄러 테스트 시작...")
    
    try:
        # 설정 검증
        print("⚙️ 설정 검증...")
        is_valid, errors = validate_scheduler_config()
        if is_valid:
            print("✅ 설정 검증 성공")
        else:
            print("❌ 설정 검증 실패:")
            for error in errors:
                print(f"  - {error}")
            sys.exit(1)
        
        # 스케줄러 테스트
        print("🔧 스케줄러 기능 테스트...")
        if test_scheduler():
            print("✅ 스케줄러 테스트 성공")
        else:
            print("❌ 스케줄러 테스트 실패")
            sys.exit(1)
        
        # 실제 실행 테스트 (짧은 시간)
        print("⏰ 단기 실행 테스트 (10초)...")
        scheduler = get_background_scheduler()
        
        if scheduler.start_background():
            print("✅ 백그라운드 스케줄러 시작 성공")
            
            # 10초 대기
            import time
            time.sleep(10)
            
            # 상태 확인
            status = scheduler.get_status()
            print(f"실행 통계:")
            print(f"  - 동기화: {status['successful_syncs']}회 성공")
            print(f"  - 가동시간: {status['uptime_formatted']}")
            print(f"  - 등록된 작업: {status['scheduler']['jobs_count']}개")
            
            # 중지
            scheduler.stop_background()
            print("✅ 스케줄러 중지 완료")
        else:
            print("❌ 백그라운드 스케줄러 시작 실패")
            sys.exit(1)
        
        print("🎉 모든 테스트 완료!")
        
    except KeyboardInterrupt:
        print("\n👋 사용자 요청으로 테스트를 중단합니다")
    except Exception as e:
        print(f"❌ 테스트 실행 실패: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
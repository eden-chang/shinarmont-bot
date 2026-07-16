"""
스케줄러 모듈
툿수 모니터링을 주기적으로 실행하는 스케줄러입니다.
"""

import os
import sys
import time
import threading
import signal
from typing import Dict, List, Optional, Callable, Tuple
from datetime import datetime, timedelta
import pytz
import schedule

# VM 환경 대응
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(current_dir)

try:
    from config.settings import config
    from utils.error_handling import (
        safe_execute, ErrorContext, log_error_context,
        setup_global_exception_handler
    )
    from utils.logging_config import logger, toot_logger, log_system_stats
    from core.toot_counter import run_monitoring_cycle
    from core.reward_system import execute_reward_process
    from core.mastodon_api import send_system_notification
except ImportError as e:
    # 임포트 실패 시 기본 로거
    import logging
    logger = logging.getLogger('scheduler')
    print(f"임포트 실패: {e}")


class TootMonitorScheduler:
    """툿수 모니터링 스케줄러 클래스"""
    
    def __init__(self):
        """스케줄러 초기화"""
        self.is_running = False
        self.cycle_count = 0
        self.start_time = None
        self.last_run_time = None
        self.last_run_success = None
        self.total_successful_cycles = 0
        self.total_failed_cycles = 0
        self.scheduler_thread = None
        self.stop_event = threading.Event()

        logger.info("툿수 모니터링 스케줄러 초기화 완료")

    def _run_monitoring_task(self):
        """모니터링 작업 실행"""
        try:
            self.cycle_count += 1
            self.last_run_time = datetime.now(pytz.timezone('Asia/Seoul'))
            
            logger.info("=" * 60)
            logger.info(f"🤖 툿수 모니터링 사이클 #{self.cycle_count} 시작")
            logger.info(f"⏰ 실행 시간: {self.last_run_time.strftime('%Y-%m-%d %H:%M:%S KST')}")
            logger.info("=" * 60)
            
            # 1. 툿수 계산 및 재화 지급 실행
            monitoring_result = run_monitoring_cycle(self.cycle_count)
            
            if not monitoring_result['success']:
                self.total_failed_cycles += 1
                self.last_run_success = False
                logger.error(f"❌ 모니터링 사이클 #{self.cycle_count} 실패: {monitoring_result.get('error', 'Unknown error')}")
                
                # 관리자에게 알림 전송
                if config.NOTIFICATION_ENABLED:
                    error_msg = f"모니터링 사이클 #{self.cycle_count} 실패\n오류: {monitoring_result.get('error', 'Unknown error')}"
                    send_system_notification(error_msg, to_admin=True)
                
                return
            
            # 2. 재화 지급 처리 (즉시 처리 방식 여부에 따라)
            if config.IMMEDIATE_REWARD_PROCESSING:
                # 즉시 처리 방식: 이미 calculate_and_process_immediate()에서 재화 지급 완료
                logger.info("🚀 즉시 처리 방식으로 재화 지급이 완료되었습니다.")
                reward_users = monitoring_result.get('reward_users', [])
                logger.info(f"💰 총 재화 지급 대상자: {len(reward_users)}명")
            else:
                # 기존 방식: 별도로 재화 지급 실행
                reward_users = monitoring_result.get('reward_users', [])
                
                if reward_users:
                    logger.info(f"🎁 재화 지급 대상자 {len(reward_users)}명 처리 시작")
                    reward_result = execute_reward_process(reward_users)
                    
                    if reward_result['success']:
                        summary = reward_result['summary']
                        logger.info(f"✅ 재화 지급 완료: {summary.successful_rewards}/{summary.total_users}명 성공")
                    else:
                        logger.error(f"❌ 재화 지급 실패: {reward_result.get('error', 'Unknown error')}")
                else:
                    logger.info("💰 재화 지급 대상자가 없습니다.")
            
            # 3. 성공 처리
            self.total_successful_cycles += 1
            self.last_run_success = True
            
            # 통계 정보
            stats = monitoring_result['statistics']
            duration = monitoring_result['duration']
            
            logger.info("📈 사이클 완료 요약:")
            logger.info(f"   - 소요시간: {duration:.2f}초")
            logger.info(f"   - 확인된 사용자: {stats['successful_checks']}/{stats['total_users_checked']}명")
            logger.info(f"   - 툿수 증가: {stats['users_with_toot_increase']}명 (+{stats['total_toot_increase']}툿)")
            logger.info(f"   - 재화 지급: {stats['users_with_rewards']}명 ({stats['total_rewards_to_give']}재화)")
            
            # 시스템 통계 로그 기록
            log_system_stats(
                users_total=stats['total_users_checked'],
                users_active=stats['users_with_toot_increase'],
                total_rewards_given=stats['total_rewards_to_give'],
                errors_count=stats['failed_checks']
            )
            
            logger.info("=" * 60)
            logger.info(f"✅ 툿수 모니터링 사이클 #{self.cycle_count} 완료")
            logger.info("=" * 60)
            
        except Exception as e:
            self.total_failed_cycles += 1
            self.last_run_success = False
            error_msg = f"모니터링 작업 실행 중 예상치 못한 오류: {str(e)}"
            logger.error(error_msg, exc_info=True)
            
            # 관리자에게 긴급 알림
            if config.NOTIFICATION_ENABLED:
                send_system_notification(f"🚨 긴급: 모니터링 작업 실패\n{error_msg}", to_admin=True)
    
    def start(self):
        """스케줄러 시작"""
        if self.is_running:
            logger.warning("스케줄러가 이미 실행 중입니다.")
            return
        
        try:
            logger.info("🚀 툿수 모니터링 스케줄러 시작")
            logger.info(f"⏰ 실행 간격: {config.CHECK_INTERVAL_MINUTES}분")
            logger.info(f"💰 재화 지급: {config.TOOTS_PER_REWARD}툿마다 {config.REWARD_AMOUNT}재화")
            
            # 전역 예외 핸들러 설정
            setup_global_exception_handler()
            
            # 스케줄 설정
            schedule.every(config.CHECK_INTERVAL_MINUTES).minutes.do(self._run_monitoring_task)
            
            self.is_running = True
            self.start_time = datetime.now(pytz.timezone('Asia/Seoul'))
            
            # 첫 번째 사이클 즉시 실행 (선택사항)
            logger.info("🎯 첫 번째 모니터링 사이클을 즉시 실행합니다...")
            self._run_monitoring_task()
            
            # 스케줄러 메인 루프
            logger.info("⏰ 스케줄러 메인 루프 진입")
            
            while self.is_running and not self.stop_event.is_set():
                try:
                    schedule.run_pending()
                    time.sleep(1)  # CPU 사용량 최적화
                    
                except Exception as e:
                    logger.error(f"스케줄러 메인 루프 오류: {e}")
                    time.sleep(5)  # 오류 발생 시 잠시 대기
            
            logger.info("📴 스케줄러 메인 루프 종료")
            
        except KeyboardInterrupt:
            logger.info("👋 사용자 요청으로 스케줄러를 종료합니다.")
        except Exception as e:
            logger.error(f"💥 스케줄러 시작 실패: {e}", exc_info=True)
        finally:
            self._cleanup()
    
    def stop(self):
        """스케줄러 중지"""
        if not self.is_running:
            logger.info("스케줄러가 실행 중이 아닙니다.")
            return
        
        logger.info("🛑 스케줄러 중지 요청")
        self.is_running = False
        self.stop_event.set()
        
        # 모든 스케줄 취소
        schedule.clear()
        
        # 종료 알림
        if config.NOTIFICATION_ENABLED:
            uptime = self.get_uptime()
            success_rate = self.get_success_rate()
            stop_msg = (
                f"📴 툿수 모니터링 봇 종료\n"
                f"⏱️ 총 가동시간: {uptime}\n"
                f"🔄 총 사이클: {self.cycle_count}회\n"
                f"✅ 성공률: {success_rate:.1f}%"
            )
            send_system_notification(stop_msg)
    
    def _cleanup(self):
        """정리 작업"""
        try:
            logger.info("🧹 스케줄러 정리 작업 시작...")
            
            # 최종 통계 출력
            uptime = self.get_uptime()
            success_rate = self.get_success_rate()
            
            logger.info("📊 최종 통계:")
            logger.info(f"   - 총 가동시간: {uptime}")
            logger.info(f"   - 총 실행 사이클: {self.cycle_count}회")
            logger.info(f"   - 성공한 사이클: {self.total_successful_cycles}회")
            logger.info(f"   - 실패한 사이클: {self.total_failed_cycles}회")
            logger.info(f"   - 전체 성공률: {success_rate:.1f}%")
            
            if self.last_run_time:
                logger.info(f"   - 마지막 실행: {self.last_run_time.strftime('%Y-%m-%d %H:%M:%S KST')}")
                logger.info(f"   - 마지막 실행 결과: {'성공' if self.last_run_success else '실패'}")
            
            # 스케줄 정리
            schedule.clear()
            
            # 상태 초기화
            self.is_running = False
            
            logger.info("✅ 스케줄러 정리 작업 완료")
            
        except Exception as e:
            logger.error(f"❌ 정리 작업 중 오류: {e}")
    
    def get_status(self) -> Dict[str, any]:
        """스케줄러 상태 반환"""
        status = {
            'is_running': self.is_running,
            'cycle_count': self.cycle_count,
            'start_time': self.start_time.isoformat() if self.start_time else None,
            'uptime_seconds': self.get_uptime_seconds(),
            'uptime_formatted': self.get_uptime(),
            'last_run_time': self.last_run_time.isoformat() if self.last_run_time else None,
            'last_run_success': self.last_run_success,
            'total_successful_cycles': self.total_successful_cycles,
            'total_failed_cycles': self.total_failed_cycles,
            'success_rate': self.get_success_rate(),
            'next_run_time': self.get_next_run_time(),
            'check_interval_minutes': config.CHECK_INTERVAL_MINUTES
        }
        
        return status
    
    def get_uptime_seconds(self) -> float:
        """가동 시간 (초) 반환"""
        if not self.start_time:
            return 0.0
        
        current_time = datetime.now(pytz.timezone('Asia/Seoul'))
        return (current_time - self.start_time).total_seconds()
    
    def get_uptime(self) -> str:
        """포맷된 가동 시간 반환"""
        uptime_seconds = self.get_uptime_seconds()
        
        if uptime_seconds == 0:
            return "0초"
        
        days = int(uptime_seconds // 86400)
        hours = int((uptime_seconds % 86400) // 3600)
        minutes = int((uptime_seconds % 3600) // 60)
        seconds = int(uptime_seconds % 60)
        
        parts = []
        if days > 0:
            parts.append(f"{days}일")
        if hours > 0:
            parts.append(f"{hours}시간")
        if minutes > 0:
            parts.append(f"{minutes}분")
        if seconds > 0 or not parts:
            parts.append(f"{seconds}초")
        
        return " ".join(parts)
    
    def get_success_rate(self) -> float:
        """성공률 반환"""
        if self.cycle_count == 0:
            return 0.0
        
        return (self.total_successful_cycles / self.cycle_count) * 100
    
    def get_next_run_time(self) -> Optional[str]:
        """다음 실행 예정 시간 반환"""
        if not self.is_running:
            return None
        
        jobs = schedule.get_jobs()
        if not jobs:
            return None
        
        next_run = jobs[0].next_run
        if next_run:
            # UTC를 KST로 변환
            kst_time = next_run.replace(tzinfo=pytz.UTC).astimezone(pytz.timezone('Asia/Seoul'))
            return kst_time.strftime('%Y-%m-%d %H:%M:%S KST')
        
        return None
    
    def force_run_cycle(self) -> bool:
        """강제로 모니터링 사이클 실행"""
        if not self.is_running:
            logger.warning("스케줄러가 실행 중이 아닙니다.")
            return False
        
        try:
            logger.info("🎯 수동으로 모니터링 사이클을 실행합니다...")
            self._run_monitoring_task()
            return True
        except Exception as e:
            logger.error(f"수동 사이클 실행 실패: {e}")
            return False


# 스케줄러 관리 함수들
def create_scheduler() -> TootMonitorScheduler:
    """스케줄러 생성"""
    return TootMonitorScheduler()


def run_scheduler_daemon():
    """스케줄러 데몬 모드로 실행"""
    scheduler = create_scheduler()
    
    try:
        scheduler.start()
    except KeyboardInterrupt:
        logger.info("👋 사용자 요청으로 스케줄러 종료")
    except Exception as e:
        logger.error(f"💥 스케줄러 실행 중 오류: {e}")
    finally:
        scheduler.stop()


# 백그라운드 스케줄러 클래스 (스레드 기반)
class BackgroundScheduler:
    """백그라운드에서 실행되는 스케줄러"""
    
    def __init__(self):
        """백그라운드 스케줄러 초기화"""
        self.scheduler = TootMonitorScheduler()
        self.thread = None
        
    def start_background(self):
        """백그라운드에서 스케줄러 시작"""
        if self.thread and self.thread.is_alive():
            logger.warning("백그라운드 스케줄러가 이미 실행 중입니다.")
            return
        
        def background_runner():
            try:
                self.scheduler.start()
            except Exception as e:
                logger.error(f"백그라운드 스케줄러 오류: {e}")
        
        self.thread = threading.Thread(target=background_runner, daemon=True)
        self.thread.start()
        
        logger.info("🔄 백그라운드 스케줄러 시작됨")
    
    def stop_background(self):
        """백그라운드 스케줄러 중지"""
        if self.scheduler.is_running:
            self.scheduler.stop()
        
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=5)
        
        logger.info("⏹️ 백그라운드 스케줄러 중지됨")
    
    def get_status(self) -> Dict[str, any]:
        """백그라운드 스케줄러 상태"""
        status = self.scheduler.get_status()
        status['background_thread_alive'] = self.thread.is_alive() if self.thread else False
        return status


# 전역 스케줄러 인스턴스
_global_scheduler: Optional[TootMonitorScheduler] = None
_global_background_scheduler: Optional[BackgroundScheduler] = None


def get_scheduler() -> TootMonitorScheduler:
    """전역 스케줄러 인스턴스 반환"""
    global _global_scheduler
    if _global_scheduler is None:
        _global_scheduler = TootMonitorScheduler()
    return _global_scheduler


def get_background_scheduler() -> BackgroundScheduler:
    """전역 백그라운드 스케줄러 인스턴스 반환"""
    global _global_background_scheduler
    if _global_background_scheduler is None:
        _global_background_scheduler = BackgroundScheduler()
    return _global_background_scheduler


# CLI 인터페이스 함수들
def start_monitoring():
    """모니터링 시작 (CLI용)"""
    scheduler = get_scheduler()
    scheduler.start()


def stop_monitoring():
    """모니터링 중지 (CLI용)"""
    scheduler = get_scheduler()
    scheduler.stop()


def get_monitoring_status() -> Dict[str, any]:
    """모니터링 상태 조회 (CLI용)"""
    scheduler = get_scheduler()
    return scheduler.get_status()


def force_run_monitoring() -> bool:
    """강제 모니터링 실행 (CLI용)"""
    scheduler = get_scheduler()
    return scheduler.force_run_cycle()


# 설정 검증 함수
def validate_scheduler_config() -> Tuple[bool, List[str]]:
    """스케줄러 설정 검증"""
    errors = []
    
    # 간격 설정 확인 (1시간=60분, 최대 24시간까지 허용)
    if config.CHECK_INTERVAL_MINUTES < 1 or config.CHECK_INTERVAL_MINUTES > 1440:
        errors.append(f"체크 간격이 잘못되었습니다: {config.CHECK_INTERVAL_MINUTES}분 (1-1440분 사이여야 함)")
    
    # 재화 설정 확인
    if config.TOOTS_PER_REWARD < 1 or config.TOOTS_PER_REWARD > 1000:
        errors.append(f"툿수당 재화 설정이 잘못되었습니다: {config.TOOTS_PER_REWARD}툿 (1-1000 사이여야 함)")
    
    if config.REWARD_AMOUNT < 1 or config.REWARD_AMOUNT > 9999999:
        errors.append(f"재화 지급량이 잘못되었습니다: {config.REWARD_AMOUNT}개 (1-100 사이여야 함)")
    
    # 마스토돈 설정 확인
    if not config.MASTODON_ACCESS_TOKEN:
        errors.append("마스토돈 액세스 토큰이 설정되지 않았습니다.")
    
    if not config.MASTODON_API_BASE_URL:
        errors.append("마스토돈 API URL이 설정되지 않았습니다.")
    
    # Google Sheets 설정 확인
    credentials_path = config.get_credentials_path()
    if not credentials_path.exists():
        errors.append(f"Google 인증 파일을 찾을 수 없습니다: {credentials_path}")
    
    return len(errors) == 0, errors


# 테스트 함수
def test_scheduler():
    """스케줄러 시스템 테스트"""
    try:
        logger.info("스케줄러 시스템 테스트 시작...")
        
        # 설정 검증
        is_valid, errors = validate_scheduler_config()
        if not is_valid:
            logger.error("스케줄러 설정 오류:")
            for error in errors:
                logger.error(f"  - {error}")
            return False
        
        # 스케줄러 생성 테스트
        scheduler = create_scheduler()
        logger.info("스케줄러 생성 성공")
        
        # 상태 확인 테스트
        status = scheduler.get_status()
        logger.info(f"초기 상태: {status}")
        
        # 강제 사이클 실행 테스트 (실제로는 실행하지 않음)
        logger.info("테스트용 모니터링 사이클 시뮬레이션...")
        
        logger.info("스케줄러 시스템 테스트 완료")
        return True
        
    except Exception as e:
        logger.error(f"스케줄러 시스템 테스트 실패: {e}")
        return False


# 메인 실행부
if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='툿수 모니터링 스케줄러')
    parser.add_argument('--test', action='store_true', help='테스트 모드로 실행')
    parser.add_argument('--status', action='store_true', help='상태 확인')
    parser.add_argument('--force-run', action='store_true', help='강제 사이클 실행')
    parser.add_argument('--background', action='store_true', help='백그라운드 모드로 실행')
    
    args = parser.parse_args()
    
    if args.test:
        # 테스트 모드
        test_scheduler()
    elif args.status:
        # 상태 확인
        status = get_monitoring_status()
        print(f"스케줄러 상태: {status}")
    elif args.force_run:
        # 강제 실행
        success = force_run_monitoring()
        print(f"강제 실행 {'성공' if success else '실패'}")
    elif args.background:
        # 백그라운드 모드
        bg_scheduler = get_background_scheduler()
        try:
            bg_scheduler.start_background()
            logger.info("백그라운드 모드로 실행 중... Ctrl+C로 종료")
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            bg_scheduler.stop_background()
    else:
        # 기본 모드 (포그라운드)
        run_scheduler_daemon()
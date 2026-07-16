"""
툿수 모니터링 자동봇 메인 실행 파일
모든 모듈을 통합하여 실행합니다.
"""

import os
import sys
import signal
import time
import argparse
from typing import Optional
from datetime import datetime
import pytz

# VM 환경 대응 - 프로젝트 루트 경로 설정
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(current_dir)

try:
    from config.settings import config
    # from config.validators import validate_startup_config
    from utils.logging_config import setup_logging, logger, toot_logger
    from utils.error_handling import setup_global_exception_handler
    from utils.sheets_operations import get_sheets_manager
    from core.mastodon_api import get_mastodon_manager, check_mastodon_connection, send_system_notification
    from core.toot_counter import get_toot_counter, test_toot_counter
    from core.reward_system import get_reward_system, test_reward_system
    from scheduler import (
        get_scheduler, get_background_scheduler, run_scheduler_daemon,
        validate_scheduler_config, test_scheduler
    )
except ImportError as e:
    print(f"❌ 필수 모듈 임포트 실패: {e}")
    print("필요한 패키지가 설치되어 있는지 확인해주세요.")
    print("pip install -r requirements.txt 를 실행하세요.")
    sys.exit(1)


def validate_startup_config() -> tuple[bool, str]:
    """
    시작시 설정 검증을 수행하고 결과를 반환합니다.
    
    Returns:
        tuple[bool, str]: (검증 성공 여부, 검증 결과 메시지)
    """
    errors = []
    
    # 필수 환경 변수 검사
    if not config.MASTODON_ACCESS_TOKEN:
        errors.append("MASTODON_ACCESS_TOKEN이 설정되지 않았습니다.")
    
    if not config.MASTODON_API_BASE_URL:
        errors.append("MASTODON_API_BASE_URL이 설정되지 않았습니다.")
    
    # 마스토돈 API URL 형식 검사
    if config.MASTODON_API_BASE_URL and not config.MASTODON_API_BASE_URL.startswith(('http://', 'https://')):
        errors.append("MASTODON_API_BASE_URL은 http:// 또는 https://로 시작해야 합니다.")
    
    # Google 인증 파일 존재 확인
    cred_path = config.get_credentials_path()
    if not cred_path.exists():
        errors.append(f"Google 인증 파일을 찾을 수 없습니다: {cred_path}")
    
    # 숫자 설정값 범위 검사
    if config.CHECK_INTERVAL_MINUTES < 1 or config.CHECK_INTERVAL_MINUTES > 1440:
        errors.append(f"체크 간격이 잘못되었습니다: {config.CHECK_INTERVAL_MINUTES}분 (1-1440분 사이여야 함)")
    
    if config.TOOTS_PER_REWARD < 1:
        errors.append(f"툿수당 재화 설정이 잘못되었습니다: {config.TOOTS_PER_REWARD}툿")
    
    # 결과 생성
    is_valid = len(errors) == 0
    
    if is_valid:
        summary = "✅ 모든 설정이 유효합니다."
    else:
        summary = "❌ 설정 검증 실패:\n" + "\n".join([f"  - {error}" for error in errors])
    
    return is_valid, summary


class TootMonitoringBot:
    """
    툿수 모니터링 봇 애플리케이션 클래스
    
    봇의 전체 생명주기를 관리합니다:
    - 초기화 및 설정 검증
    - 외부 서비스 연결 (마스토돈, Google Sheets)
    - 시스템 컴포넌트 초기화
    - 스케줄러 시작 및 관리
    """
    
    def __init__(self):
        """TootMonitoringBot 초기화"""
        self.mastodon_manager: Optional[object] = None
        self.sheets_manager: Optional[object] = None
        self.toot_counter: Optional[object] = None
        self.reward_system: Optional[object] = None
        self.scheduler: Optional[object] = None
        self.background_scheduler: Optional[object] = None
        self.is_running = False
        self.startup_time = time.time()
        
        # 시그널 핸들러 설정 (Ctrl+C 처리)
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def run(self, mode: str = 'daemon') -> int:
        """
        봇 애플리케이션 실행
        
        Args:
            mode: 실행 모드 ('daemon', 'background', 'test')
        
        Returns:
            int: 종료 코드 (0: 정상, 1: 오류)
        """
        try:
            logger.info("━" * 70)
            logger.info("🚀 툿수 모니터링 자동봇 시작")
            logger.info("━" * 70)
            
            # 1. 기본 설정 및 검증
            if not self._initialize_basic_systems():
                return 1
            
            # 2. 외부 서비스 연결
            if not self._connect_external_services():
                return 1
            
            # 3. 시스템 컴포넌트 초기화
            if not self._initialize_system_components():
                return 1
            
            # 4. 모드별 실행
            if mode == 'test':
                return self._run_test_mode()
            elif mode == 'background':
                return self._run_background_mode()
            else:  # daemon
                return self._run_daemon_mode()
            
        except KeyboardInterrupt:
            logger.info("👋 사용자 요청으로 봇을 종료합니다.")
            return 0
        except Exception as e:
            logger.critical(f"💥 예상치 못한 오류로 봇이 종료됩니다: {e}", exc_info=True)
            self._send_emergency_notification(str(e))
            return 1
        finally:
            self._cleanup()
    
    def _initialize_basic_systems(self) -> bool:
        """기본 시스템 초기화"""
        try:
            logger.info("⚙️  기본 시스템 초기화 중...")

            # 전역 예외 핸들러 설정
            setup_global_exception_handler()

            # 환경 설정 검증
            is_valid, validation_summary = validate_startup_config()
            if not is_valid:
                logger.error("설정 검증 실패")
                logger.error(validation_summary)
                return False

            logger.info("환경 설정 검증 완료")

            # 스케줄러 설정 검증
            scheduler_valid, scheduler_errors = validate_scheduler_config()
            if not scheduler_valid:
                logger.error("스케줄러 설정 검증 실패")
                for error in scheduler_errors:
                    logger.error(f"  {error}")
                return False

            logger.info("스케줄러 설정 검증 완료")

            # 설정 요약 출력
            config.print_config_summary()

            return True

        except Exception as e:
            logger.error(f"기본 시스템 초기화 실패: {e}")
            return False
    
    def _connect_external_services(self) -> bool:
        """외부 서비스 연결"""
        try:
            logger.info("🌐 외부 서비스 연결 중...")

            # 마스토돈 API 연결
            if not self._connect_mastodon_api():
                return False

            # Google Sheets 연결
            if not self._connect_google_sheets():
                return False

            logger.info("모든 외부 서비스 연결 완료")
            return True

        except Exception as e:
            logger.error(f"외부 서비스 연결 실패: {e}")
            return False
    
    def _connect_mastodon_api(self) -> bool:
        """마스토돈 API 연결"""
        try:
            logger.info("📡 마스토돈 API 연결 중...")

            self.mastodon_manager = get_mastodon_manager()

            # 연결 테스트
            if not check_mastodon_connection():
                logger.error("마스토돈 API 연결 테스트 실패")
                return False

            # 봇 정보 확인
            bot_info = self.mastodon_manager.get_bot_info()
            if bot_info:
                bot_username = bot_info.get('username', 'Unknown')
                bot_toots = bot_info.get('statuses_count', 0)
                logger.info(f"마스토돈 연결 성공  @{bot_username}  {bot_toots}툿")

            return True

        except Exception as e:
            logger.error(f"마스토돈 API 연결 실패: {e}")
            return False
    
    def _connect_google_sheets(self) -> bool:
        """Google Sheets 연결"""
        try:
            logger.info("📊 Google Sheets 연결 중...")

            self.sheets_manager = get_sheets_manager()

            # 시트 구조 검증
            validation_result = self.sheets_manager.validate_sheet_structure()

            if not validation_result['valid']:
                logger.error("시트 구조 검증 실패")
                for error in validation_result['errors']:
                    logger.error(f"  {error}")
                return False

            if validation_result['warnings']:
                logger.warning("시트 구조 경고")
                for warning in validation_result['warnings']:
                    logger.warning(f"  {warning}")

            # 사용자 수 확인
            toot_users = self.sheets_manager.get_toot_users()
            shop_users = self.sheets_manager.get_shop_users()

            logger.info(f"Google Sheets 연결 성공  툿수관리 {len(toot_users)}명  상점봇 {len(shop_users)}명")

            return True

        except Exception as e:
            logger.error(f"Google Sheets 연결 실패: {e}")
            return False
    
    def _initialize_system_components(self) -> bool:
        """시스템 컴포넌트 초기화"""
        try:
            logger.info("⚙️  시스템 컴포넌트 초기화 중...")

            # 툿수 계산기 초기화
            self.toot_counter = get_toot_counter()
            logger.info("툿수 계산기 초기화 완료")

            # 재화 시스템 초기화
            self.reward_system = get_reward_system()

            # 재화 시스템 상태 확인
            health_check = self.reward_system.check_reward_system_health()
            if not health_check['healthy']:
                logger.error("재화 시스템 상태 확인 실패")
                for issue in health_check['issues']:
                    logger.error(f"  {issue}")
                return False

            if health_check['warnings']:
                logger.warning("재화 시스템 경고")
                for warning in health_check['warnings']:
                    logger.warning(f"  {warning}")

            logger.info("재화 시스템 초기화 완료")

            # 스케줄러 초기화
            self.scheduler = get_scheduler()
            self.background_scheduler = get_background_scheduler()
            logger.info("스케줄러 초기화 완료")

            return True

        except Exception as e:
            logger.error(f"시스템 컴포넌트 초기화 실패: {e}")
            return False
    
    def _run_daemon_mode(self) -> int:
        """데몬 모드 실행"""
        try:
            logger.info("🚀 데몬 모드 시작")

            # 스케줄러 시작 (블로킹)
            self.is_running = True
            self.scheduler.start()

            logger.info("데몬 모드 정상 종료")
            return 0

        except Exception as e:
            self.is_running = False
            logger.error(f"데몬 모드 실행 실패: {e}")
            return 1
    
    def _run_background_mode(self) -> int:
        """백그라운드 모드 실행"""
        try:
            logger.info("🔄 백그라운드 모드 시작")

            # 백그라운드 스케줄러 시작
            self.background_scheduler.start_background()
            self.is_running = True

            logger.info("백그라운드 모드 실행 중  Ctrl+C로 종료")

            # 메인 스레드는 대기
            try:
                while self.is_running:
                    time.sleep(1)
            except KeyboardInterrupt:
                logger.info("사용자 요청으로 백그라운드 모드 종료")

            return 0

        except Exception as e:
            logger.error(f"백그라운드 모드 실행 실패: {e}")
            return 1
        finally:
            if self.background_scheduler:
                self.background_scheduler.stop_background()
    
    def _run_test_mode(self) -> int:
        """테스트 모드 실행"""
        try:
            logger.info("🧪 테스트 모드 시작")

            # 각 컴포넌트 테스트
            test_results = {
                '툿수 계산': test_toot_counter(),
                '재화 시스템': test_reward_system(),
                '스케줄러': test_scheduler()
            }

            # 결과 출력
            logger.info("테스트 결과")
            all_passed = True
            for test_name, result in test_results.items():
                status = "✓ 통과" if result else "✗ 실패"
                logger.info(f"  {test_name}: {status}")
                if not result:
                    all_passed = False

            if all_passed:
                logger.info("🎉 모든 테스트 통과")
                return 0
            else:
                logger.error("일부 테스트 실패")
                return 1

        except Exception as e:
            logger.error(f"테스트 모드 실행 실패: {e}")
            return 1
    
    
    def _send_emergency_notification(self, error_message: str) -> None:
        """긴급 상황 알림 전송"""
        try:
            if not config.NOTIFICATION_ENABLED or not self.mastodon_manager:
                return
            
            # 사용자 공지
            public_message = "툿수 모니터링 봇이 오류로 인해 중지되었습니다. 복구 작업 중입니다."
            send_system_notification(public_message, to_admin=False)
            
            # 관리자 알림
            if config.SYSTEM_ADMIN_ID:
                admin_message = f"봇 시스템 긴급 오류\n시간: {datetime.now(pytz.timezone('Asia/Seoul')).strftime('%Y-%m-%d %H:%M:%S KST')}\n오류: {error_message[:300]}"
                send_system_notification(admin_message, to_admin=True)

            logger.info("긴급 알림 전송 완료")

        except Exception as e:
            logger.error(f"긴급 알림 전송 실패: {e}")
    
    def _signal_handler(self, signum, frame):
        """시그널 핸들러 (Ctrl+C 등)"""
        logger.info(f"🛑 종료 시그널 수신 ({signum})")
        self.is_running = False
        
        if self.scheduler and self.scheduler.is_running:
            self.scheduler.stop()
        
        if self.background_scheduler:
            self.background_scheduler.stop_background()
    
    def _cleanup(self) -> None:
        """정리 작업"""
        try:
            logger.info("🧹 정리 작업 시작")

            # 스케줄러 중지
            if self.scheduler and self.scheduler.is_running:
                self.scheduler.stop()

            if self.background_scheduler:
                self.background_scheduler.stop_background()

            # 통계 출력
            if self.scheduler:
                try:
                    status = self.scheduler.get_status()
                    uptime = status.get('uptime_formatted', '0초')
                    success_rate = status.get('success_rate', 0)
                    total_cycles = status.get('cycle_count', 0)

                    logger.info("📊 최종 통계")
                    logger.info(f"  가동시간: {uptime}")
                    logger.info(f"  실행 사이클: {total_cycles}회")
                    logger.info(f"  성공률: {success_rate:.1f}%")
                except Exception as e:
                    logger.warning(f"통계 출력 실패: {e}")

            logger.info("정리 작업 완료")

        except Exception as e:
            logger.error(f"정리 작업 중 오류: {e}")
    
    def get_status(self) -> dict:
        """애플리케이션 상태 반환 (개발/디버깅용)"""
        status = {
            'is_running': self.is_running,
            'startup_time': self.startup_time,
            'uptime_seconds': time.time() - self.startup_time,
            'mastodon_connected': self.mastodon_manager is not None,
            'sheets_connected': self.sheets_manager is not None,
            'components_initialized': {
                'toot_counter': self.toot_counter is not None,
                'reward_system': self.reward_system is not None,
                'scheduler': self.scheduler is not None
            }
        }
        
        # 스케줄러 상태 추가
        if self.scheduler:
            status['scheduler_status'] = self.scheduler.get_status()
        
        return status


def create_argument_parser() -> argparse.ArgumentParser:
    """명령행 인수 파서 생성"""
    parser = argparse.ArgumentParser(
        description='툿수 모니터링 자동봇',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
실행 모드:
  daemon      포그라운드에서 실행 (기본값)
  background  백그라운드에서 실행
  test        모든 컴포넌트 테스트

예시:
  python main.py                    # 기본 데몬 모드
  python main.py --mode background  # 백그라운드 모드
  python main.py --test             # 테스트 모드
  python main.py --status           # 현재 상태 확인
  python main.py --version          # 버전 정보
        """
    )
    
    parser.add_argument(
        '--mode',
        choices=['daemon', 'background', 'test'],
        default='daemon',
        help='실행 모드 선택 (기본값: daemon)'
    )
    
    parser.add_argument(
        '--test',
        action='store_true',
        help='테스트 모드로 실행 (--mode test와 동일)'
    )
    
    parser.add_argument(
        '--status',
        action='store_true',
        help='현재 봇 상태 확인'
    )
    
    parser.add_argument(
        '--version',
        action='store_true',
        help='버전 정보 출력'
    )
    
    parser.add_argument(
        '--config-check',
        action='store_true',
        help='설정 검증만 수행'
    )
    
    return parser


def show_version():
    """버전 정보 출력"""
    print("🤖 툿수 모니터링 자동봇 v1.0")
    print("📅 개발 버전 - 2025.07")
    print("🔧 Python 기반 마스토돈 봇")
    print("📊 Google Sheets 연동")
    print("💰 자동 재화 지급 시스템")


def show_status():
    """현재 봇 상태 출력"""
    try:
        # 간단한 상태 확인
        print("📊 툿수 모니터링 봇 상태")
        print("━" * 50)

        # 설정 상태
        is_valid, _ = validate_startup_config()
        print(f"설정 상태: {'✓ 정상' if is_valid else '✗ 오류'}")

        # 마스토돈 연결 상태
        mastodon_ok = check_mastodon_connection()
        print(f"마스토돈 연결: {'✓ 정상' if mastodon_ok else '✗ 연결 실패'}")

        # 시트 연결 상태
        try:
            sheets_manager = get_sheets_manager()
            users_count = len(sheets_manager.get_toot_users())
            print(f"Google Sheets: ✓ 정상 ({users_count}명 등록)")
        except Exception:
            print("Google Sheets: ✗ 연결 실패")

        print("━" * 50)

    except Exception as e:
        print(f"✗ 상태 확인 실패: {e}")


def perform_config_check():
    """설정 검증 수행"""
    print("🔧 설정 검증 시작")
    print("━" * 50)

    # 환경 설정 검증
    is_valid, validation_summary = validate_startup_config()
    print("환경 설정 검증:")
    print(validation_summary)

    # 스케줄러 설정 검증
    scheduler_valid, scheduler_errors = validate_scheduler_config()
    print("\n⏰ 스케줄러 설정 검증:")
    if scheduler_valid:
        print("✓ 스케줄러 설정이 유효합니다")
    else:
        print("✗ 스케줄러 설정 오류:")
        for error in scheduler_errors:
            print(f"  {error}")

    print("━" * 50)

    overall_valid = is_valid and scheduler_valid
    print(f"전체 검증 결과: {'✓ 통과' if overall_valid else '✗ 실패'}")

    return overall_valid


def main() -> int:
    """메인 엔트리 포인트"""
    # 로깅 시스템 초기화
    setup_logging()
    
    try:
        # 명령행 인수 파싱
        parser = create_argument_parser()
        args = parser.parse_args()
        
        # 버전 정보
        if args.version:
            show_version()
            return 0
        
        # 상태 확인
        if args.status:
            show_status()
            return 0
        
        # 설정 검증
        if args.config_check:
            return 0 if perform_config_check() else 1
        
        # 테스트 모드 설정
        if args.test:
            args.mode = 'test'
        
        # 봇 애플리케이션 생성 및 실행
        bot = TootMonitoringBot()
        return bot.run(mode=args.mode)
        
    except KeyboardInterrupt:
        print("\n👋 사용자 요청으로 프로그램을 종료합니다.")
        return 0
    except Exception as e:
        print(f"💥 프로그램 시작 실패: {e}")
        return 1


if __name__ == '__main__':
    # 프로그램 실행
    exit_code = main()
    sys.exit(exit_code)
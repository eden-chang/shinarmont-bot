"""
마스토돈 스토리 스크립트 자동 출력 봇 메인 실행 파일
Google Sheets의 스토리 스크립트를 자동으로 송출하는 봇입니다.
"""

import os
import sys
import signal
import time
import argparse
import traceback
from typing import Optional
from datetime import datetime
import pytz

# VM 환경 대응 - 프로젝트 루트 경로 설정
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(current_dir)

try:
    from config.settings import config, validate_startup_config
    from utils.logging_config import setup_logging, get_logger
    from utils.datetime_utils import format_datetime_korean, default_parser
    from core.sheets_client import get_sheets_manager, test_sheets_connection
    from core.mastodon_client import get_mastodon_manager, check_mastodon_connection, send_system_notification
    from core.story_loop_manager import get_story_loop_manager
    from core.notification_handler import get_notification_handler
except ImportError as e:
    print(f"❌ 필수 모듈 임포트 실패: {e}")
    print("필요한 패키지가 설치되어 있는지 확인해주세요.")
    print("pip install -r requirements.txt 를 실행하세요.")
    sys.exit(1)

# 전역 로거
logger = None


class MastodonStoryBot:
    """
    마스토돈 스토리 스크립트 자동 출력 봇 애플리케이션 클래스
    
    봇의 전체 생명주기를 관리합니다:
    - 초기화 및 설정 검증
    - 외부 서비스 연결 (마스토돈, Google Sheets)
    - 스토리 루프 매니저 및 알림 처리기 초기화
    - 알림 모니터링 시작 및 관리
    - 정상/비정상 종료 처리
    """
    
    def __init__(self):
        """MastodonStoryBot 초기화"""
        self.mastodon_manager: Optional[object] = None
        self.sheets_manager: Optional[object] = None
        self.story_loop_manager: Optional[object] = None
        self.notification_handler: Optional[object] = None
        self.is_running = False
        self.startup_time = time.time()
        self.shutdown_requested = False
        
        # 시그널 핸들러 설정 (Ctrl+C, 강제 종료 처리)
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        # Windows에서도 작동하는 시그널이 있다면 추가
        if hasattr(signal, 'SIGBREAK'):
            signal.signal(signal.SIGBREAK, self._signal_handler)
    
    def run(self, mode: str = 'daemon') -> int:
        """
        봇 애플리케이션 실행
        
        Args:
            mode: 실행 모드 ('daemon', 'background', 'test')
        
        Returns:
            int: 종료 코드 (0: 정상, 1: 오류)
        """
        try:
            logger.info("=" * 70)
            logger.info("🤖 마스토돈 스토리 스크립트 자동 출력 봇 시작")
            logger.info("=" * 70)
            
            # 1. 기본 설정 및 검증
            if not self._initialize_basic_systems():
                return 1
            
            # 2. 외부 서비스 연결
            if not self._connect_external_services():
                return 1
            
            # 3. 시스템 컴포넌트 초기화
            if not self._initialize_system_components():
                return 1
            
            # 5. 모드별 실행
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
            self._send_emergency_shutdown_notification(str(e))
            return 1
        finally:
            self._cleanup()
    
    def _initialize_basic_systems(self) -> bool:
        """기본 시스템 초기화"""
        try:
            logger.info("🔧 기본 시스템 초기화 중...")
            
            # 환경 설정 검증
            is_valid, validation_summary = validate_startup_config()
            if not is_valid:
                logger.error("❌ 설정 검증 실패:")
                logger.error(validation_summary)
                return False
            
            logger.info("✅ 환경 설정 검증 완료")
            
            # 설정 요약 출력
            config.print_config_summary()
            
            return True
            
        except Exception as e:
            logger.error(f"❌ 기본 시스템 초기화 실패: {e}")
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
            
            logger.info("✅ 모든 외부 서비스 연결 완료")
            return True
            
        except Exception as e:
            logger.error(f"❌ 외부 서비스 연결 실패: {e}")
            return False
    
    def _connect_mastodon_api(self) -> bool:
        """마스토돈 API 연결"""
        try:
            logger.info("📡 마스토돈 API 연결 중...")
            
            self.mastodon_manager = get_mastodon_manager()
            
            # 연결 테스트
            if not check_mastodon_connection():
                logger.error("❌ 마스토돈 API 연결 테스트 실패")
                return False
            
            # 봇 정보 확인
            bot_info = self.mastodon_manager.get_bot_info()
            if bot_info:
                bot_username = bot_info.get('username', 'Unknown')
                bot_toots = bot_info.get('statuses_count', 0)
                bot_followers = bot_info.get('followers_count', 0)
                logger.info(f"✅ 마스토돈 API 연결 성공 (@{bot_username}, {bot_toots}툿, {bot_followers}팔로워)")
            else:
                logger.warning("⚠️ 봇 정보 조회 실패, 연결은 성공")
            
            return True
            
        except Exception as e:
            logger.error(f"❌ 마스토돈 API 연결 실패: {e}")
            return False
    
    def _connect_google_sheets(self) -> bool:
        """Google Sheets 연결"""
        try:
            logger.info("📊 Google Sheets 연결 중...")
            
            self.sheets_manager = get_sheets_manager()
            
            # 워크시트 목록 확인으로 기본 연결만 테스트
            worksheets = self.sheets_manager.get_worksheet_names()
            
            if not worksheets:
                logger.error("❌ Google Sheets 연결 실패: 워크시트를 찾을 수 없습니다")
                return False
            
            logger.info(f"✅ Google Sheets 연결 성공")
            logger.info(f"   - 시트 ID: {config.GOOGLE_SHEETS_ID[:20]}...")
            logger.info(f"   - 워크시트: {len(worksheets)}개 발견: {worksheets[:5]}...")
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Google Sheets 연결 실패: {e}")
            return False
    
    def _initialize_system_components(self) -> bool:
        """시스템 컴포넌트 초기화"""
        try:
            logger.info("🔧 시스템 컴포넌트 초기화 중...")
            
            # 스토리 루프 매니저 초기화
            self.story_loop_manager = get_story_loop_manager()
            if not self.story_loop_manager.initialize(self.sheets_manager, self.mastodon_manager):
                logger.error("❌ 스토리 루프 매니저 초기화 실패")
                return False
            logger.info("✅ 스토리 루프 매니저 초기화 완료")
            
            # 알림 처리기 초기화
            self.notification_handler = get_notification_handler()
            if not self.notification_handler.initialize(self.mastodon_manager, self.story_loop_manager):
                logger.error("❌ 알림 처리기 초기화 실패")
                return False
            logger.info("✅ 알림 처리기 초기화 완료")
            
            return True
            
        except Exception as e:
            logger.error(f"❌ 시스템 컴포넌트 초기화 실패: {e}")
            return False
    
    def _send_startup_notification(self) -> None:
        """시작 알림 전송 (비활성화됨)"""
        return  # 시작 알림 비활성화
    
    def _send_shutdown_notification(self, planned: bool = True) -> None:
        """종료 알림 전송 (비활성화됨)"""
        return  # 종료 알림 비활성화
    
    def _send_emergency_shutdown_notification(self, error_message: str) -> None:
        """비정상 종료 알림 전송 (관리자 DM)"""
        try:
            if not getattr(config, 'ERROR_NOTIFICATION_ENABLED', True) or not self.mastodon_manager:
                return
            
            current_time = default_parser.get_current_datetime()
            
            # 관리자 DM 전송
            admin_id = getattr(config, 'SYSTEM_ADMIN_ID', None)
            if admin_id:
                admin_message = (
                    f"@{admin_id} 🚨 봇 시스템 비정상 종료\n\n"
                    f"시간: {format_datetime_korean(current_time)}\n"
                    f"오류: {error_message[:200]}...\n\n"
                    f"즉시 확인이 필요합니다."
                )
                
                admin_result = self.mastodon_manager.post_toot(
                    content=admin_message,
                    visibility='direct',  # DM으로 전송
                    validate_content=False
                )
                
                if admin_result.success:
                    logger.info("✅ 관리자 비상 알림 DM 전송 완료")
                else:
                    logger.error(f"❌ 관리자 DM 전송 실패: {admin_result.error_message}")
            
        except Exception as e:
            logger.error(f"❌ 비정상 종료 알림 전송 실패: {e}")
    
    def _run_daemon_mode(self) -> int:
        """데몬 모드 실행"""
        try:
            logger.info("🚀 데몬 모드로 실행 시작...")
            
            # 스토리 루프 매니저 시작
            self.story_loop_manager.start()
            
            # 알림 모니터링 시작
            if not self.notification_handler.start_monitoring():
                logger.error("❌ 알림 모니터링 시작 실패")
                return 1
            
            self.is_running = True
            logger.info("✅ 스토리 봇이 실행되었습니다. 알림을 기다리는 중...")
            
            # 메인 루프 (중단 신호까지 대기)
            try:
                while self.is_running and not self.shutdown_requested:
                    time.sleep(1)
            except KeyboardInterrupt:
                logger.info("👋 사용자 요청으로 데몬 모드 종료")
            
            self.is_running = False
            
            logger.info("✅ 데몬 모드 정상 종료")
            self._send_shutdown_notification(planned=True)
            return 0
            
        except Exception as e:
            self.is_running = False
            logger.error(f"❌ 데몬 모드 실행 실패: {e}")
            return 1
    
    def _run_background_mode(self) -> int:
        """백그라운드 모드 실행"""
        try:
            logger.info("🔄 백그라운드 모드로 실행 시작...")
            
            # 스토리 루프 매니저 시작
            self.story_loop_manager.start()
            
            # 알림 모니터링 시작
            if not self.notification_handler.start_monitoring():
                logger.error("❌ 알림 모니터링 시작 실패")
                return 1
            
            self.is_running = True
            
            logger.info("백그라운드 모드로 실행 중... Ctrl+C로 종료")
            
            # 메인 스레드는 대기
            try:
                while self.is_running and not self.shutdown_requested:
                    time.sleep(1)
            except KeyboardInterrupt:
                logger.info("👋 사용자 요청으로 백그라운드 모드 종료")
            
            self._send_shutdown_notification(planned=True)
            return 0
            
        except Exception as e:
            logger.error(f"❌ 백그라운드 모드 실행 실패: {e}")
            return 1
    
    def _run_test_mode(self) -> int:
        """테스트 모드 실행"""
        try:
            logger.info("🧪 테스트 모드 실행...")
            
            # 각 컴포넌트 테스트
            test_results = {
                '시트 연결': test_sheets_connection(),
                '마스토돈 연결': check_mastodon_connection(),
                '스토리 루프 매니저': self._test_story_loop_manager(),
                '알림 처리기': self._test_notification_handler()
            }
            
            # 결과 출력
            logger.info("📊 테스트 결과:")
            all_passed = True
            for test_name, result in test_results.items():
                status = "✅ 통과" if result else "❌ 실패"
                logger.info(f"   - {test_name}: {status}")
                if not result:
                    all_passed = False
            
            if all_passed:
                logger.info("🎉 모든 테스트가 통과했습니다!")
                return 0
            else:
                logger.error("💥 일부 테스트가 실패했습니다.")
                return 1
                
        except Exception as e:
            logger.error(f"❌ 테스트 모드 실행 실패: {e}")
            return 1
    
    def _test_story_loop_manager(self) -> bool:
        """스토리 루프 매니저 테스트"""
        try:
            if not self.story_loop_manager:
                return False
            
            # 간단한 상태 확인
            stats = self.story_loop_manager.get_stats()
            logger.info(f"스토리 루프 매니저 상태: {stats}")
            return True
            
        except Exception as e:
            logger.error(f"스토리 루프 매니저 테스트 실패: {e}")
            return False
    
    def _test_notification_handler(self) -> bool:
        """알림 처리기 테스트"""
        try:
            if not self.notification_handler:
                return False
            
            # 간단한 상태 확인
            stats = self.notification_handler.get_stats()
            logger.info(f"알림 처리기 상태: {stats}")
            return True
            
        except Exception as e:
            logger.error(f"알림 처리기 테스트 실패: {e}")
            return False
    
    def _signal_handler(self, signum, frame):
        """시그널 핸들러 (Ctrl+C 등)"""
        logger.info(f"🛑 종료 시그널 수신 ({signum})")
        self.shutdown_requested = True
        self.is_running = False
        
        # 알림 처리기 중지
        if self.notification_handler:
            self.notification_handler.stop_monitoring()
        
        # 스토리 루프 매니저 중지
        if self.story_loop_manager:
            self.story_loop_manager.stop()
    
    def _cleanup(self) -> None:
        """정리 작업"""
        try:
            logger.info("🧹 정리 작업 시작...")
            
            # 알림 처리기 중지
            if self.notification_handler:
                try:
                    self.notification_handler.stop_monitoring()
                    logger.info("알림 처리기 중지 완료")
                except Exception as e:
                    logger.warning(f"알림 처리기 중지 실패: {e}")
            
            # 스토리 루프 매니저 중지
            if self.story_loop_manager:
                try:
                    self.story_loop_manager.stop()
                    logger.info("스토리 루프 매니저 중지 완료")
                except Exception as e:
                    logger.warning(f"스토리 루프 매니저 중지 실패: {e}")
            
            # 통계 출력
            try:
                uptime = time.time() - self.startup_time
                uptime_formatted = f"{uptime:.1f}초"
                
                logger.info("📊 최종 통계:")
                logger.info(f"   - 총 가동시간: {uptime_formatted}")
                
                if self.story_loop_manager:
                    story_stats = self.story_loop_manager.get_stats()
                    logger.info(f"   - 스토리 세션: {story_stats.get('total_sessions', 0)}개")
                    logger.info(f"   - 완료된 세션: {story_stats.get('completed_sessions', 0)}개")
                    logger.info(f"   - 총 툿 포스팅: {story_stats.get('total_posts', 0)}개")
                
                if self.notification_handler:
                    notif_stats = self.notification_handler.get_stats()
                    logger.info(f"   - 처리된 알림: {notif_stats.get('total_notifications', 0)}개")
                    logger.info(f"   - 실행된 명령어: {notif_stats.get('processed_commands', 0)}개")
                    
            except Exception as e:
                logger.warning(f"통계 출력 실패: {e}")
            
            logger.info("✅ 정리 작업 완료")
            
        except Exception as e:
            logger.error(f"❌ 정리 작업 중 오류: {e}")


def create_argument_parser() -> argparse.ArgumentParser:
    """명령행 인수 파서 생성"""
    parser = argparse.ArgumentParser(
        description='마스토돈 스토리 스크립트 자동 출력 봇',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
실행 모드:
  daemon      포그라운드에서 실행 (기본값)
  background  백그라운드에서 실행
  test        모든 컴포넌트 테스트

명령어 형식:
  [스토리/워크시트명] 또는 [스진/워크시트명] 또는 [스토리진행/워크시트명]
  
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
    print("🤖 마스토돈 스토리 스크립트 자동 출력 봇 v1.0")
    print("📅 개발 버전 - 2025.08")
    print("🔧 Python 기반 마스토돈 봇")
    print("📊 Google Sheets 연동")
    print("🎬 스토리 스크립트 자동 송출")
    print("📢 Direct 메시지 명령어 처리")


def show_status():
    """현재 봇 상태 출력"""
    try:
        print("📊 마스토돈 스토리 스크립트 자동 출력 봇 상태")
        print("=" * 50)
        
        # 설정 상태
        is_valid, _ = validate_startup_config()
        print(f"설정 상태: {'✅ 정상' if is_valid else '❌ 오류'}")
        
        # 마스토돈 연결 상태
        mastodon_ok = check_mastodon_connection()
        print(f"마스토돈 연결: {'✅ 정상' if mastodon_ok else '❌ 연결 실패'}")
        
        # 시트 연결 상태
        try:
            sheets_ok = test_sheets_connection()
            print(f"Google Sheets: {'✅ 정상' if sheets_ok else '❌ 연결 실패'}")
            
            if sheets_ok:
                sheets_manager = get_sheets_manager()
                worksheets = sheets_manager.get_worksheet_names()
                print(f"워크시트: {len(worksheets)}개 발견")
        except Exception:
            print("Google Sheets: ❌ 연결 실패")
        
        # 스토리 시스템 상태
        try:
            story_manager = get_story_loop_manager()
            story_stats = story_manager.get_stats()
            print(f"스토리 루프 매니저: ✅ 정상 (활성 세션: {story_stats.get('active_sessions', 0)}개)")
        except Exception:
            print("스토리 루프 매니저: ❌ 오류")
        
        try:
            notif_handler = get_notification_handler()
            notif_stats = notif_handler.get_stats()
            monitoring_status = "실행 중" if notif_stats.get('is_monitoring', False) else "중지됨"
            print(f"알림 처리기: ✅ 정상 ({monitoring_status})")
        except Exception:
            print("알림 처리기: ❌ 오류")
        
        print("=" * 50)
        
    except Exception as e:
        print(f"❌ 상태 확인 실패: {e}")


def perform_config_check():
    """설정 검증 수행"""
    print("🔧 설정 검증 시작...")
    print("=" * 50)
    
    # 환경 설정 검증
    is_valid, validation_summary = validate_startup_config()
    print("📋 환경 설정 검증:")
    print(validation_summary)
    
    print("=" * 50)
    
    print(f"🎯 전체 검증 결과: {'✅ 통과' if is_valid else '❌ 실패'}")
    
    return is_valid


def main() -> int:
    """메인 엔트리 포인트"""
    global logger
    
    # 로깅 시스템 초기화 (가장 먼저)
    setup_logging()
    logger = get_logger(__name__)
    
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
        bot = MastodonStoryBot()
        return bot.run(mode=args.mode)
        
    except KeyboardInterrupt:
        if logger:
            logger.info("👋 사용자 요청으로 프로그램을 종료합니다.")
        else:
            print("\n👋 사용자 요청으로 프로그램을 종료합니다.")
        return 0
    except Exception as e:
        error_msg = f"💥 프로그램 시작 실패: {e}"
        if logger:
            logger.critical(error_msg, exc_info=True)
        else:
            print(error_msg)
            traceback.print_exc()
        return 1


if __name__ == '__main__':
    # 전역 예외 핸들러 설정
    def handle_exception(exc_type, exc_value, exc_traceback):
        """처리되지 않은 예외 핸들러"""
        if issubclass(exc_type, KeyboardInterrupt):
            # KeyboardInterrupt는 정상적인 종료로 처리
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        
        # 다른 예외들은 로그에 기록
        if logger:
            logger.critical(
                "처리되지 않은 예외 발생",
                exc_info=(exc_type, exc_value, exc_traceback)
            )
        else:
            # 로거가 없는 경우 기본 처리
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
    
    sys.excepthook = handle_exception
    
    # 프로그램 실행
    exit_code = main()
    sys.exit(exit_code)
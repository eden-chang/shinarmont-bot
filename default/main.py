"""
마스토돈 자동봇 메인 실행 파일
리팩토링된 버전 - 모든 새로운 모듈들을 통합하여 실행합니다.
"""

import os
import sys
import signal
import time
from typing import Optional

# Windows 콘솔 UTF-8 인코딩 설정
if sys.platform == 'win32':
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')

# 경로 설정 (VM 환경 대응)
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    import mastodon
    from config.settings import config
    from config.validators import validate_startup_config
    from utils.logging_config import setup_logging, logger, bot_logger
    from utils.error_handling import setup_global_exception_handler
    from utils.sheets_operations import SheetsManager
    from utils.credential_pool import (
        PURPOSE_INVESTIGATION, PURPOSE_MAIN, PURPOSE_SYSTEM, get_pool,
    )
    from utils.cache_manager import bot_cache, warmup_cache
    from handlers.stream_handler import StreamManager,validate_stream_dependencies
    from handlers.command_router import initialize_command_router
    from utils.api_retry import api_retry
    from utils.operation_period import (
        check_operation_period, post_expiry_toot, set_expired,
        is_before_operation_start, OperationPeriodMonitor
    )
except ImportError as e:
    print(f"❌ 필수 모듈 임포트 실패: {e}")
    print("필요한 패키지가 설치되어 있는지 확인해주세요.")
    sys.exit(1)


class BotApplication:
    """
    마스토돈 봇 애플리케이션 클래스
    
    봇의 전체 생명주기를 관리합니다:
    - 초기화 및 설정 검증
    - 마스토돈 API 연결
    - Google Sheets 연결
    - 명령어 시스템 초기화
    - 스트리밍 시작 및 관리
    """
    
    def __init__(self):
        """BotApplication 초기화"""
        self.api: Optional[mastodon.Mastodon] = None
        self.sheets_manager: Optional[SheetsManager] = None
        self.investigation_sheets_manager: Optional[SheetsManager] = None
        self.system_sheets_manager: Optional[SheetsManager] = None
        self.stream_manager: Optional[StreamManager] = None
        self.operation_monitor: Optional[OperationPeriodMonitor] = None
        self.shinarmont_scheduler = None
        self.is_running = False
        self._shutdown_requested = False
        self.startup_time = time.time()

        # 시그널 핸들러 설정 (Ctrl+C 처리)
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def run(self) -> int:
        """
        봇 애플리케이션 실행

        Returns:
            int: 종료 코드 (0: 정상, 1: 오류)
        """
        try:
            logger.info("=" * 60)

            # 멀티 봇 모드 확인 및 로깅
            if config.is_multi_bot_mode():
                logger.info(f"🤖 마스토돈 자동봇 시작 (멀티 봇 모드)")
                logger.info(f"   봇 ID: {config.BOT_ID}")
                logger.info(f"   봇 이름: {config.BOT_NAME}")
                logger.info(f"   명령어 필터: {', '.join(config.COMMAND_FILTER) if config.COMMAND_FILTER else '없음'}")
            else:
                logger.info("🤖 마스토돈 자동봇 시작 (단일 봇 모드)")

            logger.info("=" * 60)
            
            # 1. 기본 설정 및 검증
            if not self._initialize_basic_systems():
                return 1
            
            # 2. 외부 서비스 연결
            if not self._connect_external_services():
                return 1

            # 2.5. 가동 기간 확인
            if not self._check_operation_period():
                return 0  # 만료 시 정상 종료

            # 3. 봇 시스템 초기화
            if not self._initialize_bot_systems():
                return 1

            # 4. 스트리밍 시작 (가동 기간 모니터링 포함)
            self._start_operation_monitor()
            self._start_shinarmont_scheduler()
            self._reconcile_stat_messages()
            if not self._start_streaming():
                return 1
            
            # 정상 종료
            logger.info("🎉 봇이 정상적으로 종료되었습니다.")
            return 0
            
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
            logger.info("🔧 기본 시스템 초기화 중...")
            
            # 전역 예외 핸들러 설정
            setup_global_exception_handler()
            
            # 환경 설정 검증
            is_valid, validation_summary = validate_startup_config()
            if not is_valid:
                logger.error("❌ 설정 검증 실패:")
                logger.error(validation_summary)
                return False
            
            logger.info("✅ 설정 검증 완료")
            
            # 스트리밍 의존성 검증
            deps_valid, deps_errors = validate_stream_dependencies()
            if not deps_valid:
                logger.error("❌ 스트리밍 의존성 검증 실패:")
                for error in deps_errors:
                    logger.error(f"  - {error}")
                return False
            
            logger.info("✅ 의존성 검증 완료")
            return True
            
        except Exception as e:
            logger.error(f"❌ 기본 시스템 초기화 실패: {e}")
            return False
    
    def _check_operation_period(self) -> bool:
        """
        가동 기간 확인

        Returns:
            bool: 가동 가능하면 True, 만료되어 종료해야 하면 False
        """
        is_active, status_msg = check_operation_period(
            config.BOT_OPERATION_START,
            config.BOT_OPERATION_END
        )
        logger.info(f"📅 가동 기간 상태: {status_msg}")

        if is_active:
            return True

        if is_before_operation_start(config.BOT_OPERATION_START):
            # 가동 시작일 이전 → 봇은 켜두고 시작 시각까지 멘션을 무시
            # (별도 툿/멘션 게시 없이 조용히 대기)
            logger.info("가동 시작일 이전 - 봇을 켜두고 시작 시각까지 멘션을 무시합니다.")
            return True

        if config.BOT_OPERATION_END:
            # 가동 기간 만료 → 만료 툿 게시 후 종료
            logger.warning("가동 기간 만료 - 만료 툿 게시 후 종료합니다.")
            set_expired()
            if self.api:
                post_expiry_toot(self.api)
            return False

        return True

    def _start_operation_monitor(self) -> None:
        """가동 기간 모니터링 시작 (백그라운드 스레드)"""
        if not config.BOT_OPERATION_END:
            return

        self.operation_monitor = OperationPeriodMonitor(
            api=self.api,
            end_str=config.BOT_OPERATION_END,
            stream_manager=self.stream_manager
        )
        self.operation_monitor.start()

    def _start_shinarmont_scheduler(self) -> None:
        """
        시너몬트 스케줄러 시작 (백그라운드).

        `SCHEDULER_OWNER=True`인 슬롯에서만 기동하여 멀티 슬롯 중복 실행을 방지한다.
        `utils.shinarmont_scheduler` 모듈은 유틸 단계에서 생성되므로, 아직 없으면 조용히 건너뛴다
        (인프라 단계에서 순환/부재로 기동이 깨지지 않도록 보호).
        """
        if not getattr(config, 'SCHEDULER_OWNER', False):
            return

        try:
            from utils import shinarmont_scheduler
        except ImportError as e:
            logger.info(f"시너몬트 스케줄러 모듈 미탑재 - 스케줄러 기동 건너뜀: {e}")
            return

        try:
            self.shinarmont_scheduler = shinarmont_scheduler.start(
                owner=True,
                sheets_manager=self.sheets_manager,
                system_sheets_manager=self.system_sheets_manager,
                investigation_sheets_manager=self.investigation_sheets_manager,
                api=self.api,
            )
            logger.info("✅ 시너몬트 스케줄러 시작 완료")
        except TypeError:
            # start() 시그니처가 단순형(owner만)인 경우 폴백
            try:
                self.shinarmont_scheduler = shinarmont_scheduler.start(True)
                logger.info("✅ 시너몬트 스케줄러 시작 완료 (단순 시그니처)")
            except Exception as e:
                logger.error(f"❌ 시너몬트 스케줄러 시작 실패: {e}")
        except Exception as e:
            logger.error(f"❌ 시너몬트 스케줄러 시작 실패: {e}")

    def _reconcile_stat_messages(self) -> None:
        """봇 시작 시 미발송 이성/건강 경고 문구 재확인·재전송(전 캐릭터 스윕).

        봇이 중간에 끊겨 DM이 못 나갔을 수 있으므로, 시작 시 관리 시트의 모든
        캐릭터에 대해 현재 이성/건강값과 game_state 발송 플래그를 대조해, 아직
        안 나갔는데 지금 임계 이하인 문구만 DM으로 보낸다. game_state 플래그로
        재발송을 막으므로 매 시작 호출해도 중복 발송되지 않는다.

        `SCHEDULER_OWNER=True` 슬롯에서만 실행하여 멀티 슬롯 중복 발송을 방지한다.
        """
        if not getattr(config, 'SCHEDULER_OWNER', False):
            return
        try:
            # DM 전송기가 준비되지 않았으면 큐잉해도 드롭되므로, 스윕을 건너뛴다
            # (플래그를 세우지 않으므로 다음 시작 때 다시 시도된다).
            from utils.dm_sender import get_dm_sender
            if get_dm_sender() is None:
                logger.warning("⚠️ DM 전송기 미초기화 - 시작 재전송 스윕 건너뜀(다음 시작에 재시도)")
                return

            from utils import stat_gate
            sent = stat_gate.reconcile_stat_messages(
                self.sheets_manager, self.system_sheets_manager, self.api
            )
            if sent:
                logger.info(f"✅ 시작 재전송 스윕 - 경고 문구 {sent}건 큐잉(스트리밍 시작 후 발송)")
        except Exception as e:
            logger.error(f"❌ 시작 재전송 스윕 실패: {e}", exc_info=True)

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
    
    @api_retry(max_retries=3, delay_seconds=60)
    def _connect_mastodon_api(self) -> bool:
        """마스토돈 API 연결"""
        try:
            logger.info("📡 마스토돈 API 연결 중...")
            
            # 액세스 토큰이 있으면 직접 사용 (CLIENT_ID, CLIENT_SECRET 불필요)
            if config.MASTODON_ACCESS_TOKEN:
                self.api = mastodon.Mastodon(
                    access_token=config.MASTODON_ACCESS_TOKEN,
                    api_base_url=config.MASTODON_API_BASE_URL,
                    version_check_mode='none'
                )
            else:
                # 액세스 토큰이 없는 경우만 CLIENT_ID, CLIENT_SECRET 사용
                self.api = mastodon.Mastodon(
                    client_id=config.MASTODON_CLIENT_ID,
                    client_secret=config.MASTODON_CLIENT_SECRET,
                    access_token=config.MASTODON_ACCESS_TOKEN,
                    api_base_url=config.MASTODON_API_BASE_URL,
                    version_check_mode='none'
                )
            
            # 연결 테스트
            account_info = self.api.me()
            bot_username = account_info.get('username', 'Unknown')
            
            logger.info(f"✅ 마스토돈 API 연결 성공 (@{bot_username})")
            return True
            
        except Exception as e:
            logger.error(f"❌ 마스토돈 API 연결 실패: {e}")
            return False
    
    def _connect_google_sheets(self) -> bool:
        """Google Sheets 연결"""
        try:
            logger.info("📊 Google Sheets 연결 중...")
            
            # purpose를 주면 크레덴셜 풀이 용도별 계정을 고르고,
            # 쿼터(429)에 걸리면 다른 프로젝트의 계정을 빌려 재시도한다.
            self.sheets_manager = SheetsManager(
                sheet_id=config.SHEET_ID,
                purpose=PURPOSE_MAIN,
            )
            
            # 연결 테스트 및 구조 검증
            validation_result = self.sheets_manager.validate_sheet_structure()
            
            if not validation_result['valid']:
                logger.error("❌ 시트 구조 검증 실패:")
                for error in validation_result['errors']:
                    logger.error(f"  - {error}")
                return False
            
            if validation_result['warnings']:
                logger.warning("⚠️ 시트 구조 경고:")
                for warning in validation_result['warnings']:
                    logger.warning(f"  - {warning}")
            
            logger.info(f"✅ Google Sheets 연결 성공 (시트: {len(validation_result['worksheets_found'])}개)")

            # 조사 기능용 보조 시트 연결 (investigate 봇 또는 INVESTIGATION_ENABLED=True)
            self._connect_investigation_sheet()

            # 시스템 시트 연결 (행동로그/투표/고발/추적/소문/이성/설정 등)
            self._connect_system_sheet()

            return True

        except Exception as e:
            logger.error(f"❌ Google Sheets 연결 실패: {e}")
            return False

    def _connect_investigation_sheet(self) -> None:
        """
        조사 기능용 보조 스프레드시트 연결 (선택적)

        `INVESTIGATION_ENABLED`가 True이고 `INVESTIGATION_SHEET_ID`가 설정되어 있으면
        별도의 `SheetsManager` 인스턴스를 생성합니다. 조건 불충족 시 조용히 건너뜁니다.
        """
        if not getattr(config, 'INVESTIGATION_ENABLED', False):
            return

        investigation_sheet_id = getattr(config, 'INVESTIGATION_SHEET_ID', '')
        if not investigation_sheet_id:
            logger.warning(
                "⚠️ INVESTIGATION_ENABLED=True이지만 INVESTIGATION_SHEET_ID가 비어있습니다. 조사 기능 비활성화."
            )
            return

        try:
            logger.info("📊 조사 스프레드시트 연결 중...")
            self.investigation_sheets_manager = SheetsManager(
                sheet_id=investigation_sheet_id,
                purpose=PURPOSE_INVESTIGATION,
            )
            # 조사 시트는 [장소 목록]에서 장소 수만큼 읽으므로 짧은 TTL 캐시를 둔다.
            # (개방 여부·스탯 등 정합성이 중요한 읽기는 호출측이 use_cache=False로 재검증)
            self.investigation_sheets_manager.set_data_cache_ttl(
                getattr(config, 'INVESTIGATION_CACHE_TTL', 60)
            )
            # 연결 지연 평가: 속성 접근 시점에 실패 감지
            _ = self.investigation_sheets_manager.spreadsheet
            logger.info("✅ 조사 스프레드시트 연결 성공")
        except Exception as e:
            logger.error(f"❌ 조사 스프레드시트 연결 실패 - 조사 기능 비활성화: {e}")
            self.investigation_sheets_manager = None

    def _connect_system_sheet(self) -> None:
        """
        시스템 기능용 보조 스프레드시트 연결 (선택적)

        `SYSTEM_SHEET_ENABLED`가 True이고 `SYSTEM_SHEET_ID`가 설정되어 있으면
        별도의 `SheetsManager` 인스턴스를 생성합니다. 조건 불충족 시 조용히 건너뜁니다.
        (행동로그/투표/고발/부탁지령/추적/소문/이성/설정 등 시스템 시트 담당)
        """
        if not getattr(config, 'SYSTEM_SHEET_ENABLED', False):
            return

        system_sheet_id = getattr(config, 'SYSTEM_SHEET_ID', '')
        if not system_sheet_id:
            logger.warning(
                "⚠️ SYSTEM_SHEET_ENABLED=True이지만 SYSTEM_SHEET_ID가 비어있습니다. 시스템 시트 기능 비활성화."
            )
            return

        try:
            logger.info("📊 시스템 스프레드시트 연결 중...")
            self.system_sheets_manager = SheetsManager(
                sheet_id=system_sheet_id,
                purpose=PURPOSE_SYSTEM,
            )
            # 연결 지연 평가: 속성 접근 시점에 실패 감지
            _ = self.system_sheets_manager.spreadsheet
            logger.info("✅ 시스템 스프레드시트 연결 성공")
        except Exception as e:
            logger.error(f"❌ 시스템 스프레드시트 연결 실패 - 시스템 시트 기능 비활성화: {e}")
            self.system_sheets_manager = None

    def _log_discovered_commands(self) -> None:
        """발견된 명령어 목록을 로그로 출력"""
        try:
            from commands.registry import get_registry

            registry = get_registry()
            all_commands = registry._commands

            if not all_commands:
                logger.warning("발견된 명령어가 없습니다.")
                return

            # 패키지별로 그룹화
            by_package = {}
            for cmd_name, registered_cmd in all_commands.items():
                pkg = registered_cmd.metadata.command_package
                if pkg not in by_package:
                    by_package[pkg] = []

                # 대표 키워드: 데코레이터 name(메타데이터 name)을 우선 사용
                aliases = registered_cmd.metadata.aliases
                logger.debug(f"명령어 '{cmd_name}': aliases={aliases[:3] if aliases else []}")
                display_keyword = registered_cmd.metadata.name or cmd_name

                by_package[pkg].append({
                    'name': cmd_name,
                    'display_keyword': display_keyword,
                    'class': registered_cmd.command_class.__name__,
                    'aliases': aliases
                })

            # 총 개수 로그
            logger.info(f"📋 총 {len(all_commands)}개의 명령어가 발견되었습니다.")

            # 각 패키지별 출력
            for pkg_name in ['default', 'store', 'stats', 'system', 'investigate', 'shinarmont']:
                if pkg_name in by_package:
                    cmds = by_package[pkg_name]
                    logger.info(f"  [{pkg_name.upper()}] {len(cmds)}개")
                    for cmd in sorted(cmds, key=lambda x: x['display_keyword']):
                        logger.info(f"    - [{cmd['display_keyword']}] {cmd['class']}")

        except Exception as e:
            logger.warning(f"명령어 목록 로그 출력 실패: {e}")

    def _initialize_bot_systems(self) -> bool:
        """봇 시스템 초기화"""
        try:
            logger.info("🤖 봇 시스템 초기화 중...")
            
            # 명령어 라우터 초기화 (API + 보조 의존성 전달)
            router_kwargs = {}
            if self.investigation_sheets_manager is not None:
                router_kwargs['investigation_sheets_manager'] = self.investigation_sheets_manager
            if self.system_sheets_manager is not None:
                router_kwargs['system_sheets_manager'] = self.system_sheets_manager
            command_router = initialize_command_router(
                self.sheets_manager, self.api, **router_kwargs
            )
            logger.info("✅ 명령어 라우터 초기화 완료")

            # 발견된 명령어 목록 로그 출력
            self._log_discovered_commands()
            
            # 캐시 워밍업
            try:
                warmup_cache(self.sheets_manager)
                logger.info("✅ 캐시 워밍업 완료")
            except Exception as e:
                logger.warning(f"⚠️ 캐시 워밍업 실패 (계속 진행): {e}")

            # 사용자 명단 데이터 사전 로드
            try:
                from models.user import user_manager
                user_manager.set_sheets_manager(self.sheets_manager)
                preload_success = user_manager.preload_user_data()
                if preload_success:
                    logger.info("✅ 사용자 명단 사전 로드 완료")
                else:
                    logger.warning("⚠️ 사용자 명단 사전 로드 실패 (계속 진행)")
            except Exception as e:
                logger.warning(f"⚠️ 사용자 명단 사전 로드 오류 (계속 진행): {e}")
            
            # DM 지원 스트림 매니저 생성
            try:
                from handlers.stream_handler import initialize_stream_with_dm
                self.stream_manager = initialize_stream_with_dm(self.api, self.sheets_manager)
                logger.info("✅ DM 지원 스트림 매니저 생성 완료")
            except ImportError:
                # initialize_stream_with_dm 함수가 없는 경우 기본 스트림 매니저 사용
                logger.warning("⚠️ DM 지원 함수를 찾을 수 없어 기본 스트림 매니저 사용")
                self.stream_manager = StreamManager(self.api, self.sheets_manager)
                logger.info("✅ 기본 스트림 매니저 생성 완료")
            except Exception as e:
                logger.error(f"❌ DM 지원 스트림 매니저 생성 실패, 기본 매니저로 전환: {e}")
                self.stream_manager = StreamManager(self.api, self.sheets_manager)
                logger.info("✅ 기본 스트림 매니저 생성 완료")
            
            # 명령어 검증
            validation_result = command_router.validate_all_systems()
            if not validation_result['overall_valid']:
                logger.warning("⚠️ 일부 명령어에 문제가 있습니다:")
                for error in validation_result['errors']:
                    logger.warning(f"  - {error}")
            else:
                logger.info("✅ 모든 명령어 검증 완료")
            
            return True
            
        except Exception as e:
            logger.error(f"❌ 봇 시스템 초기화 실패: {e}")
            return False
    
    def _start_streaming(self) -> bool:
        """스트리밍 시작"""
        try:
            logger.info("🚀 마스토돈 스트리밍 시작...")
            
            # 스트리밍 시작 (블로킹)
            self.is_running = True
            success = self.stream_manager.start_streaming(max_retries=config.MAX_RETRIES)
            self.is_running = False
            
            if success:
                logger.info("✅ 스트리밍이 정상적으로 종료되었습니다.")
                return True
            else:
                logger.error("❌ 스트리밍 시작 실패")
                return False
                
        except Exception as e:
            self.is_running = False
            logger.error(f"❌ 스트리밍 중 오류 발생: {e}")
            return False
    
    def _send_startup_notification(self) -> None:
        """시작 알림 전송"""
        try:
            uptime_seconds = time.time() - self.startup_time
            startup_message = (
                f"🤖 자동봇이 시작되었습니다!\n"
                f"📊 명령어 시스템 준비 완료\n"
                f"🔧 시작 시간: {uptime_seconds:.2f}초"
            )
            
            logger.info("✅ 시작 알림 전송 완료")
            
        except Exception as e:
            logger.warning(f"⚠️ 시작 알림 전송 실패: {e}")
    
    def _send_emergency_notification(self, error_message: str) -> None:
        """긴급 상황 알림 전송"""
        @api_retry(max_retries=3, delay_seconds=60)
        def _send_status(status_text, visibility_level):
            return self.api.status_post(
                status=status_text,
                visibility=visibility_level
            )
        
        try:
            if not self.api:
                return
            
            # 사용자 공지
            _send_status(
                "자동봇이 오류로 인해 중지되었습니다. 복구 작업 중입니다.",
                'unlisted'
            )
            
            # 관리자 알림
            if config.SYSTEM_ADMIN_ID:
                # 설정된 글자수 제한에서 여유분을 두고 오류 메시지 자르기
                max_error_length = (config.MAX_MESSAGE_LENGTH if config else 500) - 50  # 50자 여유분
                admin_message = f"@{config.SYSTEM_ADMIN_ID} 🚨 봇 시스템 오류\n{error_message[:max_error_length]}"
                _send_status(
                    admin_message,
                    'direct'
                )
            
            logger.info("✅ 긴급 알림 전송 완료")
            
        except Exception as e:
            logger.error(f"❌ 긴급 알림 전송 실패: {e}")
    
    def _signal_handler(self, signum, frame):
        """시그널 핸들러 (Ctrl+C 등)"""
        if self._shutdown_requested:
            # 두 번째 시그널 - 강제 종료
            logger.info("프로그램을 강제 종료합니다...")
            sys.exit(1)

        self._shutdown_requested = True
        logger.info(f"종료 시그널 수신 ({signum}). 정리 작업 진행 중... (다시 Ctrl+C로 강제 종료)")
        self.is_running = False

        if self.stream_manager:
            self.stream_manager.stop_streaming()
    
    def _cleanup(self) -> None:
        """정리 작업"""
        try:
            logger.info("🧹 정리 작업 시작...")

            # 가동 기간 모니터 중지
            if self.operation_monitor:
                self.operation_monitor.stop()

            # 스트리밍 중지
            if self.stream_manager:
                self.stream_manager.stop_streaming()
            
            # 통계 기능 제거됨 - 더 이상 사용하지 않음
            
            # 캐시 정리
            try:
                bot_cache.cleanup_all_expired()
                logger.info("🗑️ 캐시 정리 완료")
            except Exception as e:
                logger.warning(f"캐시 정리 실패: {e}")
            
            logger.info("✅ 정리 작업 완료")
            
        except Exception as e:
            logger.error(f"❌ 정리 작업 중 오류: {e}")
    
    def get_status(self) -> dict:
        """애플리케이션 상태 반환 (개발/디버깅용)"""
        status = {
            'is_running': self.is_running,
            'startup_time': self.startup_time,
            'uptime_seconds': time.time() - self.startup_time,
            'api_connected': self.api is not None,
            'sheets_connected': self.sheets_manager is not None,
            'stream_manager_ready': self.stream_manager is not None,
        }
        
        # 스트림 매니저 상태 추가
        if self.stream_manager:
            status['stream_status'] = self.stream_manager.get_status()
            # handler_stats 제거됨 - 통계 기능 사용 안함
        
        return status


def main() -> int:
    """메인 엔트리 포인트"""
    # 로깅 시스템 초기화
    setup_logging()
    
    try:
        # 봇 애플리케이션 생성 및 실행
        app = BotApplication()
        return app.run()
        
    except Exception as e:
        print(f"💥 애플리케이션 시작 실패: {e}")
        return 1


def show_version():
    """버전 정보 출력"""
    print("🤖 마스토돈 자동봇 v2.1")
    print("🔧 모듈형 아키텍처")
    print("📊 Google Sheets 연동")
    print("⏰ 자동 일일 리셋 (KST 0시)")


def show_help():
    """도움말 출력"""
    print("🤖 마스토돈 자동봇 사용법")
    print("")
    print("실행:")
    print("  python main.py              # 봇 시작")
    print("  python main.py --version    # 버전 정보")
    print("  python main.py --help       # 이 도움말")
    print("")
    print("새로운 기능:")
    print("  ⏰ 자동 일일 리셋          # 매일 KST 0시에 성공수 초기화")
    print("  🎯 일일 성공 제한          # 하루 최대 2번까지 성공 가능")
    print("")
    print("환경 설정:")
    print("  .env 파일을 생성하거나 환경 변수를 설정하세요.")
    print("  .env.example 파일을 참고하세요.")
    print("")
    print("필수 환경 변수:")
    print("  MASTODON_CLIENT_ID       # 마스토돈 클라이언트 ID")
    print("  MASTODON_CLIENT_SECRET   # 마스토돈 클라이언트 시크릿")  
    print("  MASTODON_ACCESS_TOKEN    # 마스토돈 액세스 토큰")
    print("  MASTODON_API_BASE_URL    # 마스토돈 인스턴스 URL")
    print("")
    print("선택 환경 변수:")
    print("  SHEET_ID                # Google Sheets ID")
    print("  GOOGLE_CREDENTIALS_PATH # Google 인증 파일 경로")
    print("  LOG_LEVEL               # 로그 레벨 (DEBUG/INFO/WARNING/ERROR)")
    print("")


def check_multi_bot_mode() -> bool:
    """
    .env 파일에서 ENABLE_MULTI_BOT 설정을 확인합니다.

    Returns:
        bool: 멀티 봇 모드 활성화 여부
    """
    from pathlib import Path

    env_path = Path('.env')

    # 환경 변수에서 먼저 확인
    env_value = os.getenv('ENABLE_MULTI_BOT', '').lower()
    if env_value in ['true', '1', 'yes']:
        return True
    elif env_value in ['false', '0', 'no']:
        return False

    # .env 파일에서 확인
    if env_path.exists():
        try:
            with open(env_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line.startswith('ENABLE_MULTI_BOT'):
                        # 주석 제거
                        if '#' in line:
                            line = line.split('#')[0].strip()

                        if '=' in line:
                            value = line.split('=', 1)[1].strip()
                            # 따옴표 제거
                            value = value.strip('\'"')
                            return value.lower() in ['true', '1', 'yes']
        except Exception as e:
            print(f"⚠️ .env 파일 읽기 실패: {e}")

    return False


if __name__ == '__main__':
    import argparse

    # 명령행 인수 파서
    parser = argparse.ArgumentParser(description='마스토돈 자동봇')
    parser.add_argument('--version', '-v', action='store_true', help='버전 정보 출력')
    parser.add_argument('--help-full', action='store_true', help='상세 도움말 출력')
    parser.add_argument('--bot-id', type=str, help='봇 ID (멀티 봇 모드용, 예: BOT1, BOT2)')

    args = parser.parse_args()

    # 버전 정보
    if args.version:
        show_version()
        sys.exit(0)

    # 상세 도움말
    if args.help_full:
        show_help()
        sys.exit(0)

    # ================================================================
    # 멀티 봇 모드 자동 라우팅
    # ================================================================
    # ENABLE_MULTI_BOT=True이고 BOT_ID가 없으면 → BotManager 실행
    # (bot_manager.py가 각 봇 프로세스를 --bot-id와 함께 실행함)
    # ================================================================
    is_multi_bot_mode = check_multi_bot_mode()
    has_bot_id = os.getenv('BOT_ID') or args.bot_id

    if is_multi_bot_mode and not has_bot_id:
        print("=" * 60)
        print("🔀 멀티 봇 모드 감지")
        print("   BotManager를 실행하여 모든 봇 프로세스를 시작합니다.")
        print("=" * 60)
        print("")

        try:
            from bot_manager import BotManager

            manager = BotManager()
            success = manager.start_all_bots()

            if success:
                print("✅ 모든 봇이 시작되었습니다.")
                print("   Ctrl+C를 눌러 종료할 수 있습니다.")
                print("")

                try:
                    manager.monitor_loop()
                except KeyboardInterrupt:
                    print("\n👋 종료 중...")
                    manager.stop_all_bots(graceful=True)

                sys.exit(0)
            else:
                print("❌ 봇 시작 실패")
                sys.exit(1)

        except ImportError as e:
            print(f"❌ BotManager 임포트 실패: {e}")
            print("   bot_manager.py 파일이 있는지 확인해주세요.")
            sys.exit(1)
        except Exception as e:
            print(f"❌ BotManager 실행 중 오류: {e}")
            sys.exit(1)

    # ================================================================
    # 싱글 봇 모드 또는 개별 봇 프로세스 실행
    # ================================================================
    # 멀티 봇 모드: 봇 ID가 전달된 경우 (bot_manager가 실행한 개별 프로세스)
    if args.bot_id:
        bot_id = args.bot_id
        logger.info(f"멀티 봇 모드로 시작: BOT_ID={bot_id}")

        # 환경 변수에서 BOT_ID가 이미 설정되어 있는지 확인
        if not os.getenv('BOT_ID'):
            logger.warning(f"환경 변수에 BOT_ID가 없습니다. 명령행 인자로 전달된 값 사용: {bot_id}")

        # 봇 이름 로깅
        bot_name = os.getenv('BOT_NAME', bot_id)
        logger.info(f"봇 이름: {bot_name}")

    # 봇 실행 (싱글 봇 또는 개별 봇 프로세스)
    exit_code = main()
    sys.exit(exit_code)
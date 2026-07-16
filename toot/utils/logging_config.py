"""
로깅 설정 모듈
툿수 모니터링 봇의 로깅 시스템을 설정하고 관리합니다.
"""

import json
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime
import pytz

# VM 환경 대응
current_dir = Path(__file__).parent
project_root = current_dir.parent
sys.path.append(str(project_root))

try:
    from config.settings import config
except ImportError:
    # 임포트 실패 시 기본 설정
    class MockConfig:
        LOG_LEVEL = 'INFO'
        LOG_MAX_BYTES = 10485760
        LOG_BACKUP_COUNT = 5
        DEBUG_MODE = False
        ENABLE_CONSOLE_LOG = True
        
        @staticmethod
        def get_log_file_path():
            return Path('logs/toot_monitor.log')
    
    config = MockConfig()


class KSTFormatter(logging.Formatter):
    """KST 시간을 사용하는 커스텀 포매터"""

    def formatTime(self, record, datefmt=None):
        """KST 시간으로 포맷팅"""
        dt = datetime.fromtimestamp(record.created, tz=pytz.timezone('Asia/Seoul'))
        if datefmt:
            return dt.strftime(datefmt)
        else:
            return dt.strftime('%Y-%m-%d %H:%M:%S KST')


class ColoredKSTFormatter(KSTFormatter):
    """이모지와 ANSI 색상을 포함한 KST 포매터 - 날짜/시간대 구분선 자동 추가"""

    # 클래스 변수: 마지막 로그의 날짜와 시간대 추적
    _last_date = None
    _last_period = None  # 'AM' or 'PM'

    # ANSI 색상 코드
    COLORS = {
        'RESET': '\033[0m',
        'BOLD': '\033[1m',
        'DIM': '\033[2m',

        # 기본 색상
        'BLACK': '\033[30m',
        'RED': '\033[31m',
        'GREEN': '\033[32m',
        'YELLOW': '\033[33m',
        'BLUE': '\033[34m',
        'MAGENTA': '\033[35m',
        'CYAN': '\033[36m',
        'WHITE': '\033[37m',

        # 밝은 색상
        'BRIGHT_BLACK': '\033[90m',
        'BRIGHT_RED': '\033[91m',
        'BRIGHT_GREEN': '\033[92m',
        'BRIGHT_YELLOW': '\033[93m',
        'BRIGHT_BLUE': '\033[94m',
        'BRIGHT_MAGENTA': '\033[95m',
        'BRIGHT_CYAN': '\033[96m',
        'BRIGHT_WHITE': '\033[97m',
    }

    # 로그 레벨별 색상 및 이모지 설정
    LEVEL_STYLES = {
        'DEBUG': {
            'emoji': '🔍',
            'color': COLORS['DIM'] + COLORS['BRIGHT_BLACK'],
            'label_color': COLORS['DIM'] + COLORS['CYAN'],
            'prefix': '   ',
        },
        'INFO': {
            'emoji': '✓',
            'color': COLORS['BRIGHT_WHITE'],
            'label_color': COLORS['BRIGHT_GREEN'],
            'prefix': '   ',
        },
        'WARNING': {
            'emoji': '⚠',
            'color': COLORS['BRIGHT_YELLOW'],
            'label_color': COLORS['BOLD'] + COLORS['BRIGHT_YELLOW'],
            'prefix': ' ⚡',
        },
        'ERROR': {
            'emoji': '✗',
            'color': COLORS['BRIGHT_RED'],
            'label_color': COLORS['BOLD'] + COLORS['BRIGHT_RED'],
            'prefix': ' ❌',
        },
        'CRITICAL': {
            'emoji': '💥',
            'color': COLORS['BOLD'] + COLORS['RED'],
            'label_color': COLORS['BOLD'] + COLORS['RED'],
            'prefix': '🚨 ',
        },
    }

    def format(self, record):
        """색상과 이모지를 포함한 포맷팅 - 날짜/시간대 변경 시 구분선 자동 추가"""
        # KST 시간 가져오기
        dt = datetime.fromtimestamp(record.created, tz=pytz.timezone('Asia/Seoul'))

        # 현재 날짜와 시간대 (AM/PM)
        current_date = dt.strftime('%Y-%m-%d')
        current_hour = dt.hour
        current_period = 'AM' if current_hour < 12 else 'PM'

        # 구분선 생성 여부 확인
        separator = ""
        if ColoredKSTFormatter._last_date is not None:
            # 날짜가 바뀌거나 AM/PM이 바뀌면 구분선 추가
            if (current_date != ColoredKSTFormatter._last_date or
                current_period != ColoredKSTFormatter._last_period):

                # 날짜 포맷: "12월 9일 오전/오후" 형식
                month = dt.month
                day = dt.day
                period_kr = '오전' if current_period == 'AM' else '오후'
                weekday_kr = ['월', '화', '수', '목', '금', '토', '일'][dt.weekday()]
                separator = (
                    f"\n{self.COLORS['BRIGHT_CYAN']}{self.COLORS['BOLD']}"
                    f"{'━' * 50}\n"
                    f"  📅 {month}월 {day}일 ({weekday_kr}) {period_kr}\n"
                    f"{'━' * 50}{self.COLORS['RESET']}\n"
                )

        # 마지막 날짜와 시간대 업데이트
        ColoredKSTFormatter._last_date = current_date
        ColoredKSTFormatter._last_period = current_period

        # 시간 포맷팅: HH:MM:SS
        record.asctime = dt.strftime('%H:%M:%S')

        style = self.LEVEL_STYLES.get(record.levelname, {
            'emoji': '📌',
            'color': self.COLORS['RESET'],
            'label_color': self.COLORS['RESET'],
            'prefix': '   ',
        })

        # 레벨 정보
        emoji = style['emoji']
        label_color = style['label_color']
        msg_color = style['color']
        prefix = style.get('prefix', '   ')
        reset = self.COLORS['RESET']

        # 레벨 표시를 5자로 고정 (INFO, DEBUG, ERROR, WARNING)
        level_display = record.levelname[:5].ljust(5)
        record.levelname_emoji = f"{label_color}{emoji} {level_display}{reset}"

        # 기본 포맷팅
        original_format = super().format(record)

        # 시간 부분에 색상 적용 (더 밝게)
        colored_time = f"{self.COLORS['DIM']}{record.asctime}{reset}"
        # 기존 시간을 색상이 적용된 시간으로 교체
        original_format = original_format.replace(record.asctime, colored_time, 1)

        # 메시지에 색상 적용
        if record.levelname in ['WARNING', 'ERROR', 'CRITICAL']:
            # 중요한 로그는 메시지 전체에 색상 적용
            parts = original_format.split(' | ')
            if len(parts) >= 3:
                # 마지막 부분(메시지)에 색상 적용
                message_part = ' | '.join(parts[2:])
                colored_parts = parts[:2] + [f"{msg_color}{message_part}{reset}"]
                original_format = ' | '.join(colored_parts)

        # 구분선이 있으면 앞에 추가
        if separator:
            return separator + original_format
        else:
            return original_format


class TootMonitorLogger:
    """툿수 모니터링 봇 전용 로거 클래스"""
    
    _instance: Optional['TootMonitorLogger'] = None
    _logger: Optional[logging.Logger] = None
    
    def __new__(cls) -> 'TootMonitorLogger':
        """싱글톤 패턴 구현"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        """로거 초기화 (한 번만 실행됨)"""
        if self._logger is None:
            self._setup_logger()
    
    def _setup_logger(self) -> None:
        """로거 설정"""
        # 기본 로거 생성
        self._logger = logging.getLogger('toot_monitor')
        self._logger.setLevel(getattr(logging, config.LOG_LEVEL.upper()))
        
        # 핸들러 중복 방지
        if self._logger.handlers:
            self._logger.handlers.clear()
        
        # 파일 핸들러 설정
        self._setup_file_handler()
        
        # 콘솔 핸들러 설정
        if config.ENABLE_CONSOLE_LOG:
            self._setup_console_handler()
        
        # 외부 라이브러리 로거 설정
        self._setup_external_loggers()

        # 초기 로그 메시지
        self._logger.info("━" * 60)
        self._logger.info("🚀 툿수 모니터링 봇 시작")
        self._logger.info(f"⚙️  로그 레벨: {config.LOG_LEVEL} | 디버그: {'ON' if config.DEBUG_MODE else 'OFF'}")
        self._logger.info(f"💾 로그 파일: {config.get_log_file_path().name}")
        self._logger.info("━" * 60)
    
    def _setup_file_handler(self) -> None:
        """파일 핸들러 설정"""
        try:
            # 로그 파일 디렉토리 생성
            log_path = config.get_log_file_path()
            log_path.parent.mkdir(parents=True, exist_ok=True)
            
            # 회전 파일 핸들러 생성
            file_handler = RotatingFileHandler(
                filename=str(log_path),
                maxBytes=config.LOG_MAX_BYTES,
                backupCount=config.LOG_BACKUP_COUNT,
                encoding='utf-8'
            )
            
            # 파일용 포매터 (상세한 정보 포함, KST 명시)
            file_formatter = KSTFormatter(
                fmt='[%(asctime)s KST] %(levelname)-8s | %(funcName)s:%(lineno)d | %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            )
            file_handler.setFormatter(file_formatter)
            file_handler.setLevel(logging.DEBUG)  # 파일에는 모든 레벨 기록
            
            self._logger.addHandler(file_handler)
            
        except Exception as e:
            print(f"⚠️ 파일 핸들러 설정 실패: {e}")
            print("파일 로깅 없이 계속 진행합니다.")
    
    def _setup_console_handler(self) -> None:
        """콘솔 핸들러 설정"""
        try:
            # Windows 콘솔에서 UTF-8 인코딩 강제
            import io
            if sys.platform == 'win32':
                sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

            console_handler = logging.StreamHandler(sys.stdout)

            # 콘솔용 포매터 (색상 및 이모지 포함)
            if config.DEBUG_MODE:
                # 디버그 모드: 상세한 정보 (함수명:라인)
                console_formatter = ColoredKSTFormatter(
                    fmt='%(asctime)s  %(levelname_emoji)s  %(funcName)s:%(lineno)d  %(message)s',
                    datefmt='%H:%M:%S'
                )
            else:
                # 일반 모드: 간단한 정보 (시간, 레벨, 메시지만)
                console_formatter = ColoredKSTFormatter(
                    fmt='%(asctime)s  %(levelname_emoji)s  %(message)s',
                    datefmt='%H:%M:%S'
                )

            console_handler.setFormatter(console_formatter)
            console_handler.setLevel(getattr(logging, config.LOG_LEVEL.upper()))

            self._logger.addHandler(console_handler)

        except Exception as e:
            print(f"⚠️ 콘솔 핸들러 설정 실패: {e}")
    
    def _setup_external_loggers(self) -> None:
        """외부 라이브러리 로거 설정"""
        # gspread 로거 (Google Sheets API 관련)
        gspread_logger = logging.getLogger('gspread')
        gspread_logger.setLevel(logging.WARNING)
        
        # requests 로거 (HTTP 요청 관련)
        requests_logger = logging.getLogger('requests')
        requests_logger.setLevel(logging.WARNING)
        
        # urllib3 로거 (HTTP 라이브러리)
        urllib3_logger = logging.getLogger('urllib3')
        urllib3_logger.setLevel(logging.WARNING)
        
        # mastodon 로거
        mastodon_logger = logging.getLogger('mastodon')
        mastodon_logger.setLevel(logging.INFO)
        
        # schedule 로거
        schedule_logger = logging.getLogger('schedule')
        schedule_logger.setLevel(logging.INFO)
    
    @property
    def logger(self) -> logging.Logger:
        """로거 인스턴스 반환"""
        return self._logger
    
    def log_toot_check(self, user_id: str, username: str, old_count: int, new_count: int, reward_given: int = 0) -> None:
        """툿수 체크 로그"""
        increase = new_count - old_count
        if reward_given > 0:
            self._logger.info(f"📈 @{username}  툿수 {old_count} → {new_count} (+{increase})  💰 +{reward_given} 소지금")
        else:
            self._logger.debug(f"📊 @{username}  툿수 {old_count} → {new_count} (+{increase})")

    def log_reward_given(self, user_id: str, username: str, amount: int, total_rewards: int) -> None:
        """재화 지급 로그"""
        self._logger.info(f"💰 @{username}  +{amount} 소지금 지급  (누적: {total_rewards})")

    def log_sheet_operation(self, operation: str, sheet_name: str, success: bool, details: str = None) -> None:
        """시트 작업 로그"""
        details_str = f"  {details}" if details else ""
        status = "✓" if success else "✗"
        if success:
            self._logger.debug(f"📊 {status} {operation}  ({sheet_name}){details_str}")
        else:
            self._logger.warning(f"📊 {status} {operation} 실패  ({sheet_name}){details_str}")

    def log_api_call(self, api_name: str, operation: str, success: bool, duration: float = None, error: str = None) -> None:
        """API 호출 로그"""
        duration_str = f"  {duration:.2f}s" if duration else ""
        error_str = f"  오류: {error}" if error else ""
        status = "✓" if success else "✗"

        if success:
            self._logger.debug(f"🌐 {status} {api_name}  {operation}{duration_str}")
        else:
            self._logger.warning(f"🌐 {status} {api_name}  {operation}{duration_str}{error_str}")

    def log_monitoring_cycle(self, cycle_count: int, users_checked: int, rewards_given: int, errors: int) -> None:
        """모니터링 사이클 로그"""
        self._logger.info(f"🔄 사이클 #{cycle_count}  확인 {users_checked}명  💰 지급 {rewards_given}명  {'⚠️ 오류 ' + str(errors) + '건' if errors > 0 else ''}")

    def log_system_event(self, event: str, details: str = None) -> None:
        """시스템 이벤트 로그"""
        details_str = f"  {details}" if details else ""
        self._logger.info(f"⚙️  {event}{details_str}")
    
    def log_error_with_context(self, error: Exception, context: Dict[str, Any] = None) -> None:
        """컨텍스트와 함께 에러 로그"""
        error_msg = f"🚨 오류 발생: {type(error).__name__}: {str(error)}"

        if context:
            context_str = " | ".join([f"{k}: {v}" for k, v in context.items() if v is not None])
            error_msg += f" | 컨텍스트: {context_str}"

        self._logger.error(error_msg, exc_info=config.DEBUG_MODE)

    def shutdown(self):
        """로거 종료 처리"""
        self._logger.info("━" * 60)
        self._logger.info("👋 툿수 모니터링 봇 종료")
        self._logger.info("━" * 60)
        logging.shutdown()


# JSON 로깅 함수들 (통계 및 분석용)
def log_toot_monitoring_data(user_id: str, username: str, old_toots: int, new_toots: int, reward_given: int):
    """툿수 모니터링 데이터를 JSON 파일에 로그"""
    try:
        log_entry = {
            'timestamp': datetime.now(pytz.timezone('Asia/Seoul')).isoformat(),
            'type': 'toot_monitoring',
            'user_id': user_id,
            'username': username,
            'old_toots': old_toots,
            'new_toots': new_toots,
            'toot_increase': new_toots - old_toots,
            'reward_given': reward_given
        }
        
        # 모니터링 로그 파일에 기록
        log_file = config.get_log_file_path().parent / 'toot_monitoring.jsonl'
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
            
    except Exception as e:
        logger.error(f"툿수 모니터링 데이터 로그 기록 실패: {e}")


def log_reward_transaction(user_id: str, username: str, amount: int, total_rewards: int, reason: str = "툿수 달성"):
    """재화 지급 거래를 JSON 파일에 로그"""
    try:
        log_entry = {
            'timestamp': datetime.now(pytz.timezone('Asia/Seoul')).isoformat(),
            'type': 'reward_transaction',
            'user_id': user_id,
            'username': username,
            'amount': amount,
            'total_rewards_after': total_rewards,
            'reason': reason
        }
        
        # 재화 거래 로그 파일에 기록
        log_file = config.get_log_file_path().parent / 'reward_transactions.jsonl'
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
            
    except Exception as e:
        logger.error(f"재화 거래 로그 기록 실패: {e}")


def log_system_stats(users_total: int, users_active: int, total_rewards_given: int, errors_count: int):
    """시스템 통계를 JSON 파일에 로그"""
    try:
        log_entry = {
            'timestamp': datetime.now(pytz.timezone('Asia/Seoul')).isoformat(),
            'type': 'system_stats',
            'users_total': users_total,
            'users_active': users_active,
            'total_rewards_given': total_rewards_given,
            'errors_count': errors_count
        }
        
        # 시스템 통계 로그 파일에 기록
        log_file = config.get_log_file_path().parent / 'system_stats.jsonl'
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
            
    except Exception as e:
        logger.error(f"시스템 통계 로그 기록 실패: {e}")


# 전역 로거 인스턴스
toot_logger = TootMonitorLogger()
logger = toot_logger.logger


def setup_logging() -> TootMonitorLogger:
    """
    로깅 시스템을 설정하고 봇 로거를 반환합니다.
    
    Returns:
        TootMonitorLogger: 설정된 봇 로거 인스턴스
    """
    return toot_logger


def get_logger() -> logging.Logger:
    """
    봇 로거를 반환합니다.
    
    Returns:
        logging.Logger: 설정된 로거 인스턴스
    """
    return logger


# 편의 함수들
def log_info(message: str) -> None:
    """정보 로그"""
    logger.info(message)


def log_warning(message: str) -> None:
    """경고 로그"""
    logger.warning(message)


def log_error(message: str, exc_info: bool = None) -> None:
    """에러 로그"""
    if exc_info is None:
        exc_info = config.DEBUG_MODE
    logger.error(message, exc_info=exc_info)


def log_debug(message: str) -> None:
    """디버그 로그"""
    logger.debug(message)


def log_critical(message: str) -> None:
    """치명적 오류 로그"""
    logger.critical(message)


def shutdown_logging():
    """로깅 시스템 종료"""
    toot_logger.shutdown()


# 컨텍스트 매니저로 사용할 수 있는 로깅
class LogContext:
    """로깅 컨텍스트 매니저"""
    
    def __init__(self, operation: str, **context):
        self.operation = operation
        self.context = context
        self.start_time = None
    
    def __enter__(self):
        self.start_time = datetime.now()
        context_str = " | ".join([f"{k}: {v}" for k, v in self.context.items() if v is not None])
        logger.debug(f"▶️ 시작: {self.operation} | {context_str}")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        duration = (datetime.now() - self.start_time).total_seconds()

        if exc_type is None:
            logger.debug(f"✅ 완료: {self.operation} | ⏱️ {duration:.3f}s")
        else:
            logger.error(f"❌ 실패: {self.operation} | ⏱️ {duration:.3f}s | 오류: {exc_val}")

        return False  # 예외를 다시 발생시킴


# 로그 분석 유틸리티 함수들
def get_daily_stats(date: str = None) -> Dict[str, Any]:
    """일일 통계 조회"""
    if date is None:
        date = datetime.now(pytz.timezone('Asia/Seoul')).strftime('%Y-%m-%d')
    
    try:
        log_file = config.get_log_file_path().parent / 'toot_monitoring.jsonl'
        if not log_file.exists():
            return {'error': '로그 파일을 찾을 수 없습니다.'}
        
        daily_stats = {
            'date': date,
            'total_checks': 0,
            'rewards_given': 0,
            'total_toot_increase': 0,
            'active_users': set(),
            'users_with_rewards': set()
        }
        
        with open(log_file, 'r', encoding='utf-8') as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    if entry.get('timestamp', '').startswith(date):
                        daily_stats['total_checks'] += 1
                        daily_stats['total_toot_increase'] += entry.get('toot_increase', 0)
                        daily_stats['active_users'].add(entry.get('username'))
                        
                        if entry.get('reward_given', 0) > 0:
                            daily_stats['rewards_given'] += entry.get('reward_given', 0)
                            daily_stats['users_with_rewards'].add(entry.get('username'))
                            
                except json.JSONDecodeError:
                    continue
        
        # set을 개수로 변환
        daily_stats['active_users'] = len(daily_stats['active_users'])
        daily_stats['users_with_rewards'] = len(daily_stats['users_with_rewards'])
        
        return daily_stats
        
    except Exception as e:
        logger.error(f"일일 통계 조회 실패: {e}")
        return {'error': str(e)}


def cleanup_old_logs(days_to_keep: int = 30):
    """오래된 로그 파일 정리"""
    try:
        log_dir = config.get_log_file_path().parent
        cutoff_date = datetime.now() - datetime.timedelta(days=days_to_keep)
        
        cleaned_files = []
        for log_file in log_dir.glob('*.jsonl'):
            try:
                # 파일의 생성 시간 확인
                file_time = datetime.fromtimestamp(log_file.stat().st_mtime)
                if file_time < cutoff_date:
                    log_file.unlink()
                    cleaned_files.append(str(log_file))
            except Exception as e:
                logger.warning(f"로그 파일 정리 실패: {log_file} - {e}")
        
        if cleaned_files:
            logger.info(f"오래된 로그 파일 정리 완료: {len(cleaned_files)}개 파일")
            
    except Exception as e:
        logger.error(f"로그 파일 정리 중 오류: {e}")


# 애플리케이션 종료 시 cleanup
import atexit
atexit.register(shutdown_logging)


# 테스트 및 디버그용 함수
def test_logging():
    """로깅 시스템 테스트"""
    logger.info("━" * 60)
    logger.info("🧪 로깅 시스템 테스트 시작")
    logger.info("━" * 60)

    logger.debug("디버그 메시지 테스트")
    logger.info("정보 메시지 테스트")
    logger.warning("경고 메시지 테스트")
    logger.error("에러 메시지 테스트")

    # 툿수 체크 로그 테스트
    toot_logger.log_toot_check("test_user", "테스트유저", 100, 150, 0)
    toot_logger.log_toot_check("test_user2", "테스트유저2", 200, 310, 1)

    # 재화 지급 로그 테스트
    toot_logger.log_reward_given("test_user2", "테스트유저2", 1, 5)

    # 시스템 이벤트 로그 테스트
    toot_logger.log_system_event("봇 시작", "모니터링 준비 완료")

    # 모니터링 사이클 로그 테스트
    toot_logger.log_monitoring_cycle(1, 10, 3, 0)

    # JSON 로그 테스트
    log_toot_monitoring_data("test_user", "테스트유저", 100, 150, 0)
    log_reward_transaction("test_user2", "테스트유저2", 1, 5)

    logger.info("━" * 60)
    logger.info("✅ 로깅 시스템 테스트 완료")
    logger.info("━" * 60)


if __name__ == "__main__":
    # 로깅 시스템 테스트 실행
    test_logging()
    
    # 일일 통계 테스트
    stats = get_daily_stats()
    print(f"오늘의 통계: {stats}")
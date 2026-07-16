"""
로깅 설정 모듈 - 파일 로깅 전용
애플리케이션 전반의 로깅을 설정하고 관리합니다.
파일 로깅만 사용하여 성능을 최적화했습니다.
"""

import json  
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional
from datetime import datetime
import pytz

# 경로 설정 (VM 환경 대응)
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

try:
    from config.settings import config
except ImportError:
    # VM 환경에서 임포트 실패 시 폴백
    import importlib.util
    config_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'settings.py')
    spec = importlib.util.spec_from_file_location("settings", config_path)
    settings_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(settings_module)
    config = settings_module.config


class UTCFormatter(logging.Formatter):
    """UTC 시간을 사용하는 커스텀 포매터"""
    
    def formatTime(self, record, datefmt=None):
        """UTC 시간으로 포맷팅"""
        dt = datetime.fromtimestamp(record.created, tz=pytz.UTC)
        if datefmt:
            return dt.strftime(datefmt)
        else:
            return dt.strftime('%Y-%m-%d %H:%M:%S UTC')


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
            'label_color': COLORS['DIM'] + COLORS['BRIGHT_BLACK'],
        },
        'INFO': {
            'emoji': '',
            'color': COLORS['RESET'],
            'label_color': COLORS['BRIGHT_BLUE'],
        },
        'WARNING': {
            'emoji': '⚠️ ',
            'color': COLORS['YELLOW'],
            'label_color': COLORS['BOLD'] + COLORS['YELLOW'],
        },
        'ERROR': {
            'emoji': '❌',
            'color': COLORS['RED'],
            'label_color': COLORS['BOLD'] + COLORS['RED'],
        },
        'CRITICAL': {
            'emoji': '🚨',
            'color': COLORS['BOLD'] + COLORS['RED'],
            'label_color': COLORS['BOLD'] + COLORS['RED'],
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

                # 날짜 포맷: "11월 23일 AM" 형식
                month = dt.month
                day = dt.day
                separator = f"\n{self.COLORS['BRIGHT_CYAN']}{self.COLORS['BOLD']}{'=' * 16} {month}월 {day}일 {current_period} {'=' * 16}{self.COLORS['RESET']}\n\n"

        # 마지막 날짜와 시간대 업데이트
        ColoredKSTFormatter._last_date = current_date
        ColoredKSTFormatter._last_period = current_period

        # 시간 포맷팅: MM.DD  HH:MM:SS
        record.asctime = dt.strftime('%m.%d  %H:%M:%S')

        style = self.LEVEL_STYLES.get(record.levelname, {
            'emoji': '📌',
            'color': self.COLORS['RESET'],
            'label_color': self.COLORS['RESET'],
        })

        # 레벨 라벨 (색상 + 이모지 + 레벨명)
        emoji = style['emoji']
        label_color = style['label_color']
        reset = self.COLORS['RESET']

        # 레벨명을 중앙 정렬로 표시 (총 13칸)
        if emoji:
            # 이모지가 있는 경우: 이모지는 2칸으로 계산
            level_text = f"{emoji} {record.levelname}"
            # 전체 길이를 13으로 맞춤 (이모지 2 + 공백 1 + 레벨명)
            padding_needed = 13 - len(record.levelname) - 3  # 이모지(2) + 공백(1)
            padded_level = f"{level_text}{' ' * padding_needed}"
            record.levelname_emoji = f"{label_color}{padded_level}{reset}"
        else:
            # 이모지가 없는 경우: 레벨명만 중앙 정렬
            padding_needed = (13 - len(record.levelname)) // 2
            padding_right = 13 - len(record.levelname) - padding_needed
            padded_level = f"{' ' * padding_needed}{record.levelname}{' ' * padding_right}"
            record.levelname_emoji = f"{label_color}{padded_level}{reset}"

        # 기본 포맷팅
        original_format = super().format(record)

        # 시간 부분에 색상 적용
        colored_time = f"{self.COLORS['BRIGHT_BLACK']}{record.asctime}{reset}"
        # 기존 시간을 색상이 적용된 시간으로 교체
        original_format = original_format.replace(record.asctime, colored_time, 1)

        # 구분선이 있으면 앞에 추가
        if separator:
            return separator + original_format
        else:
            return original_format


class BotLogger:
    """봇 전용 로거 클래스 - 파일 로깅 전용"""
    
    _instance: Optional['BotLogger'] = None
    _logger: Optional[logging.Logger] = None
    
    def __new__(cls) -> 'BotLogger':
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
        self._logger = logging.getLogger('mastodon_bot')
        self._logger.setLevel(getattr(logging, config.LOG_LEVEL.upper()))
        
        # 핸들러 중복 방지
        if self._logger.handlers:
            self._logger.handlers.clear()
        
        # 파일 핸들러 설정
        self._setup_file_handler()
        
        # 콘솔 핸들러 설정
        if config.ENABLE_CONSOLE_LOG:
            self._setup_console_handler()
        
        # 기본 로거 설정 (다른 라이브러리 로그 레벨 조정)
        self._setup_external_loggers()
        
        # 초기 로그 메시지
        self._logger.info("=" * 50)
        self._logger.info("마스토돈 봇 로깅 시스템 초기화 완료")
        self._logger.info(f"로그 레벨: {config.LOG_LEVEL}")
        self._logger.info(f"파일 로깅: {config.LOG_FILE_PATH}")
        self._logger.info(f"콘솔 로깅: {config.ENABLE_CONSOLE_LOG}")
        self._logger.info(f"디버그 모드: {config.DEBUG_MODE}")
        self._logger.info("=" * 50)
    
    def _setup_file_handler(self) -> None:
        """파일 핸들러 설정"""
        try:
            # 로그 파일 디렉토리 생성
            log_path = Path(config.LOG_FILE_PATH)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            
            # 회전 파일 핸들러 생성
            file_handler = RotatingFileHandler(
                filename=config.LOG_FILE_PATH,
                maxBytes=config.LOG_MAX_BYTES,
                backupCount=config.LOG_BACKUP_COUNT,
                encoding='utf-8'
            )
            
            # 파일용 포매터 (상세한 정보 포함) - KST 기준
            file_formatter = KSTFormatter(
                fmt='%(asctime)s | %(levelname)-8s | %(name)s | %(funcName)s:%(lineno)d | %(message)s',
                datefmt='%Y-%m-%d  %H:%M:%S'
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
            console_handler = logging.StreamHandler(sys.stdout)

            # 콘솔용 포매터 (간결하고 보기 좋은 정보)
            if config.DEBUG_MODE:
                # 디버그 모드: 상세한 정보 (함수명:라인)
                console_formatter = ColoredKSTFormatter(
                    fmt='  %(asctime)s | %(levelname_emoji)s | %(message)s',
                    datefmt='%m.%d  %H:%M:%S'
                )
            else:
                # 일반 모드: 간단한 정보 (시간, 레벨, 메시지만)
                console_formatter = ColoredKSTFormatter(
                    fmt='  %(asctime)s | %(levelname_emoji)s | %(message)s',
                    datefmt='%m.%d  %H:%M:%S'
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
    
    @property
    def logger(self) -> logging.Logger:
        """로거 인스턴스 반환"""
        return self._logger
    
    def log_command_execution(self, user_id: str, command: str, result: str, success: bool) -> None:
        """명령어 실행 로그"""
        # 명령어 실행은 1줄만 출력 (응답 전송 로그는 별도로 출력됨)
        self._logger.info(f"💬 [{command}] @{user_id}")
        if not success:
            self._logger.warning(f"❌ 처리 실패 | 오류: {result}")
    
    def log_api_call(self, api_name: str, operation: str, success: bool, duration: float = None) -> None:
        """API 호출 로그"""
        status_emoji = "✅" if success else "❌"
        status = "성공" if success else "실패"
        duration_str = f" | ⏱️ {duration:.3f}s" if duration else ""

        if success:
            self._logger.debug(f"🌐 [{api_name}] {operation} → {status_emoji} {status}{duration_str}")
        else:
            self._logger.warning(f"🌐 [{api_name}] {operation} → {status_emoji} {status}{duration_str}")
    
    def log_sheet_operation(self, operation: str, worksheet: str, success: bool, error: str = None) -> None:
        """시트 작업 로그"""
        status_emoji = "✅" if success else "❌"
        status = "성공" if success else "실패"
        error_msg = f" | 오류: {error}" if error else ""

        if success:
            self._logger.debug(f"📊 [시트] {worksheet}/{operation} → {status_emoji} {status}")
        else:
            self._logger.warning(f"📊 [시트] {worksheet}/{operation} → {status_emoji} {status}{error_msg}")
    
    def log_user_action(self, user_id: str, action: str, details: str = None) -> None:
        """사용자 행동 로그"""
        details_str = f" | {details}" if details else ""
        self._logger.info(f"사용자 행동 | {user_id} | {action}{details_str}")
    
    def log_system_event(self, event: str, details: str = None) -> None:
        """시스템 이벤트 로그"""
        details_str = f" | {details}" if details else ""
        self._logger.info(f"시스템 이벤트 | {event}{details_str}")
    
    def log_error_with_context(self, error: Exception, context: dict = None) -> None:
        """컨텍스트와 함께 에러 로그"""
        error_msg = f"🚨 오류 발생: {type(error).__name__}: {str(error)}"

        if context:
            context_str = " | ".join([f"{k}: {v}" for k, v in context.items()])
            error_msg += f" | 컨텍스트: {context_str}"

        self._logger.error(error_msg, exc_info=config.DEBUG_MODE)
    
    def shutdown(self):
        """로거 종료 처리"""
        self._logger.info("로깅 시스템 종료됨")


def setup_logging() -> BotLogger:
    """
    로깅 시스템을 설정하고 봇 로거를 반환합니다.
    
    Returns:
        BotLogger: 설정된 봇 로거 인스턴스
    """
    return BotLogger()


def get_logger() -> logging.Logger:
    """
    봇 로거를 반환합니다.
    
    Returns:
        logging.Logger: 설정된 로거 인스턴스
    """
    bot_logger = BotLogger()
    return bot_logger.logger


# 모듈 레벨에서 사용할 수 있는 로거 인스턴스
bot_logger = setup_logging()
logger = bot_logger.logger


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
    bot_logger.shutdown()


# ============================================================================
# 개선된 로깅 유틸리티 (2024-10-23 추가)
# ============================================================================

class LogFormatter:
    """
    통일된 로그 포맷을 제공하는 유틸리티 클래스

    사용 예:
        logger.info(LogFormatter.command("user123", "도움말", success=True, duration=0.5))
        logger.info(LogFormatter.system("봇 시작", "명령어 15개 로드"))
        logger.info(LogFormatter.api("Sheets", "읽기", success=True, duration=0.3))
    """

    @staticmethod
    def command(user_id: str, command: str, success: bool, duration: float = None,
                details: str = None) -> str:
        """
        명령어 실행 로그 포맷

        Args:
            user_id: 사용자 ID
            command: 명령어
            success: 성공 여부
            duration: 실행 시간 (초) - 사용 안 함
            details: 추가 세부정보 - 사용 안 함

        Returns:
            str: 포맷된 로그 메시지
        """
        status_emoji = "✅" if success else "❌"
        status = "성공" if success else "실패"

        msg = f"💬 [{command}] @{user_id} → {status_emoji} {status}"

        return msg

    @staticmethod
    def system(event: str, details: str = None, success: bool = True) -> str:
        """
        시스템 이벤트 로그 포맷

        Args:
            event: 이벤트명
            details: 세부정보
            success: 성공 여부

        Returns:
            str: 포맷된 로그 메시지
        """
        status_emoji = "✅" if success else "❌"
        msg = f"⚙️ {event} → {status_emoji}"

        if details:
            msg += f" | {details}"

        return msg

    @staticmethod
    def api(service: str, operation: str, success: bool, duration: float = None,
            details: str = None) -> str:
        """
        API 호출 로그 포맷

        Args:
            service: 서비스명 (Sheets, Mastodon 등)
            operation: 작업명
            success: 성공 여부
            duration: 소요 시간 (초)
            details: 추가 세부정보

        Returns:
            str: 포맷된 로그 메시지
        """
        status_emoji = "✅" if success else "❌"
        result = "성공" if success else "실패"

        msg = f"🌐 [{service}] {operation} → {status_emoji} {result}"

        if duration is not None:
            msg += f" | ⏱️ {duration:.3f}s"

        if details:
            msg += f" | {details}"

        return msg

    @staticmethod
    def cache(operation: str, key: str, hit: bool = None, details: str = None) -> str:
        """
        캐시 작업 로그 포맷

        Args:
            operation: 작업 (get, set, invalidate 등)
            key: 캐시 키
            hit: 캐시 히트 여부 (get 작업에만 해당)
            details: 추가 세부정보

        Returns:
            str: 포맷된 로그 메시지
        """
        if hit is not None:
            status_emoji = "✅" if hit else "⚡"
            status = "HIT" if hit else "MISS"
            msg = f"💾 [{operation}] {key} → {status_emoji} {status}"
        else:
            msg = f"💾 [{operation}] {key}"

        if details:
            msg += f" | {details}"

        return msg

    @staticmethod
    def user_action(user_id: str, action: str, details: str = None) -> str:
        """
        사용자 행동 로그 포맷

        Args:
            user_id: 사용자 ID
            action: 행동
            details: 세부정보

        Returns:
            str: 포맷된 로그 메시지
        """
        msg = f"👤 @{user_id} → {action}"

        if details:
            msg += f" | {details}"

        return msg


def should_log_debug() -> bool:
    """
    DEBUG 로그를 출력할지 결정

    Returns:
        bool: DEBUG 모드가 활성화되어 있으면 True
    """
    return config.DEBUG_MODE


def log_command_result(user_id: str, command: str, success: bool,
                       duration: float = None, result_length: int = None) -> None:
    """
    명령어 실행 결과를 일관된 포맷으로 로깅

    Args:
        user_id: 사용자 ID
        command: 실행된 명령어
        success: 성공 여부
        duration: 실행 시간 (초)
        result_length: 결과 메시지 길이
    """
    details = None
    if result_length is not None:
        details = f"결과: {result_length}자"

    if success:
        logger.info(LogFormatter.command(user_id, command, True, duration, details))
    else:
        logger.warning(LogFormatter.command(user_id, command, False, duration, details))


def log_api_operation(service: str, operation: str, success: bool,
                      duration: float = None, item_count: int = None) -> None:
    """
    API 작업을 일관된 포맷으로 로깅

    Args:
        service: 서비스명
        operation: 작업명
        success: 성공 여부
        duration: 소요 시간
        item_count: 처리된 아이템 수
    """
    details = None
    if item_count is not None:
        details = f"{item_count}개 항목"

    level = logger.debug if success else logger.warning
    level(LogFormatter.api(service, operation, success, duration, details))


def log_system_event(event: str, details: str = None, success: bool = True) -> None:
    """
    시스템 이벤트를 일관된 포맷으로 로깅

    Args:
        event: 이벤트명
        details: 세부정보
        success: 성공 여부
    """
    level = logger.info if success else logger.error
    level(LogFormatter.system(event, details, success))


# 컨텍스트 매니저로 사용할 수 있는 로깅
class LogContext:
    """로깅 컨텍스트 매니저"""
    
    def __init__(self, operation: str, **context):
        self.operation = operation
        self.context = context
        self.start_time = None
    
    def __enter__(self):
        self.start_time = datetime.now()
        context_str = " | ".join([f"{k}: {v}" for k, v in self.context.items()])
        logger.debug(f"시작: {self.operation} | {context_str}")
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        duration = (datetime.now() - self.start_time).total_seconds()
        
        if exc_type is None:
            logger.debug(f"완료: {self.operation} | 소요시간: {duration:.3f}s")
        else:
            logger.error(f"실패: {self.operation} | 소요시간: {duration:.3f}s | 오류: {exc_val}")
        
        return False  # 예외를 다시 발생시킴

def log_command_usage(user_id: str, username: str, command: str, result: str, success: bool = True):
    """
    명령어 사용을 JSON 파일에 로그

    Args:
        user_id: 사용자 ID
        username: 사용자명
        command: 실행된 명령어
        result: 명령어 결과
        success: 성공 여부
    """
    try:
        log_entry = {
            'timestamp': datetime.now().isoformat(),
            'user_id': user_id,
            'username': username,
            'command': command,
            'result': result[:200] if len(result) > 200 else result,  # 결과가 너무 길면 자르기
            'result_length': len(result),
            'success': success
        }

        # 명령어 로그 파일에 기록
        with open('command_usage.log', 'a', encoding='utf-8') as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')

        # 일반 로거에도 기록
        logger.info(f"💬 [{command}] @{username}")
        if not success:
            logger.warning(f"❌ 처리 실패 | 오류: {result}")

    except Exception as e:
        logger.error(f"🔴 명령어 로그 기록 실패: {e}")


def log_money_transaction(user_id: str, username: str, transaction_type: str, amount: int, balance_after: int, details: str = ""):
    """
    돈 거래 로그 (상점, 양도 등)

    Args:
        user_id: 사용자 ID
        username: 사용자명
        transaction_type: 거래 유형 (구매, 양도, 용돈 등)
        amount: 거래 금액
        balance_after: 거래 후 잔액
        details: 추가 세부사항
    """
    try:
        log_entry = {
            'timestamp': datetime.now().isoformat(),
            'user_id': user_id,
            'username': username,
            'type': 'money_transaction',
            'transaction_type': transaction_type,
            'amount': amount,
            'balance_after': balance_after,
            'details': details
        }

        # 거래 로그 파일에 기록
        with open('money_transactions.log', 'a', encoding='utf-8') as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')

        # 일반 로거에도 기록
        logger.info(f"💰 [거래] @{username} | {transaction_type} | {amount}갈레온 | 잔액: {balance_after}")

    except Exception as e:
        logger.error(f"🔴 거래 로그 기록 실패: {e}")


def log_item_transaction(user_id: str, username: str, action: str, item_name: str, quantity: int, details: str = ""):
    """
    아이템 거래 로그

    Args:
        user_id: 사용자 ID
        username: 사용자명
        action: 행동 (구매, 사용, 획득 등)
        item_name: 아이템명
        quantity: 수량
        details: 추가 세부사항
    """
    try:
        log_entry = {
            'timestamp': datetime.now().isoformat(),
            'user_id': user_id,
            'username': username,
            'type': 'item_transaction',
            'action': action,
            'item_name': item_name,
            'quantity': quantity,
            'details': details
        }

        # 아이템 로그 파일에 기록
        with open('item_transactions.log', 'a', encoding='utf-8') as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')

        # 일반 로거에도 기록
        logger.info(f"🎁 [아이템] @{username} | {action} | {item_name} x{quantity}")

    except Exception as e:
        logger.error(f"🔴 아이템 로그 기록 실패: {e}")

# 애플리케이션 종료 시 cleanup
import atexit
atexit.register(shutdown_logging)
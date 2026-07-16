"""
마스토돈 예약 봇 로깅 시스템
구조화된 로깅을 통해 봇의 모든 활동을 추적하고 디버깅을 지원합니다.
"""

import os
import sys
import logging
import logging.handlers
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Dict, Any
import json

# 프로젝트 루트 경로 설정
current_dir = Path(__file__).parent.parent.absolute()
sys.path.append(str(current_dir))

try:
    from config.settings import config
except ImportError:
    # config가 없는 경우 기본 설정 사용
    class DefaultConfig:
        LOG_LEVEL = 'INFO'
        LOG_DIR = Path(__file__).parent.parent / 'logs'
        PROJECT_ROOT = Path(__file__).parent.parent
    config = DefaultConfig()


class ColoredFormatter(logging.Formatter):
    """
    컬러 출력을 지원하는 로그 포맷터
    콘솔 출력 시 로그 레벨에 따라 다른 색상을 적용합니다.
    """
    
    # ANSI 색상 코드
    COLORS = {
        'DEBUG': '\033[36m',      # 청록색
        'INFO': '\033[32m',       # 녹색
        'WARNING': '\033[33m',    # 노란색
        'ERROR': '\033[31m',      # 빨간색
        'CRITICAL': '\033[35m',   # 마젠타
        'RESET': '\033[0m'        # 색상 리셋
    }
    
    # 로그 레벨별 이모지
    EMOJIS = {
        'DEBUG': '🔍',
        'INFO': 'ℹ️',
        'WARNING': '⚠️',
        'ERROR': '❌',
        'CRITICAL': '💥'
    }
    
    def __init__(self, use_colors: bool = True, use_emojis: bool = True):
        super().__init__()
        self.use_colors = use_colors and self._supports_color()
        self.use_emojis = use_emojis
    
    def _supports_color(self) -> bool:
        """터미널이 색상을 지원하는지 확인"""
        return (
            hasattr(sys.stderr, "isatty") and sys.stderr.isatty() and
            os.environ.get('TERM') != 'dumb'
        )
    
    def format(self, record: logging.LogRecord) -> str:
        """로그 레코드를 포맷팅"""
        # 기본 포맷 적용
        log_time = datetime.fromtimestamp(record.created).strftime('%H:%M:%S')
        level_name = record.levelname
        
        # 이모지 추가
        if self.use_emojis and level_name in self.EMOJIS:
            emoji = self.EMOJIS[level_name]
        else:
            emoji = ''
        
        # 색상 적용
        if self.use_colors and level_name in self.COLORS:
            color = self.COLORS[level_name]
            reset = self.COLORS['RESET']
            level_name = f"{color}{level_name}{reset}"
        
        # 모듈명 추가 (너무 길면 축약)
        module_name = record.name
        if len(module_name) > 20:
            module_name = f"...{module_name[-17:]}"
        
        # 최종 메시지 조합
        formatted_msg = f"{log_time} {emoji} [{level_name:>8}] {module_name:<20} | {record.getMessage()}"
        
        # 예외 정보가 있으면 추가
        if record.exc_info:
            formatted_msg += f"\n{self.formatException(record.exc_info)}"
        
        return formatted_msg


class JSONFormatter(logging.Formatter):
    """
    JSON 형식의 로그 포맷터
    구조화된 로그 분석을 위해 사용됩니다.
    """
    
    def format(self, record: logging.LogRecord) -> str:
        """로그 레코드를 JSON 형식으로 포맷팅"""
        log_data = {
            'timestamp': datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
            'module': record.module,
            'function': record.funcName,
            'line': record.lineno,
            'process_id': record.process,
            'thread_id': record.thread
        }
        
        # 예외 정보 추가
        if record.exc_info:
            log_data['exception'] = self.formatException(record.exc_info)
        
        # 추가 컨텍스트 정보가 있으면 포함
        if hasattr(record, 'extra_data'):
            log_data['extra'] = record.extra_data
        
        return json.dumps(log_data, ensure_ascii=False)


class TootLoggerAdapter(logging.LoggerAdapter):
    """
    툿 관련 로그를 위한 어댑터
    툿 ID, 사용자 정보 등을 자동으로 포함시킵니다.
    """
    
    def process(self, msg, kwargs):
        """로그 메시지 처리"""
        extra_info = []
        
        if 'toot_id' in self.extra:
            extra_info.append(f"툿ID:{self.extra['toot_id']}")
        
        if 'user_id' in self.extra:
            extra_info.append(f"사용자:{self.extra['user_id']}")
        
        if 'scheduled_time' in self.extra:
            extra_info.append(f"예약시간:{self.extra['scheduled_time']}")
        
        if extra_info:
            msg = f"[{' | '.join(extra_info)}] {msg}"
        
        return msg, kwargs


def setup_logging(log_level: Optional[str] = None, 
                  log_dir: Optional[Path] = None,
                  enable_console: bool = True,
                  enable_file: bool = True,
                  enable_json: bool = True) -> None:
    """
    로깅 시스템 초기화
    
    Args:
        log_level: 로그 레벨 (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_dir: 로그 파일 저장 디렉토리
        enable_console: 콘솔 출력 활성화
        enable_file: 파일 출력 활성화
        enable_json: JSON 로그 파일 활성화
    """
    # 설정값 결정
    if log_level is None:
        log_level = getattr(config, 'LOG_LEVEL', 'INFO')
    
    if log_dir is None:
        log_dir = getattr(config, 'LOG_DIR', Path(__file__).parent.parent / 'logs')
    
    # 로그 디렉토리 생성
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    
    # 루트 로거 설정
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper()))
    
    # 기존 핸들러 제거
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # 날짜 기반 파일명
    today = datetime.now().strftime('%Y%m%d')
    
    # === 콘솔 핸들러 ===
    if enable_console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(getattr(logging, log_level.upper()))
        console_handler.setFormatter(ColoredFormatter(use_colors=True, use_emojis=True))
        root_logger.addHandler(console_handler)
    
    # === 파일 핸들러 (일반 로그) ===
    if enable_file:
        file_handler = logging.handlers.RotatingFileHandler(
            filename=log_dir / f'mastodon_bot_{today}.log',
            maxBytes=10 * 1024 * 1024,  # 10MB
            backupCount=7,  # 7일치 보관
            encoding='utf-8'
        )
        file_handler.setLevel(logging.DEBUG)
        file_formatter = logging.Formatter(
            '%(asctime)s [%(levelname)8s] %(name)20s | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(file_formatter)
        root_logger.addHandler(file_handler)
    
    # === JSON 파일 핸들러 (구조화된 로그) ===
    if enable_json:
        json_handler = logging.handlers.RotatingFileHandler(
            filename=log_dir / f'mastodon_bot_{today}.json',
            maxBytes=50 * 1024 * 1024,  # 50MB
            backupCount=7,
            encoding='utf-8'
        )
        json_handler.setLevel(logging.INFO)
        json_handler.setFormatter(JSONFormatter())
        root_logger.addHandler(json_handler)
    
    # === 에러 전용 파일 핸들러 ===
    error_handler = logging.handlers.RotatingFileHandler(
        filename=log_dir / f'mastodon_bot_errors_{today}.log',
        maxBytes=5 * 1024 * 1024,  # 5MB
        backupCount=30,  # 30일치 보관
        encoding='utf-8'
    )
    error_handler.setLevel(logging.ERROR)
    error_formatter = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s:%(lineno)d | %(message)s\n%(pathname)s\n',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    error_handler.setFormatter(error_formatter)
    root_logger.addHandler(error_handler)
    
    # 써드파티 라이브러리 로그 레벨 조정
    logging.getLogger('googleapiclient').setLevel(logging.WARNING)
    logging.getLogger('google.auth').setLevel(logging.WARNING)
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('requests').setLevel(logging.WARNING)
    
    # APScheduler 로그 레벨 조정 (스케줄러 실행 메시지 억제)
    logging.getLogger('apscheduler').setLevel(logging.WARNING)
    logging.getLogger('apscheduler.executors').setLevel(logging.WARNING)
    logging.getLogger('apscheduler.executors.default').setLevel(logging.WARNING)
    logging.getLogger('apscheduler.scheduler').setLevel(logging.WARNING)
    
    # 초기화 완료 로그
    logger = logging.getLogger('logging_config')
    logger.info("🔧 로깅 시스템 초기화 완료")
    logger.info(f"   로그 레벨: {log_level}")
    logger.info(f"   로그 디렉토리: {log_dir}")
    logger.info(f"   콘솔 출력: {'활성화' if enable_console else '비활성화'}")
    logger.info(f"   파일 출력: {'활성화' if enable_file else '비활성화'}")
    logger.info(f"   JSON 출력: {'활성화' if enable_json else '비활성화'}")


def get_logger(name: str) -> logging.Logger:
    """
    특정 이름의 로거 반환
    
    Args:
        name: 로거 이름 (보통 모듈명)
    
    Returns:
        logging.Logger: 설정된 로거
    """
    return logging.getLogger(name)


def get_toot_logger(toot_id: Optional[str] = None,
                   user_id: Optional[str] = None,
                   scheduled_time: Optional[str] = None) -> TootLoggerAdapter:
    """
    툿 관련 로그를 위한 특수 로거 반환
    
    Args:
        toot_id: 툿 ID
        user_id: 사용자 ID
        scheduled_time: 예약 시간
    
    Returns:
        TootLoggerAdapter: 툿 정보가 포함된 로거
    """
    logger = logging.getLogger('toot')
    extra = {}
    
    if toot_id:
        extra['toot_id'] = toot_id
    if user_id:
        extra['user_id'] = user_id
    if scheduled_time:
        extra['scheduled_time'] = scheduled_time
    
    return TootLoggerAdapter(logger, extra)


def log_api_call(func):
    """
    API 호출을 로깅하는 데코레이터
    
    Usage:
        @log_api_call
        def some_api_function():
            pass
    """
    def wrapper(*args, **kwargs):
        logger = logging.getLogger(func.__module__)
        func_name = func.__name__
        
        logger.debug(f"🌐 API 호출 시작: {func_name}")
        start_time = datetime.now()
        
        try:
            result = func(*args, **kwargs)
            duration = (datetime.now() - start_time).total_seconds()
            logger.info(f"✅ API 호출 성공: {func_name} ({duration:.2f}초)")
            return result
            
        except Exception as e:
            duration = (datetime.now() - start_time).total_seconds()
            logger.error(f"❌ API 호출 실패: {func_name} ({duration:.2f}초) - {str(e)}")
            raise
    
    return wrapper


def log_performance(func):
    """
    함수 실행 성능을 로깅하는 데코레이터
    
    Usage:
        @log_performance
        def some_heavy_function():
            pass
    """
    def wrapper(*args, **kwargs):
        logger = logging.getLogger(func.__module__)
        func_name = func.__name__
        
        start_time = datetime.now()
        
        try:
            result = func(*args, **kwargs)
            duration = (datetime.now() - start_time).total_seconds()
            
            if duration > 1.0:  # 1초 이상이면 WARNING
                logger.warning(f"⏱️ 느린 실행: {func_name} ({duration:.2f}초)")
            else:
                logger.debug(f"⚡ 실행 완료: {func_name} ({duration:.2f}초)")
            
            return result
            
        except Exception as e:
            duration = (datetime.now() - start_time).total_seconds()
            logger.error(f"💥 실행 실패: {func_name} ({duration:.2f}초) - {str(e)}")
            raise
    
    return wrapper


class LogContext:
    """
    로그 컨텍스트 매니저
    특정 작업 단위의 로그를 그룹화합니다.
    
    Usage:
        with LogContext('데이터 동기화') as ctx:
            ctx.log_step('시트 데이터 조회')
            # ... 작업 수행
            ctx.log_step('캐시 업데이트')
    """
    
    def __init__(self, operation_name: str, logger_name: str = 'context'):
        self.operation_name = operation_name
        self.logger = logging.getLogger(logger_name)
        self.start_time = None
        self.step_count = 0
    
    def __enter__(self):
        self.start_time = datetime.now()
        self.logger.info(f"🚀 작업 시작: {self.operation_name}")
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        duration = (datetime.now() - self.start_time).total_seconds()
        
        if exc_type is None:
            self.logger.info(f"✅ 작업 완료: {self.operation_name} ({duration:.2f}초, {self.step_count}단계)")
        else:
            self.logger.error(f"❌ 작업 실패: {self.operation_name} ({duration:.2f}초) - {exc_val}")
    
    def log_step(self, step_description: str, level: str = 'info'):
        """작업 단계 로깅"""
        self.step_count += 1
        getattr(self.logger, level)(f"   #{self.step_count} {step_description}")
    
    def log_data(self, description: str, data: Dict[str, Any]):
        """데이터 정보 로깅"""
        self.logger.debug(f"   📊 {description}: {json.dumps(data, ensure_ascii=False, indent=2)}")


def create_daily_log_summary(log_dir: Optional[Path] = None) -> Dict[str, Any]:
    """
    일일 로그 요약 생성
    
    Args:
        log_dir: 로그 디렉토리 경로
    
    Returns:
        Dict[str, Any]: 로그 요약 정보
    """
    if log_dir is None:
        log_dir = getattr(config, 'LOG_DIR', Path(__file__).parent.parent / 'logs')
    
    log_dir = Path(log_dir)
    today = datetime.now().strftime('%Y%m%d')
    log_file = log_dir / f'mastodon_bot_{today}.log'
    
    summary = {
        'date': today,
        'log_file': str(log_file),
        'total_lines': 0,
        'level_counts': {'DEBUG': 0, 'INFO': 0, 'WARNING': 0, 'ERROR': 0, 'CRITICAL': 0},
        'errors': [],
        'warnings': []
    }
    
    if not log_file.exists():
        return summary
    
    try:
        with open(log_file, 'r', encoding='utf-8') as f:
            for line in f:
                summary['total_lines'] += 1
                
                # 로그 레벨 카운트
                for level in summary['level_counts']:
                    if f'[{level:>8}]' in line:
                        summary['level_counts'][level] += 1
                        
                        # 에러/경고 메시지 수집 (최대 10개)
                        if level == 'ERROR' and len(summary['errors']) < 10:
                            summary['errors'].append(line.strip())
                        elif level == 'WARNING' and len(summary['warnings']) < 10:
                            summary['warnings'].append(line.strip())
                        break
    
    except Exception as e:
        logger = logging.getLogger('logging_config')
        logger.error(f"로그 요약 생성 실패: {e}")
    
    return summary


# 전역 로거들 (편의용)
logger = logging.getLogger('mastodon_bot')
toot_logger = get_toot_logger()


if __name__ == "__main__":
    """로깅 시스템 테스트"""
    print("🧪 로깅 시스템 테스트 시작...")
    
    # 로깅 시스템 초기화
    setup_logging(log_level='DEBUG')
    
    # 테스트 로거들
    test_logger = get_logger('test')
    test_toot_logger = get_toot_logger(toot_id='12345', user_id='testuser')
    
    # 다양한 로그 레벨 테스트
    test_logger.debug("디버그 메시지 테스트")
    test_logger.info("정보 메시지 테스트")
    test_logger.warning("경고 메시지 테스트")
    test_logger.error("에러 메시지 테스트")
    
    # 툿 로거 테스트
    test_toot_logger.info("툿 관련 로그 테스트")
    
    # 컨텍스트 매니저 테스트
    with LogContext('테스트 작업') as ctx:
        ctx.log_step('첫 번째 단계')
        ctx.log_step('두 번째 단계')
        ctx.log_data('테스트 데이터', {'count': 5, 'status': 'ok'})
    
    # 데코레이터 테스트
    @log_performance
    def test_function():
        import time
        time.sleep(0.1)
        return "테스트 완료"
    
    result = test_function()
    test_logger.info(f"함수 결과: {result}")
    
    # 로그 요약 테스트
    summary = create_daily_log_summary()
    test_logger.info(f"오늘의 로그 요약: {summary['total_lines']}줄")
    
    print("✅ 로깅 시스템 테스트 완료!")
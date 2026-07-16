"""
툿수 모니터링 자동봇 설정 관리
환경 변수를 로드하고 애플리케이션 전반의 설정을 관리합니다.
"""

import os
import sys
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

# VM 환경 대응 - 현재 파일의 경로를 기준으로 프로젝트 루트 설정
current_dir = Path(__file__).parent
project_root = current_dir.parent
sys.path.append(str(project_root))

# .env 파일 로드 (표준 관행)
# env.txt는 예시 파일로만 사용하고, 실제 설정은 .env 파일에 저장
env_path = project_root / '.env'
env_txt_path = project_root / 'env.txt'

# .env 파일을 우선 로드 (표준)
if env_path.exists():
    load_dotenv(env_path)
    print(f"[OK] .env file loaded: {env_path}")
elif env_txt_path.exists():
    # .env가 없으면 env.txt를 로드 (하위 호환성)
    load_dotenv(env_txt_path)
    print(f"[OK] env.txt file loaded: {env_txt_path} (권장: .env 파일 사용)")
else:
    print(f"[WARNING] .env or env.txt file not found: {env_path} or {env_txt_path}")


class Config:
    """툿수 모니터링 봇 설정 클래스"""
    
    # 기본 경로 설정
    BASE_DIR = project_root
    
    # 마스토돈 API 설정
    MASTODON_ACCESS_TOKEN: str = os.getenv('MASTODON_ACCESS_TOKEN', '')
    MASTODON_API_BASE_URL: str = os.getenv('MASTODON_API_BASE_URL', '')
    
    # Google Sheets 설정
    GOOGLE_CREDENTIALS_PATH: str = os.getenv(
        'GOOGLE_CREDENTIALS_PATH',
        str(BASE_DIR / 'credentials.json')
    )
    TOOT_SHEET_ID: str = os.getenv('TOOT_SHEET_ID', '')
    SHOP_SHEET_ID: str = os.getenv('SHOP_SHEET_ID', '')

    # 워크시트 이름 설정
    TOOT_WORKSHEET_NAME: str = os.getenv('TOOT_WORKSHEET_NAME', '관리')
    SHOP_WORKSHEET_NAME: str = os.getenv('SHOP_WORKSHEET_NAME', '명단')
    
    # 툿수 모니터링 설정
    TOOTS_PER_MONEY_REWARD: int = int(os.getenv('TOOTS_PER_MONEY_REWARD', '10'))  # 10툿마다 소지금 1
    CHECK_INTERVAL_MINUTES: int = int(os.getenv('CHECK_INTERVAL_MINUTES', '20'))  # 20분 간격
    MONEY_REWARD_AMOUNT: int = int(os.getenv('MONEY_REWARD_AMOUNT', '1'))  # 소지금 지급량

    # 하위 호환성을 위한 설정 (기존 코드 호환)
    TOOTS_PER_REWARD: int = TOOTS_PER_MONEY_REWARD
    REWARD_AMOUNT: int = MONEY_REWARD_AMOUNT
    
    # API 제한 대응 설정
    MAX_RETRIES: int = int(os.getenv('MAX_RETRIES', '5'))
    BASE_WAIT_TIME: int = int(os.getenv('BASE_WAIT_TIME', '2'))  # 초
    MAX_WAIT_TIME: int = int(os.getenv('MAX_WAIT_TIME', '60'))   # 최대 대기 시간
    
    # 배치 처리 설정 (Google Sheets API 제한 최적화)
    BATCH_SIZE: int = int(os.getenv('BATCH_SIZE', '10'))  # 배치당 처리할 사용자 수 (3→10으로 증가, 배치 업데이트 사용)
    BATCH_INTERVAL_MINUTES: int = int(os.getenv('BATCH_INTERVAL_MINUTES', '1'))  # 배치 간 휴식 시간 (분)
    FINAL_BATCH_INTERVAL_MINUTES: int = int(os.getenv('FINAL_BATCH_INTERVAL_MINUTES', '15'))  # 마지막 배치 전 휴식 시간 (분)

    # 새로운 즉시 처리 방식 설정
    IMMEDIATE_REWARD_PROCESSING: bool = os.getenv('IMMEDIATE_REWARD_PROCESSING', 'True').lower() == 'true'  # 즉시 재화 처리 활성화

    # API 제한 대응 설정 (Google Sheets 제한 고려) - 배치 업데이트로 최적화됨
    API_CALL_DELAY_SECONDS: float = float(os.getenv('API_CALL_DELAY_SECONDS', '1.0'))  # API 호출 간 딜레이 (초) (1.2→1.0으로 단축, 배치 업데이트 사용)
    SHEET_UPDATE_DELAY_SECONDS: float = float(os.getenv('SHEET_UPDATE_DELAY_SECONDS', '1.0'))  # 시트 업데이트 간 딜레이 (초) (2.5→1.0으로 단축, 배치 업데이트 사용)
    CACHE_DURATION_MINUTES: int = int(os.getenv('CACHE_DURATION_MINUTES', '45'))  # 캐시 지속 시간 (분) (30→45로 증가)
    
    # 로그 설정
    LOG_LEVEL: str = os.getenv('LOG_LEVEL', 'INFO')
    LOG_FILE_PATH: str = os.getenv('LOG_FILE_PATH', str(BASE_DIR / 'logs' / 'toot_monitor.log'))
    LOG_MAX_BYTES: int = int(os.getenv('LOG_MAX_BYTES', '10485760'))  # 10MB
    LOG_BACKUP_COUNT: int = int(os.getenv('LOG_BACKUP_COUNT', '5'))
    
    # 디버그 설정
    DEBUG_MODE: bool = os.getenv('DEBUG_MODE', 'False').lower() == 'true'
    ENABLE_CONSOLE_LOG: bool = os.getenv('ENABLE_CONSOLE_LOG', 'True').lower() == 'true'
    
    # 시스템 관리자 설정
    SYSTEM_ADMIN_ID: str = os.getenv('SYSTEM_ADMIN_ID', '')
    NOTIFICATION_ENABLED: bool = os.getenv('NOTIFICATION_ENABLED', 'True').lower() == 'true'
    
    # 툿수 관리 시트 헤더 설정
    TOOT_SHEET_HEADERS = {
        'NAME': '이름',
        'ID': '아이디',
        'CURRENT_MONEY': '현재 소지금',
        'RECENT_TOOTS': '최근 확인 툿수',
        'TOTAL_REWARDS': '누적 지급 재화',
        'LAST_UPDATE': '마지막 업데이트'
    }
    
    # 에러 메시지
    ERROR_MESSAGES = {
        'USER_NOT_FOUND': '등록되지 않은 사용자입니다.',
        'SHEET_ACCESS_ERROR': '시트에 접근할 수 없습니다.',
        'API_ERROR': 'API 호출 중 오류가 발생했습니다.',
        'TEMPORARY_ERROR': '일시적인 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.',
        'MASTODON_API_ERROR': '마스토돈 API 호출 실패',
        'GOOGLE_SHEETS_ERROR': 'Google Sheets 접근 실패'
    }
    
    # 성공 메시지
    SUCCESS_MESSAGES = {
        'REWARD_GIVEN': '재화 지급 완료',
        'TOOTS_UPDATED': '툿수 업데이트 완료',
        'SHEET_UPDATED': '시트 업데이트 완료',
        'MONITORING_STARTED': '툿수 모니터링 시작',
        'MONITORING_STOPPED': '툿수 모니터링 중지'
    }
    
    # 데이터 검증 설정
    MAX_NORMAL_TOOT_INCREASE: int = int(os.getenv('MAX_NORMAL_TOOT_INCREASE', '1000'))
    
    @classmethod
    def get_credentials_path(cls) -> Path:
        """
        Google 인증 파일의 경로를 반환합니다.
        
        Returns:
            Path: 인증 파일 경로
        """
        cred_path = Path(cls.GOOGLE_CREDENTIALS_PATH)
        if not cred_path.is_absolute():
            cred_path = cls.BASE_DIR / cred_path
        return cred_path
    
    @classmethod
    def get_log_file_path(cls) -> Path:
        """
        로그 파일 경로를 반환하고 디렉토리를 생성합니다.
        
        Returns:
            Path: 로그 파일 경로
        """
        log_path = Path(cls.LOG_FILE_PATH)
        if not log_path.is_absolute():
            log_path = cls.BASE_DIR / log_path
        
        # 로그 디렉토리 생성
        log_path.parent.mkdir(parents=True, exist_ok=True)
        
        return log_path
    
    @classmethod
    def validate_required_settings(cls) -> tuple[bool, list[str]]:
        """
        필수 설정값들을 검증합니다.
        
        Returns:
            tuple[bool, list[str]]: (검증 성공 여부, 오류 메시지 리스트)
        """
        errors = []
        
        # 필수 환경 변수 검사
        required_vars = [
            ('MASTODON_ACCESS_TOKEN', cls.MASTODON_ACCESS_TOKEN),
            ('MASTODON_API_BASE_URL', cls.MASTODON_API_BASE_URL),
        ]
        
        for var_name, var_value in required_vars:
            if not var_value or var_value.strip() == '':
                errors.append(f"필수 환경 변수 '{var_name}'가 설정되지 않았습니다.")
        
        # 마스토돈 API URL 형식 검사
        if cls.MASTODON_API_BASE_URL and not cls.MASTODON_API_BASE_URL.startswith(('http://', 'https://')):
            errors.append("MASTODON_API_BASE_URL은 http:// 또는 https://로 시작해야 합니다.")
        
        # Google 인증 파일 존재 확인
        cred_path = cls.get_credentials_path()
        if not cred_path.exists():
            errors.append(f"Google 인증 파일을 찾을 수 없습니다: {cred_path}")
        
        # 숫자 설정값 범위 검사
        numeric_checks = [
            ('TOOTS_PER_MONEY_REWARD', cls.TOOTS_PER_MONEY_REWARD, 1, 1000),
            ('CHECK_INTERVAL_MINUTES', cls.CHECK_INTERVAL_MINUTES, 1, 1440),  # 최대 24시간까지 허용
            ('MONEY_REWARD_AMOUNT', cls.MONEY_REWARD_AMOUNT, 1, 100),
            ('MAX_RETRIES', cls.MAX_RETRIES, 1, 10),
            ('BASE_WAIT_TIME', cls.BASE_WAIT_TIME, 1, 60),
            ('BATCH_SIZE', cls.BATCH_SIZE, 1, 50),
            ('BATCH_INTERVAL_MINUTES', cls.BATCH_INTERVAL_MINUTES, 1, 60),
            ('FINAL_BATCH_INTERVAL_MINUTES', cls.FINAL_BATCH_INTERVAL_MINUTES, 1, 60),
            ('MAX_NORMAL_TOOT_INCREASE', cls.MAX_NORMAL_TOOT_INCREASE, 10, 100000),
        ]
        
        for var_name, value, min_val, max_val in numeric_checks:
            if not isinstance(value, int) or value < min_val or value > max_val:
                errors.append(f"{var_name}은 {min_val}과 {max_val} 사이의 정수여야 합니다. 현재값: {value}")

        # 로그 레벨 검사
        valid_log_levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
        if cls.LOG_LEVEL.upper() not in valid_log_levels:
            errors.append(f"LOG_LEVEL은 다음 중 하나여야 합니다: {', '.join(valid_log_levels)}")
        
        return len(errors) == 0, errors
    
    @classmethod
    def get_error_message(cls, key: str) -> str:
        """에러 메시지 반환"""
        return cls.ERROR_MESSAGES.get(key, cls.ERROR_MESSAGES['TEMPORARY_ERROR'])
    
    @classmethod
    def get_success_message(cls, key: str) -> str:
        """성공 메시지 반환"""
        return cls.SUCCESS_MESSAGES.get(key, '')
    
    @classmethod
    def print_config_summary(cls):
        """설정 요약 출력 (디버깅용)"""
        print("=" * 60)
        print("🤖 툿수 모니터링 봇 설정 요약")
        print("=" * 60)
        print(f"📁 프로젝트 루트: {cls.BASE_DIR}")
        toot_id_display = cls.TOOT_SHEET_ID[:20] + "..." if len(cls.TOOT_SHEET_ID) > 20 else cls.TOOT_SHEET_ID
        shop_id_display = cls.SHOP_SHEET_ID[:20] + "..." if len(cls.SHOP_SHEET_ID) > 20 else cls.SHOP_SHEET_ID
        print(f"📊 툿수 시트 ID: {toot_id_display if cls.TOOT_SHEET_ID else '(설정되지 않음)'}")
        print(f"🏪 상점 시트 ID: {shop_id_display if cls.SHOP_SHEET_ID else '(설정되지 않음)'}")
        print(f"⏰ 체크 간격: {cls.CHECK_INTERVAL_MINUTES}분")
        print(f"💰 소지금 지급: {cls.TOOTS_PER_MONEY_REWARD}툿마다 {cls.MONEY_REWARD_AMOUNT}소지금")
        print(f"🔄 최대 재시도: {cls.MAX_RETRIES}회")
        print(f"📦 배치 크기: {cls.BATCH_SIZE}명")
        print(f"⏱️ 배치 간격: {cls.BATCH_INTERVAL_MINUTES}분")
        print(f"⏱️ 최종 배치 간격: {cls.FINAL_BATCH_INTERVAL_MINUTES}분")
        print(f"📝 로그 레벨: {cls.LOG_LEVEL}")
        print(f"🐛 디버그 모드: {cls.DEBUG_MODE}")
        print(f"🔔 알림 활성화: {cls.NOTIFICATION_ENABLED}")
        print("=" * 60)


# 설정 인스턴스 생성 (싱글톤 패턴)
config = Config()

# 시작시 설정 검증
if __name__ == "__main__":
    is_valid, errors = config.validate_required_settings()
    
    config.print_config_summary()
    
    if is_valid:
        print("✅ 모든 설정이 유효합니다!")
    else:
        print("❌ 설정 오류가 발견되었습니다:")
        for error in errors:
            print(f"  - {error}")
        sys.exit(1)
"""
봇 설정 클래스
각 봇의 독립적인 설정을 관리합니다.
"""

import os
import re
from dataclasses import dataclass, field
from typing import List, Dict, Any, Tuple, Optional, Iterable
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


_BOT_SLOT_PATTERN = re.compile(r'^BOT(\d+)_')


def discover_bot_slots(env_dict: Iterable[str]) -> List[int]:
    """
    환경변수 dict에서 정의된 BOT 슬롯 번호를 자동 탐지합니다.

    `BOT1_*`, `BOT2_*`, `BOT5_*` ... 키가 존재하는 슬롯을 찾아 오름차순으로 반환합니다.
    슬롯 번호가 비어 있어도 (예: BOT1, BOT2, BOT5만 있고 BOT3/BOT4는 없음) 정상 동작합니다.

    Args:
        env_dict: 환경변수 딕셔너리 (또는 키를 iterate 가능한 매핑)

    Returns:
        List[int]: 정렬된 슬롯 번호 목록. 없으면 빈 리스트.
    """
    slots = set()
    for key in env_dict:
        match = _BOT_SLOT_PATTERN.match(key)
        if match:
            slots.add(int(match.group(1)))
    return sorted(slots)


@dataclass
class BotConfig:
    """
    봇 설정 클래스

    각 봇의 독립적인 설정을 관리합니다.
    """

    # 기본 설정
    bot_id: str              # BOT1, BOT2, BOT3
    bot_name: str            # default_bot, store_bot, stats_bot
    enabled: bool
    access_token: str

    # 봇 타입 설정
    bot_type: str = "default"  # default, store, stats

    # 명령어 필터링
    command_filter: List[str] = field(default_factory=list)
    ignored_keywords: List[str] = field(default_factory=list)

    # 응답 설정
    response_prefix: str = ""

    # 로깅 설정
    log_file: str = "logs/bot.log"

    # 공유 설정 (모든 봇 공통)
    api_base_url: str = ""
    sheet_id: str = ""
    credentials_path: str = "credentials.json"
    system_admin_id: str = ""

    # 추가 설정
    custom_settings: Dict[str, Any] = field(default_factory=dict)

    # 도움말 시트 (봇별로 다른 도움말 시트 참조)
    help_sheet: Optional[str] = None

    def __post_init__(self):
        """초기화 후 처리"""
        # 명령어 필터와 키워드는 소문자로 정규화
        self.command_filter = [cmd.strip().lower() for cmd in self.command_filter if cmd.strip()]
        self.ignored_keywords = [kw.strip().lower() for kw in self.ignored_keywords if kw.strip()]

    def validate(self) -> Tuple[bool, List[str]]:
        """
        설정 검증

        Returns:
            (검증 성공 여부, 에러 메시지 리스트)
        """
        errors = []

        # 기본 필드 체크
        if not self.bot_id:
            errors.append("bot_id가 비어있습니다.")

        if not self.bot_name:
            errors.append("bot_name이 비어있습니다.")

        if not self.access_token:
            errors.append(f"[{self.bot_name}] access_token이 비어있습니다.")

        if not self.api_base_url:
            errors.append(f"[{self.bot_name}] api_base_url이 비어있습니다.")

        if not self.sheet_id:
            errors.append(f"[{self.bot_name}] sheet_id가 비어있습니다.")

        # 파일 경로 체크
        if self.credentials_path:
            # 상대 경로면 절대 경로로 변환
            if not os.path.isabs(self.credentials_path):
                base_dir = Path(__file__).parent.parent
                cred_path = base_dir / self.credentials_path
            else:
                cred_path = Path(self.credentials_path)

            if not cred_path.exists():
                errors.append(f"[{self.bot_name}] credentials 파일을 찾을 수 없습니다: {self.credentials_path}")

        # 로그 디렉토리 체크
        log_dir = Path(self.log_file).parent
        if not log_dir.exists():
            try:
                log_dir.mkdir(parents=True, exist_ok=True)
                logger.info(f"로그 디렉토리 생성: {log_dir}")
            except Exception as e:
                errors.append(f"[{self.bot_name}] 로그 디렉토리 생성 실패: {e}")

        return (len(errors) == 0, errors)

    def to_env_dict(self) -> Dict[str, str]:
        """
        환경 변수 딕셔너리로 변환 (subprocess용)

        Returns:
            환경 변수 딕셔너리
        """
        env_vars = {
            'BOT_ID': self.bot_id,
            'BOT_NAME': self.bot_name,
            'BOT_TYPE': self.bot_type,
            'MASTODON_ACCESS_TOKEN': self.access_token,
            'MASTODON_API_BASE_URL': self.api_base_url,
            'SHEET_ID': self.sheet_id,
            'GOOGLE_CREDENTIALS_PATH': self.credentials_path,
            'RESPONSE_PREFIX': self.response_prefix,
            'LOG_FILE_PATH': self.log_file,
            'SYSTEM_ADMIN_ID': self.system_admin_id,
        }

        # 명령어 필터
        if self.command_filter:
            env_vars['COMMAND_FILTER'] = ','.join(self.command_filter)

        # 무시 키워드
        if self.ignored_keywords:
            env_vars['BOT_IGNORED_KEYWORDS'] = ','.join(self.ignored_keywords)

        # 도움말 시트
        if self.help_sheet:
            env_vars['HELP_SHEET'] = self.help_sheet

        # 커스텀 설정
        for key, value in self.custom_settings.items():
            env_vars[key] = str(value)

        return env_vars

    @classmethod
    def from_env(cls, bot_id: str, env_dict: Dict[str, str]) -> 'BotConfig':
        """
        환경 변수에서 BotConfig 생성

        Args:
            bot_id: 봇 ID (BOT1, BOT2, ...)
            env_dict: 환경 변수 딕셔너리

        Returns:
            BotConfig 인스턴스
        """
        prefix = f"{bot_id}_"

        # 기본 설정 로드
        config = cls(
            bot_id=bot_id,
            bot_name=env_dict.get(f"{prefix}NAME", f"bot_{bot_id.lower()}"),
            enabled=env_dict.get(f"{prefix}ENABLED", "True").lower() == "true",
            access_token=env_dict.get(f"{prefix}ACCESS_TOKEN", ""),
            bot_type=env_dict.get(f"{prefix}BOT_TYPE", "default").lower(),
            command_filter=[
                cmd.strip()
                for cmd in env_dict.get(f"{prefix}COMMAND_FILTER", "").split(',')
                if cmd.strip()
            ],
            ignored_keywords=[
                kw.strip()
                for kw in env_dict.get(f"{prefix}IGNORED_KEYWORDS", "").split(',')
                if kw.strip()
            ],
            response_prefix=env_dict.get(f"{prefix}RESPONSE_PREFIX", ""),
            log_file=env_dict.get(f"{prefix}LOG_FILE", f"logs/bot_{bot_id.lower()}.log"),
            api_base_url=env_dict.get("MASTODON_API_BASE_URL", ""),
            sheet_id=env_dict.get("SHEET_ID", ""),
            credentials_path=env_dict.get("GOOGLE_CREDENTIALS_PATH", "credentials.json"),
            system_admin_id=env_dict.get("SYSTEM_ADMIN_ID", ""),
            help_sheet=env_dict.get(f"{prefix}HELP_SHEET", None)
        )

        # 커스텀 설정 로드 (BOT1_로 시작하는 키 중 위에서 처리하지 않은 것)
        excluded_keys = [
            f"{prefix}NAME", f"{prefix}ENABLED", f"{prefix}ACCESS_TOKEN",
            f"{prefix}BOT_TYPE", f"{prefix}COMMAND_FILTER", f"{prefix}IGNORED_KEYWORDS",
            f"{prefix}RESPONSE_PREFIX", f"{prefix}LOG_FILE", f"{prefix}HELP_SHEET"
        ]

        for key, value in env_dict.items():
            if key.startswith(prefix) and key not in excluded_keys:
                # prefix 제거하고 custom_settings에 저장
                custom_key = key[len(prefix):]
                config.custom_settings[custom_key] = value

        return config

    def get_info_dict(self) -> Dict[str, Any]:
        """
        봇 정보 딕셔너리로 변환 (로깅/디버깅용)

        Returns:
            봇 정보 딕셔너리
        """
        return {
            'bot_id': self.bot_id,
            'bot_name': self.bot_name,
            'bot_type': self.bot_type,
            'enabled': self.enabled,
            'command_filter': self.command_filter,
            'ignored_keywords': self.ignored_keywords,
            'response_prefix': self.response_prefix,
            'log_file': self.log_file,
            'help_sheet': self.help_sheet,
            'custom_settings_count': len(self.custom_settings)
        }

    def __repr__(self) -> str:
        """문자열 표현"""
        return (
            f"BotConfig(bot_id={self.bot_id}, bot_name={self.bot_name}, "
            f"bot_type={self.bot_type}, enabled={self.enabled}, commands={len(self.command_filter)})"
        )


def load_bot_configs_from_env(env_file_path: str = '.env') -> List[BotConfig]:
    """
    .env 파일에서 봇 설정 로드

    Args:
        env_file_path: .env 파일 경로

    Returns:
        로드된 BotConfig 리스트
    """
    # .env 파일 읽기
    env_dict = {}
    env_path = Path(env_file_path)

    if not env_path.exists():
        logger.error(f".env 파일을 찾을 수 없습니다: {env_file_path}")
        return []

    try:
        with open(env_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip()
                    # 인라인 주석 제거 (#으로 시작하는 부분 제거)
                    # config/settings.py::_load_env 와 동일한 동작 유지
                    if '#' in value:
                        value = value.split('#')[0].strip()
                    # 따옴표 제거
                    if value.startswith('"') and value.endswith('"'):
                        value = value[1:-1]
                    elif value.startswith("'") and value.endswith("'"):
                        value = value[1:-1]
                    env_dict[key] = value
    except Exception as e:
        logger.error(f".env 파일 읽기 실패: {e}")
        return []

    # 멀티 봇 모드 체크
    enable_multi_bot = env_dict.get('ENABLE_MULTI_BOT', 'False').lower() == 'true'

    if not enable_multi_bot:
        logger.info("멀티 봇 모드가 비활성화되어 있습니다. (ENABLE_MULTI_BOT=False)")
        return []

    # 슬롯 자동 탐지 — BOTn_* 키가 있는 슬롯만 스캔
    # (BOT_COUNT는 더 이상 제한 역할이 아님. 어떤 슬롯 번호든 건너뛰고 사용 가능)
    slot_numbers = discover_bot_slots(env_dict)
    if not slot_numbers:
        logger.warning("정의된 BOT 슬롯이 없습니다. (BOT1_*, BOT2_* ... 키가 없음)")
        return []

    logger.info(f"멀티 봇 모드 로드: 슬롯 {slot_numbers} 발견")

    # 봇 설정 로드 — ENABLED=True인 것만 실제로 사용
    configs = []
    for i in slot_numbers:
        bot_id = f"BOT{i}"

        try:
            config = BotConfig.from_env(bot_id, env_dict)

            # 활성화된 봇만 추가
            if config.enabled:
                # 설정 검증
                is_valid, errors = config.validate()
                if is_valid:
                    configs.append(config)
                    logger.info(f"봇 설정 로드 완료: {config.bot_name} (슬롯 {bot_id})")
                else:
                    logger.error(f"봇 설정 검증 실패: {config.bot_name} (슬롯 {bot_id})")
                    for error in errors:
                        logger.error(f"  - {error}")
            else:
                logger.info(f"봇 비활성화됨: {config.bot_name} (슬롯 {bot_id}, ENABLED=False)")

        except Exception as e:
            logger.error(f"봇 설정 로드 실패: {bot_id} - {e}", exc_info=True)

    logger.info(f"총 {len(configs)}개 봇 설정 로드 완료 (탐지된 슬롯 {len(slot_numbers)}개 중)")
    return configs


def get_bot_config_by_name(configs: List[BotConfig], bot_name: str) -> Optional[BotConfig]:
    """
    봇 이름으로 설정 찾기

    Args:
        configs: BotConfig 리스트
        bot_name: 찾을 봇 이름

    Returns:
        찾은 BotConfig 또는 None
    """
    for config in configs:
        if config.bot_name == bot_name:
            return config
    return None


def validate_all_bot_configs(configs: List[BotConfig]) -> Tuple[bool, List[str]]:
    """
    모든 봇 설정 검증

    Args:
        configs: BotConfig 리스트

    Returns:
        (전체 검증 성공 여부, 에러 메시지 리스트)
    """
    all_errors = []

    for config in configs:
        is_valid, errors = config.validate()
        if not is_valid:
            all_errors.extend(errors)

    return (len(all_errors) == 0, all_errors)

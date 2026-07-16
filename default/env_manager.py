"""
.env 파일 관리 헬퍼 스크립트
.env 파일을 대화형으로 생성하고 수정할 수 있습니다.
"""

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional, Set, Tuple, Union


@dataclass(frozen=True)
class FieldSpec:
    """전역 설정 필드 메타데이터"""

    key: str
    prompt: str
    default: Optional[str] = None
    condition: Optional[Callable[[Dict[str, str]], bool]] = None


@dataclass(frozen=True)
class SectionSpec:
    """전역 섹션 정의"""

    title: str
    keys: Tuple[str, ...]
    description: Optional[str] = None


BotCondition = Callable[['EnvManager', int, Dict[str, str]], bool]
BotDefaultFactory = Callable[[int, Dict[str, str]], Optional[str]]


@dataclass(frozen=True)
class BotFieldSpec:
    """봇별 설정 필드 메타데이터"""

    suffix: str
    prompt: str
    default: Union[None, str, BotDefaultFactory] = None
    condition: Optional[BotCondition] = None


def _as_bool(value: str) -> bool:
    return str(value).strip().lower() in ('true', '1', 'y', 'yes')


def _safe_int(value: str, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


GLOBAL_FIELD_SPECS: Dict[str, FieldSpec] = {
    'ENABLE_MULTI_BOT': FieldSpec(
        key='ENABLE_MULTI_BOT',
        prompt='멀티 봇 모드 활성화? (True/False)',
        default='True'
    ),
    'BOT_COUNT': FieldSpec(
        key='BOT_COUNT',
        prompt='봇 개수 (1-5)',
        default='3',
        condition=lambda cfg: _as_bool(cfg.get('ENABLE_MULTI_BOT', 'True'))
    ),
    'SHEET_ID': FieldSpec(
        key='SHEET_ID',
        prompt='Google Sheets ID',
        default=''
    ),
    'GOOGLE_CREDENTIALS_PATH': FieldSpec(
        key='GOOGLE_CREDENTIALS_PATH',
        prompt='Google 인증 파일 경로',
        default='credentials.json'
    ),
    'MASTODON_API_BASE_URL': FieldSpec(
        key='MASTODON_API_BASE_URL',
        prompt='Mastodon 서버 URL',
        default=''
    ),
    'SYSTEM_ADMIN_ID': FieldSpec(
        key='SYSTEM_ADMIN_ID',
        prompt='시스템 관리자 ID (콤마 구분)',
        default=''
    ),
    'CURRENCY': FieldSpec(
        key='CURRENCY',
        prompt='화폐 이름',
        default='골드'
    ),
    'CURRENCY_EUNNEUN': FieldSpec(
        key='CURRENCY_EUNNEUN',
        prompt='화폐 조사(은/는)',
        default='는'
    ),
    'MAX_MESSAGE_LENGTH': FieldSpec(
        key='MAX_MESSAGE_LENGTH',
        prompt='메시지 최대 길이',
        default='1000'
    ),
    'BOT_MAX_DICE_COUNT': FieldSpec(
        key='BOT_MAX_DICE_COUNT',
        prompt='주사위 개수 제한',
        default='20'
    ),
    'BOT_MAX_DICE_SIDES': FieldSpec(
        key='BOT_MAX_DICE_SIDES',
        prompt='주사위 면수 제한',
        default='1000'
    ),
    'RESPONSE_PREFIX': FieldSpec(
        key='RESPONSE_PREFIX',
        prompt='응답 접두사',
        default="'✶ '"
    ),
    'PREMIUM_CUSTOMC_ENABLED': FieldSpec(
        key='PREMIUM_CUSTOMC_ENABLED',
        prompt='프리미엄 커스텀 명령 활성화? (True/False)',
        default='False'
    ),
    'PREMIUM_IMAGE_ENABLED': FieldSpec(
        key='PREMIUM_IMAGE_ENABLED',
        prompt='프리미엄 이미지 기능 활성화? (True/False)',
        default='False'
    ),
    'PREMIUM_TRANSFER_ENABLED': FieldSpec(
        key='PREMIUM_TRANSFER_ENABLED',
        prompt='프리미엄 양도 기능 활성화? (True/False)',
        default='False'
    ),
    'PROCESS_MONITOR_ENABLED': FieldSpec(
        key='PROCESS_MONITOR_ENABLED',
        prompt='프로세스 모니터링 활성화? (True/False)',
        default='True'
    ),
    'AUTO_RESTART_ON_CRASH': FieldSpec(
        key='AUTO_RESTART_ON_CRASH',
        prompt='비정상 종료 시 자동 재시작? (True/False)',
        default='True'
    ),
    'RESTART_DELAY_SECONDS': FieldSpec(
        key='RESTART_DELAY_SECONDS',
        prompt='재시작 대기 시간(초)',
        default='5'
    ),
    'MAX_RESTART_ATTEMPTS': FieldSpec(
        key='MAX_RESTART_ATTEMPTS',
        prompt='재시작 최대 횟수',
        default='3'
    ),
    'HEALTHCHECK_INTERVAL_SECONDS': FieldSpec(
        key='HEALTHCHECK_INTERVAL_SECONDS',
        prompt='헬스체크 주기(초)',
        default='60'
    ),
    'ENABLE_RESOURCE_LOCKING': FieldSpec(
        key='ENABLE_RESOURCE_LOCKING',
        prompt='리소스 락 활성화? (True/False)',
        default='True'
    ),
    'RESOURCE_LOCK_TIMEOUT': FieldSpec(
        key='RESOURCE_LOCK_TIMEOUT',
        prompt='리소스 락 타임아웃(초)',
        default='30.0'
    ),
    'ENABLE_RATE_LIMITING': FieldSpec(
        key='ENABLE_RATE_LIMITING',
        prompt='Sheets Rate Limiting 활성화? (True/False)',
        default='True'
    ),
    'SHEETS_READ_RPM': FieldSpec(
        key='SHEETS_READ_RPM',
        prompt='Sheets 읽기 제한(분당)',
        default='50',
        condition=lambda cfg: _as_bool(cfg.get('ENABLE_RATE_LIMITING', 'True'))
    ),
    'SHEETS_WRITE_RPM': FieldSpec(
        key='SHEETS_WRITE_RPM',
        prompt='Sheets 쓰기 제한(분당)',
        default='50',
        condition=lambda cfg: _as_bool(cfg.get('ENABLE_RATE_LIMITING', 'True'))
    ),
    'SHEETS_GLOBAL_RPM': FieldSpec(
        key='SHEETS_GLOBAL_RPM',
        prompt='Sheets 전체 제한(분당)',
        default='90',
        condition=lambda cfg: _as_bool(cfg.get('ENABLE_RATE_LIMITING', 'True'))
    ),
    'LOG_LEVEL': FieldSpec(
        key='LOG_LEVEL',
        prompt='로그 레벨 (DEBUG/INFO/...)',
        default='INFO'
    ),
    'LOG_MAX_BYTES': FieldSpec(
        key='LOG_MAX_BYTES',
        prompt='로그 파일 최대 크기(Bytes)',
        default='10485760'
    ),
    'LOG_BACKUP_COUNT': FieldSpec(
        key='LOG_BACKUP_COUNT',
        prompt='로그 백업 파일 개수',
        default='5'
    ),
    'ENABLE_CONSOLE_LOG': FieldSpec(
        key='ENABLE_CONSOLE_LOG',
        prompt='콘솔 로그 출력? (True/False)',
        default='True'
    ),
    'POLLING_INTERVAL': FieldSpec(
        key='POLLING_INTERVAL',
        prompt='폴링 주기(초)',
        default='30'
    ),
    'CACHE_TTL': FieldSpec(
        key='CACHE_TTL',
        prompt='캐시 TTL(초)',
        default='3600'
    ),
    'FORTUNE_CACHE_ENABLED': FieldSpec(
        key='FORTUNE_CACHE_ENABLED',
        prompt='운세 캐시 사용? (True/False)',
        default='True'
    ),
    'DEBUG_MODE': FieldSpec(
        key='DEBUG_MODE',
        prompt='디버그 모드 활성화? (True/False)',
        default='False'
    ),
    'HELP_SHEET': FieldSpec(
        key='HELP_SHEET',
        prompt='기본 도움말 시트 이름',
        default='도움말'
    ),
    'LIST_SHEET': FieldSpec(
        key='LIST_SHEET',
        prompt='명단 시트 이름',
        default='명단'
    ),
    'CUSTOM_SHEET': FieldSpec(
        key='CUSTOM_SHEET',
        prompt='커스텀 명령어 시트 이름',
        default='커스텀'
    ),
    'FORTUNE_SHEET': FieldSpec(
        key='FORTUNE_SHEET',
        prompt='운세 시트 이름',
        default='운세'
    ),
    'SHOP_SHEET': FieldSpec(
        key='SHOP_SHEET',
        prompt='상점 시트 이름',
        default='상점'
    ),
}


GLOBAL_SECTIONS: Tuple[SectionSpec, ...] = (
    SectionSpec(
        title='기본 설정',
        description='멀티 봇 및 외부 서비스 연결을 위한 필수 항목',
        keys=('ENABLE_MULTI_BOT', 'BOT_COUNT', 'SHEET_ID', 'GOOGLE_CREDENTIALS_PATH',
              'MASTODON_API_BASE_URL', 'SYSTEM_ADMIN_ID')
    ),
    SectionSpec(
        title='게임/기능 설정',
        description='화폐 및 명령어 제한 관련 옵션',
        keys=('CURRENCY', 'CURRENCY_EUNNEUN', 'MAX_MESSAGE_LENGTH',
              'BOT_MAX_DICE_COUNT', 'BOT_MAX_DICE_SIDES',
              'PREMIUM_CUSTOMC_ENABLED', 'PREMIUM_IMAGE_ENABLED', 'PREMIUM_TRANSFER_ENABLED',
              'RESPONSE_PREFIX')
    ),
    SectionSpec(
        title='프로세스 모니터링',
        description='자동 재시작 및 헬스체크 설정',
        keys=('PROCESS_MONITOR_ENABLED', 'AUTO_RESTART_ON_CRASH', 'RESTART_DELAY_SECONDS',
              'MAX_RESTART_ATTEMPTS', 'HEALTHCHECK_INTERVAL_SECONDS')
    ),
    SectionSpec(
        title='공유 리소스 관리',
        description='Google Sheets Rate Limiting 및 락 설정',
        keys=('ENABLE_RESOURCE_LOCKING', 'RESOURCE_LOCK_TIMEOUT', 'ENABLE_RATE_LIMITING',
              'SHEETS_READ_RPM', 'SHEETS_WRITE_RPM', 'SHEETS_GLOBAL_RPM')
    ),
    SectionSpec(
        title='로깅 설정',
        description='로그 출력 및 보관 관련 옵션',
        keys=('LOG_LEVEL', 'LOG_MAX_BYTES', 'LOG_BACKUP_COUNT', 'ENABLE_CONSOLE_LOG')
    ),
    SectionSpec(
        title='운영 옵션',
        description='캐시/폴링/디버그 등 기타 설정',
        keys=('POLLING_INTERVAL', 'CACHE_TTL', 'FORTUNE_CACHE_ENABLED', 'DEBUG_MODE',
              'HELP_SHEET', 'LIST_SHEET', 'CUSTOM_SHEET', 'FORTUNE_SHEET', 'SHOP_SHEET')
    ),
)


BOT_PRESETS: Dict[Union[int, str], Dict[str, Union[str, Dict[str, Optional[str]]]]] = {
    1: {
        'label': 'SYSTEM',
        'defaults': {
            'ENABLED': 'True',
            'NAME': 'system_bot',
            'BOT_TYPE': 'system',
            'COMMAND_FILTER': '커스텀,도움말,가방,상태 확인,ndm,운세,랜덤,캐시 리셋,스탯 변경,소지금 관리,사용',
            'IGNORED_KEYWORDS': '상점,구매,선물,양도,설명',
            'HELP_SHEET': '도움말(시스템)',
            'LOG_FILE': 'logs/bot_system.log'
        }
    },
    2: {
        'label': 'STORE',
        'defaults': {
            'ENABLED': 'True',
            'NAME': 'store_bot',
            'BOT_TYPE': 'store',
            'COMMAND_FILTER': '도움말,상점,구매,아이템 설명,양도,소지금 관리,가방',
            'IGNORED_KEYWORDS': '주사위,운세,커스텀,랜덤,스탯,사용',
            'HELP_SHEET': '도움말(상점)',
            'LOG_FILE': 'logs/bot_store.log',
            'PREMIUM_TRANSFER_ENABLED': 'True'
        }
    },
    3: {
        'label': 'STATS',
        'defaults': {
            'ENABLED': 'False',
            'NAME': 'admin_bot',
            'BOT_TYPE': 'stats',
            'COMMAND_FILTER': '',
            'IGNORED_KEYWORDS': '',
            'HELP_SHEET': '',
            'LOG_FILE': 'logs/bot_admin.log'
        }
    },
    'default': {
        'label': 'BOT',
        'defaults': {
            'ENABLED': 'True',
            'NAME': None,
            'BOT_TYPE': 'system',
            'COMMAND_FILTER': '',
            'IGNORED_KEYWORDS': '',
            'HELP_SHEET': '',
            'LOG_FILE': None
        }
    }
}


def _help_sheet_condition(manager: 'EnvManager', index: int, preset: Dict[str, Optional[str]]) -> bool:
    current_filter = manager.get_value(
        f'BOT{index}_COMMAND_FILTER',
        preset.get('COMMAND_FILTER') or ''
    )
    return 'help' in current_filter.lower()


BOT_FIELD_SPECS: Tuple[BotFieldSpec, ...] = (
    BotFieldSpec(
        suffix='ENABLED',
        prompt='활성화? (True/False)',
        default='True'
    ),
    BotFieldSpec(
        suffix='NAME',
        prompt='봇 이름',
        default=lambda idx, preset: preset.get('NAME') or f'bot_{idx}'
    ),
    BotFieldSpec(
        suffix='ACCESS_TOKEN',
        prompt='액세스 토큰',
        default=''
    ),
    BotFieldSpec(
        suffix='BOT_TYPE',
        prompt='봇 타입 (system/store/admin)',
        default=lambda idx, preset: preset.get('BOT_TYPE') or 'system'
    ),
    BotFieldSpec(
        suffix='COMMAND_FILTER',
        prompt='명령어 필터 (콤마 구분)',
        default=lambda idx, preset: preset.get('COMMAND_FILTER') or ''
    ),
    BotFieldSpec(
        suffix='IGNORED_KEYWORDS',
        prompt='무시할 키워드 (콤마 구분)',
        default=lambda idx, preset: preset.get('IGNORED_KEYWORDS') or ''
    ),
    BotFieldSpec(
        suffix='LOG_FILE',
        prompt='로그 파일 경로',
        default=lambda idx, preset: preset.get('LOG_FILE') or f'logs/bot_{idx}.log'
    ),
    BotFieldSpec(
        suffix='HELP_SHEET',
        prompt='도움말 시트 이름',
        default=lambda idx, preset: preset.get('HELP_SHEET') or '',
        condition=lambda manager, idx, preset: _help_sheet_condition(manager, idx, preset)
    ),
    BotFieldSpec(
        suffix='PREMIUM_TRANSFER_ENABLED',
        prompt='프리미엄 양도 기능 활성화? (True/False)',
        default=lambda idx, preset: preset.get('PREMIUM_TRANSFER_ENABLED'),
        condition=lambda manager, idx, preset: 'PREMIUM_TRANSFER_ENABLED' in preset
    ),
)


DEFAULT_VALUES: Dict[str, str] = {
    'PROCESS_MONITOR_ENABLED': 'True',
    'AUTO_RESTART_ON_CRASH': 'True',
    'RESTART_DELAY_SECONDS': '5',
    'MAX_RESTART_ATTEMPTS': '3',
    'HEALTHCHECK_INTERVAL_SECONDS': '60',
    'RESOURCE_LOCK_TIMEOUT': '30.0',
    'HELP_SHEET': '도움말',
    'LIST_SHEET': '명단',
    'CUSTOM_SHEET': '커스텀',
    'FORTUNE_SHEET': '운세',
    'SHOP_SHEET': '상점',
    'LOG_LEVEL': 'INFO',
    'LOG_MAX_BYTES': '10485760',
    'LOG_BACKUP_COUNT': '5',
    'ENABLE_CONSOLE_LOG': 'True',
    'POLLING_INTERVAL': '30',
    'CACHE_TTL': '3600',
    'FORTUNE_CACHE_ENABLED': 'True',
    'CURRENCY': '골드',
    'CURRENCY_EUNNEUN': '는',
    'MAX_MESSAGE_LENGTH': '500',
    'RESPONSE_PREFIX': "'✶ '",
    'BOT_MAX_DICE_COUNT': '20',
    'BOT_MAX_DICE_SIDES': '1000',
    'DEBUG_MODE': 'False',
    'PREMIUM_CUSTOMC_ENABLED': 'False',
    'PREMIUM_IMAGE_ENABLED': 'False',
    'PREMIUM_TRANSFER_ENABLED': 'False'
}


class EnvManager:
    """환경 변수 관리 클래스"""

    def __init__(self, env_path: str = '.env'):
        self.env_path = Path(env_path)
        self.example_path = Path('env.txt')
        self.config: Dict[str, str] = {}

    def _should_prompt(self, field_spec: FieldSpec) -> bool:
        if field_spec.condition is None:
            return True
        return field_spec.condition(self.config)

    def _prompt_field(self, field_spec: FieldSpec) -> None:
        current = self.get_value(field_spec.key, field_spec.default or '')
        placeholder = current or '입력 필요'
        value = input(f"{field_spec.prompt} [{placeholder}]: ").strip()
        if not value:
            value = current
        if value is None:
            value = ''
        self.set_value(field_spec.key, value)

    def _get_bot_preset(self, index: int) -> Dict[str, Union[str, Dict[str, Optional[str]]]]:
        return BOT_PRESETS.get(index, BOT_PRESETS['default'])

    def _should_prompt_bot_field(
        self,
        spec: BotFieldSpec,
        index: int,
        preset_defaults: Dict[str, Optional[str]]
    ) -> bool:
        if spec.condition is None:
            return True
        return spec.condition(self, index, preset_defaults)

    def _resolve_bot_default(
        self,
        spec: BotFieldSpec,
        index: int,
        preset_defaults: Dict[str, Optional[str]]
    ) -> str:
        if callable(spec.default):
            value = spec.default(index, preset_defaults)
            if value is not None:
                return value
        if spec.suffix in preset_defaults and preset_defaults[spec.suffix] is not None:
            return preset_defaults[spec.suffix] or ''
        return spec.default if isinstance(spec.default, str) else ''

    def _prompt_bot_fields(self, index: int) -> None:
        preset = self._get_bot_preset(index)
        preset_defaults: Dict[str, Optional[str]] = preset.get('defaults', {})  # type: ignore[assignment]
        label = preset.get('label', f'BOT{index}')

        print()
        print(f"=== 봇 {index} 설정 ({label}) ===")
        print()

        for spec in BOT_FIELD_SPECS:
            key = f'BOT{index}_{spec.suffix}'

            if spec.suffix != 'ENABLED':
                enabled_key = f'BOT{index}_ENABLED'
                enabled_value = self.get_value(
                    enabled_key,
                    preset_defaults.get('ENABLED') or 'True'
                )
                if not _as_bool(enabled_value):
                    continue

            if not self._should_prompt_bot_field(spec, index, preset_defaults):
                continue

            current_default = self.get_value(
                key,
                self._resolve_bot_default(spec, index, preset_defaults)
            )
            placeholder = current_default or '입력 필요'
            value = input(f"봇 {index} - {spec.prompt} [{placeholder}]: ").strip()
            if not value:
                value = current_default
            if value is None:
                value = ''
            self.set_value(key, value)

    def load_existing(self) -> bool:
        """기존 .env 파일 로드"""
        if not self.env_path.exists():
            return False

        try:
            with open(self.env_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, value = line.split('=', 1)
                        self.config[key.strip()] = value.strip()
            return True
        except Exception as e:
            print(f"[오류] .env 파일 로드 실패: {e}")
            return False

    def get_value(self, key: str, default: str = '') -> str:
        """설정값 가져오기"""
        return self.config.get(key, default)

    def set_value(self, key: str, value: str):
        """설정값 설정"""
        self.config[key] = value

    def interactive_setup(self):
        """대화형 설정"""
        print("=" * 60)
        print("멀티 봇 환경 설정")
        print("=" * 60)
        print()

        has_existing = self.load_existing()
        if has_existing:
            print("[정보] 기존 .env 파일을 찾았습니다.")
            use_existing = input("기존 설정을 유지하시겠습니까? (Y/n): ").strip().lower()
            if use_existing == 'n':
                self.config = {}

        for section in GLOBAL_SECTIONS:
            print()
            print(f"=== {section.title} ===")
            if section.description:
                print(section.description)
            print()

            for key in section.keys:
                spec = GLOBAL_FIELD_SPECS[key]
                if not self._should_prompt(spec):
                    if spec.default is not None and key not in self.config:
                        self.set_value(key, spec.default)
                    continue
                self._prompt_field(spec)

        enable_multi = _as_bool(self.get_value('ENABLE_MULTI_BOT', 'True'))
        if enable_multi:
            bot_count = _safe_int(self.get_value('BOT_COUNT', '3'), default=3)
            bot_count = max(1, min(bot_count, 10))
            self.set_value('BOT_COUNT', str(bot_count))

            for i in range(1, bot_count + 1):
                self._prompt_bot_fields(i)

        self._set_defaults()

        print()
        print("=== 설정 완료 ===")
        print()

    def _set_defaults(self):
        """기본값 설정"""
        for key, value in DEFAULT_VALUES.items():
            self.config.setdefault(key, value)

        if _as_bool(self.config.get('ENABLE_MULTI_BOT', 'True')):
            bot_count = _safe_int(self.config.get('BOT_COUNT', '0'), default=0)
            for i in range(1, bot_count + 1):
                preset = self._get_bot_preset(i)
                preset_defaults: Dict[str, Optional[str]] = preset.get('defaults', {})  # type: ignore[assignment]
                for suffix, default_value in preset_defaults.items():
                    if default_value is None:
                        continue
                    self.config.setdefault(f'BOT{i}_{suffix}', default_value)
                # 공유 접두사를 사용하므로 봇 개별 접두사는 제거
                self.config.pop(f'BOT{i}_RESPONSE_PREFIX', None)

    def save(self) -> bool:
        """설정 저장"""
        try:
            if self.env_path.exists():
                backup_path = Path(f"{self.env_path}.backup")
                with open(self.env_path, 'r', encoding='utf-8') as src:
                    with open(backup_path, 'w', encoding='utf-8') as dst:
                        dst.write(src.read())
                print(f"[정보] 기존 파일 백업: {backup_path}")

            with open(self.env_path, 'w', encoding='utf-8') as f:
                f.write("# ============================================================\n")
                f.write("# 멀티 봇 설정 (자동 생성)\n")
                f.write("# ============================================================\n")
                f.write("# 생성 도구: env_manager.py\n")
                f.write("\n")

                written_keys: Set[str] = set()

                for section in GLOBAL_SECTIONS:
                    present_keys = [key for key in section.keys if key in self.config]
                    if not present_keys:
                        continue

                    f.write("# ============================================================\n")
                    f.write(f"# {section.title}\n")
                    if section.description:
                        f.write(f"# {section.description}\n")
                    f.write("# ============================================================\n")
                    f.write("\n")

                    for key in present_keys:
                        value = self.config[key]
                        f.write(f"{key}={value}\n")
                        written_keys.add(key)
                    f.write("\n")

                if _as_bool(self.config.get('ENABLE_MULTI_BOT', 'True')):
                    bot_count = _safe_int(self.config.get('BOT_COUNT', '0'), default=0)
                    for i in range(1, bot_count + 1):
                        prefix = f'BOT{i}_'
                        bot_keys = [key for key in self.config.keys() if key.startswith(prefix)]
                        if not bot_keys:
                            continue

                        preset = self._get_bot_preset(i)
                        label = preset.get('label', f'BOT{i}')

                        f.write("# ============================================================\n")
                        f.write(f"# 봇 {i} 설정 ({label})\n")
                        f.write("# ============================================================\n")
                        f.write("\n")

                        ordered = [f'BOT{i}_{spec.suffix}' for spec in BOT_FIELD_SPECS]
                        seen: Set[str] = set()

                        for key in ordered:
                            if key in self.config:
                                f.write(f"{key}={self.config[key]}\n")
                                written_keys.add(key)
                                seen.add(key)

                        extra_keys = sorted(set(bot_keys) - seen)
                        if extra_keys and seen:
                            f.write("# -- 추가 봇 설정 --\n")
                        for key in extra_keys:
                            f.write(f"{key}={self.config[key]}\n")
                            written_keys.add(key)

                        f.write("\n")

                remaining = [
                    key for key in sorted(self.config.keys())
                    if key not in written_keys
                ]

                if remaining:
                    f.write("# ============================================================\n")
                    f.write("# 기타 설정\n")
                    f.write("# ============================================================\n")
                    f.write("\n")
                    for key in remaining:
                        f.write(f"{key}={self.config[key]}\n")

            print(f"[성공] 설정 파일 저장: {self.env_path}")
            return True

        except Exception as e:
            print(f"[오류] 설정 파일 저장 실패: {e}")
            return False

    def quick_edit(self, key: str, value: str) -> bool:
        """빠른 수정"""
        self.load_existing()
        self.set_value(key, value)
        return self.save()

    def show_current(self):
        """현재 설정 표시"""
        if not self.load_existing():
            print("[오류] .env 파일을 찾을 수 없습니다.")
            return

        print("=" * 60)
        print("현재 설정")
        print("=" * 60)
        print()

        for key, value in sorted(self.config.items()):
            if 'TOKEN' in key and value:
                display_value = value[:10] + '...' if len(value) > 10 else value
            else:
                display_value = value
            print(f"{key}={display_value}")


def main():
    """메인 함수"""
    manager = EnvManager()

    if len(sys.argv) > 1:
        command = sys.argv[1]

        if command == 'show':
            manager.show_current()

        elif command == 'edit' and len(sys.argv) >= 4:
            key = sys.argv[2]
            value = sys.argv[3]
            if manager.quick_edit(key, value):
                print(f"[성공] {key}={value}")

        elif command == 'setup':
            manager.interactive_setup()
            manager.save()

        else:
            print("사용법:")
            print("  python env_manager.py setup    - 대화형 설정")
            print("  python env_manager.py show     - 현재 설정 보기")
            print("  python env_manager.py edit KEY VALUE - 빠른 수정")

    else:
        manager.interactive_setup()

        print()
        save = input("설정을 저장하시겠습니까? (Y/n): ").strip().lower()
        if save != 'n':
            manager.save()
            print()
            print("[완료] 이제 'python main.py'로 봇을 실행하세요!")


if __name__ == '__main__':
    main()

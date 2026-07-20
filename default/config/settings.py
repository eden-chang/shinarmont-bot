"""
설정 관리 모듈
환경 변수를 로드하고 애플리케이션 전반의 설정을 관리합니다.
"""

import os
import logging
import sys
from pathlib import Path
from typing import Optional

# 경로 설정 (VM 환경 대응)
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))


# .env 파일을 먼저 로드
def _load_env():
    """환경 변수를 먼저 로드하는 함수"""
    base_dir = Path(__file__).parent.parent
    env_path = base_dir / '.env'

    if env_path.exists():
        with open(env_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip()
                    # 인라인 주석 제거 (#으로 시작하는 부분 제거)
                    if '#' in value:
                        value = value.split('#')[0].strip()
                    # 따옴표 제거
                    if value.startswith('"') and value.endswith('"'):
                        value = value[1:-1]
                    elif value.startswith("'") and value.endswith("'"):
                        value = value[1:-1]
                    os.environ.setdefault(key, value)


# 환경변수 먼저 로드
_load_env()


_logger = logging.getLogger('config')


def _env_num(key: str, default, cast=int):
    """숫자 설정 읽기 — **빈 값·오타에 죽지 않는다**.

    `.env`의 `KEY=`(빈 값)는 `os.getenv`가 `''`를 주므로 `int('')`가 터진다.
    설정 파일 한 줄 때문에 봇 전체가 기동하지 못하는 건 과하다 — 기본값으로 넘어가고
    경고만 남긴다.

    주의: `int(os.getenv(K, D) or D)` 관용구는 **'0'을 기본값으로 되돌린다**
    (`'0' or 5` → `'0'`은 truthy 문자열이라 괜찮지만, 값이 0인 int였다면 위험).
    여기서는 문자열 단계에서 빈 값만 걸러내므로 0이 살아남는다.
    """
    raw = os.getenv(key)
    if raw is None:
        return default
    raw = raw.strip()
    if not raw:
        return default
    try:
        return cast(raw)
    except (TypeError, ValueError):
        _logger.warning(
            "[설정] %s='%s' 를 숫자로 읽을 수 없어 기본값 %s 를 씁니다.", key, raw, default
        )
        return default


def _env_int(key: str, default: int) -> int:
    return _env_num(key, default, int)


def _env_float(key: str, default: float) -> float:
    return _env_num(key, default, float)


class Config:
    """애플리케이션 설정 클래스"""

    # 기본 경로 설정
    BASE_DIR = Path(__file__).parent.parent

    # 멀티 봇 시스템 설정
    ENABLE_MULTI_BOT: bool = os.getenv('ENABLE_MULTI_BOT', 'False').lower() == 'true'
    BOT_COUNT: int = _env_int('BOT_COUNT', 0)

    # 싱글/멀티 봇 모드 판별
    _is_single_bot = not ENABLE_MULTI_BOT

    # 현재 봇 식별자 (멀티 봇 모드에서 사용)
    BOT_ID: str = os.getenv('BOT_ID', '')
    BOT_NAME: str = os.getenv('BOT_NAME') or (os.getenv('BOT1_NAME', '') if _is_single_bot else '')

    # 명령어 필터 (이 봇이 처리할 명령어 목록) - 싱글 봇 모드면 BOT1_COMMAND_FILTER 자동 사용
    _command_filter_value = os.getenv('COMMAND_FILTER') or (os.getenv('BOT1_COMMAND_FILTER', '') if _is_single_bot else '')
    COMMAND_FILTER: list = [
        cmd.strip().lower() for cmd in _command_filter_value.split(',') if cmd.strip()
    ]

    # 봇별 무시 키워드 (다른 봇이 처리하도록) - 싱글 봇 모드면 BOT1_IGNORED_KEYWORDS 자동 사용
    _ignored_keywords_value = os.getenv('BOT_IGNORED_KEYWORDS') or (os.getenv('BOT1_IGNORED_KEYWORDS', '') if _is_single_bot else '')
    BOT_IGNORED_KEYWORDS: list = [
        kw.strip().lower() for kw in _ignored_keywords_value.split(',') if kw.strip()
    ]

    # 응답 메시지 프리픽스 (이것만 수정하면 모든 응답에 반영!) - 싱글 봇 모드면 BOT1_RESPONSE_PREFIX 자동 사용
    RESPONSE_PREFIX: str = os.getenv('RESPONSE_PREFIX') or (os.getenv('BOT1_RESPONSE_PREFIX', '') if _is_single_bot else '')

    # Mastodon API 설정 (싱글 봇 모드면 BOT1 설정 자동 사용)
    MASTODON_CLIENT_ID: str = os.getenv('MASTODON_CLIENT_ID') or (os.getenv('BOT1_CLIENT_ID', '') if _is_single_bot else '')
    MASTODON_CLIENT_SECRET: str = os.getenv('MASTODON_CLIENT_SECRET') or (os.getenv('BOT1_CLIENT_SECRET', '') if _is_single_bot else '')
    MASTODON_ACCESS_TOKEN: str = os.getenv('MASTODON_ACCESS_TOKEN') or (os.getenv('BOT1_ACCESS_TOKEN', '') if _is_single_bot else '')
    MASTODON_API_BASE_URL: str = os.getenv('MASTODON_API_BASE_URL', '')

    # Google Sheets 설정
    GOOGLE_CREDENTIALS_PATH: str = os.getenv(
        'GOOGLE_CREDENTIALS_PATH',
        str(BASE_DIR / 'credentials' / 'credentials.json')
    )
    SHEET_ID: str = os.getenv('SHEET_ID', '')

    # =====================================================================
    # 크레덴셜 풀 (utils/credential_pool.py)
    # Sheets 쿼터는 **프로젝트 단위**(읽기 300회/분)라, 서로 다른 프로젝트의 서비스 계정을
    # 기능별로 나눠 쓰면 쿼터가 그만큼 늘어난다. 쿼터에 걸리면 후보에서 빌려 재시도한다.
    # =====================================================================
    CREDENTIALS_DIR: str = os.getenv('CREDENTIALS_DIR', 'credentials')

    # 용도별 크레덴셜 우선순위(쉼표 구분, 파일명에서 `_credentials.json`을 뗀 이름).
    # 첫 번째가 주 크레덴셜, 나머지는 대여 후보. 비우면 발견된 전체를 쓴다.
    #   main          = 기본 시트(관리: 출석·소지금·스탯 / 명단 / 상점) — 가장 무겁고 쓰기가 많다
    #   system        = 시스템 시트(행동로그·투표·추적·소문)
    #   investigation = 조사 시트(진입·장소·예외·로그)
    CREDENTIAL_MAIN: str = os.getenv('CREDENTIAL_MAIN', 'genesis,oblivion,koltsevaya')
    CREDENTIAL_SYSTEM: str = os.getenv('CREDENTIAL_SYSTEM', 'koltsevaya,pgd,shinarmont')
    CREDENTIAL_INVESTIGATION: str = os.getenv(
        'CREDENTIAL_INVESTIGATION', 'shinarmont,pgd,genesis')

    # 할당된 크레덴셜이 전부 쿼터에 걸리면 다른 용도의 것까지 빌릴지
    CREDENTIAL_BORROW_ENABLED: bool = os.getenv(
        'CREDENTIAL_BORROW_ENABLED', 'True').lower() == 'true'

    # 쿼터 소진으로 판단된 크레덴셜을 쉬게 하는 시간(초). Sheets 쿼터는 분 단위로 회복된다.
    CREDENTIAL_COOLDOWN: int = _env_int('CREDENTIAL_COOLDOWN', 60)

    # Google Drive 설정
    GOOGLE_DRIVE_FOLDER_ID: str = os.getenv('GOOGLE_DRIVE_FOLDER_ID', '')

    # 봇 타입 설정 (default: 기본 게임 기능만, store: 전체 기능)
    # 싱글 봇 모드면 BOT1_BOT_TYPE 자동 사용
    BOT_TYPE: str = (os.getenv('BOT_TYPE') or (os.getenv('BOT1_BOT_TYPE', 'default') if _is_single_bot else 'default')).lower()

    # 화폐 설정 (.env에서 정의, 캐싱 불필요)
    CURRENCY: str = os.getenv('CURRENCY', '포인트')
    CURRENCY_EUNNEUN: str = os.getenv('CURRENCY_EUNNEUN', '은')

    # =====================================================================
    # 도박 (@BAR) — 게임별 한도·일일 제한
    #
    # ⚠️ **여기 정의하지 않으면 .env 값이 조용히 무시된다.**
    #    `config_int()`는 `getattr(config, KEY, default)`라 config에 없으면 .env를 보지도 않는다.
    #    2026-07-16: 도박 설정 8개가 전부 이 함정에 빠져 있었다(값이 기본값과 같아 안 드러남).
    # =====================================================================

    # 블랙잭 딜러가 소프트 17(A+6)에서 한 장 더 받을지.
    # True(H17) = 카지노 표준·하우스 유리 / False(S17) = 이용자에게 약간 유리(엣지 ~0.2%p 차이)
    BLACKJACK_HIT_ON_SOFT_17: bool = os.getenv(
        'BLACKJACK_HIT_ON_SOFT_17', 'True').lower() == 'true'
    # 블랙잭 일일 제한(판 단위 — 딜 1회 = 1판). 베팅 상한은 BET_MAX를 따른다.
    BLACKJACK_DAILY_LIMIT: int = _env_int('BLACKJACK_DAILY_LIMIT', 20)

    # 슬롯머신 — BET_MAX와 **별개인 전용 한도**.
    # 원 잭팟이 100배라 상한이 크면 한 번에 경제가 흔들린다(10달러 → 최대 1,000달러).
    SLOT_BET_MIN: int = _env_int('SLOT_BET_MIN', 3)
    SLOT_BET_MAX: int = _env_int('SLOT_BET_MAX', 10)
    SLOT_DAILY_LIMIT: int = _env_int('SLOT_DAILY_LIMIT', 20)

    # 크랩스 — 컴아웃 7·11은 베팅액의 2배, 포인트 재현은 1배.
    CRAPS_BET_MIN: int = _env_int('CRAPS_BET_MIN', 1)
    CRAPS_BET_MAX: int = _env_int('CRAPS_BET_MAX', 100)
    CRAPS_MAX_REROLLS: int = _env_int('CRAPS_MAX_REROLLS', 3)
    CRAPS_DAILY_LIMIT: int = _env_int('CRAPS_DAILY_LIMIT', 20)

    # 봇 동작 설정
    MAX_RETRIES: int = _env_int('BOT_MAX_RETRIES', 5)
    BASE_WAIT_TIME: int = _env_int('BOT_BASE_WAIT_TIME', 2)

    # 스트리밍이 끊겼을 때 폴백 폴링 주기(초). handlers/stream_handler 가 쓴다.
    # (2026-07-16: .env에 값은 있는데 여기 정의가 없어 getattr 기본값 30에 묶여 있었다)
    POLLING_INTERVAL: int = _env_int('POLLING_INTERVAL', 30)
    MAX_DICE_COUNT: int = _env_int('BOT_MAX_DICE_COUNT', 20)
    MAX_DICE_SIDES: int = _env_int('BOT_MAX_DICE_SIDES', 1000)
    MAX_CARD_COUNT: int = _env_int('BOT_MAX_CARD_COUNT', 52)
    
    # 시스템 관리자 설정
    SYSTEM_ADMIN_ID: str = os.getenv('SYSTEM_ADMIN_ID', 'admin')
    
    # 로그 설정 (싱글 봇 모드면 BOT1_LOG_FILE 자동 사용)
    LOG_LEVEL: str = os.getenv('LOG_LEVEL', 'INFO')
    LOG_FILE_PATH: str = os.getenv('LOG_FILE_PATH') or (os.getenv('BOT1_LOG_FILE', 'logs/bot.log') if _is_single_bot else 'logs/bot.log')
    LOG_MAX_BYTES: int = _env_int('LOG_MAX_BYTES', 10485760)  # 10MB
    LOG_BACKUP_COUNT: int = _env_int('LOG_BACKUP_COUNT', 5)
    
    # 캐시 설정 (초 단위: 1800 = 30분)
    # 명단, 도움말, 상점, 운세, 커스텀 워크시트만 캐싱 사용
    CACHE_TTL: int = _env_int('CACHE_TTL', 1800)

    # 워크시트 데이터 캐시 TTL(초). get_worksheet_data(use_cache=True) 전용.
    # GM이 손대는 정적 시트(상점·운세·단상·장소·단서 등)의 반복 읽기를 이 시간만큼 캐시해
    # Google Sheets API 호출을 줄인다. 가변 시트(관리·투표·행동로그 등)는 use_cache=False라 무영향.
    SHEET_CACHE_TTL: int = _env_int('SHEET_CACHE_TTL', 90)
    
    # 운세 설정
    FORTUNE_CACHE_ENABLED: bool = os.getenv('FORTUNE_CACHE_ENABLED', 'True').lower() == 'true'
    
    # 프리미엄 기능 설정 (+5000원)
    PREMIUM_CUSTOMC_ENABLED: bool = os.getenv('PREMIUM_CUSTOMC_ENABLED', 'False').lower() == 'true'
    PREMIUM_IMAGE_ENABLED: bool = os.getenv('PREMIUM_IMAGE_ENABLED', 'False').lower() == 'true'

    # 양도 기능 세분화 (아이템 양도 / 소지금 양도 별도 제어)
    PREMIUM_TRANSFER_ITEM_ENABLED: bool = os.getenv('PREMIUM_TRANSFER_ITEM_ENABLED', 'False').lower() == 'true'
    PREMIUM_TRANSFER_MONEY_ENABLED: bool = os.getenv('PREMIUM_TRANSFER_MONEY_ENABLED', 'False').lower() == 'true'

    # 하위 호환성: 기존 PREMIUM_TRANSFER_ENABLED가 있으면 두 기능 모두 활성화
    _legacy_transfer = os.getenv('PREMIUM_TRANSFER_ENABLED', '').lower() == 'true'
    if _legacy_transfer:
        PREMIUM_TRANSFER_ITEM_ENABLED = True
        PREMIUM_TRANSFER_MONEY_ENABLED = True

    # 상태 확인 기능 설정
    PEEK_STATUS: bool = os.getenv('PEEK_STATUS', 'False').lower() == 'true'
    PEEK_HEADER: str = os.getenv('PEEK_HEADER', '')

    # 출석 기능 설정
    # ATTENDANCE_COMMAND 값이 명령어 이름과 '관리' 워크시트 컬럼명으로 동시에 사용됩니다.
    ATTENDANCE_ENABLED: bool = os.getenv('ATTENDANCE_ENABLED', 'False').lower() == 'true'
    ATTENDANCE_COMMAND: str = (os.getenv('ATTENDANCE_COMMAND', '출석') or '출석').strip()
    try:
        ATTENDANCE_AMOUNT: int = _env_int('ATTENDANCE_AMOUNT', 0)
    except (ValueError, TypeError):
        ATTENDANCE_AMOUNT: int = 0

    # 명령어 허용 공개 범위 (threshold)
    # 가능한 값: unlisted / private / direct
    # - unlisted: unlisted/private/direct 허용
    # - private:  private/direct 허용 (기본값, 기존 동작)
    # - direct:   direct만 허용
    # public은 서버 정책상 어떤 경우에도 차단됩니다.
    ALLOWED_VISIBILITY: str = os.getenv('ALLOWED_VISIBILITY', 'private').lower()

    _VISIBILITY_THRESHOLD_MAP = {
        'unlisted': ['unlisted', 'private', 'direct'],
        'private':  ['private', 'direct'],
        'direct':   ['direct'],
    }
    ALLOWED_VISIBILITY_LEVELS: list = _VISIBILITY_THRESHOLD_MAP.get(
        ALLOWED_VISIBILITY, ['private', 'direct']
    )

    # 공개 범위 위반 시 사용자에게 보내는 문구 템플릿.
    # {ranges}에 허용 범위 라벨이 들어간다(예: "**팔로워 전용** 혹은 **다이렉트 메시지**").
    # 명령어별 허용 범위표는 utils/command_visibility.py 참조.
    VISIBILITY_ERROR_TEMPLATE: str = os.getenv(
        'VISIBILITY_ERROR_TEMPLATE',
        '이 명령어는 {ranges} 범위로만 사용할 수 있습니다. '
        '위 툿을 삭제한 후, 범위를 올바르게 설정하여 다시 업로드하세요.',
    )

    # =====================================================================
    # 조사 기능 설정 (investigate 봇 타입 전용, 2차 스프레드시트 사용)
    # 명세: docs/조사봇_개발_가이드.md
    # =====================================================================
    INVESTIGATION_ENABLED: bool = os.getenv('INVESTIGATION_ENABLED', 'False').lower() == 'true'
    INVESTIGATION_SHEET_ID: str = os.getenv('INVESTIGATION_SHEET_ID', '')

    # 시트명 상수 (가이드 §8)
    INVESTIGATION_ENTRY_SHEET: str = os.getenv('INVESTIGATION_ENTRY_SHEET', '진입')
    INVESTIGATION_EXCEPTION_SHEET: str = os.getenv('INVESTIGATION_EXCEPTION_SHEET', '예외')
    INVESTIGATION_LOG_SHEET: str = os.getenv('INVESTIGATION_LOG_SHEET', '로그')

    # 장소 시트 목록에서 제외할 워크시트(템플릿 등).
    # 실제 화이트리스트는 `진입` 시트의 장소명이며, 이 목록은 2차 방어선이다.
    INVESTIGATION_EXCLUDED_SHEETS: list = [
        s.strip() for s in os.getenv(
            'INVESTIGATION_EXCLUDED_SHEETS',
            '이 워크시트 복사 후 시트 이름을 장소명으로 변경',
        ).split(',') if s.strip()
    ]

    # 조사 시트 읽기 캐시 TTL(초). 기본 3시간(10800).
    # '현재 조사 가능'·스탯 등 정합성이 중요한 값은 명령 처리 시점에 미캐시로 재검증하므로
    # ([진입]·[조사]는 항상 최신) 길게 잡아도 판정은 정확하다.
    # 영향받는 것은 [장소 목록]의 최신성뿐이며, 즉시 반영이 필요하면
    # @TOWN 봇에 [캐시 리셋/전체]를 보내 매니저 캐시를 비운다.
    INVESTIGATION_CACHE_TTL: int = _env_int('INVESTIGATION_CACHE_TTL', 10800)

    # 일일 조사 횟수 (성공한 [조사]만 카운트). 기존 시너몬트 기획의 `오늘조사` 한도와 동일하게 2.
    INVESTIGATION_DAILY_LIMIT: int = _env_int('INVESTIGATION_DAILY_LIMIT', 2)

    # 특정 캐릭터의 조사 자격 박탈(운영 결정). [장소 목록]·[진입]·[조사]를 모두 막는다.
    # 형식: 콤마로 구분된 `아이디[:일차]` 토큰.
    #   evaristo:4        → evaristo를 4일차에만 박탈
    #   evaristo:4-6      → 4~6일차 박탈
    #   evaristo          → 모든 날 박탈(일차 무제한)
    #   evaristo:4,mika:2 → 여러 명
    INVESTIGATION_DISQUALIFIED: str = os.getenv('INVESTIGATION_DISQUALIFIED', '')

    # [조사] 시 현재 위치엔 없지만 다른 장소에 존재하는 포인트를 조사하려 하면,
    # "먼저 그 장소로 진입하라"는 안내를 준다(어느 장소인지는 알려주지 않는다).
    # False면 기존처럼 일반 실패 문구(NO_SUCH_POINT)로 응답한다.
    INVESTIGATION_WRONG_LOCATION_HINT: bool = os.getenv(
        'INVESTIGATION_WRONG_LOCATION_HINT', 'True').lower() == 'true'
    # 위 안내 문구 오버라이드 템플릿({location}, {point} 치환). 비우면 코드 기본 문구 사용.
    INVESTIGATION_MSG_WRONG_LOCATION: str = os.getenv('INVESTIGATION_MSG_WRONG_LOCATION', '')

    # 조사 횟수를 `관리` 시트 조사 카운터 컬럼에도 미러링(GM 트래킹용).
    # 판정의 기준(single source of truth)은 항상 `로그` 시트다.
    # 컬럼명은 **실제 시트 기준**(2026-07-16 실측: 관리 헤더가 '추적'/'조사'다).
    INVESTIGATION_MIRROR_COUNTER: bool = os.getenv(
        'INVESTIGATION_MIRROR_COUNTER', 'True').lower() == 'true'
    INVESTIGATION_COUNTER_COLUMN: str = os.getenv('INVESTIGATION_COUNTER_COLUMN', '조사')

    # 추적 일일 카운터 컬럼(`관리` 시트).
    # ⚠️ 이 이름이 시트와 다르면 **[추적]이 항상 실패한다** — 컬럼을 못 찾으면
    # check_and_inc_sheet가 False를 돌려주고 추적은 그걸 '한도 초과'로 해석한다.
    TRACK_COUNTER_COLUMN: str = os.getenv('TRACK_COUNTER_COLUMN', '추적')

    # 조사 결과 문구에 이성 왜곡(마스킹/오정보) 적용 여부 (utils/distortion.py)
    INVESTIGATION_DISTORTION_ENABLED: bool = os.getenv(
        'INVESTIGATION_DISTORTION_ENABLED', 'True').lower() == 'true'

    # 스탯 클램프 범위 (가이드 §9-4 → 0~100으로 자르기만, 별도 알림 없음)
    INVESTIGATION_STAT_MIN: int = _env_int('INVESTIGATION_STAT_MIN', 0)
    INVESTIGATION_STAT_MAX: int = _env_int('INVESTIGATION_STAT_MAX', 100)

    # --- 스케줄러: 조사 자동 개방 (가이드 §6) ---
    # 개방 시각 (KST)
    INVESTIGATION_OPEN_HOUR: int = _env_int('INVESTIGATION_OPEN_HOUR', 21)
    INVESTIGATION_OPEN_MINUTE: int = _env_int('INVESTIGATION_OPEN_MINUTE', 0)

    # 일차 ↔ 개방 날짜 매핑 (가이드 §6.2). 형식: "라벨=YYYY-MM-DD,라벨=YYYY-MM-DD"
    # 시각은 INVESTIGATION_OPEN_HOUR/MINUTE를 따른다.
    _open_schedule_raw: str = os.getenv('INVESTIGATION_OPEN_SCHEDULE', '') or (
        '1주-4일차=2026-07-20,'
        '2주-8일차=2026-07-24,'
        '2주-11일차=2026-07-27,'
        '3주-16일차=2026-08-01,'
        '3주-18일차=2026-08-03,'
        '3주-20일차=2026-08-05,'
        '4주-23일차=2026-08-08,'
        '4주-26일차=2026-08-11'
    )
    INVESTIGATION_OPEN_SCHEDULE: list = [
        (pair.split('=', 1)[0].strip(), pair.split('=', 1)[1].strip())
        for pair in _open_schedule_raw.split(',')
        if pair.strip() and '=' in pair
    ]

    # 버전 전환 시 이전 버전 행에 쓸 '불가능' 값 (가이드 §6.1).
    # GM 드롭다운 선택지와 맞춰야 시트에 경고 표시가 생기지 않는다.
    # 판정은 "'가능'이 아니면 전부 불가능"이라 어떤 값이든 동작에는 지장이 없다.
    INVESTIGATION_UNAVAILABLE_VALUE: str = os.getenv(
        'INVESTIGATION_UNAVAILABLE_VALUE', '불가능') or '불가능'

    # 놓친 개방(봇 다운 등) 보정 실행 정책 (가이드 §6.3-4)
    # True면 기동 시 관리자에게 알림만 보내고 실행하지 않는다(운영진 수동 조작 존중).
    # False면 기동 시 자동으로 1회 보정 실행한다.
    INVESTIGATION_MISSED_OPEN_CONFIRM: bool = os.getenv(
        'INVESTIGATION_MISSED_OPEN_CONFIRM', 'True').lower() == 'true'

    # 개방 실행 기록 파일 (멱등성·놓친 실행 감지용)
    INVESTIGATION_OPEN_STATE_FILE: str = os.getenv(
        'INVESTIGATION_OPEN_STATE_FILE', 'state/investigation_opens.json')

    # 오픈일 조사 횟수 보정 (가이드 §8). off / reset / plus_one
    # 개방 시각(21:00)이 지난 개방일에만 적용된다. 스진 직후 신규 조사 러시 허용 여부.
    # - off      : 보정 없음(기본)
    # - reset    : 개방 시각 이후의 조사만 세어 그날 횟수를 사실상 리셋
    # - plus_one : 그날 한도를 1 늘림
    INVESTIGATION_OPEN_ADJUST: str = (
        os.getenv('INVESTIGATION_OPEN_ADJUST', 'off') or 'off').strip().lower()

    # 참고: 대괄호 없는 일반 멘션(가이드 §10-12)은 **무응답**이다.
    # 근거(2026-07-16 코드 확인): command_router.parse_command_from_text가 `[...]`를
    # 하나도 못 찾으면 []를 반환 → stream_handler._handle_mention이 keywords 없음으로
    # 조기 return → 답글 자체가 나가지 않는다.
    # (BOT_TYPE=='investigate'인 silent-ignore 블록과는 무관하다. 시너몬트는 5슬롯 모두
    #  BOT_TYPE=shinarmont라 그 블록은 실행되지 않는다.)
    # 가이드가 말하는 '안내 1회' 옵션은 구현하지 않았다. 조사 봇은 DM 전용이라
    # 잡담 멘션에 매번 안내를 붙이면 소음이 된다. 필요해지면 그때 설정을 추가한다.

    # --- 실패 응답 문구 세트 (가이드 §3.4) ---
    # 세계관 톤 유지. 시스템 용어를 노출하지 않는다. 운영진이 .env로 수정 가능.
    INVESTIGATION_MESSAGES = {
        # 진입 시트에 아예 없는 장소명
        'NO_SUCH_PLACE': os.getenv(
            'INVESTIGATION_MSG_NO_SUCH_PLACE', '그런 곳은 이 마을에 없다.'),
        # 진입 불가 (장소가 닫혀 있거나 조사 가능한 포인트가 없음) — 둘을 구분해 주지 않는다
        'CANNOT_ENTER': os.getenv(
            'INVESTIGATION_MSG_CANNOT_ENTER', '이곳에는 현재 조사할 수 있는 것이 없다.'),
        # 조사 불가 포인트 (없거나·닫혔거나·권한 없음 — 전부 동일 문구)
        'NO_SUCH_POINT': os.getenv(
            'INVESTIGATION_MSG_NO_SUCH_POINT', '그곳에서는 더 조사할 것이 없다.'),
        # 일일 조사 횟수 초과
        'DAILY_LIMIT': os.getenv(
            'INVESTIGATION_MSG_DAILY_LIMIT',
            '오늘은 너무 많이 돌아다녔다. 내일 다시 살펴보는 게 좋겠다.'),
        # 재화 잔액 부족
        'NO_MONEY': os.getenv(
            'INVESTIGATION_MSG_NO_MONEY', '주머니 사정이 여의치 않다.'),
        # 미진입 상태에서 조사 시도
        'NOT_ENTERED': os.getenv(
            'INVESTIGATION_MSG_NOT_ENTERED', '어디서 조사할지부터 정해야 한다. [진입/장소명]'),
        # 진입 가능한 장소가 하나도 없음
        'NO_LOCATIONS': os.getenv(
            'INVESTIGATION_MSG_NO_LOCATIONS', '지금은 어디도 둘러볼 만한 곳이 없다.'),
        # 관리 시트에 캐릭터가 없음 (운영 오류)
        'NO_CHARACTER': os.getenv(
            'INVESTIGATION_MSG_NO_CHARACTER', '아직 이 마을 사람으로 등록되지 않았다.'),
        # 시트 오류 등 일시적 실패
        'TEMPORARY': os.getenv(
            'INVESTIGATION_MSG_TEMPORARY', '지금은 생각이 잘 정리되지 않는다. 잠시 후 다시 시도해 보자.'),
        # 조사 자격 박탈(운영 결정) — INVESTIGATION_DISQUALIFIED로 지정
        'DISQUALIFIED': os.getenv(
            'INVESTIGATION_MSG_DISQUALIFIED', '금일 조사 자격이 박탈되었습니다.'),
    }

    @classmethod
    def investigation_message(cls, key: str) -> str:
        """조사 실패 응답 문구 조회 (없는 키는 TEMPORARY로 폴백)."""
        return cls.INVESTIGATION_MESSAGES.get(key, cls.INVESTIGATION_MESSAGES['TEMPORARY'])

    # =====================================================================
    # 시너몬트(shinarmont) 봇 설정
    # BOTn_ 접두 전송은 bot_config가 bare 키로 정규화하므로 여기서는 bare 키만 읽는다.
    # 명령어/유틸은 getattr(config, KEY, 기본값)로 참조하며 이 파일을 직접 수정하지 않는다.
    # =====================================================================

    # 시스템 스프레드시트 (행동로그/투표/고발/부탁지령/추적/소문/이성/설정 등, 3번째 매니저)
    SYSTEM_SHEET_ENABLED: bool = os.getenv('SYSTEM_SHEET_ENABLED', 'False').lower() == 'true'
    SYSTEM_SHEET_ID: str = os.getenv('SYSTEM_SHEET_ID', '')

    # 스케줄러 소유자 여부 (멀티 슬롯 중 1개 슬롯만 True로 두어 중복 실행 방지)
    SCHEDULER_OWNER: bool = os.getenv('SCHEDULER_OWNER', 'False').lower() == 'true'

    # 게임 기간 (KST 기준, 형식: YYYY.MM.DD) — 일차 계산 기준
    GAME_START_DATE: str = os.getenv('GAME_START_DATE', '2026.07.17')
    GAME_END_DATE: str = os.getenv('GAME_END_DATE', '2026.08.14')

    # 입원 판정 임계 (건강 <= 이 값이면 입원 파생)
    HEALTH_HOSPITALIZE_THRESHOLD: int = _env_int('HEALTH_HOSPITALIZE_THRESHOLD', 20)

    # 이성(정신) 관련 임계
    SANITY_DISTORTION_THRESHOLD: int = _env_int('SANITY_DISTORTION_THRESHOLD', 40)
    SANITY_HALLUCINATION_THRESHOLD: int = _env_int('SANITY_HALLUCINATION_THRESHOLD', 20)
    SANITY_MSG1_THRESHOLD: int = _env_int('SANITY_MSG1_THRESHOLD', 60)
    SANITY_MSG2_THRESHOLD: int = _env_int('SANITY_MSG2_THRESHOLD', 30)

    # 교류 농도 1레벨당 이성 변동량
    EXCHANGE_SANITY_PER_LEVEL: int = _env_int('EXCHANGE_SANITY_PER_LEVEL', 5)

    # 고발 보상 (소지금)
    ACCUSE_REWARD: int = _env_int('ACCUSE_REWARD', 50)

    # 의무실 회복식 (dice_parser evaluate_amount로 계산) — 규칙 폴백/과잉심문 회복량의 원천
    INFIRMARY_HEAL_HEALTH: str = os.getenv('INFIRMARY_HEAL_HEALTH', '2d6+2') or '2d6+2'
    INFIRMARY_HEAL_SANITY: str = os.getenv('INFIRMARY_HEAL_SANITY', '2d6+2') or '2d6+2'

    # 의무실 처치 결과(대화 종료 시 관리 시트 반영)의 능력치 변동 클램프
    # AI가 대화 맥락으로 결정한 건강/이성 변동을 이 범위로 제한한다(폴백 규칙도 동일).
    DOCTOR_TREAT_DELTA_MAX: int = _env_int('DOCTOR_TREAT_DELTA_MAX', 12)   # 한 방문 최대 회복(+)
    DOCTOR_TREAT_DELTA_MIN: int = _env_int('DOCTOR_TREAT_DELTA_MIN', -6)   # 과잉심문 등 최대 하락(-)

    # 의무실 의사(AI) 대화 턴 — 의사가 흐름을 보며 스스로 진료를 마무리한다.
    # 의사는 최소 DOCTOR_TURNS_MIN번, 최대 DOCTOR_MAX_TURNS번 말하며, 보통 IDEAL 범위에서 마무리.
    DOCTOR_MAX_TURNS: int = _env_int('DOCTOR_MAX_TURNS', 8)          # 하드 상한(이 턴에 무조건 종료)
    DOCTOR_TURNS_MIN: int = _env_int('DOCTOR_TURNS_MIN', 2)          # 최소 발화(그 전엔 종료 금지)
    DOCTOR_TURNS_IDEAL_MIN: int = _env_int('DOCTOR_TURNS_IDEAL_MIN', 3)  # 이상적 종료 하한
    DOCTOR_TURNS_IDEAL_MAX: int = _env_int('DOCTOR_TURNS_IDEAL_MAX', 6)  # 이상적 종료 상한

    # AI(anthropic) 설정
    AI_ENABLED: bool = os.getenv('AI_ENABLED', 'False').lower() == 'true'
    ANTHROPIC_API_KEY: str = os.getenv('ANTHROPIC_API_KEY', '')
    AI_MODEL: str = os.getenv('AI_MODEL', 'claude-sonnet-5') or 'claude-sonnet-5'
    AI_MASK_MODEL: str = os.getenv('AI_MASK_MODEL', 'claude-haiku-4-5') or 'claude-haiku-4-5'
    AI_DOCTOR_MODEL: str = os.getenv('AI_DOCTOR_MODEL', 'claude-sonnet-5') or 'claude-sonnet-5'

    # 도박 베팅 상한
    BET_MAX: int = _env_int('BET_MAX', 100000)

    # 소문 발송 설정
    RUMOR_WEEKDAY: str = os.getenv('RUMOR_WEEKDAY', '토') or '토'
    RUMOR_SAMPLE_MIN: int = _env_int('RUMOR_SAMPLE_MIN', 3)
    RUMOR_SAMPLE_MAX: int = _env_int('RUMOR_SAMPLE_MAX', 5)
    RUMOR_RECIPIENTS_MIN: int = _env_int('RUMOR_RECIPIENTS_MIN', 3)
    RUMOR_RECIPIENTS_MAX: int = _env_int('RUMOR_RECIPIENTS_MAX', 5)

    # 일일보고(GM 전용) — @SYSTEM → @NOTICE DM 타래
    # ⚠️ 여기 정의를 빼먹으면 .env 값이 조용히 무시된다(getattr 기본값만 쓰인다).
    #    tests/test_settings_env.py::ConfigWiringTest 가 이 목록을 지킨다.
    DIGEST_ENABLED: bool = os.getenv('DIGEST_ENABLED', 'True').lower() == 'true'
    DIGEST_AI_SEEDS: bool = os.getenv('DIGEST_AI_SEEDS', 'True').lower() == 'true'
    DIGEST_RECIPIENT_ID: str = os.getenv('DIGEST_RECIPIENT_ID', '') or ''
    DIGEST_HOUR: int = _env_int('DIGEST_HOUR', 23)
    DIGEST_MINUTE: int = _env_int('DIGEST_MINUTE', 30)
    DIGEST_SEED_COUNT: int = _env_int('DIGEST_SEED_COUNT', 8)
    # 마스토돈 한 통 한도(shinarmont.site 공백 미포함 5천자) 대비 여유분
    DIGEST_CHUNK_LIMIT: int = _env_int('DIGEST_CHUNK_LIMIT', 4800)
    # 의무실 진료 대화록(러너가 실제로 친 말)을 보고서에 실을지, 발화당 몇 자까지 실을지
    DIGEST_INCLUDE_TRANSCRIPT: bool = os.getenv('DIGEST_INCLUDE_TRANSCRIPT', 'True').lower() == 'true'
    DIGEST_TRANSCRIPT_CHARS: int = _env_int('DIGEST_TRANSCRIPT_CHARS', 300)
    # 진료 대화록을 전문 대신 AI 요약으로 갈음할지(길이 관리). AI 미사용 시 자동으로 전문 폴백.
    DIGEST_DOCTOR_SUMMARY: bool = os.getenv('DIGEST_DOCTOR_SUMMARY', 'True').lower() == 'true'
    # 지령 표시 길이. 지령 본문은 GM이 직접 쓴 글이라 보고서에 되풀이할 값어치가 없다 —
    # 제목 + 어느 지령인지 짚을 만큼의 발췌면 충분하다. 0 이면 제한 없음.
    DIGEST_DIRECTIVE_TITLE_CHARS: int = _env_int('DIGEST_DIRECTIVE_TITLE_CHARS', 60)
    DIGEST_DIRECTIVE_CHARS: int = _env_int('DIGEST_DIRECTIVE_CHARS', 160)
    DIGEST_DIRECTIVE_REPORT_CHARS: int = _env_int('DIGEST_DIRECTIVE_REPORT_CHARS', 300)
    # §1 인물별 행적은 훑어보는 절 — 인용을 더 짧게 자른다
    DIGEST_ACTOR_EXCERPT_CHARS: int = _env_int('DIGEST_ACTOR_EXCERPT_CHARS', 80)

    # ── 소문 브리핑(재구성) — 개발안내서 §9 설정값 ──
    # 새 파이프라인: 사건을 이벤트로 정규화 → 필터·점수 → 씨앗 4줄 브리핑.
    # DIGEST_BRIEF=True 면 브리핑을 발송하고, LEGACY_REPORT 면 기존 상세 보고를 병행한다.
    DIGEST_BRIEF: bool = os.getenv('DIGEST_BRIEF', 'True').lower() == 'true'
    DIGEST_LEGACY_REPORT: bool = os.getenv('DIGEST_LEGACY_REPORT', 'False').lower() == 'true'
    DIGEST_MAX_SEEDS: int = _env_int('DIGEST_MAX_SEEDS', 8)          # DM에 싣는 씨앗 최대 수
    DIGEST_MIN_SEED_SCORE: int = _env_int('DIGEST_MIN_SEED_SCORE', 4)  # 이 점수 미만은 탈락
    DIGEST_GAMBLE_THRESHOLD: int = _env_int('DIGEST_GAMBLE_THRESHOLD', 5)  # 도박 이상 감지 횟수
    DIGEST_MONEY_RATIO: float = _env_float('DIGEST_MONEY_RATIO', 2.5)  # 소지금 이상 감지 평균 배수
    DIGEST_MONEY_FLOOR: int = _env_int('DIGEST_MONEY_FLOOR', 15)     # 소지금 하한 이상 감지
    DIGEST_SANITY_DROP: int = _env_int('DIGEST_SANITY_DROP', 8)      # 이성 하락 이상 감지(전일 대비)
    DIGEST_HEALTH_DROP: int = _env_int('DIGEST_HEALTH_DROP', 10)     # 건강 하락 이상 감지(전일 대비)
    DIGEST_WITNESS_WINDOW_MIN: int = _env_int('DIGEST_WITNESS_WINDOW_MIN', 60)  # 목격자 판정 창(분)
    DIGEST_BRIEF_LIMIT: int = _env_int('DIGEST_BRIEF_LIMIT', 2000)   # 브리핑 한 통 목표 길이
    DIGEST_BRIEF_EVIDENCE_CHARS: int = _env_int('DIGEST_BRIEF_EVIDENCE_CHARS', 220)  # 근거 줄 상한
    # 진술 모순·은밀 지령 감지 키워드(쉼표 구분). 비우면 기본값.
    DIGEST_CONTRADICTION_KEYWORDS: list = [
        k.strip() for k in os.getenv(
            'DIGEST_CONTRADICTION_KEYWORDS',
            '번복,불일치,함구,얼버무,방어적,어긋남,캐물었으나,말이 바뀌').split(',') if k.strip()
    ]
    DIGEST_SECRET_KEYWORDS: list = [
        k.strip() for k in os.getenv(
            'DIGEST_SECRET_KEYWORDS',
            '서명은 없었다,적지 말아,눈에 띄지 않는,사본을 보관,갖고 있어 주게').split(',') if k.strip()
    ]

    # 메시지 설정
    MAX_MESSAGE_LENGTH: int = _env_int('MAX_MESSAGE_LENGTH', 500)

    # 봇 가동 기간 설정 (KST 기준, 형식: YYYY.MM.DD)
    BOT_OPERATION_START: str = os.getenv('BOT_OPERATION_START', '')
    BOT_OPERATION_END: str = os.getenv('BOT_OPERATION_END', '')

    # 개발/디버그 설정
    DEBUG_MODE: bool = os.getenv('DEBUG_MODE', 'False').lower() == 'true'
    ENABLE_CONSOLE_LOG: bool = os.getenv('ENABLE_CONSOLE_LOG', 'True').lower() == 'true'

    # 무시할 명령어 키워드 (다른 봇과 충돌 방지)
    IGNORED_COMMAND_KEYWORDS: list = [
        kw.strip() for kw in os.getenv('IGNORED_COMMAND_KEYWORDS', '').split(',') if kw.strip()
    ]

    # 워크시트 이름 상수 (환경변수에서 로드) - 싱글 봇 모드면 BOT1_HELP_SHEET 등 자동 사용
    WORKSHEET_NAMES = {
        'HELP': os.getenv('HELP_SHEET') or (os.getenv('BOT1_HELP_SHEET', '도움말') if _is_single_bot else '도움말'),
        'ROSTER': os.getenv('LIST_SHEET', '명단'),
        'CUSTOM': os.getenv('CUSTOM_SHEET', '커스텀'),
        'FORTUNE': os.getenv('FORTUNE_SHEET', '운세'),
        'SHOP': os.getenv('SHOP_SHEET', '상점'),
    }

    # ⚠️ 캐시 사용 규칙 ⚠️
    #
    # ✅ 캐싱 가능한 워크시트 (5개만!)
    # - 명단 (ROSTER): 사용자 데이터, 소지금, 인벤토리
    # - 도움말 (HELP): 도움말 항목
    # - 상점 (SHOP): 아이템 목록
    # - 운세 (FORTUNE): 운세 문구
    # - 커스텀 (CUSTOM): 커스텀 명령어
    #
    # ❌ 캐싱 금지 워크시트
    # - 위 5개를 제외한 모든 워크시트는 캐싱 사용 금지!
    # - 이유: 실시간 반영이 필요한 데이터는 캐시하면 안됨
    #
    # 📌 TTL 설정
    # - 모든 캐시 가능 워크시트는 CACHE_TTL 환경변수 값을 사용
    # - 현재 기본값: 1800초 (30분)
    #
    CACHEABLE_WORKSHEETS = ['명단', '도움말', '상점', '운세', '커스텀']
    
    # 시스템 키워드 (커스텀 명령어와 구분하기 위함)
    # 다이스/카드/운세 명령어는 시너몬트에서 제거됨 → 스테일 항목 정리
    SYSTEM_KEYWORDS = [
        '도움말',]
    
    # 에러 메시지 상수
    ERROR_MESSAGES = {
        'USER_NOT_FOUND': '등록되지 않은 사용자입니다. 먼저 캐릭터를 등록해주세요.',
        'USER_ID_CHECK_FAILED': '명령어 시전자의 아이디를 확인할 수 없습니다. 잠시만 기다려 주세요.',
        'USER_NAME_INVALID': '사용자 이름 정보가 올바르지 않습니다.',
        'TEMPORARY_ERROR': '일시적인 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.',
        'UNKNOWN_COMMAND': '알 수 없는 명령어입니다. [도움말]을 입력해 사용 가능한 명령어를 확인하세요.',
        'DICE_FORMAT_ERROR': '주사위 형식이 올바르지 않습니다. 예: [2d6], [1d6<4] (4 이하 성공), [3d10>7] (7 이상 성공)',
        'DICE_COUNT_LIMIT': f'주사위 개수는 최대 20개까지 가능합니다.',
        'DICE_SIDES_LIMIT': f'주사위 면수는 최대 1000면까지 가능합니다.',
        'SHEET_NOT_FOUND': '필요한 시트를 찾을 수 없습니다.',
        'DATA_NOT_FOUND': '데이터를 찾을 수 없습니다.',
    }
    
    # 성공 메시지 상수
    SUCCESS_MESSAGES = {
        'SHEET_CONNECTED': '스프레드시트 연결 성공',
        'AUTH_SUCCESS': 'auth success',
        'STREAMING_START': 'Mastodon 스트리밍 시작',
        'ERROR_NOTIFICATION_SENT': '오류 알림 전송 완료'
    }
    
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
    def is_system_keyword(cls, keyword: str) -> bool:
        """
        시스템 키워드인지 확인합니다.
        
        Args:
            keyword: 확인할 키워드
            
        Returns:
            bool: 시스템 키워드면 True
        """
        return keyword in cls.SYSTEM_KEYWORDS
    
    @classmethod
    def get_worksheet_name(cls, key: str) -> Optional[str]:
        """
        워크시트 키에 해당하는 실제 시트 이름을 반환합니다.
        
        Args:
            key: 워크시트 키 (예: 'ROSTER', 'LOG')
            
        Returns:
            Optional[str]: 시트 이름 또는 None
        """
        return cls.WORKSHEET_NAMES.get(key.upper())
    
    @classmethod
    def get_error_message(cls, key: str) -> str:
        """
        에러 메시지 키에 해당하는 메시지를 반환합니다.
        
        Args:
            key: 에러 메시지 키
            
        Returns:
            str: 에러 메시지
        """
        return cls.ERROR_MESSAGES.get(key, cls.ERROR_MESSAGES['TEMPORARY_ERROR'])
    
    @classmethod
    def get_success_message(cls, key: str) -> str:
        """
        성공 메시지 키에 해당하는 메시지를 반환합니다.
        
        Args:
            key: 성공 메시지 키
            
        Returns:
            str: 성공 메시지
        """
        return cls.SUCCESS_MESSAGES.get(key, '')
    
    @classmethod
    def format_response(cls, message: str) -> str:
        """
        모든 응답 메시지에 프리픽스 추가

        Args:
            message: 원본 메시지

        Returns:
            str: 프리픽스가 추가된 메시지
        """
        if not message or not isinstance(message, str):
            return message

        # 공백 제거
        message = message.strip()
        if not message:
            return message

        # 이미 프리픽스가 있으면 중복 방지
        if message.startswith(cls.RESPONSE_PREFIX.strip()):
            return message

        return f"{cls.RESPONSE_PREFIX}{message}"

    @classmethod
    def check_bot_type(cls, required_type: str) -> bool:
        """
        현재 봇 타입이 요구되는 타입과 일치하는지 확인

        Args:
            required_type: 요구되는 봇 타입 ('default', 'store', 'stats')

        Returns:
            bool: 일치 여부
        """
        return cls.BOT_TYPE == required_type.lower()

    @classmethod
    def is_bot_type_enabled(cls, *allowed_types: str) -> bool:
        """
        현재 봇 타입이 허용된 타입 중 하나인지 확인

        Args:
            *allowed_types: 허용된 봇 타입들 (예: 'default', 'store', 'stats')

        Returns:
            bool: 허용 여부
        """
        return cls.BOT_TYPE in [t.lower() for t in allowed_types]

    @classmethod
    def is_multi_bot_mode(cls) -> bool:
        """
        멀티 봇 모드인지 확인

        Returns:
            bool: 멀티 봇 모드면 True
        """
        return cls.ENABLE_MULTI_BOT

    @classmethod
    def is_command_allowed(cls, command_name: str) -> bool:
        """
        이 봇에서 명령어 실행 가능한지 확인 (멀티 봇 모드에서만 필터링)

        Args:
            command_name: 명령어 이름

        Returns:
            bool: 실행 가능하면 True
        """
        # 멀티 봇 모드가 아니거나 필터가 없으면 모두 허용
        if not cls.ENABLE_MULTI_BOT or not cls.COMMAND_FILTER:
            return True

        # 명령어 필터에 포함되어 있는지 확인
        return command_name.lower() in cls.COMMAND_FILTER

    @classmethod
    def should_ignore_keyword(cls, keyword: str) -> bool:
        """
        키워드를 무시해야 하는지 확인 (다른 봇이 처리하도록)

        Args:
            keyword: 확인할 키워드

        Returns:
            bool: 무시해야 하면 True
        """
        # 기존 IGNORED_COMMAND_KEYWORDS 확인
        if keyword.lower() in [k.lower() for k in cls.IGNORED_COMMAND_KEYWORDS]:
            return True

        # 멀티 봇 모드: BOT_IGNORED_KEYWORDS 확인
        if cls.ENABLE_MULTI_BOT and keyword.lower() in cls.BOT_IGNORED_KEYWORDS:
            return True

        return False

    @classmethod
    def get_bot_info(cls) -> dict:
        """
        현재 봇 정보 반환

        Returns:
            dict: 봇 정보 딕셔너리
        """
        return {
            'multi_bot_mode': cls.ENABLE_MULTI_BOT,
            'bot_id': cls.BOT_ID,
            'bot_name': cls.BOT_NAME,
            'bot_type': cls.BOT_TYPE,
            'command_filter': cls.COMMAND_FILTER,
            'ignored_keywords': cls.BOT_IGNORED_KEYWORDS,
            'response_prefix': cls.RESPONSE_PREFIX
        }


# 설정 인스턴스 (싱글톤 패턴)
config = Config()
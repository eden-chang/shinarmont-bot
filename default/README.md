# 마스토돈 자동봇 (Mastodon Auto Bot)

Google Sheets와 연동되는 확장 가능한 마스토돈 봇 시스템입니다. 다이스 굴리기, 운세, 상점 시스템, 커스텀 명령어 등 다양한 기능을 제공합니다.

## 목차

- [주요 기능](#주요-기능)
- [시스템 요구사항](#시스템-요구사항)
- [설정 방법](#설정-방법)
- [사용 방법](#사용-방법)
- [프로젝트 구조](#프로젝트-구조)
- [AI 코딩 작업 시작 가이드](#ai-코딩-작업-시작-가이드)
- [명령어 시스템](#명령어-시스템)
- [개발 가이드](#개발-가이드)
- [트러블슈팅](#트러블슈팅)

## 레퍼런스 문서 목록

| 문서 | 설명 |
|------|------|
| [`docs/references/COMMAND_TEMPLATE.md`](docs/references/COMMAND_TEMPLATE.md) | 복붙 가능한 명령어 코드 템플릿 (기본/상점/스탯) + 개발 팁 |
| [`docs/references/COMMAND_API.md`](docs/references/COMMAND_API.md) | CommandContext · CommandResponse · BaseCommand · BaseStoreCommand API |
| [`docs/references/COMMAND_RULES.md`](docs/references/COMMAND_RULES.md) | 명령어 클래스 아키텍처 규칙 (생성자, 데코레이터, 임포트) |
| [`docs/references/SHEETS_STRUCTURE.md`](docs/references/SHEETS_STRUCTURE.md) | 모든 워크시트 컬럼 정의, 데이터 형식, 인벤토리 형식 |
| [`docs/references/SHEETS_API.md`](docs/references/SHEETS_API.md) | SheetsManager 메서드 레퍼런스, 행/컬럼 번호 계산법 |
| [`docs/references/CONCURRENCY_GUIDE.md`](docs/references/CONCURRENCY_GUIDE.md) | 락 패턴, batch_update, 캐시 무효화, 데드락 방지 |
| [`docs/references/ENV_VARS.md`](docs/references/ENV_VARS.md) | 전체 환경변수 목록 (타입·기본값·봇타입별 적용 범위) |
| [`docs/references/LOGGING_GUIDE.md`](docs/references/LOGGING_GUIDE.md) | 로그 레벨, 포맷, 카테고리 명명 규칙 |

## 주요 기능

### 기본 기능 (Default)
- **다이스 굴리기**: 다양한 형식의 주사위 굴리기 지원 (`1d6`, `2d10+5`, `3d6<4` 등)
- **운세**: 일일 운세 시스템 (캐싱 지원)
- **커스텀 명령어**: Google Sheets에서 관리하는 사용자 정의 명령어

### 상점 시스템 (Store)
- **소지금 관리**: 사용자별 가상 화폐 시스템
- **상점**: 아이템 구매 및 관리
- **인벤토리**: 소지 아이템 확인
- **양도**: 아이템 및 화폐 양도 기능 (PREMIUM 설정 필요)
- **관리자 기능**: 화폐 관리 및 시스템 제어

### 스탯 시스템 (Stats)
`store`의 모든 기능 포함 + 스탯 기능 추가:
- **상태 확인**: 체력·정신력 등 커스텀 스탯 표시 (`PEEK_STATUS=True`, `PEEK_HEADER` 설정 필요)
- **아이템 사용 시 스탯 변동**: 상점의 `스탯`/`수치` 컬럼 기반으로 자동 처리
- **스탯 관리자 기능**: 관리자가 스탯 직접 수정 가능

### 시스템 기능 (System)
- **도움말**: 사용 가능한 명령어 안내
- **캐시 리셋**: 캐시 데이터 초기화

### 고급 기능
- **Google Sheets 연동**: 실시간 데이터 동기화
- **DM 지원**: 다이렉트 메시지 전송 기능
- **캐싱 시스템**: 성능 최적화를 위한 스마트 캐싱
- **개선된 로깅**: 깔끔하고 일관된 로그 시스템
- **모듈형 아키텍처**: 확장 가능한 명령어 시스템

## 시스템 요구사항

- **Python**: 3.8 이상
- **운영체제**: Windows, Linux, macOS
- **필수 계정**:
  - 마스토돈 인스턴스 계정
  - Google Cloud Platform 계정 (Sheets API 사용)

## 설정 방법

### 1. 환경 변수 설정

`.env.example` 파일을 복사하여 `.env` 파일을 생성합니다:

```bash
cp .env.example .env
```

`.env` 파일을 편집하여 필요한 값을 설정합니다:

```env
# 마스토돈 API 설정
MASTODON_ACCESS_TOKEN=your_access_token_here
MASTODON_API_BASE_URL=https://your-mastodon-instance.com

# Google Sheets 설정
GOOGLE_CREDENTIALS_PATH=credentials.json
SHEET_ID=your_sheet_id_here

# 봇 타입 (default: 기본 기능만, store: 상점 기능 포함, stats: store + 스탯 시스템)
BOT_TYPE=store

# 화폐 설정
CURRENCY=코인
CURRENCY_EUNNEUN=은

# 디버그 모드
DEBUG_MODE=False
```

### 2. Google Sheets API 설정

1. Google Cloud Console에서 프로젝트 생성
2. Google Sheets API 활성화
3. 서비스 계정 생성 및 JSON 키 다운로드
4. 다운로드한 JSON 파일을 `credentials.json`으로 저장
5. Google Sheets를 서비스 계정 이메일과 공유

### 3. Google Sheets 구조

봇 타입에 따라 필요한 워크시트가 다릅니다:

| 워크시트 | default | store | stats |
|---------|:-------:|:-----:|:-----:|
| 명단    | ✅ | ✅ | ✅ |
| 도움말  | ✅ | ✅ | ✅ |
| 운세    | ✅ | ✅ | ✅ |
| 커스텀  | ✅ | ✅ | ✅ |
| 관리    | — | ✅ | ✅ |
| 상점    | — | ✅ | ✅ |

각 워크시트의 **컬럼 구조·데이터 형식·접근 패턴**은 [`docs/references/SHEETS_STRUCTURE.md`](docs/references/SHEETS_STRUCTURE.md)를 참조하세요.

### 4. 마스토돈 액세스 토큰 발급

마스토돈 인스턴스에서 애플리케이션을 등록하고 액세스 토큰을 발급받습니다.

## 사용 방법

### 봇 실행

```bash
python main.py
```

### 명령어 사용

마스토돈에서 봇 계정을 멘션하여 명령어를 사용합니다:

```
@봇계정 [도움말]
@봇계정 [1d20]
@봇계정 [운세]
@봇계정 [소지금]
@봇계정 [상점]
```

### 명령어 옵션

```bash
# 버전 정보
python main.py --version

# 도움말
python main.py --help
```

## 프로젝트 구조

```
.
├── main.py                    # 메인 실행 파일
├── requirements.txt           # 의존성 목록
├── .env.example              # 환경 변수 예시
├── credentials.json          # Google API 인증 파일
│
├── config/                   # 설정
│   └── settings.py          # 전역 설정 (Config 클래스, 모든 env 변수)
│
├── handlers/                 # 이벤트 핸들러
│   ├── command_router.py    # 명령어 라우팅 (ModernCommandRouter)
│   └── stream_handler.py    # 마스토돈 스트림 처리
│
├── commands/                 # 명령어 시스템
│   ├── base_command.py      # BaseCommand, CommandContext, CommandResponse
│   ├── registry.py          # 명령어 레지스트리 (@register_command)
│   ├── factory.py           # 명령어 팩토리 (인스턴스 생성)
│   ├── default/             # 기본 명령어 (dice, fortune, random, custom)
│   ├── store/               # 상점 명령어 (buy, shop, inventory, transfer 등)
│   │   └── base_store_command.py  # 상점 명령어 공통 기반 클래스
│   ├── stats/               # 스탯 명령어 (peek_status, use_item, stat_admin)
│   └── system/              # 시스템 명령어 (help, cache_reset)
│
├── models/                   # 데이터 모델
│   ├── user.py              # User 데이터클래스
│   └── command_result.py    # CommandResult, CommandStatus
│
├── utils/                    # 유틸리티
│   ├── imports.py           # 명령어 개발용 일괄 import (from utils.imports import *)
│   ├── sheets_operations.py # SheetsManager (Google Sheets API 래퍼)
│   ├── store_helpers.py     # parse_inventory_string, serialize_inventory 등
│   ├── lock_manager.py      # 동시성 제어 (LockManager)
│   ├── cache_manager.py     # 캐시 시스템 (bot_cache)
│   ├── korean_utils.py      # 한국어 조사 처리
│   ├── error_handling.py    # CommandError, SheetAccessError
│   └── logging_config.py   # 로깅 설정
│
├── docs/references/          # 개발 레퍼런스 문서 (아래 참조)
└── logs/                     # 로그 파일
```

## 명령어 시스템

### 명령어 추가 방법

1. `commands/` 디렉토리 내 적절한 패키지에 명령어 파일 생성
2. `BaseCommand`를 상속받은 클래스 작성
3. 데코레이터로 메타데이터 정의

```python
from utils.imports import *  # BaseCommand, CommandContext, CommandResponse, register_command 등 일괄 import

@register_command(
    name="예시명령어",
    aliases=["예시", "example"],
    description="명령어 설명",
    category="카테고리",
    requires_sheets=False,
    requires_api=False
)
class ExampleCommand(BaseCommand):
    def __init__(self, sheets_manager=None, api=None, **kwargs):
        super().__init__(sheets_manager, api, **kwargs)

    def execute(self, context: CommandContext) -> CommandResponse:
        return CommandResponse.create_success("실행 결과")
```

> **주의**: `async def`가 아닌 일반 `def`를 사용합니다. 상세 규칙은 [`docs/references/COMMAND_RULES.md`](docs/references/COMMAND_RULES.md) 참조.

### 커스텀 명령어 추가

Google Sheets의 "커스텀" 워크시트에 다음 형식으로 추가:

| 명령어 | 문구 | 이미지 |
|--------|------|--------|
| 인사 | 안녕하세요! {시전자}님! | |
| 주사위 | {1d6}이 나왔습니다! | |

**지원 문법:**
- `{시전자}`: 명령어 사용자 이름
- `{NdM}`, `{NdM+K}`, `{NdM-K}`: 주사위 (예: `{1d100}`, `{3d6+2}`)
- `{은는}`, `{이가}`, `{을를}`: 한국어 조사 자동 선택
- `{랜덤: 항목1, 항목2, 항목3}`: 쉼표 구분 선택지 중 랜덤 반환

**동일 명령어 여러 행**: 랜덤으로 하나 선택 → `YN`, `가위바위보`, `홀짝` 등 구현에 활용

자세한 컬럼 구조는 [`docs/references/SHEETS_STRUCTURE.md`](docs/references/SHEETS_STRUCTURE.md) 참조.

---

## AI 코딩 작업 시작 가이드

> 새 기능을 추가하거나 AI에게 작업을 요청하기 전, 아래 파일들을 읽히세요.
> 파일 수에 따라 "최소 세트"와 "전체 세트"로 나뉩니다.

### 최소 세트 (모든 작업에 공통)

| 순서 | 파일 | 이유 |
|------|------|------|
| 1 | `README.md` (이 파일) | 전체 구조, 봇 타입 |
| 2 | `docs/references/COMMAND_TEMPLATE.md` | 복붙 가능한 코드 템플릿 |
| 3 | `docs/references/COMMAND_API.md` | CommandContext·CommandResponse·BaseCommand 필드/메서드 |
| 4 | `docs/references/SHEETS_STRUCTURE.md` | 시트 컬럼 구조 및 데이터 형식 |

### 상점/스탯 데이터 변경이 포함된 작업

| 순서 | 파일 | 이유 |
|------|------|------|
| 5 | `docs/references/SHEETS_API.md` | SheetsManager 메서드, 행/컬럼 번호 계산 |
| 6 | `docs/references/CONCURRENCY_GUIDE.md` | 락 패턴, batch_update, 캐시 무효화 |

### 환경변수 확인이 필요한 작업

| 순서 | 파일 | 이유 |
|------|------|------|
| 7 | `docs/references/ENV_VARS.md` | 전체 env var 목록, 기본값, 봇타입별 적용 범위 |

### 아키텍처 규칙 / 코드 리뷰

| 순서 | 파일 | 이유 |
|------|------|------|
| 8 | `docs/references/COMMAND_RULES.md` | 생성자, 데코레이터, 임포트 규약 |
| 9 | `docs/references/LOGGING_GUIDE.md` | 로그 레벨, 카테고리 명명 규칙 |

### 비슷한 기존 명령어 읽기 (참고용)

| 작업 유형 | 참고 파일 |
|----------|----------|
| 기본 명령어 (조회만) | `commands/default/fortune_command.py` |
| 상점 명령어 (소지금/인벤토리) | `commands/store/buy_command.py` |
| 스탯 명령어 (스탯 변경) | `commands/stats/use_item_command.py` |
| 스탯 관리자 | `commands/stats/stat_admin_command.py` |
| 양도 (두 사용자 동시) | `commands/store/transfer_command.py` |

### 프롬프트 예시 (최소 표현)

```
docs/references/ 의 COMMAND_TEMPLATE.md, COMMAND_API.md, SHEETS_STRUCTURE.md, SHEETS_API.md, CONCURRENCY_GUIDE.md 를 읽고,

commands/stats/ 에 [체력 회복] 명령어를 추가해줘.
- 관리 워크시트의 '체력' 컬럼에 10을 더함
- category: "스탯", requires_sheets: True
- 동시성 제어 포함
```

---

## 개발 가이드

### Utils 폴더 활용 가이드

`utils/` 폴더는 프로젝트 전반에서 사용되는 공통 유틸리티 함수들을 포함합니다. 새로운 명령어를 개발할 때 이 함수들을 활용하면 중복 코드를 줄이고 일관된 패턴을 유지할 수 있습니다.

#### 주요 Utils 모듈

**1. `utils/store_helpers.py` - 상점 명령어 공통 함수**
```python
from utils.store_helpers import (
    parse_inventory_string,  # 인벤토리 문자열 파싱
    invalidate_user_cache,   # 사용자 캐시 무효화
    load_item_data,          # 아이템 데이터 로드
    load_user_data           # 명단 데이터 로드
)

# 인벤토리 파싱 예시
inventory_str = "{'사과': 3, '반지': 1}"
inventory = parse_inventory_string(inventory_str)
# 결과: {'사과': 3, '반지': 1}

# 캐시 무효화
invalidate_user_cache()  # user_data와 all_users_data 모두 무효화
```

**2. `utils/korean_utils.py` - 한국어 조사 처리**
```python
from utils.korean_utils import add_eul_reul, add_eun_neun, add_i_ga

# 을/를 조사 추가
item = "사과"
item_with_josa = add_eul_reul(item)  # "사과를"

# 은/는 조사 추가
user = "홍길동"
user_with_josa = add_eun_neun(user)  # "홍길동은" 또는 "홍길동는"

# 이/가 조사 추가
name = "철수"
name_with_josa = add_i_ga(name)  # "철수가"
```

**3. `utils/cache_manager.py` - 캐싱 관리**
```python
from utils.cache_manager import bot_cache

# 캐시에 데이터 저장
bot_cache.cache_item_data(item_data, ttl_seconds=config.CACHE_TTL)

# 캐시에서 데이터 조회
cached_data = bot_cache.get_item_data()

# 캐시 무효화
bot_cache.invalidate_item_data()
```

**4. `utils/error_handling.py` - 에러 처리**
```python
from utils.error_handling import CommandError

# 명령어 에러 발생
raise CommandError("아이템을 찾을 수 없습니다.")

# 자동으로 사용자에게 표시되는 에러 메시지
```

**5. `utils/logging_config.py` - 로깅**
```python
from utils.logging_config import logger

logger.info("정보 메시지")
logger.warning("경고 메시지")
logger.error("에러 메시지")
logger.debug("디버그 메시지")
```

#### 새로운 명령어 개발 시 활용

**기본 패턴:**
```python
from utils.error_handling import CommandError
from utils.logging_config import logger
from utils.store_helpers import parse_inventory_string, invalidate_user_cache
from utils.korean_utils import add_eul_reul

class MyCommand(BaseCommand):
    def _execute_command_logic(self, user: User, keywords: List[str]):
        try:
            # 1. 입력 파싱
            item_name = keywords[1] if len(keywords) > 1 else None
            
            # 2. 데이터 조회 (시트 직접 조회)
            management_data = self.sheets_manager.get_worksheet_data('관리', use_cache=False)
            
            # 3. 데이터 가공
            inventory_str = str(row.get('소지품', '{}'))
            inventory = parse_inventory_string(inventory_str)
            
            # 4. 검증
            if item_name not in inventory:
                item_with_josa = add_eul_reul(item_name)
                raise CommandError(f"{item_name}{item_with_josa} 보유하고 있지 않습니다.")
            
            # 5. 시트 업데이트
            # ... 업데이트 로직 ...
            
            # 6. 캐시 무효화
            invalidate_user_cache()
            
            # 7. 결과 반환
            return "완료되었습니다.", data
            
        except CommandError:
            raise  # CommandError는 그대로 전달
        except Exception as e:
            logger.error(f"명령어 실행 중 오류: {e}", exc_info=True)
            raise CommandError("처리 중 오류가 발생했습니다.")
```

**중요 원칙:**
1. **에러 처리는 일관되게**: `CommandError`를 사용하여 사용자에게 표시할 에러 생성
2. **조사는 동적으로**: `korean_utils`의 함수를 사용하여 조사 처리
3. **캐시는 제때 무효화**: 데이터 변경 시 `invalidate_user_cache()` 호출
4. **로깅은 적절히**: 중요한 단계마다 로그 기록
5. **중복 코드는 피하기**: 공통 함수는 `utils/`에 모아 사용

#### Utils 모듈 추가 시

새로운 공통 함수가 필요하면 `utils/store_helpers.py`에 추가:

```python
def my_new_helper_function(param: str) -> str:
    """
    새로운 헬퍼 함수 설명
    
    Args:
        param: 설명
        
    Returns:
        설명
    """
    # 구현
    return result
```

### 로깅 시스템

개선된 로깅 시스템을 사용하여 일관된 로그를 출력합니다:

```python
from utils.logging_config import LogFormatter, log_command_result

# 명령어 실행 로그
log_command_result(user_id, command, success=True, duration=0.5)

# 직접 포맷 사용
logger.info(LogFormatter.command(user_id, command, True, 0.5, "추가정보"))
logger.info(LogFormatter.api("Sheets", "읽기", True, 0.3))
logger.info(LogFormatter.system("봇 시작", "명령어 15개 로드"))
```

**로그 포맷:**
```
[CMD] user@명령어 → ✓ 성공 | 0.450s | 결과: 256자
[API] Sheets/읽기 → ✓ 성공 | 0.320s | 10개 항목
[SYS] ✓ 봇 시작 | 명령어 15개 로드
```

### DEBUG 모드

`.env` 파일에서 디버그 모드 제어:

```env
DEBUG_MODE=False  # 기본 로그만 출력
DEBUG_MODE=True   # 상세한 디버그 로그 포함
```

### 테스트

```bash
# 테스트 실행
pytest tests/

# 커버리지 포함
pytest --cov=. tests/
```

## 트러블슈팅

### 일반적인 문제

**1. Google Sheets 연결 실패**
```
❌ Google Sheets 연결 실패
```
- `credentials.json` 파일 경로 확인
- Google Sheets가 서비스 계정과 공유되었는지 확인
- Google Sheets API가 활성화되었는지 확인

**2. 마스토돈 API 연결 실패**
```
❌ 마스토돈 API 연결 실패
```
- `MASTODON_ACCESS_TOKEN` 확인
- `MASTODON_API_BASE_URL` 확인 (끝에 슬래시 없이)
- 네트워크 연결 확인

**3. 명령어가 작동하지 않음**
- 봇 계정을 올바르게 멘션했는지 확인
- 명령어 형식이 `[명령어]`인지 확인
- 로그 파일(`bot.log`)에서 오류 확인

**4. 캐시 문제**
```bash
# 관리자 계정으로 캐시 리셋
@봇계정 [캐시 리셋/전체]
```

### 로그 확인

```bash
# 최근 로그 확인
tail -f bot.log

# 에러만 확인
grep "ERROR" bot.log

# 특정 사용자 로그 확인
grep "user_id" bot.log
```

### 성능 최적화

**캐시 TTL 조정:**
```env
CACHE_TTL=300  # 5분 (초 단위)
```

**로그 레벨 조정:**
```env
LOG_LEVEL=WARNING  # ERROR, WARNING, INFO, DEBUG
```

## 환경 변수 레퍼런스

### 필수 설정

| 변수 | 설명 | 예시 |
|------|------|------|
| `MASTODON_ACCESS_TOKEN` | 마스토돈 액세스 토큰 | `abc123...` |
| `MASTODON_API_BASE_URL` | 마스토돈 인스턴스 URL | `https://mastodon.social` |
| `SHEET_ID` | Google Sheets ID | `1AbC...` |

### 선택 설정

| 변수 | 설명 | 기본값 |
|------|------|--------|
| `BOT_TYPE` | 봇 타입 (`default`/`store`/`stats`) | `default` |
| `CURRENCY` | 화폐 이름 | `포인트` |
| `CURRENCY_EUNNEUN` | 화폐명 조사 (은/는) | `은` |
| `LOG_LEVEL` | 로그 레벨 | `INFO` |
| `DEBUG_MODE` | 디버그 모드 | `False` |
| `CACHE_TTL` | 캐시 유효 시간 (초) | `1800` |
| `PEEK_STATUS` | `[상태 확인]` 명령어 활성화 (stats 타입) | `False` |
| `PEEK_HEADER` | 상태 확인 시 표시할 컬럼 목록 (쉼표 구분) | `` |
| `PREMIUM_IMAGE_ENABLED` | 커스텀 명령어 이미지 첨부 기능 | `False` |
| `PREMIUM_TRANSFER_ITEM_ENABLED` | 아이템 양도 기능 | `False` |
| `PREMIUM_TRANSFER_MONEY_ENABLED` | 소지금 양도 기능 | `False` |
| `BOT_OPERATION_START` | 봇 가동 시작일 (YYYY.MM.DD) | `` |
| `BOT_OPERATION_END` | 봇 가동 종료일 (YYYY.MM.DD) | `` |

자세한 설정은 `.env.example` 파일을 참조하세요.

## 업데이트 내역

### v2.1 (최신)
- 개선된 로깅 시스템 (통일된 로그 포맷)
- DEBUG 모드 지원
- 성능 최적화 (로그 출력 대폭 축소)
- 버그 수정 및 안정성 개선

### v2.0
- 모듈형 명령어 시스템
- 자동 명령어 발견
- DM 지원
- 캐싱 시스템 개선

## 라이선스

이 프로젝트는 커미션 작업물입니다.

## 기여

버그 리포트나 기능 제안은 이슈로 등록해주세요.

## 문의

프로젝트 관련 문의사항이 있으시면 이슈를 생성해주세요.

---

**한참 longwhile@crepe**

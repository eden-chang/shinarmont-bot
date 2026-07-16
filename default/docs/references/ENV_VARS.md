# 환경변수 (.env) 레퍼런스

`.env` 파일에서 설정하는 모든 환경변수 목록입니다.
`config/settings.py`의 `Config` 클래스가 이 값을 로드합니다.

코드에서 접근: `from config.settings import config` → `config.VARIABLE_NAME`

---

## Mastodon API 인증

| 변수 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `MASTODON_CLIENT_ID` | str | ✅ | 앱 클라이언트 ID |
| `MASTODON_CLIENT_SECRET` | str | ✅ | 앱 클라이언트 시크릿 |
| `MASTODON_ACCESS_TOKEN` | str | ✅ | 봇 계정 액세스 토큰 |
| `MASTODON_API_BASE_URL` | str | ✅ | 인스턴스 URL (예: `https://example.social`) |

---

## Google 서비스

| 변수 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `SHEET_ID` | str | — | Google Sheets 스프레드시트 ID |
| `GOOGLE_CREDENTIALS_PATH` | str | `credentials/credentials.json` | 서비스 계정 JSON 파일 경로 |
| `GOOGLE_DRIVE_FOLDER_ID` | str | — | Drive 파일 업로드 대상 폴더 ID |

---

## 봇 기본 설정

| 변수 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `BOT_NAME` | str | — | 봇 표시 이름 |
| `BOT_TYPE` | str | `default` | 봇 타입. `default` / `store` / `stats` |
| `RESPONSE_PREFIX` | str | (없음) | 모든 응답 앞에 붙는 문자열 (예: `🤖 `) |
| `SYSTEM_ADMIN_ID` | str | `admin` | 관리자 명령어를 사용할 수 있는 Mastodon 아이디 (`,`로 복수 지정 가능) |

### BOT_TYPE 기능 범위

| 타입 | 포함 기능 |
|------|----------|
| `default` | 주사위, 운세, 커스텀 명령어 |
| `store` | default + 소지금/소지품/상점/양도 |
| `stats` | store + 체력·정신력 등 스탯 컬럼, [사용], [상태 확인], [스탯 관리] |
| `investigate` | `[진입]`, `[조사]` 전용. 그 외 모든 명령어는 조용히 무시. 보조 스프레드시트(`INVESTIGATION_SHEET_ID`) 필요. 소지품·스탯 변동은 기본 시트의 `관리` 워크시트에 반영 |

---

## 워크시트 이름 커스터마이즈

기본값을 사용하면 `.env`에 없어도 됩니다.

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `HELP_SHEET` | `도움말` | 도움말 워크시트 이름 |
| `LIST_SHEET` | `명단` | 명단 워크시트 이름 |
| `CUSTOM_SHEET` | `커스텀` | 커스텀 명령어 워크시트 이름 |
| `FORTUNE_SHEET` | `운세` | 운세 워크시트 이름 |
| `SHOP_SHEET` | `상점` | 상점 워크시트 이름 |

코드에서 접근: `config.get_worksheet_name('ROSTER')` → `'명단'`

---

## 화폐 설정 (store / stats 타입)

| 변수 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `CURRENCY` | str | `포인트` | 화폐 단위 이름 |
| `CURRENCY_EUNNEUN` | str | `은` | 화폐에 붙는 조사 (`은` 또는 `는`) |

---

## 프리미엄 기능 (store / stats 타입)

기본값은 모두 `False`. 필요한 것만 활성화.

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `PREMIUM_CUSTOMC_ENABLED` | `False` | 커스텀 명령어 `이미지` 컬럼 활성화 |
| `PREMIUM_IMAGE_ENABLED` | `False` | 명령어 응답에 이미지 첨부 허용 |
| `PREMIUM_TRANSFER_ITEM_ENABLED` | `False` | `[양도]` 시 아이템 양도 허용 |
| `PREMIUM_TRANSFER_MONEY_ENABLED` | `False` | `[양도]` 시 소지금 양도 허용 |
| `PREMIUM_TRANSFER_ENABLED` | `False` | *(레거시)* True이면 아이템·소지금 양도 모두 활성화 |

---

## 조사 기능 설정 (investigate 타입 전용)

| 변수 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `INVESTIGATION_ENABLED` | bool | `False` | 조사 기능 활성화 여부. False면 `[진입]`·`[조사]` 명령어가 등록되지 않음 |
| `INVESTIGATION_SHEET_ID` | str | (없음) | 조사 데이터가 담긴 보조 스프레드시트 ID (기본 `SHEET_ID`와 **별개**) |
| `INVESTIGATION_ENTRY_SHEET` | str | `진입` | 보조 시트의 '진입' 워크시트 이름 |

```env
# 예시 (investigate 봇)
INVESTIGATION_ENABLED=True
INVESTIGATION_SHEET_ID=abc123xyz456
```

> 조사 시트의 `진입` 워크시트와 각 장소명 워크시트(`병원`, `학교` 등)만 보조 시트에 둡니다.
> 소지품·스탯 변동은 기본 시트(`SHEET_ID`)의 `관리` 워크시트에 반영됩니다.

---

## 상태 확인 설정 (stats 타입 전용)

| 변수 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `PEEK_STATUS` | bool | `False` | `[상태 확인]` 명령어 활성화 여부 |
| `PEEK_HEADER` | str | (없음) | `[상태 확인]`이 출력할 컬럼명 목록 (`,`로 구분) |

```env
# 예시
PEEK_STATUS=True
PEEK_HEADER=소지금,소지품,체력,정신력
```

---

## 캐시 설정

| 변수 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `CACHE_TTL` | int | `1800` | 캐시 유효 시간 (초). 명단·도움말·상점·운세·커스텀에만 적용 |
| `FORTUNE_CACHE_ENABLED` | bool | `True` | 운세 캐시 사용 여부 |

> **관리 워크시트는 캐싱 금지.** `get_worksheet_data('관리', use_cache=False)` 항상 사용.

---

## 봇 동작 제한

| 변수 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `BOT_MAX_RETRIES` | int | `5` | API 실패 시 최대 재시도 횟수 (`config.MAX_RETRIES`) |
| `BOT_BASE_WAIT_TIME` | int | `2` | 재시도 대기 시간 기본값(초) (`config.BASE_WAIT_TIME`) |
| `BOT_MAX_DICE_COUNT` | int | `20` | 주사위 최대 개수 |
| `BOT_MAX_DICE_SIDES` | int | `1000` | 주사위 최대 면수 |
| `BOT_MAX_CARD_COUNT` | int | `52` | 카드 최대 수 |
| `MAX_MESSAGE_LENGTH` | int | `500` | 응답 메시지 최대 길이 (초과 시 자동 분할) |

---

## 봇 가동 기간

| 변수 | 형식 | 예시 | 설명 |
|------|------|------|------|
| `BOT_OPERATION_START` | `YYYY.MM.DD` | `2025.01.01` | 가동 시작일 (비어 있으면 제한 없음) |
| `BOT_OPERATION_END` | `YYYY.MM.DD` | `2025.12.31` | 가동 종료일 (비어 있으면 제한 없음) |

---

## 로그 설정

| 변수 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `LOG_LEVEL` | str | `INFO` | `ERROR` / `WARNING` / `INFO` / `DEBUG` |
| `LOG_FILE_PATH` | str | `logs/bot.log` | 로그 파일 경로 |
| `LOG_MAX_BYTES` | int | `10485760` | 로그 파일 최대 크기 (10MB) |
| `LOG_BACKUP_COUNT` | int | `5` | 로그 파일 최대 보관 수 |
| `DEBUG_MODE` | bool | `False` | DEBUG 로그 출력 여부 |
| `ENABLE_CONSOLE_LOG` | bool | `True` | 콘솔 출력 여부 |

---

## 명령어 필터 / 무시 키워드

| 변수 | 타입 | 설명 |
|------|------|------|
| `COMMAND_FILTER` | str | 이 봇이 처리할 명령어 목록 (`,`로 구분). 비어 있으면 전체 허용 |
| `BOT_IGNORED_KEYWORDS` | str | 이 봇이 무시할 키워드 (`,`로 구분). 다른 봇에 넘길 때 사용 |
| `IGNORED_COMMAND_KEYWORDS` | str | (레거시) 무시 키워드 목록 |

---

## 멀티 봇 모드

싱글 봇이면 이 섹션은 무시해도 됩니다.

| 변수 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `ENABLE_MULTI_BOT` | bool | `False` | 멀티 봇 모드 활성화 |
| `BOT_COUNT` | int | `0` | (참고용) 활성화된 봇 수 표시. 실제 로딩에는 영향 없음 |
| `BOT_ID` | str | — | 이 봇의 식별자 (멀티 봇 모드에서 사용) |

멀티 봇 모드에서는 봇별로 `BOT1_*`, `BOT2_*` 형태의 접두사가 붙은 변수를 사용합니다.
(예: `BOT1_ACCESS_TOKEN`, `BOT1_BOT_TYPE`, `BOT1_LOG_FILE`)

### 슬롯 자동 탐지

`BOT_COUNT`는 더 이상 "스캔할 슬롯의 상한"이 아닙니다. 멀티 봇 모드에서는 **환경변수에 `BOTn_*` 키가 존재하는 슬롯**을 모두 자동 탐지하고, 그중 `BOTn_ENABLED=True`인 것만 실행합니다.

```env
# 예: 슬롯 2와 5만 실행하고 싶을 때
ENABLE_MULTI_BOT=True
BOT2_ENABLED=True
BOT2_ACCESS_TOKEN=...
BOT2_BOT_TYPE=store
BOT5_ENABLED=True
BOT5_ACCESS_TOKEN=...
BOT5_BOT_TYPE=investigate
```

→ 2개 봇 실행. 슬롯 1/3/4 관련 키가 없어도 정상 동작합니다.

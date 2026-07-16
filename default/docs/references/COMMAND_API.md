# 명령어 API 레퍼런스

명령어 개발에서 사용하는 핵심 클래스의 필드·메서드 정리.
`commands/base_command.py`와 `commands/store/base_store_command.py` 기준.

---

## CommandContext

명령어 실행 시 전달되는 컨텍스트 객체. `execute(self, context: CommandContext)`로 받습니다.

### 필드

| 필드 | 타입 | 설명 |
|------|------|------|
| `user_id` | str | 사용자 Mastodon 아이디 (`@` 제외) |
| `user_name` | str | 사용자 이름. 비어 있으면 `user_id`와 동일 |
| `original_text` | str | 원본 멘션 텍스트 |
| `keywords` | `List[str]` | 파싱된 키워드 목록. `[소지품]` → `['소지품']`, `[구매/사과/2]` → `['구매', '사과', '2']` |
| `request_id` | str | 요청 고유 ID |
| `metadata` | `Dict[str, Any]` | 추가 메타데이터 |

### 메서드

```python
# 특정 위치의 키워드 반환 (없으면 default 반환)
context.get_keyword(1, default="")  # keywords[1] 또는 ""

# 키워드 포함 여부 (대소문자 무시)
context.has_keyword("구매")  # bool

# 메타데이터 저장/조회
context.add_metadata("key", value)
context.get_metadata("key", default=None)
```

### 자주 쓰는 패턴

```python
def execute(self, context: CommandContext) -> CommandResponse:
    user_id   = context.user_id    # 'longwhile'
    user_name = context.user_name  # '한참'
    keywords  = context.keywords   # ['구매', '사과', '2']

    # 파라미터 추출
    item_name = keywords[1] if len(keywords) > 1 else None
    count_str = keywords[2] if len(keywords) > 2 else "1"
```

---

## CommandResponse

명령어 실행 결과를 담는 데이터 클래스.

### 클래스 메서드 (팩토리)

```python
# 성공 응답
CommandResponse.create_success("처리 완료!")
CommandResponse.create_success("완료", data={"item": "사과", "count": 2})

# 에러 응답
CommandResponse.create_error("소지금이 부족합니다.")
CommandResponse.create_error("오류 발생", error=e, data={"debug": "..."})
```

### 필드

| 필드 | 타입 | 설명 |
|------|------|------|
| `success` | bool | 성공 여부 |
| `message` | str | 사용자에게 전달될 응답 메시지 |
| `data` | Any | 추가 데이터 (로그/디버그용) |
| `error` | Exception | 발생한 예외 (있을 경우) |

### 인스턴스 메서드

```python
response.is_successful()  # bool
response.get_message()    # str
```

---

## BaseCommand

모든 명령어의 기반 클래스. `commands/base_command.py`.

### 인스턴스 속성

| 속성 | 타입 | 설명 |
|------|------|------|
| `self.sheets_manager` | `SheetsManager` | Google Sheets 접근 객체. `requires_sheets=True`인 명령어에서 사용 |
| `self.api` | Mastodon API | Mastodon API 인스턴스. `requires_api=True`인 명령어에서 사용 |

### 클래스 속성 (기본값, @register_command로 오버라이드)

| 속성 | 기본값 | 설명 |
|------|--------|------|
| `instance_scope` | `SINGLETON` | 인스턴스 생명주기 (`SINGLETON` / `PROTOTYPE` / `REQUEST`) |
| `requires_sheets` | `True` | 시트 연결 필요 여부 |
| `requires_api` | `False` | Mastodon API 필요 여부 |
| `admin_only` | `False` | 관리자 전용 여부 |
| `enabled` | `True` | 명령어 활성화 여부 |

### 생성자

```python
def __init__(self, sheets_manager=None, api=None, **kwargs):
    super().__init__(sheets_manager, api, **kwargs)
```

모든 서브클래스에서 이 시그니처를 따라야 합니다.

### 추상 메서드

```python
@abstractmethod
def execute(self, context: CommandContext) -> CommandResponse:
    """서브클래스에서 반드시 구현"""
```

### 선택적 오버라이드 메서드

```python
def validate_context(self, context: CommandContext) -> bool:
    """실행 전 컨텍스트 유효성 검사 (기본: True 반환)"""

def pre_execute(self, context: CommandContext) -> None:
    """실행 전 훅"""

def post_execute(self, context: CommandContext, response: CommandResponse) -> None:
    """실행 후 훅"""

@staticmethod
def get_supported_keywords() -> list[str]:
    """지원 키워드 목록 (선택, 데코레이터와 일치시킬 것)"""
```

---

## BaseStoreCommand

store/stats 명령어용 기반 클래스. `commands/store/base_store_command.py`.
`BaseCommand`를 상속하며 `execute()` 보일러플레이트를 제공합니다.

### 사용 방법

`execute()` 대신 `_execute_command_logic()` 만 구현합니다.

```python
class MyCommand(BaseStoreCommand):
    def _execute_command_logic(self, user: User, keywords: List[str]):
        # 로직 구현
        return CommandResponse.create_success("완료")
        # 또는: return ("완료 메시지", {"data": "값"})
```

### 제공 메서드

#### 워크시트 컬럼/행 탐색

```python
# 관리 워크시트에서 컬럼 번호(int, 1-indexed) 반환
self._find_id_column()         # '아이디' 컬럼
self._find_money_column()      # '소지금' 컬럼
self._find_inventory_column()  # '소지품' 컬럼

# 사용자의 시트 행 번호(int, 1-indexed) 반환
self._find_user_row(user_id)
self._find_user_row(user_id, worksheet=ws, id_col=2)
```

> **참고**: 위 메서드들은 `worksheet.col_values()`를 사용하므로 빈 행이 있어도 정확합니다.
> `get_worksheet_data()`의 `_row_number`와 같은 방식으로 신뢰할 수 있습니다.

#### 데이터 로드

```python
items = self._load_item_data()   # 상점 아이템 목록 (캐시 사용)
users = self._load_user_data()   # 명단 데이터 (캐시 사용)
```

#### 인벤토리 / 캐시

```python
inventory = self._parse_inventory(inventory_str)  # None 안전 파싱
self._invalidate_user_cache()                      # 변경 후 캐시 무효화
```

---

## @register_command 데코레이터

```python
@register_command(
    name="명령어이름",          # 대표 키워드 (표시용)
    aliases=["별칭1", "별칭2"], # 검색/매칭용 별칭
    description="설명",
    category="카테고리",         # 예: "상점", "시스템", "게임"
    examples=["[명령어이름]"],
    requires_sheets=True,        # SheetsManager 필요 여부
    requires_api=False,          # Mastodon API 필요 여부
    priority=0                   # 숫자 클수록 먼저 매칭
)
```

---

## CommandError

사용자에게 보여줄 에러 메시지 전달용 예외. `utils/error_handling.py`.

```python
from utils.imports import *  # CommandError 포함

# 사용자 에러 → 메시지가 응답으로 전달됨
raise CommandError("소지금이 부족합니다.")

# 예외 처리 패턴
try:
    ...
except CommandError:
    raise  # 그대로 전파 (BaseStoreCommand가 처리)
except Exception as e:
    logger.error(f"오류: {e}", exc_info=True)
    raise CommandError("처리 중 오류가 발생했습니다.")
```

> `BaseStoreCommand`를 사용하면 `CommandError`와 일반 `Exception` 처리가 자동으로 됩니다.
> 직접 `BaseCommand`를 상속할 때만 수동 try/except가 필요합니다.

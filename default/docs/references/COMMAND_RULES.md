# 명령어 정규화 규칙 (BaseCommand 기준)

본 문서는 모든 명령어 구현을 최신 아키텍처(BaseCommand)와 호환되도록 정규화하기 위한 규칙을 정의합니다. 대상은 `default/`, `store/`, `system/` 전 범위이며, 특히 `store/`의 레거시 코드를 본 규칙에 맞게 업데이트합니다.

## 1) 생성자 규약

- 모든 명령어 클래스는 다음 시그니처를 반드시 따릅니다.

```python
def __init__(self, sheets_manager=None, api=None, **kwargs):
    super().__init__(sheets_manager, api, **kwargs)
```

- 의미
  - `sheets_manager`: Google Sheets 접근 객체 (옵션)
  - `api`: Mastodon API 인스턴스 (옵션)
  - `**kwargs`: 향후 확장을 위한 여분
- 절대 금지
  - 인자를 받지 않는 생성자만 제공
  - `super().__init__` 미호출
  - 더미/폴백 BaseCommand로 인해 `__init__(self)` 형태만 남는 상태

## 2) 메타데이터 규약 (@register_command)

- 모든 명령어는 `@register_command(...)` 데코레이터로 메타데이터를 고정합니다.
- 대표 키워드는 데코레이터의 `name`에 설정합니다(원하는 표시 문자열, 보통 한글). 첫 번째 표시 키워드가 됩니다.
- `aliases`는 사람 친화적 표기를 그대로(대소문자/공백/한글 포함) 적되, 첫 번째 항목이 대표가 되지 않도록 주의합니다.

```python
from commands.registry import register_command

@register_command(
    name="소지품",                       # 대표 키워드 (표시용)
    aliases=["인벤토리", "가방", "주머니"], # 별칭 (원본 표기 유지)
    description="현재 소지품을 확인합니다.",
    category="상점",
    examples=["[소지품]"],
    requires_sheets=True,
    requires_api=False,
    priority=0
)
class InventoryCommand(BaseCommand):
    def __init__(self, sheets_manager=None, api=None, **kwargs):
        super().__init__(sheets_manager, api, **kwargs)
```

- 규칙
  - `name`: 대표 키워드. 로그/도움말 표시에 그대로 사용됩니다.
  - `aliases`: 검색/매칭 용 별칭들. 표기 원형을 보존합니다(소문자 강제 금지).
  - `description`, `category`, `examples`, `requires_*`, `priority`는 가능한 한 채워 넣습니다.

## 3) get_supported_keywords 규약 (선택)

- 데코레이터가 메인 소스입니다. `get_supported_keywords()`는 선택 사항입니다.
- 제공 시 다음을 권장합니다.
  - 대표 키워드를 첫 번째로 반환
  - 인스턴스 생성에 의존하지 않도록 `@staticmethod`를 권장
  - 데코레이터의 `name/aliases`와 내용이 일치해야 합니다.

```python
@staticmethod
def get_supported_keywords() -> list[str]:
    return ["소지품", "인벤토리", "가방", "주머니"]
```

## 4) Import / 폴백(ImportError) 규약

- 광범위한 `try: import ... except ImportError: 더미 클래스 정의` 패턴을 금지합니다.
- 필수 모듈이 없으면 초기화 단계에서 명확히 로그를 남기고 실패시키며, 더미 BaseCommand/더미 타입을 정의해 흐름을 이어가지 않습니다.
- `sys.path` 조작을 최소화합니다. 루트 기준 절대 임포트를 사용합니다.

허용되는 최소 폴백 예시 (로그만 남기고 즉시 반환):
```python
try:
    from models.command_result import SomeResult
except ImportError as e:
    logger.error(f"필수 모듈 임포트 실패: {e}")
    raise
```

## 5) 표시/검색 규약 (레지스트리/메인)

- 대표 키워드 표시는 "데코레이터 메타데이터"를 우선합니다.
- 표시/로그를 위해 명령어 인스턴스를 생성하지 않습니다(인스턴스화 실패로 표기가 깨지는 문제 방지).
- 별칭은 원본 표기를 유지하고, 검색/매칭 시 내부적으로만 소문자 비교를 합니다.

## 6) 로깅/카테고리 규약 (요약)

- 카테고리는 의미 있는 한글 명칭을 사용합니다(예: "상점", "시스템", "게임").
- 명령어별 주요 로깅 메시지는 다음을 권장합니다.
  - 초기화: "[초기화] {CommandName} 초기화 완료"
  - 라우팅/실행: "[라우팅]", "[실행]" 접두사 일관성 유지
  - 시트 접근: "관리 워크시트에서 조회/업데이트" 명시

## 7) 레거시 제거

- Legacy/Adapter/Strategy 기반 인스턴스 생성 경로를 제거하고 표준 생성자만 사용합니다.
- 더미 BaseCommand/더미 타입 정의를 제거합니다.

## 8) 신규 명령어 개발

실제 코드 템플릿(최소형/상점형/스탯형)과 개발 팁은 [`COMMAND_TEMPLATE.md`](COMMAND_TEMPLATE.md) 를 사용하세요.
CommandContext·CommandResponse·BaseCommand API는 [`COMMAND_API.md`](COMMAND_API.md) 를 참조하세요.

---

본 규칙은 system/ 폴더 구현을 기준으로 하며, store/ 및 default/ 폴더의 모든 명령어가 동일한 규약을 따르도록 합니다. 규약 위반으로 인한 인스턴스 생성 실패, 대표 키워드 표기 오류, 더미 클래스 주입 등의 문제를 방지하는 것이 목적입니다.



# 명령어 개발 템플릿

새로운 명령어를 추가할 때 이 템플릿을 사용하면 import 오류 없이 빠르게 개발할 수 있습니다.

## 기본 명령어 템플릿

### 최소 템플릿 (개선됨)

```python
"""
나의 명령어 설명
"""

from utils.imports import *

@register_command(
    name="나의명령어",
    aliases=["별칭1", "별칭2"],
    description="명령어 설명",
    category="카테고리",
    examples=["[명령어/파라미터]"],
    requires_sheets=True,
    requires_api=False
)
class MyCommand(BaseCommand):
    
    @handle_command_errors  # ← 에러 자동 처리
    @validate_keywords(min_length=1, max_length=3)  # ← 입력 검증
    @log_execution  # ← 로깅 자동
    def execute(self, context: CommandContext) -> CommandResponse:
        """명령어 실행"""
        # User 객체 자동 생성
        user = create_user_from_context(context)
        
        # 명령어 로직만 집중
        message = f"{user.name}님, 완료되었습니다!"
        data = {}
        
        return CommandResponse.create_success(message, data)
```

### 상점 명령어 템플릿 (데이터 조회 + 업데이트)

```python
"""
상점 관련 명령어 - 인벤토리 조회 및 수정 예시
"""

from utils.imports import *

@register_command(
    name="내명령어",
    aliases=["명령어"],
    description="설명",
    category="상점",
    requires_sheets=True,
    requires_api=False
)
class MyStoreCommand(BaseCommand):
    
    def execute(self, context: CommandContext) -> CommandResponse:
        """명령어 실행"""
        try:
            user = User(id=context.user_id, name=context.user_name)
            keywords = context.keywords
            
            # 1. 입력 파싱
            item_name = keywords[1] if len(keywords) > 1 else None
            if not item_name:
                raise CommandError("아이템명을 입력해주세요.")
            
            # 2. 사용자 데이터 조회
            management_data = self.sheets_manager.get_worksheet_data('관리', use_cache=False)
            user_data = self._find_user_in_management(user.id, management_data)
            
            if not user_data:
                raise CommandError(f"{user.name} 님의 정보를 찾을 수 없습니다.")
            
            # 3. 인벤토리 파싱
            inventory_str = str(user_data.get('소지품', '{}'))
            inventory = parse_inventory_string(inventory_str)
            
            # 4. 비즈니스 로직
            if item_name not in inventory:
                item_josa = add_eul_reul(item_name)
                raise CommandError(f"{item_name}{item_josa} 보유하고 있지 않습니다.")
            
            # 5. 데이터 수정 (예시)
            # ... 업데이트 로직 ...
            
            # 6. 캐시 무효화
            invalidate_user_cache()
            
            # 7. 결과 반환
            message = f"{item_name} 처리 완료"
            data = {'result': 'success'}
            
            return CommandResponse.create_success(message, data)
            
        except CommandError as e:
            return CommandResponse.create_error(str(e), error=e)
        except Exception as e:
            logger.error(f"명령어 실행 오류: {e}", exc_info=True)
            return CommandResponse.create_error("처리 중 오류가 발생했습니다.", error=e)
    
    def _find_user_in_management(self, user_id: str, management_data: list) -> dict:
        """관리 워크시트에서 사용자 찾기"""
        for row in management_data:
            if str(row.get('아이디', '')).strip() == user_id:
                return row
        return None
```

### 스탯 명령어 템플릿 (관리 워크시트 스탯 컬럼 읽기/쓰기)

stats 타입 전용. 체력·정신력 등 커스텀 스탯 컬럼을 읽거나 수정할 때 사용.

```python
"""
스탯 명령어 예시 - 체력 회복
"""

from utils.imports import *

@register_command(
    name="체력 회복",
    aliases=["회복"],
    description="체력을 10 회복합니다.",
    category="스탯",
    examples=["[체력 회복]"],
    requires_sheets=True,
    requires_api=False
)
class HealCommand(BaseCommand):

    def execute(self, context: CommandContext) -> CommandResponse:
        try:
            user = User(id=context.user_id, name=context.user_name)

            # 1. 관리 워크시트 조회 (캐싱 금지)
            management_data = self.sheets_manager.get_worksheet_data('관리', use_cache=False)
            user_row = self._find_user_row(user.id, management_data)

            if not user_row:
                raise CommandError(f"{user.name} 님의 정보를 찾을 수 없습니다.")

            # 2. 스탯 컬럼 읽기 (정수 변환)
            current_hp = int(float(user_row.get('체력', 0)))

            # 3. 비즈니스 로직
            healed = 10
            new_hp = current_hp + healed

            # 4. 시트 업데이트
            # _row_number: get_worksheet_data()가 자동 포함하는 실제 시트 행 번호
            # (빈 행이 중간에 있어도 정확하게 동작)
            row_index = user_row['_row_number']
            # 컬럼 번호는 정수로 필요 — 헤더에서 조회
            worksheet = self.sheets_manager.get_worksheet('관리')
            header_row = worksheet.row_values(1)
            hp_col = header_row.index('체력') + 1  # 0-indexed → 1-indexed
            self.sheets_manager.update_cell('관리', row_index, hp_col, new_hp)

            # 5. 캐시 무효화
            invalidate_user_cache()

            return CommandResponse.create_success(
                f"{user.name} 님의 체력이 {healed} 회복되었습니다. (현재: {new_hp})"
            )

        except CommandError:
            raise
        except Exception as e:
            logger.error(f"체력 회복 오류: {e}", exc_info=True)
            raise CommandError("처리 중 오류가 발생했습니다.")

    def _find_user_row(self, user_id: str, data: list) -> dict | None:
        for row in data:
            if str(row.get('아이디', '')).strip() == user_id:
                return row
        return None
```

**스탯 컬럼 주요 패턴:**
- 읽기: `int(float(user_row.get('체력', 0)))` — 빈 셀·소수점 모두 안전 처리
- 행 번호: `user_row['_row_number']` — `get_worksheet_data()`가 자동 삽입, 빈 행 있어도 정확
- 컬럼 번호: `worksheet.row_values(1)` 로 헤더 가져온 뒤 `.index(컬럼명) + 1`
- 쓰기: `self.sheets_manager.update_cell('관리', row_index, col_index_int, value)` — col은 **int**
- 여러 셀 동시: `batch_update_cells('관리', [(row, col1, v1), (row, col2, v2)])`
- `PEEK_HEADER` 컬럼만 `[상태 확인]`에 표시됨. 어떤 컬럼이든 직접 읽고 쓸 수 있음.

---

## 개발 팁

### 1. Import는 단 한 줄로

```python
from utils.imports import *
```

이 한 줄로 다음이 모두 import됩니다:
- `CommandError` - 에러 처리
- `logger` - 로깅
- `bot_cache` - 캐싱
- `add_eul_reul`, `add_eun_neun`, `add_i_ga` - 조사 처리
- `parse_inventory_string` - 인벤토리 파싱
- `invalidate_user_cache` - 캐시 무효화
- `User`, `BaseCommand`, `CommandContext`, `CommandResponse` - 기본 클래스

### 2. 자주 사용하는 패턴

#### A. 시트 데이터 조회
```python
# 명단 데이터 (캐시 사용)
roster_data = self.sheets_manager.get_roster_data(use_cache=True)

# 관리 워크시트 (캐시 없이 - 최신 데이터)
management_data = self.sheets_manager.get_worksheet_data('관리', use_cache=False)

# 상점 데이터
shop_data = self.sheets_manager.get_item_data()
```

#### B. 에러 메시지
```python
# 조사 자동 처리
item_josa = add_eul_reul(item_name)
raise CommandError(f"{item_name}{item_josa} 보유하고 있지 않습니다.")

# 사용자 이름 + 조사
user_josa = add_eun_neun(user.name)
message = f"{user.name}{user_josa} 완료했습니다."
```

#### C. 캐시 관리
```python
# 데이터 변경 후
invalidate_user_cache()  # user_data, all_users_data 무효화

# 아이템 캐시
bot_cache.cache_item_data(data, ttl_seconds=config.CACHE_TTL)
cached = bot_cache.get_item_data()
```

### 3. 검증 체크리스트

새 명령어를 만들 때 확인:
- [ ] `from utils.imports import *` 사용
- [ ] `@register_command` 데코레이터 적용
- [ ] `CommandError`로 사용자 에러 처리
- [ ] `logger.error()`로 예외 로깅
- [ ] 데이터 변경 시 `invalidate_user_cache()` 호출
- [ ] 조사는 `add_eul_reul` 등 사용
- [ ] `CommandResponse.create_success()` / `create_error()` 사용

### 4. 디버깅

```python
# 로그 추가
logger.info(f"처리 시작: {user.id}")
logger.debug(f"인벤토리: {inventory}")
logger.warning(f"주의: {something}")
logger.error(f"오류: {e}", exc_info=True)
```

### 5. 예외 처리 패턴

```python
try:
    # 1. 입력 검증
    if not param:
        raise CommandError("파라미터가 필요합니다.")
    
    # 2. 데이터 조회
    data = self._get_data()
    
    # 3. 비즈니스 로직
    result = self._process(data)
    
    return CommandResponse.create_success("완료", result)
    
except CommandError:
    raise  # 사용자 에러는 그대로 전달
except Exception as e:
    logger.error(f"오류: {e}", exc_info=True)
    raise CommandError("처리 중 오류가 발생했습니다.")
```

## 단계별 가이드

### 1단계: 파일 생성
```bash
# commands/{category}/new_command.py 생성
touch commands/store/new_command.py
```

### 2단계: 템플릿 복사
위의 템플릿을 복사하여 붙여넣기

### 3단계: 커스터마이즈
- `name`, `aliases`, `description` 수정
- 비즈니스 로직 구현

### 4단계: 테스트
```bash
# 봇 실행
python main.py

# 명령어 확인
@봇 [나의명령어]
```

### 5단계: 완료!
명령어가 자동으로 발견되어 등록됩니다.


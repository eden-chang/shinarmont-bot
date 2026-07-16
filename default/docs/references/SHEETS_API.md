# SheetsManager API 레퍼런스

`utils/sheets_operations.py`의 `SheetsManager` 클래스 메서드 정리.
명령어 코드에서 `self.sheets_manager`로 접근합니다.

---

## 핵심 주의사항

### 시트 행 구조
모든 워크시트는 다음 구조를 따릅니다:
- **1행** = 헤더 (컬럼명)
- **2행** = 설명/주석 (자동 무시됨)
- **3행부터** = 실제 데이터

### _row_number — 정확한 행 번호 (필수)
`get_worksheet_data()`가 반환하는 각 레코드에는 `_row_number` 키가 자동으로 포함됩니다.
이 값이 **실제 시트 행 번호**이며, 중간에 빈 행이 있어도 항상 정확합니다.

```python
management_data = self.sheets_manager.get_worksheet_data('관리', use_cache=False)

# ✅ 올바른 행 번호 조회
for row in management_data:
    if str(row.get('아이디', '')).strip() == user_id:
        row_index = row['_row_number']  # 실제 시트 행 번호, 빈 행 있어도 정확
        break

# ❌ 잘못된 방법 (중간에 빈 행이 있으면 틀림)
row_index = management_data.index(user_row) + 3
```

> **왜 +3이 아닌가?**
> `get_worksheet_data()`는 빈 행을 건너뛰고 반환합니다. 따라서 리스트 index와 실제 행 번호가 달라질 수 있습니다. `_row_number`는 빈 행도 포함해서 카운트된 실제 시트 행 번호입니다.

### update_cell의 col 인자
`update_cell`의 3번째 인자는 **컬럼 이름(str)이 아니라 컬럼 번호(int)**입니다.

```python
# ❌ 잘못된 코드
self.sheets_manager.update_cell('관리', row, '체력', value)

# ✅ 올바른 코드
worksheet = self.sheets_manager.get_worksheet('관리')
header_row = worksheet.row_values(1)
col = header_row.index('체력') + 1  # 0-indexed → 1-indexed
self.sheets_manager.update_cell('관리', row, col, value)
```

---

## 데이터 조회 메서드

### `get_worksheet_data(worksheet_name, use_cache=False)`
워크시트 전체 데이터를 딕셔너리 리스트로 반환합니다.
2행(설명)을 자동으로 건너뜁니다.

```python
# 반환: [{'이름': '한참', '아이디': 'longwhile', '체력': '100', ...}, ...]
data = self.sheets_manager.get_worksheet_data('관리', use_cache=False)
```

| 인자 | 타입 | 설명 |
|------|------|------|
| `worksheet_name` | str | 워크시트 이름 |
| `use_cache` | bool | 캐싱 여부. 관리 시트는 반드시 `False` |

---

### `get_roster_data(use_cache=True)`
명단 워크시트 데이터를 반환합니다. `get_worksheet_data('명단')`의 래퍼.

```python
roster = self.sheets_manager.get_roster_data(use_cache=True)
# 반환: [{'이름': '한참', '아이디': 'longwhile'}, ...]
```

---

### `get_item_data()`
상점 워크시트 데이터를 반환합니다. `get_worksheet_data('상점')`의 래퍼.

```python
items = self.sheets_manager.get_item_data()
# 반환: [{'아이템명': '사과', '가격': '1', '스탯': '체력', '수치': '1d10', ...}, ...]
```

---

### `find_user_by_id(user_id)`
명단에서 사용자 ID로 행을 찾습니다. 내부적으로 캐시를 사용합니다.

```python
user_row = self.sheets_manager.find_user_by_id('longwhile')
# 반환: {'이름': '한참', '아이디': 'longwhile'} 또는 None
```

---

### `user_exists(user_id)`
사용자가 명단에 존재하는지 확인합니다.

```python
if not self.sheets_manager.user_exists(context.user_id):
    raise CommandError("명단에 등록되지 않은 사용자입니다.")
```

---

### `get_help_items(sheet_name=None)`
도움말 목록을 반환합니다.

```python
help_items = self.sheets_manager.get_help_items()
# 반환: [{'명령어': '[도움말]', '설명': '도움말을 보여줍니다.'}, ...]
```

---

## 데이터 수정 메서드

### `update_cell(worksheet_name, row, col, value)`
단일 셀을 업데이트합니다.

```python
# col은 int (1-indexed)
self.sheets_manager.update_cell('관리', row_index, col_index, new_value)
```

| 인자 | 타입 | 설명 |
|------|------|------|
| `worksheet_name` | str | 워크시트 이름 |
| `row` | int | 행 번호 (1-indexed, 데이터는 3부터) |
| `col` | int | 열 번호 (1-indexed) |
| `value` | Any | 저장할 값 |

**컬럼 번호 조회 패턴:**
```python
worksheet = self.sheets_manager.get_worksheet('관리')
header_row = worksheet.row_values(1)  # 1행 = 헤더
col_index = header_row.index('소지금') + 1  # 0→1-indexed
```

---

### `batch_update_cells(worksheet_name, updates)`
여러 셀을 **원자적으로** 업데이트합니다. 소지품 + 스탯 동시 변경 등에 사용.

```python
self.sheets_manager.batch_update_cells('관리', [
    (row_index, inventory_col, new_inventory_str),
    (row_index, stat_col,      new_stat_value),
    (row_index, money_col,     new_money),
])
```

| 인자 | 타입 | 설명 |
|------|------|------|
| `worksheet_name` | str | 워크시트 이름 |
| `updates` | `List[Tuple[int, int, Any]]` | `(row, col, value)` 튜플 리스트 |

---

### `append_row(worksheet_name, values)`
워크시트 끝에 새 행을 추가합니다.

```python
self.sheets_manager.append_row('관리', ['새이름', '새아이디', '0', ''])
```

---

## 워크시트 객체 직접 접근

### `get_worksheet(worksheet_name)`
raw gspread 워크시트 객체를 반환합니다. 컬럼 번호 조회 등에 사용.

```python
worksheet = self.sheets_manager.get_worksheet('관리')
header_row = worksheet.row_values(1)   # 헤더 리스트
col_values = worksheet.col_values(2)   # 2열 전체
```

---

## 유틸리티

### `get_current_time()`
KST 기준 현재 시간을 문자열로 반환합니다.

```python
now = SheetsManager.get_current_time()
# 반환: '2026-03-17 14:30:00'
```

---

## store_helpers의 시트 관련 함수

`SheetsManager`와 함께 자주 사용되는 유틸리티 함수들입니다.

### `find_column_by_header(worksheet, keyword)`
워크시트 헤더에서 keyword를 포함하는 컬럼 번호를 찾습니다.

```python
from utils.store_helpers import find_column_by_header
worksheet = self.sheets_manager.get_worksheet('관리')
col = find_column_by_header(worksheet, '소지금')  # 반환: int (1-indexed)
```

### `find_user_row(worksheet, user_id, id_col=None)`
워크시트에서 사용자 ID의 행 번호를 찾습니다.

```python
from utils.store_helpers import find_user_row
worksheet = self.sheets_manager.get_worksheet('관리')
row = find_user_row(worksheet, context.user_id)  # 반환: int (1-indexed) 또는 None
```

### `parse_inventory_string(inventory_str)`
인벤토리 문자열을 딕셔너리로 파싱합니다. JSON/단순텍스트 형식 모두 지원.

```python
from utils.store_helpers import parse_inventory_string
inventory = parse_inventory_string("사과: 3, 반지: 1")
# 결과: {'사과': 3, '반지': 1}
```

### `serialize_inventory(inventory)`
인벤토리 딕셔너리를 시트 저장용 문자열로 변환합니다.
0개 이하 자동 제거, 알파벳/가나다 순 정렬.

```python
from utils.store_helpers import serialize_inventory
inventory_str = serialize_inventory({'사과': 2, '반지': 1})
# 결과: "반지: 1, 사과: 2"
```

---

## 전체 관리 워크시트 업데이트 패턴 (완성 예시)

```python
# 1. 데이터 조회
management_data = self.sheets_manager.get_worksheet_data('관리', use_cache=False)
worksheet = self.sheets_manager.get_worksheet('관리')
header_row = worksheet.row_values(1)

# 2. 사용자 행 찾기
user_row = None
for row in management_data:
    if str(row.get('아이디', '')).strip() == context.user_id:
        user_row = row
        break

if not user_row:
    raise CommandError("사용자를 찾을 수 없습니다.")

# 3. 행/컬럼 번호 계산
row_index = user_row['_row_number']        # ← _row_number 사용 (빈 행 있어도 정확)
money_col = header_row.index('소지금') + 1 # ← +1 (0→1-indexed)
inv_col   = header_row.index('소지품') + 1
stat_col  = header_row.index('체력') + 1

# 4. 현재 값 읽기
current_money = int(float(user_row.get('소지금', 0)))
inventory = parse_inventory_string(user_row.get('소지품', ''))

# 5. 값 계산
new_money = current_money - 10
inventory['사과'] = inventory.get('사과', 0) + 1
inventory_str = serialize_inventory(inventory)

# 6. 원자적 업데이트
self.sheets_manager.batch_update_cells('관리', [
    (row_index, money_col, new_money),
    (row_index, inv_col,   inventory_str),
])

# 7. 캐시 무효화
from utils.store_helpers import invalidate_user_cache
invalidate_user_cache()
```

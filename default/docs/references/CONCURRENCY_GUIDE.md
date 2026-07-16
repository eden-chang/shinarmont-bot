# 동시성 처리 가이드

소지금 차감, 아이템 구매/사용 등 **상태를 변경하는 명령어**는 반드시 동시성 제어가 필요합니다.
같은 사용자가 동시에 두 요청을 보내면 race condition이 발생하여 데이터가 손상될 수 있습니다.

---

## 언제 락이 필요한가

| 상황 | 락 필요 여부 |
|------|-------------|
| 조회만 하는 명령어 (`[소지품]`, `[상태 확인]` 등) | ❌ 불필요 |
| 소지금 변경 | ✅ 필요 |
| 인벤토리 변경 | ✅ 필요 |
| 스탯 변경 | ✅ 필요 |
| 양도 (두 사용자 모두 변경) | ✅ 필요 (특수 패턴) |

---

## 기본 락 패턴

```python
from utils.lock_manager import get_lock_manager

def execute(self, context: CommandContext) -> CommandResponse:
    lock_manager = get_lock_manager()

    with lock_manager.acquire_lock(context.user_id, timeout=10.0) as acquired:
        if not acquired:
            return CommandResponse.create_error(
                "다른 처리가 진행 중입니다. 잠시 후 다시 시도해 주세요."
            )

        try:
            # ← 이 안에서 시트 조회 및 업데이트 수행
            # 락 획득 후 반드시 데이터를 재조회해야 합니다 (stale read 방지)
            management_data = self.sheets_manager.get_worksheet_data('관리', use_cache=False)
            # ... 로직 ...

        except CommandError:
            raise
        except Exception as e:
            logger.error(f"오류: {e}", exc_info=True)
            raise CommandError("처리 중 오류가 발생했습니다.")
```

**핵심 규칙:**
- 락 획득 **이후**에 시트 데이터를 조회할 것 (락 전 조회는 stale)
- timeout은 `10.0`초 사용 (기본값)
- `acquired=False`이면 즉시 에러 반환, 재시도 금지

---

## 두 사용자 동시 락 (양도 명령어 패턴)

두 사용자의 데이터를 동시에 바꿔야 할 때 (A → B 양도 등) 데드락을 방지하려면
**두 ID를 정렬하여 항상 같은 순서로 락을 획득**합니다.

```python
from utils.lock_manager import get_lock_manager

def execute(self, context: CommandContext) -> CommandResponse:
    lock_manager = get_lock_manager()
    sender_id = context.user_id
    receiver_id = target_user_id  # 수령자 ID

    # 데드락 방지: 두 ID를 정렬하여 항상 같은 순서로 락
    first_id, second_id = sorted([sender_id, receiver_id])

    with lock_manager.acquire_lock(first_id, timeout=10.0) as acquired1:
        if not acquired1:
            return CommandResponse.create_error("처리 중입니다. 잠시 후 다시 시도해 주세요.")

        with lock_manager.acquire_lock(second_id, timeout=10.0) as acquired2:
            if not acquired2:
                return CommandResponse.create_error("처리 중입니다. 잠시 후 다시 시도해 주세요.")

            # 두 사용자 데이터 모두 처리
            # ...
```

---

## 원자적 업데이트 (batch_update_cells)

소지품 차감과 스탯 변경을 **동시에** 해야 하는 경우 `batch_update_cells`를 사용합니다.
단일 `update_cell`을 두 번 호출하면 사이에 오류 발생 시 데이터가 반만 변경될 수 있습니다.

```python
# ❌ 위험한 패턴 (두 번 API 호출, 중간 실패 시 데이터 불일치)
self.sheets_manager.update_cell('관리', row, inv_col,  new_inventory_str)
self.sheets_manager.update_cell('관리', row, stat_col, new_stat)

# ✅ 안전한 패턴 (한 번의 API 호출로 원자적 처리)
self.sheets_manager.batch_update_cells('관리', [
    (row_index, inv_col,  new_inventory_str),
    (row_index, stat_col, new_stat),
])
```

**언제 batch가 필요한가:**
- 인벤토리 차감 + 스탯 변경 (아이템 사용)
- 소지금 차감 + 인벤토리 추가 (아이템 구매)
- 두 사용자의 소지금/인벤토리 변경 (양도)

---

## 캐시 무효화

데이터 변경 후 반드시 캐시를 무효화해야 합니다. 그렇지 않으면 이후 조회 시 이전 데이터가 반환됩니다.

```python
from utils.store_helpers import invalidate_user_cache

# 모든 데이터 변경 후
invalidate_user_cache()  # user_data 및 all_users_data 캐시 무효화
```

**아이템 데이터 캐시 무효화 (상점 변경 시):**
```python
from utils.cache_manager import bot_cache
bot_cache.invalidate_item_data()
```

---

## 완전한 패턴 예시

```python
from utils.lock_manager import get_lock_manager
from utils.store_helpers import parse_inventory_string, serialize_inventory, invalidate_user_cache

def execute(self, context: CommandContext) -> CommandResponse:
    lock_manager = get_lock_manager()

    with lock_manager.acquire_lock(context.user_id, timeout=10.0) as acquired:
        if not acquired:
            return CommandResponse.create_error(
                "다른 처리가 진행 중입니다. 잠시 후 다시 시도해 주세요."
            )

        try:
            # 락 획득 후 최신 데이터 조회
            management_data = self.sheets_manager.get_worksheet_data('관리', use_cache=False)
            worksheet = self.sheets_manager.get_worksheet('관리')
            header_row = worksheet.row_values(1)

            # 사용자 찾기
            user_row = None
            for row in management_data:
                if str(row.get('아이디', '')).strip() == context.user_id:
                    user_row = row
                    break

            if not user_row:
                raise CommandError("사용자를 찾을 수 없습니다.")

            # 행/컬럼 번호
            row_index = user_row['_row_number']  # _row_number: 빈 행 있어도 정확한 실제 시트 행 번호
            money_col = header_row.index('소지금') + 1
            inv_col   = header_row.index('소지품') + 1

            # 현재값
            current_money = int(float(user_row.get('소지금', 0)))
            inventory = parse_inventory_string(user_row.get('소지품', ''))

            # 검증
            if current_money < 10:
                raise CommandError("소지금이 부족합니다.")

            # 변경
            new_money = current_money - 10
            inventory['사과'] = inventory.get('사과', 0) + 1
            inv_str = serialize_inventory(inventory)

            # 원자적 업데이트
            self.sheets_manager.batch_update_cells('관리', [
                (row_index, money_col, new_money),
                (row_index, inv_col,   inv_str),
            ])

            # 캐시 무효화
            invalidate_user_cache()

            return CommandResponse.create_success("처리 완료!")

        except CommandError:
            raise
        except Exception as e:
            logger.error(f"오류: {e}", exc_info=True)
            raise CommandError("처리 중 오류가 발생했습니다.")
```

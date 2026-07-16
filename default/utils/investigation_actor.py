"""
utils/investigation_actor.py — 조사 캐릭터(관리 시트) 조회 및 보상 적용

조사 시트가 아니라 **기본 스프레드시트의 `관리` 워크시트**를 다룬다.
계정 핸들 ↔ 캐릭터명 매핑, 직군, 건강/이성, 소지금, 소지품이 모두 여기 있다.

관리 시트 컬럼 (docs/스프레드시트_구성.md §3):
    이름 / 아이디 / 소지금 / 소지품 / 건강 / 이성 / 직군 / 오늘추적 / 오늘조사

보상 적용 순서 (가이드 §3.3):
    주사위를 **먼저 전부 굴린 뒤** 재화 잔액을 검사한다.
    잔액이 부족하면 아무것도 적용하지 않는다(부분 적용 금지).
    확정된 수치만 시트에 쓰고 응답에도 그 확정값을 출력한다(수식이 아니라).

동시성: 호출측(명령어)이 캐릭터 단위 락 안에서 부른다는 전제다 (가이드 §7).
"""

import os
import sys
from typing import Any, Dict, List, Optional, Tuple

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

try:
    from utils.logging_config import logger
except ImportError:  # pragma: no cover
    import logging
    logger = logging.getLogger('utils.investigation_actor')

try:
    from config.settings import config
except ImportError:  # pragma: no cover
    config = None

from utils.dice_parser import evaluate_amount
from utils.investigation_sheet import (
    COL_ITEM, COL_ITEM_COUNT, COL_MONEY, COL_STAT, COL_STAT_VALUE,
    config_int, display_name, normalize_name,
)
from utils.store_helpers import (
    invalidate_user_cache, parse_inventory_string, serialize_inventory,
)

MGMT_SHEET = '관리'
COL_NAME = '이름'
COL_ID = '아이디'
COL_MONEY_BALANCE = '소지금'
COL_INVENTORY = '소지품'
COL_ROLE = '직군'
COL_HEALTH = '건강'
COL_SANITY = '이성'

SHOP_SHEET = '상점'
SHOP_ITEM_COLUMN = '아이템'


class Actor:
    """관리 시트의 캐릭터 한 명."""

    def __init__(self, row: Dict[str, Any]):
        self.row = row
        self.name: str = display_name(row.get(COL_NAME))
        self.user_id: str = display_name(row.get(COL_ID))
        self.role: str = display_name(row.get(COL_ROLE))
        self.money: int = _to_int(row.get(COL_MONEY_BALANCE), 0)
        self.health: int = _to_int(row.get(COL_HEALTH), 0)
        self.sanity: int = _to_int(row.get(COL_SANITY), 0)
        self.row_number: int = row.get('_row_number')
        # get_worksheet_data가 dict(zip(header, row))로 만들므로 키 순서 = 시트 열 순서.
        # 이미 읽은 행에서 산출해 별도 헤더 조회(API 호출)를 없앤다.
        self.header: List[str] = [k for k in row.keys() if k != '_row_number']

    def column_index(self, column: str) -> Optional[int]:
        """1-indexed 열 번호. 컬럼이 없으면 None."""
        try:
            return self.header.index(column) + 1
        except ValueError:
            return None

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Actor {self.name}({self.user_id}) 직군={self.role}>"


def _to_int(raw: Any, default: int = 0) -> int:
    try:
        text = str(raw).strip()
        if not text:
            return default
        return int(float(text))
    except (ValueError, TypeError):
        return default


def load_actor(sheets_manager, user_id: str) -> Optional[Actor]:
    """계정 핸들로 관리 시트의 캐릭터를 찾는다. 없으면 None.

    관리 시트는 가변 시트이므로 항상 미캐시로 읽는다.
    """
    if sheets_manager is None:
        logger.warning("[조사] 기본 시트 매니저 없음 - 캐릭터 조회 불가")
        return None
    try:
        rows = sheets_manager.get_worksheet_data(MGMT_SHEET, use_cache=False) or []
    except Exception as e:
        logger.error(f"[조사] 관리 시트 조회 실패: {e}", exc_info=True)
        return None

    target = str(user_id or '').strip()
    found = None
    for row in rows:
        if str(row.get(COL_ID, '')).strip() == target:
            found = Actor(row)
            break

    if found is None:
        logger.info(f"[조사] 관리 시트에 사용자 없음: {user_id}")
        return None

    _warn_if_name_collides(rows, found)
    return found


def _warn_if_name_collides(rows: List[Dict[str, Any]], actor: Actor) -> None:
    """캐릭터명이 중복되면 관리자에게 경고한다 (프로세스당 이름별 1회).

    `로그`·`예외` 시트는 계정 아이디가 아니라 **캐릭터명**을 키로 쓴다(가이드 §2.3·§2.4).
    따라서 동명이인이 생기면 일일 조사 횟수·현재 위치·예외 권한이 **뒤섞인다**.
    운영상 캐릭터명은 유일하다는 전제이므로, 전제가 깨지면 조용히 오동작하지 말고 알린다.

    이미 읽어 온 행에서 검사하므로 추가 API 호출이 없다.
    """
    target = normalize_name(actor.name)
    if not target:
        return
    same = [r for r in rows if normalize_name(r.get(COL_NAME)) == target]
    if len(same) <= 1:
        return

    ids = ', '.join(sorted(str(r.get(COL_ID, '')).strip() for r in same))
    try:
        from utils.investigation_notify import notify_admin_once
        notify_admin_once(
            f"duplicate-name:{target}",
            f"⚠️ 관리 시트에 캐릭터명 '{actor.name}'이(가) {len(same)}명 있습니다 (계정: {ids}).\n"
            f"조사 로그·예외 시트는 캐릭터명이 키라서, 이 사람들의 일일 조사 횟수·현재 위치·"
            f"예외 권한이 서로 섞입니다. 이름을 유일하게 고쳐 주세요.",
        )
    except Exception as e:  # 알림 실패가 명령 처리를 막지 않는다
        logger.error(f"[조사] 동명이인 경고 발송 실패: {e}")


# --------------------------------------------------------------------------- #
# 주사위
# --------------------------------------------------------------------------- #
def roll(expression: Any, field: str, warnings: List[str]) -> Optional[int]:
    """수식을 굴려 확정값을 반환 (가이드 §4).

    - 빈 값은 0 (해당 보상 없음)
    - 파싱 불가능한 값은 None을 반환하고 경고를 남긴다.
      호출측은 해당 보상만 건너뛰고 조사 자체는 진행한다.
    """
    text = str(expression or '').strip()
    if not text:
        return 0
    try:
        value, detail = evaluate_amount(text)
    except ValueError as e:
        warnings.append(f"{field} 수식 파싱 실패: '{text}' ({e})")
        return None
    if detail:
        logger.debug(f"[조사] {field} 굴림: {text} → {detail}")
    return value


# --------------------------------------------------------------------------- #
# 보상 계산 (시트 쓰기 없음 — 순수 계산)
# --------------------------------------------------------------------------- #
class Reward:
    """굴림이 끝난 확정 보상."""

    def __init__(self):
        self.item_name: str = ''
        self.item_count: int = 0
        self.stat_name: str = ''
        self.stat_delta: int = 0
        self.money_delta: int = 0
        self.warnings: List[str] = []

    @property
    def has_item(self) -> bool:
        return bool(self.item_name) and self.item_count > 0

    @property
    def has_stat(self) -> bool:
        return bool(self.stat_name) and self.stat_delta != 0

    @property
    def has_money(self) -> bool:
        return self.money_delta != 0


def roll_reward(point_row: Dict[str, Any]) -> Reward:
    """포인트 행의 아이템·스탯·재화 수식을 전부 굴려 확정값으로 만든다.

    시트에 쓰지 않는다. 잔액 검사 전에 굴림을 끝내야 응답과 시트가 같은 값을 갖는다.
    """
    reward = Reward()

    # --- 아이템 ---
    item_name = display_name(point_row.get(COL_ITEM))
    if item_name:
        count_expr = str(point_row.get(COL_ITEM_COUNT) or '').strip()
        if not count_expr:
            # 획득 아이템이 있는데 개수가 빈칸이면 1 (§2.2)
            count = 1
        else:
            count = roll(count_expr, '개수', reward.warnings)
        if count is None:
            pass  # 파싱 실패 → 아이템 지급만 건너뜀
        elif count < 0:
            # 개수에 음수는 지원하지 않는다 (§2.2)
            reward.warnings.append(
                f"'{item_name}' 개수가 음수({count_expr} → {count})입니다. "
                f"아이템 지급을 건너뜁니다."
            )
        elif count > 0:
            reward.item_name = item_name
            reward.item_count = count

    # --- 스탯 ---
    stat_name = display_name(point_row.get(COL_STAT))
    if stat_name:
        delta = roll(point_row.get(COL_STAT_VALUE), f'{stat_name} 수치', reward.warnings)
        if delta is not None:
            reward.stat_name = stat_name
            reward.stat_delta = delta

    # --- 재화 ---
    money = roll(point_row.get(COL_MONEY), '재화 증감', reward.warnings)
    if money is not None:
        reward.money_delta = money

    return reward


# --------------------------------------------------------------------------- #
# 보상 적용 (시트 쓰기)
# --------------------------------------------------------------------------- #
def _clamp_stat(value: int) -> int:
    lo = config_int('INVESTIGATION_STAT_MIN', 0)
    hi = config_int('INVESTIGATION_STAT_MAX', 100)
    return max(lo, min(hi, value))


class RewardPlan:
    """시트에 쓰기 직전의 확정 계획. 아직 아무것도 반영하지 않았다."""

    def __init__(self):
        self.updates: List[Tuple[int, int, Any]] = []
        self.display: List[str] = []   # 러너 응답용 "➭ ..." 줄
        self.summary: List[str] = []   # 로그 요약용 "이성 -2"
        self.warnings: List[str] = []

    @property
    def has_changes(self) -> bool:
        return bool(self.summary)


def plan_reward(
    actor: Actor,
    reward: Reward,
    shop_items: Optional[set] = None,
    extra_updates: Optional[List[Tuple[int, int, Any]]] = None,
) -> RewardPlan:
    """확정 보상을 '무엇을 어디에 쓸지'로 변환한다. **시트를 건드리지 않는다.**

    쓰기와 분리한 이유: 로그 시트를 먼저 기록해야 하는데(일일 제한의 기준),
    로그에 남길 변동 요약은 실제 반영값을 알아야 만들 수 있다.
    계획을 먼저 세우면 로그 → 보상 순서로 쓸 수 있고, 로그가 실패하면
    아무것도 반영되지 않은 상태로 안전하게 중단할 수 있다.

    표시·요약에는 **실제로 반영되는 값**을 쓴다(클램프 반영 후).
    가이드 §3.3 "➭ 줄은 실제 발생한 변동만 출력한다."를 그대로 따른다.
    굴림값을 그대로 보여 주면 건강 95에 +30을 굴렸을 때 "건강 +30"이라 하고
    실제로는 +5만 반영되어, 러너는 오해하고 GM은 로그로 정산할 수 없다.

    잔액 검사는 호출측이 미리 끝냈다고 가정한다(can_afford).

    Args:
        extra_updates: 같은 배치로 함께 쓸 (행, 열, 값) 목록.
            `오늘조사` 카운터 미러링처럼 관리 시트의 다른 셀을 갱신할 때 쓴다.
            사용자 락은 재진입이 불가능하므로(daily_counter는 자체 락을 잡는다)
            락 안에서의 부수 갱신은 반드시 이 배치에 실어야 한다.
    """
    plan = RewardPlan()
    plan.updates.extend(extra_updates or [])
    plan.warnings.extend(reward.warnings)
    currency = getattr(config, 'CURRENCY', '달러') or '달러'

    # ➭ 줄 순서: 스탯 → 아이템 → 재화 (2026-07-16 확정).
    # 러너가 매번 같은 자리에서 같은 정보를 읽게 하려는 것이지 기능상 이유는 없다.
    # --- 스탯 (클램프 후 실제 변동만 표시·기록) ---
    if reward.has_stat:
        col = actor.column_index(reward.stat_name)
        if col is None:
            logger.warning(
                f"[조사] 관리 시트에 '{reward.stat_name}' 컬럼 없음 - 스탯 변동 생략")
            plan.warnings.append(
                f"관리 시트에 '{reward.stat_name}' 컬럼이 없어 스탯 변동을 건너뛰었습니다.")
        else:
            current = _to_int(actor.row.get(reward.stat_name), 0)
            new_value = _clamp_stat(current + reward.stat_delta)
            actual = new_value - current
            if actual != reward.stat_delta:
                logger.info(
                    f"[조사] 스탯 클램프: {actor.name} {reward.stat_name} "
                    f"{current}{reward.stat_delta:+d} → {new_value} (실제 {actual:+d})"
                )
            if actual != 0:
                plan.updates.append((actor.row_number, col, new_value))
                plan.display.append(f"➭ {reward.stat_name} {actual:+d}")
                plan.summary.append(f"{reward.stat_name} {actual:+d}")
            # actual == 0 이면 이미 상/하한이라 변동이 없다 → 쓰지도, 표시하지도 않는다

    # --- 아이템 → 소지품 ---
    if reward.has_item:
        col = actor.column_index(COL_INVENTORY)
        if col is None:
            logger.warning("[조사] 관리 시트에 '소지품' 컬럼 없음 - 아이템 지급 생략")
            plan.warnings.append("관리 시트에 '소지품' 컬럼이 없어 아이템 지급을 건너뛰었습니다.")
        else:
            if shop_items is not None and normalize_name(reward.item_name) not in shop_items:
                # 상점에 없는 아이템도 지급하되 관리자에게 알린다.
                # 건너뛰면 러너가 보상을 조용히 잃는다. 지급해 두면 GM이 사후 정정할 수 있다.
                plan.warnings.append(
                    f"'{reward.item_name}'이(가) 상점 워크시트에 없습니다. "
                    f"지급은 했으나 시트 확인이 필요합니다."
                )
            inventory = parse_inventory_string(actor.row.get(COL_INVENTORY, '')) or {}
            inventory[reward.item_name] = inventory.get(reward.item_name, 0) + reward.item_count
            plan.updates.append((actor.row_number, col, serialize_inventory(inventory)))
            plan.display.append(f"➭ '{reward.item_name}' {reward.item_count}개 획득")
            plan.summary.append(f"{reward.item_name} {reward.item_count} 획득")

    # --- 재화 (클램프 없음 → 굴림값 = 실제값) ---
    if reward.has_money:
        col = actor.column_index(COL_MONEY_BALANCE)
        if col is None:
            logger.warning("[조사] 관리 시트에 '소지금' 컬럼 없음 - 재화 변동 생략")
            plan.warnings.append("관리 시트에 '소지금' 컬럼이 없어 재화 변동을 건너뛰었습니다.")
        else:
            new_money = actor.money + reward.money_delta
            plan.updates.append((actor.row_number, col, new_money))
            if reward.money_delta > 0:
                plan.display.append(f"➭ {reward.money_delta}{currency} 획득")
            else:
                plan.display.append(f"➭ {abs(reward.money_delta)}{currency} 지불")
            plan.summary.append(f"재화 {reward.money_delta:+d}")

    return plan


def commit_reward(sheets_manager, actor: Actor, plan: RewardPlan) -> None:
    """계획된 변동을 관리 시트에 한 번의 배치로 반영한다.

    Raises:
        RewardWriteError: 쓰기 실패. 일부만 반영되었을 수 있어 수동 정산이 필요하다 (§7).
    """
    if not plan.updates:
        return
    ok = sheets_manager.batch_update_cells(MGMT_SHEET, plan.updates)
    if not ok:
        raise RewardWriteError(
            f"관리 시트 반영 실패 (캐릭터={actor.name}, 변동={plan.summary})")
    invalidate_user_cache()


def can_afford(actor: Actor, reward: Reward) -> bool:
    """재화 증감이 음수일 때 잔액이 충분한지 (§3.3-6)."""
    if reward.money_delta >= 0:
        return True
    return actor.money + reward.money_delta >= 0


def load_shop_items(sheets_manager) -> Optional[set]:
    """상점 워크시트의 아이템명 집합(정규화). 실패 시 None(검증 생략)."""
    if sheets_manager is None:
        return None
    try:
        rows = sheets_manager.get_worksheet_data(SHOP_SHEET, use_cache=True) or []
    except Exception as e:
        logger.warning(f"[조사] 상점 시트 조회 실패 - 아이템 검증 생략: {e}")
        return None
    names = set()
    for row in rows:
        for key in (SHOP_ITEM_COLUMN, '아이템명', '이름'):
            value = normalize_name(row.get(key))
            if value:
                names.add(value)
                break
    return names or None


class RewardWriteError(Exception):
    """보상 시트 쓰기 실패 — 일부 적용되었을 수 있어 관리자 알림이 필요하다 (§7)."""

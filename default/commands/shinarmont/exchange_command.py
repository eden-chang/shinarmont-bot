"""
[교류/농도] @상대 명령어 (시너몬트 스토리, @STORY)

DM 전용. 상대를 멘션(@)하고 농도(1~5)를 지정하면 시전자와 상대 양쪽의
이성이 `농도 × EXCHANGE_SANITY_PER_LEVEL`(상한 100)만큼 회복된다.

표준 흐름(코딩_계획 §6):
  DM게이트 → 파싱(농도 1~5) → 대상(멘션) 해석 → 정렬 이중락 →
  락 내부 재조회 → batch_update(양쪽 이성) → 캐시 무효화 →
  행동로그 append(교류) → 상대 DM 알림 → 양쪽 이성 문구 검사.

- 일일 제한 없음(이성 상한 100으로만 자연 제어). (구현계획 §5.8 / §9)
"""

from utils.imports import *

from utils.dm_guard import dm_only
from utils.lock_manager import get_lock_manager
from utils.target_helpers import resolve_target, get_target_error, relay_dm
from utils import action_log
from utils import stat_gate


MGMT_SHEET = '관리'
COL_NAME = '이름'
COL_ID = '아이디'
COL_SANITY = '이성'
SANITY_MAX = 100


def _normalize_acct(acct) -> str:
    """아이디/acct 매칭용 정규화 (선행 @ 제거 + 소문자 + 공백제거)."""
    if not acct:
        return ''
    return str(acct).strip().lstrip('@').strip().lower()


def _to_int(raw, default=0) -> int:
    """시트 셀 값을 정수로 변환. 실패 시 default."""
    try:
        return int(float(raw))
    except (ValueError, TypeError):
        return default


def _row_keys(row):
    """행 딕셔너리에서 헤더 순서 키 목록(‘_row_number’ 제외)."""
    return [k for k in row.keys() if k != '_row_number']


def _find_mgmt_row(rows, acct):
    """관리 시트에서 아이디(acct)로 행 조회 (대소문자/@ 무시)."""
    target = _normalize_acct(acct)
    if not target:
        return None
    for row in rows:
        if _normalize_acct(row.get(COL_ID, '')) == target:
            return row
    return None


def _wa_gwa(name: str) -> str:
    """이름 뒤 와/과 조사."""
    return '과' if has_final_consonant(get_last_char(name)) else '와'


@register_command(
    name="교류",
    aliases=["exchange"],
    description="상대와 교류하여 서로의 이성을 회복합니다. (DM 전용)",
    category="스토리",
    examples=["[교류/3] @캐릭터명"],
    requires_sheets=True,
    requires_api=False,
)
class ExchangeCommand(BaseCommand):
    """
    [교류/농도] @상대 — 양쪽 이성 회복(이중 락, batch, 상한 100).
    """

    def __init__(self, sheets_manager=None, api=None, **kwargs):
        super().__init__(sheets_manager, api, **kwargs)
        self.system_sheets_manager = kwargs.get('system_sheets_manager')

    @dm_only
    def execute(self, context: CommandContext) -> CommandResponse:
        # 1) 농도 파싱 (1~5 자연수)
        keywords = context.keywords or []
        if len(keywords) < 2:
            return CommandResponse.create_error(
                "사용법: [교류/농도] @상대\n농도는 1~5 사이의 자연수입니다. 예: [교류/3] @캐릭터명"
            )

        raw = str(keywords[1]).strip()
        # isascii 병행: '²' 등 유니크한 숫자문자는 isdigit=True지만 int() 불가 → 크래시 방지
        if not (raw.isascii() and raw.isdigit()):
            return CommandResponse.create_error("농도는 1~5 사이의 자연수여야 합니다. 예: [교류/3] @캐릭터명")
        level = int(raw)
        if not (1 <= level <= 5):
            return CommandResponse.create_error("농도는 1~5 사이의 자연수여야 합니다.")

        # 2) 대상(멘션) 해석
        target = resolve_target(context, self.sheets_manager, keyword_index=None)
        if target is None:
            return CommandResponse.create_error(
                get_target_error(context, "대상을 지정해 주세요. 교류할 상대를 멘션(@)해야 합니다.")
            )
        target_id = str(target.get('아이디', '')).strip()
        target_name = str(target.get('이름', '')).strip()
        if not target_id:
            return CommandResponse.create_error("대상의 아이디를 확인할 수 없습니다.")

        per_level = getattr(config, 'EXCHANGE_SANITY_PER_LEVEL', 6)
        gain = level * per_level

        caster_id = str(context.user_id or '').strip()

        # 3) 정렬 이중락 (데드락 방지) → 락 내부 재조회 → batch_update
        lock_manager = get_lock_manager()
        lock_ids = sorted([caster_id, target_id])

        with lock_manager.acquire_lock(lock_ids[0], timeout=10.0) as acquired1:
            if not acquired1:
                return CommandResponse.create_error("다른 처리가 진행 중입니다. 잠시 후 다시 시도해 주세요.")
            with lock_manager.acquire_lock(lock_ids[1], timeout=10.0) as acquired2:
                if not acquired2:
                    return CommandResponse.create_error("다른 처리가 진행 중입니다. 잠시 후 다시 시도해 주세요.")

                try:
                    mgmt_rows = self.sheets_manager.get_worksheet_data(MGMT_SHEET, use_cache=False)
                except Exception as e:
                    logger.error(f"[교류] 관리 시트 조회 실패: {e}")
                    return CommandResponse.create_error("정보를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.")

                caster_row = _find_mgmt_row(mgmt_rows, caster_id)
                target_row = _find_mgmt_row(mgmt_rows, target_id)
                if caster_row is None:
                    return CommandResponse.create_error("교류를 사용할 캐릭터 정보를 찾을 수 없습니다. 명단/관리에 등록되어 있는지 확인해 주세요.")
                if target_row is None:
                    return CommandResponse.create_error(f"'{target_name or target_id}' 캐릭터의 정보를 찾을 수 없습니다.")

                # 이성 컬럼 확인 (행별 헤더 기준)
                caster_keys = _row_keys(caster_row)
                target_keys = _row_keys(target_row)
                if COL_SANITY not in caster_keys or COL_SANITY not in target_keys:
                    return CommandResponse.create_error("관리 시트에 '이성' 컬럼이 없습니다. 관리자에게 문의해 주세요.")

                caster_row_no = caster_row.get('_row_number')
                target_row_no = target_row.get('_row_number')
                if caster_row_no is None or target_row_no is None:
                    return CommandResponse.create_error("행 정보를 확인할 수 없습니다. 잠시 후 다시 시도해 주세요.")

                caster_cur = _to_int(caster_row.get(COL_SANITY), default=0)
                target_cur = _to_int(target_row.get(COL_SANITY), default=0)
                caster_new = min(SANITY_MAX, caster_cur + gain)
                target_new = min(SANITY_MAX, target_cur + gain)

                updates = [
                    (caster_row_no, caster_keys.index(COL_SANITY) + 1, caster_new),
                    (target_row_no, target_keys.index(COL_SANITY) + 1, target_new),
                ]
                ok = self.sheets_manager.batch_update_cells(MGMT_SHEET, updates)
                if not ok:
                    logger.error("[교류] batch_update_cells 실패")
                    return CommandResponse.create_error("교류 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.")

                # 락 내부에서 확보한 이름 (관리 시트 우선, 없으면 컨텍스트)
                caster_name = str(caster_row.get(COL_NAME, '')).strip() or str(context.user_name or caster_id).strip()

        # 4) 캐시 무효화
        try:
            invalidate_user_cache()
        except Exception as e:
            logger.warning(f"[교류] 캐시 무효화 실패: {e}")

        caster_delta = caster_new - caster_cur
        target_delta = target_new - target_cur

        # 5) 행동로그 append (종류=교류)
        try:
            action_log.append(
                self.system_sheets_manager,
                kind=action_log.KIND_EXCHANGE,
                actor=caster_name,
                target=target_name,
                # 요약은 소문 문구가 될 수 있다 → 농도·수치 같은 시스템 값을 넣지 않는다.
                # `_wa_gwa`는 조사만 돌려준다(add_i_ga와 달리 이름이 안 붙는다).
                summary=f"{add_i_ga(caster_name)} {target_name}{_wa_gwa(target_name)} 교류했다",
            )
        except Exception as e:
            logger.warning(f"[교류] 행동로그 기록 실패: {e}")

        # 6) 상대에게 DM 알림
        # add_i_ga 는 단어+조사를 돌려준다 → 이름을 또 붙이면 '데보라데보라가'가 된다.
        relay_dm(
            target_id,
            f"{add_i_ga(caster_name)} 당신과 교류했습니다. 이성이 {target_delta} 회복되었습니다.",
        )

        # 7) 양쪽 이성 문구 검사 (상승이라 보통 무발생이나 호출은 안전)
        for uid in (caster_id, target_id):
            try:
                stat_gate.apply_sanity_messages(
                    self.sheets_manager, self.system_sheets_manager, self.api, uid
                )
            except Exception as e:
                logger.warning(f"[교류] 이성 문구 검사 실패({uid}): {e}")

        # 상대 이름·농도를 되읊지 않는다(2026-07-16). 교류는 사적인 일이고,
        # 본인 이성 변동만 알면 충분하다. 상대에게는 위에서 DM으로 따로 알렸다.
        # 상한(100)에 걸려 실제 회복이 0이면 ➭ 줄을 빼서 헛된 기대를 주지 않는다.
        message = "접수 완료"
        if caster_delta:
            message += f"\n➭ 이성 +{caster_delta}"
        return CommandResponse.create_success(
            message,
            data={
                'type': 'exchange',
                'caster_id': caster_id,
                'caster_name': caster_name,
                'target_id': target_id,
                'target_name': target_name,
                'level': level,
                'gain': gain,
                'caster_delta': caster_delta,
                'target_delta': target_delta,
            },
        )

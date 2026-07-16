"""
[추적/대상] 명령어 (시너몬트 · 마을)

DM 전용. 하루 1회(`관리.오늘추적`) 대상 캐릭터를 추적해, 그 대상에게 배정된
단서 중 **이 추적자가 아직 받지 않은 것 하나**를 무작위로 획득한다.

- 단서 풀 = 시스템 `추적` 시트: `이름`(=대상) + `추적1`~`추적5`(단서 텍스트, 빈 칸 허용).
- 수령 이력 = 시스템 `추적기록` 시트: `이름`(=추적자) + `추적기록`(콤마 목록, 토큰 `{대상}{번호}`).
- 추적자 이성이 낮으면 단서 텍스트에 왜곡 필터(`utils.distortion.apply`)를 적용.
- 남은 단서가 없으면 "더 알아낼 것이 없다." 안내(추적 시도는 소진).

docs/추적_시트_형식.md / docs/코딩_계획.md §6 (그룹 B) 참조.
"""

import os
import re
import sys
import random
from typing import Any, Dict, List, Optional, Set, Tuple

# 경로 설정 (VM 환경 대응)
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))

from utils.imports import *  # noqa: F401,F403  (config, logger, CommandError, BaseCommand 등)

from utils.dm_guard import dm_only
from utils.lock_manager import get_lock_manager
from utils import daily_counter
from utils import distortion
from utils import action_log


# 시스템 시트 이름
TRACK_SHEET = '추적'          # 단서 풀 (GM이 채움)
TRACK_RECORD_SHEET = '추적기록'  # 수령 이력 (봇 자동)

# 추적 시트 단서 컬럼 (추적1~추적5)
CLUE_COLUMNS = [f'추적{n}' for n in range(1, 6)]

# 추적기록 시트 헤더
NAME_COL_HEADER = '이름'
RECORD_COL_HEADER = '추적기록'

# `관리` 시트의 추적 카운터 컬럼(실측 헤더는 '추적'). config로 조정.
# 이 이름이 시트와 다르면 [추적]이 항상 실패한다.
TRACK_DAILY_COLUMN = (getattr(config, 'TRACK_COUNTER_COLUMN', '추적') or '추적').strip()
TRACK_DAILY_LIMIT = 1

# 설명 행 마커 (2행: 'ㅇ') — 데이터에서 제외
DESCRIPTION_MARKER = 'ㅇ'

# 트레일링 숫자(단서 번호) 추출
_TRAILING_NUM_RE = re.compile(r'(\d+)\s*$')


def _normalize_name(name: Any) -> str:
    """이름 매칭용 정규화 (공백 제거 + 소문자)."""
    if name is None:
        return ''
    return re.sub(r'\s+', '', str(name)).strip().lower()


def _normalize_acct(acct: Any) -> str:
    """아이디 매칭용 정규화 (@ 제거 + 소문자 + 공백 제거)."""
    if acct is None:
        return ''
    return str(acct).strip().lstrip('@').strip().lower()


def _split_token(token: str) -> Optional[Tuple[str, int]]:
    """`{대상}{번호}` 토큰을 (이름, 번호)로 분리.

    이름은 숫자로 끝나지 않으므로(추적_시트_형식 §4), 뒤따르는 숫자를 번호로,
    앞부분을 이름으로 본다. 분리 실패 시 None.
    """
    if not token:
        return None
    token = str(token).strip()
    m = _TRAILING_NUM_RE.search(token)
    if not m:
        return None
    name = token[:m.start()].strip()
    if not name:
        return None
    try:
        num = int(m.group(1))
    except (ValueError, TypeError):
        return None
    return name, num


@register_command(
    name="추적",
    aliases=["미행"],
    description="대상 캐릭터를 하루 1회 추적해 단서를 얻습니다. (DM 전용)",
    category="마을",
    examples=["[추적/데이나]", "[추적/데이비드]"],
    requires_sheets=True,
    requires_api=False,
)
class TrackCommand(BaseCommand):
    """[추적/대상] — 대상의 단서를 중복 없이 무작위로 하나 획득."""

    @staticmethod
    def get_supported_keywords() -> List[str]:
        return ['추적', '미행']

    def __init__(self, sheets_manager=None, api=None, **kwargs):
        super().__init__(sheets_manager, api, **kwargs)
        self.system_sheets_manager = kwargs.get('system_sheets_manager')
        logger.debug(
            f"[추적] TrackCommand 초기화: system_sheets_manager="
            f"{'있음' if self.system_sheets_manager else '없음'}"
        )

    @dm_only
    def execute(self, context: CommandContext) -> CommandResponse:
        # 1) 파싱
        target_input = self._parse_target(context.keywords)
        if not target_input:
            return CommandResponse.create_error(
                "추적할 대상을 입력해 주세요. 예) [추적/데이나]"
            )

        if self.system_sheets_manager is None:
            return CommandResponse.create_error(
                "추적 기능이 설정되지 않았습니다. 관리자에게 문의해 주세요."
            )

        # 2) 검증: 추적 시트에서 대상 존재 확인 (소진 전 read — 미존재 시 일일제한 소비 없음)
        try:
            track_rows = self.system_sheets_manager.get_worksheet_data(
                TRACK_SHEET, use_cache=True   # 원본 단서 시트는 GM 정적 콘텐츠 → 캐시
            )
        except Exception as e:
            logger.error(f"[추적] '{TRACK_SHEET}' 시트 조회 실패: {e}", exc_info=True)
            return CommandResponse.create_error("추적 데이터를 불러올 수 없습니다.")

        known_names = self._collect_known_names(track_rows)
        target_row = self._find_target_row(track_rows, target_input)
        if target_row is None:
            return CommandResponse.create_error(
                f"'{target_input}'은(는) 추적할 수 있는 대상이 아닙니다."
            )
        target_name = str(target_row.get(NAME_COL_HEADER, target_input)).strip()

        # 추적자 정보 (이름 · 이성)
        tracker_name, tracker_sanity = self._resolve_tracker(context)

        # 자기 자신은 추적 대상이 될 수 없음 (일일 제한 소비 전 차단)
        if _normalize_name(tracker_name) == _normalize_name(target_name):
            return CommandResponse.create_error("자기 자신은 추적할 수 없습니다.")

        # 3) 일일 제한 (관리.오늘추적, 1/일) — 소비
        if not daily_counter.check_and_inc_sheet(
            self.sheets_manager, context.user_id, TRACK_DAILY_COLUMN, TRACK_DAILY_LIMIT
        ):
            return CommandResponse.create_error(
                "오늘은 더 이상 추적할 수 없습니다. (하루 1회)"
            )

        # 4) 락 내부에서 재조회 → diff 계산 → append (원자적)
        # 소비했으므로, 예외/락실패로 추적이 성립하지 못하면 카운터를 롤백한다(락 밖에서).
        lock_manager = get_lock_manager()
        need_rollback = False
        with lock_manager.acquire_lock(context.user_id, timeout=10.0) as acquired:
            if not acquired:
                need_rollback = True
            else:
                try:
                    return self._run_track(
                        context.user_id, tracker_name, tracker_sanity,
                        target_name, known_names,
                    )
                except Exception as e:
                    logger.error(f"[추적] 처리 중 오류: {e}", exc_info=True)
                    need_rollback = True

        # 여기 도달 = 락 실패 또는 예외 → 소비한 일일 제한 롤백(락 비재진입이라 락 밖에서 수행)
        if need_rollback:
            try:
                daily_counter.dec_sheet(self.sheets_manager, context.user_id, TRACK_DAILY_COLUMN)
            except Exception as rb_err:
                logger.error(f"[추적] 일일 제한 롤백 실패: {rb_err}")
        if not acquired:
            return CommandResponse.create_error(
                "다른 처리가 진행 중입니다. 잠시 후 다시 시도해 주세요."
            )
        return CommandResponse.create_error("추적 처리 중 오류가 발생했습니다.")

    # ------------------------------------------------------------------
    # 파싱 / 조회 헬퍼
    # ------------------------------------------------------------------

    def _parse_target(self, keywords: List[str]) -> str:
        if not keywords or len(keywords) < 2:
            return ''
        return str(keywords[1]).strip()

    def _collect_known_names(self, track_rows: List[Dict[str, Any]]) -> List[str]:
        """추적 시트의 비어있지 않은 대상 이름 집합(설명행 제외)."""
        names: List[str] = []
        seen: Set[str] = set()
        for row in track_rows or []:
            name = str(row.get(NAME_COL_HEADER, '')).strip()
            if not name or name == DESCRIPTION_MARKER:
                continue
            key = _normalize_name(name)
            if key in seen:
                continue
            seen.add(key)
            names.append(name)
        return names

    def _find_target_row(
        self, track_rows: List[Dict[str, Any]], target_input: str
    ) -> Optional[Dict[str, Any]]:
        """추적 시트에서 대상 이름 행 조회 (공백/대소문자 무시, 설명행 제외)."""
        target_key = _normalize_name(target_input)
        if not target_key:
            return None
        for row in track_rows or []:
            name = str(row.get(NAME_COL_HEADER, '')).strip()
            if not name or name == DESCRIPTION_MARKER:
                continue
            if _normalize_name(name) == target_key:
                return row
        return None

    def _available_clues(self, target_row: Dict[str, Any]) -> Dict[int, str]:
        """대상 행에서 비어있지 않은 단서 {번호: 텍스트}."""
        clues: Dict[int, str] = {}
        for n, col in enumerate(CLUE_COLUMNS, start=1):
            text = str(target_row.get(col, '')).strip()
            if text:
                clues[n] = text
        return clues

    def _resolve_tracker(self, context: CommandContext) -> Tuple[str, float]:
        """추적자의 표기 이름(명단)과 이성 수치(관리)를 조회.

        조회 실패 시 이름=context.user_name, 이성=100(왜곡 없음)으로 폴백.
        """
        user_id = context.user_id
        name = context.user_name or user_id
        sanity: float = 100.0
        target_acct = _normalize_acct(user_id)

        # 명단에서 캐릭터 이름
        try:
            roster = load_user_data(self.sheets_manager) or []
            for row in roster:
                if _normalize_acct(row.get('아이디', '')) == target_acct:
                    roster_name = str(row.get('이름', '')).strip()
                    if roster_name:
                        name = roster_name
                    break
        except Exception as e:
            logger.warning(f"[추적] 명단 조회 실패 - user_name 사용: {e}")

        # 관리 시트에서 이성 수치
        try:
            management = self.sheets_manager.get_worksheet_data('관리', use_cache=False) or []
            for row in management:
                if _normalize_acct(row.get('아이디', '')) == target_acct:
                    raw = row.get('이성', '')
                    try:
                        sanity = float(str(raw).strip())
                    except (ValueError, TypeError):
                        pass
                    break
        except Exception as e:
            logger.warning(f"[추적] 관리 시트 이성 조회 실패 - 왜곡 미적용: {e}")

        return name, sanity

    # ------------------------------------------------------------------
    # 핵심 로직 (락 내부)
    # ------------------------------------------------------------------

    def _run_track(
        self,
        user_id: str,
        tracker_name: str,
        tracker_sanity: float,
        target_name: str,
        known_names: List[str],
    ) -> CommandResponse:
        # 락 내부 재조회 (stale 방지)
        track_rows = self.system_sheets_manager.get_worksheet_data(
            TRACK_SHEET, use_cache=True   # 원본 단서 시트는 정적 → 캐시(기록 시트는 아래에서 fresh)
        )
        target_row = self._find_target_row(track_rows, target_name)
        if target_row is None:
            # 락 획득 사이에 시트가 바뀐 극단적 경우
            return CommandResponse.create_success(
                f"'{target_name}'에 대해 더 알아낼 것이 없다."
            )
        target_name = str(target_row.get(NAME_COL_HEADER, target_name)).strip()
        available = self._available_clues(target_row)

        # 이미 받은 이 대상의 번호 집합
        record_rows = self.system_sheets_manager.get_worksheet_data(
            TRACK_RECORD_SHEET, use_cache=False
        )
        tracker_record = self._find_tracker_record(record_rows, tracker_name)
        received = self._received_numbers_for_target(tracker_record, target_name)

        remaining = sorted(set(available.keys()) - received)
        if not remaining:
            return CommandResponse.create_success(
                f"'{target_name}'에 대해 더 알아낼 것이 없다."
            )

        # 랜덤 1개 선택
        pick = random.choice(remaining)
        clue_text = available[pick]
        token = f"{target_name}{pick}"

        # 추적기록에 토큰 append (신규 행 또는 기존 셀 갱신)
        self._append_record_token(record_rows, tracker_record, tracker_name, token)

        # 왜곡 필터 (추적자 이성 기준)
        candidates = [n for n in known_names if _normalize_name(n) != _normalize_name(target_name)]
        distorted_text, changes = distortion.apply(clue_text, tracker_sanity, candidates or None)

        # 행동로그
        stat_note = ''
        detail = f"단서 토큰 {token}"
        if changes:
            detail += f" · 왜곡 {len(changes)}건"
        try:
            action_log.append(
                self.system_sheets_manager,
                kind=action_log.KIND_TRACK,
                actor=tracker_name,
                target=target_name,
                # 단서 번호는 시스템 값이라 요약(=소문 재료)에 넣지 않는다.
                summary=f"{add_i_ga(tracker_name)} {add_eul_reul(target_name)} 뒤쫓았다",
            )
        except Exception as e:
            logger.warning(f"[추적] 행동로그 기록 실패(무시): {e}")

        logger.info(
            f"[추적] user={user_id}, 추적자={tracker_name}, 대상={target_name}, "
            f"단서={pick}, 남은후보={len(remaining)}, 왜곡={len(changes)}건"
        )

        # 시트 셀 내용 **그대로**만 보낸다(2026-07-16). 헤더·미사여구를 붙이지 않는다.
        # 단서 문장은 그 자체로 완결된 서술이라, 앞에 뭘 얹으면 몰입이 깨진다.
        # (이성이 낮으면 위에서 왜곡 필터를 이미 태웠다 — 그것도 본문 안에 녹아 있다)
        message = distorted_text
        data = {
            'target': target_name,
            'clue_number': pick,
            'token': token,
            'distortion_changes': changes,
        }
        return CommandResponse.create_success(message, data=data)

    # ------------------------------------------------------------------
    # 추적기록 시트 조작
    # ------------------------------------------------------------------

    def _find_tracker_record(
        self, record_rows: List[Dict[str, Any]], tracker_name: str
    ) -> Optional[Dict[str, Any]]:
        """추적기록 시트에서 추적자 행 조회 (설명행 제외)."""
        key = _normalize_name(tracker_name)
        if not key:
            return None
        for row in record_rows or []:
            name = str(row.get(NAME_COL_HEADER, '')).strip()
            if not name or name == DESCRIPTION_MARKER:
                continue
            if _normalize_name(name) == key:
                return row
        return None

    def _received_numbers_for_target(
        self, tracker_record: Optional[Dict[str, Any]], target_name: str
    ) -> Set[int]:
        """추적자 기록 셀에서 이 대상에 대해 이미 받은 번호 집합."""
        received: Set[int] = set()
        if not tracker_record:
            return received
        raw = str(tracker_record.get(RECORD_COL_HEADER, '')).strip()
        if not raw:
            return received
        target_key = _normalize_name(target_name)
        for token in raw.split(','):
            token = token.strip()
            if not token:
                continue
            parsed = _split_token(token)
            if not parsed:
                continue
            name, num = parsed
            if _normalize_name(name) == target_key:
                received.add(num)
        return received

    def _append_record_token(
        self,
        record_rows: List[Dict[str, Any]],
        tracker_record: Optional[Dict[str, Any]],
        tracker_name: str,
        token: str,
    ) -> None:
        """추적기록 셀에 토큰을 추가(기존 행 갱신 또는 신규 행 append)."""
        if tracker_record is not None:
            # 기존 셀에 콤마로 append
            current = str(tracker_record.get(RECORD_COL_HEADER, '')).strip()
            new_value = f"{current}, {token}" if current else token
            row_number = tracker_record.get('_row_number')
            rec_col = self._record_column_index(tracker_record)
            if row_number and rec_col:
                self.system_sheets_manager.update_cell(
                    TRACK_RECORD_SHEET, row_number, rec_col, new_value
                )
                return
            logger.warning("[추적] 추적기록 행/열 조회 실패 - 신규 행으로 폴백")

        # 신규 추적자 행 append
        new_row = self._build_record_row(record_rows, tracker_name, token)
        self.system_sheets_manager.append_row(TRACK_RECORD_SHEET, new_row)

    def _record_column_index(self, tracker_record: Dict[str, Any]) -> Optional[int]:
        """추적기록 셀(record) 컬럼의 1-indexed 열 번호 (dict 키 순서 기반)."""
        header = [k for k in tracker_record.keys() if k != '_row_number']
        try:
            return header.index(RECORD_COL_HEADER) + 1
        except ValueError:
            logger.warning(f"[추적] 추적기록 시트에 '{RECORD_COL_HEADER}' 컬럼 없음")
            return None

    def _build_record_row(
        self, record_rows: List[Dict[str, Any]], tracker_name: str, token: str
    ) -> List[Any]:
        """신규 추적자 행을 헤더 순서에 맞춰 구성."""
        header = self._record_header(record_rows)
        if not header:
            # 헤더를 알 수 없으면 [이름, 추적기록] 2열로 폴백
            return [tracker_name, token]
        row: List[Any] = ['' for _ in header]
        for idx, col in enumerate(header):
            if col == NAME_COL_HEADER:
                row[idx] = tracker_name
            elif col == RECORD_COL_HEADER:
                row[idx] = token
        return row

    def _record_header(self, record_rows: List[Dict[str, Any]]) -> List[str]:
        """추적기록 시트 헤더 컬럼 목록."""
        for row in record_rows or []:
            keys = [k for k in row.keys() if k != '_row_number']
            if keys:
                return keys
        # 데이터가 없으면 시트에서 직접 헤더 조회
        try:
            ws = self.system_sheets_manager.get_worksheet(TRACK_RECORD_SHEET)
            header = ws.row_values(1)
            return [h for h in header if str(h).strip()]
        except Exception as e:
            logger.warning(f"[추적] 추적기록 헤더 조회 실패: {e}")
            return []

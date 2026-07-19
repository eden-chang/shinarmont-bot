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

# 설명 행 마커 (2행) — 데이터에서 제외. 실측 시트는 '.', 문서 예시는 'ㅇ'.
DESCRIPTION_MARKERS = {'ㅇ', '.'}

# `관리` 시트의 추적 카운터 컬럼(실측 헤더는 '추적'). config로 조정.
# 이 이름이 시트와 다르면 [추적]이 항상 실패한다.
TRACK_DAILY_COLUMN = (getattr(config, 'TRACK_COUNTER_COLUMN', '추적') or '추적').strip()
TRACK_DAILY_LIMIT = 1

# 트레일링 숫자(단서 번호) 추출
_TRAILING_NUM_RE = re.compile(r'(\d+)\s*$')


def _header_key(name: Any) -> str:
    """헤더 매칭용 정규화 (공백 **전부** 제거 + 소문자).

    시트 실측 헤더는 '추적 기록'인데 코드 리터럴은 '추적기록'이다.
    `sheets_operations.normalize_header`는 연속 공백을 하나로 줄일 뿐 없애지 않으므로,
    `row.get('추적기록')`이 조용히 ''를 돌려주고 수령 이력이 통째로 비어 보였다
    (→ 중복 단서 + 이름만 있는 빈 행이 계속 append 됨). 여기서는 공백을 무시하고 맞춘다.
    """
    return re.sub(r'\s+', '', str(name if name is not None else '')).lower()


RECORD_COL_KEY = _header_key(RECORD_COL_HEADER)
NAME_COL_KEY = _header_key(NAME_COL_HEADER)


def _is_description_row(name: str) -> bool:
    return name in DESCRIPTION_MARKERS


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
            if not name or _is_description_row(name):
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
            if not name or _is_description_row(name):
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
        tracker_records = self._find_tracker_records(record_rows, tracker_name)
        received = self._received_numbers_for_target(tracker_records, target_name)

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
        # 단서를 내보내기 **전에** 기록한다 — 실패하면 예외 → 일일 제한 롤백 + 오류 응답.
        self._append_record_token(record_rows, tracker_records, tracker_name, token)

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

    def _find_tracker_records(
        self, record_rows: List[Dict[str, Any]], tracker_name: str
    ) -> List[Dict[str, Any]]:
        """추적기록 시트에서 이 추적자의 행 **전부**를 위에서부터 조회 (설명행 제외).

        같은 이름의 행이 여러 개 있을 수 있다(과거 폴백 append가 남긴 잔재).
        수령 이력은 이 행들을 모두 합쳐서 판단해야 중복 단서가 안 나온다.
        """
        key = _normalize_name(tracker_name)
        if not key:
            return []
        found: List[Dict[str, Any]] = []
        for row in record_rows or []:
            name = str(self._row_get(row, NAME_COL_KEY)).strip()
            if not name or _is_description_row(name):
                continue
            if _normalize_name(name) == key:
                found.append(row)
        if len(found) > 1:
            rows = [r.get('_row_number') for r in found]
            logger.warning(
                f"[추적] '{tracker_name}' 행이 추적기록 시트에 {len(found)}개 있습니다{rows}. "
                f"첫 행에 기록하고 나머지는 읽기만 합니다 — 시트를 정리해 주세요."
            )
        return found

    def _row_get(self, row: Dict[str, Any], header_key: str) -> Any:
        """헤더 공백을 무시하고 행에서 값을 꺼낸다 ('추적 기록' vs '추적기록')."""
        for k, v in (row or {}).items():
            if k == '_row_number':
                continue
            if _header_key(k) == header_key:
                return v if v is not None else ''
        return ''

    def _received_tokens(self, tracker_records: List[Dict[str, Any]]) -> List[str]:
        """추적자의 모든 행에서 토큰 목록을 순서대로 수집(중복 제거)."""
        tokens: List[str] = []
        seen: Set[str] = set()
        for row in tracker_records or []:
            raw = str(self._row_get(row, RECORD_COL_KEY)).strip()
            for token in raw.split(','):
                token = token.strip()
                if not token:
                    continue
                key = _normalize_name(token)
                if key in seen:
                    continue
                seen.add(key)
                tokens.append(token)
        return tokens

    def _received_numbers_for_target(
        self, tracker_records: List[Dict[str, Any]], target_name: str
    ) -> Set[int]:
        """추적자 기록에서 이 대상에 대해 이미 받은 번호 집합."""
        received: Set[int] = set()
        target_key = _normalize_name(target_name)
        for token in self._received_tokens(tracker_records):
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
        tracker_records: List[Dict[str, Any]],
        tracker_name: str,
        token: str,
    ) -> None:
        """추적기록 셀에 토큰을 추가(기존 행 갱신 또는 신규 행 append).

        기록이 실패하면 **예외를 던진다**. 조용히 넘기면 단서만 나가고 이력이 안 남아
        같은 단서가 다시 배분된다(실제로 그렇게 됐다). 호출부가 일일 제한을 롤백한다.
        """
        if tracker_records:
            # 첫 행에 콤마로 append (나머지 행의 토큰도 합쳐 한 곳으로 모은다)
            primary = tracker_records[0]
            merged = self._received_tokens(tracker_records)
            merged.append(token)
            new_value = ', '.join(merged)
            row_number = primary.get('_row_number')
            rec_col = self._record_column_index(primary)
            if row_number and rec_col:
                ok = self.system_sheets_manager.update_cell(
                    TRACK_RECORD_SHEET, row_number, rec_col, new_value
                )
                if not ok:
                    raise CommandError(
                        f"추적기록 갱신 실패 (행 {row_number}, 열 {rec_col})"
                    )
                return
            logger.warning("[추적] 추적기록 행/열 조회 실패 - 신규 행으로 폴백")

        # 신규 추적자 행 append
        new_row = self._build_record_row(record_rows, tracker_name, token)
        if not self.system_sheets_manager.append_row(TRACK_RECORD_SHEET, new_row):
            raise CommandError(f"추적기록 행 추가 실패 ({tracker_name})")

    def _record_column_index(self, tracker_record: Dict[str, Any]) -> Optional[int]:
        """추적기록 셀(record) 컬럼의 1-indexed 열 번호 (dict 키 순서 기반).

        헤더 공백을 무시하고 맞춘다 — 실측 시트 헤더가 '추적 기록'이다.
        """
        header = [k for k in tracker_record.keys() if k != '_row_number']
        for idx, col in enumerate(header):
            if _header_key(col) == RECORD_COL_KEY:
                return idx + 1
        logger.warning(f"[추적] 추적기록 시트에 '{RECORD_COL_HEADER}' 컬럼 없음: {header}")
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
        placed = False
        for idx, col in enumerate(header):
            key = _header_key(col)
            if key == NAME_COL_KEY:
                row[idx] = tracker_name
            elif key == RECORD_COL_KEY:
                row[idx] = token
                placed = True
        if not placed:
            # 기록 열을 못 찾으면 이름만 있는 빈 행이 쌓인다 — 그럴 바엔 실패시킨다.
            raise CommandError(
                f"추적기록 시트에서 '{RECORD_COL_HEADER}' 열을 찾지 못했습니다: {header}"
            )
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

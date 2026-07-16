"""
[투표/이름/유죄] · [투표/이름/무죄] 명령어 (시너몬트 · 스토리 · @STORY)

DM 전용. **표를 '투표' 시트에 남기는 것이 전부**다.
집계·처분·과반 판정은 GM의 몫이고 이 명령어의 스코프가 아니다.

조건은 둘뿐이다:
1. 명단에 있는 인물이어야 한다 (없는 사람에게는 투표할 수 없다).
2. 표는 '유죄' 또는 '무죄' 중 하나여야 한다.

단상(설정 시트 '단상 명단')은 보지 않는다. 누구에게든 투표할 수 있다.
자기 자신에게도 투표할 수 있다 — 피고가 스스로 무죄를 던지는 건 자연스럽다.

'유죄'/'무죄'는 예약어라 **순서가 뒤바뀌어도**(`[투표/유죄/데보라]`) 알아본다.
이름이 '유죄'인 사람은 없다.

같은 일차에 같은 대상으로 다시 투표하면 **기존 행의 표를 바꾼다**(행을 더하지 않는다).
시트는 집계의 원장이라 한 사람이 두 행을 차지하면 GM이 중복으로 센다.
거절하지 않는 이유는, 마음을 바꾸는 것 자체는 막을 이유가 없어서다.

docs/코딩_계획.md §6(그룹 C) / docs/시너몬트_구현계획.md §5.11 / docs/스프레드시트_구성.md 준수.
"""

import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

# 경로 설정 (VM 환경 대응)
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))

from utils.imports import *  # noqa: F401,F403  (register_command, BaseCommand, CommandContext, CommandResponse, config, CommandError, logger, load_user_data 등)
from utils.dm_guard import dm_only
from utils.lock_manager import get_lock_manager

try:
    from utils.game_day import current_day
except ImportError:  # 폴백 (부분 환경/테스트)
    def current_day() -> int:  # type: ignore
        return 1


# 시트/컬럼 상수
VOTE_SHEET = '투표'
VOTE_COLUMNS = ['일차', '대상', '투표자', '표']
VERDICT_COL_INDEX = 4  # '표' 열 (1-indexed) — 재투표 시 이 칸만 덮어쓴다

# 허용 투표 값(표)
VERDICT_GUILTY = '유죄'
VERDICT_INNOCENT = '무죄'
VALID_VERDICTS = (VERDICT_GUILTY, VERDICT_INNOCENT)


def _normalize(name: str) -> str:
    """이름 매칭용 정규화(공백 제거 + 소문자)."""
    if not name:
        return ''
    return re.sub(r'\s+', '', str(name)).strip().lower()


@register_command(
    name="투표",
    aliases=["vote"],
    description="인물의 처분을 유죄/무죄로 투표합니다. (DM 전용)",
    category="스토리",
    examples=["[투표/데보라/유죄]", "[투표/데보라/무죄]"],
    requires_sheets=True,
    requires_api=False,
)
class VoteCommand(BaseCommand):
    """유죄/무죄 투표 기록 명령어."""

    @staticmethod
    def get_supported_keywords() -> List[str]:
        return ["투표", "vote"]

    def __init__(self, sheets_manager=None, api=None, **kwargs):
        super().__init__(sheets_manager, api, **kwargs)
        self.system_sheets_manager = kwargs.get('system_sheets_manager')
        logger.debug(
            f"[투표] VoteCommand 초기화: system_sheets_manager="
            f"{'있음' if self.system_sheets_manager else '없음'}"
        )

    @dm_only
    def execute(self, context: CommandContext) -> CommandResponse:
        # 1) 파싱 (락 밖)
        try:
            target_input, verdict = self._parse(context.keywords)
        except CommandError as e:
            return CommandResponse.create_error(str(e), error=e)

        if self.system_sheets_manager is None:
            return CommandResponse.create_error(
                "투표 기능이 설정되지 않았습니다. 관리자에게 문의해 주세요."
            )

        # 2) 명단 대조 — 존재하는 인물인가
        roster = self._load_roster()
        if not roster:
            return CommandResponse.create_error(
                "명단을 확인할 수 없습니다. 잠시 후 다시 시도해 주세요."
            )

        target_name = self._match_roster_name(roster, target_input)
        if not target_name:
            return CommandResponse.create_error(
                f"'{target_input}'은(는) 명단에 없는 인물입니다."
            )

        voter_name = self._voter_name(roster, context)
        if not voter_name:
            return CommandResponse.create_error("투표자 정보를 확인할 수 없습니다.")

        day = current_day()

        # 3) 락 → 재조회 → 기록 (stale 방지)
        lock_manager = get_lock_manager()
        with lock_manager.acquire_lock(context.user_id, timeout=10.0) as acquired:
            if not acquired:
                return CommandResponse.create_error(
                    "다른 처리가 진행 중입니다. 잠시 후 다시 시도해 주세요."
                )

            try:
                existing = self._find_vote_row(day, target_name, voter_name)
                if existing is None:
                    changed = False
                    success = self.system_sheets_manager.append_row(
                        VOTE_SHEET,
                        [day, target_name, voter_name, verdict],
                    )
                else:
                    changed = True
                    row_number, previous = existing
                    if previous == verdict:
                        # 같은 표를 다시 던졌다 — 시트를 건드릴 이유가 없다.
                        return CommandResponse.create_success(
                            f"'{target_name}'에 대한 [{verdict}] 투표가 이미 접수되어 있습니다.",
                            data={'일차': day, '대상': target_name,
                                  '투표자': voter_name, '표': verdict, '변경': False},
                        )
                    success = self.system_sheets_manager.update_cell(
                        VOTE_SHEET, row_number, VERDICT_COL_INDEX, verdict
                    )
            except CommandError as e:
                return CommandResponse.create_error(str(e), error=e)
            except Exception as e:
                logger.error(f"[투표] 처리 중 오류: {e}", exc_info=True)
                return CommandResponse.create_error("투표 처리 중 오류가 발생했습니다.", error=e)

        if not success:
            return CommandResponse.create_error(
                "투표 기록에 실패했습니다. 잠시 후 다시 시도해 주세요."
            )

        logger.info(
            f"[투표] {'변경' if changed else '접수'}: 일차={day}, 대상={target_name}, "
            f"투표자={voter_name}, 표={verdict}"
        )
        if changed:
            message = f"'{target_name}'에 대한 표를 [{verdict}]로 변경했습니다."
        else:
            message = f"'{target_name}'에 대한 [{verdict}] 투표가 접수되었습니다."
        return CommandResponse.create_success(
            message,
            data={
                '일차': day,
                '대상': target_name,
                '투표자': voter_name,
                '표': verdict,
                '변경': changed,
            },
        )

    # ------------------------------------------------------------------
    # 파싱
    # ------------------------------------------------------------------
    def _parse(self, keywords: List[str]) -> Tuple[str, str]:
        """인자에서 (대상 이름, 표)를 뽑는다. 자리 순서는 보지 않는다.

        '유죄'/'무죄'는 예약어이므로 어느 자리에 있든 그걸 표로 보고
        나머지를 이름으로 본다 → `[투표/유죄/데보라]`도 그대로 통한다.
        """
        args = [str(k).strip() for k in (keywords or [])[1:] if str(k).strip()]
        verdicts = [a for a in args if a in VALID_VERDICTS]
        names = [a for a in args if a not in VALID_VERDICTS]

        if not args:
            raise CommandError(
                "투표 형식이 올바르지 않습니다. 예) [투표/데보라/유죄]"
            )
        if not verdicts:
            raise CommandError(
                "투표는 '유죄' 또는 '무죄'만 가능합니다. 예) [투표/데보라/유죄]"
            )
        if not names:
            raise CommandError(
                "투표 대상을 지정해 주세요. 예) [투표/데보라/유죄]"
            )
        return names[0], verdicts[0]

    # ------------------------------------------------------------------
    # 명단
    # ------------------------------------------------------------------
    def _load_roster(self) -> List[Dict[str, Any]]:
        """명단 로드. 준정적이라 캐시를 쓴다."""
        try:
            return load_user_data(self.sheets_manager) or []
        except Exception as e:
            logger.error(f"[투표] 명단 조회 실패: {e}", exc_info=True)
            return []

    @staticmethod
    def _match_roster_name(roster: List[Dict[str, Any]], given: str) -> str:
        """명단에서 이름을 찾아 시트 표기 그대로 반환(공백/대소문자 무시)."""
        target_norm = _normalize(given)
        for row in roster:
            name = str(row.get('이름', '')).strip()
            if name and _normalize(name) == target_norm:
                return name
        return ''

    @staticmethod
    def _voter_name(roster: List[Dict[str, Any]], context: CommandContext) -> str:
        """투표자의 캐릭터 이름.

        시트를 사람이 읽는 원장으로 쓰므로 아이디가 아니라 이름을 남긴다
        ('대상' 열도 이름이라, 한쪽만 아이디면 GM이 대조할 수 없다).
        """
        acct = str(context.user_id or '').strip().lstrip('@').lower()
        for row in roster:
            row_acct = str(row.get('아이디', '')).strip().lstrip('@').lower()
            if acct and row_acct == acct:
                name = str(row.get('이름', '')).strip()
                if name:
                    return name
        # 명단에 없으면 표시명 → 아이디 순으로 폴백한다. 투표자 미등록은
        # GM의 데이터 문제지 러너 잘못이 아니라, 표를 버리진 않는다.
        return str(context.user_name or '').strip() or str(context.user_id or '').strip()

    # ------------------------------------------------------------------
    # 투표 시트
    # ------------------------------------------------------------------
    def _find_vote_row(
        self, day: int, target_name: str, voter_name: str
    ) -> Optional[Tuple[int, str]]:
        """(일차, 대상, 투표자)로 기존 표를 찾아 (행 번호, 기존 표)를 반환."""
        try:
            rows = self.system_sheets_manager.get_worksheet_data(VOTE_SHEET, use_cache=False)
        except Exception as e:
            logger.error(f"[투표] '투표' 시트 조회 실패: {e}", exc_info=True)
            # 조회 실패 시 차단한다. 그냥 append 하면 이미 던진 표 위에 한 행이 더 쌓여
            # GM이 중복으로 센다 — 원장이 어긋나는 쪽이 더 나쁘다.
            raise CommandError("투표 내역을 확인할 수 없습니다. 잠시 후 다시 시도해 주세요.")

        day_str = str(day).strip()
        target_norm = _normalize(target_name)
        voter_norm = _normalize(voter_name)

        for row in rows or []:
            if not self._same_day(str(row.get('일차', '')).strip(), day_str):
                continue
            if _normalize(row.get('대상', '')) != target_norm:
                continue
            if _normalize(row.get('투표자', '')) != voter_norm:
                continue
            row_number = row.get('_row_number')
            if not isinstance(row_number, int):
                # 행 번호를 모르면 덮어쓸 수 없다. 중복 행을 만드느니 거절한다.
                raise CommandError(
                    "이미 투표하셨습니다. 표를 바꾸려면 관리자에게 문의해 주세요."
                )
            return row_number, str(row.get('표', '')).strip()
        return None

    @staticmethod
    def _same_day(a: str, b: str) -> bool:
        """일차 문자열 비교('1', '1.0' 등 관용 처리)."""
        if a == b:
            return True
        try:
            return int(float(a)) == int(float(b))
        except (ValueError, TypeError):
            return False

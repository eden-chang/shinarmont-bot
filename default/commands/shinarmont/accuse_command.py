"""
[고발/캐릭터명] + 본문 사유 명령어 (시너몬트 @STORY)

DM 전용. 하루 1회(봇 JSON `오늘고발`). 의심스러운 인물을 고발한다.
- 사유는 context.original_text 의 명령 대괄호(]) 이후 텍스트에서 추출(공백 제외 1줄 이상).
- 대상 유효성은 target_helpers.resolve_target 으로 검증(자기자신/미등록 방지).
- 시스템 '고발' 시트에 (일차, 고발자, 대상, 사유, 처리='') append.
- 고발자 소지금 += ACCUSE_REWARD(기본 50) 원자적 갱신 + 캐시 무효화.
- 행동로그(종류='고발') 1행 append.

docs/시너몬트_구현계획.md §5.10 / docs/명령어_개요.md @STORY / docs/코딩_계획.md §6 참조.
"""

from utils.imports import *  # config, logger, CommandError, BaseCommand, CommandContext, CommandResponse, register_command, invalidate_user_cache, load_user_data, add_i_ga, add_eul_reul 등

import re

from utils.dm_guard import dm_only
from utils.lock_manager import get_lock_manager
from utils.target_helpers import resolve_target, get_target_error
from utils import game_state
from utils import action_log
from utils import game_day


@register_command(
    name="고발",
    aliases=["accuse"],
    description="의심스러운 인물을 고발합니다. (DM 전용, 하루 1회) 사유를 함께 적어주세요.",
    category="스토리",
    examples=["[고발/철수] 창고 근처에서 수상한 행동을 목격했습니다."],
    requires_sheets=True,
    requires_api=False,
)
class AccuseCommand(BaseCommand):
    """[고발/캐릭터명] + 사유 본문 처리."""

    def __init__(self, sheets_manager=None, api=None, **kwargs):
        super().__init__(sheets_manager, api, **kwargs)
        self.system_sheets_manager = kwargs.get('system_sheets_manager')

    # ------------------------------------------------------------------
    # 보조 유틸
    # ------------------------------------------------------------------
    @staticmethod
    def _norm_id(acct) -> str:
        """아이디/acct 매칭 정규화 (선행 @ 제거 + 공백제거 + 소문자)."""
        if not acct:
            return ''
        return str(acct).strip().lstrip('@').strip().lower()

    @staticmethod
    def _parse_money(value) -> int:
        """소지금 값을 정수로 파싱 (쉼표/공백/실수 표기 허용)."""
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, str):
            cleaned = value.replace(',', '').strip()
            try:
                return int(float(cleaned))
            except (ValueError, TypeError):
                return 0
        return 0

    @staticmethod
    def _extract_reason(original_text: str) -> str:
        """
        명령 대괄호(]) 이후의 텍스트를 사유로 추출한다.

        예) '[고발/철수] 창고에서 수상한 행동' -> '창고에서 수상한 행동'
        닫는 대괄호가 없으면 빈 문자열을 반환한다.

        original_text 는 라우터에서 BBCode 색상/배경 태그가 제거되지 않은 채
        전달될 수 있으므로(report_command 와 동일 규칙), 먼저 색상/배경 태그를
        제거한 뒤 첫 번째 닫는 대괄호 이후를 사유로 사용한다.
        """
        if not original_text:
            return ''
        # BBCode 스타일 색상/배경 태그 제거 ([color:hex], [/color], [bg:hex], [/bg])
        cleaned = re.sub(r'\[/?(color|bg)(:[0-9a-fA-F]{3,8})?\]', '', original_text)
        idx = cleaned.find(']')
        if idx < 0:
            return ''
        return cleaned[idx + 1:].strip()

    @staticmethod
    def _summarize_reason(reason: str, limit: int = 100) -> str:
        """사유를 행동로그 상세용 한 줄로 요약(개행 공백화 + 길이 제한)."""
        one_line = re.sub(r'\s+', ' ', reason).strip()
        if len(one_line) > limit:
            one_line = one_line[:limit].rstrip() + '…'
        return one_line

    def _get_accuser(self, user_id: str):
        """고발자(명령 사용자)의 이름 + 관리 시트 행/소지금을 조회."""
        norm = self._norm_id(user_id)
        if not norm:
            return None

        roster = load_user_data(self.sheets_manager) or []
        name = None
        for row in roster:
            if self._norm_id(row.get('아이디', '')) == norm:
                name = str(row.get('이름', '')).strip()
                break
        if not name:
            return None

        try:
            management = self.sheets_manager.get_worksheet_data('관리', use_cache=False) or []
        except Exception as e:  # pragma: no cover - 시트 오류 방어
            logger.error(f"[고발] 관리 시트 조회 실패: {e}")
            return None

        for row in management:
            if self._norm_id(row.get('아이디', '')) == norm:
                header = [k for k in row.keys() if k != '_row_number']
                return {
                    '이름': name,
                    '_row_number': row.get('_row_number'),
                    '소지금': row.get('소지금', 0),
                    'header': header,
                }
        return None

    # ------------------------------------------------------------------
    # 실행
    # ------------------------------------------------------------------
    @dm_only
    def execute(self, context: CommandContext) -> CommandResponse:
        try:
            # 0) 시스템 시트 연결 확인
            if self.system_sheets_manager is None:
                return CommandResponse.create_error("고발 시스템(시트) 연결이 필요합니다. 잠시 후 다시 시도해 주세요.")

            keywords = context.keywords or []

            # 1) 대상 캐릭터명 파싱
            if len(keywords) < 2 or not str(keywords[1]).strip():
                return CommandResponse.create_error(
                    "사용법: [고발/캐릭터명] 뒤에 고발 사유를 적어주세요.\n"
                    "예: [고발/철수] 창고 근처에서 수상한 행동을 목격했습니다."
                )

            # 2) 사유 추출 및 검증 (공백 제외 1줄 이상)
            reason = self._extract_reason(context.original_text or '')
            if not reason:
                return CommandResponse.create_error(
                    "고발 사유를 함께 적어주세요. [고발/캐릭터명] 뒤에 사유를 1줄 이상 작성해야 합니다."
                )

            # 3) 대상 유효성 검증 (자기자신/미등록 방지)
            target = resolve_target(context, self.sheets_manager, keyword_index=1)
            if not target:
                reason_msg = get_target_error(context, "고발 대상을 찾을 수 없습니다.")
                return CommandResponse.create_error(reason_msg)

            # 4) 고발자 정보 조회 (이름/행/소지금)
            accuser = self._get_accuser(context.user_id)
            if not accuser or not accuser.get('_row_number'):
                return CommandResponse.create_error(
                    "고발자 정보를 찾을 수 없습니다. 명단/관리 시트에 등록되어 있는지 확인해 주세요."
                )

            accuser_name = accuser['이름']
            target_name = target['이름']

            # 5) 일일 제한 체크 (JSON '오늘고발', 하루 1회)
            if not game_state.check_and_set(context.user_id, '오늘고발', 1):
                return CommandResponse.create_error("오늘은 이미 고발했습니다. 고발은 하루 1회만 가능합니다.")

            reward = int(getattr(config, 'ACCUSE_REWARD', 50))
            day = self._current_day()

            # 6) 락 확보 후 원자적 처리
            lock_manager = get_lock_manager()
            with lock_manager.acquire_lock(context.user_id, timeout=10.0) as acquired:
                if not acquired:
                    # 처리 못 했으므로 일일 제한 롤백
                    game_state.set(context.user_id, '오늘고발', 0)
                    return CommandResponse.create_error("다른 처리가 진행 중입니다. 잠시 후 다시 시도해 주세요.")

                # 락 내부 재조회 (stale 방지)
                accuser = self._get_accuser(context.user_id)
                if not accuser or not accuser.get('_row_number'):
                    game_state.set(context.user_id, '오늘고발', 0)
                    return CommandResponse.create_error("고발자 정보를 찾을 수 없습니다.")

                # 6-1) '고발' 시트 기록: [일차, 고발자, 대상, 사유, 처리='']
                appended = self.system_sheets_manager.append_row(
                    '고발', [day, accuser_name, target_name, reason, '']
                )
                if not appended:
                    game_state.set(context.user_id, '오늘고발', 0)
                    return CommandResponse.create_error("고발 기록 저장에 실패했습니다. 잠시 후 다시 시도해 주세요.")

                # 6-2) 고발자 소지금 += ACCUSE_REWARD (원자적 갱신)
                try:
                    current_money = self._parse_money(accuser.get('소지금', 0))
                    new_money = current_money + reward
                    money_col = accuser['header'].index('소지금') + 1
                    self.sheets_manager.batch_update_cells(
                        '관리', [(accuser['_row_number'], money_col, new_money)]
                    )
                    invalidate_user_cache()
                except ValueError:
                    logger.error("[고발] 관리 시트에서 '소지금' 컬럼을 찾을 수 없어 보상 지급을 건너뜁니다.")
                    new_money = None
                except Exception as e:  # pragma: no cover - 시트 오류 방어
                    logger.error(f"[고발] 소지금 갱신 실패(고발 기록은 유지): {e}")
                    new_money = None

            # 7) 행동로그 append (종류='고발')
            # add_i_ga/add_eul_reul 은 **단어+조사 전체**를 돌려준다.
            # 앞에 이름을 또 붙이면 '데보라데보라가'가 된다(2026-07-16 실제로 그랬다).
            summary = f"{add_i_ga(accuser_name)} {add_eul_reul(target_name)} 고발했다"
            action_log.append(
                self.system_sheets_manager,
                kind=action_log.KIND_ACCUSE,
                actor=accuser_name,
                target=target_name,
                summary=summary,
            )

            # 8) 응답
            # 고발은 밀고다 — 누구를 고발했는지 되읊지 않는다(수정2).
            # 사례금이 지급되지 않았으면 그 줄을 아예 빼서 헛된 기대를 주지 않는다.
            currency = getattr(config, 'CURRENCY', '달러')
            message = "접수 완료"
            if new_money is not None:
                message += f"\n➭ {reward}{currency} 획득"

            return CommandResponse.create_success(
                message,
                data={
                    'accuser': accuser_name,
                    'target': target_name,
                    'reward': reward,
                    'day': day,
                },
            )

        except CommandError as e:
            return CommandResponse.create_error(str(e))
        except Exception as e:
            logger.error(f"[고발] 실행 중 예외: {e}", exc_info=True)
            return CommandResponse.create_error("고발 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.")

    @staticmethod
    def _current_day() -> int:
        try:
            return game_day.current_day()
        except Exception as e:  # pragma: no cover
            logger.warning(f"[고발] 일차 계산 실패, 1일차로 폴백: {e}")
            return 1

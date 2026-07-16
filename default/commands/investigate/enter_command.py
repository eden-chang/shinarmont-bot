"""
[진입/장소명] 명령어 (가이드 §3.2)

판정 순서:
1. 장소명이 진입 시트에 존재하는가 → 없으면 "그런 곳은 이 마을에 없다."
2. 진입 판정 조건 1(장소가 열려 있음)과 2(조사 가능한 포인트 1개 이상)를 모두 충족하는가
   → 아니면 "이곳에는 현재 조사할 수 있는 것이 없다."
   장소가 닫힌 것인지 자기 직군이 볼 게 없는 것인지 구분해 주지 않는다(정보 비대칭 유지).
3. 성공 → 현재 위치 설정 + 로그 기록 + 응답

응답: "{진입 시 문구} {포인트 목록 문장}"
'@계정' 프리픽스는 스트림 핸들러가 붙인다.
"""

import os
import sys
from typing import Any, Dict, List

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))

try:
    from utils.logging_config import logger
    from utils.korean_utils import get_last_char, has_final_consonant
    from utils.investigation_log import append_entry
    from utils.investigation_sheet import (
        COL_ENTRY_TEXT, COL_POINT, InvestigationDataError,
        display_name, pick_variant, visible_point_names,
    )
    from utils.investigation_state import get_investigation_state
    from commands.base_command import CommandContext, CommandResponse
    from commands.registry import register_command
    from commands.default.custom_command import process_all_custom_substitutions
    from commands.investigate.base_investigate import BaseInvestigateCommand
except ImportError as e:  # pragma: no cover
    import logging
    logger = logging.getLogger('investigate.enter')
    logger.error(f"[진입] 필수 모듈 임포트 실패: {e}")
    raise


@register_command(
    name="진입",
    aliases=["입장"],
    description="지정한 장소로 진입합니다. 이후 [조사] 명령어는 해당 장소를 기준으로 수행됩니다.",
    category="조사",
    examples=["[진입/중앙 광장]"],
    requires_sheets=True,
    requires_api=False,
)
class EnterCommand(BaseInvestigateCommand):
    """조사 장소 진입 명령어."""

    @staticmethod
    def get_supported_keywords() -> List[str]:
        return ["진입", "입장"]

    def execute(self, context: CommandContext) -> CommandResponse:
        location_arg = self.parse_argument(context.keywords)
        if not location_arg:
            return self.refuse('NO_SUCH_PLACE')

        try:
            return self._run(context, location_arg)
        except InvestigationDataError as e:
            logger.error(f"[진입] 시트 오류: {e}")
            return CommandResponse.create_error(self.msg('TEMPORARY'), error=e)
        except Exception as e:
            logger.error(f"[진입] 처리 중 오류: {e}", exc_info=True)
            return CommandResponse.create_error(self.msg('TEMPORARY'), error=e)

    def _run(self, context: CommandContext, location_arg: str) -> CommandResponse:
        actor = self.load_actor(context.user_id)
        if actor is None:
            return self.refuse('NO_CHARACTER')

        repo = self.repo
        # 진입은 개방 여부가 곧 판정이므로 진입 시트를 미캐시로 재검증한다 (§1)
        entry_rows, exception_rows = self.load_context(fresh_entry=True)

        # 1. 장소명이 진입 시트에 존재하는가
        location = repo.canonical_location(location_arg, entry_rows)
        if location is None:
            logger.info(f"[진입] 없는 장소: {actor.name} → '{location_arg}'")
            return self.refuse('NO_SUCH_PLACE')

        # 2-1. 조건 1 — 장소가 '가능' 상태인가
        open_rows = repo.open_entry_rows(location, entry_rows)
        if not open_rows:
            logger.info(f"[진입] 닫힌 장소: {actor.name} → '{location}'")
            return self.refuse('CANNOT_ENTER', location=location)

        # 2-2. 조건 2 — 캐릭터가 조사 가능한 포인트가 1개 이상인가
        point_rows = repo.point_rows(location, fresh=True)
        self.warn_version_conflicts(point_rows, COL_POINT, f"'{location}' 시트")

        points = visible_point_names(
            point_rows, exception_rows, actor.name, location, actor.role)
        if not points:
            logger.info(
                f"[진입] 조사 가능 포인트 없음: {actor.name}(직군={actor.role}) → '{location}'")
            return self.refuse('CANNOT_ENTER', location=location)

        # 3. 성공
        entry_row = pick_variant(open_rows)
        message = self._entry_text(entry_row, location, actor.name)
        sentence = self._point_sentence(points)

        # 시트 기록을 먼저 완료한 뒤 답글을 보낸다 (§7)
        self._log_entry(context.user_id, actor.name, location)
        get_investigation_state().enter(context.user_id, location)

        logger.info(
            f"[진입] {actor.name}(직군={actor.role}) → '{location}', 포인트 {len(points)}개")
        # 장소명을 제목 줄로 세운다. 진입 문구만 오면 어디에 들어왔는지가 본문에
        # 묻혀서, 러너가 [조사] 대상을 고를 때 현재 위치를 되짚을 수 없다.
        return CommandResponse.create_success(
            f"{location}\n\n" + f"{message} {sentence}".strip(),
            data={'location': location, 'points': points},
        )

    def _log_entry(self, user_id: str, character: str, location: str) -> None:
        """진입을 로그 시트에 기록한다.

        같은 장소에 이미 들어와 있으면 기록하지 않는다. 진입은 횟수 제한이 없어
        러너가 반복 입력할 수 있는데, 매번 행을 추가하면 로그가 무한히 길어지고
        로그 전체를 읽는 [조사]의 판정 비용까지 함께 커진다.
        현재 위치 복원에 필요한 정보는 '마지막 진입 장소' 하나뿐이라 중복 행은 무가치하다.

        기록 실패는 러너에게 알리지 않는다(진입 자체는 메모리 상태로 성립).
        다만 재시작 시 위치 복원이 불가능해지므로 관리자에게 알린다.
        """
        if get_investigation_state().get_location(user_id) == location:
            logger.debug(f"[진입] 이미 '{location}'에 있음 - 로그 기록 생략: {character}")
            return

        if not append_entry(self.investigation_sheets_manager, character, location):
            self.warn_admin(
                f"⚠️ 진입 로그 기록 실패 — {character}의 '{location}' 진입이 로그 시트에 "
                f"남지 않았습니다. 봇이 재시작되면 이 러너의 현재 위치가 복원되지 않아 "
                f"[조사] 시 다시 진입하라는 응답을 받게 됩니다."
            )

    def _entry_text(self, entry_row: Dict[str, Any], location: str, user_name: str) -> str:
        """'진입 시 문구'. 여러 '가능' 행이 있으면 호출측이 이미 무작위로 골랐다 (§2.1 변주)."""
        message = display_name(entry_row.get(COL_ENTRY_TEXT))
        if not message:
            logger.warning(f"[진입] '{location}'의 '진입 시 문구'가 비어 있습니다.")
            self.warn_admin_once(
                f"empty-entry-text:{location}",
                f"'{location}'의 '가능' 행에 진입 시 문구가 비어 있습니다.",
            )
            return f"{location}에 들어선다."
        try:
            return process_all_custom_substitutions(message, user_name)
        except Exception as e:
            logger.warning(f"[진입] 템플릿 치환 실패 - 원문 반환: {e}")
            return message

    @staticmethod
    def _point_sentence(points: List[str]) -> str:
        """포인트 목록 문장을 동적으로 생성 (§3.2).

        "[{포인트1}], [{포인트2}]을(를) 조사할 수 있다."
        을/를은 마지막 포인트명의 종성 유무로 판단한다.
        """
        if not points:
            return ''
        listed = ', '.join(f"[{p}]" for p in points)
        josa = '을' if has_final_consonant(get_last_char(points[-1])) else '를'
        return f"{listed}{josa} 조사할 수 있다."

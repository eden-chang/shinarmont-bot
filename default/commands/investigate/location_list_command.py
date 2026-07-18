"""
[장소 목록] 명령어 (가이드 §3.1)

진입 판정(§2.1의 조건 1과 2)을 모든 장소에 대해 수행하고, 통과한 장소만 나열한다.
즉 진입 시트가 '가능'이더라도 캐릭터가 조사할 포인트가 하나도 없는 장소는 목록에서 빠진다.
예: 기술공이 조사할 포인트가 중앙 광장과 연구소에만 있다면 그 둘만 뜬다.

장소 수 × 포인트 수만큼의 판정이 필요하므로 시트 캐시를 활용한다
(TTL은 config.INVESTIGATION_CACHE_TTL). 목록은 정합성보다 응답성이 중요한 읽기 전용
명령이라 캐시를 쓰고, 실제 진입·조사 시점에 미캐시로 재검증한다.
"""

import os
import sys
from typing import List

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))

try:
    from utils.logging_config import logger
    from utils.investigation_sheet import (
        COL_LOCATION, COL_POINT, InvestigationDataError,
        accessible_point_rows, display_name,
    )
    from commands.base_command import CommandContext, CommandResponse
    from commands.registry import register_command
    from commands.investigate.base_investigate import BaseInvestigateCommand
except ImportError as e:  # pragma: no cover
    import logging
    logger = logging.getLogger('investigate.location_list')
    logger.error(f"[장소 목록] 필수 모듈 임포트 실패: {e}")
    raise


@register_command(
    name="장소 목록",
    aliases=["장소목록", "진입 가능 장소", "진입가능장소"],
    description="현재 진입할 수 있는 장소 목록을 출력합니다.",
    category="조사",
    examples=["[장소 목록]"],
    requires_sheets=True,
    requires_api=False,
)
class LocationListCommand(BaseInvestigateCommand):
    """진입 가능한 장소 목록 출력 명령어."""

    @staticmethod
    def get_supported_keywords() -> List[str]:
        return ["장소 목록", "장소목록", "진입 가능 장소", "진입가능장소"]

    def execute(self, context: CommandContext) -> CommandResponse:
        blocked = self.disqualified_response(context)
        if blocked is not None:
            return blocked
        try:
            actor = self.load_actor(context.user_id)
            if actor is None:
                return self.refuse('NO_CHARACTER')

            locations = self._enterable_locations(actor)
        except InvestigationDataError as e:
            logger.error(f"[장소 목록] 시트 오류: {e}")
            return CommandResponse.create_error(self.msg('TEMPORARY'), error=e)
        except Exception as e:
            logger.error(f"[장소 목록] 처리 중 오류: {e}", exc_info=True)
            return CommandResponse.create_error(self.msg('TEMPORARY'), error=e)

        if not locations:
            return self.refuse('NO_LOCATIONS', locations=[])

        lines = ["진입 가능 장소", ""]
        lines.extend(f"- {name}" for name in locations)
        lines.extend(["", "[진입/장소명]으로 조사 시작"])

        logger.info(
            f"[장소 목록] {actor.name}(직군={actor.role}) → {len(locations)}개: {locations}")
        return CommandResponse.create_success(
            "\n".join(lines), data={'locations': locations})

    def _enterable_locations(self, actor) -> List[str]:
        """진입 판정 조건 1·2를 모두 통과한 장소명 (중복 제거, 시트 순서)."""
        repo = self.repo
        entry_rows, exception_rows = self.load_context()

        self.warn_version_conflicts(entry_rows, COL_LOCATION, '진입 시트')

        result: List[str] = []
        for location in repo.known_locations(entry_rows):
            # 조건 1: 진입 시트에 '현재 조사 가능 = 가능' 행이 존재
            if not repo.open_entry_rows(location, entry_rows):
                continue

            # 조건 2: 캐릭터가 조사 가능한 포인트가 1개 이상
            #
            # 장소 시트를 읽지 못하면 그 장소만 건너뛰지 않고 **명령 전체를 실패**시킨다.
            # 건너뛰면 러너에게는 장소가 조용히 사라진 것처럼 보이고(어제는 보였는데?),
            # 원인이 시트 삭제인지 API 장애인지 아무도 모른 채 목록이 계속 틀리게 나간다.
            # 진입 시트에 등재된 장소의 시트는 반드시 존재해야 하므로, 못 읽으면 운영 사고다.
            try:
                point_rows = repo.point_rows(location)
            except InvestigationDataError:
                self.warn_admin(
                    f"⚠️ '{location}' 장소 시트를 읽지 못해 [장소 목록]을 중단했습니다. "
                    f"진입 시트에 등재된 장소이므로 같은 이름의 시트가 있어야 합니다. "
                    f"시트명 일치 여부와 API 상태를 확인해 주세요."
                )
                raise

            self.warn_version_conflicts(point_rows, COL_POINT, f"'{location}' 시트")

            if accessible_point_rows(
                point_rows, exception_rows, actor.name, location, actor.role
            ):
                result.append(display_name(location))

        return result

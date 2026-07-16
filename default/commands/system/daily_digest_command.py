"""
[일일보고] · [일일보고/일차] 명령어 (시너몬트 · 관리자 전용 · @SYSTEM)

하루치 사건을 모아 @NOTICE 에게 DM 타래로 보낸다(GM 소문 제작용).
스케줄러가 매일 23:30 KST 에 자동으로 돌리지만, GM이 아무 때나 다시 뽑아볼 수 있어야 한다:
- 지난 일차를 되짚을 때: `[일일보고/3]`
- 보고 시각 전에 미리 볼 때
- 자동 발송이 실패했을 때

본체는 utils/daily_digest.py. 여기서는 인자 파싱과 관리자 확인만 한다.

멱등하다 — 몇 번을 돌려도 시트에 아무것도 쓰지 않는다(읽기 전용 + DM 발송).
같은 일차를 두 번 돌리면 타래가 두 개 온다. 그뿐이다.
"""

import os
import sys
from typing import Any, List, Optional

# 경로 설정
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))

from utils.imports import *  # noqa: F401,F403  (register_command, BaseCommand, CommandContext, CommandResponse, config, logger 등)
from utils.dm_guard import dm_only


@register_command(
    name="일일보고",
    aliases=["일일 보고", "digest", "보고서"],
    description=(
        "그날의 사건을 모아 소문 제작용 보고서를 DM 타래로 보냅니다. (관리자 전용) "
        "일차를 생략하면 현재 일차."
    ),
    examples=["[일일보고]", "[일일보고/3]"],
    category="관리",
    admin_only=True,
    requires_sheets=True,
    requires_api=False,
)
class DailyDigestCommand(BaseCommand):
    """일일보고 수동 트리거."""

    @staticmethod
    def get_supported_keywords() -> List[str]:
        return ["일일보고", "일일 보고", "digest", "보고서"]

    def __init__(self, sheets_manager=None, api=None, **kwargs):
        super().__init__(sheets_manager, api, **kwargs)
        self.system_sheets_manager = kwargs.get('system_sheets_manager')
        self.investigation_sheets_manager = kwargs.get('investigation_sheets_manager')

    @dm_only
    def execute(self, context: CommandContext) -> CommandResponse:
        day = None
        raw = ''
        keywords = context.keywords or []
        if len(keywords) > 1:
            raw = str(keywords[1]).strip()
        if raw:
            try:
                day = int(float(raw))
            except (ValueError, TypeError):
                return CommandResponse.create_error(
                    f"'{raw}'은(는) 일차가 아닙니다. 예) [일일보고/3] 또는 [일일보고]"
                )
            if day < 1:
                return CommandResponse.create_error("일차는 1 이상이어야 합니다.")

        if self.system_sheets_manager is None:
            return CommandResponse.create_error(
                "시스템 시트에 연결되어 있지 않아 보고서를 만들 수 없습니다."
            )

        try:
            from utils import daily_digest
            result = daily_digest.run_daily_digest(
                sheets_manager=self.sheets_manager,
                system_sheets_manager=self.system_sheets_manager,
                api=self.api,
                investigation_sheets_manager=self.investigation_sheets_manager,
                day=day,
            )
        except Exception as e:
            logger.error(f"[일일보고] 생성 실패: {e}", exc_info=True)
            return CommandResponse.create_error("보고서 생성 중 오류가 발생했습니다.", error=e)

        if result.get('day') is None:
            return CommandResponse.create_error(
                "현재 일차를 알 수 없습니다. '설정' 시트를 확인하거나 [일일보고/3]처럼 "
                "일차를 지정해 주세요."
            )

        total = result.get('total', 0)
        sent = result.get('sent', 0)
        if total == 0:
            return CommandResponse.create_success(
                f"{result['day']}일차 — 보고할 사건이 없습니다."
            )
        if sent == 0:
            return CommandResponse.create_error(
                f"{result['day']}일차 보고서({total}통)를 만들었지만 발송에 실패했습니다. "
                f"DIGEST_RECIPIENT_ID/SYSTEM_ADMIN_ID 설정과 로그를 확인해 주세요."
            )
        if sent < total:
            return CommandResponse.create_error(
                f"{result['day']}일차 보고서 {total}통 중 {sent}통만 발송됐습니다. "
                f"타래가 중간에 끊겼습니다 — 로그를 확인해 주세요."
            )
        return CommandResponse.create_success(
            f"{result['day']}일차 보고서를 {total}통의 타래로 보냈습니다."
        )

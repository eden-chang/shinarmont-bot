"""
[조사 개방/일차] 관리자 명령어 (가이드 §6.3-4)

놓친 개방을 확인 후 수동으로 실행하거나, 예정 일차를 미리 개방할 때 쓴다.
스케줄러가 놓친 실행을 감지하면 관리자에게 이 명령을 안내한다.

- 관리자(config.SYSTEM_ADMIN_ID) 전용
- 인자 없이 [조사 개방]만 쓰면 개방 일정과 실행 상태를 보여 준다
- 개방 자체는 멱등하다(이미 가능인 행은 건너뜀). 이미 실행된 일차를 다시 실행할 수 있고,
  이 경우 운영진이 수동으로 닫아 둔 행을 되켤 수 있으니 안내를 덧붙인다.
"""

import os
import sys
from typing import List

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))

try:
    from utils.logging_config import logger
    from utils.investigation_open import (
        is_executed, load_state, pending_opens, run_open, scheduled_opens,
    )
    from commands.base_command import CommandContext, CommandResponse
    from commands.registry import register_command
    from commands.investigate.base_investigate import BaseInvestigateCommand
except ImportError as e:  # pragma: no cover
    import logging
    logger = logging.getLogger('investigate.open_admin')
    logger.error(f"[조사 개방] 필수 모듈 임포트 실패: {e}")
    raise


@register_command(
    name="조사 개방",
    aliases=["조사개방"],
    description="[관리자] 지정한 일차의 조사 포인트를 개방합니다.",
    category="조사",
    examples=["[조사 개방]", "[조사 개방/1주-4일차]"],
    requires_sheets=True,
    requires_api=False,
    admin_only=True,
)
class OpenAdminCommand(BaseInvestigateCommand):
    """조사 개방 수동 트리거 (관리자 전용)."""

    @staticmethod
    def get_supported_keywords() -> List[str]:
        return ["조사 개방", "조사개방"]

    def execute(self, context: CommandContext) -> CommandResponse:
        # 관리자 권한은 BaseCommand.validate_context가 admin_only 메타데이터로 강제한다
        # (라우터가 execute_with_lifecycle → pre_execute → validate_context 순으로 호출).
        if self.investigation_sheets_manager is None:
            # 이 상태로 run_open을 부르면 0건을 돌려줘 "0건 개방 완료"라는
            # 성공 응답이 나간다. 실패로 명시한다.
            return CommandResponse.create_error(
                "조사 스프레드시트에 연결되어 있지 않습니다. "
                "INVESTIGATION_ENABLED / INVESTIGATION_SHEET_ID 설정과 "
                "서비스 계정 공유 상태를 확인해 주세요."
            )

        label = self.parse_argument(context.keywords)
        if not label:
            return CommandResponse.create_success(self._status_text())

        known = {name for name, _ in scheduled_opens()}
        if label not in known:
            return CommandResponse.create_error(
                f"'{label}'은(는) 개방 일정에 없는 일차입니다.\n\n{self._status_text()}"
            )

        already = is_executed(label)
        try:
            result = run_open(self.investigation_sheets_manager, label, api=self.api)
        except Exception as e:
            logger.error(f"[조사 개방] '{label}' 수동 개방 실패: {e}", exc_info=True)
            return CommandResponse.create_error(f"'{label}' 개방 중 오류가 발생했습니다: {e}")

        failures = result.get('failures') or []
        lines = [
            f"{label} 조사 {result['opened']}건 개방"
            + (" 완료" if not failures else " (부분 실패)"),
            f"이전 버전 종료: {result['closed']}건 / 시트 {result['sheets']}개",
        ]
        if failures:
            lines.append(f"⚠️ 실패: {', '.join(failures)}")
            lines.append("실행 기록을 남기지 않았습니다. 원인을 고친 뒤 다시 실행해 주세요.")
        if already:
            lines.append(
                "※ 이미 실행 기록이 있는 일차입니다. 수동으로 닫아 둔 행이 있었다면 "
                "다시 열렸을 수 있으니 확인해 주세요."
            )
        if failures:
            return CommandResponse.create_error("\n".join(lines), data=result)
        return CommandResponse.create_success("\n".join(lines), data=result)

    def _status_text(self) -> str:
        """개방 일정과 실행 상태."""
        state = load_state()
        pending = {label for label, _ in pending_opens()}

        lines = ["조사 개방 일정", ""]
        for label, when in scheduled_opens():
            if label in state:
                mark = f"완료 ({state[label][:16].replace('T', ' ')})"
            elif label in pending:
                mark = "⚠️ 미실행 (지난 일정)"
            else:
                mark = "예정"
            lines.append(f"- {label} / {when.strftime('%Y-%m-%d %H:%M')} → {mark}")

        if pending:
            lines.extend(["", f"[조사 개방/{sorted(pending)[0]}]으로 수동 개방할 수 있습니다."])
        return "\n".join(lines)

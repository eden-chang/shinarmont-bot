"""
조사 명령어 공통 베이스 (commands/investigate/base_investigate.py)

[장소 목록] / [진입] / [조사]가 공유하는 의존성·판정·문구 처리를 모은다.

응답 규약:
- 세계관 톤의 '실패'(장소가 없다, 조사할 게 없다, 횟수 초과 등)는 정상적인 게임 진행이므로
  `ok()`로 반환한다. `create_error`는 시트 장애 같은 진짜 오류에만 쓴다.
  (스트림 핸들러는 둘 다 답글로 보내지만, 오류는 ❌로 로깅되고 통계에 잡힌다.)
- 응답의 '@계정' 프리픽스는 스트림 핸들러가 붙인다. 명령어가 직접 붙이지 않는다.
"""

import os
import sys
from abc import abstractmethod
from typing import List, Optional

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))

try:
    from config.settings import config
    from utils.logging_config import logger
    from utils.investigation_actor import Actor, load_actor
    # 모듈로 참조한다(`from ... import notify_admin` 금지).
    # 이름을 직접 바인딩하면 테스트에서 모듈 속성을 갈아끼워도 이 참조가 바뀌지 않는다.
    from utils import investigation_notify
    from utils.investigation_sheet import (
        InvestigationRepo, InvestigationDataError, detect_version_conflicts,
    )
    from commands.base_command import BaseCommand, CommandContext, CommandResponse
except ImportError as e:  # pragma: no cover
    import logging
    logger = logging.getLogger('investigate.base')
    logger.error(f"[조사] 필수 모듈 임포트 실패: {e}")
    raise


class BaseInvestigateCommand(BaseCommand):
    """조사 계열 명령어의 공통 베이스.

    `execute`를 추상으로 선언해 이 클래스 자체가 추상이 되게 한다.
    레지스트리가 `inspect.isabstract`로 베이스 클래스를 걸러내므로,
    이게 없으면 'baseinvestigate'라는 유령 명령어가 등록된다
    (BaseCommand.execute는 @abstractmethod가 아니라 상속만으로는 추상이 되지 않는다).
    BaseStoreCommand도 같은 방식으로 제외된다.
    """

    @abstractmethod
    def execute(self, context: CommandContext) -> CommandResponse:
        """서브클래스가 구현한다."""
        raise NotImplementedError

    def __init__(self, sheets_manager=None, api=None, **kwargs):
        super().__init__(sheets_manager, api, **kwargs)
        self.investigation_sheets_manager = kwargs.get('investigation_sheets_manager')
        # 행동로그는 **시스템 시트**에 있다(조사 시트가 아니다).
        self.system_sheets_manager = kwargs.get('system_sheets_manager')
        # 여기서는 경고하지 않는다. 매니저가 없는 게 **정상인 경우가 대부분**이라
        # 경고를 띄우면 기동 로그가 거짓 경보로 뒤덮여 진짜 문제를 가린다:
        #   · 레지스트리가 검증용으로 버리는 인스턴스를 만든다(registry.can_create_instance:
        #     `command_class(None, None)`) — 5슬롯 × 4명령어 × 2회
        #   · 조사를 쓰지 않는 슬롯(@SYSTEM·@STORY·@DOCTOR·@BAR)도 모든 명령어를 등록한다
        # 진짜 문제(=조사를 실행하는데 매니저가 없다)는 아래 repo에서 잡는다.

    # -- 의존성 ------------------------------------------------------------
    @property
    def repo(self) -> InvestigationRepo:
        """조사 시트 저장소. 매니저가 없으면 여기서 막는다.

        None을 그대로 넘기면 시트 접근 순간 AttributeError가 나고, 이용자에겐
        "처리 중 오류"만 뜬다. 원인(설정 누락)이 로그 어디에도 안 남는다.
        """
        if self.investigation_sheets_manager is None:
            logger.error(
                f"[조사] {type(self).__name__} 실행 — investigation_sheets_manager가 없다. "
                "조사 기능이 동작하지 않는다. "
                "`INVESTIGATION_ENABLED=True` / `INVESTIGATION_SHEET_ID` 설정을 확인할 것."
            )
            raise InvestigationDataError(
                'investigation_sheets_manager 미주입 — 조사 시트에 접근할 수 없다')
        return InvestigationRepo(self.investigation_sheets_manager)

    # -- 문구 --------------------------------------------------------------
    @staticmethod
    def msg(key: str) -> str:
        """config의 실패 응답 문구 세트에서 조회 (§3.4)."""
        return config.investigation_message(key)

    @classmethod
    def refuse(cls, key: str, **data) -> CommandResponse:
        """세계관 톤의 거절 응답 (정상 진행)."""
        return CommandResponse.create_success(cls.msg(key), data=data or None)

    # -- 행동로그 ----------------------------------------------------------
    def log_action(self, kind: str, actor_name: str, target: str, summary: str) -> None:
        """행동로그 1행. 실패해도 조사 자체를 막지 않는다(연출용 부가 기록).

        조사 시트의 `로그`와 역할이 다르다:
          · 조사 `로그` = 일일 제한 판정의 **기준**(없으면 제한이 무너진다)
          · `행동로그`  = GM 타임라인 + **소문 재료**(없어도 게임은 돈다)
        """
        if self.system_sheets_manager is None:
            logger.debug("[조사] system_sheets_manager 없음 - 행동로그 생략")
            return
        try:
            from utils import action_log
            action_log.append(
                self.system_sheets_manager,
                kind=kind,
                actor=actor_name,
                target=target,
                summary=summary,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[조사] 행동로그 기록 실패(무시): {e}")

    # -- 관리자 알림 -------------------------------------------------------
    def warn_admin(self, message: str) -> None:
        investigation_notify.notify_admin(message, api=self.api)

    def warn_admin_once(self, key: str, message: str) -> None:
        investigation_notify.notify_admin_once(key, message, api=self.api)

    def warn_version_conflicts(self, rows, name_column: str, scope: str) -> None:
        """버전 중복(같은 이름의 '가능' 행이 서로 다른 오픈 일자)을 관리자에게 경고.

        동작은 유지하되 경고만 보낸다. 버전 전환 누락 감지용 (§2.1 부가 규칙).
        """
        for name, days in detect_version_conflicts(rows, name_column):
            self.warn_admin_once(
                f"version-conflict:{scope}:{name}",
                f"버전 중복 경고 — {scope}의 '{name}'이(가) 서로 다른 오픈 일자"
                f"({', '.join(days)})로 동시에 '가능' 상태입니다. "
                f"버전 전환이 누락되지 않았는지 확인해 주세요.",
            )

    def warn_data(self, warnings: List[str], context: str) -> None:
        """시트 데이터 경고(수식 파싱 실패 등)를 관리자에게 전달."""
        for w in warnings or []:
            self.warn_admin(f"{context} — {w}")

    # -- 캐릭터 ------------------------------------------------------------
    def load_actor(self, user_id: str) -> Optional[Actor]:
        return load_actor(self.sheets_manager, user_id)

    # -- 판정 --------------------------------------------------------------
    def load_context(self, fresh_entry: bool = False):
        """진입 시트 + 예외 시트를 함께 읽는다.

        Returns:
            (entry_rows, exception_rows)
        """
        entry_rows = self.repo.entry_rows(fresh=fresh_entry)
        exception_rows = self.repo.exception_rows()
        return entry_rows, exception_rows

    def parse_argument(self, keywords: List[str]) -> str:
        """명령어 인자(장소명/포인트명)를 추출. 앞뒤 공백은 제거한다 (§3).

        인자가 없으면 빈 문자열.
        """
        if not keywords or len(keywords) < 2:
            return ''
        return str(keywords[1] or '').strip()

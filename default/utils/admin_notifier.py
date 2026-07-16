"""
관리자 알림 시스템
봇 프로세스 문제 발생 시 관리자에게 알림을 전송합니다.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class AdminNotifier:
    """
    관리자 알림 전송 클래스

    마스토돈 DM으로 관리자에게 알림을 전송합니다.
    """

    def __init__(self, mastodon_api=None, admin_id: str = '', enabled: bool = True):
        """
        Args:
            mastodon_api: 마스토돈 API 인스턴스 (선택)
            admin_id: 관리자 계정 ID
            enabled: 알림 활성화 여부
        """
        self.api = mastodon_api
        self.admin_id = admin_id
        self.enabled = enabled

        if enabled and not admin_id:
            logger.warning("관리자 알림이 활성화되었지만 SYSTEM_ADMIN_ID가 설정되지 않았습니다.")

        logger.info(f"AdminNotifier 초기화: enabled={enabled}, admin_id={admin_id}")

    def send_bot_crash_alert(
        self,
        bot_name: str,
        bot_id: str,
        message: str,
        restart_count: int = 0,
        uptime_seconds: float = 0,
        pid: Optional[int] = None
    ) -> bool:
        """
        봇 크래시 알림 전송

        Args:
            bot_name: 봇 이름
            bot_id: 봇 ID
            message: 알림 메시지
            restart_count: 재시작 횟수
            uptime_seconds: 가동 시간 (초)
            pid: 프로세스 ID

        Returns:
            bool: 전송 성공 여부
        """
        if not self.enabled:
            logger.debug("관리자 알림이 비활성화되어 있습니다.")
            return False

        if not self.admin_id:
            logger.warning("관리자 ID가 설정되지 않아 알림을 전송할 수 없습니다.")
            return False

        # 가동 시간 포맷팅
        uptime_minutes = int(uptime_seconds // 60)
        uptime_secs = int(uptime_seconds % 60)
        uptime_str = f"{uptime_minutes}분 {uptime_secs}초"

        # 알림 메시지 구성
        alert_text = (
            f"🚨 [봇 매니저 알림]\n\n"
            f"봇: {bot_name} ({bot_id})\n"
            f"상태: {message}\n"
            f"가동 시간: {uptime_str}\n"
            f"재시작 횟수: {restart_count}\n"
            f"PID: {pid if pid else 'N/A'}"
        )

        # 로그에 알림 내용 기록
        logger.critical(f"[ALERT] {bot_name}: {message}")

        # 마스토돈 API가 있으면 DM 전송
        if self.api:
            try:
                self.api.status_post(
                    status=f"@{self.admin_id} {alert_text}",
                    visibility='direct'
                )
                logger.info(f"✅ 관리자 알림 전송 완료: @{self.admin_id}")
                return True
            except Exception as e:
                logger.error(f"❌ 관리자 알림 전송 실패: {e}")
                return False
        else:
            logger.warning("알림용 API 인스턴스가 없어 DM을 전송할 수 없습니다. 로그만 기록됩니다.")
            return False

    def send_bot_restart_success(
        self,
        bot_name: str,
        bot_id: str,
        restart_count: int
    ) -> bool:
        """
        봇 재시작 성공 알림 전송

        Args:
            bot_name: 봇 이름
            bot_id: 봇 ID
            restart_count: 재시작 횟수

        Returns:
            bool: 전송 성공 여부
        """
        if not self.enabled or not self.admin_id or not self.api:
            return False

        try:
            alert_text = (
                f"✅ [봇 재시작 성공]\n\n"
                f"봇: {bot_name} ({bot_id})\n"
                f"재시작 횟수: {restart_count}"
            )

            self.api.status_post(
                status=f"@{self.admin_id} {alert_text}",
                visibility='direct'
            )
            logger.info(f"✅ 재시작 성공 알림 전송: {bot_name}")
            return True
        except Exception as e:
            logger.error(f"❌ 재시작 성공 알림 전송 실패: {e}")
            return False

    def send_manager_start_alert(self, bot_count: int) -> bool:
        """
        매니저 시작 알림 전송

        Args:
            bot_count: 시작된 봇 개수

        Returns:
            bool: 전송 성공 여부
        """
        if not self.enabled or not self.admin_id or not self.api:
            return False

        try:
            alert_text = (
                f"🤖 [봇 매니저 시작]\n\n"
                f"시작된 봇: {bot_count}개\n"
                f"자동 재시작: 활성화\n"
                f"모니터링: 활성화"
            )

            self.api.status_post(
                status=f"@{self.admin_id} {alert_text}",
                visibility='direct'
            )
            logger.info("✅ 매니저 시작 알림 전송 완료")
            return True
        except Exception as e:
            logger.error(f"❌ 매니저 시작 알림 전송 실패: {e}")
            return False

    def send_manager_stop_alert(self) -> bool:
        """
        매니저 종료 알림 전송

        Returns:
            bool: 전송 성공 여부
        """
        if not self.enabled or not self.admin_id or not self.api:
            return False

        try:
            alert_text = "🛑 [봇 매니저 종료]\n\n모든 봇이 종료되었습니다."

            self.api.status_post(
                status=f"@{self.admin_id} {alert_text}",
                visibility='direct'
            )
            logger.info("✅ 매니저 종료 알림 전송 완료")
            return True
        except Exception as e:
            logger.error(f"❌ 매니저 종료 알림 전송 실패: {e}")
            return False

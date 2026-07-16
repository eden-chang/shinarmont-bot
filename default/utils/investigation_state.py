"""
조사 상태 관리자 — 캐릭터별 '현재 위치' (가이드 §5.1)

- 캐릭터별로 마지막으로 진입한 장소를 기억한다. 새로 진입할 때마다 덮어쓴다.
- 만료 시간은 두지 않되, 게임 일차가 바뀌면(00:00 KST) 초기화한다.
  → 항목마다 진입 날짜를 함께 보관하고, 날짜가 다르면 없는 것으로 취급한다.
  스케줄러의 일괄 초기화에 의존하지 않으므로 자정을 넘겨도 항상 정확하다.
- 영속화: 별도 저장소를 만들지 않고 `로그` 시트의 최근 진입 기록에서 복원한다
  (`get_or_restore`). 봇 재시작 후에도 [조사]가 정상 동작한다.
"""

import threading
from datetime import date, datetime
from typing import Callable, Dict, Optional, Tuple

import pytz

try:
    from utils.logging_config import logger
except ImportError:  # pragma: no cover
    import logging
    logger = logging.getLogger('investigation_state')

KST = pytz.timezone('Asia/Seoul')


def _today() -> date:
    """게임 일차 기준 오늘 (일차 전환 = 00:00 KST = 달력 날짜)."""
    return datetime.now(KST).date()


class InvestigationStateManager:
    """사용자별 조사 장소 상태를 관리하는 스레드 안전 싱글톤."""

    def __init__(self) -> None:
        # user_id -> (진입 날짜, 장소명)
        self._state: Dict[str, Tuple[date, str]] = {}
        self._lock = threading.Lock()

    def enter(self, user_id: str, location: str) -> None:
        """사용자의 현재 장소를 기록(덮어쓰기)합니다."""
        with self._lock:
            self._state[user_id] = (_today(), location)
        logger.debug(f"[조사] 진입 상태 저장: user={user_id}, 장소={location}")

    def get_location(self, user_id: str) -> Optional[str]:
        """사용자의 현재 장소명. 기록이 없거나 일차가 바뀌었으면 None."""
        today = _today()
        with self._lock:
            entry = self._state.get(user_id)
            if entry is None:
                return None
            entered_on, location = entry
            if entered_on != today:
                # 일차가 바뀌었다 → 위치 초기화 (§5.1)
                del self._state[user_id]
                logger.debug(f"[조사] 일차 변경으로 진입 상태 만료: user={user_id}")
                return None
            return location

    def get_or_restore(
        self,
        user_id: str,
        loader: Callable[[], Optional[str]],
    ) -> Optional[str]:
        """메모리에 없으면 `loader`로 복원한다 (재시작 대비, §5.1).

        Args:
            loader: 로그 시트에서 당일 최근 진입 장소를 읽어 오는 콜러블.
                    메모리에 없을 때만 호출되므로 평소 시트 읽기 비용이 없다.
        """
        location = self.get_location(user_id)
        if location is not None:
            return location

        try:
            restored = loader()
        except Exception as e:
            logger.error(f"[조사] 진입 상태 복원 실패: user={user_id}: {e}", exc_info=True)
            return None

        if restored:
            self.enter(user_id, restored)
            logger.info(f"[조사] 로그에서 진입 상태 복원: user={user_id}, 장소={restored}")
        return restored

    def clear(self, user_id: str) -> None:
        """사용자의 조사 상태를 제거합니다."""
        with self._lock:
            removed = self._state.pop(user_id, None)
        if removed is not None:
            logger.debug(f"[조사] 진입 상태 해제: user={user_id} (이전={removed[1]})")

    def clear_all(self) -> None:
        """전체 초기화 (일차 전환 스케줄러/테스트용)."""
        with self._lock:
            count = len(self._state)
            self._state.clear()
        if count:
            logger.info(f"[조사] 진입 상태 전체 초기화: {count}건")

    def snapshot(self) -> Dict[str, str]:
        """디버깅용 현재 상태 스냅샷 (복사본)."""
        with self._lock:
            return {uid: loc for uid, (_, loc) in self._state.items()}


_state_manager: Optional[InvestigationStateManager] = None
_instance_lock = threading.Lock()


def get_investigation_state() -> InvestigationStateManager:
    """전역 `InvestigationStateManager` 싱글톤을 반환합니다."""
    global _state_manager
    if _state_manager is None:
        with _instance_lock:
            if _state_manager is None:
                _state_manager = InvestigationStateManager()
                logger.info("[조사] InvestigationStateManager 초기화 완료")
    return _state_manager

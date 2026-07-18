"""답글-스레드 라우팅 레지스트리 (디스크 영속, 슬롯 로컬).

봇이 보낸 답글의 status_id를 "그 답글이 속한 대화 세션 정보"에 매핑한다.
사용자가 그 답글에 (대괄호 없이) 이어서 답글을 달면, 스트림 핸들러가 이 레지스트리로
어느 명령어·세션의 이어가기인지 판별해 라우팅한다.

흐름:
  1) 명령어가 응답을 만들며 stage(user_id, keyword, session_key)로 "전송되면 등록하라"고 예약.
  2) 핸들러가 전송 직후 commit(user_id, status_id)를 호출 → 예약을 실제 status_id에 확정.
  3) 다음 답글이 들어오면 핸들러가 resolve(reply_to_id)로 세션 정보를 조회.

영속성:
  확정된 매핑(status_id → 세션)은 **슬롯별 JSON 파일**에 저장된다.
  봇을 업데이트하려고 껐다 켜도 진행 중인 타래에 계속 답글을 달 수 있다.
  파일은 `state/{슬롯}/reply_threads.json` — 슬롯마다 분리한다(§utils/json_store.slot_path).

  `_pending`(전송 전 예약)은 **메모리에만** 둔다. 응답을 보내기 직전의 찰나라
  그 사이에 죽으면 답글 자체가 나가지 않았으므로 복원할 대상도 없다.

- 멀티프로세스: 각 봇 슬롯은 별도 프로세스이며 파일도 분리된다(의무실 스레드는 @DOCTOR 슬롯에만 존재).
- size 상한으로 오래된 매핑을 자동 폐기.
"""

import threading
from typing import Any, Dict, Optional

from utils.json_store import JsonStore, slot_path

# status_id → 세션정보 매핑의 최대 보관 수(초과 시 오래된 것부터 폐기)
_MAX_ENTRIES = 5000

_STORE_FILENAME = 'reply_threads.json'


class _ReplyThreadRegistry:
    def __init__(self, store: Optional[JsonStore] = None) -> None:
        # `store or ...` 금지: JsonStore는 __len__이 있어 **빈 저장소가 falsy**다.
        self._store = store if store is not None else JsonStore(
            slot_path(_STORE_FILENAME), max_entries=_MAX_ENTRIES)
        self._pending: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def stage(self, user_id: str, keyword: str, session_key: str,
              participants: Optional[list] = None) -> None:
        """이번 응답이 전송되면 등록할 세션정보를 사용자별로 예약(마지막 예약이 우선).

        participants: 이 타래에 이어쓸 수 있는 계정(acct) 목록. 지정하면 그 목록에 든
            누구든(원작성자가 아니어도) 답글로 세션을 이어갈 수 있다(예: 비밀 대화의 두 참여자).
        """
        entry = {
            'keyword': keyword,
            'user_id': str(user_id),
            'session_key': session_key,
        }
        if participants:
            entry['participants'] = [str(p) for p in participants]
        with self._lock:
            self._pending[str(user_id)] = entry

    def link(self, status_id: Any, keyword: str, user_id: str, session_key: str,
             participants: Optional[list] = None) -> bool:
        """특정 status_id를 세션에 **즉시** 매핑(stage→commit 우회).

        봇이 답글을 보내지 않는 턴에도(침묵 진행) 사용자가 방금 올린 툿에 다음 답글이
        달리면 세션으로 라우팅되게 하려고 쓴다. 비밀 대화의 이어가기에 필요.
        """
        if status_id is None:
            return False
        entry = {
            'keyword': keyword,
            'user_id': str(user_id),
            'session_key': session_key,
        }
        if participants:
            entry['participants'] = [str(p) for p in participants]
        self._store.set(str(status_id), entry)
        return True

    def commit(self, user_id: str, status_id: Any) -> bool:
        """전송된 status_id에 예약을 확정. 예약이 없으면 False(다른 명령어 → 무시)."""
        if status_id is None:
            return False
        with self._lock:
            entry = self._pending.pop(str(user_id), None)
        if entry is None:
            return False
        # 확정된 매핑만 디스크에 남긴다(JsonStore가 자체 락 + 원자적 저장).
        self._store.set(str(status_id), entry)
        return True

    def discard(self, user_id: str) -> None:
        """대기 중인 예약을 취소(대화 종료 등으로 더 이상 스레드를 잇지 않을 때)."""
        with self._lock:
            self._pending.pop(str(user_id), None)

    def resolve(self, status_id: Any) -> Optional[Dict[str, Any]]:
        """들어온 답글의 reply_to_id로 세션정보 조회(사본 반환)."""
        if status_id is None:
            return None
        entry = self._store.get(str(status_id))
        return dict(entry) if entry else None

    def clear(self) -> None:
        with self._lock:
            self._pending.clear()
        self._store.clear()


_registry: Optional[_ReplyThreadRegistry] = None
_registry_lock = threading.Lock()


def _get_registry() -> _ReplyThreadRegistry:
    """지연 초기화 — 임포트 시점에 파일을 만들지 않는다(테스트가 경로를 갈아끼울 수 있게)."""
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = _ReplyThreadRegistry()
    return _registry


def set_registry(registry: Optional[_ReplyThreadRegistry]) -> None:
    """테스트용 주입/초기화."""
    global _registry
    with _registry_lock:
        _registry = registry


def stage(user_id: str, keyword: str, session_key: str,
          participants: Optional[list] = None) -> None:
    _get_registry().stage(user_id, keyword, session_key, participants)


def link(status_id: Any, keyword: str, user_id: str, session_key: str,
         participants: Optional[list] = None) -> bool:
    return _get_registry().link(status_id, keyword, user_id, session_key, participants)


def commit(user_id: str, status_id: Any) -> bool:
    return _get_registry().commit(user_id, status_id)


def discard(user_id: str) -> None:
    _get_registry().discard(user_id)


def resolve(status_id: Any) -> Optional[Dict[str, Any]]:
    return _get_registry().resolve(status_id)


def clear() -> None:
    _get_registry().clear()

"""
utils/json_store.py — 디스크 영속 키-값 저장소 (JSON)

봇을 업데이트하려고 껐다 켜도 **진행 중인 대화가 끊기지 않도록** 인메모리 상태를
JSON 파일로 영속화한다. 쓰는 곳:
- `utils/reply_threads.py`      — 답글 status_id → 세션 매핑 (bare 답글 이어가기)
- `commands/shinarmont/infirmary_command.py` — 의무실 진료 대화 세션
- `commands/shinarmont/talk_command.py`      — 비밀 대화 세션

설계:
- **변경 시마다 저장**한다. 언제 죽을지 모르므로 주기 저장은 의미가 없다.
  대화 한 턴에 파일 하나 쓰기(수 KB) — 마스토돈 왕복보다 훨씬 싸다.
- **원자적 쓰기**(임시파일 + `os.replace`). 쓰는 도중 죽어도 원본이 남는다.
- 스레드 안전(`threading.Lock`).
- `max_entries`로 오래된 항목부터 폐기(LRU). 무한 증식 방지.
- 파일이 깨졌거나 없으면 **빈 상태로 시작**한다. 세션 유실이 봇 기동 실패보다 낫다.

멀티프로세스 주의:
    슬롯마다 별도 프로세스이므로 **파일을 슬롯별로 분리**해야 한다
    (@DOCTOR의 진료 세션을 @TOWN이 건드릴 일이 없다).
    같은 파일을 두 슬롯이 함께 쓰면 마지막 쓰기가 이긴다 — 그런 용도로 쓰지 말 것.
"""

import json
import os
import sys
import tempfile
import threading
from collections import OrderedDict
from typing import Any, Callable, Dict, Optional

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

try:
    from utils.logging_config import logger
except ImportError:  # pragma: no cover
    import logging
    logger = logging.getLogger('utils.json_store')

try:
    from config.settings import config
except ImportError:  # pragma: no cover
    config = None


def resolve_path(path: str) -> str:
    """상대 경로를 프로젝트 루트 기준으로 해석."""
    if os.path.isabs(path):
        return path
    base = getattr(config, 'BASE_DIR', None)
    return os.path.join(str(base), path) if base else path


def slot_id() -> str:
    """현재 봇 슬롯 식별자 (파일명에 쓸 수 있게 정규화)."""
    raw = (getattr(config, 'BOT_NAME', '') or getattr(config, 'BOT_ID', '') or 'single')
    safe = ''.join(c for c in str(raw) if c.isalnum() or c in '-_')
    return safe or 'single'


def slot_path(filename: str, directory: str = 'state') -> str:
    """슬롯별로 분리된 상태 파일 경로. 예: state/DOCTOR/doctor_sessions.json

    **왜 슬롯별로 나누는가**: 5개 슬롯은 별도 프로세스인데 각자 파일 전체를
    인메모리로 들고 통째로 덮어쓴다. 같은 파일을 공유하면 한 슬롯의 저장이
    다른 슬롯의 기록을 지운다(마지막 쓰기가 이김).
    세션 상태는 슬롯 전용(의무실 세션은 @DOCTOR에만 존재)이라 파일을 나누면
    프로세스 간 조율 없이 문제가 사라진다.
    """
    return resolve_path(os.path.join(directory, slot_id(), filename))


class JsonStore:
    """JSON 파일에 얹은 스레드 안전 키-값 저장소."""

    def __init__(self, path: str, max_entries: Optional[int] = None):
        """
        Args:
            path: 저장 파일 경로(상대 경로면 프로젝트 루트 기준).
            max_entries: 최대 항목 수. 초과 시 **오래 손대지 않은 것부터** 폐기.
        """
        self.path = resolve_path(path)
        self.max_entries = max_entries
        self._data: "OrderedDict[str, Any]" = OrderedDict()
        self._lock = threading.Lock()
        self._load()

    # -- 파일 ---------------------------------------------------------------
    def _load(self) -> None:
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._data = OrderedDict(data)
                logger.info(f"[json_store] 복원: {self.path} ({len(self._data)}건)")
            else:
                logger.warning(f"[json_store] 최상위가 객체가 아님 - 빈 상태로 시작: {self.path}")
        except (OSError, ValueError) as e:
            # 깨진 파일 때문에 봇이 못 뜨는 것보다 세션을 잃는 편이 낫다
            logger.error(f"[json_store] 로드 실패 - 빈 상태로 시작 ({self.path}): {e}")
            self._data = OrderedDict()

    def _save_locked(self) -> None:
        """호출자가 이미 락을 쥐고 있어야 한다."""
        try:
            directory = os.path.dirname(self.path) or '.'
            os.makedirs(directory, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=directory, suffix='.tmp')
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as f:
                    json.dump(self._data, f, ensure_ascii=False)
                os.replace(tmp, self.path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        except (OSError, TypeError, ValueError) as e:
            # 저장 실패해도 인메모리 상태는 살아 있다 → 이번 프로세스는 계속 동작
            logger.error(f"[json_store] 저장 실패 ({self.path}): {e}")

    def _prune_locked(self) -> None:
        if not self.max_entries:
            return
        while len(self._data) > self.max_entries:
            self._data.popitem(last=False)

    # -- API ----------------------------------------------------------------
    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.get(str(key), default)

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            k = str(key)
            self._data[k] = value
            self._data.move_to_end(k)
            self._prune_locked()
            self._save_locked()

    def delete(self, key: str) -> Any:
        with self._lock:
            removed = self._data.pop(str(key), None)
            if removed is not None:
                self._save_locked()
            return removed

    def mutate(self, key: str, fn: Callable[[Any], Any], default: Any = None) -> Any:
        """읽기-수정-쓰기를 락 안에서 원자적으로 수행한다.

        `fn`이 None을 반환하면 값을 지운다. 그 외에는 반환값을 저장한다.
        대화 세션처럼 '읽어서 고치고 다시 넣는' 갱신에 쓴다.
        """
        with self._lock:
            k = str(key)
            current = self._data.get(k, default)
            updated = fn(current)
            if updated is None:
                self._data.pop(k, None)
            else:
                self._data[k] = updated
                self._data.move_to_end(k)
                self._prune_locked()
            self._save_locked()
            return updated

    def items(self) -> Dict[str, Any]:
        """전체 사본(삽입/갱신 순서 유지)."""
        with self._lock:
            return dict(self._data)

    def keys(self) -> list:
        with self._lock:
            return list(self._data.keys())

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self._save_locked()

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)

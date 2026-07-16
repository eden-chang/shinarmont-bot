"""
봇 내부 상태 관리자 (game_state)

봇 내부의 일일/캐릭터 상태(오늘대화여부·오늘고발·오늘의무실·
어제대화상대·오늘대화상대 등)를 `state/{슬롯}/game_state.json`에 디스크 영속합니다.

(출석 1일1회는 여기가 아니라 `관리` 시트 `출석` 컬럼이 판정합니다 —
 commands/store/attendance_command.py. 추적·조사 카운터도 `관리` 시트에 있습니다.)

## 일일 키는 **날짜 스탬프**로 관리한다 (스케줄러 의존 없음)

`오늘*` 키는 값과 함께 **기록한 날짜(KST)** 를 저장하고, 읽을 때 날짜가 오늘이 아니면
없는 것으로 취급한다. 그래서 자정이 지나면 **아무도 리셋해 주지 않아도** 저절로 풀린다.

왜 이렇게 바꿨나 (2026-07-16, PoC로 재현한 실제 결함):
  - 예전엔 스케줄러(BOT1)가 00:00에 `reset_daily()`로 '오늘*'을 0으로 만들었다.
    그런데 스케줄러는 **BOT1 프로세스의 싱글톤만** 리셋한다. @STORY·@DOCTOR는
    별도 프로세스라 자기 메모리 값을 그대로 들고 있어 **이벤트 내내 [대화]·[고발]·
    [의무실]이 1회만 가능**했다(재시작 전까지).
  - 조사 일일 제한이 이미 '로그 시트의 일시'로 판정해 스케줄러와 무관하게 정확했다.
    같은 방식을 여기에도 적용한 것이다.

`어제대화상대`는 저장하지 않고 **`오늘대화상대`의 날짜에서 파생**한다.
어제 날짜로 기록된 대화상대가 곧 '어제대화상대'다. 이월 잡도 필요 없다.

## 파일은 슬롯별로 분리한다

5개 슬롯은 별도 프로세스인데 각자 파일 전체를 인메모리로 들고 **통째로 덮어쓴다**.
한 파일을 공유하면 @DOCTOR의 저장이 @STORY가 쓴 `오늘대화여부`를 지웠고,
**@STORY가 재시작하는 순간 [대화] 일일 제한이 풀렸다**(PoC로 재현).
슬롯별 파일로 나누면 조율 없이 문제가 사라진다(각 키는 한 슬롯만 쓴다).

- `threading.Lock`으로 스레드 안전을 보장합니다.
- 쓰기는 임시 파일 + `os.replace`로 원자적 교체합니다(중간 크래시 시 원본 보존).
- 모듈 로드 시 파일을 읽어 인메모리 상태를 복원합니다(파일 없으면 빈 dict).

계약(docs/코딩_계획.md §5):
    get(uid, key, default=None)
    set(uid, key, val)
    check_and_set(uid, key, limit) -> bool   # 현재값 >= limit 이면 False, 아니면 +1 후 True
    reset_daily()                            # 폐기(no-op) — 날짜 스탬프가 대신한다
    carry_talk_partner()                     # 폐기(no-op) — 어제대화상대는 파생된다
    get_game_state()                         # 전역 싱글톤
"""

import os
import json
import tempfile
import threading
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import pytz

try:
    from utils.logging_config import logger
except ImportError:  # pragma: no cover - 로깅 미구성 환경 폴백
    import logging
    logger = logging.getLogger('game_state')

try:
    from utils.json_store import slot_path
except ImportError:  # pragma: no cover
    slot_path = None


KST = pytz.timezone('Asia/Seoul')

# state/{슬롯}/game_state.json (프로젝트 루트 기준). utils/ 의 부모가 프로젝트 루트.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_STATE_DIR = os.path.join(_PROJECT_ROOT, 'state')

# 이월 대상 키 이름
_TODAY_TALK_KEY = '오늘대화상대'
_YESTERDAY_TALK_KEY = '어제대화상대'

# 날짜 스탬프 레코드의 필드
_VALUE_FIELD = 'v'
_DATE_FIELD = 'd'

# 일일 키 접두 — 이 접두로 시작하는 키만 날짜 스탬프를 붙인다
_DAILY_PREFIX = '오늘'


def _default_state_path() -> str:
    """슬롯별 상태 파일 경로. 슬롯 정보를 못 얻으면 기존 경로로 폴백."""
    if slot_path is not None:
        try:
            return slot_path('game_state.json')
        except Exception:
            pass
    return os.path.join(_DEFAULT_STATE_DIR, 'game_state.json')


def _today() -> str:
    """게임 일차 경계는 00:00 KST = 달력 날짜."""
    return datetime.now(KST).strftime('%Y-%m-%d')


def _yesterday() -> str:
    """'오늘'에서 파생한다 — 둘이 따로 시계를 보면 자정 근처에서 어긋날 수 있다."""
    return (datetime.strptime(_today(), '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')


def is_daily_key(key: str) -> bool:
    return str(key or '').startswith(_DAILY_PREFIX)


def _stamp(value: Any) -> Dict[str, Any]:
    return {_VALUE_FIELD: value, _DATE_FIELD: _today()}


def _read_stamped(record: Any, on_date: str, default: Any = None) -> Any:
    """날짜 스탬프 레코드에서 `on_date`에 기록된 값을 꺼낸다. 아니면 default.

    구버전 파일(스탬프 없는 raw 값)은 **날짜를 알 수 없으므로 만료로 본다**.
    일일 제한이 한 번 더 풀리는 쪽이, 영원히 잠기는 쪽보다 안전하다.
    """
    if isinstance(record, dict) and _DATE_FIELD in record:
        if record.get(_DATE_FIELD) == on_date:
            return record.get(_VALUE_FIELD, default)
        return default
    return default


class GameStateManager:
    """봇 내부 상태를 JSON에 영속하는 스레드 안전 매니저."""

    def __init__(self, path: Optional[str] = None) -> None:
        self._path = path or _default_state_path()
        self._lock = threading.RLock()
        self._state: Dict[str, Dict[str, Any]] = {}
        self._load()

    # ------------------------------------------------------------------
    # 내부: 로드/저장
    # ------------------------------------------------------------------
    def _legacy_path(self) -> Optional[str]:
        """슬롯 분리 이전의 공유 파일(`state/game_state.json`) 경로. 없으면 None.

        기본 경로를 쓸 때만 본다 — 테스트가 임시 경로를 주입하면 새어 들면 안 된다.
        슬롯 파일이 아직 없을 때 1회만 읽고, 첫 저장부터는 슬롯 파일에 쓴다.
        구 파일은 지우지 않는다(다른 슬롯도 같은 값을 이어받아야 한다).
        """
        shared = os.path.join(_DEFAULT_STATE_DIR, 'game_state.json')
        if self._path == shared or self._path != _default_state_path():
            return None
        return shared if os.path.exists(shared) else None

    def _load(self) -> None:
        """디스크에서 상태를 읽어 인메모리로 복원. 파일 없으면 빈 dict."""
        with self._lock:
            path = self._path
            if not os.path.exists(path):
                legacy = self._legacy_path()
                if legacy is None:
                    self._state = {}
                    return
                # 슬롯 분리 이전의 공유 파일에서 1회 이어받는다.
                # 안 그러면 '의무실방문횟수' 같은 누적값이 사라져 러셀이 다시 초진처럼 군다.
                logger.info(f"[game_state] 구 공유 파일에서 상태 이어받음: {legacy}")
                path = legacy
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    # uid -> dict 구조 보정
                    self._state = {
                        str(uid): dict(kv) if isinstance(kv, dict) else {}
                        for uid, kv in data.items()
                    }
                else:
                    logger.warning("[game_state] 예상치 못한 JSON 구조 → 빈 상태로 시작")
                    self._state = {}
            except (json.JSONDecodeError, OSError, ValueError) as e:
                logger.error(f"[game_state] 상태 파일 로드 실패({e}) → 빈 상태로 시작")
                self._state = {}

    def _save_locked(self) -> None:
        """(락 보유 상태에서) 임시 파일 + os.replace 로 원자적 저장."""
        directory = os.path.dirname(self._path) or '.'
        os.makedirs(directory, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(prefix='.game_state_', suffix='.tmp', dir=directory)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(self._state, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self._path)
        except Exception as e:
            logger.error(f"[game_state] 상태 저장 실패: {e}")
            # 임시 파일 정리
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass
            raise

    # ------------------------------------------------------------------
    # public API (계약)
    # ------------------------------------------------------------------
    def get(self, uid: str, key: str, default: Any = None) -> Any:
        """uid의 key 값을 반환. 없으면 default.

        - `오늘*` 키: **오늘 기록된 값**만 돌려준다. 어제 것이면 default(= 자동 만료).
        - `어제대화상대`: 저장하지 않고 `오늘대화상대`의 날짜에서 파생한다.
        - 그 외 키(누적값 등): 날짜와 무관하게 그대로.
        """
        with self._lock:
            kv = self._state.get(str(uid), {})

            if key == _YESTERDAY_TALK_KEY:
                # 어제 날짜로 기록된 '오늘대화상대'가 곧 어제대화상대다(이월 잡 불필요)
                value = _read_stamped(kv.get(_TODAY_TALK_KEY), _yesterday(), None)
                return value if value is not None else (default if default is not None else '')

            if is_daily_key(key):
                return _read_stamped(kv.get(key), _today(), default)

            return kv.get(key, default)

    def set(self, uid: str, key: str, val: Any) -> None:
        """uid의 key를 val로 설정하고 디스크에 저장.

        `오늘*` 키는 오늘 날짜 스탬프와 함께 저장된다.
        """
        with self._lock:
            if key == _YESTERDAY_TALK_KEY:
                # 파생값이라 직접 쓰지 않는다(써도 다음 읽기에서 무시되어 혼란만 준다)
                logger.warning(
                    "[game_state] '어제대화상대'는 '오늘대화상대'의 날짜에서 파생됩니다. "
                    "직접 설정은 무시합니다."
                )
                return
            self._state.setdefault(str(uid), {})[key] = (
                _stamp(val) if is_daily_key(key) else val
            )
            self._save_locked()

    def check_and_set(self, uid: str, key: str, limit: int) -> bool:
        """일일 제한 체크-후-증가.

        현재값 >= limit 이면 False(제한 초과, 변경 없음).
        아니면 현재값 + 1 로 갱신하고 True.

        `오늘*` 키는 어제 값을 0으로 보므로 자정이 지나면 저절로 다시 쓸 수 있다.
        """
        with self._lock:
            kv = self._state.setdefault(str(uid), {})
            raw = _read_stamped(kv.get(key), _today(), 0) if is_daily_key(key) else kv.get(key, 0)
            try:
                current = int(raw)
            except (TypeError, ValueError):
                current = 0
            if current >= limit:
                return False
            kv[key] = _stamp(current + 1) if is_daily_key(key) else (current + 1)
            self._save_locked()
            return True

    def reset_daily(self) -> None:
        """**폐기(no-op)** — 일일 키는 날짜 스탬프로 자동 만료된다.

        예전엔 스케줄러가 이걸 불러 '오늘*'을 0으로 만들었다. 그러나 스케줄러는
        BOT1 프로세스의 싱글톤만 리셋해서 @STORY·@DOCTOR에는 닿지 않았다
        (그 슬롯들은 자기 메모리 값을 그대로 들고 있었다).
        이제 읽는 시점에 날짜를 보고 판단하므로 리셋할 것이 없다.

        스케줄러가 아직 호출하므로 하위 호환을 위해 남겨 둔다.
        """
        logger.debug("[game_state] reset_daily는 폐기됨(날짜 스탬프로 자동 만료) - no-op")

    def carry_talk_partner(self) -> None:
        """**폐기(no-op)** — 어제대화상대는 오늘대화상대의 날짜에서 파생된다."""
        logger.debug("[game_state] carry_talk_partner는 폐기됨(파생으로 대체) - no-op")

    def purge_stale(self) -> int:
        """지난 날짜의 일일 키를 파일에서 지운다(용량 정리용, 판정과 무관).

        읽기가 이미 날짜로 거르므로 없어도 동작에는 지장이 없다.
        단 `오늘대화상대`는 **어제 것이 어제대화상대로 쓰이므로** 이틀 치를 남긴다.
        """
        keep = {_today(), _yesterday()}
        removed = 0
        with self._lock:
            for kv in self._state.values():
                for key in list(kv.keys()):
                    if not is_daily_key(key):
                        continue
                    record = kv[key]
                    if not isinstance(record, dict) or _DATE_FIELD not in record:
                        del kv[key]          # 구버전 raw 값 → 만료 취급이라 지운다
                        removed += 1
                    elif record.get(_DATE_FIELD) not in keep:
                        del kv[key]
                        removed += 1
            if removed:
                self._save_locked()
        return removed

    # ------------------------------------------------------------------
    # 보조
    # ------------------------------------------------------------------
    def snapshot(self) -> Dict[str, Dict[str, Any]]:
        """디버깅/테스트용 현재 상태 깊은 복사본."""
        with self._lock:
            return {uid: dict(kv) for uid, kv in self._state.items()}

    def reload(self) -> None:
        """디스크에서 상태를 다시 읽어 들입니다(테스트/외부 변경 반영)."""
        self._load()


_state_manager: Optional[GameStateManager] = None
_instance_lock = threading.Lock()


def get_game_state() -> GameStateManager:
    """전역 `GameStateManager` 싱글톤을 반환합니다."""
    global _state_manager
    if _state_manager is None:
        with _instance_lock:
            if _state_manager is None:
                _state_manager = GameStateManager()
                logger.info("[game_state] GameStateManager 초기화 완료")
    return _state_manager


# ----------------------------------------------------------------------
# 모듈 수준 편의 함수 (계약: 명령어가 이 이름으로 import 가능)
# ----------------------------------------------------------------------
def get(uid: str, key: str, default: Any = None) -> Any:
    return get_game_state().get(uid, key, default)


def set(uid: str, key: str, val: Any) -> None:  # noqa: A001 - 계약상 이름 고정
    get_game_state().set(uid, key, val)


def check_and_set(uid: str, key: str, limit: int) -> bool:
    return get_game_state().check_and_set(uid, key, limit)


def purge_stale() -> int:
    return get_game_state().purge_stale()


def reset_daily() -> None:
    get_game_state().reset_daily()


def carry_talk_partner() -> None:
    get_game_state().carry_talk_partner()

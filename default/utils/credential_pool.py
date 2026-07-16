"""
utils/credential_pool.py — 구글 서비스 계정 크레덴셜 풀 (용도별 할당 + 쿼터 소진 시 대여)

## 왜 필요한가

Google Sheets API 쿼터는 **프로젝트 단위**로 걸린다(읽기 300회/분, 사용자당 60회/분).
크레덴셜이 하나면 모든 슬롯·기능이 그 한 프로젝트의 쿼터를 나눠 쓴다.
`credentials/`의 계정들은 **서로 다른 프로젝트**라 나눠 쓰면 쿼터가 그만큼 늘어난다.

게다가 기존 `safe_execute`는 **429(쿼터 초과)를 재시도하지 않는다**(500/503만 재시도).
쿼터가 터지면 그대로 명령 실패다. 이 풀은 그때 **다른 크레덴셜을 빌려 재시도**한다.

## 동작

1. **용도별 할당**: 무거운 기능마다 주 크레덴셜을 지정한다(config).
   예) 기본 시트(관리/출석/소지금) / 시스템 시트(행동로그) / 조사 시트
2. **대여**: 주 크레덴셜이 쿼터에 걸리면 후보군에서 빌려 재시도한다.
   빌린 뒤에도 원래 주 크레덴셜은 쿨다운이 끝나면 다시 쓴다.
3. **접근 불가 자동 제외**: 시트에 공유되지 않은 계정은 첫 사용에서 걸러진다.
   (모든 계정이 모든 시트에 공유돼 있지는 않다)

## 주의

- **계정마다 스프레드시트에 공유(편집자)돼 있어야 한다.** 공유 안 된 계정은
  `report_no_access()`로 그 용도에서 영구 제외되며, 후보가 다 떨어지면 기능이 멈춘다.
- 슬롯(프로세스)마다 이 풀이 따로 존재한다. 쿨다운도 프로세스 로컬이다.
  한 슬롯이 쿼터에 걸렸다고 다른 슬롯이 알지는 못한다(각자 겪고 각자 우회한다).
"""

import os
import sys
import threading
import time
from typing import Dict, List, Optional

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

try:
    from utils.logging_config import logger
except ImportError:  # pragma: no cover
    import logging
    logger = logging.getLogger('utils.credential_pool')

try:
    from config.settings import config
except ImportError:  # pragma: no cover
    config = None


# 용도 식별자 (SheetsManager가 purpose로 넘긴다)
PURPOSE_MAIN = 'main'                    # 기본 시트: 관리(출석·소지금·스탯)·명단·상점
PURPOSE_SYSTEM = 'system'                # 시스템 시트: 행동로그·투표·추적·소문
PURPOSE_INVESTIGATION = 'investigation'  # 조사 시트: 진입·장소·예외·로그

_ALL_PURPOSES = (PURPOSE_MAIN, PURPOSE_SYSTEM, PURPOSE_INVESTIGATION)

# 쿼터 소진으로 판단된 크레덴셜을 쉬게 하는 시간(초). 기본 5분(운영 결정 2026-07-16).
# Sheets 쿼터 자체는 분 단위로 회복되지만, 짧게 잡으면 회복되자마자 다시 몰려가
# 곧바로 또 429를 맞는다. 5분 쉬게 하고 그 뒤 주 크레덴셜로 돌아간다.
_DEFAULT_COOLDOWN = 300.0


def _iter_causes(error: Exception):
    """예외와 그 원인 사슬(`__cause__`/`__context__`)을 훑는다.

    **왜 필요한가**: 호출측이 원래 예외를 감싸면 429 신호가 문자열에서 사라진다.
    2026-07-16 실측 — `sheets_operations.connect_to_sheet`가
    `SheetAccessError(f"스프레드시트 연결 실패: {str(e)}")`로 감쌌는데 `str(e)`가 비어서
    로그에 `스프레드시트 연결 실패: ` 만 남았다. 쿼터 판정이 실패해 **페일오버가 돌지 않았고**,
    커스텀 시트 읽기가 그대로 죽었다(그리고 "데이터가 없습니다."로 보고됐다).
    """
    seen = set()
    current: Optional[BaseException] = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _status_codes(error: Exception) -> List[int]:
    """예외 사슬에서 HTTP 상태 코드를 모은다(문자열 매칭보다 확실하다)."""
    codes: List[int] = []
    for exc in _iter_causes(error):
        for holder in (exc, getattr(exc, 'response', None)):
            code = getattr(holder, 'status_code', None) or getattr(holder, 'code', None)
            if isinstance(code, int):
                codes.append(code)
    return codes


def _chain_text(error: Exception) -> str:
    """예외 사슬 전체를 문자열로. str()이 빈 예외도 있어 repr()을 함께 본다."""
    parts = []
    for exc in _iter_causes(error):
        parts.append(str(exc))
        parts.append(repr(exc))
    return ' '.join(p for p in parts if p)


def is_quota_error(error: Exception) -> bool:
    """쿼터 초과(429/RESOURCE_EXHAUSTED) 오류인지.

    gspread는 APIError에 응답 본문을 담아 던진다. 라이브러리 버전에 따라
    구조가 달라 상태 코드·문자열·원인 사슬을 모두 본다
    (과잉 매칭보다 놓치는 편이 위험 — 놓치면 페일오버가 안 돌아 기능이 죽는다).
    """
    if 429 in _status_codes(error):
        return True
    text = _chain_text(error)
    if '429' in text or 'RESOURCE_EXHAUSTED' in text:
        return True
    lowered = text.lower()
    return 'quota exceeded' in lowered or 'rate limit' in lowered


def is_permission_error(error: Exception) -> bool:
    """이 계정이 그 시트에 공유되지 않아 생긴 오류인지(403/PERMISSION_DENIED).

    쿼터 판정과 같은 이유로 원인 사슬까지 본다 — 감싼 예외가 403을 지워 버리면
    그 계정을 영구 제외하지 못하고 매번 같은 실패를 반복한다.
    """
    if 403 in _status_codes(error):
        return True
    text = _chain_text(error)
    if '403' in text or 'PERMISSION_DENIED' in text:
        return True
    if 'permission' in text.lower():
        return True
    return any(type(e).__name__ == 'PermissionError' for e in _iter_causes(error))


def _split(value: str) -> List[str]:
    return [v.strip() for v in str(value or '').split(',') if v.strip()]


class CredentialPool:
    """용도별 크레덴셜 할당 + 대여 + 쿨다운."""

    def __init__(self, directory: Optional[str] = None,
                 cooldown: Optional[float] = None):
        self._lock = threading.Lock()
        if cooldown is None:
            try:
                cooldown = float(getattr(config, 'CREDENTIAL_COOLDOWN', _DEFAULT_COOLDOWN))
            except (TypeError, ValueError):
                cooldown = _DEFAULT_COOLDOWN
        self._cooldown = cooldown
        # 이름 → 파일 경로
        self._creds: Dict[str, str] = {}
        # 이름 → 쿨다운 해제 시각(time.time())
        self._resting: Dict[str, float] = {}
        # (용도, 이름) → 접근 불가 확정
        self._no_access: set = set()
        self._discover(directory)
        self._assignment = self._load_assignment()

    # -- 탐색/할당 ---------------------------------------------------------
    def _discover(self, directory: Optional[str]) -> None:
        """`credentials/` 디렉터리의 *.json + 기존 루트 credentials.json을 모은다.

        이름은 파일명에서 `_credentials`/`.json`을 뗀 것(예: genesis_credentials.json → genesis).
        """
        base = getattr(config, 'BASE_DIR', None)
        directory = directory or getattr(config, 'CREDENTIALS_DIR', 'credentials')
        path = directory if os.path.isabs(directory) else (
            os.path.join(str(base), directory) if base else directory)

        if os.path.isdir(path):
            for filename in sorted(os.listdir(path)):
                if not filename.endswith('.json'):
                    continue
                name = filename[:-len('.json')].replace('_credentials', '')
                self._creds[name] = os.path.join(path, filename)

        # 기존 단일 크레덴셜(루트 credentials.json)은 **디렉터리가 비었을 때만** 쓴다.
        #
        # 2026-07-16 실측: 루트 계정은 `기본` 스프레드시트에 **읽기 전용**으로 공유돼 있다.
        # 읽기는 통과하고 쓰기만 403으로 실패하는 계정이라, 풀이 이걸 고르면
        # 출석·소지금·스탯 쓰기가 깨진다(읽기 단계에선 아무 문제가 없어 보인다).
        # credentials/ 의 계정들은 3개 시트 모두 쓰기 가능하므로 그쪽만 쓴다.
        if not self._creds:
            try:
                legacy = str(config.get_credentials_path())
                if os.path.exists(legacy):
                    self._creds['default'] = legacy
                    logger.warning(
                        "[크레덴셜] credentials/ 디렉터리가 비어 루트 credentials.json을 사용합니다. "
                        "이 계정은 '기본' 시트에 읽기 전용일 수 있습니다."
                    )
            except Exception:
                pass

        if self._creds:
            logger.info(f"[크레덴셜] {len(self._creds)}개 발견: {sorted(self._creds)}")
        else:
            logger.warning("[크레덴셜] 발견된 크레덴셜이 없습니다.")

    def _load_assignment(self) -> Dict[str, List[str]]:
        """용도별 크레덴셜 우선순위. config에 없으면 발견된 전체를 쓴다."""
        env_keys = {
            PURPOSE_MAIN: 'CREDENTIAL_MAIN',
            PURPOSE_SYSTEM: 'CREDENTIAL_SYSTEM',
            PURPOSE_INVESTIGATION: 'CREDENTIAL_INVESTIGATION',
        }
        assignment: Dict[str, List[str]] = {}
        for purpose, key in env_keys.items():
            names = [n for n in _split(getattr(config, key, '')) if n in self._creds]
            assignment[purpose] = names
        return assignment

    def candidates(self, purpose: str) -> List[str]:
        """이 용도로 시도할 크레덴셜 이름 목록(우선순위 순).

        1) 할당된 것 → 2) 나머지 전부(대여). 접근 불가로 확정된 것은 뺀다.
        """
        assigned = list(self._assignment.get(purpose) or [])
        borrow_ok = bool(getattr(config, 'CREDENTIAL_BORROW_ENABLED', True))
        rest = [n for n in self._creds if n not in assigned] if borrow_ok else []
        ordered = assigned + rest
        if not ordered:
            ordered = list(self._creds)
        return [n for n in ordered if (purpose, n) not in self._no_access]

    # -- 선택 -------------------------------------------------------------
    def acquire(self, purpose: str, exclude: Optional[List[str]] = None) -> Optional[str]:
        """이 용도로 지금 쓸 크레덴셜 이름. 없으면 None.

        쉬고 있지 않은 것 중 우선순위가 가장 높은 것을 준다.
        전부 쉬는 중이면 **가장 빨리 깨어나는 것**을 준다(멈추는 것보다 낫다).
        """
        with self._lock:
            excluded = set(exclude or [])
            now = time.time()
            available, resting = [], []
            for name in self.candidates(purpose):
                if name in excluded:
                    continue
                until = self._resting.get(name, 0)
                (available if until <= now else resting).append((name, until))
            if available:
                return available[0][0]
            if resting:
                # 전부 쿨다운 중 — 가장 빨리 풀리는 것으로 시도(실패해도 상위가 처리)
                soonest = min(resting, key=lambda x: x[1])[0]
                logger.warning(
                    f"[크레덴셜] '{purpose}' 전부 쿨다운 중 - 가장 빠른 '{soonest}'로 시도")
                return soonest
            return None

    def path_of(self, name: str) -> Optional[str]:
        return self._creds.get(name)

    # -- 상태 보고 ---------------------------------------------------------
    def report_quota_exhausted(self, name: str) -> None:
        """이 크레덴셜이 쿼터에 걸렸다 → 잠시 쉬게 한다."""
        with self._lock:
            self._resting[name] = time.time() + self._cooldown
        logger.warning(
            f"[크레덴셜] '{name}' 쿼터 소진 - {self._cooldown:.0f}초 쿨다운")

    def report_no_access(self, purpose: str, name: str) -> None:
        """이 크레덴셜은 그 시트에 공유되지 않았다 → 해당 용도에서 영구 제외."""
        with self._lock:
            if (purpose, name) in self._no_access:
                return
            self._no_access.add((purpose, name))
        logger.error(
            f"[크레덴셜] '{name}'은(는) '{purpose}' 스프레드시트에 공유되어 있지 않습니다. "
            f"이 용도에서 제외합니다. 남은 후보: {self.candidates(purpose)}"
        )

    def report_success(self, name: str) -> None:
        """정상 동작 확인 → 쿨다운 해제."""
        with self._lock:
            self._resting.pop(name, None)

    # -- 진단 -------------------------------------------------------------
    def status(self) -> Dict[str, object]:
        with self._lock:
            now = time.time()
            return {
                'discovered': sorted(self._creds),
                'assignment': {p: list(self._assignment.get(p) or []) for p in _ALL_PURPOSES},
                'resting': {n: round(t - now, 1) for n, t in self._resting.items() if t > now},
                'no_access': sorted(f"{p}:{n}" for p, n in self._no_access),
            }


_pool: Optional[CredentialPool] = None
_pool_lock = threading.Lock()


def get_pool() -> CredentialPool:
    """전역 CredentialPool 싱글톤."""
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = CredentialPool()
    return _pool


def set_pool(pool: Optional[CredentialPool]) -> None:
    """테스트용 주입/초기화."""
    global _pool
    with _pool_lock:
        _pool = pool

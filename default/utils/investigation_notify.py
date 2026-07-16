"""
utils/investigation_notify.py — 조사 봇 관리자 알림

가이드가 관리자 DM을 요구하는 지점:
- §2.1 / §2.2  버전 중복(같은 이름의 '가능' 행이 서로 다른 오픈 일자) 경고
- §2.2 / §4    수식 파싱 실패, 음수 개수 등 시트 데이터 경고
- §6.3-5       개방 완료 알림 ("{일차} 조사 {n}건 개방 완료")
- §6.3-4       놓친 개방 감지 알림
- §7           보상 일부 적용 후 시트 쓰기 실패 (수동 정산 필요)

DM 발송은 best-effort다. 실패해도 명령 처리를 막지 않으며 항상 로컬 로그에 남는다.
같은 경고가 매 명령마다 반복 발송되지 않도록 프로세스 수명 동안 중복을 억제한다.
"""

import os
import sys
import threading
from typing import Set

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

try:
    from utils.logging_config import logger
except ImportError:  # pragma: no cover
    import logging
    logger = logging.getLogger('utils.investigation_notify')

try:
    from config.settings import config
except ImportError:  # pragma: no cover
    config = None

_PREFIX = '[조사봇]'

# 반복 경고 억제용 (프로세스 수명 동안 유지)
_seen_lock = threading.Lock()
_seen: Set[str] = set()


def _admin_id() -> str:
    return str(getattr(config, 'SYSTEM_ADMIN_ID', '') or '').strip()


def notify_admin(message: str, api=None, prefix: str = _PREFIX) -> bool:
    """관리자에게 DM 발송. 항상 로컬 로그에도 남긴다.

    Args:
        api: 마스토돈 API. 없으면 전역 dm_sender를 시도한다.
        prefix: 말머리. 조사 외 기능(출석 등)이 재사용할 때 바꾼다.

    Returns:
        bool: DM 발송 성공 여부 (로그 기록은 성공 여부와 무관하게 수행).
    """
    text = f"{prefix} {message}"
    logger.warning(text)

    admin = _admin_id()
    if not admin:
        logger.debug("[조사] SYSTEM_ADMIN_ID 미설정 - 관리자 DM 생략")
        return False

    # 1) 명시적으로 넘어온 api 우선
    if api is not None:
        try:
            api.status_post(status=f"@{admin} {text}", visibility='direct')
            return True
        except Exception as e:
            logger.error(f"[조사] 관리자 DM 발송 실패: {e}")

    # 2) 전역 dm_sender 폴백 (큐잉되어 재시도된다)
    try:
        from utils.dm_sender import queue_dm
        queue_dm(admin, text)
        return True
    except Exception as e:
        logger.error(f"[조사] 관리자 DM 큐잉 실패: {e}")
        return False


def notify_admin_once(key: str, message: str, api=None, prefix: str = _PREFIX) -> bool:
    """같은 key의 경고는 프로세스당 1회만 발송한다.

    버전 중복 경고처럼 매 [장소 목록]마다 재발견되는 경고에 쓴다.
    """
    with _seen_lock:
        if key in _seen:
            return False
        _seen.add(key)
    return notify_admin(message, api=api, prefix=prefix)


def reset_once_cache() -> None:
    """중복 억제 캐시 초기화 (테스트/캐시 리셋 명령용)."""
    with _seen_lock:
        _seen.clear()

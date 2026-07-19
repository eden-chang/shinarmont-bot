"""
대상(캐릭터) 해석 유틸리티

transfer_command 의 대상 조회 로직을 일반화한 헬퍼.
- resolve_target(context, sheets_manager, keyword_index=None) -> dict | None
    · keyword_index 가 주어지면 context.keywords[keyword_index] 를 캐릭터 이름으로 매칭
    · 아니면 context.get_metadata('original_status').mentions 에서 봇을 제외한 첫 대상
    · 명단(ROSTER) + 관리(관리) 시트에서 {'이름','아이디','은는', ...관리컬럼} 를 반환
    · 자기 자신 / 미등록 대상은 None 을 반환하고, 사유를 context 에 기록한다.
- relay_dm(receiver_id, message)
    · utils.dm_sender.queue_dm 을 호출한다(transfer 의 끊긴 _store_dm_info 방식은 쓰지 않는다).

docs/코딩_계획.md §5 유틸 계약을 따른다.
"""

import os
import re
import sys
from typing import Any, Dict, List, Optional

# 경로 설정 (VM 환경 대응)
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

try:
    from config.settings import config
    from utils.logging_config import logger
    from utils.store_helpers import load_user_data
except ImportError:  # 폴백 (테스트/부분 환경)
    import logging
    logger = logging.getLogger('target_helpers')
    config = None

    def load_user_data(sheets_manager):  # type: ignore
        try:
            return sheets_manager.get_roster_data(use_cache=True) or []
        except Exception:
            return []


# resolve_target 실패 사유를 context 에 저장할 때 쓰는 키
TARGET_ERROR_KEY = 'target_error'


def _normalize_name(name: str) -> str:
    """이름 매칭용 정규화 (공백 제거 + 소문자)."""
    if not name:
        return ''
    return re.sub(r'\s+', '', str(name)).strip().lower()


def _normalize_acct(acct: str) -> str:
    """아이디/acct 매칭용 정규화 (선행 @ 제거 + 소문자 + 공백제거)."""
    if not acct:
        return ''
    return str(acct).strip().lstrip('@').strip().lower()


def _set_error(context: Any, message: str) -> None:
    """실패 사유를 context 의 추가 데이터에 기록한다(가능한 경우)."""
    try:
        if context is not None and hasattr(context, 'add_data'):
            context.add_data(TARGET_ERROR_KEY, message)
    except Exception:
        pass
    logger.debug(f"resolve_target 실패: {message}")


def get_target_error(context: Any, default: str = '') -> str:
    """resolve_target 이 기록한 실패 사유를 반환한다."""
    try:
        if context is not None and hasattr(context, 'get_data'):
            return context.get_data(TARGET_ERROR_KEY, default)
    except Exception:
        pass
    return default


def _bot_accts() -> List[str]:
    """알려진 봇 계정 acct 목록(정규화).

    대화·고발 등 대상(캐릭터) 해석에서 **봇 자신의 멘션**(@STORY 등)을 상대로 오인하지
    않도록 제외 목록을 만든다. 봇 슬롯은 대체로 `BOTn_ID` 없이 `BOTn_NAME`만 설정되어
    있으므로(예: BOT3_NAME=STORY, 계정 acct=STORY), **NAME도 acct로 취급**해 제외한다.
    로컬 계정은 acct == username == NAME 이라 이 매칭이 안전하다.
    """
    accts: List[str] = []
    if config is not None:
        # 단일 봇 식별자
        bot_id = getattr(config, 'BOT_ID', '') or ''
        if bot_id:
            accts.append(_normalize_acct(bot_id))
        # 단일 봇 이름(@STORY 처럼 이름이 곧 계정)
        bot_name = getattr(config, 'BOT_NAME', '') or ''
        if bot_name:
            accts.append(_normalize_acct(bot_name))
    # 멀티 봇 슬롯 환경변수 (BOT1_ID~BOT9_ID / BOT1_NAME~BOT9_NAME 등)
    for key, value in os.environ.items():
        if not value:
            continue
        if re.fullmatch(r'BOT\d+_ID', key) or re.fullmatch(r'BOT\d+_NAME', key):
            accts.append(_normalize_acct(value))
    return [a for a in accts if a]


def _load_roster(sheets_manager) -> List[Dict[str, Any]]:
    """명단(ROSTER) 데이터 로드."""
    try:
        data = load_user_data(sheets_manager)
        return data or []
    except Exception as e:
        logger.warning(f"명단 데이터 로드 실패: {e}")
        return []


def _load_management(sheets_manager) -> List[Dict[str, Any]]:
    """관리 시트 데이터 로드 (항상 최신값)."""
    try:
        return sheets_manager.get_worksheet_data('관리', use_cache=False) or []
    except Exception as e:
        logger.warning(f"관리 워크시트 조회 실패: {e}")
        return []


def _roster_entry_by_name(roster: List[Dict[str, Any]], name: str) -> Optional[Dict[str, Any]]:
    """명단에서 이름으로 항목 조회 (공백/대소문자 무시)."""
    target = _normalize_name(name)
    if not target:
        return None
    for row in roster:
        if _normalize_name(row.get('이름', '')) == target:
            return row
    return None


def _roster_entry_by_id(roster: List[Dict[str, Any]], acct: str) -> Optional[Dict[str, Any]]:
    """명단에서 아이디(acct)로 항목 조회 (대소문자/@ 무시)."""
    target = _normalize_acct(acct)
    if not target:
        return None
    for row in roster:
        if _normalize_acct(row.get('아이디', '')) == target:
            return row
    return None


def _suffix_of(row: Dict[str, Any]) -> str:
    """은/는 조사 안전 추출."""
    suffix = str(row.get('은는', '은')).strip()
    return suffix if suffix in ('은', '는') else '은'


def _build_target(roster_row: Dict[str, Any], sheets_manager) -> Dict[str, Any]:
    """명단 항목 + 관리 시트 항목을 병합해 대상 정보 dict 생성."""
    name = str(roster_row.get('이름', '')).strip()
    acct = str(roster_row.get('아이디', '')).strip()

    result: Dict[str, Any] = {
        '이름': name,
        '아이디': acct,
        '은는': _suffix_of(roster_row),
    }

    # 관리 시트의 상태 컬럼(소지금/소지품/건강/이성/직군 등) 병합
    management = _load_management(sheets_manager)
    target_acct = _normalize_acct(acct)
    for row in management:
        if _normalize_acct(row.get('아이디', '')) == target_acct:
            for key, value in row.items():
                # 명단 기준 이름/아이디/은는 는 덮어쓰지 않는다
                if key not in result:
                    result[key] = value
            break

    return result


def _extract_first_mention_acct(context: Any) -> Optional[str]:
    """original_status.mentions 에서 봇을 제외한 첫 멘션 acct 를 반환."""
    status = None
    if context is not None and hasattr(context, 'get_metadata'):
        status = context.get_metadata('original_status')
    if status is None:
        return None

    mentions = getattr(status, 'mentions', None)
    if not mentions:
        return None

    bots = set(_bot_accts())
    self_acct = _normalize_acct(getattr(context, 'user_id', '') or '')

    # 봇 자신의 실제 계정(api.me() 기반)을 제외 목록에 추가한다.
    # `_bot_accts()`는 BOTn_NAME(디스플레이 이름)을 acct로 가정하는데, 실제 @username이
    # 다르면(대소문자/별칭/도메인) 봇 자신을 놓쳐 대상으로 오인한다. stream_handler가
    # context 메타데이터에 실어 준 실제 acct로 이 구멍을 막는다.
    bot_self = ''
    if context is not None and hasattr(context, 'get_metadata'):
        try:
            bot_self = _normalize_acct(context.get_metadata('bot_acct') or '')
        except Exception:
            bot_self = ''
    if bot_self:
        bots.add(bot_self)
        # 도메인이 붙은 acct(story@instance)와 로컬 acct(story)를 함께 대비해 로컬파트도 제외
        bots.add(bot_self.split('@', 1)[0])

    for mention in mentions:
        # mention 은 dict 또는 속성 접근 객체일 수 있다
        if isinstance(mention, dict):
            acct = mention.get('acct', '')
        else:
            acct = getattr(mention, 'acct', '')
        norm = _normalize_acct(acct)
        if not norm:
            continue
        # 도메인 유무 양방향으로 봇 판정(story == story@instance)
        if norm in bots or norm.split('@', 1)[0] in bots:
            continue
        if norm == self_acct:
            continue
        return acct

    return None


def resolve_target(context: Any, sheets_manager: Any, keyword_index: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """
    명령어 대상(캐릭터)을 해석한다.

    Args:
        context: CommandContext (keywords / metadata['original_status'] / user_id 사용)
        sheets_manager: 기본 시트 매니저 (명단/관리)
        keyword_index: 주어지면 context.keywords[keyword_index] 를 캐릭터 이름으로 사용.
                       None 이면 original_status.mentions 에서 봇 제외 첫 대상.

    Returns:
        dict: {'이름','아이디','은는', ...관리컬럼} (성공)
        None: 미등록/자기자신/대상 없음 (사유는 get_target_error(context) 로 조회)
    """
    if sheets_manager is None:
        _set_error(context, "시트 연결이 없어 대상을 조회할 수 없습니다.")
        return None

    roster = _load_roster(sheets_manager)
    if not roster:
        _set_error(context, "명단 데이터가 없어 대상을 조회할 수 없습니다.")
        return None

    roster_row: Optional[Dict[str, Any]] = None
    target_label: str = ''

    if keyword_index is not None:
        # 키워드명 기반 해석
        keywords = getattr(context, 'keywords', None) or []
        if keyword_index >= len(keywords):
            _set_error(context, "대상 캐릭터명이 지정되지 않았습니다.")
            return None
        name = str(keywords[keyword_index]).strip()
        target_label = name
        if not name:
            _set_error(context, "대상 캐릭터명이 비어 있습니다.")
            return None
        roster_row = _roster_entry_by_name(roster, name)
        if not roster_row:
            _set_error(context, f"'{name}' 캐릭터를 찾을 수 없습니다. 명단에 등록되어 있는지 확인해 주세요.")
            return None
    else:
        # 멘션 기반 해석
        acct = _extract_first_mention_acct(context)
        if not acct:
            _set_error(context, "대상을 지정해 주세요. 상대를 멘션(@)해야 합니다.")
            return None
        target_label = acct
        roster_row = _roster_entry_by_id(roster, acct)
        if not roster_row:
            _set_error(context, f"'{acct}' 캐릭터를 찾을 수 없습니다. 명단에 등록되어 있는지 확인해 주세요.")
            return None

    # 자기 자신 방지
    self_acct = _normalize_acct(getattr(context, 'user_id', '') or '')
    if self_acct and _normalize_acct(roster_row.get('아이디', '')) == self_acct:
        _set_error(context, "자기 자신은 대상으로 지정할 수 없습니다.")
        return None

    return _build_target(roster_row, sheets_manager)


def relay_dm(receiver_id: str, message: str) -> bool:
    """
    대상에게 DM 을 전송(대기열 추가)한다.

    transfer 의 끊긴 _store_dm_info 방식이 아니라 utils.dm_sender.queue_dm 을 사용한다.

    Args:
        receiver_id: 수신자 acct
        message: 전송할 메시지

    Returns:
        bool: 대기열 추가 성공 여부
    """
    if not receiver_id or not str(receiver_id).strip():
        logger.warning("relay_dm: 수신자 ID 가 비어 있습니다.")
        return False
    try:
        from utils.dm_sender import queue_dm
        queue_dm(str(receiver_id).strip(), message)
        return True
    except Exception as e:
        logger.error(f"relay_dm 실패: {e}")
        return False

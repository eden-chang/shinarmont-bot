"""
로그 인젝션 방지 유틸리티

사용자 입력을 로그에 기록하기 전에 제어 문자를 제거하고 길이를 제한합니다.
"""

import re

_CONTROL_CHAR_RE = re.compile(r'[\x00-\x1f\x7f-\x9f]')


def sanitize_log_input(value: str, max_length: int = 200) -> str:
    """
    로그에 안전하게 기록할 수 있도록 사용자 입력을 정제합니다.

    Args:
        value: 정제할 값
        max_length: 최대 허용 길이

    Returns:
        str: 제어 문자가 제거되고 길이가 제한된 문자열
    """
    if not isinstance(value, str):
        value = str(value)
    sanitized = _CONTROL_CHAR_RE.sub('', value)
    if len(sanitized) > max_length:
        return sanitized[:max_length] + "..."
    return sanitized

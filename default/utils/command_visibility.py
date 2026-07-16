"""
utils/command_visibility.py — 명령어별 허용 공개 범위(visibility) 강제

명령어마다 허용되는 툿 공개 범위가 다르다. 예를 들어 조사 결과는 남이 보면 안 되는
정보라 DM 전용이고, 출석은 팔로워 전용으로만 받는다.

**이 검사는 다른 모든 로직보다 먼저 수행된다** (handlers/stream_handler._handle_mention).
범위가 맞지 않으면 시트를 한 번도 읽지 않고 즉시 차단하고 안내 문구만 답한다.
러너가 잘못된 범위로 올린 툿은 이미 남들에게 노출된 상태이므로, 봇이 거기에
실제 결과를 덧붙이면 피해가 커진다. 그래서 '처리 후 거절'이 아니라 '선(先) 차단'이다.

정책:
- 표(RULES)에 있는 명령어 → **표가 유일한 기준**이다. 슬롯 전역 설정
  (BOTn_ALLOWED_VISIBILITY)보다 우선한다.
  예: BOT1이 전역으로 unlisted까지 허용해도 [출석]은 팔로워 전용만 받는다.
- 표에 없는 명령어(도움말·가방·관리자 명령 등) → 기존 전역 설정
  (config.ALLOWED_VISIBILITY_LEVELS)을 그대로 따른다. 정책이 없는 명령어에
  임의의 규칙을 지어내지 않는다.
- `public`은 어떤 명령어에서도 허용하지 않는다(표에 없다).

별칭 처리: 표는 **정식 명령어명**으로만 적는다. `[탐색/…]` 같은 별칭은 레지스트리로
정식명을 찾아 해석하므로 표에 별칭을 나열할 필요가 없다.
단, 별칭 해석은 레지스트리의 키워드 맵이 채워진 뒤에야 동작한다(기동 시
`discover_commands()`가 채운다 → 스트림 수신보다 항상 먼저다). 맵이 비어 있으면
정식명으로만 조회하고, 못 찾은 명령어는 전역 설정으로 폴백한다.
"""

import os
import sys
from typing import Optional, Sequence, Tuple

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

try:
    from utils.logging_config import logger
except ImportError:  # pragma: no cover
    import logging
    logger = logging.getLogger('utils.command_visibility')

try:
    from config.settings import config
except ImportError:  # pragma: no cover
    config = None


PUBLIC = 'public'
UNLISTED = 'unlisted'
PRIVATE = 'private'
DIRECT = 'direct'

# 문구에 나열할 순서 (넓은 범위 → 좁은 범위)
VISIBILITY_ORDER: Tuple[str, ...] = (PUBLIC, UNLISTED, PRIVATE, DIRECT)

# 마스토돈 한국어 UI 표기
VISIBILITY_LABELS = {
    PUBLIC: '공개',
    UNLISTED: '미등재',
    PRIVATE: '팔로워 전용',
    DIRECT: '다이렉트 메시지',
}


# --------------------------------------------------------------------------- #
# 명령어별 허용 범위표 (정식 명령어명 기준)
# --------------------------------------------------------------------------- #
RULES = {
    # --- @SYSTEM ---
    '상태 확인':    (PRIVATE, DIRECT),
    '출석':         (PRIVATE,),
    '상점':         (PRIVATE, DIRECT),
    '구매':         (PRIVATE, DIRECT),
    '아이템 설명':  (PRIVATE, DIRECT),   # [설명/아이템명]
    '사용':         (PRIVATE, DIRECT),
    '양도':         (UNLISTED, PRIVATE, DIRECT),

    # --- @TOWN (마을) ---
    '장소 목록':    (DIRECT,),
    '진입':         (DIRECT,),
    '조사':         (DIRECT,),
    '추적':         (DIRECT,),

    # --- @STORY (스토리) ---
    '투표':         (DIRECT,),
    '대화':         (DIRECT,),
    '고발':         (DIRECT,),
    '결과 보고':    (DIRECT,),
    '교류':         (DIRECT,),

    # --- @DOCTOR (의사) ---
    '의무실 방문':  (DIRECT,),

    # --- @BAR (도박) ---
    # 2026-07-16: 팔로워 전용 + 다이렉트 둘 다 허용으로 완화(운영 결정).
    '블랙잭':       (PRIVATE, DIRECT),
    '슬롯머신':     (PRIVATE, DIRECT),
    '크랩스':       (PRIVATE, DIRECT),

    # --- 공통 / 관리자 ---
    '도움말':       (PRIVATE, DIRECT),
    '대화 끝내기':  (DIRECT,),
    '캐시 리셋':    (DIRECT,),
    '소지금 관리':  (DIRECT,),
    '스탯 변경':    (DIRECT,),
    '조사 개방':    (DIRECT,),
    '일일보고':     (DIRECT,),
}


def _normalize(name) -> str:
    """비교용 정규화: 공백 제거 + 소문자."""
    return ''.join(str(name or '').split()).lower()


# 정규화된 키로 조회할 수 있게 미리 구축
_RULES_NORMALIZED = {_normalize(k): v for k, v in RULES.items()}


# --------------------------------------------------------------------------- #
# 조회
# --------------------------------------------------------------------------- #
def _canonical_name(keyword: str) -> Optional[str]:
    """레지스트리에서 정식 명령어명을 찾는다(별칭·공백 무시). 실패 시 None.

    `resolve_keyword`를 쓴다. `get_command_by_keyword`는 BOT_TYPE/COMMAND_FILTER로
    걸러져 **어느 슬롯이 받았는지에 따라 결과가 달라진다.** 공개 범위 정책은
    슬롯과 무관하게 같아야 하므로 필터를 타지 않는 쪽을 쓴다.

    지연 임포트: 레지스트리가 이 모듈을 간접 임포트할 여지가 있어 순환을 피한다.
    """
    try:
        from commands.registry import resolve_keyword
        return resolve_keyword(keyword)
    except Exception as e:  # 레지스트리 미초기화 등 — 표 직접 조회로 폴백
        logger.debug(f"[visibility] 레지스트리 조회 실패({keyword}): {e}")
        return None


def allowed_for(keyword: str) -> Optional[Tuple[str, ...]]:
    """이 명령어에 지정된 허용 범위. 표에 없으면 None(= 전역 설정을 따른다).

    별칭(`탐색` → `조사`)도 레지스트리로 해석한다.
    """
    canonical = _canonical_name(keyword)
    if canonical:
        rule = _RULES_NORMALIZED.get(_normalize(canonical))
        if rule:
            return rule
    # 레지스트리를 못 쓰거나(테스트·초기화 전) 별칭이 아닌 경우: 키워드 직접 조회
    return _RULES_NORMALIZED.get(_normalize(keyword))


def _global_levels() -> Tuple[str, ...]:
    levels = getattr(config, 'ALLOWED_VISIBILITY_LEVELS', None) or [PRIVATE, DIRECT]
    return tuple(levels)


# --------------------------------------------------------------------------- #
# 문구
# --------------------------------------------------------------------------- #
def format_ranges(allowed: Sequence[str]) -> str:
    """허용 범위를 문구용 문자열로. 예: '**팔로워 전용** 혹은 **다이렉트 메시지**'."""
    labels = [
        f"**{VISIBILITY_LABELS[v]}**"
        for v in VISIBILITY_ORDER
        if v in allowed and v in VISIBILITY_LABELS
    ]
    if not labels:
        return f"**{VISIBILITY_LABELS[DIRECT]}**"
    if len(labels) == 1:
        return labels[0]
    return ', '.join(labels[:-1]) + ' 혹은 ' + labels[-1]


def message_for(allowed: Sequence[str]) -> str:
    """공개 범위 위반 안내 문구."""
    template = getattr(
        config, 'VISIBILITY_ERROR_TEMPLATE',
        '이 명령어는 {ranges} 범위로만 사용할 수 있습니다. '
        '위 툿을 삭제한 후, 범위를 올바르게 설정하여 다시 업로드하세요.',
    )
    return template.format(ranges=format_ranges(allowed))


# --------------------------------------------------------------------------- #
# 공개 API
# --------------------------------------------------------------------------- #
def check(keyword: str, visibility: str) -> Optional[str]:
    """공개 범위를 검사한다.

    Args:
        keyword: 명령어 키워드(별칭 허용). 보통 keywords[0].
        visibility: 툿의 공개 범위 ('public'/'unlisted'/'private'/'direct').

    Returns:
        None이면 허용. 아니면 사용자에게 보낼 안내 문구.
    """
    allowed = allowed_for(keyword)
    if allowed is None:
        allowed = _global_levels()   # 표에 없는 명령어는 기존 전역 정책을 따른다
    if str(visibility or '').strip().lower() in allowed:
        return None
    return message_for(allowed)

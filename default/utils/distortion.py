"""
이성(정신력) 왜곡 필터 - distortion

이성 수치에 따라 조사/추적/소문 등 출력 텍스트를 왜곡한다.
- 이성 > SANITY_DISTORTION_THRESHOLD(40): 왜곡 없음 (원문 그대로)
- SANITY_HALLUCINATION_THRESHOLD(20) < 이성 <= 40: 마스킹 (정보 흐림)
- 이성 <= SANITY_HALLUCINATION_THRESHOLD(20): 오정보 치환 (거짓 정보 + 환각 서두)

우선 utils.ai_client(Claude API)를 시도하고, 실패/미설치/비활성 시 규칙 기반 폴백으로 흐른다.
계약: apply(text, sanity, candidates=None) -> (str, changes_list)
  - changes_list: 오정보 치환에서 무엇을 바꿨는지 GM 추적용 리스트(마스킹/무왜곡은 빈 리스트).
"""

import re
import random

try:
    from utils.logging_config import logger
except ImportError:  # pragma: no cover - VM 폴백
    import logging
    logger = logging.getLogger('distortion')

try:
    from config.settings import config
except ImportError:  # pragma: no cover - 설정 로드 실패 폴백
    config = None


# ---------------------------------------------------------------------------
# 임계값 헬퍼
# ---------------------------------------------------------------------------

def _distortion_threshold() -> int:
    """이 값 이하에서 마스킹 시작(초과면 왜곡 없음)."""
    return int(getattr(config, 'SANITY_DISTORTION_THRESHOLD', 40) or 40)


def _hallucination_threshold() -> int:
    """이 값 이하에서 오정보 치환."""
    return int(getattr(config, 'SANITY_HALLUCINATION_THRESHOLD', 20) or 20)


# ---------------------------------------------------------------------------
# 규칙 기반 폴백 (AI 없이 결정적으로 동작; §1.6)
# ---------------------------------------------------------------------------

# 어절 내부의 한글/영숫자 덩어리를 잡는 패턴
_TOKEN_RE = re.compile(r'[0-9]+|[가-힣A-Za-z]+')


def _mask_strength_from_sanity(sanity) -> float:
    """이성 수치를 0.0~1.0 마스킹 강도로 환산.

    왜곡 임계(마스킹 시작)에서 0에 가깝고, 환각 임계(오정보 시작)에서 1에 가깝다.
    """
    hi = _distortion_threshold()
    lo = _hallucination_threshold()
    try:
        s = float(sanity)
    except (TypeError, ValueError):
        return 0.5
    if hi <= lo:  # 방어: 잘못된 설정
        return 0.5
    # sanity가 hi이면 강도 낮음, lo이면 강도 높음
    ratio = (hi - s) / (hi - lo)
    # 0.2 ~ 0.8 사이로 클램프(전부 가리거나 전혀 안 가리는 것 방지)
    strength = 0.2 + 0.6 * max(0.0, min(1.0, ratio))
    return strength


def _rule_based_mask(text: str, strength: float) -> str:
    """정규식으로 숫자/일부 어절을 ▓로 치환하는 규칙 기반 마스킹.

    - 숫자는 강도와 무관하게 우선적으로 가린다(정보성이 높음).
    - 한글/영문 어절은 강도(0~1)에 비례한 확률로 길이만큼 ▓ 치환.
    - 결정적이지 않게 매 호출 무작위(왜곡은 매번 다르게).
    """
    if not text:
        return text

    strength = max(0.0, min(1.0, float(strength)))

    def _repl(match: 're.Match') -> str:
        token = match.group(0)
        if token.isdigit():
            # 숫자는 높은 확률로 가림
            if random.random() < min(1.0, strength + 0.4):
                return '▓' * len(token)
            return token
        # 한 글자 어절(조사 등)은 보존 성향
        if len(token) <= 1:
            return token
        if random.random() < strength:
            return '▓' * len(token)
        return token

    return _TOKEN_RE.sub(_repl, text)


def _rule_based_false(text, candidates):
    """candidates에서 1~2개를 무작위 치환하고 환각 서두를 붙이는 규칙 기반 오정보.

    Returns:
        (distorted_text, changes_list)
    """
    hallucination_prefix = random.choice([
        "…무언가 잘못됐다. 기억이 자꾸 뒤틀린다.",
        "…시야가 흐려지고, 방금 본 것이 사실인지 확신이 서지 않는다.",
        "…귓가에 속삭임이 맴돈다. 이게 진짜 기억일까.",
        "…머릿속이 웅웅거린다. 무언가가 뒤바뀐 것 같다.",
    ])

    changes = []

    # 원문에서 치환 가능한 어절 후보 추출
    tokens = _TOKEN_RE.findall(text or '')
    # 후보 풀 정리(공백/빈값 제거, 원문과 중복되지 않게)
    pool = [c.strip() for c in (candidates or []) if c and str(c).strip()]

    distorted = text or ''

    if pool and tokens:
        # 원문 어절 중 길이 2 이상인 것(고유명사 후보)을 우선
        swap_candidates = [t for t in tokens if len(t) >= 2]
        if not swap_candidates:
            swap_candidates = tokens[:]
        random.shuffle(swap_candidates)

        num_swaps = min(len(swap_candidates), random.randint(1, 2))
        used_replacements = set()
        for original in swap_candidates:
            if num_swaps <= 0:
                break
            # 원문과 다른 대체어 선택
            choices = [c for c in pool if c != original and c not in used_replacements]
            if not choices:
                continue
            replacement = random.choice(choices)
            used_replacements.add(replacement)
            # 첫 등장 1회만 치환(과도 치환 방지)
            new_distorted = distorted.replace(original, replacement, 1)
            if new_distorted != distorted:
                distorted = new_distorted
                changes.append(f"{original}→{replacement}")
                num_swaps -= 1

    distorted = f"{hallucination_prefix}\n\n{distorted}"
    return distorted, changes


# ---------------------------------------------------------------------------
# ai_client 지연 로드 (그룹 A에서 별도 생성; 없어도 폴백 동작)
# ---------------------------------------------------------------------------

def _get_ai_client():
    try:
        from utils import ai_client
        return ai_client
    except Exception:
        return None


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------

def apply(text, sanity, candidates=None):
    """이성 수치에 따라 텍스트를 왜곡한다.

    Args:
        text: 원문(조사/추적/소문 결과 등).
        sanity: 대상의 이성 수치(정수/실수).
        candidates: 오정보 치환에 쓸 후보 풀(명단/장소 등). None이면 마스킹까지만.

    Returns:
        (distorted_text, changes_list)
        - changes_list: 오정보에서 바꾼 지점("원본→대체") 리스트. 마스킹/무왜곡은 [].
    """
    if text is None:
        return text, []

    try:
        s = float(sanity)
    except (TypeError, ValueError):
        # 이성 수치를 알 수 없으면 안전하게 원문 유지
        return text, []

    distortion_th = _distortion_threshold()
    hallucination_th = _hallucination_threshold()

    # 1) 왜곡 없음 구간
    if s > distortion_th:
        return text, []

    ai = _get_ai_client()

    # 2) 오정보 치환 구간 (후보 풀이 있을 때만)
    if s <= hallucination_th and candidates:
        # AI 시도
        if ai is not None:
            try:
                result = ai.distort_false(text, list(candidates))
                if result is not None:
                    distorted, changes = result
                    if distorted:
                        return distorted, list(changes or [])
            except Exception as e:
                logger.warning(f"[distortion] distort_false 실패, 규칙 폴백: {e}")
        # 규칙 폴백
        return _rule_based_false(text, list(candidates))

    # 3) 마스킹 구간 (오정보 구간이지만 후보 풀이 없을 때도 마스킹으로 폴백)
    strength = _mask_strength_from_sanity(s)
    if ai is not None:
        try:
            masked = ai.distort_mask(text, strength)
            if masked:
                return masked, []
        except Exception as e:
            logger.warning(f"[distortion] distort_mask 실패, 규칙 폴백: {e}")
    return _rule_based_mask(text, strength), []

"""
이성(정신력) 왜곡 필터 - distortion

이성 수치에 따라 조사/추적/소문 등 출력 텍스트를 왜곡한다.
- 이성 > SANITY_DISTORTION_THRESHOLD(40): 왜곡 없음 (원문 그대로)
- SANITY_HALLUCINATION_THRESHOLD(20) < 이성 <= 40: 마스킹 (핵심 서술 구간을 '▓'로 검열 + 최하단 판독 불능 문구)
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

# 검열에 쓰는 문자('▓' 반복). 규칙/AI 양쪽 모두 이 문자로 가린다.
# 검열 흔적 판별도 이 문자로만 한다(원문에 흔한 '…'/'ㅡ' 오탐 방지).
_MASK_CHAR = '▓'
_MASK_MARKERS = ('▓',)

# 문장 분리(구분자 보존): 마침표/물음표/느낌표/말줄임/개행
_SENT_SPLIT_RE = re.compile(r'([.!?…。\n]+)')

# 검열된 출력 최하단에 붙는 판독 불능 문구
_CENSOR_FOOTER = "글자가 잘 읽히지 않는다. 머리가 깨질 것 같다."


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


def _reveal_count(word_len: int) -> int:
    """단어(어절) 길이별로 남길 글자 수.

    - 4글자 이하: 전부 가림(0글자 노출)
    - 5~7글자: 1글자만 노출
    - 8글자 이상: 2글자만 노출
    """
    if word_len <= 4:
        return 0
    if word_len <= 7:
        return 1
    return 2


def _mask_word(token: str) -> str:
    """한 단어(내용 어절)를 길이 규칙에 따라 일부만 남기고 ▓로 가린다.

    남길 글자의 위치는 매번 무작위로 흩뿌려 '부분적으로만 읽히는' 느낌을 준다.
        예) "엘레노어의"(5) → "▓레▓▓▓" / "푸르스름하게"(6) → "▓▓스▓▓▓"
    """
    n = len(token)
    reveal = _reveal_count(n)
    if reveal <= 0:
        return _MASK_CHAR * n
    positions = set(random.sample(range(n), min(reveal, n)))
    return ''.join(token[i] if i in positions else _MASK_CHAR for i in range(n))


def _mask_by_word_length(segment: str) -> str:
    """구간을 단어 단위로 잘라, 각 단어를 길이 규칙으로 가린다.

    공백·구두점·따옴표는 그대로 두고, 내용 어절(한글/영숫자 덩어리)만 규칙 적용.
    """
    return _TOKEN_RE.sub(lambda m: _mask_word(m.group(0)), segment)


def _mask_sentence(sentence: str, strength: float):
    """한 문장에서 앞 맥락만 남기고, 핵심 서술 구간을 단어 단위 길이 규칙으로 검열한다.

    정보의 핵심이 실리는 뒷부분(서술어·수식구)을 대상으로, 각 단어를 길이에 따라
    가린다(4↓ 전부 / 5~7 1글자 / 8↑ 2글자 노출). 앞 도입 맥락과 끝 구두점은 보존한다.
        예) "오금이 푸르스름하게 죽어있다" → "오금이 ▓▓스▓▓▓ ▓어▓▓"

    Returns:
        (masked_sentence, did_mask)
    """
    tokens = list(_TOKEN_RE.finditer(sentence))
    if len(tokens) < 2:
        # 어절이 하나뿐이면 가릴 맥락이 없다(그대로 둠).
        return sentence, False

    # 강도가 높을수록 남기는 앞 맥락이 적다(= 더 많은 단어를 가림).
    keep_ratio = max(0.1, 0.5 - 0.4 * strength)
    keep = max(1, round(len(tokens) * keep_ratio))
    keep = min(keep, len(tokens) - 1)  # 최소 한 어절은 반드시 가림

    start = tokens[keep].start()   # 가림 시작(앞 맥락 다음부터)
    end = tokens[-1].end()         # 마지막 어절 끝(뒤 구두점/공백은 보존)

    span = sentence[start:end]
    masked = _mask_by_word_length(span)
    return sentence[:start] + masked + sentence[end:], True


def _rule_based_mask(text: str, strength: float) -> str:
    """핵심 서술 구간을 '단어 단위 길이 규칙'(4↓ 전부 / 5~7 1글자 / 8↑ 2글자 노출)으로 검열하는 규칙 기반 마스킹.

    - 문장 단위로 나눠, 강도에 비례한 확률로 각 문장을 검열한다.
    - 검열된 문장은 앞 맥락만 남기고, 핵심 구간의 각 단어를 길이 규칙으로 가린다.
    - 확률 탓에 아무것도 안 가려졌으면 후보 한 문장은 강제로 가린다.
    - 남기는 글자 위치는 매 호출 무작위(검열은 매번 다르게).
    """
    if not text:
        return text

    strength = max(0.0, min(1.0, float(strength)))
    censor_prob = min(0.9, 0.35 + strength)

    parts = _SENT_SPLIT_RE.split(text)  # [문장, 구분자, 문장, ...]
    out = list(parts)
    did_mask = False
    skipped = []  # 확률로 건너뛴 문장 index(강제 마스킹 후보)

    for i, part in enumerate(parts):
        if i % 2 == 1 or not part.strip():
            continue  # 구분자/공백은 그대로
        if random.random() < censor_prob:
            masked, ok = _mask_sentence(part, strength)
            out[i] = masked
            did_mask = did_mask or ok
        else:
            skipped.append(i)

    # 확률 미스로 하나도 안 가려졌으면, 후보 중 하나는 강제로 검열한다.
    if not did_mask:
        for i in skipped:
            masked, ok = _mask_sentence(parts[i], strength)
            if ok:
                out[i] = masked
                break

    return ''.join(out)


def _looks_masked(masked: str, original: str) -> bool:
    """실제로 검열 흔적이 생겼는지(원문과 다르고 마스크 마커 포함) 판별."""
    return bool(masked) and masked != original and any(mk in masked for mk in _MASK_MARKERS)


def _with_censor_footer(masked: str, original: str) -> str:
    """검열 흔적이 있으면 최하단에 판독 불능 문구를 붙인다."""
    if _looks_masked(masked, original):
        return f"{masked}\n\n{_CENSOR_FOOTER}"
    return masked


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
    masked = None
    if ai is not None:
        try:
            ai_out = ai.distort_mask(text, strength)
            # AI가 실제로 가렸을 때만 채택. 원문을 거의 그대로 돌려주면(검열 흔적 없음)
            # 규칙 기반으로 폴백해 검열이 항상 이뤄지도록 보장한다.
            if ai_out and _looks_masked(ai_out, text):
                masked = ai_out
        except Exception as e:
            logger.warning(f"[distortion] distort_mask 실패, 규칙 폴백: {e}")
    if not masked:
        masked = _rule_based_mask(text, strength)
    return _with_censor_footer(masked, text), []

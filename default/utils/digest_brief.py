"""
utils/digest_brief.py — 소문 브리핑 렌더러 (시너몬트)

extract_seeds 가 뽑은 씨앗·참고를 **한 통짜리 브리핑 텍스트**로 편다.
개발안내서 §3·§8 의 포맷을 그대로 따른다:

    [소문 브리핑] N일차
    사건 63건 중 씨앗 7건 선별 · 활동 18명 · 조용 1명

    ── 소문 씨앗 (점수순)

    [씨앗 1] 에두아르도 · 점수 9
    근거: 어제 "누군가를 찾는다" → 오늘 "검열 탓" 진술 번복 (의무실)
    목격자 후보: 러셀
    각도: 밤마다 용접장에서 뭘 하는지 아무도 모른다더라

    ── 참고
    다중 표적: 존 3회, 클라라 3회, 휴고 3회
    미완 행동: 휴고 (클라라와 대화 시작 후 중단)
    침묵: 엘레노어

    원본 로그: 시트 아카이브 탭 N일차 참조

포맷 원칙(§8-1)
---------------
- 굵은 글씨·이모지·장식 괘선 금지. 구분선은 '──' 하나만.
- 씨앗은 4줄 고정. 각도 줄은 AI가 못 채우면 **통째로 생략**(§12: 3단계까지 각도 없이 발송).
- 목격자 후보 줄도 없으면 생략.

안전
----
이 텍스트에는 슬롯 소견·AI 각도처럼 defang 을 안 거친 남의 글이 섞인다.
render_brief 는 반환 직전 mention_guard.defang 으로 본문의 '@'를 죽인다
(daily_digest.send_thread 가 3단계 최후 방어를 한 번 더 한다).
"""

import os
import sys
from typing import Any, Dict, List, Optional

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from utils import mention_guard

try:
    from config.settings import config
except Exception:  # pragma: no cover
    config = None  # type: ignore

DIVIDER = '──'
ARCHIVE_NOTE_DEFAULT = '시트 아카이브 탭'


def _cfg_int(name: str, default: int) -> int:
    try:
        return int(getattr(config, name, default))
    except Exception:
        return default


def render_brief(day: Any, result: Dict[str, Any],
                 archive_note: Optional[str] = None) -> str:
    """씨앗·참고를 한 편의 브리핑 텍스트로.

    Args:
        day: 일차.
        result: digest_seeds.extract_seeds() 반환 dict.
        archive_note: 원본 로그 안내 문구. None 이면 기본값, ''면 줄 생략.

    Returns:
        완성된 브리핑 본문(멘션 무력화 완료). 분할·번호매김은 호출측(daily_digest)이 한다.
    """
    seeds = result.get('seeds') or []
    reference = result.get('reference') or {}
    stats = result.get('stats') or {}

    lines: List[str] = []
    lines.append(f"[소문 브리핑] {day}일차")
    lines.append(_headline(stats))
    lines.append('')

    if seeds:
        lines.append(f"{DIVIDER} 소문 씨앗 (점수순)")
        for i, seed in enumerate(seeds, 1):
            lines.append('')
            lines.extend(_seed_block(i, seed))
    else:
        # §11 엣지: 빈 보고를 안 보내면 봇 장애와 구분이 안 된다.
        lines.append(f"{DIVIDER} 소문 씨앗")
        lines.append('오늘은 소문 씨앗 없음.')

    ref_lines = _reference_block(reference)
    if ref_lines:
        lines.append('')
        lines.append(f"{DIVIDER} 참고")
        lines.extend(ref_lines)

    note = ARCHIVE_NOTE_DEFAULT if archive_note is None else archive_note
    if note:
        lines.append('')
        lines.append(f"원본 로그: {note} {day}일차 참조")

    text = '\n'.join(lines).strip()
    # 소견·각도 등 defang 안 거친 인용이 섞여 있다 — 여기서 본문 '@'를 죽인다.
    return mention_guard.defang(text)


# --------------------------------------------------------------------------- #
def _headline(stats: Dict[str, Any]) -> str:
    incidents = stats.get('incidents', 0)
    seeds = stats.get('seeds', 0)
    active = stats.get('active', 0)
    silent = stats.get('silent', 0)
    return (f"사건 {incidents}건 중 씨앗 {seeds}건 선별 · "
            f"활동 {active}명 · 조용 {silent}명")


def _seed_block(idx: int, seed) -> List[str]:
    """씨앗 4줄(§8-1). 각도·목격자 줄은 비면 생략."""
    out = [f"[씨앗 {idx}] {seed.subject} · 점수 {seed.score}"]

    evidence = _clip(' · '.join(seed.evidence), _cfg_int('DIGEST_BRIEF_EVIDENCE_CHARS', 220))
    out.append(f"근거: {evidence}" if evidence else "근거: (요약 없음)")

    if seed.witnesses:
        out.append(f"목격자 후보: {', '.join(seed.witnesses)}")
    if seed.angle:
        out.append(f"각도: {seed.angle}")
    return out


def _reference_block(reference: Dict[str, Any]) -> List[str]:
    """§8-2 참고. 최대 몇 줄. 비면 줄 자체를 안 만든다."""
    out: List[str] = []

    multi = reference.get('multi_target') or []
    if multi:
        out.append("다중 표적: " + ", ".join(f"{n} {c}회" for n, c in multi))

    incomplete = reference.get('incomplete') or []
    if incomplete:
        out.append("미완 행동: " + " / ".join(incomplete))

    silence = reference.get('silence') or []
    if silence:
        out.append("침묵: " + ", ".join(silence))

    anomaly = reference.get('anomaly') or []
    if anomaly:
        out.append("수치 특이: " + " / ".join(anomaly))

    return out


def _clip(text: str, limit: int) -> str:
    text = (text or '').strip()
    if limit and len(text) > limit:
        return text[:limit - 1].rstrip() + '…'
    return text

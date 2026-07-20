"""
utils/digest_seeds.py — 이벤트에서 '소문 씨앗'을 뽑는다 (시너몬트 소문 브리핑)

정규화된 Event 리스트(digest_events)를 받아, 소문이 될 만한 것만 골라 점수를 매기고
사람/쌍 기준으로 묶어 **씨앗**을 만든다. AI 각도 문장은 여기서 만들지 않는다 —
확정된 씨앗의 '근거'만 넘기면 daily_digest 가 ai_client.rumor_angle 로 채운다.

무엇을 버리고 무엇을 올리나 (개발안내서 §5)
--------------------------------------------
제외(E): 소문 재료가 못 되는 잡음. 단독 조사·진입 로그·소액 소비·빈 지령 보고·
         잔챙이 도박·수치 나열. 대부분 '신호가 아예 안 생기게' 하는 방식으로 처리된다
         (예: 결과 없는 단독 조사는 어떤 I 규칙에도 안 걸리므로 저절로 빠진다).
포함(I): 씨앗 후보. 접촉·집중 표적·진술 모순·은밀·수치 이상·미완·침묵·동선 겹침.

점수와 병합 (개발안내서 §6·§10)
-------------------------------
각 신호에 §6-1 점수를 매기고, **사람으로 얽힌 신호는 한 씨앗으로 합친다.**
얽힘은 union-find 로 푼다: 쌍 신호(비밀 대화·상호 접촉·동선 겹침)가 두 사람을 잇고,
개인 신호(모순·수치·미완 등)는 자기가 속한 덩어리에 붙는다. 한 사람은 정확히
한 덩어리에만 속하므로 "한 명이 씨앗을 도배"하는 일이 구조적으로 막힌다(§11).

이 모듈은 시트·슬롯·AI 를 건드리지 않는다. 순수 함수다(테스트하기 쉽게).
"""

import os
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from utils import digest_events as ev
from utils.digest_events import Event


# 설정 기본값 — config.settings 에 같은 이름이 있으면 그쪽이 이긴다(_cfg).
# 개발안내서 §9 의 값을 그대로 옮겼다.
DEFAULTS: Dict[str, Any] = {
    'DIGEST_MAX_SEEDS': 8,
    'DIGEST_MIN_SEED_SCORE': 4,
    'DIGEST_GAMBLE_THRESHOLD': 5,
    'DIGEST_MONEY_RATIO': 2.5,
    'DIGEST_MONEY_FLOOR': 15,
    'DIGEST_SANITY_DROP': 8,
    'DIGEST_HEALTH_DROP': 10,
    'DIGEST_WITNESS_WINDOW_MIN': 60,
    'DIGEST_CONTRADICTION_KEYWORDS':
        ['번복', '불일치', '함구', '얼버무', '방어적', '어긋남', '캐물었으나', '말이 바뀌'],
    'DIGEST_SECRET_KEYWORDS':
        ['서명은 없었다', '적지 말아', '눈에 띄지 않는', '사본을 보관', '갖고 있어 주게'],
}

# §6-1 기본 점수표. 신호 코드 → 점수.
PTS_CONTRADICTION = 4      # I3 진술 모순
PTS_SECRET_TALK = 4        # I4 비밀 대화
PTS_SECRET_DIRECTIVE = 3   # I4 은밀 지령
PTS_MUTUAL = 3             # I1 상호 접촉
PTS_OVERLAP = 3            # I8 동선 겹침
PTS_ANOMALY = 2            # I5 수치 이상(보통)
PTS_ANOMALY_EXTREME = 4    # I5 수치 이상(임계값 크게 초과 → 단독 씨앗감, §6-2)
PTS_INCOMPLETE = 2         # I6 미완
PTS_SILENCE = 2            # I7 침묵
PTS_OVERROGATION = 2       # 과잉심문 처치(§11 엣지)
PTS_WITNESS = 2            # §6-1 목격자 보너스
PTS_PER_TARGET = 1         # I2 표적 1회당


def _cfg(name: str) -> Any:
    try:
        from config.settings import config
        val = getattr(config, name, None)
        if val is not None:
            return val
    except Exception:
        pass
    return DEFAULTS.get(name)


@dataclass
class Seed:
    """소문 씨앗 하나. 렌더러(digest_brief)가 4줄로 편다."""
    subject: str                       # "에두아르도" 또는 "에블린 ↔ 휴고"
    people: List[str]
    score: int
    signals: List[str] = field(default_factory=list)    # 붙은 신호 라벨(디버깅/정렬 근거)
    evidence: List[str] = field(default_factory=list)   # 근거 줄(운영자용, 정확하게)
    witnesses: List[str] = field(default_factory=list)
    angle: str = ''                    # AI 각도(나중에 채움). 비면 렌더러가 줄을 생략.


@dataclass
class _Signal:
    code: str
    people: Tuple[str, ...]            # 1명 또는 2명
    points: int
    label: str                         # 짧은 신호명
    evidence: str                      # 사람이 읽는 근거 한 줄
    target: Optional[str] = None       # 목격자 계산용
    minute: Optional[int] = None
    count: int = 0                     # I2 표적 횟수 등(참고 파트용)


# --------------------------------------------------------------------------- #
# 공개 진입점
# --------------------------------------------------------------------------- #
def extract_seeds(events: List[Event],
                  facts: Optional[Dict[str, Any]] = None,
                  prev_stats: Optional[Dict[str, Dict[str, Any]]] = None
                  ) -> Dict[str, Any]:
    """이벤트에서 씨앗과 참고 자료를 뽑는다.

    Args:
        events: digest_events.normalize() 결과.
        facts:  수치 이상(§6-2) 계산용 stats·roster. 없으면 수치 이상은 건너뛴다.
        prev_stats: 전일 스냅샷({이름:{건강,이성}}). 없으면 이성·건강 하락 감지 생략(첫날).

    Returns:
        {'seeds': [Seed...], 'reference': {...}, 'stats': {...}}
    """
    facts = facts or {}
    # 신호는 한 번만 만든다 — 병합(씨앗)과 참고 파트가 같은 목록을 나눠 쓴다.
    signals = _build_signals(events, facts, prev_stats)

    seeds = _merge_into_seeds(signals)          # 신호 점수를 합산해 score 를 채운다
    seeds = _apply_witness_bonus(seeds, events)  # 목격자 있으면 +2

    seeds.sort(key=lambda s: (s.score, _recency(s, events)), reverse=True)

    min_score = int(_cfg('DIGEST_MIN_SEED_SCORE'))
    max_seeds = int(_cfg('DIGEST_MAX_SEEDS'))
    qualified = [s for s in seeds if s.score >= min_score]
    chosen = qualified[:max_seeds]

    reference = _build_reference(signals, chosen)
    stats = _headline_stats(events, facts, chosen)
    return {'seeds': chosen, 'reference': reference, 'stats': stats}


# --------------------------------------------------------------------------- #
# 신호 만들기 (I1~I8 + 보너스)
# --------------------------------------------------------------------------- #
def _build_signals(events: List[Event], facts: Dict[str, Any],
                   prev_stats: Optional[Dict[str, Dict[str, Any]]]) -> List[_Signal]:
    out: List[_Signal] = []
    out += _contact_signals(events)          # I1 상호, I2 집중
    out += _secret_signals(events)           # I4 비밀 대화 / 은밀 지령
    out += _contradiction_signals(events)    # I3 + 과잉심문
    out += _overlap_signals(events)          # I8 동선 겹침
    out += _incomplete_silence_signals(events)  # I6, I7
    out += _anomaly_signals(events, facts, prev_stats)  # I5
    return out


def _contact_signals(events: List[Event]) -> List[_Signal]:
    """I1 상호 접촉(+3, 쌍) · I2 집중 표적(+표적수, 개인).

    접촉 = 추적·대화·교류·고발. '대상'이 사람인 이벤트만 본다(정규화가 이미 보장).
    """
    directed = set()
    incoming: Dict[str, int] = {}
    for e in events:
        if e.type not in ev.CONTACT_TYPES or not e.target:
            continue
        directed.add((e.actor, e.target))
        incoming[e.target] = incoming.get(e.target, 0) + 1

    out: List[_Signal] = []

    seen_pair = set()
    for a, b in directed:
        if (b, a) in directed:
            pair = tuple(sorted((a, b)))
            if pair in seen_pair:
                continue
            seen_pair.add(pair)
            out.append(_Signal(
                code='I1', people=pair, points=PTS_MUTUAL, label='상호 접촉',
                evidence=f"{pair[0]}·{pair[1]} 서로를 향한 행동",
            ))

    for name, cnt in incoming.items():
        if cnt >= 2:
            out.append(_Signal(
                code='I2', people=(name,), points=cnt * PTS_PER_TARGET,
                label=f"다중 표적 {cnt}회",
                evidence=f"{name} 오늘 {cnt}번 표적", target=name, count=cnt,
            ))
    return out


def _secret_signals(events: List[Event]) -> List[_Signal]:
    """I4 은밀. 비밀 대화(+4, 쌍) · 은밀 지령(+3, 개인).

    대화는 전부 비밀(secret=True)이라 그대로 +4. 지령은 본문에 SECRET_KEYWORDS 가
    있을 때만 은밀로 본다 — 무기명 쪽지·장부 누락 요청 등.
    """
    secret_kw = _cfg('DIGEST_SECRET_KEYWORDS') or []
    out: List[_Signal] = []
    seen_talk = set()
    for e in events:
        if e.type == ev.T_TALK and e.secret and e.target:
            pair = tuple(sorted((e.actor, e.target)))
            if pair in seen_talk:
                continue
            seen_talk.add(pair)
            out.append(_Signal(
                code='I4', people=pair, points=PTS_SECRET_TALK, label='비밀 대화',
                evidence=f"{pair[0]} ↔ {pair[1]} 비밀 대화",
            ))
        elif e.type == ev.T_DIRECTIVE and _has_kw(e.detail, secret_kw):
            out.append(_Signal(
                code='I4', people=(e.actor,), points=PTS_SECRET_DIRECTIVE,
                label='은밀 지령', evidence=f"{e.actor} 은밀한 지령 수행",
            ))
    return out


def _contradiction_signals(events: List[Event]) -> List[_Signal]:
    """I3 진술 모순(+4) + 과잉심문 처치(+2). 둘 다 의무실 이벤트에서 나온다."""
    kw = _cfg('DIGEST_CONTRADICTION_KEYWORDS') or []
    out: List[_Signal] = []
    for e in events:
        if e.type != ev.T_INFIRMARY:
            continue
        hit = _first_kw(e.detail, kw)
        if hit:
            out.append(_Signal(
                code='I3', people=(e.actor,), points=PTS_CONTRADICTION,
                label='진술 모순', evidence=f"{e.actor} 진료 중 '{hit}' (의무실)",
            ))
        blob = f"{e.detail or ''} {e.result or ''}"
        if '과잉심문' in blob:
            out.append(_Signal(
                code='I3+', people=(e.actor,), points=PTS_OVERROGATION,
                label='과잉심문', evidence=f"{e.actor} 과잉심문 처치",
            ))
    return out


def _overlap_signals(events: List[Event]) -> List[_Signal]:
    """I8 동선 겹침(+3, 쌍). 서로 다른 두 사람이 같은 세부 장소를 조사.

    단독 조사는 E1 로 버려지지만, 장소가 겹치면 "같은 걸 캐고 있다"는 각도가 생긴다.
    location 은 '장소 / 포인트' 단위라, 같은 포인트를 팠을 때만 겹침으로 본다.
    """
    by_loc: Dict[str, set] = {}
    for e in events:
        if e.type == ev.T_INVESTIGATE and e.location:
            by_loc.setdefault(e.location, set()).add(e.actor)

    out: List[_Signal] = []
    seen_pair = set()
    for loc, actors in by_loc.items():
        actors = sorted(a for a in actors if a)
        for i in range(len(actors)):
            for j in range(i + 1, len(actors)):
                pair = (actors[i], actors[j])
                if pair in seen_pair:
                    # 같은 쌍이 여러 포인트에서 겹쳐도 신호는 한 번(점수 중복 방지)
                    continue
                seen_pair.add(pair)
                out.append(_Signal(
                    code='I8', people=pair, points=PTS_OVERLAP, label='동선 겹침',
                    evidence=f"{pair[0]}·{pair[1]} 같은 곳 조사 ({_place(loc)})",
                ))
    return out


def _incomplete_silence_signals(events: List[Event]) -> List[_Signal]:
    """I6 미완(+2) · I7 침묵(+2). 둘 다 개인 신호."""
    out: List[_Signal] = []
    for e in events:
        if e.type == ev.T_INCOMPLETE:
            out.append(_Signal(
                code='I6', people=(e.actor,), points=PTS_INCOMPLETE,
                label='미완 행동', evidence=f"{e.actor} {e.detail or '하려다 만 행동'}",
            ))
        elif e.type == ev.T_SILENCE:
            out.append(_Signal(
                code='I7', people=(e.actor,), points=PTS_SILENCE,
                label='침묵', evidence=f"{e.actor} 오늘 전 항목 무기록",
            ))
    return out


def _anomaly_signals(events: List[Event], facts: Dict[str, Any],
                     prev_stats: Optional[Dict[str, Dict[str, Any]]]) -> List[_Signal]:
    """I5 수치 이상(§6-2). 소지금·도박은 오늘 값만으로, 이성·건강은 전일 대비.

    임계값을 크게 넘으면(도박 2배·소지금 3배) 단독 씨앗감이라 +4로 올린다(§6-2).
    이성·건강 하락은 prev_stats 가 있어야 잰다 — 없으면(첫날/미제공) 건너뛴다.
    """
    out: List[_Signal] = []
    stats = facts.get('stats') or {}

    # ── 소지금 ──
    amounts: Dict[str, int] = {}
    for name, s in stats.items():
        v = _num(s.get('소지금'))
        if v is not None:
            amounts[name] = v
    if amounts:
        avg = sum(amounts.values()) / len(amounts)
        ratio = float(_cfg('DIGEST_MONEY_RATIO'))
        floor = float(_cfg('DIGEST_MONEY_FLOOR'))
        for name, v in amounts.items():
            extreme = avg > 0 and v >= avg * 3
            if avg > 0 and v > avg * ratio:
                note = f"소지금 {v}달러(평균 {avg:.0f}의 {v/avg:.1f}배)"
                out.append(_anomaly(name, note, extreme))
            elif v < floor:
                out.append(_anomaly(name, f"소지금 {v}달러(바닥)", extreme=False))

    # ── 도박 ──
    thr = int(_cfg('DIGEST_GAMBLE_THRESHOLD'))
    for e in events:
        if e.type == ev.T_GAMBLE and e.count >= thr:
            extreme = e.count >= thr * 2
            out.append(_anomaly(e.actor, f"도박 {e.count}회 ({e.detail})", extreme))

    # ── 이성·건강 하락(전일 대비) ──
    if prev_stats:
        sdrop = int(_cfg('DIGEST_SANITY_DROP'))
        hdrop = int(_cfg('DIGEST_HEALTH_DROP'))
        for name, s in stats.items():
            prev = prev_stats.get(name) or {}
            for label, key, thr_drop in (('이성', '이성', sdrop), ('건강', '건강', hdrop)):
                now, was = _num(s.get(key)), _num(prev.get(key))
                if now is not None and was is not None and was - now >= thr_drop:
                    out.append(_anomaly(name, f"{label} {was}→{now} 급락", extreme=False))
    return out


def _anomaly(name: str, note: str, extreme: bool) -> _Signal:
    return _Signal(
        code='I5', people=(name,),
        points=PTS_ANOMALY_EXTREME if extreme else PTS_ANOMALY,
        label='수치 이상', evidence=f"{name} {note}",
    )


# --------------------------------------------------------------------------- #
# 병합 (union-find)
# --------------------------------------------------------------------------- #
def _merge_into_seeds(signals: List[_Signal]) -> List[Seed]:
    """사람으로 얽힌 신호를 한 씨앗으로. 쌍 신호가 잇고, 개인 신호는 붙는다.

    §11 "한 사람이 씨앗 3개 이상에 등장 → 인물 기준 병합"은 union-find 로 저절로
    지켜진다: 한 사람은 한 덩어리에만 속한다.
    """
    parent: Dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for s in signals:
        for p in s.people:
            find(p)
        if len(s.people) == 2:
            union(s.people[0], s.people[1])

    groups: Dict[str, List[_Signal]] = {}
    for s in signals:
        root = find(s.people[0])
        groups.setdefault(root, []).append(s)

    seeds: List[Seed] = []
    for root, sigs in groups.items():
        people = sorted({p for s in sigs for p in s.people})
        seeds.append(Seed(
            subject=_subject(people),
            people=people,
            score=sum(s.points for s in sigs),   # §6-1 신호 점수 합산
            signals=[s.label for s in sigs],
            evidence=_dedup([s.evidence for s in sigs]),
        ))
    return seeds


def _apply_witness_bonus(seeds: List[Seed], events: List[Event]) -> List[Seed]:
    """§6-1 목격자 보너스(+2). 씨앗 밖의 제3자가 같은 대상을 같은 시간대에 건드렸나.

    시각을 못 읽은 이벤트는 시간 창을 무시하고 '같은 대상'만으로 본다.
    """
    window = int(_cfg('DIGEST_WITNESS_WINDOW_MIN'))
    contacts = [e for e in events if e.type in ev.CONTACT_TYPES and e.target]

    for s in seeds:
        pset = set(s.people)
        # 씨앗 안의 접촉이 향한 대상들 + 그 시각
        targets: Dict[str, List[Optional[int]]] = {}
        for e in contacts:
            if e.actor in pset:
                targets.setdefault(e.target, []).append(e.minute)
        witnesses = []
        for e in contacts:
            if e.actor in pset or e.target not in targets:
                continue
            if _within(e.minute, targets[e.target], window):
                witnesses.append(e.actor)
        if witnesses:
            s.witnesses = _dedup(witnesses)
            s.signals.append('목격자')
            s.score += PTS_WITNESS   # §6-1 목격자 보너스
    return seeds


# --------------------------------------------------------------------------- #
# 참고 파트 · 헤드라인
# --------------------------------------------------------------------------- #
def _build_reference(signals: List[_Signal], chosen: List[Seed]) -> Dict[str, Any]:
    """§8-2 참고. 씨앗에 안 오른 다중 표적·미완·침묵·수치 이상 요약.

    다중 표적은 전역 집계라 씨앗 여부와 무관하게 보여준다(개발안내서 §3 예시 준수).
    나머지(미완·침묵·수치)는 이미 씨앗에 오른 사람은 뺀다 — 같은 정보를 두 번 싣지 않는다.
    신호를 재계산하지 않고 extract_seeds 가 만든 목록을 그대로 나눠 쓴다.
    """
    seeded = {p for s in chosen for p in s.people}

    multi, incomplete, silence, anomaly = [], [], [], []
    for sig in signals:
        who = sig.people[0]
        if sig.code == 'I2':
            multi.append((who, sig.count))          # 전역 집계(씨앗 여부 무관)
        elif who in seeded:
            continue
        elif sig.code == 'I6':
            incomplete.append(sig.evidence)
        elif sig.code == 'I7':
            silence.append(who)
        elif sig.code == 'I5':
            anomaly.append(sig.evidence)

    multi.sort(key=lambda x: (-x[1], x[0]))
    return {
        'multi_target': multi,
        'incomplete': _dedup(incomplete),
        'silence': _dedup(silence),
        'anomaly': _dedup(anomaly),
    }


def _headline_stats(events: List[Event], facts: Dict[str, Any],
                    chosen: List[Seed]) -> Dict[str, int]:
    actions = facts.get('actions')
    incidents = len(actions) if actions is not None else sum(
        1 for e in events if e.type not in (ev.T_SILENCE,))
    active = {e.actor for e in events if e.type != ev.T_SILENCE and e.actor}
    silent = sum(1 for e in events if e.type == ev.T_SILENCE)
    return {
        'incidents': incidents,
        'seeds': len(chosen),
        'active': len(active),
        'silent': silent,
    }


# --------------------------------------------------------------------------- #
# 소도구
# --------------------------------------------------------------------------- #
def _recency(seed: Seed, events: List[Event]) -> int:
    """동점 정렬용. 씨앗에 얽힌 이벤트 중 가장 늦은 분(§6-1 '동점이면 최근 우선')."""
    best = -1
    pset = set(seed.people)
    for e in events:
        if e.actor in pset and e.minute is not None:
            best = max(best, e.minute)
    return best


def _subject(people: List[str]) -> str:
    if len(people) == 1:
        return people[0]
    return ' ↔ '.join(people)


def _within(minute: Optional[int], others: List[Optional[int]], window: int) -> bool:
    """minute 가 others 중 하나와 window 분 이내인가. 시각 불명이면 True(대상만으로 인정)."""
    valid = [m for m in others if m is not None]
    if minute is None or not valid:
        return True
    return any(abs(minute - m) <= window for m in valid)


def _has_kw(text: Optional[str], keywords: List[str]) -> bool:
    return _first_kw(text, keywords) is not None


def _first_kw(text: Optional[str], keywords: List[str]) -> Optional[str]:
    if not text:
        return None
    for kw in keywords:
        if kw and kw in text:
            return kw
    return None


def _num(value: Any) -> Optional[int]:
    try:
        return int(float(str(value).strip()))
    except (ValueError, TypeError, AttributeError):
        return None


def _place(loc: str) -> str:
    return (loc or '').split(' / ')[-1] or loc


def _dedup(items: List[str]) -> List[str]:
    seen, out = set(), []
    for x in items:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out

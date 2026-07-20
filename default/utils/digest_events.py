"""
utils/digest_events.py — 하루치 사실을 '이벤트'로 정규화한다 (시너몬트 소문 브리핑)

`digest_facts.fetch_facts()` 가 모아 온 잡다한 사실(행동로그 행·조사 로그·부탁지령·
슬롯 JSON)을 **공통 Event 객체**로 바꾼다. 필터(digest_seeds)와 점수 계산이
종류마다 다른 형태의 원본을 직접 다루지 않게 하는 게 목적이다.

왜 정규화가 먼저인가
--------------------
소문 씨앗을 뽑으려면 "누가 · 누구에게 · 어디서 · 무엇을 · 그게 은밀했나"를
한 줄로 물어야 한다. 그런데 원본은 종류마다 그 정보가 다른 칸에 흩어져 있다:

    조사   → '대상' 칸이 사람이 아니라 **장소**다. 결과는 조사 로그에 따로 있다.
    지령   → '대상' 칸이 **자기 자신**이다. 본문은 부탁지령 시트에 있다.
    대화   → 행동로그엔 **최초 상대 하나**뿐. 2차 상대는 @STORY 슬롯 JSON 에만 있다.
    도박   → 행동로그엔 **금액만**. 게임별 횟수는 @BAR 슬롯 JSON 에만 있다.

Event 로 한번 펴 놓으면 필터·점수는 이 왜곡을 다시 신경 쓰지 않아도 된다.

읽기 전용이다. 이 모듈은 어떤 시트·슬롯에도 쓰지 않는다.

개발안내서 §4(이벤트 정규화) 대응.
"""

import os
import re
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

try:
    from utils import action_log
except ImportError:  # 부분 환경/테스트
    action_log = None  # type: ignore

try:
    from utils import digest_facts
except ImportError:
    digest_facts = None  # type: ignore


# 이벤트 종류 — 개발안내서 §4 의 type 열거값.
# 행동로그 종류(action_log.KIND_*)와 다르다: 여기는 '소문 관점'의 분류다.
#   · 조사/추적/대화/교류/지령/의무실 = 행동로그에서 옴
#   · 도박/미완/침묵                  = 슬롯 JSON·집계에서 파생(행동로그에 없다)
T_INVESTIGATE = '조사'
T_TRACK = '추적'
T_TALK = '대화'
T_EXCHANGE = '교류'
T_DIRECTIVE = '지령'
T_INFIRMARY = '의무실'
T_ACCUSE = '고발'
T_GAMBLE = '도박'
T_INCOMPLETE = '미완'
T_SILENCE = '침묵'

# 사람과 사람 사이의 접촉으로 세는 종류(관계망·집중 표적 판정에 쓴다).
# 조사는 대상이 장소, 지령은 대상이 자기 자신이라 접촉이 아니다.
CONTACT_TYPES = (T_TRACK, T_TALK, T_EXCHANGE, T_ACCUSE)

_TIME_RE = re.compile(r'(\d{1,2}):(\d{2})')


@dataclass
class Event:
    """개발안내서 §4 의 Event. 소문 판정의 최소 단위.

    필드는 안내서 스펙을 그대로 따르되, 파생 계산용 보조 필드 몇 개를 뒤에 붙였다
    (count·minute·source). 스펙 필드만 보면 소문 한 줄을 재구성할 수 있어야 한다.
    """
    id: str
    day: int
    type: str
    actor: str
    target: Optional[str] = None      # 대상 인물 (추적·대화·교류·고발). 장소·자기자신은 넣지 않는다.
    location: Optional[str] = None     # 구역 / 세부 장소 (조사)
    result: Optional[str] = None       # "조사", "아스피린 1 획득" 등
    detail: Optional[str] = None       # 지령 본문·진료 소견·보고 내용 등 자유 텍스트
    secret: bool = False               # 비밀 대화·무기명 지령 등 은밀 플래그
    timestamp: str = ''
    # ── 보조(스펙 외) ──
    count: int = 0                     # 도박 횟수·집계 등
    minute: Optional[int] = None       # timestamp 를 분으로 (목격자 ±60분 판정용)
    source: str = ''                   # 어디서 왔는지(디버깅/근거용)


def _s(value: Any) -> str:
    if value is None:
        return ''
    return str(value).strip()


def _minute_of(ts: str) -> Optional[int]:
    """'2026-07-16 21:35:00' / '07-16 21:35' 등에서 하루 중 분(0~1439)을 뽑는다.

    목격자 보너스(±60분)만을 위한 거친 파싱이다. 못 읽으면 None — 그 경우
    목격자 판정은 시간 창을 무시하고 '같은 대상'만으로 본다(digest_seeds).
    """
    m = _TIME_RE.search(ts or '')
    if not m:
        return None
    try:
        h, mm = int(m.group(1)), int(m.group(2))
    except (ValueError, TypeError):
        return None
    if 0 <= h < 24 and 0 <= mm < 60:
        return h * 60 + mm
    return None


class _IdGen:
    """결정론적 이벤트 id. (종류 + 일련번호) — 랜덤·시각을 쓰지 않는다."""
    def __init__(self, day: int):
        self._day = day
        self._n = 0

    def next(self, kind: str) -> str:
        self._n += 1
        return f"d{self._day}-{kind}-{self._n}"


def normalize(facts: Dict[str, Any]) -> List[Event]:
    """하루치 facts 를 Event 리스트로.

    Args:
        facts: digest_facts.fetch_facts() 반환 dict.

    Returns:
        Event 리스트(정렬은 하지 않는다 — 점수 단계가 정렬한다).
    """
    day = _int(facts.get('day'), 0)
    ids = _IdGen(day)
    name_by_id = facts.get('name_by_id') or {}

    events: List[Event] = []
    events += _investigate_events(facts, ids)
    events += _contact_events(facts, ids)         # 추적·대화·교류·고발
    events += _secondary_talk_events(facts, ids, name_by_id)
    events += _directive_events(facts, ids)
    events += _infirmary_events(facts, ids)
    events += _gamble_events(facts, ids, name_by_id)
    events += _incomplete_events(facts, ids)
    events += _silence_events(facts, ids)
    return events


# --------------------------------------------------------------------------- #
# 종류별 변환
# --------------------------------------------------------------------------- #
def _investigate_events(facts: Dict[str, Any], ids: _IdGen) -> List[Event]:
    """조사. 대상 칸은 **장소**이므로 location 에 넣고 target 은 비운다.

    포인트별 결과는 조사 로그(facts['investigations'])에 있다. (행위자, 장소) 로 묶어
    포인트 하나당 이벤트 하나를 만든다 — I8(동선 겹침)이 세부 장소로 겹침을 보기 때문.
    결과가 붙는 포인트가 하나도 없으면(아무것도 못 얻은 단독 조사) 장소 단위로 한 건만
    남긴다. result='조사' 는 나중에 E1(제외)의 표식이 된다.
    """
    if action_log is None:
        return []
    out: List[Event] = []

    # (행위자, 장소) → [포인트 결과...]
    by_place: Dict[Any, List[Dict[str, str]]] = {}
    for inv in facts.get('investigations') or []:
        key = (_s(inv.get('캐릭터명')), _s(inv.get('장소명')))
        by_place.setdefault(key, []).append(inv)

    seen_place = set()   # 같은 (행위자,장소)를 두 번 조사해도 포인트는 한 번만 편다
    for row in facts.get('actions') or []:
        if _s(row.get('종류')) != action_log.KIND_INVESTIGATE:
            continue
        actor = _s(row.get('행위자'))
        place = _s(row.get('대상'))
        if not actor:
            continue
        key = (actor, place)
        points = by_place.get(key)
        ts = _s(row.get('일시'))
        summary = _s(row.get('요약'))
        if points and key not in seen_place:
            seen_place.add(key)
            for p in points:
                pname = _s(p.get('포인트명'))
                loc = f"{place} / {pname}" if pname else place
                out.append(Event(
                    id=ids.next(T_INVESTIGATE), day=_int(row.get('일차'), 0),
                    type=T_INVESTIGATE, actor=actor, target=None,
                    location=loc, result=_s(p.get('결과')) or T_INVESTIGATE,
                    detail=summary, timestamp=ts, minute=_minute_of(ts),
                    source='조사로그',
                ))
        elif not points:
            # 결과가 붙지 않은 조사 — 얻은 게 없다. E1 후보.
            out.append(Event(
                id=ids.next(T_INVESTIGATE), day=_int(row.get('일차'), 0),
                type=T_INVESTIGATE, actor=actor, target=None,
                location=place or None, result=T_INVESTIGATE,
                detail=summary, timestamp=ts, minute=_minute_of(ts),
                source='행동로그',
            ))
    return out


def _contact_events(facts: Dict[str, Any], ids: _IdGen) -> List[Event]:
    """추적·대화·교류·고발 — 사람↔사람 접촉.

    대화는 이 세계관에서 전부 '비밀 대화'다(talk_command: "으슥한 곳에서 비밀 대화").
    그래서 대화 이벤트는 항상 secret=True — I4(은밀)로 잡힌다.
    고발은 §4 열거값엔 없지만, 남을 표적으로 삼는 접촉이라 집중 표적(I2)·관계망(I1)에
    필요하다. 버리지 않고 접촉 이벤트로 살린다.
    """
    if action_log is None:
        return []
    kind_map = {
        action_log.KIND_TRACK: T_TRACK,
        action_log.KIND_TALK: T_TALK,
        action_log.KIND_EXCHANGE: T_EXCHANGE,
        action_log.KIND_ACCUSE: T_ACCUSE,
    }
    reason_by_pair = {
        (_s(a.get('고발자')), _s(a.get('대상'))): _s(a.get('사유'))
        for a in facts.get('accusations') or []
    }
    out: List[Event] = []
    for row in facts.get('actions') or []:
        etype = kind_map.get(_s(row.get('종류')))
        if not etype:
            continue
        actor = _s(row.get('행위자'))
        target = _s(row.get('대상'))
        if not actor or not target or actor == target:
            continue
        ts = _s(row.get('일시'))
        detail = _s(row.get('요약'))
        if etype == T_ACCUSE:
            detail = reason_by_pair.get((actor, target)) or detail
        out.append(Event(
            id=ids.next(etype), day=_int(row.get('일차'), 0), type=etype,
            actor=actor, target=target, detail=detail,
            secret=(etype == T_TALK), timestamp=ts, minute=_minute_of(ts),
            source='행동로그',
        ))
    return out


def _secondary_talk_events(facts: Dict[str, Any], ids: _IdGen,
                           name_by_id: Dict[str, str]) -> List[Event]:
    """2차 대화 상대. 행동로그엔 최초 상대만 남는다 — @STORY 슬롯이 유일한 기록.

    A가 B·C·D와 얘기했는데 시트엔 "A→B"만 있으면, C·D 접촉이 통째로 사라져
    관계망·목격자 판정이 틀어진다. 슬롯의 '오늘대화상대'에서 최초 상대를 뺀
    나머지를 접촉 이벤트로 보탠다(secret=True, 시각 없음).
    """
    story = (facts.get('slots') or {}).get('story') or {}
    talks = story.get('대화') or {}
    # 이미 행동로그에 잡힌 (행위자, 대상) 대화는 중복 생성하지 않는다.
    logged_pairs = set()
    if action_log is not None:
        for row in facts.get('actions') or []:
            if _s(row.get('종류')) == action_log.KIND_TALK:
                logged_pairs.add((_s(row.get('행위자')), _s(row.get('대상'))))

    out: List[Event] = []
    for uid, entry in talks.items():
        actor = name_by_id.get(uid, uid)
        for partner in entry.get('상대') or []:
            partner = _s(partner)
            if not partner or partner == actor:
                continue
            if (actor, partner) in logged_pairs:
                continue
            logged_pairs.add((actor, partner))   # 슬롯 내부 중복도 막는다
            out.append(Event(
                id=ids.next(T_TALK), day=_int(facts.get('day'), 0), type=T_TALK,
                actor=actor, target=partner, detail='2차 대화 상대(시트엔 없음)',
                secret=True, source='슬롯:story',
            ))
    return out


def _directive_events(facts: Dict[str, Any], ids: _IdGen) -> List[Event]:
    """부탁·지령 수행. '대상'은 지령을 받은 사람(=수행자)이다.

    은밀 여부는 구조 플래그가 없다 — 본문(내용)에서 SECRET_KEYWORDS 로만 감지한다
    (digest_seeds 가 판정). 여기선 본문과 보고 내용을 detail 에 합쳐 두고,
    보고가 비었는지(report_empty)를 표시해 E4(제외)가 판단하게 한다.
    """
    out: List[Event] = []
    for d in facts.get('directives') or []:
        actor = _s(d.get('대상'))
        if not actor:
            continue
        body = _s(d.get('내용'))
        report = _s(d.get('완료 내용'))
        # 보고가 '@STORY' 류거나 비면 실질 내용이 없다(E4). '@'는 이미 전각 처리됨(_text).
        report_empty = (not report) or _strip_tags(report) == ''
        detail = body
        if report and not report_empty:
            detail = f"{body}\n보고: {report}" if body else report
        out.append(Event(
            id=ids.next(T_DIRECTIVE), day=_int(facts.get('day'), 0),
            type=T_DIRECTIVE, actor=actor, target=None, detail=detail,
            result='보고없음' if report_empty else '보고있음',
            source='부탁지령',
        ))
    return out


def _infirmary_events(facts: Dict[str, Any], ids: _IdGen) -> List[Event]:
    """의무실. 행동로그엔 '방문' 한 줄뿐 — 소견·처치는 @DOCTOR 슬롯에 있다.

    소견 텍스트가 detail 로 들어가고, 여기서 I3(진술 모순) 키워드가 검출된다.
    처치 종류(과잉심문 등)는 result 에 남긴다 — 과잉심문은 그 자체로 이야깃거리(§11).
    행동로그에 방문 기록이 있는 사람은 그걸로, 슬롯에만 있는 사람도 빠뜨리지 않는다.
    """
    doctor = (facts.get('slots') or {}).get('doctor') or {}
    charts = doctor.get('소견') or {}
    visits = doctor.get('진료') or {}
    name_by_id = facts.get('name_by_id') or {}

    # 방문한 사람 = 소견 있는 사람 ∪ 진료 세션 있는 사람 ∪ 행동로그 의무실 방문자
    visitor_ids = set(charts) | set(visits)
    logged_names = set()
    if action_log is not None:
        logged_names = {_s(r.get('행위자')) for r in facts.get('actions') or []
                        if _s(r.get('종류')) == action_log.KIND_INFIRMARY}

    out: List[Event] = []
    handled = set()
    for uid in visitor_ids:
        name = name_by_id.get(uid, uid)
        handled.add(name)
        note_texts, treatment = [], ''
        for n in charts.get(uid) or []:
            txt = _s(n.get('소견')) or _s(n.get('내용'))
            if txt:
                note_texts.append(txt)
            treatment = _s(n.get('처치')) or treatment
        detail = '\n'.join(note_texts)
        out.append(Event(
            id=ids.next(T_INFIRMARY), day=_int(facts.get('day'), 0),
            type=T_INFIRMARY, actor=name, target=None,
            detail=detail or None, result=treatment or None,
            source='슬롯:doctor',
        ))
    # 슬롯엔 안 잡혔지만 행동로그엔 방문이 찍힌 사람(소견 미작성) — I3는 건너뛰지만 흔적은 남긴다.
    for name in logged_names - handled:
        out.append(Event(
            id=ids.next(T_INFIRMARY), day=_int(facts.get('day'), 0),
            type=T_INFIRMARY, actor=name, target=None, detail=None,
            result='방문', source='행동로그',
        ))
    return out


def _gamble_events(facts: Dict[str, Any], ids: _IdGen,
                   name_by_id: Dict[str, str]) -> List[Event]:
    """도박. 행동로그엔 금액만 — 게임별 횟수는 @BAR 슬롯에 있다.

    count 에 총 플레이 횟수를 담는다. E5(임계값 미만 제외)·I5(수치 이상)가 이 수를 본다.
    """
    plays = ((facts.get('slots') or {}).get('bar') or {}).get('플레이') or {}
    out: List[Event] = []
    for uid, games in plays.items():
        if not isinstance(games, dict):
            continue
        total = 0
        parts = []
        for label, n in games.items():
            try:
                n = int(n)
            except (ValueError, TypeError):
                continue
            if n:
                total += n
                parts.append(f"{label} {n}회")
        if total <= 0:
            continue
        out.append(Event(
            id=ids.next(T_GAMBLE), day=_int(facts.get('day'), 0), type=T_GAMBLE,
            actor=name_by_id.get(uid, uid), target=None,
            detail=', '.join(parts), count=total, source='슬롯:bar',
        ))
    return out


def _incomplete_events(facts: Dict[str, Any], ids: _IdGen) -> List[Event]:
    """하려다 만 것 — 하루치를 썼는데 로그엔 없는 시도(대화·고발 소진)."""
    if digest_facts is None:
        return []
    out: List[Event] = []
    for a in digest_facts.unlogged_attempts(facts):
        who = _s(a.get('이름'))
        kind = _s(a.get('종류'))
        partner = _s(a.get('상대'))
        detail = f"{kind} 시작 후 중단" + (f" (상대: {partner})" if partner else "")
        out.append(Event(
            id=ids.next(T_INCOMPLETE), day=_int(facts.get('day'), 0),
            type=T_INCOMPLETE, actor=who, target=partner or None,
            detail=detail, source='슬롯:story',
        ))
    return out


def _silence_events(facts: Dict[str, Any], ids: _IdGen) -> List[Event]:
    """오늘 조용했던 사람 — 전 항목 무기록. 왜 조용했느냐는 것도 이야깃거리다."""
    if digest_facts is None:
        return []
    out: List[Event] = []
    for name in digest_facts.quiet_runners(facts):
        out.append(Event(
            id=ids.next(T_SILENCE), day=_int(facts.get('day'), 0),
            type=T_SILENCE, actor=name, detail='전 항목 무기록', source='집계',
        ))
    return out


# --------------------------------------------------------------------------- #
# 소도구
# --------------------------------------------------------------------------- #
def _int(value: Any, default: int) -> int:
    try:
        return int(float(_s(value)))
    except (ValueError, TypeError):
        return default


def _strip_tags(text: str) -> str:
    """전각·반각 계정 태그(＠STORY/@STORY)와 공백을 걷어낸 알맹이.

    보고 내용이 '＠STORY' 한 조각뿐이면 실질 내용이 없다(E4). mention_guard 가
    이미 '@'를 '＠'로 바꿔 두므로 둘 다 지운다.
    """
    t = re.sub(r'[＠@]\S+', '', text or '')
    return t.strip()

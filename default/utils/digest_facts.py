"""
utils/digest_facts.py — 일일보고의 '팩트 시트' 수집 (시너몬트)

하루치 사건을 **시트에서 결정론적으로** 모은다. AI를 쓰지 않는다.
소문의 원재료가 환각이면 GM 정본이 오염된다 — 사실은 코드가, 각색은 AI가 한다
(각색은 utils/digest_seeds.py).

왜 행동로그만으로는 부족한가
---------------------------
`행동로그.요약`은 일부러 빈약하다. 그 문장이 소문 문구로 그대로 나가기 때문에
"수치를 섞지 말 것"이 규칙이다(utils/action_log.py). 그래서 소문 재료로는 좋지만
GM 보고서로는 앙상하다.

해결은 로그를 살찌우는 게 아니라 **원본 시트를 되읽어 join** 하는 것이다.
상세는 이미 어딘가에 다 있고, 행동로그에만 없다:

    고발.사유          ← 고발 이유 전문 (제일 좋은 소문 재료)
    조사 로그.결과      ← 포인트별 획득물·수치 변동
    부탁지령.경중/보상/완료 내용
    관리.소지금/건강/이성  ← 현재 스냅샷
    투표.표

시트 컬럼을 늘리지 않아도 되고, 이미 쌓인 과거 데이터에도 소급 적용된다.

읽기 전용이다. 이 모듈은 어떤 시트에도 쓰지 않는다.
"""

import os
import sys
from typing import Any, Dict, List, Optional

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from utils.logging_config import logger

try:
    from utils import action_log
except ImportError:  # 부분 환경/테스트
    action_log = None  # type: ignore


# 시트 이름
ROSTER_SHEET = '명단'
MANAGEMENT_SHEET = '관리'
ACCUSE_SHEET = '고발'
DIRECTIVE_SHEET = '부탁지령'
VOTE_SHEET = '투표'
INVESTIGATION_LOG_SHEET = '로그'


def _s(value: Any) -> str:
    """셀 값을 안전한 문자열로."""
    if value is None:
        return ''
    return str(value).strip()


def _same_day(a: Any, b: Any) -> bool:
    """일차 비교 ('3', '3.0', 3 관용 처리)."""
    sa, sb = _s(a), _s(b)
    if sa == sb:
        return True
    if not sa or not sb:
        return False
    try:
        return int(float(sa)) == int(float(sb))
    except (ValueError, TypeError):
        return False


def _read(manager, sheet: str, use_cache: bool = False) -> List[Dict[str, Any]]:
    """시트 한 장 읽기. 실패는 빈 목록으로 흡수한다.

    한 장을 못 읽었다고 보고 전체를 포기하면, 정작 사고가 난 날 보고가 안 온다.
    무엇을 못 읽었는지는 fetch_facts 가 'errors'에 담아 보고서에 싣는다.
    """
    if manager is None:
        return []
    try:
        return manager.get_worksheet_data(sheet, use_cache=use_cache) or []
    except Exception as e:
        logger.warning(f"[일일보고] '{sheet}' 조회 실패: {e}")
        raise


def _read_soft(manager, sheet: str, errors: List[str],
               use_cache: bool = False) -> List[Dict[str, Any]]:
    try:
        return _read(manager, sheet, use_cache=use_cache)
    except Exception as e:
        errors.append(f"'{sheet}' 시트를 읽지 못했습니다({type(e).__name__}) — 이 절은 비어 있습니다")
        return []


def fetch_facts(day: int,
                sheets_manager=None,
                system_sheets_manager=None,
                investigation_sheets_manager=None) -> Dict[str, Any]:
    """
    해당 일차의 사건을 모아 구조화한다.

    Returns:
        dict:
          'day'          : 일차
          'actions'      : 행동로그 행(그날치)
          'by_actor'     : {이름: {'kinds': {종류: [행]}, 'stats': {...}}}
          'accusations'  : [{'고발자','대상','사유','처리'}]
          'directives'   : [{'대상','내용','경중','보상','상태','완료 내용'}]
          'votes'        : [{'대상','투표자','표'}]
          'investigations': [{'캐릭터명','장소명','포인트명','결과'}]
          'stats'        : {이름: {'소지금','건강','이성'}}
          'roster'       : [이름...]
          'name_by_id'   : {아이디: 이름}  — 슬롯 JSON 은 아이디로 키를 잡는다
          'slots'        : {'story','doctor','bar'} — 슬롯 로컬 JSON (시트에 없는 것)
          'errors'       : [읽기 실패 메시지...]
    """
    errors: List[str] = []
    facts: Dict[str, Any] = {
        'day': day,
        'actions': [],
        'by_actor': {},
        'accusations': [],
        'directives': [],
        'votes': [],
        'investigations': [],
        'stats': {},
        'roster': [],
        'name_by_id': {},
        'slots': {},
        'errors': errors,
    }

    # ── 행동로그 (그날치) ──
    if action_log is not None and system_sheets_manager is not None:
        try:
            rows = _read(system_sheets_manager, action_log.SHEET_NAME)
            facts['actions'] = [r for r in rows if _same_day(r.get('일차'), day)]
        except Exception:
            errors.append("'행동로그'를 읽지 못했습니다 — 보고서의 뼈대가 비었습니다")

    # ── 고발 (사유 전문) ──
    for row in _read_soft(system_sheets_manager, ACCUSE_SHEET, errors):
        if _same_day(row.get('일차'), day):
            facts['accusations'].append({
                '고발자': _s(row.get('고발자')),
                '대상': _s(row.get('대상')),
                '사유': _s(row.get('사유')),
                '처리': _s(row.get('처리')),
            })

    # ── 부탁지령 (그날 완료된 것) ──
    for row in _read_soft(system_sheets_manager, DIRECTIVE_SHEET, errors):
        done_day = row.get('완료 일차')
        if _same_day(done_day, day) or (not _s(done_day) and _same_day(row.get('일차'), day)):
            facts['directives'].append({
                '일차': _s(row.get('일차')),
                '대상': _s(row.get('대상')),
                '내용': _s(row.get('내용')),
                '경중': _s(row.get('경중')),
                '보상': _s(row.get('보상')),
                '상태': _s(row.get('상태')),
                '완료 내용': _s(row.get('완료 내용')),
                '완료 일차': _s(done_day),
            })

    # ── 투표 ──
    for row in _read_soft(system_sheets_manager, VOTE_SHEET, errors):
        if _same_day(row.get('일차'), day):
            facts['votes'].append({
                '대상': _s(row.get('대상')),
                '투표자': _s(row.get('투표자')),
                '표': _s(row.get('표')),
            })

    # ── 조사 로그 (포인트·결과) ──
    #    조사 시트는 '일차'가 아니라 '일시'로 남는다. 행동로그의 그날 조사 기록과
    #    (캐릭터, 장소) 로 맞춰 붙인다 — 조사 로그에만 '포인트명·결과'가 있다.
    inv_rows = _read_soft(investigation_sheets_manager, INVESTIGATION_LOG_SHEET, errors)
    if inv_rows:
        today_pairs = {
            (_s(a.get('행위자')), _s(a.get('대상')))
            for a in facts['actions']
            if action_log is not None and _s(a.get('종류')) == action_log.KIND_INVESTIGATE
        }
        for row in inv_rows:
            pair = (_s(row.get('캐릭터명')), _s(row.get('장소명')))
            if pair in today_pairs:
                facts['investigations'].append({
                    '캐릭터명': pair[0],
                    '장소명': pair[1],
                    '포인트명': _s(row.get('포인트명')),
                    '결과': _s(row.get('결과')),
                })

    # ── 관리 스냅샷 ──
    for row in _read_soft(sheets_manager, MANAGEMENT_SHEET, errors):
        name = _s(row.get('이름'))
        if not name:
            continue
        facts['stats'][name] = {
            '소지금': _s(row.get('소지금')),
            '건강': _s(row.get('건강')),
            '이성': _s(row.get('이성')),
        }

    # ── 명단 ──
    try:
        roster = _read(sheets_manager, ROSTER_SHEET, use_cache=True)
        facts['roster'] = [_s(r.get('이름')) for r in roster if _s(r.get('이름'))]
        # 슬롯 JSON 은 아이디로 키를 잡는다. 보고서는 이름으로 읽혀야 한다.
        facts['name_by_id'] = {
            _s(r.get('아이디')).lstrip('@').lower(): _s(r.get('이름'))
            for r in roster if _s(r.get('아이디')) and _s(r.get('이름'))
        }
    except Exception:
        errors.append("'명단'을 읽지 못했습니다 — '오늘 조용했던 사람' 절이 빠집니다")

    # ── 슬롯 로컬 JSON (시트에 없는 것들) ──
    #    @STORY 2차 대화 상대, @DOCTOR 소견·대화록, @BAR 게임별 횟수.
    #    시트만 읽으면 통째로 빠지는 정보다.
    try:
        from utils import slot_reader
        facts['slots'] = slot_reader.fetch_slot_facts(day, errors)
    except Exception as e:
        logger.warning(f"[일일보고] 슬롯 JSON 수집 실패: {e}", exc_info=True)
        errors.append(f"슬롯 로컬 기록을 읽지 못했습니다({type(e).__name__}) — "
                      f"의사 소견·2차 대화 상대·도박 횟수가 빠집니다")

    facts['by_actor'] = _group_by_actor(facts)
    return facts


def _group_by_actor(facts: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """행위자별로 그날 행동을 종류별로 묶는다."""
    grouped: Dict[str, Dict[str, Any]] = {}
    for row in facts['actions']:
        actor = _s(row.get('행위자'))
        if not actor:
            continue
        entry = grouped.setdefault(actor, {'kinds': {}})
        entry['kinds'].setdefault(_s(row.get('종류')), []).append(row)
    for name, entry in grouped.items():
        entry['stats'] = facts['stats'].get(name, {})
    return grouped


def build_contact_graph(facts: Dict[str, Any]) -> Dict[str, Any]:
    """
    누가 누구를 건드렸는지 (소문의 뼈대).

    Returns:
        dict:
          'edges'  : [{'from','to','kind'}]
          'mutual' : [(A, B, [kinds])]  — 서로 접촉한 쌍
          'targets': {이름: 자기를 향한 접촉 수}  — 오늘 표적이 된 사람
    """
    # 관계망은 **사람 사이**의 것이다. 그런데 '대상' 열은 종류마다 뜻이 다르다:
    #   조사 → 장소명('보안 경계')  ·  의무실 방문 → 리터럴 '의사'
    #   부탁지령수행 → 자기 자신
    # 걸러내지 않으면 "데보라 →조사→ 보안 경계"처럼 장소가 인물 행세를 하고,
    # 표적 집계에도 장소가 올라온다(2026-07-16 실제로 그랬다).
    # 명단에 있는 이름만 남긴다 — 명단이 사람의 정의다.
    people = {n for n in facts.get('roster') or [] if n}

    edges: List[Dict[str, str]] = []
    for row in facts['actions']:
        actor = _s(row.get('행위자'))
        target = _s(row.get('대상'))
        kind = _s(row.get('종류'))
        if not actor or not target or actor == target:
            continue
        if people and target not in people:
            continue
        # 명단을 못 읽었을 때의 폴백. 최소한 '의사'와 장소 같은 비인물은 거른다.
        if not people and (target == '의사' or kind == '조사'):
            continue
        edges.append({'from': actor, 'to': target, 'kind': kind})

    pair_kinds: Dict[Any, List[str]] = {}
    directed = set()
    for e in edges:
        directed.add((e['from'], e['to']))
        key = tuple(sorted((e['from'], e['to'])))
        pair_kinds.setdefault(key, []).append(e['kind'])

    mutual = []
    for (a, b), kinds in pair_kinds.items():
        if (a, b) in directed and (b, a) in directed:
            mutual.append((a, b, sorted(set(kinds))))

    targets: Dict[str, int] = {}
    for e in edges:
        targets[e['to']] = targets.get(e['to'], 0) + 1

    return {'edges': edges, 'mutual': sorted(mutual), 'targets': targets}


def quiet_runners(facts: Dict[str, Any]) -> List[str]:
    """오늘 정말로 아무것도 안 한 사람.

    '아무것도 안 한 사람'도 소문 재료다 — 왜 조용했느냐는 이야기가 붙는다.
    다만 **행동로그만 보면 틀린다.** 도박은 행동로그에 안 남고, 대화는 끝맺어야만
    남는다. 슬롯 17판을 돌린 사람을 '조용했다'고 적으면 GM이 헛다리를 짚는다
    (2026-07-16 실제로 그랬다).
    """
    active = {_s(a.get('행위자')) for a in facts['actions'] if _s(a.get('행위자'))}
    active |= slot_active_names(facts)
    return sorted(n for n in facts['roster'] if n and n not in active)


def slot_active_names(facts: Dict[str, Any]) -> set:
    """슬롯 로컬 JSON 에만 흔적이 남은 사람들의 이름.

    도박(횟수), 대화 소진, 진료 — 전부 행동로그에 안 남거나 조건부로만 남는다.
    """
    slots = facts.get('slots') or {}
    name_by_id = facts.get('name_by_id') or {}
    ids = set()

    story = slots.get('story') or {}
    ids |= set(story.get('대화') or {})
    ids |= set(story.get('진행중') or {})

    doctor = slots.get('doctor') or {}
    ids |= set(doctor.get('소견') or {})
    ids |= set(doctor.get('진료') or {})

    ids |= set((slots.get('bar') or {}).get('플레이') or {})

    return {name_by_id[i] for i in ids if i in name_by_id}


def unlogged_attempts(facts: Dict[str, Any]) -> List[Dict[str, str]]:
    """소진했는데 행동로그엔 없는 시도.

    대화를 걸어놓고 끝맺지 않으면 하루치를 쓰고도 시트엔 아무것도 안 남는다.
    '뭔가 하려다 만 사람'은 그 자체로 이야깃거리다.
    """
    story = (facts.get('slots') or {}).get('story') or {}
    name_by_id = facts.get('name_by_id') or {}
    logged = {(_s(a.get('행위자')), _s(a.get('종류'))) for a in facts['actions']}

    out = []
    for uid, entry in (story.get('대화') or {}).items():
        name = name_by_id.get(uid, uid)
        if entry.get('대화소진') and (name, '대화') not in logged:
            out.append({'이름': name, '종류': '대화',
                        '상대': ', '.join(entry.get('상대') or []) or '(기록 없음)'})
        if entry.get('고발소진') and (name, '고발') not in logged:
            out.append({'이름': name, '종류': '고발', '상대': ''})
    return sorted(out, key=lambda x: (x['이름'], x['종류']))

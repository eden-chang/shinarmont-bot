"""
utils/digest_report.py — 일일보고 본문 조립 + 타래 분할 (시너몬트)

digest_facts 가 모은 사실을 GM이 읽을 문서로 만든다.
그리고 마스토돈 한 통 한도(공백 미포함 5천자)에 맞춰 **4800자씩 잘라** 타래로 보낸다.

분할 규칙
---------
- 자를 때 **줄 경계를 지킨다.** 문장 한복판에서 끊기면 읽다가 맥이 끊긴다.
- 한 줄이 통째로 한도를 넘으면(GM이 쓴 긴 고발 사유 등) 그 줄만 강제로 쪼갠다.
  자르지 않으면 그 한 통이 422 로 실패하고 **타래 전체가 거기서 멈춘다**.
- 각 통에 `(n/N)` 을 붙인다. 타래가 어디서 끊겼는지 GM이 알아야 한다.
- 수신자 멘션과 머리표는 **본문 예산에서 미리 뺀다**. 이걸 빼먹으면 딱 한도에
  맞춘 통이 멘션 길이만큼 초과해서 실패한다.
"""

import os
import sys
from typing import Any, Dict, List, Optional

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from utils.logging_config import logger
from utils import digest_facts

try:
    from utils import action_log
except ImportError:
    action_log = None  # type: ignore


# 마스토돈 한 통 한도(shinarmont.site: 공백 미포함 5천자) 대비 여유분
DEFAULT_CHUNK_LIMIT = 4800

_KIND_ORDER = ('조사', '추적', '대화', '교류', '의무실 방문', '고발', '부탁지령수행')


def _s(v: Any) -> str:
    return digest_facts._s(v)


def _cfg_int(key: str, default: int) -> int:
    try:
        from config.settings import config
        return int(getattr(config, key, default))
    except Exception:
        return default


def _cfg_bool(key: str, default: bool) -> bool:
    try:
        from config.settings import config
        return bool(getattr(config, key, default))
    except Exception:
        return default


def _number_sections(text: str) -> str:
    """'#N.' 자리표시자에 순번을 매긴다.

    절은 내용이 없으면 통째로 빠진다(고발이 없는 날엔 고발 절이 없다).
    그래서 번호를 소스에 박아 두면 실제 출력에서 건너뛰거나 겹친다 —
    실제로 5·6이 각각 두 번 쓰였다(2026-07-16).
    """
    out = []
    n = 0
    for line in text.split("\n"):
        if line.startswith("#N."):
            n += 1
            line = f"{n}." + line[3:]
        out.append(line)
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# 본문 조립
# --------------------------------------------------------------------------- #
def build_report(facts: Dict[str, Any], seeds: Optional[Dict[str, Any]] = None) -> str:
    """팩트 시트 + 관계망 + (선택)소문 씨앗을 하나의 문서로."""
    day = facts.get('day')
    lines: List[str] = [f"[일일보고] {day}일차", ""]

    if facts.get('errors'):
        lines.append("⚠️ 이 보고서는 일부 시트를 읽지 못한 채 작성됐습니다:")
        lines.extend(f"  · {e}" for e in facts['errors'])
        lines.append("")

    total = len(facts.get('actions') or [])
    if not total:
        lines.append("오늘 행동로그에 기록된 사건이 없습니다.")
        lines.append("")
        lines.append(_quiet_section(facts))
        return _number_sections("\n".join(lines).strip())

    lines.append(f"사건 {total}건 · 활동 인원 {len(facts.get('by_actor') or {})}명")
    lines.append("")
    lines.append(_actor_section(facts))
    lines.append(_graph_section(facts))
    lines.append(_accusation_section(facts))
    lines.append(_directive_section(facts))
    lines.append(_doctor_section(facts))
    lines.append(_gambling_section(facts))
    lines.append(_vote_section(facts))
    lines.append(_attempt_section(facts))
    lines.append(_quiet_section(facts))
    if seeds:
        lines.append(_seed_section(seeds))

    return _number_sections("\n".join(l for l in lines if l is not None).strip())


def _actor_section(facts: Dict[str, Any]) -> str:
    """1. 인물별 행적 — 보고서의 몸통."""
    out = ["━━━━━━━━━━━━━━━━━━━━━", "#N. 인물별 행적", ""]

    inv_by_actor: Dict[str, List[Dict[str, Any]]] = {}
    for r in facts.get('investigations') or []:
        inv_by_actor.setdefault(r['캐릭터명'], []).append(r)

    for name in sorted(facts.get('by_actor') or {}):
        entry = facts['by_actor'][name]
        stats = entry.get('stats') or {}
        head = f"◆ {name}"
        bits = [f"{k} {stats[k]}" for k in ('소지금', '건강', '이성') if stats.get(k)]
        if bits:
            head += f"  ({' · '.join(bits)})"
        out.append(head)

        for kind in _KIND_ORDER:
            rows = entry['kinds'].get(kind)
            if not rows:
                continue
            out.append(f"  [{kind}] {len(rows)}회")
            for row in rows:
                out.extend(_detail_lines(kind, row, name, inv_by_actor, facts))

        # 순서 상수에 없는 종류도 흘리지 않는다(새 종류가 조용히 사라지면 안 된다)
        for kind, rows in entry['kinds'].items():
            if kind in _KIND_ORDER or not kind:
                continue
            out.append(f"  [{kind}] {len(rows)}회")
            for row in rows:
                out.append(f"    · {_s(row.get('요약'))}")
        out.append("")

    return "\n".join(out)


def _detail_lines(kind: str, row: Dict[str, Any], actor: str,
                  inv_by_actor: Dict[str, List[Dict[str, Any]]],
                  facts: Dict[str, Any]) -> List[str]:
    """종류별로 원본 시트의 상세를 붙인다. 여기가 소문 재료의 밀도를 결정한다."""
    when = _s(row.get('일시'))
    target = _s(row.get('대상'))
    summary = _s(row.get('요약'))
    lines = [f"    · {summary}" + (f"  ({when})" if when else "")]

    if kind == '조사':
        # 행동로그의 조사 '대상'은 **장소**다. 포인트명은 요약 문장 안에만 있다
        # ("데보라가 보안 경계의 초소 앞을 살폈다"). (행위자, 장소)로만 맞추면
        # 같은 장소를 두 번 조사했을 때 두 포인트가 양쪽에 다 붙는다.
        # 포인트명이 요약에 들어 있는지로 한 건씩 짝지어야 한다.
        for inv in inv_by_actor.get(actor, []):
            if inv['장소명'] != target:
                continue
            point = inv['포인트명']
            if point and point not in summary:
                continue
            detail = f"      → {inv['장소명']} / {point}"
            if inv['결과']:
                detail += f" : {inv['결과']}"
            lines.append(detail)

    elif kind == '고발':
        for acc in facts.get('accusations') or []:
            if acc['고발자'] == actor and acc['대상'] == target:
                if acc['사유']:
                    lines.append(f"      → 사유: {acc['사유']}")
                if acc['처리']:
                    lines.append(f"      → GM 처리: {acc['처리']}")

    elif kind == '대화':
        # 행동로그는 '최초 상대' 하나만 남긴다(talk_command). A가 B·C·D와 얘기해도
        # 시트엔 "A가 B와 대화"뿐이다. 2차 상대는 @STORY 의 JSON 이 유일한 기록이다.
        partners = _talk_partners(facts, actor)
        others = [p for p in partners if p != target]
        if others:
            lines.append(f"      → 다른 상대: {', '.join(others)}  (시트엔 없음)")
        live = _live_talk(facts, actor)
        if live and live.get('상대별횟수'):
            counts = ', '.join(f"{n} {c}회" for n, c in live['상대별횟수'].items())
            lines.append(f"      → 멘션: {counts} (총 {live.get('총횟수', 0)}회)")

    elif kind == '부탁지령수행':
        for d in facts.get('directives') or []:
            if d['대상'] != actor:
                continue
            if d['내용']:
                lines.append(f"      → 지령: {d['내용']}")
            meta = [x for x in (f"경중 {d['경중']}" if d['경중'] else '',
                                f"보상 {d['보상']}" if d['보상'] else '',
                                d['상태']) if x]
            if meta:
                lines.append(f"      → {' · '.join(meta)}")
            if d['완료 내용']:
                lines.append(f"      → 보고 내용: {d['완료 내용']}")

    return lines


def _ids_for(facts: Dict[str, Any], name: str) -> List[str]:
    """이름 → 아이디들. 슬롯 JSON 이 아이디로 키를 잡아서 되짚어야 한다."""
    return [uid for uid, n in (facts.get('name_by_id') or {}).items() if n == name]


def _name_of(facts: Dict[str, Any], uid: str) -> str:
    """아이디 → 이름. 명단에 없으면 아이디를 그대로 보여준다(사라지는 것보단 낫다)."""
    return (facts.get('name_by_id') or {}).get(str(uid).lstrip('@').lower(), str(uid))


def _talk_partners(facts: Dict[str, Any], actor: str) -> List[str]:
    talks = ((facts.get('slots') or {}).get('story') or {}).get('대화') or {}
    for uid in _ids_for(facts, actor):
        entry = talks.get(uid)
        if entry and entry.get('상대'):
            return list(entry['상대'])
    return []


def _live_talk(facts: Dict[str, Any], actor: str) -> Optional[Dict[str, Any]]:
    live = ((facts.get('slots') or {}).get('story') or {}).get('진행중') or {}
    for uid in _ids_for(facts, actor):
        if uid in live:
            return live[uid]
    return None


def _doctor_section(facts: Dict[str, Any]) -> str:
    """의무실 — 러셀의 소견과 진료 대화록. 어느 시트에도 없다.

    행동로그엔 '누가 무슨 처치를 받았다' 한 줄뿐이라, 소문 재료로서는 여기가 훨씬 진하다.
    """
    doctor = (facts.get('slots') or {}).get('doctor') or {}
    charts = doctor.get('소견') or {}
    visits = doctor.get('진료') or {}
    if not charts and not visits:
        return ""

    out = ["━━━━━━━━━━━━━━━━━━━━━",
           "#N. 의무실 — 러셀의 소견 · 진료 기록  ※ 시트에 없는 정보", ""]

    uids = sorted(set(charts) | set(visits), key=lambda u: _name_of(facts, u))
    for uid in uids:
        name = _name_of(facts, uid)
        visit = visits.get(uid) or {}
        head = f"  ◆ {name}"
        if visit.get('턴수'):
            head += f"  (대화 {visit['턴수']}턴{' · 진행 중' if visit.get('진행중') else ''})"
        out.append(head)

        for note in charts.get(uid, []):
            out.append(f"    소견: {_s(note.get('소견'))}")

        hist = visit.get('대화록') or []
        if hist and _cfg_bool('DIGEST_INCLUDE_TRANSCRIPT', True):
            out.append("    ── 대화록")
            for line in _format_history(hist):
                out.append(f"      {line}")

        last = ((doctor.get('이력') or {}).get(uid) or {}).get('지난방문')
        if isinstance(last, dict) and last.get('일차') is not None:
            bits = [f"{k} {last[k]}" for k in ('건강', '이성') if last.get(k) is not None]
            if bits:
                out.append(f"    지난 방문({last['일차']}일차) 당시: {' · '.join(bits)}")
        out.append("")

    return "\n".join(out)


def _format_history(history: List[Dict[str, Any]]) -> List[str]:
    """진료 대화록을 '환자/의사' 줄로. 한 발화가 너무 길면 자른다.

    러너가 실제로 친 말이다. 통째로 실으면 보고서가 대화록에 잡아먹히고,
    타래가 수십 통이 된다.
    """
    cap = _cfg_int('DIGEST_TRANSCRIPT_CHARS', 300)
    lines = []
    for msg in history:
        if not isinstance(msg, dict):
            continue
        content = msg.get('content')
        if not isinstance(content, str) or not content.strip():
            continue
        text = content.strip()
        if len(text) > cap:
            text = text[:cap].rstrip() + '···'
        who = '환자' if msg.get('role') == 'user' else '러셀'
        lines.append(f"{who}: {text}")
    return lines


def _gambling_section(facts: Dict[str, Any]) -> str:
    """도박 — 게임별 플레이 횟수. 시트엔 금액만 남아서 '얼마나 매달렸나'는 여기뿐이다."""
    bar = (facts.get('slots') or {}).get('bar') or {}
    plays = bar.get('플레이') or {}
    orphans = bar.get('지급중단') or []
    if not plays and not orphans:
        return ""

    out = ["━━━━━━━━━━━━━━━━━━━━━", "#N. 도박  ※ 시트엔 금액만 남는다", ""]
    rows = sorted(((_name_of(facts, uid), counts) for uid, counts in plays.items()),
                  key=lambda x: (-sum(x[1].values()), x[0]))
    for name, counts in rows:
        detail = ', '.join(f"{g} {n}회" for g, n in counts.items())
        out.append(f"  {name} — {detail}  (총 {sum(counts.values())}회)")

    if orphans:
        out.append("")
        out.append("  🔴 배당 지급 중 중단된 판 — 소지금 확인 필요")
        for o in orphans:
            out.append(f"    {_name_of(facts, o['아이디'])}: 베팅 {o['베팅']} / "
                       f"지급시도 {o['지급시도']}")
    out.append("")
    return "\n".join(out)


def _graph_section(facts: Dict[str, Any]) -> str:
    """2. 관계망 — 소문은 사람 사이에서 난다."""
    graph = digest_facts.build_contact_graph(facts)
    out = ["━━━━━━━━━━━━━━━━━━━━━", "#N. 관계망", ""]

    if not graph['edges']:
        out.append("오늘 인물 간 접촉이 없습니다.")
        out.append("")
        return "\n".join(out)

    for e in graph['edges']:
        out.append(f"  {e['from']} →{e['kind']}→ {e['to']}")

    if graph['mutual']:
        out.append("")
        out.append("  ⚠️ 서로 접촉한 쌍 (소문 각도가 제일 잘 붙는 자리)")
        for a, b, kinds in graph['mutual']:
            out.append(f"    {a} ↔ {b}  ({', '.join(kinds)})")

    hot = sorted(((n, c) for n, c in graph['targets'].items() if c >= 2),
                 key=lambda x: (-x[1], x[0]))
    if hot:
        out.append("")
        out.append("  🎯 오늘 여러 번 표적이 된 사람")
        for name, count in hot:
            out.append(f"    {name} — {count}회")

    out.append("")
    return "\n".join(out)


def _accusation_section(facts: Dict[str, Any]) -> str:
    rows = facts.get('accusations') or []
    if not rows:
        return ""
    out = ["━━━━━━━━━━━━━━━━━━━━━", "#N. 고발 (사유 전문)", ""]
    for a in rows:
        out.append(f"  ◆ {a['고발자']} → {a['대상']}")
        out.append(f"    사유: {a['사유'] or '(비어 있음)'}")
        if a['처리']:
            out.append(f"    GM 처리: {a['처리']}")
        out.append("")
    return "\n".join(out)


def _directive_section(facts: Dict[str, Any]) -> str:
    rows = [d for d in (facts.get('directives') or []) if d.get('완료 일차')]
    if not rows:
        return ""
    out = ["━━━━━━━━━━━━━━━━━━━━━", "#N. 부탁·지령 수행", ""]
    for d in rows:
        out.append(f"  ◆ {d['대상']}  ({d['일차']}일차 지령 · {d['상태']})")
        if d['내용']:
            out.append(f"    지령: {d['내용']}")
        if d['완료 내용']:
            out.append(f"    보고: {d['완료 내용']}")
        meta = [x for x in (f"경중 {d['경중']}" if d['경중'] else '',
                            f"보상 {d['보상']}" if d['보상'] else '') if x]
        if meta:
            out.append(f"    {' · '.join(meta)}")
        out.append("")
    return "\n".join(out)


def _vote_section(facts: Dict[str, Any]) -> str:
    rows = facts.get('votes') or []
    if not rows:
        return ""
    out = ["━━━━━━━━━━━━━━━━━━━━━", "#N. 투표", ""]
    by_target: Dict[str, List[Dict[str, str]]] = {}
    for v in rows:
        by_target.setdefault(v['대상'], []).append(v)
    for target in sorted(by_target):
        votes = by_target[target]
        guilty = [v['투표자'] for v in votes if v['표'] == '유죄']
        innocent = [v['투표자'] for v in votes if v['표'] == '무죄']
        out.append(f"  ◆ {target} — 유죄 {len(guilty)} / 무죄 {len(innocent)}")
        if guilty:
            out.append(f"    유죄: {', '.join(guilty)}")
        if innocent:
            out.append(f"    무죄: {', '.join(innocent)}")
        out.append("")
    return "\n".join(out)


def _attempt_section(facts: Dict[str, Any]) -> str:
    """소진했는데 로그엔 없는 시도 — 뭔가 하려다 만 사람."""
    rows = digest_facts.unlogged_attempts(facts)
    if not rows:
        return ""
    out = ["━━━━━━━━━━━━━━━━━━━━━",
           "#N. 하려다 만 것  ※ 하루치를 썼는데 로그엔 없다", ""]
    for r in rows:
        line = f"  {r['이름']} — {r['종류']}를 시작했지만 끝맺지 않음"
        if r.get('상대'):
            line += f"  (상대: {r['상대']})"
        out.append(line)
    out.append("")
    return "\n".join(out)


def _quiet_section(facts: Dict[str, Any]) -> str:
    quiet = digest_facts.quiet_runners(facts)
    if not quiet:
        return ""
    return "\n".join([
        "━━━━━━━━━━━━━━━━━━━━━",
        "#N. 오늘 조용했던 사람",
        "",
        "  " + ", ".join(quiet),
        "  (행동로그·도박·진료·대화 어디에도 없는 사람. "
        "아무것도 안 한 것도 이야깃거리다)",
        "",
    ])


def _seed_section(seeds: Dict[str, Any]) -> str:
    """7. 소문 씨앗 — 유일하게 AI가 쓴 절. 사실이 아님을 못박는다."""
    items = seeds.get('seeds') or []
    if not items:
        return ""
    out = [
        "━━━━━━━━━━━━━━━━━━━━━",
        "#N. 소문 씨앗  ※ AI 제안 — 사실 아님",
        "",
        "  아래는 위 사실을 재료로 만든 각색안입니다.",
        "  그대로 쓰지 마시고 고르고 다듬어 '소문' 시트에 넣으세요.",
        "",
    ]
    for s in items:
        angle = _s(s.get('각도')) or '각도 미상'
        text = _s(s.get('문구'))
        basis = _s(s.get('근거'))
        out.append(f"  [{angle}] {text}")
        if basis:
            out.append(f"      ← 근거: {basis}")
    out.append("")
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# 타래 분할
# --------------------------------------------------------------------------- #
def chunk_report(text: str, limit: int = DEFAULT_CHUNK_LIMIT,
                 reserve: int = 0) -> List[str]:
    """
    본문을 타래 한 통 분량으로 자른다.

    Args:
        text: 보고서 전문.
        limit: 한 통 최대 글자수.
        reserve: 멘션·머리표 등 본문 밖에서 잡아먹을 글자수(예산에서 미리 뺀다).

    Returns:
        list[str]: 자른 조각들. 빈 입력이면 빈 목록.
    """
    budget = max(1, limit - max(0, reserve))
    if not text or not text.strip():
        return []

    chunks: List[str] = []
    current: List[str] = []
    size = 0

    def flush():
        nonlocal current, size
        if current:
            chunks.append("\n".join(current).strip("\n"))
            current, size = [], 0

    for line in text.split("\n"):
        # 한 줄이 통째로 예산을 넘으면(긴 고발 사유 등) 그 줄만 강제로 쪼갠다.
        # 안 쪼개면 그 통이 실패하고 타래가 거기서 멈춘다.
        if len(line) > budget:
            flush()
            for i in range(0, len(line), budget):
                chunks.append(line[i:i + budget])
            continue

        add = len(line) + (1 if current else 0)
        if size + add > budget:
            flush()
            add = len(line)
        current.append(line)
        size += add

    flush()
    return [c for c in chunks if c.strip()]


def paginate(chunks: List[str], header: str = "") -> List[str]:
    """각 조각에 `(n/N)` 머리표를 붙인다. 타래가 끊겨도 GM이 알아채도록."""
    total = len(chunks)
    if total <= 1:
        return list(chunks)
    out = []
    for i, c in enumerate(chunks, 1):
        tag = f"{header} ({i}/{total})".strip()
        out.append(f"{tag}\n{c}")
    return out

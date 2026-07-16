"""
utils/slot_reader.py — 다른 슬롯의 JSON 상태를 읽는다 (읽기 전용)

일일보고는 @SYSTEM(스케줄러 소유 슬롯)에서 돈다. 그런데 슬롯마다 자기 프로세스의
`state/{슬롯}/` 아래에만 쓰는 데이터가 있고, 그중엔 **어느 시트에도 없는 것**이 있다:

    @STORY  · 오늘대화상대 — 2차 대화 상대. 행동로그엔 '최초 상대'만 남는다.
                             A가 B·C·D와 얘기해도 시트엔 "A가 B와 대화"뿐이다.
            · 오늘대화여부/오늘고발 — 소진했지만 끝맺지 않은 시도
    @DOCTOR · doctor_charts   — 러셀이 쓴 소견. 시트에 없다.
            · doctor_sessions — 진료 대화록 전문. 행동로그엔 '의무실 방문' 한 줄뿐이다.
            · 의무실지난방문   — 그때의 건강/이성 스냅샷(관리 시트는 이미 덮어썼다)
    @BAR    · 오늘슬롯/오늘크랩스/오늘블랙잭 — 게임별 플레이 횟수(시트엔 금액만)
            · blackjack_sessions.payout_attempted — 지급 도중 죽은 판(재화 사고 경보)
    @TOWN   · 없다. 조사 데이터는 설계상 전부 시트에 있다.

왜 slot_path()를 안 쓰나
------------------------
`json_store.slot_path()`는 **현재 프로세스의** `config.BOT_NAME` 을 본다. @SYSTEM 에서
부르면 항상 `state/SYSTEM/` 이 나온다. 다른 슬롯을 보려면 경로를 직접 만들어야 한다.
5개 슬롯은 전부 같은 머신·같은 프로젝트 디렉터리에서 `subprocess.Popen` 으로 뜨므로
(bot_process_wrapper.py) 파일 접근 자체는 된다.

절대 쓰지 않는다
----------------
JsonStore 는 파일 전체를 메모리에 들고 통째로 다시 쓴다. @SYSTEM 이 남의 스토어에
`set()` 하면 그 슬롯이 그 사이에 쓴 걸 전부 날린다. 그래서 여기서는 **연 즉시 읽고 닫는**
맨 `json.load` 만 쓴다 — 윈도우에서는 남이 연 파일에 `os.replace` 가 실패하는데,
JsonStore 의 저장 실패는 로그만 남기고 조용히 지나가서(json_store.py) 그 슬롯의 쓰기가
소리 없이 사라진다. 핸들을 오래 쥐고 있으면 안 된다.
"""

import json
import os
import sys
from typing import Any, Dict, List, Optional

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from utils.logging_config import logger

try:
    from config.settings import config
except ImportError:
    config = None  # type: ignore


# 슬롯 디렉터리 이름 = BOT{n}_NAME (.env). 전부 ASCII 라 slot_id() 는 항등이다.
SLOT_SYSTEM = 'SYSTEM'
SLOT_TOWN = 'TOWN'
SLOT_STORY = 'STORY'
SLOT_DOCTOR = 'DOCTOR'
SLOT_BAR = 'BAR'
SLOTS = (SLOT_SYSTEM, SLOT_TOWN, SLOT_STORY, SLOT_DOCTOR, SLOT_BAR)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _base_dir() -> str:
    if config is not None:
        base = getattr(config, 'BASE_DIR', None)
        if base:
            return str(base)
    return _PROJECT_ROOT


def slot_file(slot: str, filename: str) -> str:
    """다른 슬롯의 상태 파일 경로. json_store.slot_path() 는 현재 슬롯만 가리킨다."""
    return os.path.join(_base_dir(), 'state', slot, filename)


def read_json(path: str, errors: Optional[List[str]] = None) -> Dict[str, Any]:
    """JSON 한 장을 읽는다. 없거나 깨졌으면 빈 dict.

    파일 핸들을 즉시 닫는다 — 윈도우에서 열어 두면 그 슬롯의 원자적 쓰기(os.replace)가
    실패하고, JsonStore 는 그 실패를 로그만 찍고 넘어가서 쓰기가 조용히 사라진다.
    """
    if not os.path.exists(path):
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
        msg = f"'{os.path.basename(path)}' 을(를) 읽지 못했습니다({type(e).__name__})"
        logger.warning(f"[슬롯읽기] {path}: {e}")
        if errors is not None:
            errors.append(msg)
        return {}
    if not isinstance(data, dict):
        logger.warning(f"[슬롯읽기] {path}: 최상위가 dict 가 아님 - 무시")
        return {}
    return data


# --------------------------------------------------------------------------- #
# game_state (슬롯마다 따로 있다)
# --------------------------------------------------------------------------- #
_VALUE_FIELD = 'v'
_DATE_FIELD = 'd'


def _unstamp(raw: Any, today: Optional[str]) -> Any:
    """날짜 스탬프가 붙은 값을 푼다. 오늘 것이 아니면 None.

    `오늘*` 키는 디스크에 {'v': 값, 'd': '2026-07-16'} 로 저장된다(game_state.py).
    purge_stale() 은 @SYSTEM 에서만 돌아서 다른 슬롯엔 지난 스탬프가 쌓여 있다 —
    날짜를 반드시 봐야 한다. 안 보면 어제 값을 오늘 것으로 읽는다.
    """
    if not isinstance(raw, dict) or _VALUE_FIELD not in raw:
        return raw  # 스탬프 없는 누적 키(의무실방문횟수 등)
    if today is not None and str(raw.get(_DATE_FIELD, '')) != today:
        return None
    return raw.get(_VALUE_FIELD)


def read_game_state(slot: str, today: Optional[str] = None,
                    errors: Optional[List[str]] = None) -> Dict[str, Dict[str, Any]]:
    """해당 슬롯의 game_state 를 {uid: {key: value}} 로. today 를 주면 지난 값은 뺀다."""
    raw = read_json(slot_file(slot, 'game_state.json'), errors)
    out: Dict[str, Dict[str, Any]] = {}
    for uid, keys in raw.items():
        if not isinstance(keys, dict):
            continue
        cleaned = {}
        for k, v in keys.items():
            value = _unstamp(v, today)
            if value is not None:
                cleaned[k] = value
        if cleaned:
            out[str(uid)] = cleaned
    return out


def _today_str() -> Optional[str]:
    """game_state 가 스탬프에 쓰는 것과 같은 형식의 오늘 날짜."""
    try:
        from utils import game_state
        return game_state._today()
    except Exception as e:
        logger.warning(f"[슬롯읽기] 오늘 날짜를 구하지 못했습니다: {e}")
        return None


# --------------------------------------------------------------------------- #
# 슬롯별 수집
# --------------------------------------------------------------------------- #
def read_story(today: Optional[str] = None,
               errors: Optional[List[str]] = None) -> Dict[str, Any]:
    """@STORY — 대화. **2차 상대는 여기에만 있다.**

    행동로그는 primary_partner_name 하나만 기록한다(talk_command.py).
    A가 B·C·D와 얘기해도 시트엔 "A가 B와 대화"뿐이라, 나머지는 이 JSON 이 유일한 기록이다.
    """
    gs = read_game_state(SLOT_STORY, today, errors)
    talks: Dict[str, Dict[str, Any]] = {}
    for uid, keys in gs.items():
        partners = keys.get('오늘대화상대')
        entry: Dict[str, Any] = {}
        if partners:
            names = [n.strip() for n in str(partners).split(',') if n.strip()]
            if names:
                entry['상대'] = names
        if keys.get('오늘대화여부'):
            entry['대화소진'] = True
        if keys.get('오늘고발'):
            entry['고발소진'] = True
        if entry:
            talks[uid] = entry

    live = read_json(slot_file(SLOT_STORY, 'talk_sessions.json'), errors)
    sessions = {}
    for uid, s in live.items():
        if not isinstance(s, dict):
            continue
        sessions[str(uid)] = {
            '최초상대': s.get('primary_partner_name', ''),
            '상대별횟수': s.get('per_partner') or {},
            '총횟수': s.get('total', 0),
        }
    return {'대화': talks, '진행중': sessions}


def read_doctor(day: Any = None, today: Optional[str] = None,
                errors: Optional[List[str]] = None) -> Dict[str, Any]:
    """@DOCTOR — 소견 · 대화록 · 그때의 스탯 스냅샷. 전부 시트에 없다."""
    charts_raw = read_json(slot_file(SLOT_DOCTOR, 'doctor_charts.json'), errors)
    charts: Dict[str, List[Dict[str, Any]]] = {}
    for uid, notes in charts_raw.items():
        if not isinstance(notes, list):
            continue
        picked = [n for n in notes
                  if isinstance(n, dict) and (day is None or _same_day(n.get('일차'), day))]
        if picked:
            charts[str(uid)] = picked

    sessions_raw = read_json(slot_file(SLOT_DOCTOR, 'doctor_sessions.json'), errors)
    visits: Dict[str, Dict[str, Any]] = {}
    for uid, s in sessions_raw.items():
        if not isinstance(s, dict):
            continue
        if day is not None and not _same_day(s.get('day'), day):
            continue
        visits[str(uid)] = {
            '일차': s.get('day'),
            '턴수': s.get('turns', 0),
            '대화록': s.get('history') or [],
            '차트': s.get('chart') or '',
            '진행중': bool(s.get('active') and not s.get('finalized')),
        }

    gs = read_game_state(SLOT_DOCTOR, today, errors)
    history: Dict[str, Any] = {}
    for uid, keys in gs.items():
        entry = {}
        if keys.get('의무실방문횟수') is not None:
            entry['누적방문'] = keys['의무실방문횟수']
        last = keys.get('의무실지난방문')
        if isinstance(last, dict):
            entry['지난방문'] = last
        if entry:
            history[uid] = entry

    return {'소견': charts, '진료': visits, '이력': history}


def read_bar(today: Optional[str] = None,
             errors: Optional[List[str]] = None) -> Dict[str, Any]:
    """@BAR — 게임별 플레이 횟수(시트엔 금액만) + 지급 사고 경보."""
    gs = read_game_state(SLOT_BAR, today, errors)
    plays: Dict[str, Dict[str, int]] = {}
    for uid, keys in gs.items():
        counts = {}
        for key, label in (('오늘슬롯', '슬롯머신'), ('오늘크랩스', '크랩스'),
                           ('오늘블랙잭', '블랙잭')):
            try:
                n = int(keys.get(key) or 0)
            except (ValueError, TypeError):
                n = 0
            if n:
                counts[label] = n
        if counts:
            plays[uid] = counts

    # 지급 도중 프로세스가 죽은 판. 재화 사고라 GM 이 알아야 한다.
    orphans = []
    bj = read_json(slot_file(SLOT_BAR, 'blackjack_sessions.json'), errors)
    for uid, s in bj.items():
        if isinstance(s, dict) and s.get('payout_attempted') is not None:
            orphans.append({'아이디': str(uid), '베팅': s.get('bet'),
                            '지급시도': s.get('payout_attempted')})

    return {'플레이': plays, '지급중단': orphans}


def _same_day(a: Any, b: Any) -> bool:
    """일차 비교 ('3', '3.0', 3 관용)."""
    sa, sb = str(a or '').strip(), str(b or '').strip()
    if sa == sb:
        return True
    if not sa or not sb:
        return False
    try:
        return int(float(sa)) == int(float(sb))
    except (ValueError, TypeError):
        return False


def fetch_slot_facts(day: Any = None,
                     errors: Optional[List[str]] = None) -> Dict[str, Any]:
    """모든 슬롯의 로컬 JSON 을 한 번에. 실패는 errors 에 담고 계속한다.

    @TOWN 은 읽지 않는다 — 조사 데이터는 설계상 전부 시트에 있고
    (investigation_state 는 메모리에만 산다), 로컬 JSON 에 고유한 게 없다.
    """
    if errors is None:
        errors = []
    today = _today_str()
    return {
        'story': read_story(today, errors),
        'doctor': read_doctor(day, today, errors),
        'bar': read_bar(today, errors),
        'errors': errors,
    }

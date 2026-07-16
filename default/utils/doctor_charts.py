"""의무실 차트 — 지난 진료에서 러셀이 남긴 소견을 보관한다.

목적: 다음에 그 사람이 오면 러셀이 이어 말할 수 있게 한다.
    "전에 드린 두통약은 좀 들었습니까."

**소견은 별도 AI 호출로 만들지 않는다.** 의사가 진료를 마무리하는 그 응답에
`chart` 필드로 같이 실어 보낸다(`DOCTOR_REPLY_SCHEMA`) — 추가 API 호출 0회.

active 세션(`doctor_sessions.json`)과 파일을 나눈 이유: 저건 진행 중인 대화라
끝나면 지워지고, 이건 이벤트 내내 쌓이는 기록이라 수명이 다르다.

AI가 꺼져 있거나 폴백으로 내려가면 소견이 없다 → 그 방문은 차트에 남지 않는다.
없는 소견을 지어내느니 비워 두는 편이 낫다(러셀은 수치·횟수 이력만으로도 재진을 안다).
"""

import threading
from typing import Any, Dict, List, Optional

try:
    from utils.logging_config import logger
except ImportError:  # pragma: no cover
    import logging
    logger = logging.getLogger('doctor_charts')

from utils.json_store import JsonStore, slot_path

# 사람당 남기는 소견 수. 진료가 이보다 쌓이면 오래된 것부터 버린다.
# 러셀이 참고할 만한 건 최근 몇 번이고, 프롬프트를 무한히 불릴 수 없다.
MAX_NOTES_PER_PATIENT = 5

# 소견 한 건의 길이 상한(문자). 모델이 규칙을 어겨 길게 써도 프롬프트가 터지지 않게.
MAX_NOTE_CHARS = 400


class ChartBook:
    """user_id -> 지난 소견 목록(오래된 것 → 최신 순)."""

    def __init__(self, store: Optional[JsonStore] = None) -> None:
        # `store or ...` 금지: JsonStore는 __len__이 있어 **빈 저장소가 falsy**다.
        self._store = store if store is not None else JsonStore(
            slot_path('doctor_charts.json'), max_entries=500)
        self._lock = threading.Lock()

    def add(self, user_id: str, day: Any, note: str) -> None:
        """진료 1건의 소견을 남긴다. 빈 소견은 저장하지 않는다."""
        note = (note or '').strip()
        if not note:
            return
        if len(note) > MAX_NOTE_CHARS:
            note = note[:MAX_NOTE_CHARS].rstrip() + '…'

        with self._lock:
            notes = list(self._store.get(str(user_id), []) or [])
            notes.append({'일차': day, '소견': note})
            if len(notes) > MAX_NOTES_PER_PATIENT:
                notes = notes[-MAX_NOTES_PER_PATIENT:]
            self._store.set(str(user_id), notes)

    def notes(self, user_id: str) -> List[Dict[str, Any]]:
        """지난 소견들(오래된 것 → 최신). 없으면 빈 리스트."""
        raw = self._store.get(str(user_id), []) or []
        if not isinstance(raw, list):
            return []
        out = []
        for item in raw:
            if isinstance(item, dict) and item.get('소견'):
                out.append({'일차': item.get('일차'), '소견': str(item['소견'])})
        return out

    def clear(self, user_id: str) -> None:
        with self._lock:
            self._store.delete(str(user_id))


_book: Optional[ChartBook] = None
_lock = threading.Lock()


def get_chart_book() -> ChartBook:
    global _book
    if _book is None:
        with _lock:
            if _book is None:
                _book = ChartBook()
    return _book


def set_chart_book(book: Optional[ChartBook]) -> None:
    """테스트용 주입."""
    global _book
    with _lock:
        _book = book


def format_charts(notes: List[Dict[str, Any]]) -> str:
    """지난 소견을 의사 프롬프트용 여러 줄로. 없으면 빈 문자열."""
    if not notes:
        return ''
    lines = []
    for item in notes:
        day = item.get('일차')
        head = f"{day}일차" if day else "지난 진료"
        lines.append(f"  - {head}: {item.get('소견', '')}")
    return "\n".join(lines)

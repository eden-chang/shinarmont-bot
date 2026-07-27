"""
utils/stat_gate.py — 능력치 게이트 / 이성 문구 발송

시너몬트 봇 공통 인프라 유틸(코딩_계획 §5).

제공 함수:
- is_hospitalized(user_row) -> bool
    건강 <= HEALTH_HOSPITALIZE_THRESHOLD 이면 입원(파생, 무저장).
- apply_sanity_messages(sheets_manager, system_sheets_manager, api, user_id)
    관리 시트에서 이성/건강/이름을 읽어, 임계값별 **고정 경고 문구**를
    최초 1회씩 DM 발송. 재발송 방지는 game_state 영구 플래그로 관리한다.

2026-07-27 변경: 경고 문구를 '이성' 시트 참조에서 **코드 고정 문구**로 바꾸고,
'발송여부' 칸(시트) 대신 game_state에 발송 여부를 기록한다. 이성뿐 아니라
건강 저하 문구도 함께 발송한다. system_sheets_manager 인자는 하위호환용으로
남겨두되 더 이상 사용하지 않는다.

일일 자동 감소(건강/이성 -N)는 폐지됐다(2026-07-18 운영 결정) — 수치 변동은 GM이 수동으로 한다.

시트 접근 규칙:
- 관리(건강/이성/이름/아이디) = 기본 sheets_manager, 항상 use_cache=False.
"""

import os
import sys
from typing import Any, Dict, List, Optional, Tuple, Union

# 경로 설정 (VM 환경 대응)
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

try:
    from config.settings import config
    from utils.logging_config import logger
    from utils.dm_sender import queue_dm
    from utils import game_state
except ImportError:  # pragma: no cover - VM 환경 폴백
    import logging
    logger = logging.getLogger('stat_gate')

    import importlib.util
    _config_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'settings.py')
    _spec = importlib.util.spec_from_file_location("settings", _config_path)
    _settings = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_settings)
    config = _settings.config

    from utils import game_state

    def queue_dm(receiver_id, message):  # type: ignore
        logger.error("DM 전송기 미초기화 - queue_dm 폴백")


# 관리 시트 컬럼명 상수
MGMT_SHEET = '관리'
COL_NAME = '이름'
COL_ID = '아이디'
COL_HEALTH = '건강'
COL_SANITY = '이성'


# ---------------------------------------------------------------------------
# 고정 경고 문구 (2026-07-27: '이성' 시트 참조 폐지, 코드 상수로 고정)
# ---------------------------------------------------------------------------

SANITY_MSG1 = (
    "머리가 깨질 것처럼 무겁다. 잠을 자도 잔 것 같지 않고, 어제 한 일이 오늘 한 일처럼 뒤섞인다. "
    "어딘가 이상하게 돌아가고 있다는 느낌이 자꾸 드는데, 아마도 피곤해서 그런 것일 테다. 다들 그렇다고 하니까."
)
SANITY_MSG2 = (
    "눈앞에 보고 싶지 않은 무언가가 자꾸 어른거린다. 고개를 돌리면 사라지고, 사라졌다는 사실이 더 두렵다. "
    "방금 들은 말과 들은 것 같은 말이 구별되지 않는다. 이제는 무엇이 이상한지조차 확신할 수 없다. 나 자신을 포함해서."
)
HEALTH_MSG1 = (
    "몸이 무겁다. 계단 몇 개에 숨이 차고, 손끝이 하루 종일 저릿하다. "
    "거울 속 얼굴빛이 이 마을의 흙과 비슷한 색이 되어 간다. 좀 쉬면 나아질 것이다. 쉴 수만 있다면."
)
HEALTH_MSG2 = (
    "일어서면 바닥이 기울고, 코피가 이유 없이 터진다. 씹는 것도 삼키는 것도 일이 되었다. "
    "몸이 하루하루 무언가에게 조금씩 양보하고 있다. 무엇에게인지는 모른 채로. 이대로라면, 그리 오래 버티지 못할 것이다."
)

# 스탯 저하 경고 문구 테이블
# (스탯 종류, 임계값 config 키, 기본 임계, 고정 문구, game_state 발송 플래그 키)
# 발송 플래그는 '오늘' 접두 없이 → game_state에 영구 저장(1회 발송 후 재발송 안 함).
STAT_MSG_TIERS = [
    ('sanity', 'SANITY_MSG1_THRESHOLD', 50, SANITY_MSG1, '이성문구1발송'),
    ('sanity', 'SANITY_MSG2_THRESHOLD', 20, SANITY_MSG2, '이성문구2발송'),
    ('health', 'HEALTH_MSG1_THRESHOLD', 50, HEALTH_MSG1, '건강문구1발송'),
    ('health', 'HEALTH_MSG2_THRESHOLD', 20, HEALTH_MSG2, '건강문구2발송'),
]


def _to_int(raw: Any, default: Optional[int] = None) -> Optional[int]:
    """시트 셀 값을 정수로 변환. 실패 시 default 반환."""
    try:
        return int(float(raw))
    except (ValueError, TypeError):
        return default


def is_hospitalized(user_row: Union[Dict[str, Any], int, float, str]) -> bool:
    """
    입원(파생) 여부.

    건강 <= HEALTH_HOSPITALIZE_THRESHOLD 이면 True.
    - user_row 가 dict 이면 '건강' 컬럼을 읽는다(없으면 100으로 간주).
    - user_row 가 숫자/숫자문자열이면 건강 수치로 간주한다.
    파싱 불가하면 False(입원 아님)로 처리한다.
    """
    threshold = getattr(config, 'HEALTH_HOSPITALIZE_THRESHOLD', 20)

    if isinstance(user_row, dict):
        health_raw = user_row.get(COL_HEALTH, 100)
    else:
        health_raw = user_row

    health = _to_int(health_raw, default=None)
    if health is None:
        return False
    return health <= threshold


def _find_row_by(rows: List[Dict[str, Any]], column: str, value: str) -> Optional[Dict[str, Any]]:
    target = str(value).strip()
    for row in rows:
        if str(row.get(column, '')).strip() == target:
            return row
    return None


def _resolve_character(sheets_manager, user_id: str) -> Optional[Tuple[str, str, int, int]]:
    """
    관리 시트에서 user_id(아이디 우선, 없으면 이름)로 캐릭터를 찾아
    (이름, DM 수신 아이디, 이성, 건강) 튜플 반환. 없으면 None.
    """
    try:
        mgmt = sheets_manager.get_worksheet_data(MGMT_SHEET, use_cache=False)
    except Exception as e:
        logger.warning(f"[stat_gate] 관리 시트 조회 실패: {e}")
        return None

    row = _find_row_by(mgmt, COL_ID, user_id)
    if row is None:
        # user_id 가 이름으로 전달된 경우 폴백
        row = _find_row_by(mgmt, COL_NAME, user_id)
    if row is None:
        return None

    name = str(row.get(COL_NAME, '')).strip()
    recipient = str(row.get(COL_ID, '')).strip() or str(user_id).strip()
    sanity = _to_int(row.get(COL_SANITY, 100), default=100)
    health = _to_int(row.get(COL_HEALTH, 100), default=100)
    if not name:
        return None
    return name, recipient, sanity, health


def apply_sanity_messages(sheets_manager, system_sheets_manager, api, user_id: str) -> None:
    """
    이성/건강 저하 경고 문구 자동 발송(최초 1회, 고정 문구).

    관리 시트에서 대상 캐릭터의 이름/이성/건강을 확보한 뒤, 임계값별 고정 문구를
    game_state 발송 플래그가 비어 있을 때만 1회 DM 발송하고 플래그를 세운다.

    system_sheets_manager / api 인자는 하위호환용으로 남겨둔 것으로 더 이상 쓰지 않는다.
    """
    if sheets_manager is None:
        return

    resolved = _resolve_character(sheets_manager, user_id)
    if resolved is None:
        return
    name, recipient, sanity, health = resolved
    _send_stat_messages(name, recipient, sanity, health)


def _send_stat_messages(name: str, recipient: str, sanity: int, health: int) -> int:
    """임계값별 고정 문구를 최초 1회씩 발송한다(game_state check-and-set로 재발송 방지).

    Returns: 이번 호출에서 실제로 큐잉한 DM 개수.
    """
    stat_value = {'sanity': sanity, 'health': health}
    sent = 0

    for stat_kind, cfg_key, default_th, phrase, flag_key in STAT_MSG_TIERS:
        value = stat_value.get(stat_kind)
        if value is None:
            continue
        threshold = getattr(config, cfg_key, default_th)
        if value > threshold:
            continue
        # check_and_set(limit=1): 최초 호출만 True(+기록), 이후엔 False → 1회만 발송.
        # 원자적이므로 동시 호출에도 중복 발송되지 않는다.
        if not game_state.check_and_set(recipient, flag_key, 1):
            continue
        queue_dm(recipient, phrase)
        sent += 1
        logger.info(
            f"[stat_gate] 경고 문구 발송: {name}({recipient}) '{flag_key}' ({stat_kind}={value})"
        )
    return sent


def reconcile_stat_messages(sheets_manager, system_sheets_manager=None, api=None) -> int:
    """봇 시작 시 미발송 경고 문구 재확인·재전송 스윕(전 캐릭터).

    봇이 중간에 끊겨 DM이 못 나갔을 수 있으므로, 관리 시트의 모든 캐릭터에 대해
    '현재 이성/건강값 vs 임계값 + game_state 발송 플래그'를 재검사한다.
    이미 발송된(플래그 기록됨) 문구는 건너뛰고, 아직 안 나갔는데 현재값이
    임계 이하인 문구만 지금 DM으로 보낸다. 그 사이 값이 바뀌었어도(예: 이성 60→38)
    항상 '현재값' 기준으로 판정한다. 재발송 방지는 apply와 동일하게 game_state
    플래그로 하므로 매 시작 호출해도 중복 발송되지 않는다.

    system_sheets_manager / api 인자는 apply와 시그니처를 맞추기 위한 것으로 미사용.

    Returns: 이번 스윕에서 큐잉한 DM 개수.
    """
    if sheets_manager is None:
        return 0
    try:
        rows = sheets_manager.get_worksheet_data(MGMT_SHEET, use_cache=False) or []
    except Exception as e:
        logger.warning(f"[stat_gate] 관리 시트 조회 실패 - 시작 재전송 스윕 생략: {e}")
        return 0

    sent = 0
    checked = 0
    for row in rows:
        name = str(row.get(COL_NAME, '')).strip()
        recipient = str(row.get(COL_ID, '')).strip()
        if not name or not recipient:
            continue
        # 한 캐릭터에서 문제가 나도 나머지 전원 스윕은 계속되도록 격리한다.
        try:
            sanity = _to_int(row.get(COL_SANITY, 100), default=100)
            health = _to_int(row.get(COL_HEALTH, 100), default=100)
            sent += _send_stat_messages(name, recipient, sanity, health)
            checked += 1
        except Exception as e:
            logger.warning(f"[stat_gate] 시작 재전송 스윕 중 오류({name}/{recipient}): {e}")

    logger.info(f"[stat_gate] 시작 재전송 스윕 완료 - {checked}명 검사, {sent}건 발송")
    return sent


"""
[의무실 방문] / [대화 끝내기] 명령어 (@DOCTOR, DM 전용)

docs/시너몬트_구현계획.md §5.9 / docs/AI_API_설계.md §4 / docs/코딩_계획.md §6 / docs/의사_페르소나.md.

- DM 전용(@dm_only). 하루 1회(game_state '오늘의무실', limit=1) — 방문을 "시작한 날" 기준으로 소모.
- 방문 시작 시에는 회복하지 않는다. 러셀 선생과 답글 스레드로 진료 대화를 이어가고,
  **의사가 흐름을 보며 스스로 진료를 마무리**하면(concluding) 그때 처치 결과를 '관리'에 반영한다.
  · 처치 결정: ai_client.doctor_treatment(대화 맥락→{처치, 건강Δ, 이성Δ, 사유}). AI 실패/거부 시 규칙 폴백.
  · 변동은 config DOCTOR_TREAT_DELTA_MIN/MAX로 클램프, 능력치 0~100 유지. 건강 회복으로 입원 자동 해제.
- 다중 타래: 세션은 session_id로 관리(사용자당 여러 개 공존 가능). 봇이 보낸 답글 status_id를
  utils.reply_threads에 등록해, 사용자가 어느 타래에 답글하든 그 타래로 이어진다. 자정을 넘겨
  이어가도 "시작한 날"로 카운트되므로, 다음 날 새 [의무실 방문]을 또 쓸 수 있다.
- [대화 끝내기]: 진행 중인 진료를 처치 없이 강제 종료(방문 횟수는 이미 소모됨).
- **세션은 디스크 영속**(`state/{슬롯}/doctor_sessions.json`) — 봇을 업데이트하려고 껐다 켜도
  진행 중인 진료 대화가 이어진다. 답글 라우팅(utils.reply_threads)도 함께 영속화된다.
- **재방문 컨텍스트**: 누적 방문 횟수·직전 방문 일차/수치를 game_state에 남겨(디스크 영속)
  러셀이 초진과 재진을 구분하고 지난번과 견주어 말한다(ai_client.format_visit_context).

시트 접근:
- 관리(건강/이성) = self.sheets_manager, 항상 use_cache=False.
- 행동로그 = self.system_sheets_manager.
"""

import os
import sys
import re
import uuid
import random
import threading
from typing import Any, Dict, List, Optional, Tuple

# 경로 설정 (VM 환경 대응)
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))

from utils.imports import *  # noqa: F401,F403  (register_command/BaseCommand/config/logger 등)

from utils.dm_guard import dm_only
from utils.lock_manager import get_lock_manager
from utils.dice_parser import evaluate_amount
from utils.game_state import check_and_set
from utils import game_state
from utils import doctor_charts
from utils import game_day
from utils import action_log
from utils import ai_client
from utils import stat_gate
from utils import reply_threads
from utils.json_store import JsonStore, slot_path


# 회복식 파싱 실패 시 폴백 회복량(고정)
_FALLBACK_HEAL = 5
# 능력치 상·하한
_STAT_MAX = 100
_STAT_MIN = 0
# 인메모리 세션 보관 상한(초과 시 오래된/종료 세션부터 폐기)
_SESSION_CAP = 200
# 답글-스레드 등록에 쓰는 명령어 키워드
_THREAD_KEYWORD = '의무실'

# 의사(러셀) 폴백 대사 풀 — AI 미사용/실패/거부 시 랜덤 선택.
# ANTHROPIC_API_KEY가 비어 있으면 항상 이 풀에서 나오므로, 반복을 줄이려 여러 개를 둔다.
DOCTOR_FALLBACK_LINES = [
    "(그는 은테 안경 너머로 상대를 잠시 살핀다. 만년필 끝이 차트 위에서 잠시 멈춘다.) 앉으시죠. 오늘은 어디가 불편해서 오셨습니까?",
    "잠은 좀 주무십니까? (맥을 짚으며 나직이 묻는다. 대답을 듣는 둥 마는 둥, 시선은 당신의 손끝에 오래 머문다.)",
    "요즘··· 이상한 걸 보거나 들은 적은 없고요? (미소는 잃지 않은 채, 대답의 끝을 놓치지 않으려는 듯 고개를 살짝 기울인다.)",
    "(소독약에 손을 담갔다 꺼내며 사무적으로 처치를 이어간다.) 별것 아닙니다. 다만 무리하지 마시고, 누구와 다투는 일도 삼가시는 게 좋겠습니다.",
    "요즘 누구와 자주 어울리십니까? (별 뜻 없는 안부처럼 던진 뒤, 차트에 무언가를 적는 만년필 소리가 작게 들린다.)",
    "(그는 당신의 안색을 오래 들여다본다.) ···괜찮습니다, 이 정도면. (그 '이 정도'가 무엇을 뜻하는지는 끝내 말하지 않는다.)",
]
def _object_josa(word: str) -> str:
    """목적격 조사 '을/를'. 처치명은 AI가 지어내므로 받침을 그때그때 본다.

    한글이 아닌 끝글자(영문·숫자)는 '를'로 둔다 — 어색해도 틀린 티는 덜 난다.
    """
    text = str(word or '').strip()
    if not text:
        return '를'
    last = text[-1]
    if '가' <= last <= '힣':
        return '을' if (ord(last) - 0xAC00) % 28 else '를'
    return '를'


# 첫 응답에만 붙는 진행 안내. 중간 턴에는 붙이지 않는다(몰입 유지 — 수정4).
THREAD_GUIDE_LINE = "◎ 타래로 멘션을 달아 진료를 이어갈 수 있습니다."

# 대화 종료 시 연출.
# 정상 종료에는 쓰지 않는다 — 의사가 스스로 마무리한 말이 곧 끝인사다(수정7).
# 이미 종료된 세션에 다시 답글이 오거나, 처치 반영이 실패했을 때의 대체 문구로만 남긴다.
DOCTOR_CLOSING_LINE = (
    "(의사가 차트를 덮으며 회중시계를 흘끗 본다.) 오늘은 여기까지 하죠. 몸조심하십시오, 부디. "
    "(진료실 문이 조용히 닫힌다.)"
)


# =====================================================================
# 의무실 대화 세션 관리(인메모리, session_id 키) — 다중 타래 지원
# =====================================================================
class _DoctorSessionManager:
    """진료 대화 세션을 session_id로 보관하는 스레드 안전 싱글톤.

    세션 dict: {'id', 'user_id', 'day', 'turns', 'history', 'patient',
                'active', 'finalized', 'final_message', 'end_at', 'chart'}
    - 'chart': 의사가 마무리 발화에 실어 보낸 차트 소견. 종료 시 doctor_charts로 옮겨진다.
    - 사용자당 여러 세션이 공존할 수 있다(자정 넘긴 타래 + 새 방문).
    - **디스크 영속**(`state/{슬롯}/doctor_sessions.json`). 봇을 껐다 켜도 진료 대화가
      이어진다. 인메모리 dict를 작업본으로 두고, 변경이 생길 때마다 파일에 반영한다.

    영속화 주의:
        호출측이 `start()`/`get()`이 돌려준 dict를 **직접 수정**하는 자리가 있다
        (예: `session['end_at'] = ...`). 그런 수정은 이 클래스가 알 수 없으므로
        수정 후 반드시 `touch(session_id)`를 불러 저장시켜야 한다.
    """

    def __init__(self, store: Optional[JsonStore] = None) -> None:
        # `store or ...` 금지: JsonStore가 __len__을 정의해 **빈 저장소는 falsy**다.
        # 그러면 주입한 빈 저장소가 조용히 무시되고 기본 파일이 쓰인다(테스트 오염).
        self._store = store if store is not None else JsonStore(
            slot_path('doctor_sessions.json'), max_entries=_SESSION_CAP)
        # 인메모리 작업본 — 호출측이 들고 있는 참조와 동일 객체여야 하므로 dict로 유지
        self._sessions: Dict[str, Dict[str, Any]] = {
            sid: dict(s) for sid, s in self._store.items().items()
            if isinstance(s, dict)
        }
        self._lock = threading.Lock()
        if self._sessions:
            logger.info(f"[의무실] 진료 세션 {len(self._sessions)}건 복원")

    def _persist_locked(self, session_id: str) -> None:
        """세션 하나를 파일에 반영(락 안에서 호출). JsonStore가 자체 락·원자적 저장."""
        session = self._sessions.get(str(session_id))
        if session is not None:
            self._store.set(str(session_id), session)

    def touch(self, session_id: Optional[str]) -> None:
        """호출측이 세션 dict를 직접 수정한 뒤 저장을 요청한다."""
        if not session_id:
            return
        with self._lock:
            self._persist_locked(session_id)

    def start(self, user_id: str, day: int, patient: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            sid = uuid.uuid4().hex
            session = {
                'id': sid,
                'user_id': str(user_id),
                'day': day,
                'turns': 0,
                'history': [],
                'patient': dict(patient or {}),
                'active': True,
                'finalized': False,
                'final_message': None,
                'end_at': None,
            }
            self._sessions[sid] = session
            self._prune_locked()
            self._persist_locked(sid)
            return session

    def get(self, session_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not session_id:
            return None
        with self._lock:
            return self._sessions.get(str(session_id))

    def latest_for_user(self, user_id: str, active_only: bool = False) -> Optional[Dict[str, Any]]:
        """해당 사용자의 가장 최근 세션(삽입 순서 기준). active_only면 진행 중인 것만."""
        with self._lock:
            matches = [
                s for s in self._sessions.values()
                if s.get('user_id') == str(user_id)
                and (not active_only or (s.get('active') and not s.get('finalized')))
            ]
            return matches[-1] if matches else None

    def append_turn(self, session_id: str, user_msg: str, assistant_msg: str) -> None:
        with self._lock:
            session = self._sessions.get(str(session_id))
            if session is None:
                return
            history = session['history']
            if user_msg:
                history.append({"role": "user", "content": user_msg})
            if assistant_msg:
                history.append({"role": "assistant", "content": assistant_msg})
            session['turns'] += 1
            self._persist_locked(session_id)

    def end(self, session_id: str) -> None:
        with self._lock:
            session = self._sessions.get(str(session_id))
            if session is not None:
                session['active'] = False
                self._persist_locked(session_id)

    def begin_finalize(self, session_id: str) -> bool:
        """종료 처리를 원자적으로 선점(check-and-set). 이미 종료(중)이면 False.

        동시성이 생겨도 처치가 두 번 적용되지 않도록, finalized/active를 락 안에서 함께 설정.
        재시작을 넘겨도 두 번 적용되지 않도록 즉시 파일에도 반영한다.
        """
        with self._lock:
            session = self._sessions.get(str(session_id))
            if session is None or session.get('finalized'):
                return False
            session['finalized'] = True
            session['active'] = False
            self._persist_locked(session_id)
            return True

    def _drop_locked(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)
        self._store.delete(session_id)

    def _prune_locked(self) -> None:
        """상한 초과 시 종료 세션 → 오래된 세션 순으로 폐기(락 안에서 호출)."""
        if len(self._sessions) <= _SESSION_CAP:
            return
        for sid in [s for s, v in self._sessions.items() if v.get('finalized')]:
            if len(self._sessions) <= _SESSION_CAP:
                break
            self._drop_locked(sid)
        while len(self._sessions) > _SESSION_CAP:
            oldest = next(iter(self._sessions))
            self._drop_locked(oldest)


_session_manager: Optional[_DoctorSessionManager] = None
_session_instance_lock = threading.Lock()


def get_doctor_session() -> _DoctorSessionManager:
    """전역 `_DoctorSessionManager` 싱글톤."""
    global _session_manager
    if _session_manager is None:
        with _session_instance_lock:
            if _session_manager is None:
                _session_manager = _DoctorSessionManager()
    return _session_manager


def _session_key_from_context(context: 'CommandContext') -> Optional[str]:
    """핸들러가 답글-스레드로 라우팅한 경우 실린 session_key를 꺼낸다."""
    entry = context.get_metadata('reply_thread')
    if isinstance(entry, dict):
        return entry.get('session_key')
    return None


@register_command(
    name="의무실 방문",
    aliases=["의무실", "의무실방문"],
    description="의무실을 방문해 의사와 대화하고, 대화가 끝나면 처치를 받습니다. (DM 전용, 하루 1회)",
    category="의무실",
    examples=["[의무실 방문]", "[의무실 방문] 머리가 너무 아파요"],
    requires_sheets=True,
    requires_api=True,
)
class InfirmaryCommand(BaseCommand):
    """의무실 방문 — 의사 NPC 대화 + 대화 종료 시 처치(건강/이성 반영)."""

    @staticmethod
    def get_supported_keywords() -> List[str]:
        return ["의무실 방문", "의무실", "의무실방문"]

    def __init__(self, sheets_manager=None, api=None, **kwargs):
        super().__init__(sheets_manager, api, **kwargs)
        self.system_sheets_manager = kwargs.get('system_sheets_manager')
        logger.debug(
            "[의무실] InfirmaryCommand 초기화: system_sheets_manager=%s",
            '있음' if self.system_sheets_manager else '없음',
        )

    # ------------------------------------------------------------------
    # 진입점
    # ------------------------------------------------------------------
    @dm_only
    def execute(self, context: CommandContext) -> CommandResponse:
        user_id = context.user_id
        utterance = self._parse_utterance(context)
        day = game_day.current_day()

        # 답글-스레드로 라우팅된 이어가기인지 판별.
        session_key = _session_key_from_context(context)
        kw0 = (context.keywords[0] if getattr(context, 'keywords', None) else '').replace(' ', '')
        is_new_visit_kw = kw0 == '의무실방문'   # [의무실 방문]/[의무실방문] = 새 방문 의도

        try:
            if session_key and not is_new_visit_kw:
                session = get_doctor_session().get(session_key)
                if session and session.get('active') and not session.get('finalized'):
                    return self._continue_session(user_id, session, utterance)
                return CommandResponse.create_success(
                    "그 진료 대화는 이미 끝났습니다. 새로 진료를 받으려면 [의무실 방문]을 사용해 주세요."
                )
            return self._start_session(context, user_id, day, utterance)
        except CommandError as e:
            return CommandResponse.create_error(str(e), error=e)
        except Exception as e:  # noqa: BLE001 - 최종 방어
            logger.error(f"[의무실] 처리 중 오류: {e}", exc_info=True)
            return CommandResponse.create_error("의무실 처리 중 오류가 발생했습니다.", error=e)

    # ------------------------------------------------------------------
    # 세션 시작(회복 없음 — 처치는 대화 종료 시)
    # ------------------------------------------------------------------
    def _start_session(
        self, context: CommandContext, user_id: str, day: int, utterance: str
    ) -> CommandResponse:
        lock_manager = get_lock_manager()
        with lock_manager.acquire_lock(user_id, timeout=10.0) as acquired:
            if not acquired:
                return CommandResponse.create_error(
                    "다른 처리가 진행 중입니다. 잠시 후 다시 시도해 주세요."
                )

            # 락 내부 재조회(stale 방지)
            mgmt_row = self._get_management_row(user_id)
            if mgmt_row is None:
                raise CommandError(
                    "관리 시트에 등록되어 있지 않아 진료를 진행할 수 없습니다. 관리자에게 문의해 주세요."
                )

            # 일일 제한(시작한 날 기준): 오늘의무실 1회
            if not check_and_set(user_id, '오늘의무실', 1):
                return CommandResponse.create_success(
                    "오늘은 이미 의무실을 방문했습니다. 내일 다시 찾아와 주세요."
                )

            # 현재 능력치 스냅샷(회복 없음)
            health_col = getattr(config, 'STAT_HEALTH_COLUMN', '건강')
            sanity_col = getattr(config, 'STAT_SANITY_COLUMN', '이성')
            name = str(mgmt_row.get('이름', '') or mgmt_row.get('아이디', '')).strip() or '환자'
            job = str(mgmt_row.get(getattr(config, 'STAT_JOB_COLUMN', '직군'), '') or '').strip()
            cur_health = self._to_int(mgmt_row.get(health_col, 0), 0)
            cur_sanity = self._to_int(mgmt_row.get(sanity_col, 0), 0)

        # === 락 해제 이후: 세션 시작 + 첫 대화(지연 가능) ===
        # 슬롯을 이미 소비했으므로, 이 뒤에서 예상외 예외가 나면 오늘의무실을 롤백해 방문 낭비를 막는다.
        try:
            patient = {
                '이름': name,
                '직군': job,
                '건강': cur_health,
                '이성': cur_sanity,
                '입원': stat_gate.is_hospitalized(cur_health),
                '일차': day,
                '최근요약': self._recent_summary(name),
            }
            # 재방문 컨텍스트 — 러셀이 초진/재진을 구분하고 지난번과 견주어 말하게 한다.
            # 이번 방문을 이력에 반영한 뒤 그 값을 patient에 싣는다.
            patient.update(
                self._record_and_load_visit(user_id, day, cur_health, cur_sanity)
            )

            session = get_doctor_session().start(user_id, day, patient)
            session['end_at'] = self._pick_end_at()   # AI 미사용 시 종료 시점(폴백)
            session_id = session['id']
            get_doctor_session().touch(session_id)    # 직접 수정분 저장
            # 첫 발화(turn 1) — 최소 발화 전이라 종료(concluding)는 강제로 무시된다.
            reply, _concluding = self._doctor_turn([], patient, utterance, turn_no=1, session=session)
            get_doctor_session().append_turn(session_id, utterance, reply)
            # 이 응답이 전송되면 그 status_id를 이 세션으로 등록(다음 답글이 이어지도록).
            reply_threads.stage(user_id, _THREAD_KEYWORD, session_id)

            message = self._build_start_message(reply)
            data = {'name': name, 'health': cur_health, 'sanity': cur_sanity, 'session_id': session_id}
            return CommandResponse.create_success(message, data=data)
        except Exception:
            try:
                game_state.set(user_id, '오늘의무실', 0)
            except Exception as rollback_err:
                logger.error("[의무실] 일일 슬롯 롤백 실패: user=%s, err=%s", user_id, rollback_err)
            raise

    # ------------------------------------------------------------------
    # 세션 이어가기(자유 대화) — 의사가 스스로 마무리하면 처치 확정
    # ------------------------------------------------------------------
    def _continue_session(
        self, user_id: str, session: Dict[str, Any], utterance: str
    ) -> CommandResponse:
        session_id = session['id']

        if session.get('finalized'):
            return CommandResponse.create_success(
                "그 진료는 이미 끝났습니다. 새로 진료를 받으려면 [의무실 방문]을 사용해 주세요."
            )

        if not utterance:
            return CommandResponse.create_success(
                "의사가 당신의 다음 말을 기다립니다. 하고 싶은 말을 이어서 전해 주세요."
            )

        patient = session.get('patient', {})
        history = list(session.get('history', []))
        turn_no = int(session.get('turns', 0)) + 1   # 이번에 생성할 의사 발화 번호
        reply, concluding = self._doctor_turn(history, patient, utterance, turn_no=turn_no, session=session)
        get_doctor_session().append_turn(session_id, utterance, reply)

        if concluding:
            updated = get_doctor_session().get(session_id) or session
            result_msg = self._finalize_treatment(user_id, updated)
            reply_threads.discard(user_id)   # 스레드 종료 → 더 등록하지 않음
            return CommandResponse.create_success(f"{reply}\n\n{result_msg}")

        reply_threads.stage(user_id, _THREAD_KEYWORD, session_id)   # 다음 답글도 이 스레드로
        return CommandResponse.create_success(self._build_reply_message(reply))

    # ------------------------------------------------------------------
    # 처치 확정(대화 종료 시 1회) — 관리 시트 반영
    # ------------------------------------------------------------------
    def _finalize_treatment(self, user_id: str, session: Dict[str, Any]) -> str:
        """대화 맥락으로 처치를 결정해 관리 시트에 반영하고, 종료 메시지를 반환한다."""
        # 원자적 선점: 이미 종료(중)이면 처치를 다시 적용하지 않는다(중복 방지).
        if not get_doctor_session().begin_finalize(session['id']):
            return session.get('final_message') or DOCTOR_CLOSING_LINE

        patient = session.get('patient', {})
        history = list(session.get('history', []))
        name = patient.get('이름', '환자')

        # 이번 진료의 소견을 차트에 남긴다 — 다음 내원 때 러셀에게 그대로 돌려준다.
        # 처치 반영보다 먼저 한다: 실패해도 진료를 막지 않지만, 순서를 뒤에 두면
        # 처치 도중 예외가 났을 때 소견만 조용히 사라진다.
        self._save_chart(user_id, session)

        outcome = self._decide_treatment(history, patient)
        applied = self._apply_treatment(user_id, outcome)

        if applied is None:
            msg = (
                f"{DOCTOR_CLOSING_LINE}\n\n"
                "(처치를 반영하는 중 문제가 있었습니다. 관리자에게 문의해 주세요.)"
            )
            session['final_message'] = msg
            get_doctor_session().touch(session.get('id'))
            return msg

        self._log_treatment(name, outcome, applied)
        self._check_sanity_messages(user_id)

        msg = self._build_finish_message(outcome, applied)
        session['final_message'] = msg
        get_doctor_session().touch(session.get('id'))
        return msg

    def _decide_treatment(self, history: list, patient: dict) -> Dict[str, Any]:
        """AI(우선)/규칙(폴백)으로 처치 결과를 정하고 변동을 클램프한다."""
        dmax = self._delta_max()
        dmin = self._delta_min()

        outcome: Optional[Dict[str, Any]] = None
        try:
            outcome = ai_client.doctor_treatment(history, patient)
        except Exception as e:  # noqa: BLE001 - AI 지연/오류 방어
            logger.error(f"[의무실] 처치 판정 예외: {e}", exc_info=True)
            outcome = None

        if not outcome:
            outcome = self._rule_treatment(patient, history)

        outcome['treatment'] = str(outcome.get('treatment') or '경과관찰')
        outcome['health_delta'] = max(dmin, min(dmax, self._to_int(outcome.get('health_delta'), 0)))
        outcome['sanity_delta'] = max(dmin, min(dmax, self._to_int(outcome.get('sanity_delta'), 0)))
        outcome['reason'] = str(outcome.get('reason') or '')
        return outcome

    def _rule_treatment(self, patient: dict, history: list) -> Dict[str, Any]:
        """AI 미사용/실패 시 규칙 기반 처치(무해하게 회복만).

        대화 톤(캐물음의 집요함)은 규칙으로 판별할 수 없으므로 '과잉심문'은 AI만 판정한다.
        규칙 폴백은 더 낮은 능력치를 회복한다: '진통제'=건강, '안정제'=이성, 동률이면 '상담'(둘 다 소폭).
        """
        dmax = self._delta_max()
        heal_h = min(dmax, self._eval_heal(getattr(config, 'INFIRMARY_HEAL_HEALTH', '2d6+2')))
        heal_s = min(dmax, self._eval_heal(getattr(config, 'INFIRMARY_HEAL_SANITY', '2d6+2')))

        cur_health = self._to_int(patient.get('건강', 0), 0)
        cur_sanity = self._to_int(patient.get('이성', 0), 0)

        if cur_health < cur_sanity:
            return {
                'treatment': '진통제',
                'health_delta': heal_h,
                'sanity_delta': 0,
                'reason': '통증 완화 처치(규칙 판정).',
            }
        if cur_sanity < cur_health:
            return {
                'treatment': '안정제',
                'health_delta': 0,
                'sanity_delta': heal_s,
                'reason': '안정 처치(규칙 판정).',
            }
        # 동률: 상담(이성 회복 + 건강 소폭)
        return {
            'treatment': '상담',
            'health_delta': max(1, heal_h // 2),
            'sanity_delta': heal_s,
            'reason': '상담을 통한 안정(규칙 판정).',
        }

    def _apply_treatment(self, user_id: str, outcome: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """관리 시트의 건강/이성에 변동을 반영(락→재조회→클램프→batch_update). 실패 시 None."""
        health_col = getattr(config, 'STAT_HEALTH_COLUMN', '건강')
        sanity_col = getattr(config, 'STAT_SANITY_COLUMN', '이성')

        lock_manager = get_lock_manager()
        with lock_manager.acquire_lock(user_id, timeout=10.0) as acquired:
            if not acquired:
                logger.warning("[의무실] 처치 반영 락 획득 실패: user=%s", user_id)
                return None

            mgmt_row = self._get_management_row(user_id)
            if mgmt_row is None:
                logger.warning("[의무실] 처치 반영 대상 행 없음: user=%s", user_id)
                return None

            header = [k for k in mgmt_row.keys() if k != '_row_number']
            row_index = mgmt_row.get('_row_number')
            cur_health = self._to_int(mgmt_row.get(health_col, 0), 0)
            cur_sanity = self._to_int(mgmt_row.get(sanity_col, 0), 0)
            was_hosp = stat_gate.is_hospitalized(cur_health)

            new_health = self._clamp_stat(cur_health + self._to_int(outcome.get('health_delta'), 0))
            new_sanity = self._clamp_stat(cur_sanity + self._to_int(outcome.get('sanity_delta'), 0))

            updates: List[Tuple[int, int, Any]] = []
            if row_index is not None and health_col in header and new_health != cur_health:
                updates.append((row_index, header.index(health_col) + 1, new_health))
            if row_index is not None and sanity_col in header and new_sanity != cur_sanity:
                updates.append((row_index, header.index(sanity_col) + 1, new_sanity))

            if updates:
                ok = self.sheets_manager.batch_update_cells('관리', updates)
                if not ok:
                    logger.warning("[의무실] 처치 batch_update 실패")
                    return None
                invalidate_user_cache()

        return {
            'cur_health': cur_health,
            'cur_sanity': cur_sanity,
            'new_health': new_health,
            'new_sanity': new_sanity,
            'health_change': new_health - cur_health,
            'sanity_change': new_sanity - cur_sanity,
            'was_hospitalized': was_hosp,
        }

    # ------------------------------------------------------------------
    # 의사 대화(AI)
    # ------------------------------------------------------------------
    def _doctor_turn(
        self, history: list, patient: dict, utterance: str, turn_no: int, session: Optional[dict]
    ) -> Tuple[str, bool]:
        """의사 발화 1회를 생성한다. 반환 (대사, 종료여부).

        - AI: doctor_reply({reply, concluding, chart}). 실패/거부/AI 꺼짐 → 고정 폴백 대사 + end_at 기준 종료.
        - 경계 강제: turn_no < 최소 발화면 종료 금지, 최대 발화면 강제 종료.
        - 마무리 발화에 실려 온 차트 소견은 session['chart']에 담아 둔다.
          (_finalize_treatment 가 꺼내 저장한다 — 별도 요약 호출을 두지 않으려는 설계)
        """
        min_turns = self._min_turns()
        max_turns = self._max_turns()
        ideal = self._ideal_range()
        effective = utterance or "···(환자가 말없이 진료를 기다린다)"

        result = None
        try:
            result = ai_client.doctor_reply(
                history, patient, effective,
                turn_no=turn_no, min_turns=min_turns, max_turns=max_turns, ideal=ideal,
            )
        except Exception as e:  # noqa: BLE001 - AI 지연/오류 방어
            logger.error(f"[의무실] doctor_reply 예외: {e}", exc_info=True)
            result = None

        if isinstance(result, dict) and result.get('reply'):
            reply = result['reply']
            concluding = bool(result.get('concluding'))
            # 마무리 턴이면 의사가 같은 응답에 차트 소견을 실어 보낸다.
            # 경계 강제로 종료가 뒤집힐 수 있으니(아래) 일단 받아 두고 저장은 종료 시점에 한다.
            if session is not None and result.get('chart'):
                session['chart'] = result['chart']
        else:
            reply = random.choice(DOCTOR_FALLBACK_LINES)
            end_at = self._to_int(session.get('end_at') if session else None, ideal[1])
            concluding = turn_no >= end_at

        # 경계 강제(최소 발화 전엔 종료 금지, 최대 발화에서 강제 종료)
        if turn_no < min_turns:
            concluding = False
        if turn_no >= max_turns:
            concluding = True
        return reply, concluding

    def _eval_heal(self, expr: str) -> int:
        """회복식을 평가해 정수 회복량 반환(음수 방지). 실패 시 폴백."""
        try:
            value, _detail = evaluate_amount(expr)
            return max(0, int(value))
        except (ValueError, TypeError) as e:
            logger.warning(f"[의무실] 회복식 파싱 실패({expr!r}) → 폴백 {_FALLBACK_HEAL}: {e}")
            return _FALLBACK_HEAL

    # ------------------------------------------------------------------
    # 보조
    # ------------------------------------------------------------------
    def _parse_utterance(self, context: CommandContext) -> str:
        """원문에서 첫 [ ... ] 대괄호 명령을 제거한 뒤 남은 자유 발화를 추출."""
        text = context.original_text or ''
        if not text:
            return ''
        # 첫 대괄호 블록 제거(명령 구문). 없으면 원문 그대로(순수 답글).
        stripped = re.sub(r'\[[^\]]*\]', '', text, count=1)
        return stripped.strip()

    def _get_management_row(self, user_id: str) -> Optional[Dict[str, Any]]:
        """관리 시트에서 user_id 행 조회(항상 최신값)."""
        try:
            rows = self.sheets_manager.get_worksheet_data('관리', use_cache=False)
        except Exception as e:
            logger.error(f"[의무실] 관리 워크시트 조회 실패: {e}", exc_info=True)
            raise CommandError("환자 정보를 불러올 수 없습니다. 잠시 후 다시 시도해 주세요.")

        for row in rows or []:
            if str(row.get('아이디', '')).strip() == str(user_id).strip():
                return row
        return None

    def _save_chart(self, user_id: str, session: Dict[str, Any]) -> None:
        """마무리 발화에 실려 온 소견을 차트에 남긴다.

        소견이 없으면(AI 꺼짐·폴백·모델이 안 채움) 아무것도 남기지 않는다.
        없는 소견을 지어내느니 비워 두는 편이 낫다 — 러셀은 방문 횟수·수치 이력만으로도
        재진임을 안다(format_visit_context).
        """
        note = str(session.get('chart') or '').strip()
        if not note:
            return
        try:
            doctor_charts.get_chart_book().add(user_id, session.get('day'), note)
        except Exception as e:  # noqa: BLE001
            # 차트는 연출용 부가 기록이다. 실패해도 처치·응답을 막지 않는다.
            logger.warning(f"[의무실] 차트 기록 실패(무시하고 진행): {e}")

    # 재방문 이력 game_state 키.
    # '오늘'로 시작하지 않으므로 날짜 스탬프 만료 대상이 아니다 → 이벤트 전체 기간 누적.
    _VISIT_COUNT_KEY = '의무실방문횟수'
    _LAST_VISIT_KEY = '의무실지난방문'   # {'일차': n, '건강': n, '이성': n}
    _PRIOR_VISIT_KEY = '의무실사전진료'  # 게임 시작 전 첫 입주 후 검진으로 만난 횟수(1~3, 최초 1회 랜덤 배정)

    def _prior_visits(self, user_id: str) -> int:
        """게임 시작 전 러셀과 만난 횟수(첫 입주 후 검진). 주민마다 1~3을 한 번 랜덤 배정해 영속화한다.

        시너몬트의 모든 주민은 입주 직후 검진으로 러셀을 이미 만난 사이다 → 완전한 초진은 없다.
        한 번 정해지면 바뀌지 않도록 game_state에 남긴다(디스크 영속).
        """
        try:
            prior = int(game_state.get(user_id, self._PRIOR_VISIT_KEY, 0) or 0)
        except (TypeError, ValueError):
            prior = 0
        if prior <= 0:
            prior = random.randint(1, 3)
            game_state.set(user_id, self._PRIOR_VISIT_KEY, prior)
        return prior

    def _record_and_load_visit(self, user_id: str, day: int,
                               health: int, sanity: int) -> Dict[str, Any]:
        """이번 방문을 이력에 기록하고, 의사에게 넘길 재방문 컨텍스트를 반환한다.

        읽기 → (이전 값으로 컨텍스트 구성) → 이번 방문으로 갱신 순서다.
        '지난방문'은 **이번 방문 직전**의 값이어야 하므로 갱신 전에 읽어 둔다.

        game_state는 디스크 영속(state/game_state.json)이라 봇을 재시작해도 유지된다.

        Returns:
            patient에 합칠 dict: 방문횟수(사전진료 포함, 이번 포함) / 사전진료 / 지난방문일차
                                / 지난건강 / 지난이성
                                / 지난차트(러셀이 지난 진료에 직접 적어 둔 소견)
        """
        try:
            previous = game_state.get(user_id, self._LAST_VISIT_KEY, None) or {}
            try:
                count = int(game_state.get(user_id, self._VISIT_COUNT_KEY, 0) or 0)
            except (TypeError, ValueError):
                count = 0
            count += 1

            # 모든 주민은 첫 입주 후 검진으로 러셀과 이미 만난 사이다 → 사전진료를 누적 횟수에 얹는다.
            prior = self._prior_visits(user_id)

            context: Dict[str, Any] = {'방문횟수': count + prior, '사전진료': prior}
            if isinstance(previous, dict) and previous:
                context['지난방문일차'] = previous.get('일차')
                context['지난건강'] = previous.get('건강')
                context['지난이성'] = previous.get('이성')

            # 지난 진료에서 러셀 자신이 남긴 소견 — 이걸 돌려줘야 그가 이어 말한다.
            # ("전에 드린 두통약은 좀 들었습니까")
            try:
                notes = doctor_charts.get_chart_book().notes(user_id)
                formatted = doctor_charts.format_charts(notes)
                if formatted:
                    context['지난차트'] = formatted
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[의무실] 지난 차트 조회 실패(없이 진행): {e}")

            game_state.set(user_id, self._VISIT_COUNT_KEY, count)
            game_state.set(user_id, self._LAST_VISIT_KEY,
                           {'일차': day, '건강': health, '이성': sanity})
            return context
        except Exception as e:
            # 이력은 연출용 부가 정보다. 실패해도 진료 자체를 막지 않는다.
            logger.warning(f"[의무실] 재방문 이력 기록/조회 실패 - 초진으로 진행: {e}")
            return {'방문횟수': 1}

    def _recent_summary(self, name: str) -> str:
        """행동로그 최근 기록에서 해당 캐릭터의 요약 몇 줄을 뽑아 의사 컨텍스트로."""
        try:
            records = action_log.recent(self.system_sheets_manager, days=3)
        except Exception as e:
            logger.debug(f"[의무실] 최근 기록 조회 실패: {e}")
            return '기록 없음'

        mine = [
            str(r.get('요약', '')).strip()
            for r in (records or [])
            if str(r.get('행위자', '')).strip() == name and str(r.get('요약', '')).strip()
        ]
        if not mine:
            return '기록 없음'
        return ' / '.join(mine[-3:])

    def _log_treatment(self, name: str, outcome: Dict[str, Any], applied: Dict[str, Any]) -> None:
        """행동로그에 처치 요약 1줄 기록(소문 소스)."""
        try:
            treatment = outcome.get('treatment', '처치')
            hc = applied['health_change']
            sc = applied['sanity_change']
            # 조사 헬퍼를 두고도 하드코딩해 '데보라이(가)'가 로그에 남고 있었다.
            summary = f"{add_i_ga(name)} 의무실에서 {treatment} 처치를 받았다"

            detail_parts: List[str] = []
            if hc:
                detail_parts.append(f"건강 {hc:+d}")
            if sc:
                detail_parts.append(f"이성 {sc:+d}")
            if not detail_parts:
                detail_parts.append("변화 미미")
            if applied.get('was_hospitalized') and not stat_gate.is_hospitalized(applied['new_health']):
                detail_parts.append("입원 해제")

            change_bits = [f"{label} {val:+d}" for label, val in (('건강', hc), ('이성', sc)) if val]
            stat_change = ' / '.join(change_bits) or '변화 없음'

            action_log.append(
                self.system_sheets_manager,
                kind=action_log.KIND_INFIRMARY,
                actor=name,
                target='의사',
                summary=summary,
            )
        except Exception as e:
            logger.warning(f"[의무실] 행동로그 기록 실패: {e}")

    def _check_sanity_messages(self, user_id: str) -> None:
        """처치로 이성이 바뀐 직후 이성 임계 문구 자동 발송 검사."""
        try:
            stat_gate.apply_sanity_messages(
                self.sheets_manager, self.system_sheets_manager, self.api, user_id
            )
        except Exception as e:
            logger.debug(f"[의무실] 이성 문구 검사 실패: {e}")

    def _build_start_message(self, doctor_line: str) -> str:
        """첫 응답에만 붙는 안내(수정3)."""
        return "\n".join([
            doctor_line,
            "",
            THREAD_GUIDE_LINE,
        ])

    @staticmethod
    def _build_reply_message(doctor_line: str) -> str:
        """진료 중간 턴 — 안내 없이 러셀의 말만(수정4).

        매 턴 같은 안내를 반복하면 대화의 몰입이 끊긴다.
        이어가는 법은 첫 응답에서 이미 알렸다.
        """
        return doctor_line

    @staticmethod
    def _delta_phrase(label: str, change: int) -> str:
        """수치 변동을 사람이 읽는 말로. 예: '건강 5 회복' / '이성 3 감소'."""
        return f"{label} {abs(change)} {'회복' if change > 0 else '감소'}"

    def _build_finish_message(self, outcome: Dict[str, Any], applied: Dict[str, Any]) -> str:
        """진료 결과 줄.

        **의사의 끝인사를 여기서 다시 붙이지 않는다**(수정7). 러셀이 스스로 마무리한
        대사가 이미 끝인사이고, 고정 대사를 덧붙이면 두 번 작별하는 꼴이 된다.
        호출측이 `{러셀 대사}\n\n{이 메시지}` 로 합친다.
        """
        treatment = outcome.get('treatment', '처치')
        hc = applied['health_change']
        sc = applied['sanity_change']

        bits: List[str] = []
        if hc:
            bits.append(self._delta_phrase('건강', hc))
        if sc:
            bits.append(self._delta_phrase('이성', sc))

        if bits:
            lines = [f"➭ {treatment} 사용, " + ", ".join(bits)]
        else:
            # 변화가 없어도 무엇을 받았는지는 남긴다.
            lines = [f"➭ 의사가 {treatment}{_object_josa(treatment)} 처방했다. "
                     f"특별한 변화는 없었다."]

        if applied.get('was_hospitalized') and not stat_gate.is_hospitalized(applied['new_health']):
            lines.append("➭ 건강이 회복되어 입원 상태가 해제되었습니다.")

        return "\n".join(lines)

    def _max_turns(self) -> int:
        try:
            return max(1, int(getattr(config, 'DOCTOR_MAX_TURNS', 8)))
        except (TypeError, ValueError):
            return 8

    def _min_turns(self) -> int:
        try:
            return max(1, int(getattr(config, 'DOCTOR_TURNS_MIN', 2)))
        except (TypeError, ValueError):
            return 2

    def _ideal_range(self) -> Tuple[int, int]:
        try:
            lo = int(getattr(config, 'DOCTOR_TURNS_IDEAL_MIN', 3))
            hi = int(getattr(config, 'DOCTOR_TURNS_IDEAL_MAX', 6))
        except (TypeError, ValueError):
            lo, hi = 3, 6
        lo = max(1, lo)
        hi = max(lo, hi)
        return lo, hi

    def _pick_end_at(self) -> int:
        """AI 미사용 시 종료 시점(이상 범위에서 무작위, [최소, 최대]로 클램프)."""
        lo, hi = self._ideal_range()
        return max(self._min_turns(), min(self._max_turns(), random.randint(lo, hi)))

    def _delta_max(self) -> int:
        try:
            return int(getattr(config, 'DOCTOR_TREAT_DELTA_MAX', 12))
        except (TypeError, ValueError):
            return 12

    def _delta_min(self) -> int:
        try:
            return int(getattr(config, 'DOCTOR_TREAT_DELTA_MIN', -6))
        except (TypeError, ValueError):
            return -6

    @classmethod
    def _clamp_stat(cls, value: int) -> int:
        return max(_STAT_MIN, min(_STAT_MAX, int(value)))

    @staticmethod
    def _to_int(raw: Any, default: int = 0) -> int:
        try:
            return int(float(raw))
        except (ValueError, TypeError):
            return default


@register_command(
    name="대화 끝내기",
    aliases=["대화끝내기", "진료 종료", "진료종료"],
    description="진행 중인 의무실 진료 대화를 처치 없이 끝냅니다. (오늘 방문 횟수는 소모됩니다)",
    category="의무실",
    examples=["[대화 끝내기]"],
    requires_sheets=False,
    requires_api=False,
)
class EndDoctorTalkCommand(BaseCommand):
    """진행 중인 의무실 대화를 처치 없이 강제 종료한다(방문 횟수는 이미 소모됨)."""

    @staticmethod
    def get_supported_keywords() -> List[str]:
        return ["대화 끝내기", "대화끝내기", "진료 종료", "진료종료"]

    @dm_only
    def execute(self, context: CommandContext) -> CommandResponse:
        user_id = context.user_id
        mgr = get_doctor_session()

        # 답글-스레드로 온 경우 그 타래를, 아니면 사용자의 가장 최근 진행 중 세션을 종료.
        session_key = _session_key_from_context(context)
        session = mgr.get(session_key) if session_key else mgr.latest_for_user(user_id, active_only=True)

        if not session or not session.get('active') or session.get('finalized'):
            return CommandResponse.create_success(
                "진행 중인 의무실 진료 대화가 없습니다."
            )

        # 처치 없이 종료 — 오늘 방문 횟수는 이미 소모됨.
        mgr.end(session['id'])
        session['finalized'] = True
        mgr.touch(session['id'])
        reply_threads.discard(user_id)
        message = (
            f"{DOCTOR_CLOSING_LINE}\n\n"
            "진료 대화를 끝냈습니다. 오늘 방문은 사용되었고, 별도의 처치는 없습니다."
        )
        session['final_message'] = message
        mgr.touch(session['id'])
        return CommandResponse.create_success(message)

"""
[대화] @상대 (비밀 대화) 명령어 구현

docs/시너몬트_구현계획.md §5.7, docs/명령어_개요.md, docs/코딩_계획.md §6(그룹 B).

- DM 전용(@dm_only), 카테고리 '스토리'(@STORY 슬롯).
- 하루 1회(game_state '오늘대화여부', limit=1)만 세션을 시작할 수 있다.
- 같은 상대와 연속일 대화 금지(game_state '어제대화상대' / '오늘대화상대' 비교).
- 대상은 status.mentions 에서 해석(utils.target_helpers.resolve_target). 자기자신/미등록 방지.
- 답글 스레드 세션(인메모리, investigation_state.py 패턴):
    · 첫 [대화]로 세션을 시작(root_status_id / 멘션 카운트 저장).
    · 이후 봇 답글에 계속 [대화]로 답글을 이어감(같은 유저 = 같은 세션).
    · 멘션 캡: 캐릭터당 3회 · 총 6회. 캡 도달 시 종료 안내.
- 실제 정보 릴레이 없이 연출 + 카운트만 수행한다(상대에게 대화 내용 전달 없음).
- 세션 종료 시 action_log.append(kind='대화', ...) 로 요약 1행 기록.
"""

import os
import sys
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# 경로 설정 (VM 환경 대응)
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from utils.imports import *  # noqa: F401,F403  (config, logger, CommandResponse, BaseCommand, register_command 등)

from utils import game_state
from utils.json_store import JsonStore, slot_path
from utils import action_log
from utils.dm_guard import dm_only
from utils.target_helpers import resolve_target, get_target_error
from utils.korean_utils import has_final_consonant, get_last_char


# 멘션 캡 (config 로 조정 가능, 기본 캐릭터당 3 / 총 6)
_PER_CAP_DEFAULT = 3
_TOTAL_CAP_DEFAULT = 6

# game_state 키
_KEY_TODAY_TALKED = '오늘대화여부'
_KEY_TODAY_PARTNER = '오늘대화상대'
_KEY_YESTERDAY_PARTNER = '어제대화상대'


def _per_cap() -> int:
    try:
        return int(getattr(config, 'TALK_MENTION_PER_CAP', _PER_CAP_DEFAULT))
    except (TypeError, ValueError):
        return _PER_CAP_DEFAULT


def _total_cap() -> int:
    try:
        return int(getattr(config, 'TALK_MENTION_TOTAL_CAP', _TOTAL_CAP_DEFAULT))
    except (TypeError, ValueError):
        return _TOTAL_CAP_DEFAULT


# ----------------------------------------------------------------------
# 인메모리 세션 (investigation_state.py 패턴)
# ----------------------------------------------------------------------
@dataclass
class TalkSession:
    """한 사용자의 진행 중 비밀 대화 세션."""
    root_status_id: Optional[str]       # 첫 [대화] 툿 ID
    primary_partner_name: str           # 최초 상대 이름
    primary_partner_id: str             # 최초 상대 아이디
    per_partner: Dict[str, int] = field(default_factory=dict)  # 상대 이름 -> 멘션 횟수
    total: int = 0                      # 총 멘션 횟수

    def to_dict(self) -> Dict[str, Any]:
        return {
            'root_status_id': self.root_status_id,
            'primary_partner_name': self.primary_partner_name,
            'primary_partner_id': self.primary_partner_id,
            'per_partner': dict(self.per_partner),
            'total': self.total,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'TalkSession':
        return cls(
            root_status_id=data.get('root_status_id'),
            primary_partner_name=data.get('primary_partner_name', ''),
            primary_partner_id=data.get('primary_partner_id', ''),
            per_partner=dict(data.get('per_partner') or {}),
            total=int(data.get('total') or 0),
        )


class TalkSessionManager:
    """사용자별 비밀 대화 세션을 관리하는 스레드 안전 싱글톤.

    **디스크 영속**(`state/{슬롯}/talk_sessions.json`) — 봇을 업데이트하려고 껐다 켜도
    진행 중인 비밀 대화가 이어진다(멘션 캡 카운트도 유지).

    영속화 주의:
        호출측이 `get()`이 돌려준 TalkSession을 직접 수정한다(예: `session.total += 1`).
        그런 수정은 이 클래스가 알 수 없으므로 수정 후 `touch(user_id)`를 불러야 한다.
    """

    def __init__(self, store: Optional[JsonStore] = None) -> None:
        # `store or ...` 금지: JsonStore는 __len__이 있어 **빈 저장소가 falsy**다.
        self._store = store if store is not None else JsonStore(
            slot_path('talk_sessions.json'))
        self._sessions: Dict[str, TalkSession] = {}
        for uid, data in self._store.items().items():
            if isinstance(data, dict):
                try:
                    self._sessions[uid] = TalkSession.from_dict(data)
                except Exception as e:  # 깨진 항목 하나가 기동을 막지 않게
                    logger.warning(f"[대화] 세션 복원 실패(건너뜀) user={uid}: {e}")
        self._lock = threading.Lock()
        if self._sessions:
            logger.info(f"[대화] 비밀 대화 세션 {len(self._sessions)}건 복원")

    def _persist_locked(self, user_id: str) -> None:
        session = self._sessions.get(str(user_id))
        if session is not None:
            self._store.set(str(user_id), session.to_dict())

    def touch(self, user_id: str) -> None:
        """호출측이 세션을 직접 수정한 뒤 저장을 요청한다."""
        with self._lock:
            self._persist_locked(user_id)

    def get(self, user_id: str) -> Optional[TalkSession]:
        with self._lock:
            return self._sessions.get(str(user_id))

    def start(self, user_id: str, session: TalkSession) -> None:
        with self._lock:
            self._sessions[str(user_id)] = session
            self._persist_locked(user_id)

    def clear(self, user_id: str) -> Optional[TalkSession]:
        with self._lock:
            removed = self._sessions.pop(str(user_id), None)
        self._store.delete(str(user_id))
        return removed

    def snapshot(self) -> Dict[str, TalkSession]:
        with self._lock:
            return dict(self._sessions)


_session_manager: Optional[TalkSessionManager] = None
_instance_lock = threading.Lock()


def get_talk_session_manager() -> TalkSessionManager:
    """전역 TalkSessionManager 싱글톤을 반환한다."""
    global _session_manager
    if _session_manager is None:
        with _instance_lock:
            if _session_manager is None:
                _session_manager = TalkSessionManager()
    return _session_manager


def _wa_gwa(name: str) -> str:
    """이름 뒤 '와/과' 조사."""
    last = get_last_char(name or '')
    return '과' if has_final_consonant(last) else '와'


@register_command(
    name="대화",
    aliases=["비밀대화"],
    description="특정 상대와 으슥한 곳에서 비밀 대화를 나눕니다. (DM 전용, 하루 1회)",
    category="스토리",
    examples=["[대화] @상대"],
    requires_sheets=True,
    requires_api=False,
)
class TalkCommand(BaseCommand):
    """비밀 대화 명령어."""

    @staticmethod
    def get_supported_keywords():
        return ["대화", "비밀대화"]

    def __init__(self, sheets_manager=None, api=None, **kwargs):
        super().__init__(sheets_manager, api, **kwargs)
        self.system_sheets_manager = kwargs.get('system_sheets_manager')
        logger.debug(
            f"[대화] TalkCommand 초기화: sheets={self.sheets_manager is not None}, "
            f"system_sheets={self.system_sheets_manager is not None}"
        )

    # ------------------------------------------------------------------
    @dm_only
    def execute(self, context: CommandContext) -> CommandResponse:
        try:
            session = get_talk_session_manager().get(context.user_id)
            if session is None:
                return self._start_session(context)
            return self._continue_session(context, session)
        except Exception as e:  # 방어적 처리
            logger.error(f"[대화] 실행 중 오류: {e}", exc_info=True)
            return CommandResponse.create_error("대화 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.")

    # ------------------------------------------------------------------
    # 세션 시작
    # ------------------------------------------------------------------
    def _start_session(self, context: CommandContext) -> CommandResponse:
        # 1) 대상 해석 (멘션 기반)
        target = resolve_target(context, self.sheets_manager, keyword_index=None)
        if not target:
            reason = get_target_error(context, "대화 상대를 지정해 주세요. 상대를 멘션(@)해야 합니다.")
            return CommandResponse.create_error(reason)

        partner_name = str(target.get('이름', '')).strip()
        partner_id = str(target.get('아이디', '')).strip()

        # 2) 연속일 같은 상대 금지 (일일 제한 소모 전에 검사)
        #    어제 실제로 대화한 상대들(1차+2차 포함)의 목록과 대조한다.
        yesterday_partners = self._parse_partners(
            game_state.get(context.user_id, _KEY_YESTERDAY_PARTNER, '')
        )
        if any(self._same_name(y, partner_name) for y in yesterday_partners):
            return CommandResponse.create_error(
                f"어제 {add_eul_reul_safe(partner_name)} 만났습니다. "
                "같은 상대와는 연이어 대화할 수 없습니다."
            )

        # 3) 하루 1회 세션 시작 제한
        if not game_state.check_and_set(context.user_id, _KEY_TODAY_TALKED, 1):
            return CommandResponse.create_error("오늘은 이미 비밀 대화를 나눴습니다. 내일 다시 시도해 주세요.")

        # 4) 세션 생성 + 첫 멘션 카운트(=1)
        session = TalkSession(
            root_status_id=context.get_metadata('status_id'),
            primary_partner_name=partner_name,
            primary_partner_id=partner_id,
        )
        session.total = 1
        session.per_partner[partner_name] = 1
        get_talk_session_manager().start(context.user_id, session)

        # 5) 오늘대화상대 기록 (연속일 판정용, 스케줄러가 00:00 이월)
        self._record_partner_today(context.user_id, partner_name)

        # 캡 도달 여부(시작 직후엔 보통 미도달) 확인
        if self._is_exhausted(session, partner_name):
            return self._end_session(context, session, partner_name, opening=True)

        remaining_total = _total_cap() - session.total
        remaining_partner = _per_cap() - session.per_partner[partner_name]
        wa = _wa_gwa(partner_name)
        msg = (
            f"으슥한 곳에서 {partner_name}{wa}의 비밀 대화가 시작됩니다.\n"
            f"이 답글에 [대화]로 답하며 이야기를 이어갈 수 있습니다.\n"
            f"(남은 대화: {partner_name} {remaining_partner}회 · 총 {remaining_total}회)"
        )
        return CommandResponse.create_success(msg)

    # ------------------------------------------------------------------
    # 세션 이어가기
    # ------------------------------------------------------------------
    def _continue_session(self, context: CommandContext, session: TalkSession) -> CommandResponse:
        # 이번 턴의 상대 결정: 멘션이 있으면 그 대상, 없으면 최초 상대
        turn_partner_name = session.primary_partner_name
        target = resolve_target(context, self.sheets_manager, keyword_index=None)
        if target and str(target.get('이름', '')).strip():
            turn_partner_name = str(target.get('이름', '')).strip()

        per_cap = _per_cap()
        total_cap = _total_cap()

        # 이미 캡에 도달한 상태면(정상 흐름에선 세션이 이미 종료됐어야 함) 종료 처리
        if session.total >= total_cap:
            return self._end_session(context, session, turn_partner_name,
                                     reason=f"총 {total_cap}회 대화를 모두 마쳤습니다.")
        if session.per_partner.get(turn_partner_name, 0) >= per_cap:
            return CommandResponse.create_error(
                f"{add_eun_neun_safe(turn_partner_name)} 이미 {per_cap}회 대화를 나눴습니다. "
                "더 이상 이어갈 수 없습니다."
            )

        # 멘션 카운트 증가
        session.total += 1
        session.per_partner[turn_partner_name] = session.per_partner.get(turn_partner_name, 0) + 1
        get_talk_session_manager().touch(context.user_id)   # 직접 수정분 저장

        # 실제로 대화한 상대이므로 오늘대화상대에 기록(2차 상대도 연속일 금지 대상이 되도록)
        self._record_partner_today(context.user_id, turn_partner_name)

        # 캡 도달 시 세션 종료 + 로그
        if self._is_exhausted(session, turn_partner_name):
            return self._end_session(context, session, turn_partner_name)

        remaining_total = total_cap - session.total
        remaining_partner = per_cap - session.per_partner[turn_partner_name]
        wa = _wa_gwa(turn_partner_name)
        msg = (
            f"{turn_partner_name}{wa}의 밀담이 이어집니다.\n"
            f"(남은 대화: {turn_partner_name} {remaining_partner}회 · 총 {remaining_total}회)"
        )
        return CommandResponse.create_success(msg)

    # ------------------------------------------------------------------
    # 세션 종료 (로그 + 안내)
    # ------------------------------------------------------------------
    def _end_session(self, context: CommandContext, session: TalkSession,
                     turn_partner_name: str, reason: str = '', opening: bool = False) -> CommandResponse:
        get_talk_session_manager().clear(context.user_id)

        actor = self._actor_name(context)
        # 대화 사실을 행동로그에 요약 기록 (docs 시너몬트_구현계획 §5.7)
        try:
            # '와/과'는 **앞 단어(actor)** 를 보고 정한다. 상대 이름으로 계산하면
            # '휴고과 원쥔'처럼 어긋난다(2026-07-16까지 그랬다).
            summary = (f"{actor}{_wa_gwa(actor)} "
                       f"{session.primary_partner_name} 비밀 대화를 나눴다")
            action_log.append(
                self.system_sheets_manager,
                kind=action_log.KIND_TALK,
                actor=actor,
                target=session.primary_partner_name,
                summary=summary,
            )
        except Exception as e:
            logger.warning(f"[대화] 행동로그 기록 실패: {e}")

        wa = _wa_gwa(turn_partner_name)
        if reason:
            tail = reason
        else:
            tail = f"{turn_partner_name}{wa}의 대화가 마무리되었습니다."
        msg = f"밀담이 끝났습니다. {tail}\n(오늘의 비밀 대화가 종료되었습니다.)"
        return CommandResponse.create_success(msg)

    # ------------------------------------------------------------------
    # 보조
    # ------------------------------------------------------------------
    def _is_exhausted(self, session: TalkSession, partner_name: str) -> bool:
        """총 캡 또는 해당 상대 캡에 도달했는지."""
        if session.total >= _total_cap():
            return True
        if session.per_partner.get(partner_name, 0) >= _per_cap():
            return True
        return False

    @staticmethod
    def _same_name(a: str, b: str) -> bool:
        return (a or '').strip().replace(' ', '').lower() == (b or '').strip().replace(' ', '').lower()

    @staticmethod
    def _parse_partners(raw: Any) -> List[str]:
        """오늘/어제대화상대 값(쉼표 구분 목록)을 이름 리스트로 파싱."""
        return [n for n in (p.strip() for p in str(raw or '').split(',')) if n]

    def _record_partner_today(self, user_id: str, name: str) -> None:
        """오늘 실제로 대화한 상대를 오늘대화상대 목록(쉼표 구분)에 누적 기록(중복 제외)."""
        name = (name or '').strip()
        if not name:
            return
        try:
            partners = self._parse_partners(game_state.get(user_id, _KEY_TODAY_PARTNER, ''))
            if not any(self._same_name(p, name) for p in partners):
                partners.append(name)
                game_state.set(user_id, _KEY_TODAY_PARTNER, ','.join(partners))
        except Exception as e:
            logger.warning(f"[대화] 오늘대화상대 기록 실패: {e}")

    def _actor_name(self, context: CommandContext) -> str:
        """행위자(=명령 사용자)의 명단 이름을 반환(없으면 표시명/아이디)."""
        try:
            roster = load_user_data(self.sheets_manager) or []
            uid = (context.user_id or '').strip().lstrip('@').lower()
            for row in roster:
                if str(row.get('아이디', '')).strip().lstrip('@').lower() == uid:
                    name = str(row.get('이름', '')).strip()
                    if name:
                        return name
        except Exception as e:
            logger.debug(f"[대화] 행위자 이름 조회 실패: {e}")
        return context.user_name or context.user_id


def add_eul_reul_safe(word: str) -> str:
    """add_eul_reul 폴백 래퍼('을/를' 포함)."""
    try:
        return add_eul_reul(word)
    except Exception:
        last = get_last_char(word or '')
        return f"{word}을" if has_final_consonant(last) else f"{word}를"


def add_eun_neun_safe(word: str) -> str:
    """add_eun_neun 폴백 래퍼('은/는' 포함)."""
    try:
        return add_eun_neun(word)
    except Exception:
        last = get_last_char(word or '')
        return f"{word}은" if has_final_consonant(last) else f"{word}는"

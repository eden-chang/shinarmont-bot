"""
[대화] @상대 (비밀 대화) 명령어 구현 — **공유 타래 세션**

docs/시너몬트_구현계획.md §5.7, docs/명령어_개요.md, docs/코딩_계획.md §6(그룹 B).

동작(2026-07-18 개편):
- DM 전용(@dm_only), 카테고리 '스토리'(@STORY 슬롯).
- @STORY(이야기 봇 자신)는 대상 해석에서 제외한다. 따라서 `[대화] @STORY @상대`,
  `@상대 @STORY [대화]` 등 **태그 위치와 무관하게** 상대만 지정되면 진행된다.
- **한 대화는 두 사람이 공유하는 하나의 타래 세션**이다.
    · 시작자 A가 `[대화] @상대 @STORY`로 세션을 연다(카운트=1, 봇이 안내 답글).
    · 이후 A·B 누구든 **[대화]를 다시 쓰지 않아도**, 상대와 @STORY를 태그한 채 그 타래에
      답글을 달면 같은 세션으로 이어진다(카운트+1). 중간 턴은 봇이 침묵(카운트만).
    · 총 멘션이 캡(기본 6)에 이르면 봇이 '대화 종료'를 안내하고 세션을 닫는다.
- **하루 1회 제한은 '시작한 사람'만** 소모한다(game_state '오늘대화여부').
  초대되어 참여한 상대는 소모하지 않으므로, 같은 날 자기 대화를 따로 시작할 수 있고
  한 사람이 여러 명에게서 대화 초대를 받아도 된다.
- **연속일 같은 상대 금지도 '시작자↔시작 상대'에만 적용**한다(game_state '어제대화상대'
  / '오늘대화상대'). 초대되어 참여한 것은 다음날 금지 대상으로 기록하지 않는다.
- 실제 정보 릴레이 없이 연출 + 카운트만 수행한다(상대에게 대화 내용 전달 없음).
- 세션 종료 시 action_log.append(kind='대화', ...) 로 요약 1행 기록.

타래 라우팅:
- utils.reply_threads 에 세션 참여자(participants)를 실어 등록한다. 핸들러는 참여자면
  원작성자가 아니어도 그 타래를 이어가도록 허용한다(stream_handler._resolve_routing).
- 봇이 답하지 않는 중간 턴에도 다음 답글이 라우팅되도록, 방금 들어온 툿 id를
  reply_threads.link 로 세션에 즉시 매핑한다.
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
from utils import reply_threads
from utils.json_store import JsonStore, slot_path
from utils import action_log
from utils.dm_guard import dm_only
from utils.target_helpers import resolve_target, get_target_error
from utils.korean_utils import has_final_consonant, get_last_char


# 총 멘션 캡 (config 로 조정 가능, 기본 6). 공유 세션이므로 캐릭터별 캡은 두지 않는다.
_TOTAL_CAP_DEFAULT = 6

_THREAD_KEYWORD = '대화'

# game_state 키
_KEY_TODAY_TALKED = '오늘대화여부'
_KEY_TODAY_PARTNER = '오늘대화상대'
_KEY_YESTERDAY_PARTNER = '어제대화상대'


def _total_cap() -> int:
    try:
        return int(getattr(config, 'TALK_MENTION_TOTAL_CAP', _TOTAL_CAP_DEFAULT))
    except (TypeError, ValueError):
        return _TOTAL_CAP_DEFAULT


def _norm_acct(acct: Any) -> str:
    return str(acct or '').strip().lstrip('@').strip().lower()


# ----------------------------------------------------------------------
# 인메모리 세션 (session_key = 최초 [대화] 툿 id)
# ----------------------------------------------------------------------
@dataclass
class TalkSession:
    """두 사람이 공유하는 진행 중 비밀 대화 세션."""
    session_key: str                    # 세션 식별자(최초 [대화] 툿 id)
    initiator_name: str                 # 시작자 이름
    initiator_acct: str                 # 시작자 아이디(acct)
    partner_name: str                   # 시작 상대 이름
    partner_acct: str                   # 시작 상대 아이디(acct)
    total: int = 0                      # 공유 멘션 횟수

    def participants(self) -> List[str]:
        """타래를 이어쓸 수 있는 계정(acct, 정규화) 목록."""
        return [_norm_acct(self.initiator_acct), _norm_acct(self.partner_acct)]

    def to_dict(self) -> Dict[str, Any]:
        return {
            'session_key': self.session_key,
            'initiator_name': self.initiator_name,
            'initiator_acct': self.initiator_acct,
            'partner_name': self.partner_name,
            'partner_acct': self.partner_acct,
            'total': self.total,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'TalkSession':
        return cls(
            session_key=str(data.get('session_key', '')),
            initiator_name=data.get('initiator_name', ''),
            initiator_acct=data.get('initiator_acct', ''),
            partner_name=data.get('partner_name', ''),
            partner_acct=data.get('partner_acct', ''),
            total=int(data.get('total') or 0),
        )


class TalkSessionManager:
    """session_key별 공유 비밀 대화 세션을 관리하는 스레드 안전 싱글톤.

    **디스크 영속**(`state/{슬롯}/talk_sessions.json`) — 봇을 껐다 켜도 진행 중인 대화가
    이어진다(멘션 카운트 유지).

    영속화 주의:
        호출측이 `get()`이 돌려준 TalkSession을 직접 수정한다(예: `session.total += 1`).
        그런 수정은 이 클래스가 알 수 없으므로 수정 후 `touch(session_key)`를 불러야 한다.
    """

    def __init__(self, store: Optional[JsonStore] = None) -> None:
        # `store or ...` 금지: JsonStore는 __len__이 있어 **빈 저장소가 falsy**다.
        self._store = store if store is not None else JsonStore(
            slot_path('talk_sessions.json'))
        self._sessions: Dict[str, TalkSession] = {}
        for key, data in self._store.items().items():
            if isinstance(data, dict):
                try:
                    self._sessions[str(key)] = TalkSession.from_dict(data)
                except Exception as e:  # 깨진 항목 하나가 기동을 막지 않게
                    logger.warning(f"[대화] 세션 복원 실패(건너뜀) key={key}: {e}")
        self._lock = threading.Lock()
        if self._sessions:
            logger.info(f"[대화] 비밀 대화 세션 {len(self._sessions)}건 복원")

    def _persist_locked(self, session_key: str) -> None:
        session = self._sessions.get(str(session_key))
        if session is not None:
            self._store.set(str(session_key), session.to_dict())

    def touch(self, session_key: str) -> None:
        """호출측이 세션을 직접 수정한 뒤 저장을 요청한다."""
        with self._lock:
            self._persist_locked(session_key)

    def get(self, session_key: str) -> Optional[TalkSession]:
        with self._lock:
            return self._sessions.get(str(session_key))

    def start(self, session_key: str, session: TalkSession) -> None:
        with self._lock:
            self._sessions[str(session_key)] = session
            self._persist_locked(session_key)

    def clear(self, session_key: str) -> Optional[TalkSession]:
        with self._lock:
            removed = self._sessions.pop(str(session_key), None)
        self._store.delete(str(session_key))
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


def _session_key_from_context(context: 'CommandContext') -> Optional[str]:
    """핸들러가 답글-스레드로 라우팅한 경우 실린 session_key를 꺼낸다."""
    entry = context.get_metadata('reply_thread')
    if isinstance(entry, dict):
        key = entry.get('session_key')
        return str(key) if key is not None else None
    return None


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
            session_key = _session_key_from_context(context)
            if session_key:
                session = get_talk_session_manager().get(session_key)
                if session is not None:
                    return self._continue_session(context, session)
                # 라우팅됐지만 세션이 이미 종료됨 → 조용히 무시(빈 성공 = 전송 생략)
                return CommandResponse.create_success("")
            return self._start_session(context)
        except Exception as e:  # 방어적 처리
            logger.error(f"[대화] 실행 중 오류: {e}", exc_info=True)
            return CommandResponse.create_error("대화 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.")

    # ------------------------------------------------------------------
    # 세션 시작
    # ------------------------------------------------------------------
    def _start_session(self, context: CommandContext) -> CommandResponse:
        # 1) 대상 해석 (멘션 기반). @STORY(봇 자신)·자기 자신은 target_helpers가 제외한다.
        target = resolve_target(context, self.sheets_manager, keyword_index=None)
        if not target:
            reason = get_target_error(context, "대화 상대를 지정해 주세요. 상대를 멘션(@)해야 합니다.")
            return CommandResponse.create_error(reason)

        partner_name = str(target.get('이름', '')).strip()
        partner_acct = str(target.get('아이디', '')).strip()
        initiator_acct = context.user_id
        initiator_name = self._actor_name(context)

        # 2) 연속일 같은 상대 금지 (일일 제한 소모 전에 검사). 시작자↔시작 상대만 대상.
        yesterday_partners = self._parse_partners(
            game_state.get(initiator_acct, _KEY_YESTERDAY_PARTNER, '')
        )
        if any(self._same_name(y, partner_name) for y in yesterday_partners):
            return CommandResponse.create_error(
                f"어제 {add_eul_reul_safe(partner_name)} 만났습니다. "
                "같은 상대와는 연이어 대화할 수 없습니다."
            )

        # 3) 하루 1회 세션 시작 제한 (시작자만 소모)
        if not game_state.check_and_set(initiator_acct, _KEY_TODAY_TALKED, 1):
            return CommandResponse.create_error("오늘은 이미 비밀 대화를 나눴습니다. 내일 다시 시도해 주세요.")

        # 4) 세션 생성 + 첫 멘션 카운트(=1)
        session_key = str(context.get_metadata('status_id') or f"{initiator_acct}:{partner_acct}")
        session = TalkSession(
            session_key=session_key,
            initiator_name=initiator_name,
            initiator_acct=initiator_acct,
            partner_name=partner_name,
            partner_acct=partner_acct,
            total=1,
        )
        get_talk_session_manager().start(session_key, session)

        # 5) 오늘대화상대 기록 (연속일 판정용, 스케줄러가 00:00 이월) — 시작 상대만
        self._record_partner_today(initiator_acct, partner_name)

        # 6) 타래 라우팅 준비: 방금 들어온 툿(시작자 원문)과 곧 나갈 안내 답글을 세션에 연결
        self._link_incoming(context, session)
        self._stage_bot_reply(session)

        # 시작 직후 캡 도달(총 1회로 캡이 1이하인 극단 설정)이면 즉시 종료
        if session.total >= _total_cap():
            return self._end_session(context, session)

        wa = _wa_gwa(partner_name)
        msg = (
            f"으슥한 곳에서 {partner_name}{wa}의 비밀 대화가 시작됩니다.\n"
            "추가로 타래에 명령어를 쓸 필요는 없습니다. 상대와 이야기 계정을 모두 태그한 채로 "
            "이 답글에 답하며 대화를 이어가세요."
        )
        return CommandResponse.create_success(msg)

    # ------------------------------------------------------------------
    # 세션 이어가기 (참여자 누구든, [대화] 없이도)
    # ------------------------------------------------------------------
    def _continue_session(self, context: CommandContext, session: TalkSession) -> CommandResponse:
        total_cap = _total_cap()

        # 공유 카운트 증가
        session.total += 1
        get_talk_session_manager().touch(session.session_key)

        # 캡 도달 시 세션 종료 + 로그
        if session.total >= total_cap:
            return self._end_session(context, session)

        # 중간 턴: 봇은 침묵(빈 성공 = 전송 생략). 다음 답글이 라우팅되도록 이 툿을 세션에 링크.
        self._link_incoming(context, session)
        return CommandResponse.create_success("")

    # ------------------------------------------------------------------
    # 세션 종료 (로그 + 안내)
    # ------------------------------------------------------------------
    def _end_session(self, context: CommandContext, session: TalkSession) -> CommandResponse:
        get_talk_session_manager().clear(session.session_key)
        # 더 이상 이 타래를 잇지 않는다(대기 중 예약 폐기).
        try:
            reply_threads.discard(context.user_id)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[대화] reply_threads.discard 실패: {e}")

        actor = session.initiator_name
        partner = session.partner_name
        # 대화 사실을 행동로그에 요약 기록 (docs 시너몬트_구현계획 §5.7)
        try:
            # '와/과'는 **앞 단어(actor)** 를 보고 정한다. 상대 이름으로 계산하면
            # '휴고과 원쥔'처럼 어긋난다(2026-07-16까지 그랬다).
            summary = f"{actor}{_wa_gwa(actor)} {partner} 비밀 대화를 나눴다"
            action_log.append(
                self.system_sheets_manager,
                kind=action_log.KIND_TALK,
                actor=actor,
                target=partner,
                summary=summary,
            )
        except Exception as e:
            logger.warning(f"[대화] 행동로그 기록 실패: {e}")

        wa = _wa_gwa(partner)
        msg = (
            f"{actor}{_wa_gwa(actor)} {partner}{wa}의 밀담이 끝났습니다.\n"
            "(비밀 대화가 종료되었습니다.)"
        )
        return CommandResponse.create_success(msg)

    # ------------------------------------------------------------------
    # 타래 라우팅 보조
    # ------------------------------------------------------------------
    def _link_incoming(self, context: CommandContext, session: TalkSession) -> None:
        """방금 들어온 툿(context.status_id)을 세션에 즉시 매핑해, 다음 답글이 이어지게 한다."""
        status_id = context.get_metadata('status_id')
        if status_id is None:
            return
        try:
            reply_threads.link(
                status_id, _THREAD_KEYWORD, context.user_id,
                session.session_key, participants=session.participants(),
            )
        except Exception as e:  # noqa: BLE001 - 링크 실패는 이어가기만 끊길 뿐
            logger.debug(f"[대화] reply_threads.link 실패: {e}")

    def _stage_bot_reply(self, session: TalkSession) -> None:
        """곧 나갈 봇 답글(시작 안내)을 세션에 등록하도록 예약한다.

        핸들러가 전송 직후 commit(시작자 acct → 답글 id)으로 확정한다.
        """
        try:
            reply_threads.stage(
                session.initiator_acct, _THREAD_KEYWORD, session.session_key,
                participants=session.participants(),
            )
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[대화] reply_threads.stage 실패: {e}")

    # ------------------------------------------------------------------
    # 보조
    # ------------------------------------------------------------------
    @staticmethod
    def _same_name(a: str, b: str) -> bool:
        return (a or '').strip().replace(' ', '').lower() == (b or '').strip().replace(' ', '').lower()

    @staticmethod
    def _parse_partners(raw: Any) -> List[str]:
        """오늘/어제대화상대 값(쉼표 구분 목록)을 이름 리스트로 파싱."""
        return [n for n in (p.strip() for p in str(raw or '').split(',')) if n]

    def _record_partner_today(self, user_id: str, name: str) -> None:
        """오늘 시작한 상대를 오늘대화상대 목록(쉼표 구분)에 누적 기록(중복 제외)."""
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

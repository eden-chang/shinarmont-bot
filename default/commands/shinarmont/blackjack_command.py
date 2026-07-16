"""
블랙잭 명령어 (도박, @BAR)

게임 세션(스레드 안전, 디스크 영속) + 답글/후속 명령으로 히트/스탠드를 진행한다.
- `[블랙잭/베팅]` : 새 게임 시작(카드 딜)
- `[히트]`        : 카드 한 장 추가
- `[스탠드]`      : 딜러가 17 이상까지 히트 후 정산
- `[더블다운]`    : (첫 턴) 베팅 2배 + 카드 한 장만 받고 종료
- `[서렌더]`      : (첫 턴) 판을 접고 베팅 절반 환불

딜러는 NPC이며 사람이 아니라 규칙이다 — 16 이하 히트 / 17 이상 스탠드 /
소프트 17은 `BLACKJACK_HIT_ON_SOFT_17`(기본 H17)에 따른다. 1인용 게임이다.

세션은 `user_id` 키로 `state/{슬롯}/blackjack_sessions.json`에 영속화된다.
베팅액은 **딜 시점에 소지금에서 차감**(락+batch)하고, 정산 시 배당을 지급(락+batch)한다.
차감이 먼저이므로 세션이 사라지면 **베팅액이 그대로 유실**된다 —
봇 재시작(업데이트)을 사이에 두고도 판을 이어갈 수 있어야 하는 이유다.
공개 명령어이므로 DM 전용이 아니다(@dm_only 미적용).

docs/코딩_계획.md §6-D, docs/시너몬트_구현계획.md §5.13 도박 스펙을 따른다.
"""

import os
import sys
import random
import threading
from typing import Any, Dict, List, Optional, Tuple

# 경로 설정 (VM 환경 대응)
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from utils.imports import *  # noqa: F401,F403  (config, logger, CommandError, BaseCommand 등)

from utils.json_store import JsonStore, slot_path
from utils.investigation_sheet import config_int
from utils import game_state

try:
    from utils.lock_manager import get_lock_manager
except ImportError:  # 폴백 (부분 환경)
    get_lock_manager = None


# ── 카드/핸드 유틸 ──────────────────────────────────────────────

_SUITS = ['♠', '♥', '♦', '♣']
_RANKS = ['A', '2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K']

# 셔플 RNG. `random.shuffle`은 **전역 시드를 공유하는** Mersenne Twister라,
# 시드가 드러나면 덱 순서 전체가 예측된다(다른 코드가 random.seed()를 부르기만 해도).
# 재화가 오가는 게임이므로 OS 엔트로피를 쓴다. 테스트만 set_rng()로 고정 시드를 넣는다.
_rng: random.Random = random.SystemRandom()


def set_rng(rng: random.Random) -> None:
    """셔플 RNG 교체(테스트 전용). 운영에서는 부르지 않는다."""
    global _rng
    _rng = rng


def _build_deck() -> List[Tuple[str, str]]:
    """표준 52장 덱을 섞어서 반환."""
    deck = [(rank, suit) for suit in _SUITS for rank in _RANKS]
    _rng.shuffle(deck)
    return deck


def _card_value(rank: str) -> int:
    """카드 랭크의 기본 점수(에이스=11)."""
    if rank in ('J', 'Q', 'K'):
        return 10
    if rank == 'A':
        return 11
    return int(rank)


def _hand_value(cards: List[Tuple[str, str]]) -> Tuple[int, bool]:
    """(핸드 총점, 소프트 여부).

    소프트 = 버스트하지 않으면서 **에이스를 아직 11로 쓰고 있는** 상태.
    소프트 17(A+6)은 한 장 더 받아도 버스트하지 않으므로 딜러 규칙이 갈린다.
    """
    total = sum(_card_value(r) for r, _ in cards)
    aces = sum(1 for r, _ in cards if r == 'A')
    reduced = 0
    while total > 21 and reduced < aces:
        total -= 10
        reduced += 1
    return total, (total <= 21 and reduced < aces)


def _hand_total(cards: List[Tuple[str, str]]) -> int:
    """핸드 총점(에이스는 버스트 방지를 위해 필요 시 1로 계산)."""
    return _hand_value(cards)[0]


def _dealer_should_hit(cards: List[Tuple[str, str]]) -> bool:
    """딜러(NPC) 규칙: 16 이하 히트 / 17 이상 스탠드 / 소프트 17은 설정에 따름.

    `BLACKJACK_HIT_ON_SOFT_17`=True(H17, 기본)면 소프트 17에서 한 장 더 받는다.
    False(S17)면 선다 — 이용자에게 약간 유리하다(하우스 엣지 ~0.2%p 차이).
    """
    total, soft = _hand_value(cards)
    if total > 21:
        return False
    if total < 17:
        return True
    if total == 17 and soft:
        return bool(getattr(config, 'BLACKJACK_HIT_ON_SOFT_17', True))
    return False


def _is_blackjack(cards: List[Tuple[str, str]]) -> bool:
    """초기 2장으로 21(내추럴 블랙잭)인지."""
    return len(cards) == 2 and _hand_total(cards) == 21


def _fmt_card(card: Tuple[str, str]) -> str:
    """카드 하나를 '♠A' 형태로. 참조 구현(Card.__str__ = suit+rank)과 같은 순서다."""
    rank, suit = card
    return f"{suit}{rank}"


def _fmt_hand(cards: List[Tuple[str, str]]) -> str:
    """핸드를 '♠A ♥K' 형태로 포맷(참조 솔로 결과 블록과 같은 공백 구분)."""
    return ' '.join(_fmt_card(c) for c in cards)


# 일일 제한 — 슬롯·크랩스와 같은 방식(봇 JSON, 날짜 스탬프로 자정 자동 해제).
# 시트 컬럼을 쓰지 않는다: 딜마다 시트를 읽고 쓰면 Sheets 쿼터를 태운다.
# 판 단위로 센다(딜 1회 = 1회). 히트·스탠드는 세지 않는다.
BLACKJACK_DAILY_KEY = '오늘블랙잭'

# 딜러 히트 상한(안전장치). 규칙상 도달할 수 없지만, 무한 루프는 슬롯을 통째로 멈춘다.
_DEALER_MAX_HITS = 10

# 결과 블록 구분선 (참조 구현과 동일)
_RESULT_RULE = '━' * 21


def _notify_admin_safe(message: str) -> None:
    """관리자 DM. 실패해도 조용히 넘어간다.

    세션 복원은 **봇 기동 경로**다. 알림이 안 된다고 봇이 못 뜨면 본말전도다.
    api를 넘기지 않으면 notify_admin이 전역 dm_sender로 폴백한다.
    """
    try:
        from utils import investigation_notify
        investigation_notify.notify_admin(message, prefix='[블랙잭]')
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[블랙잭] 관리자 알림 실패(무시): {e}")


# ── 세션 상태 관리자 (스레드 안전 싱글톤) ───────────────────────

class _BlackjackSession:
    """단일 사용자 블랙잭 게임 상태."""

    def __init__(self, bet: int, _deal: bool = True) -> None:
        self.bet: int = bet
        # 배당 지급 직전에 세팅하고, 결과를 확인하면 해제한다.
        # 디스크에 이 값이 남은 채 발견되면 = **지급 도중 프로세스가 죽었다**는 뜻.
        self.payout_attempted: Optional[int] = None
        # 서렌더는 카드만 봐서는 알 수 없다(패는 그대로 2장이다).
        # 지급이 실패해 재시도할 때 서렌더가 일반 정산으로 바뀌지 않도록 기록해 둔다.
        self.surrendered: bool = False
        self.deck: List[Tuple[str, str]] = _build_deck() if _deal else []
        self.player: List[Tuple[str, str]] = [self.deck.pop(), self.deck.pop()] if _deal else []
        self.dealer: List[Tuple[str, str]] = [self.deck.pop(), self.deck.pop()] if _deal else []

    def _pop(self) -> Tuple[str, str]:
        """덱에서 한 장. 비었으면 **판에 깔린 카드를 빼고** 다시 섞는다.

        한 판에 52장을 다 쓰는 건 사실상 불가능하지만(최대 ~11장),
        빈 리스트에 pop()하면 IndexError로 판이 통째로 날아간다 — 베팅은 이미 차감된 채로.
        깔린 카드를 제외해야 같은 카드가 두 번 나오지 않는다.
        """
        if not self.deck:
            in_play = set(self.player) | set(self.dealer)
            self.deck = [c for c in _build_deck() if c not in in_play]
            logger.warning("[블랙잭] 덱 소진 → 판에 깔린 카드를 제외하고 재셔플")
        return self.deck.pop()

    def draw_player(self) -> None:
        self.player.append(self._pop())

    def draw_dealer(self) -> None:
        self.dealer.append(self._pop())

    # ── 직렬화 ──
    def to_dict(self) -> Dict[str, Any]:
        return {
            'bet': self.bet,
            'deck': [list(c) for c in self.deck],
            'player': [list(c) for c in self.player],
            'dealer': [list(c) for c in self.dealer],
            'payout_attempted': self.payout_attempted,
            'surrendered': self.surrendered,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> '_BlackjackSession':
        """JSON은 튜플을 리스트로 만든다 → 카드를 튜플로 되돌린다.

        (`_fmt_hand`/`_hand_total`은 언패킹이라 리스트여도 동작하지만,
        타입을 섞어 두면 나중에 `in` 비교나 집합 연산에서 조용히 어긋난다)
        """
        def cards(raw):
            return [(str(c[0]), str(c[1])) for c in (raw or []) if len(c) >= 2]

        session = cls(int(data.get('bet', 0)), _deal=False)
        session.deck = cards(data.get('deck'))
        session.player = cards(data.get('player'))
        session.dealer = cards(data.get('dealer'))
        attempted = data.get('payout_attempted')
        session.payout_attempted = int(attempted) if attempted else None
        session.surrendered = bool(data.get('surrendered'))
        return session


class _SessionManager:
    """user_id -> _BlackjackSession 저장소(스레드 안전, 디스크 영속).

    **왜 영속화하나**: 베팅은 딜 시점에 이미 차감된다(`_charge_bet`).
    세션이 재시작으로 사라지면 플레이어는 돈만 내고 판이 증발한다 —
    재화가 실제로 유실된다. 그래서 판이 바뀔 때마다 디스크에 쓴다.

    호출측이 세션 객체를 직접 고치면(`draw_player()` 등) 이 클래스는 알 수 없으므로
    수정 후 `touch(user_id)`를 불러야 한다.
    """

    def __init__(self, store: Optional['JsonStore'] = None) -> None:
        # `store or ...` 금지: JsonStore는 __len__이 있어 **빈 저장소가 falsy**다.
        self._store = store if store is not None else JsonStore(
            slot_path('blackjack_sessions.json'), max_entries=200)
        self._lock = threading.Lock()
        self._sessions: Dict[str, _BlackjackSession] = {}
        orphans = 0
        for uid, data in self._store.items().items():
            try:
                session = _BlackjackSession.from_dict(data)
            except Exception as e:  # noqa: BLE001
                # 깨진 세션 하나가 봇 기동을 막으면 안 된다. 그 판만 버린다.
                logger.warning(f"[블랙잭] 세션 복원 실패(uid={uid}): {e} → 해당 판 폐기")
                self._store.delete(str(uid))
                continue

            if session.payout_attempted:
                # 배당을 시트에 쓰는 도중 프로세스가 죽었다. 썼는지 안 썼는지 **알 수 없다**
                # (소지금 갱신은 read-modify-write라 멱등하지 않고, 도박용 재화 원장도 없다).
                # 다시 지급하면 재화가 조용히 복제된다 — 되돌릴 수도, 눈치챌 수도 없다.
                # 지급하지 않으면 이용자가 즉시 알아채고 GM이 [소지금 관리]로 보정할 수 있다.
                # **보이는 손실이 조용한 복제보다 낫다** → 판을 접고 크게 남긴다.
                orphans += 1
                detail = (
                    f"user={uid}, 베팅={session.bet}, 지급시도={session.payout_attempted}, "
                    f"내패={_fmt_hand(session.player)}, 딜러패={_fmt_hand(session.dealer)}"
                )
                logger.error(
                    "[블랙잭] 배당 지급 중 중단된 판 발견 — GM 확인 필요: "
                    f"{detail} → 시트 소지금을 확인해 미지급이면 [소지금 관리]로 보정하세요."
                )
                # 로그에만 남기면 아무도 안 본다 → 관리자에게 DM도 보낸다(QA §3).
                _notify_admin_safe(
                    f"⚠️ 블랙잭 배당 지급이 중단된 판을 발견했습니다. 지급 여부를 알 수 없어 "
                    f"**재지급하지 않았습니다**(재화 복제 방지).\n{detail}\n"
                    f"→ '관리' 시트의 소지금을 확인하시고, 미지급이면 [소지금 관리]로 보정해 주세요."
                )
                self._store.delete(str(uid))
                continue

            self._sessions[str(uid)] = session

        if self._sessions:
            logger.info(f"[블랙잭] 진행 중이던 게임 {len(self._sessions)}건 복원")
        if orphans:
            logger.error(f"[블랙잭] 지급 중단 판 {orphans}건 — 위 로그의 user/금액을 확인하세요")

    def get(self, user_id: str) -> Optional[_BlackjackSession]:
        with self._lock:
            return self._sessions.get(str(user_id))

    def start(self, user_id: str, bet: int) -> _BlackjackSession:
        session = _BlackjackSession(bet)
        with self._lock:
            self._sessions[str(user_id)] = session
            self._store.set(str(user_id), session.to_dict())
        return session

    def touch(self, user_id: str) -> None:
        """호출측이 직접 고친 세션을 디스크에 반영한다."""
        with self._lock:
            session = self._sessions.get(str(user_id))
            if session is not None:
                self._store.set(str(user_id), session.to_dict())

    def clear(self, user_id: str) -> None:
        with self._lock:
            self._sessions.pop(str(user_id), None)
            self._store.delete(str(user_id))


_session_manager: Optional[_SessionManager] = None
_manager_lock = threading.Lock()


def get_session_manager() -> _SessionManager:
    """전역 블랙잭 세션 매니저 싱글톤."""
    global _session_manager
    if _session_manager is None:
        with _manager_lock:
            if _session_manager is None:
                _session_manager = _SessionManager()
    return _session_manager


def set_session_manager(manager: Optional[_SessionManager]) -> None:
    """테스트용 주입. 안 그러면 테스트가 프로젝트 `state/`를 오염시킨다."""
    global _session_manager
    with _manager_lock:
        _session_manager = manager


# ── 명령어 ──────────────────────────────────────────────────────

@register_command(
    name="블랙잭",
    aliases=[
        "히트", "스탠드", "스테이", "더블다운", "더블", "서렌더", "항복",
        "blackjack", "bj", "hit", "stand", "stay", "double", "dd", "surrender",
    ],
    description=(
        "딜러와 1:1 블랙잭. 1~100달러, 하루 20판. "
        "[블랙잭/베팅액] 후 [히트]/[스탠드], 첫 턴에는 [더블다운]/[서렌더]도 할 수 있습니다. "
        "내추럴 블랙잭은 2.5배를 돌려받습니다."
    ),
    category="도박",
    examples=["[블랙잭/100]", "[히트]", "[스탠드]", "[더블다운]", "[서렌더]"],
    requires_sheets=True,
    requires_api=False,
)
class BlackjackCommand(BaseCommand):
    """블랙잭 명령어 — 딜러(NPC) vs 플레이어 1인 게임.

    같은 클래스가 `블랙잭`(딜) / `히트` / `스탠드` / `더블다운` / `서렌더`를 모두 처리한다.
    진행 상태는 `get_session_manager()`의 영속 세션으로 분기한다.

    딜러는 사람이 아니라 규칙이다(`_dealer_should_hit`) — 16 이하 히트, 17 이상 스탠드,
    소프트 17은 `BLACKJACK_HIT_ON_SOFT_17` 설정에 따른다.
    """

    def __init__(self, sheets_manager=None, api=None, **kwargs):
        super().__init__(sheets_manager, api, **kwargs)
        self.system_sheets_manager = kwargs.get('system_sheets_manager')

    @staticmethod
    def _refund_daily(user_id: str) -> None:
        """소모한 일일 횟수를 1 되돌린다(판이 성립하지 못했을 때).

        실패해도 조용히 넘어간다 — 롤백 실패로 오류를 또 던지면 원래 실패 원인이 묻힌다.
        """
        try:
            used = int(game_state.get(user_id, BLACKJACK_DAILY_KEY, 0) or 0)
            if used > 0:
                game_state.set(user_id, BLACKJACK_DAILY_KEY, used - 1)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[블랙잭] 일일 횟수 롤백 실패(무시): {e}")

    # ── 진입점 ──
    def execute(self, context: CommandContext) -> CommandResponse:
        try:
            keyword = (context.get_keyword(0, '') or '').strip().lower()
            manager = get_session_manager()

            if keyword in ('히트', 'hit'):
                return self._handle_hit(context, manager)
            if keyword in ('스탠드', '스테이', 'stand', 'stay'):
                return self._handle_stand(context, manager)
            if keyword in ('더블다운', '더블', 'double', 'dd'):
                return self._handle_double(context, manager)
            if keyword in ('서렌더', '항복', 'surrender'):
                return self._handle_surrender(context, manager)
            # 그 외(블랙잭/blackjack/bj) = 새 게임
            return self._handle_deal(context, manager)

        except CommandError as e:
            return CommandResponse.create_error(str(e), error=e)
        except Exception as e:  # noqa: BLE001
            logger.error(f"[블랙잭] 실행 중 오류: {e}", exc_info=True)
            return CommandResponse.create_error("블랙잭 처리 중 오류가 발생했습니다.", error=e)

    # ── 딜(새 게임) ──
    def _handle_deal(self, context: CommandContext, manager: '_SessionManager') -> CommandResponse:
        user_id = context.user_id

        # 이미 진행 중이면 새 게임 거부(현재 판 안내)
        existing = manager.get(user_id)
        if existing is not None:
            return CommandResponse.create_error(
                "이미 진행 중인 블랙잭 게임이 있습니다.\n"
                + self._status_text(existing)
                + "\n[히트] 또는 [스탠드]로 이어서 진행하세요."
            )

        bet = self._parse_bet(context)
        currency = getattr(config, 'CURRENCY', '포인트')

        # 일일 제한 — **차감보다 먼저** 센다.
        # 뒤에 세면 차감은 됐는데 제한에 걸려 돈만 잃는다.
        limit = config_int('BLACKJACK_DAILY_LIMIT', 20)
        if not game_state.check_and_set(user_id, BLACKJACK_DAILY_KEY, limit):
            return CommandResponse.create_error(
                f"오늘은 더 이상 할 수 없습니다. (하루 {limit}판)"
            )

        # 베팅 차감(락 + batch, 잔액 재확인)
        ok, balance = self._charge_bet(user_id, bet)
        if not ok:
            # 판이 성립하지 못했다 → 소모한 횟수를 되돌린다.
            self._refund_daily(user_id)
            return CommandResponse.create_error(
                f"소지금이 부족합니다. 현재 보유: {balance:,}{currency} (베팅 {bet:,}{currency})"
            )

        session = manager.start(user_id, bet)
        player_total = _hand_total(session.player)

        # 내추럴 블랙잭 즉시 판정
        if _is_blackjack(session.player):
            return self._settle(context, manager, session, player_stood=True, natural=True)

        header = (
            f"블랙잭 시작! 베팅 {bet:,}{currency}\n\n"
            f"당신: {_fmt_hand(session.player)} ({player_total})\n"
            f"딜러: {_fmt_card(session.dealer[0])} [?]\n\n"
            f"선택: [히트] [스탠드] [더블다운] [서렌더]"
        )
        return CommandResponse.create_success(header, data={'action': 'deal', 'bet': bet})

    # ── 히트 ──
    def _handle_hit(self, context: CommandContext, manager: '_SessionManager') -> CommandResponse:
        session = manager.get(context.user_id)
        if session is None:
            return CommandResponse.create_error(
                "진행 중인 게임이 없습니다.\n"
                "게임을 시작하려면 [블랙잭/베팅액] 명령어를 사용하세요."
            )

        session.draw_player()
        manager.touch(context.user_id)   # 뽑은 카드를 즉시 저장(정산 전에 죽어도 판 유지)
        player_total = _hand_total(session.player)

        if player_total > 21:  # 버스트 → 패배 정산
            return self._settle(context, manager, session, player_stood=False, busted=True)

        if player_total == 21:  # 자동 스탠드
            return self._settle(context, manager, session, player_stood=True)

        # 참조 솔로 히트 문구와 동일한 형태:
        #   `히트! {새 카드} 추가 → {합계}{(소프트)}` + 선택 안내
        _total, soft = _hand_value(session.player)
        soft_text = " (소프트)" if soft else ""
        msg = (
            f"히트! {_fmt_card(session.player[-1])} 추가 → {player_total}{soft_text}\n"
            f"선택: [히트] [스탠드]"
        )
        return CommandResponse.create_success(msg, data={'action': 'hit', 'total': player_total})

    # ── 스탠드 ──
    def _handle_stand(self, context: CommandContext, manager: '_SessionManager') -> CommandResponse:
        session = manager.get(context.user_id)
        if session is None:
            return CommandResponse.create_error(
                "진행 중인 게임이 없습니다.\n"
                "게임을 시작하려면 [블랙잭/베팅액] 명령어를 사용하세요."
            )
        # natural/surrendered를 카드·세션에서 되살린다. 그래야 지급 실패 후
        # [스탠드]로 재시도해도 **처음과 같은 결과**로 정산된다
        # (안 그러면 내추럴이 평범한 승리로 바뀌어 3:2 보너스가 사라진다).
        return self._settle(
            context, manager, session,
            player_stood=True,
            natural=_is_blackjack(session.player),
        )

    # ── 더블다운 ──
    def _handle_double(self, context: CommandContext, manager: '_SessionManager') -> CommandResponse:
        """베팅을 2배로 올리고 카드 **한 장만** 받은 뒤 강제 종료."""
        user_id = context.user_id
        session = manager.get(user_id)
        if session is None:
            return CommandResponse.create_error(
                "진행 중인 게임이 없습니다.\n"
                "게임을 시작하려면 [블랙잭/베팅액] 명령어를 사용하세요."
            )
        if len(session.player) != 2:
            return CommandResponse.create_error(
                "더블다운은 처음 두 장을 받은 직후에만 할 수 있습니다. [히트] 또는 [스탠드]로 진행하세요."
            )

        currency = getattr(config, 'CURRENCY', '포인트')

        # 베팅액만큼 **추가로** 차감한다.
        ok, balance = self._charge_bet(user_id, session.bet)
        if not ok:
            return CommandResponse.create_error(
                f"더블다운하려면 {session.bet:,}{currency}가 더 필요합니다. "
                f"현재 보유: {balance:,}{currency}"
            )

        # 차감 → 저장 순서를 지킨다. 그 사이에 죽으면 이용자는 추가 베팅을 잃지만(보이는 손실),
        # 반대로 저장을 먼저 하면 차감 없이 2배 배당을 받는다(조용한 재화 복제). 전자가 낫다.
        session.bet *= 2
        session.draw_player()
        manager.touch(user_id)

        # 더블다운은 한 장만 받고 곧장 정산한다. 참조도 여기서 딜러 턴으로 넘어간다.
        # 결과 블록에 카드가 다 드러나므로 중간 문구는 결과 앞에 붙인다.
        drawn = _fmt_card(session.player[-1])
        total = _hand_total(session.player)
        lead = f"더블다운! {drawn} 추가 → {total}"

        if total > 21:
            return self._settle(context, manager, session, player_stood=False,
                                busted=True, lead=lead)
        return self._settle(context, manager, session, player_stood=True, lead=lead)

    # ── 서렌더 ──
    def _handle_surrender(self, context: CommandContext, manager: '_SessionManager') -> CommandResponse:
        """판을 접고 베팅의 절반을 돌려받는다(첫 턴에만)."""
        session = manager.get(context.user_id)
        if session is None:
            return CommandResponse.create_error(
                "진행 중인 게임이 없습니다.\n"
                "게임을 시작하려면 [블랙잭/베팅액] 명령어를 사용하세요."
            )
        if len(session.player) != 2:
            return CommandResponse.create_error(
                "서렌더는 처음 두 장을 받은 직후에만 할 수 있습니다. [히트] 또는 [스탠드]로 진행하세요."
            )
        # 정산 전에 기록한다 — 환불이 실패해 재시도할 때 일반 정산으로 바뀌면 안 된다.
        session.surrendered = True
        manager.touch(context.user_id)
        return self._settle(context, manager, session, player_stood=False, surrendered=True)

    # ── 정산 ──
    def _settle(
        self,
        context: CommandContext,
        manager: '_SessionManager',
        session: '_BlackjackSession',
        player_stood: bool,
        busted: bool = False,
        natural: bool = False,
        surrendered: bool = False,
        lead: str = '',
    ) -> CommandResponse:
        user_id = context.user_id
        bet = session.bet
        player_total = _hand_total(session.player)
        surrendered = surrendered or session.surrendered

        # 딜러 플레이 (버스트/서렌더면 딜러는 더 뽑지 않는다)
        if busted or surrendered:
            dealer_total = _hand_total(session.dealer)
        else:
            # 한 장 받을 때마다 합이 최소 1 오르므로 반드시 17에서 멈춘다.
            # 그래도 상한을 둔다 — 무한 루프는 슬롯 하나를 통째로 멈춘다.
            hits = 0
            while _dealer_should_hit(session.dealer) and hits < _DEALER_MAX_HITS:
                session.draw_dealer()
                hits += 1
            if hits >= _DEALER_MAX_HITS:
                logger.warning(f"[블랙잭] 딜러 히트 상한({_DEALER_MAX_HITS}) 도달 — 강제 종료")
            dealer_total = _hand_total(session.dealer)

        # 결과 판정 → payout(사용자에게 돌려줄 **총 지급액**, 이미 bet 차감됨).
        # 문구·판정 순서는 참조 구현(casino/blackjack/payout.py BlackjackPayout.calculate)과 동일.
        if surrendered:
            outcome, payout = '서렌더', int(bet * 0.5)
        elif busted or player_total > 21:
            outcome, payout = '버스트! 패배', 0
        elif dealer_total > 21:
            outcome, payout = '딜러 버스트! 승리', bet * 2
        elif natural and _is_blackjack(session.dealer):
            outcome, payout = '푸시 (둘 다 블랙잭)', bet
        elif natural:
            outcome, payout = '블랙잭! 승리', int(bet * 2.5)
        elif _is_blackjack(session.dealer):
            outcome, payout = '딜러 블랙잭! 패배', 0
        elif player_total > dealer_total:
            outcome, payout = f'승리 ({player_total} vs {dealer_total})', bet * 2
        elif player_total == dealer_total:
            outcome, payout = f'푸시 ({player_total})', bet
        else:
            outcome, payout = f'패배 ({player_total} vs {dealer_total})', 0

        # 배당을 먼저 지급하고, 성공한 뒤에 세션을 종료한다.
        # (지급 실패 시 세션을 남겨 [스탠드] 재시도로 재정산 가능 — 베팅+배당 동반 유실 방지)
        # 재시도해도 딜러는 이미 17 이상이라 다시 굴리지 않는다 → 같은 결과로 재정산된다.
        currency_ = getattr(config, 'CURRENCY', '포인트')
        new_balance: Optional[int] = None
        if payout > 0:
            # 지급 직전에 표식을 남긴다. 여기서 죽으면 복원 시 이 판을 걸러낸다(중복 배당 방지).
            session.payout_attempted = payout
            manager.touch(user_id)

            paid, new_balance = self._pay(user_id, payout)

            if not paid:
                # _pay가 False = 시트에 **쓰이지 않았다**는 자체 보고(batch_update 실패).
                # 위험 구간을 벗어났으므로 표식을 지운다 → 재시작해도 재시도로 살릴 수 있다.
                session.payout_attempted = None
                manager.touch(user_id)
                logger.error(f"[블랙잭] 배당 지급 실패: user={user_id}, payout={payout}")
                label = '환불' if surrendered else '배당'
                retry_cmd = '[서렌더]' if surrendered else '[스탠드]'
                return CommandResponse.create_error(
                    f"{label}({payout:,}{currency_}) 지급에 실패했습니다. "
                    f"잠시 후 {retry_cmd}로 다시 시도해 주세요."
                )

        # 지급 성공(또는 지급 없음) → 판 종료
        manager.clear(user_id)

        net = payout - bet
        currency = getattr(config, 'CURRENCY', '포인트')

        # 참조 구현의 결과 블록 서식(casino/blackjack/command.py _format_game_end_message).
        # 단위만 시너몬트 것(달러)으로 바꿨다 — 참조는 '칩'이다.
        # '정산'은 **총 지급액**이다(순손익이 아니다). 참조도 payout 을 그대로 찍는다.
        block = [
            _RESULT_RULE,
            "📊 게임 결과",
            "",
            f"당신: {_fmt_hand(session.player)} ({player_total})",
            f"딜러: {_fmt_hand(session.dealer)} ({dealer_total})",
            "",
            f"결과: {outcome}",
            f"정산: {payout:,}{currency}",
            _RESULT_RULE,
        ]
        message = "\n".join(block)
        if lead:
            # 더블다운처럼 결과 직전에 알릴 게 있으면 블록 위에 붙인다.
            message = f"{lead}\n\n{message}"

        return CommandResponse.create_success(
            message,
            data={
                'action': 'settle',
                'outcome': outcome,
                'bet': bet,
                'payout': payout,
                'net': net,
                'balance': new_balance,
                'player_total': player_total,
                'dealer_total': dealer_total,
            },
        )

    # ── 베팅 파싱 ──
    def _parse_bet(self, context: CommandContext) -> int:
        raw = (context.get_keyword(1, '') or '').strip()
        if not raw:
            raise CommandError("사용법: [블랙잭/베팅]  예: [블랙잭/100]")

        currency = getattr(config, 'CURRENCY', '포인트')
        if currency and raw.endswith(currency):
            raw = raw[: -len(currency)].strip()

        raw = raw.replace(',', '')
        if not raw.lstrip('-').isdigit():
            raise CommandError("베팅액은 숫자여야 합니다. 예: [블랙잭/100]")

        bet = int(raw)
        if bet <= 0:
            raise CommandError("베팅액은 1 이상이어야 합니다.")

        bet_max = getattr(config, 'BET_MAX', None)
        if bet_max and bet > bet_max:
            raise CommandError(f"베팅 상한은 {int(bet_max):,}{currency}입니다.")

        return bet

    # ── 소지금 처리 ──
    def _charge_bet(self, user_id: str, bet: int) -> Tuple[bool, int]:
        """베팅액을 차감한다. (성공여부, 잔액) 반환. 잔액 부족 시 차감하지 않음."""
        def _op(current: int) -> Tuple[bool, int]:
            if current < bet:
                return False, current
            return True, current - bet
        return self._update_money(user_id, _op)

    def _pay(self, user_id: str, amount: int) -> Tuple[bool, int]:
        """배당을 지급한다. (성공여부, 잔액) 반환."""
        def _op(current: int) -> Tuple[bool, int]:
            return True, current + amount
        return self._update_money(user_id, _op)

    def _update_money(self, user_id: str, op) -> Tuple[bool, int]:
        """
        관리 시트의 소지금을 락 내부에서 재조회 후 op(current)->(apply, new)로 갱신.

        Returns:
            (성공여부, 갱신 후(또는 현재) 잔액)
        """
        lock_manager = get_lock_manager() if get_lock_manager else None
        if lock_manager is None:
            return self._update_money_inner(user_id, op)

        with lock_manager.acquire_lock(user_id, timeout=10.0) as acquired:
            if not acquired:
                raise CommandError("처리가 지연되고 있습니다. 잠시 후 다시 시도해 주세요.")
            return self._update_money_inner(user_id, op)

    def _update_money_inner(self, user_id: str, op) -> Tuple[bool, int]:
        if not self.sheets_manager:
            raise CommandError("소지금 정보를 확인할 수 없습니다.")

        rows = self.sheets_manager.get_worksheet_data('관리', use_cache=False)
        user_row = None
        for row in rows:
            if str(row.get('아이디', '')).strip() == str(user_id):
                user_row = row
                break
        if user_row is None:
            raise CommandError("명단(관리)에 등록되어 있지 않습니다.")

        header = [k for k in user_row.keys() if k != '_row_number']
        if '소지금' not in header:
            raise CommandError("관리 시트에 '소지금' 컬럼이 없습니다.")

        current = self._parse_money(user_row.get('소지금', 0))
        apply_change, new_value = op(current)
        if not apply_change:
            return False, current

        new_value = max(0, int(new_value))
        row_index = user_row['_row_number']
        money_col = header.index('소지금') + 1
        ok = self.sheets_manager.batch_update_cells('관리', [(row_index, money_col, new_value)])
        if not ok:
            # 시트 쓰기 실패 → 변경되지 않았음을 정확히 보고(무료 플레이/배당 유실 방지)
            logger.warning("[블랙잭] 소지금 batch_update 실패: user=%s", user_id)
            return False, current
        invalidate_user_cache()
        return True, new_value

    @staticmethod
    def _parse_money(value: Any) -> int:
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, str):
            cleaned = value.replace(',', '').strip()
            try:
                return int(float(cleaned))
            except (ValueError, TypeError):
                return 0
        return 0

    @staticmethod
    def _status_text(session: '_BlackjackSession') -> str:
        return (
            f"당신: {_fmt_hand(session.player)} ({_hand_total(session.player)})\n"
            f"딜러: {_fmt_card(session.dealer[0])} [?]"
        )

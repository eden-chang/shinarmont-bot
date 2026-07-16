"""
[크랩스/베팅액] + [주사위 굴림] 명령어 — 시너몬트 도박(@BAR).

**다단계 진행**(2026-07-16 개편). 예전엔 한 번의 명령으로 전부 시뮬레이션하고
결과만 뱉었는데, 그러면 러너가 주사위를 던지는 맛이 없다.

    [크랩스/3]        → 베팅 접수. 아직 굴리지 않는다.
    [주사위 굴림]      → 컴아웃. 7·11 승리 / 2·3·12 패배 / 그 외 포인트 설정
    [주사위 굴림]      → 포인트 재현 시도 (최대 CRAPS_MAX_REROLLS 회)

배당(순이익 기준):
    컴아웃 7·11 즉시 승리   → +베팅 × 2
    포인트 재현 승리        → +베팅 × 1
    패배                   → -베팅

**베팅은 언제 차감하나**: `[크랩스/N]` 시점이 아니라 **판이 끝날 때** 정산한다.
블랙잭은 딜 시점에 차감해서, 세션이 사라지면 베팅액이 그대로 증발했다
(2026-07-16에 영속화로 막았다). 크랩스는 애초에 차감을 미뤄 그 위험을 만들지 않는다.
대신 판이 끝나는 순간 잔액을 **다시 확인**한다(그 사이에 소지금이 줄었을 수 있다).

세션은 `state/{슬롯}/craps_sessions.json`에 영속화 — 봇을 껐다 켜도 굴리던 판이 이어진다.
일일 제한은 봇 JSON(game_state '오늘크랩스'), 자정에 날짜 스탬프로 자동 해제.

공개 명령어이므로 DM 전용(@dm_only) 아님.
"""

import os
import sys
import random
import threading
from typing import Any, Dict, List, Optional, Tuple

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from utils.imports import *  # noqa: F401,F403
from utils.lock_manager import get_lock_manager
from utils.dice_parser import evaluate_amount
from utils.investigation_sheet import config_int
from utils.json_store import JsonStore, slot_path
from utils import game_state


MANAGEMENT_SHEET = '관리'
ID_COLUMN = '아이디'
MONEY_COLUMN = '소지금'

# 일일 제한 — 시트 컬럼이 아니라 봇 JSON. 굴림이 잦아 시트로 세면 쿼터를 태운다.
CRAPS_DAILY_KEY = '오늘크랩스'

# 컴아웃 즉시 판정
COMEOUT_WIN = (7, 11)
COMEOUT_LOSE = (2, 3, 12)

# 순이익 배수
PAYOUT_COMEOUT = 2    # 컴아웃 7·11 → +베팅×2
PAYOUT_POINT = 1      # 포인트 재현 → +베팅×1

# 도박 결과는 재화로 직결된다 → OS 엔트로피(블랙잭·슬롯과 같은 이유).
_rng: random.Random = random.SystemRandom()


def set_rng(rng: random.Random) -> None:
    """주사위 RNG 교체(테스트 전용)."""
    global _rng
    _rng = rng


def roll_dice() -> Tuple[int, int]:
    """주사위 두 개."""
    return _rng.randint(1, 6), _rng.randint(1, 6)


def _max_rerolls() -> int:
    return config_int('CRAPS_MAX_REROLLS', 3)


def _bet_range() -> Tuple[int, int]:
    low = config_int('CRAPS_BET_MIN', 1)
    high = config_int('CRAPS_BET_MAX', 100)
    if low > high:
        logger.warning(f"[크랩스] CRAPS_BET_MIN({low}) > CRAPS_BET_MAX({high}) - 뒤바꿔 적용")
        low, high = high, low
    return low, high


# =====================================================================
# 세션 (베팅 후 굴림 대기 / 포인트 진행)
# =====================================================================
class _CrapsSessionManager:
    """user_id -> 진행 중인 판(디스크 영속).

    베팅을 차감하지 않으므로 세션이 사라져도 재화 손실은 없다.
    그래도 영속화하는 이유: 봇을 껐다 켰다고 굴리던 판이 증발하면
    러너는 "베팅했는데 사라졌다"고 느낀다.
    """

    def __init__(self, store: Optional[JsonStore] = None) -> None:
        # `store or ...` 금지: JsonStore는 __len__이 있어 **빈 저장소가 falsy**다.
        self._store = store if store is not None else JsonStore(
            slot_path('craps_sessions.json'), max_entries=200)
        self._lock = threading.Lock()

    def start(self, user_id: str, bet: int, day: Any = None) -> Dict[str, Any]:
        session = {
            'bet': int(bet),
            'point': None,
            'rerolls_left': _max_rerolls(),
            # 날이 바뀌었는데 어제 판이 남아 오늘을 막지 않도록 시작 일차를 남긴다(QA §2).
            'day': day,
        }
        with self._lock:
            self._store.set(str(user_id), session)
        return session

    def get(self, user_id: str) -> Optional[Dict[str, Any]]:
        raw = self._store.get(str(user_id))
        return dict(raw) if isinstance(raw, dict) else None

    def save(self, user_id: str, session: Dict[str, Any]) -> None:
        with self._lock:
            self._store.set(str(user_id), session)

    def clear(self, user_id: str) -> None:
        with self._lock:
            self._store.delete(str(user_id))


_session_manager: Optional[_CrapsSessionManager] = None
_manager_lock = threading.Lock()


def get_session_manager() -> _CrapsSessionManager:
    global _session_manager
    if _session_manager is None:
        with _manager_lock:
            if _session_manager is None:
                _session_manager = _CrapsSessionManager()
    return _session_manager


def set_session_manager(manager: Optional[_CrapsSessionManager]) -> None:
    """테스트용 주입. 안 그러면 테스트가 프로젝트 state/ 를 오염시킨다."""
    global _session_manager
    with _manager_lock:
        _session_manager = manager


def _current_day() -> Optional[int]:
    """현재 게임 일차. 못 구하면 None(그 경우 일차 검사를 건너뛴다).

    일차를 모른다고 판을 버리면 멀쩡한 판이 날아간다 → 모르면 그냥 둔다.
    """
    try:
        from utils import game_day
        return game_day.current_day()
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[크랩스] 일차 조회 실패(일차 검사 생략): {e}")
        return None


def _parse_money(value: Any) -> int:
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        cleaned = value.replace(',', '').strip()
        if not cleaned:
            return 0
        try:
            return int(float(cleaned))
        except (ValueError, TypeError):
            return 0
    return 0


@register_command(
    name="크랩스",
    aliases=["craps", "주사위 굴림", "주사위굴림", "굴림", "roll"],
    description=(
        "주사위 내기(패스 라인). 1~100달러, 하루 20회. "
        "[크랩스/베팅액]으로 걸고 [주사위 굴림]으로 굴립니다. "
        "컴아웃 7·11이면 베팅액의 2배, 포인트를 재현하면 1배를 법니다."
    ),
    category="도박",
    examples=["[크랩스/10]", "[주사위 굴림]"],
    requires_sheets=True,
    requires_api=False,
)
class CrapsCommand(BaseCommand):
    """패스 라인 크랩스 — [크랩스/베팅액] 으로 시작, [주사위 굴림] 으로 진행.

    같은 클래스가 두 키워드를 모두 처리한다(블랙잭이 히트/스탠드를 함께 받는 것과 같다).
    """

    @staticmethod
    def get_supported_keywords() -> List[str]:
        return ["크랩스", "craps", "주사위 굴림", "주사위굴림", "굴림", "roll"]

    def __init__(self, sheets_manager=None, api=None, **kwargs):
        super().__init__(sheets_manager, api, **kwargs)
        self.system_sheets_manager = kwargs.get('system_sheets_manager')

    # ── 진입점 ──
    def execute(self, context: CommandContext) -> CommandResponse:
        try:
            keyword = (context.get_keyword(0, '') or '').strip().lower()
            if keyword in ('주사위 굴림', '주사위굴림', '굴림', 'roll'):
                return self._handle_roll(context)
            return self._handle_bet(context)
        except CommandError as e:
            return CommandResponse.create_error(str(e), error=e)
        except Exception as e:  # noqa: BLE001
            logger.error(f"[크랩스] 실행 중 오류: {e}", exc_info=True)
            return CommandResponse.create_error("크랩스 처리 중 오류가 발생했습니다.", error=e)

    # ── 1단계: 베팅 접수 ──
    def _handle_bet(self, context: CommandContext) -> CommandResponse:
        currency = getattr(config, 'CURRENCY', '포인트')
        low, high = _bet_range()

        manager = get_session_manager()
        today = _current_day()
        existing = manager.get(context.user_id)

        # 지난 일차의 판은 버린다. 어제 베팅만 하고 굴리지 않았다고 오늘까지 막히면 안 된다.
        if existing is not None and today is not None:
            started = existing.get('day')
            if started is not None and started != today:
                logger.info(
                    f"[크랩스] 지난 일차({started}) 판 폐기 - user={context.user_id}, "
                    f"오늘={today}"
                )
                manager.clear(context.user_id)
                existing = None

        # **굴린 뒤**에는 판을 바꿀 수 없다(포인트를 보고 베팅을 고치면 부정이 된다).
        already_rolled = existing is not None and existing.get('point') is not None
        if already_rolled:
            return CommandResponse.create_error(
                f"이미 굴린 판이 있습니다. (베팅 {existing['bet']:,}{currency} · "
                f"포인트 {existing['point']})\n"
                "[주사위 굴림] 명령어로 이어서 진행하세요."
            )

        bet = self._parse_bet(context.keywords, low, high, currency)

        # 잔액 확인만 한다 — 차감은 판이 끝날 때. 미리 빼면 판이 사라졌을 때 돈만 잃는다.
        row = self._find_user_row(context.user_id)
        if row is None:
            return CommandResponse.create_error(
                "명단에서 정보를 찾을 수 없습니다. 등록 여부를 확인해 주세요."
            )
        balance = _parse_money(row.get(MONEY_COLUMN))
        if balance < bet:
            return CommandResponse.create_error(
                f"소지금이 부족합니다. 현재 보유: {balance:,}{currency} (베팅: {bet:,}{currency})"
            )

        # 일일 제한은 **판을 시작할 때** 센다(굴림마다 세면 한 판에 여러 번 차감된다).
        # 굴리기 전 재베팅은 같은 판의 금액 변경이라 다시 세지 않는다 — 안 그러면
        # 베팅을 잘못 넣은 러너가 고칠 때마다 횟수를 잃는다(QA §2).
        if existing is None:
            limit = config_int('CRAPS_DAILY_LIMIT', 20)
            if not game_state.check_and_set(context.user_id, CRAPS_DAILY_KEY, limit):
                return CommandResponse.create_error(
                    f"오늘은 더 이상 할 수 없습니다. (하루 {limit}회)"
                )

        manager.start(context.user_id, bet, day=today)

        changed = "베팅을 다시 걸었습니다. " if existing is not None else ""
        return CommandResponse.create_success(
            f"{changed}크랩스 - 베팅액 {bet:,}{currency}\n\n"
            "주사위 두 개를 던져주세요. 컴아웃 굴림에서 7 또는 11이 나오면 승리합니다. "
            "2, 3, 12가 나오면 패배합니다. 그 외의 숫자가 나오면 포인트가 설정됩니다.\n\n"
            "[주사위 굴림] 명령어를 통해 주사위를 굴립니다.",
            data={'action': 'bet', 'bet': bet, 'replaced': existing is not None},
        )

    # ── 2단계: 굴림 ──
    def _handle_roll(self, context: CommandContext) -> CommandResponse:
        currency = getattr(config, 'CURRENCY', '포인트')
        manager = get_session_manager()
        session = manager.get(context.user_id)
        if session is None:
            low, _high = _bet_range()
            return CommandResponse.create_error(
                f"진행 중인 판이 없습니다. [크랩스/{low}] 로 베팅부터 해 주세요."
            )

        d1, d2 = roll_dice()
        total = d1 + d2
        roll_line = f"{d1} + {d2} = {total}"

        if session.get('point') is None:
            return self._resolve_comeout(context, manager, session, roll_line, total, currency)
        return self._resolve_point(context, manager, session, roll_line, total, currency)

    def _resolve_comeout(self, context, manager, session, roll_line, total, currency):
        bet = int(session['bet'])

        if total in COMEOUT_WIN:
            return self._settle(context, manager, bet, PAYOUT_COMEOUT, currency,
                                header="컴아웃 굴림", roll_line=roll_line,
                                verdict=f"{total}! 컴아웃에서 바로 승리했습니다!")
        if total in COMEOUT_LOSE:
            return self._settle(context, manager, bet, -1, currency,
                                header="컴아웃 굴림", roll_line=roll_line,
                                verdict=f"{total}. 크랩스입니다. 패배했습니다.")

        # 포인트 설정
        session['point'] = total
        manager.save(context.user_id, session)
        left = int(session['rerolls_left'])
        return CommandResponse.create_success(
            f"컴아웃 굴림\n\n{roll_line}\n\n"
            f"포인트가 {total}으로 설정되었습니다. {left}회 내로 {total}을 다시 굴리면 승리합니다. "
            "7이 나오면 패배합니다. [주사위 굴림] 명령어를 통해 주사위를 다시 굴립니다.",
            data={'action': 'point_set', 'point': total, 'rerolls_left': left},
        )

    def _resolve_point(self, context, manager, session, roll_line, total, currency):
        bet = int(session['bet'])
        point = int(session['point'])

        if total == point:
            return self._settle(context, manager, bet, PAYOUT_POINT, currency,
                                header="포인트 재현", roll_line=roll_line,
                                verdict=f"포인트 {point} 재현 성공! 승리했습니다!")
        if total == 7:
            return self._settle(context, manager, bet, -1, currency,
                                header="포인트 재현", roll_line=roll_line,
                                verdict="7이 나왔습니다. 패배했습니다.")

        left = int(session['rerolls_left']) - 1
        if left <= 0:
            # 재굴림을 다 썼는데 포인트도 7도 안 나왔다 → 패배.
            # (실제 크랩스는 무한히 굴리지만, 여기선 한 판이 끝나야 하루 제한이 의미를 갖는다)
            return self._settle(context, manager, bet, -1, currency,
                                header="포인트 재현", roll_line=roll_line,
                                verdict=f"재굴림을 모두 썼습니다. 포인트 {point}을(를) "
                                        f"재현하지 못해 패배했습니다.")

        session['rerolls_left'] = left
        manager.save(context.user_id, session)
        return CommandResponse.create_success(
            f"포인트 재현\n\n{roll_line}\n\n"
            f"포인트 재현에 실패했습니다. 남은 재굴림 횟수는 {left}회, 7이 나오면 패배합니다. "
            "[주사위 굴림] 명령어를 통해 주사위를 다시 굴립니다.",
            data={'action': 'reroll', 'point': point, 'rerolls_left': left},
        )

    # ── 정산 ──
    def _settle(self, context, manager, bet, profit_multiplier, currency,
                header, roll_line, verdict) -> CommandResponse:
        """판을 끝내고 소지금을 갱신한다. profit_multiplier 는 **순이익** 배수(패배는 -1).

        세션은 시트 갱신이 성공한 뒤에 지운다. 먼저 지우면 갱신이 실패했을 때
        판이 사라져 러너가 결과를 못 받는다.
        """
        delta = bet * profit_multiplier

        lock_manager = get_lock_manager()
        with lock_manager.acquire_lock(str(context.user_id), timeout=10.0) as acquired:
            if not acquired:
                return CommandResponse.create_error(
                    "다른 처리가 진행 중입니다. 잠시 후 다시 시도해 주세요."
                )

            # 락 내부 재조회 — 굴리는 사이에 소지금이 바뀌었을 수 있다.
            row = self._find_user_row(context.user_id)
            if row is None:
                return CommandResponse.create_error("명단에서 정보를 찾을 수 없습니다.")
            balance = _parse_money(row.get(MONEY_COLUMN))

            if delta < 0 and balance < bet:
                # 굴리는 동안 돈이 빠져나갔다 → 있는 만큼만 잃는다(음수 잔액 방지).
                logger.warning(
                    f"[크랩스] 정산 시 잔액 부족: user={context.user_id}, "
                    f"잔액={balance}, 베팅={bet} - 잔액만큼만 차감"
                )
                delta = -balance

            row_number = row.get('_row_number')
            money_col = self._money_column()
            if not row_number or not money_col:
                return CommandResponse.create_error(
                    "소지금 정보를 갱신할 수 없습니다. 잠시 후 다시 시도해 주세요."
                )

            new_balance = balance + delta
            ok = self.sheets_manager.batch_update_cells(
                MANAGEMENT_SHEET, [(row_number, money_col, new_balance)]
            )
            if not ok:
                return CommandResponse.create_error(
                    "정산 처리에 실패했습니다. 잠시 후 다시 시도해 주세요."
                )

        manager.clear(context.user_id)
        try:
            invalidate_user_cache()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[크랩스] 캐시 무효화 실패: {e}")

        sign = '+' if delta >= 0 else ''
        message = (
            f"{header}\n\n{roll_line}\n\n{verdict}\n\n"
            f"➭ 순이익 {sign}{delta:,}{currency}\n"
            f"➭ 현재 소지금 {new_balance:,}{currency}"
        )
        logger.info(
            f"[크랩스] user={context.user_id}, 베팅={bet}, 순이익={delta}, 잔액={new_balance}"
        )
        return CommandResponse.create_success(message, data={
            'action': 'settle', 'bet': bet, 'net': delta, 'balance': new_balance,
        })

    # ── 헬퍼 ──
    def _parse_bet(self, keywords: List[str], low: int, high: int, currency: str) -> int:
        usage = f"베팅할 {currency}를 입력하세요. [크랩스/{max(low, 10)}]"
        if len(keywords) < 2 or not str(keywords[1]).strip():
            raise CommandError(usage)
        try:
            bet, _ = evaluate_amount(str(keywords[1]).strip())
        except (ValueError, TypeError):
            raise CommandError(usage)
        if bet < low:
            raise CommandError(f"최소 베팅액은 {low}{currency}입니다.")
        if bet > high:
            raise CommandError(f"최대 베팅액은 {high}{currency}입니다.")
        return bet

    def _find_user_row(self, user_id: str) -> Optional[Dict[str, Any]]:
        try:
            data = self.sheets_manager.get_worksheet_data(MANAGEMENT_SHEET, use_cache=False)
        except Exception as e:  # noqa: BLE001
            logger.error(f"[크랩스] 관리 시트 조회 실패: {e}")
            return None
        target = str(user_id).strip()
        for row in data or []:
            if str(row.get(ID_COLUMN, '')).strip() == target:
                return row
        return None

    def _money_column(self) -> Optional[int]:
        try:
            header = self.sheets_manager.get_worksheet(MANAGEMENT_SHEET).row_values(1)
        except Exception as e:  # noqa: BLE001
            logger.error(f"[크랩스] 관리 시트 헤더 조회 실패: {e}")
            return None
        try:
            return header.index(MONEY_COLUMN) + 1
        except ValueError:
            logger.warning(f"[크랩스] 관리 시트에 '{MONEY_COLUMN}' 컬럼 없음")
            return None

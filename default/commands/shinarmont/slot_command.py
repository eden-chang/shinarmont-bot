"""
[슬롯머신/베팅액] 명령어 — 시너몬트 도박(@BAR).

3릴 슬롯. 심볼은 등급이 있고, 등급이 높을수록 덜 나오고 더 준다.

심볼·확률(가중치 합 100 → 1~100 난수 하나로 구간을 가른다):
  ▲ △  최하  각 25%   (합 50%)
  ■ □  중간  각 20%   (합 40%)
  ● ○  최상  각  5%   (합 10%)

배당(베팅액 배수):
  ●●● / ○○○   100배   0.025%
  ■■■ / □□□    20배   1.6%
  ▲▲▲ / △△△    10배   3.13%
  ●● / ○○ 2개   1.5배  1.43%
  ■■ / □□ 2개   0.7배  19.2%
  ▲▲ / △△ 2개   0.3배  28.1%
  그 외          0배   46.5%
  → RTP 약 89.8%

2개 일치를 등급별로 나눈 이유: 전부 같은 배율이면 `●●▲`와 `▲▲●`가 같은 값이라
심볼 등급이 체감되지 않는다. (설계 근거는 운영진 결정 2026-07-16)

정산: `-베팅 + (베팅 × 배수)`. 락 획득 → 락 내부 재조회(stale 방지)
→ `batch_update_cells`로 소지금 원자적 갱신 → 캐시 무효화.

공개 명령어이므로 DM 전용(@dm_only) 아님. `docs/시너몬트_구현계획.md` §5.13,
`docs/명령어_개요.md` @BAR, `docs/코딩_계획.md` §6-D 참조.
"""

import os
import sys
import math
import random
from typing import Any, Dict, List, Optional, Tuple

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from utils.imports import *  # noqa: F401,F403
from utils.lock_manager import get_lock_manager
from utils.dice_parser import evaluate_amount
from utils.investigation_sheet import config_int
from utils import game_state


MANAGEMENT_SHEET = '관리'
ID_COLUMN = '아이디'
MONEY_COLUMN = '소지금'

# 심볼 등급. 값이 클수록 상위.
TIER_LOW = 0     # ▲ △
TIER_MID = 1     # ■ □
TIER_HIGH = 2    # ● ○

# 심볼 → (등급, 가중치). 가중치 합 = 100.
# 게임 규칙이라 자주 바뀌지 않는다 → 코드 상수로 둔다(운영진 결정 2026-07-16).
# 자주 만지는 값(베팅 한도·일일 제한)만 .env로 뺐다.
SYMBOLS: Dict[str, Tuple[int, int]] = {
    '▲': (TIER_LOW, 25),
    '△': (TIER_LOW, 25),
    '■': (TIER_MID, 20),
    '□': (TIER_MID, 20),
    '●': (TIER_HIGH, 5),
    '○': (TIER_HIGH, 5),
}

# 등급 이름 (응답 문구용)
TIER_NAMES = {TIER_LOW: '세모', TIER_MID: '네모', TIER_HIGH: '동그라미'}

# 3개 일치 배당 (등급별)
PAYOUT_TRIPLE = {TIER_LOW: 10.0, TIER_MID: 20.0, TIER_HIGH: 100.0}

# 2개 일치 배당 (등급별). 등급이 흐려지지 않도록 나눴다.
PAYOUT_DOUBLE = {TIER_LOW: 0.3, TIER_MID: 0.7, TIER_HIGH: 1.5}

_REEL_COUNT = 3

# 슬롯 일일 제한은 **봇 JSON**(game_state)으로 센다. 시트 컬럼을 만들지 않는다:
#   · 스핀은 빈번한데 시트 카운터는 스핀마다 읽기+쓰기 → Sheets 쿼터를 태운다.
#   · `오늘*` 키는 날짜 스탬프라 자정이 지나면 저절로 풀린다(리셋 잡이 필요 없다).
#   · 슬롯머신은 @BAR 한 슬롯에서만 돌아서 프로세스 간 경합이 없다.
SLOT_DAILY_KEY = '오늘슬롯' 

# 도박은 결과가 재화로 직결된다. `random`은 전역 시드를 공유하는 Mersenne Twister라
# 시드가 드러나면 결과가 예측된다 → OS 엔트로피를 쓴다(블랙잭과 같은 이유).
_rng: random.Random = random.SystemRandom()


def set_rng(rng: random.Random) -> None:
    """스핀 RNG 교체(테스트 전용). 운영에서는 부르지 않는다."""
    global _rng
    _rng = rng


def _weighted_symbol() -> str:
    """가중치대로 심볼 하나. 가중치 합이 100이라 1~100 난수 하나면 된다."""
    roll = _rng.randint(1, 100)
    upto = 0
    for symbol, (_tier, weight) in SYMBOLS.items():
        upto += weight
        if roll <= upto:
            return symbol
    # 가중치 합이 100이면 도달할 수 없다. 합을 잘못 고쳤을 때의 안전망.
    logger.error(f"[슬롯머신] 가중치 합이 100이 아니다(roll={roll}, upto={upto}) - 마지막 심볼 반환")
    return list(SYMBOLS)[-1]


def _fmt_mult(multiplier: float) -> str:
    """배수 표기. 10.0 → '10', 1.5 → '1.5' (소수점이 필요할 때만 붙인다)."""
    return str(int(multiplier)) if float(multiplier).is_integer() else str(multiplier)


def spin_reels(rng: Optional[random.Random] = None) -> List[str]:
    """3릴을 가중치대로 돌려 심볼 리스트를 반환."""
    if rng is not None:
        # 테스트가 로컬 rng를 넘기는 경로(전역을 건드리지 않는다).
        saved = _rng
        try:
            set_rng(rng)
            return [_weighted_symbol() for _ in range(_REEL_COUNT)]
        finally:
            set_rng(saved)
    return [_weighted_symbol() for _ in range(_REEL_COUNT)]


def evaluate(reels: List[str]) -> Tuple[float, Optional[int], int]:
    """릴 결과 판정. 반환 (배수, 맞은 심볼의 등급 or None, 맞은 개수).

    맞은 개수는 3(잭팟) / 2(부분) / 0(꽝).
    """
    counts: Dict[str, int] = {}
    for r in reels:
        counts[r] = counts.get(r, 0) + 1

    best_symbol, best_count = max(counts.items(), key=lambda kv: kv[1])
    tier = SYMBOLS.get(best_symbol, (TIER_LOW, 0))[0]

    if best_count >= _REEL_COUNT:
        return PAYOUT_TRIPLE[tier], tier, _REEL_COUNT
    if best_count == 2:
        return PAYOUT_DOUBLE[tier], tier, 2
    return 0.0, None, 0


def payout_multiplier(reels: List[str]) -> float:
    """릴 결과에 대한 배당 배수(꽝=0). 하위 호환용 얇은 래퍼."""
    return evaluate(reels)[0]


def _parse_money(raw: Any) -> int:
    """소지금 셀 값을 정수로 파싱(쉼표/실수/빈 값 안전)."""
    if isinstance(raw, (int, float)):
        return int(raw)
    if isinstance(raw, str):
        cleaned = raw.replace(',', '').strip()
        if not cleaned:
            return 0
        try:
            return int(float(cleaned))
        except (ValueError, TypeError):
            return 0
    return 0


@register_command(
    name="슬롯머신",
    aliases=["슬롯", "slot", "slots"],
    description=(
        "3~10달러를 걸고 슬롯을 돌립니다. 하루 20회. "
        "심볼 ● ○(희귀) · ■ □ · ▲ △ — 3개 일치 시 최대 100배."
    ),
    category="도박",
    examples=["[슬롯머신/3]", "[슬롯머신/10]"],
    requires_sheets=True,
    requires_api=False,
)
class SlotCommand(BaseCommand):
    """슬롯머신 도박 명령어(공개)."""

    def __init__(self, sheets_manager=None, api=None, **kwargs):
        super().__init__(sheets_manager, api, **kwargs)
        self.system_sheets_manager = kwargs.get('system_sheets_manager')

    # ── 소지금 조회/열 탐색 헬퍼 ──

    def _find_row(self, user_id: str) -> Optional[dict]:
        """관리 시트에서 사용자 행(dict, `_row_number` 포함) 조회."""
        try:
            data = self.sheets_manager.get_worksheet_data(MANAGEMENT_SHEET, use_cache=False)
        except Exception as e:
            logger.error(f"[슬롯머신] 관리 시트 조회 실패: {e}")
            return None
        for row in data or []:
            if str(row.get(ID_COLUMN, '')).strip() == str(user_id).strip():
                return row
        return None

    def _money_column(self) -> Optional[int]:
        """관리 시트 헤더에서 소지금 컬럼의 1-indexed 열 번호."""
        try:
            header = self.sheets_manager.get_worksheet(MANAGEMENT_SHEET).row_values(1)
        except Exception as e:
            logger.error(f"[슬롯머신] 관리 시트 헤더 조회 실패: {e}")
            return None
        try:
            return header.index(MONEY_COLUMN) + 1
        except ValueError:
            logger.warning(f"[슬롯머신] 관리 시트에 '{MONEY_COLUMN}' 컬럼 없음")
            return None

    # ── 베팅 파싱 ──

    @staticmethod
    def _bet_range() -> Tuple[int, int]:
        """(최소, 최대) 베팅액. 슬롯 전용 상한이며 BET_MAX와 무관하다.

        원 잭팟이 100배라 상한을 크게 두면 한 번에 경제가 흔들린다
        (상한 10달러 → 최대 당첨 1,000달러).
        """
        low = config_int('SLOT_BET_MIN', 3)
        high = config_int('SLOT_BET_MAX', 10)
        if low > high:   # 설정이 뒤집혀 있으면 아무도 못 건다 → 로그를 남기고 되돌린다
            logger.warning(f"[슬롯머신] SLOT_BET_MIN({low}) > SLOT_BET_MAX({high}) - 뒤바꿔 적용")
            low, high = high, low
        return low, high

    def _parse_bet(self, keywords: List[str]) -> int:
        """keywords[1]에서 베팅액을 파싱(정수 또는 다이스 표현식).

        Raises:
            CommandError: 형식 오류 / 범위 밖.
        """
        low, high = self._bet_range()
        currency = getattr(config, 'CURRENCY', '포인트')
        usage = f"베팅할 {currency}를 입력하시기 바랍니다. [슬롯머신/{low}]"

        if len(keywords) < 2 or not str(keywords[1]).strip():
            raise CommandError(usage)
        raw = str(keywords[1]).strip()
        try:
            bet, _ = evaluate_amount(raw)
        except (ValueError, TypeError):
            raise CommandError(usage)
        if bet < low:
            raise CommandError(f"최소 베팅액은 {low}{currency}입니다.")
        if bet > high:
            raise CommandError(f"최대 베팅액은 {high}{currency}입니다.")
        return bet

    @staticmethod
    def _refund_spin(user_id: str) -> None:
        """소모한 일일 횟수를 1 되돌린다(스핀이 성립하지 못했을 때).

        실패해도 조용히 넘어간다 — 롤백 실패로 이용자에게 오류를 또 던지면
        정작 원래 실패 원인이 묻힌다. 최악이라도 한 판을 손해 볼 뿐이다.
        """
        try:
            used = int(game_state.get(user_id, SLOT_DAILY_KEY, 0) or 0)
            if used > 0:
                game_state.set(user_id, SLOT_DAILY_KEY, used - 1)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[슬롯머신] 일일 횟수 롤백 실패(무시): {e}")

    def execute(self, context: CommandContext) -> CommandResponse:
        try:
            currency = getattr(config, 'CURRENCY', '포인트')

            # 1) 파싱 (범위 검증 포함 — 슬롯 전용 한도)
            bet = self._parse_bet(context.keywords)

            # 2) 사용자/소지금 사전 조회
            user_row = self._find_row(context.user_id)
            if user_row is None:
                return CommandResponse.create_error(
                    "명단에서 정보를 찾을 수 없습니다. 등록 여부를 확인해 주세요."
                )
            current_money = _parse_money(user_row.get(MONEY_COLUMN))
            if current_money < bet:
                return CommandResponse.create_error(
                    f"소지금이 부족합니다. 현재 보유: {current_money:,}{currency} "
                    f"(베팅: {bet:,}{currency})"
                )

            # 3) 일일 제한 (봇 JSON, 자정에 날짜 스탬프로 자동 해제)
            #    소진 순서 주의: 여기서 +1 해 두고, 아래 정산이 성립하지 못하면 되돌린다.
            #    반대로 정산 뒤에 세면 동시 요청이 한도를 넘길 수 있다.
            limit = config_int('SLOT_DAILY_LIMIT', 20)
            if not game_state.check_and_set(context.user_id, SLOT_DAILY_KEY, limit):
                return CommandResponse.create_error(
                    f"오늘은 더 이상 돌릴 수 없습니다. (하루 {limit}회)"
                )

            # 여기서부터 이탈 경로가 여럿이라, 성립하지 못하면 **횟수를 되돌린다**.
            # 각 return 앞에 롤백을 흩뿌리면 언젠가 하나를 빠뜨린다 → finally 한 곳에서.
            settled = False
            try:
                # 4) 락 + 재조회 + 정산
                lock_manager = get_lock_manager()
                with lock_manager.acquire_lock(str(context.user_id), timeout=10.0) as acquired:
                    if not acquired:
                        return CommandResponse.create_error(
                            "다른 처리가 진행 중입니다. 잠시 후 다시 시도해 주세요."
                        )

                    # 락 내부 재조회(stale 방지)
                    user_row = self._find_row(context.user_id)
                    if user_row is None:
                        return CommandResponse.create_error(
                            "명단에서 정보를 찾을 수 없습니다."
                        )
                    current_money = _parse_money(user_row.get(MONEY_COLUMN))
                    if current_money < bet:
                        return CommandResponse.create_error(
                            f"소지금이 부족합니다. 현재 보유: {current_money:,}{currency}"
                        )

                    row_number = user_row.get('_row_number')
                    money_col = self._money_column()
                    if not row_number or not money_col:
                        return CommandResponse.create_error(
                            "소지금 정보를 갱신할 수 없습니다. 잠시 후 다시 시도해 주세요."
                        )

                    # 릴 굴림 + 배당 계산
                    reels = spin_reels()
                    multiplier, tier, matched = evaluate(reels)
                    # 배수가 소수(0.3·0.7·1.5)라 정수로 만든다. **올림**이다(운영 결정 2026-07-16).
                    # 내림이면 최소 베팅에서 가장 흔한 당첨(▲▲ 0.3배)이 0원이 되어
                    # 꽝과 구분되지 않는다. 올림은 설계 RTP(89.8%)보다 조금 더 주지만
                    # 체감이 정직한 쪽을 택했다.
                    payout_amount = math.ceil(bet * multiplier)
                    new_money = current_money - bet + payout_amount

                    ok = self.sheets_manager.batch_update_cells(
                        MANAGEMENT_SHEET, [(row_number, money_col, new_money)]
                    )
                    if not ok:
                        return CommandResponse.create_error(
                            "정산 처리에 실패했습니다. 잠시 후 다시 시도해 주세요."
                        )
                    settled = True
            finally:
                if not settled:
                    self._refund_spin(context.user_id)

            # 5) 캐시 무효화
            invalidate_user_cache()

            # 6) 응답
            message = self._build_message(
                reels, multiplier, tier, matched, bet, payout_amount, new_money, currency
            )
            payload = {
                'reels': reels,
                'multiplier': multiplier,
                'bet': bet,
                'payout': payout_amount,
                'net': payout_amount - bet,
                'balance': new_money,
            }
            return CommandResponse.create_success(message, data=payload)

        except CommandError as e:
            return CommandResponse.create_error(str(e), error=e)
        except Exception as e:
            logger.error(f"[슬롯머신] 실행 중 오류: {e}", exc_info=True)
            return CommandResponse.create_error(
                "슬롯머신 처리 중 오류가 발생했습니다."
            )

    def _build_message(
        self,
        reels: List[str],
        multiplier: float,
        tier: Optional[int],
        matched: int,
        bet: int,
        payout_amount: int,
        new_money: int,
        currency: str,
    ) -> str:
        """응답 문구.

        형식(2026-07-16 확정):
            [ ● ● ▲ ]

            동그라미 2개! 베팅액의 1.5배를 돌려받습니다.
            ➭ 6달러 획득
            ➭ 현재 소지금 16달러

        꽝이어도 소지금 줄은 남긴다 — 얼마 남았는지가 다음 판을 정한다.
        """
        lines = ["[ " + " ".join(reels) + " ]", ""]

        if matched >= _REEL_COUNT:
            name = TIER_NAMES.get(tier, '')
            lines.append(f"{name} 잭팟! 베팅액의 {_fmt_mult(multiplier)}배를 돌려받습니다.")
        elif matched == 2 and payout_amount > 0:
            name = TIER_NAMES.get(tier, '')
            if multiplier >= 1:
                # 본전 이상 → 딴 것이다
                lines.append(f"{name} 2개! 베팅액의 {_fmt_mult(multiplier)}배를 돌려받습니다.")
            else:
                # 본전 미만 → "돌려받는다"가 맞는 표현(딴 게 아니다)
                lines.append(f"{name} 2개. 베팅액의 일부를 돌려받습니다.")
        elif matched == 2:
            # 2개 맞았는데 내림 때문에 0원 (예: 베팅 2 × 0.3 = 0.6 → 0)
            lines.append(f"{TIER_NAMES.get(tier, '')} 2개. 하지만 손에 남은 것은 없습니다.")
        else:
            lines.append("꽝!")

        if payout_amount > 0:
            lines.append(f"➭ {payout_amount:,}{currency} 획득")
        else:
            lines.append(f"➭ {bet:,}{currency} 잃음")
        lines.append(f"➭ 현재 소지금 {new_money:,}{currency}")

        return "\n".join(lines)

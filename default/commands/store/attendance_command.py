"""
출석 명령어 구현 (개편: 운세 + 소지금 통합)

[출석] = 오늘의 운세 문구 1줄 + ATTENDANCE_AMOUNT(기본 3) 달러 지급
         + '관리' 시트 출석 컬럼에 'O' 기록.

**하루 1회 판정의 기준은 '관리' 시트의 출석 컬럼이다.**
- 빈칸  = 오늘 출석 안 함 → 출석 가능
- 비어 있지 않음('O') = 오늘 출석함 → 거절

⚠️ **매일 00:00 KST 초기화(O → 빈칸)는 별도 스케줄러 봇의 책임이다.**
이 봇은 칸을 비우지 않는다. 그 봇이 멈추면 아무도 출석할 수 없으므로
(칸이 'O'인 채로 남는다) 운영 시 그 봇의 생존을 함께 확인해야 한다.
→ docs/운영_준비_가이드.md

시트가 곧 판정 기준이므로, GM이 칸을 손으로 지우면 그 사람은 그날 다시 출석할 수 있고
손으로 'O'를 넣으면 출석한 것으로 처리된다. 의도된 동작이다(GM이 시트로 직접 교정).

이력:
- 관리시트 MM/DD 마커 → 봇 JSON('오늘출석') → **관리시트 출석 컬럼**(현재).
  JSON 방식은 단일 슬롯을 전제했지만 시트 방식은 슬롯이 늘어도 정합성이 유지된다.

운세 문구는 '운세' 워크시트의 '문구' 열에서 무작위 선택한다
([운세] 단독 명령어는 제거되고 출석에 통합됨 — docs/코딩_계획.md §부록).

동시성·정합성:
- 사용자별 락 안에서 관리 시트를 미캐시로 재조회 → 판정 → 기록(read-modify-write 직렬화).
- 소지금 +ATTENDANCE_AMOUNT 와 출석 기록은 **한 번의 batch_update**로 함께 반영한다.
  따로 쓰면 '돈은 받았는데 출석 표시가 없는'(= 무한 재출석) 상태가 생길 수 있다.
- 출석 컬럼이 없으면 **출석을 거절한다(fail closed).** 컬럼이 곧 제한이라, 없는 채로
  진행하면 일일 제한이 사라져 소지금을 무한히 받을 수 있다.

환경변수:
- ATTENDANCE_ENABLED  : 기능 활성화 여부 (true/false)
- ATTENDANCE_COMMAND  : 명령어 이름 겸 '관리' 출석 컬럼명 (기본 '출석')
- ATTENDANCE_AMOUNT   : 출석 시 지급할 소지금 (기본 3)
"""

import os
import sys
import random
from typing import List, Tuple, Any, Optional, Dict

# 경로 설정 (VM 환경 대응)
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

try:
    from config.settings import config
    from utils.logging_config import logger
    from utils.error_handling import CommandError
    from utils.korean_utils import add_eun_neun
    from utils.lock_manager import get_lock_manager
    from utils.cache_manager import bot_cache
    from commands.store.base_store_command import BaseStoreCommand
    from commands.registry import register_command
except ImportError as e:
    import logging
    logger = logging.getLogger('attendance_command')
    logger.error(f"필수 모듈 임포트 실패: {e}")
    raise


# '관리' 워크시트 컬럼명
MGMT_SHEET = '관리'
COL_ID = '아이디'
COL_NAME = '이름'
COL_MONEY = '소지금'

# '관리' 시트 출석 컬럼에 기록하는 값.
# 알파벳 대문자 O (U+004F)이며 숫자 0이 아니다. 프로젝트 관례와 동일(소문화=O, 발송여부=O).
_ATTENDANCE_MARK = 'O'


def _cell_text(value) -> str:
    return str(value if value is not None else '').strip()


def _is_attended(value) -> bool:
    """출석 컬럼 값이 '오늘 출석함'을 뜻하는지.

    규칙은 '빈칸 = 출석 안 함'이므로, **비어 있지 않으면 전부 출석**한 것으로 본다.
    이 칸에는 메모를 적지 않는다는 운영 약속이 있다(출석 취소는 칸을 비우는 것).
    'O'만 인정하면 GM이 소문자 'o'나 'ㅇ'로 적어 둔 칸을 빈칸 취급해
    같은 사람이 두 번 출석하게 된다. 판정은 넓게, 기록은 'O'로 통일.
    """
    return bool(_cell_text(value))


def _notify_admin_once(key: str, message: str, api=None) -> None:
    """관리자에게 1회 알림. 발송 실패가 출석 처리를 막지 않는다."""
    try:
        from utils.investigation_notify import notify_admin_once
        notify_admin_once(key, message, api=api, prefix='[출석]')
    except Exception as e:
        logger.error("관리자 알림 실패: %s", e)

# 시트/캐시를 사용할 수 없을 때 사용할 폴백 운세 문구
_FALLBACK_FORTUNES = [
    "오늘은 좋은 일이 생길 것입니다.",
    "새로운 기회가 찾아올 것입니다.",
    "주변 사람들과의 관계가 좋아질 것입니다.",
    "건강에 주의하세요.",
    "금전적으로 좋은 소식이 있을 것입니다.",
]


def _resolve_attendance_name() -> str:
    """환경변수에서 출석 명령어 이름 해석 (빈 값일 경우 기본 '출석')"""
    name = (getattr(config, 'ATTENDANCE_COMMAND', '출석') or '출석').strip()
    return name or '출석'


_ATTENDANCE_NAME = _resolve_attendance_name()


@register_command(
    name=_ATTENDANCE_NAME,
    aliases=[],
    description=f"오늘의 {_ATTENDANCE_NAME}을(를) 체크하고 운세와 보너스 소지금을 받습니다.",
    category="상점",
    examples=[f"[{_ATTENDANCE_NAME}]"],
    enabled=getattr(config, 'ATTENDANCE_ENABLED', False),
    requires_sheets=True,
    requires_api=False,
    universal=True,
)
class AttendanceCommand(BaseStoreCommand):
    """
    출석 명령어 클래스 (운세 + 소지금 통합)

    동작 (사용자별 락 안에서 1~3을 수행):
    1. '관리' 시트 출석 컬럼 확인 → 비어 있지 않으면 이미 출석 → 안내 후 종료
    2. 소지금 +ATTENDANCE_AMOUNT, 출석 컬럼 'O' 를 한 번의 batch_update로 기록
    3. '운세' 시트에서 오늘의 문구 1줄 무작위 선택
    4. 운세 문구 + 지급액 응답 반환
    """

    _sheet_error_suffix = " (출석)"
    _generic_error_prefix = "출석 처리 중"

    @staticmethod
    def get_supported_keywords() -> List[str]:
        """지원 키워드 반환 — 환경변수에서 설정한 이름 하나만 사용"""
        return [_resolve_attendance_name()]

    def _execute_command_logic(self, user, keywords: List[str]) -> Tuple[str, Any]:
        """
        출석 명령어 실행

        Returns:
            Tuple[str, Any]: (응답 메시지, 결과 데이터)

        Raises:
            CommandError: 처리 실패
        """
        if not getattr(config, 'ATTENDANCE_ENABLED', False):
            raise CommandError("출석 기능이 비활성화되어 있습니다.")

        if not self.sheets_manager:
            raise CommandError("시트 관리자가 설정되지 않았습니다.")

        attendance_name = _resolve_attendance_name()
        try:
            amount = int(getattr(config, 'ATTENDANCE_AMOUNT', 3) or 0)
        except (ValueError, TypeError):
            amount = 0
        if amount < 0:
            amount = 0

        # 명단에서 사용자 확인 (이름 조회용)
        roster = self._load_user_data()
        if not roster:
            raise CommandError("명단 데이터를 불러올 수 없습니다.")

        user_record = self._find_roster_entry(roster, user.id)
        if not user_record:
            raise CommandError(
                "명단에서 사용자 정보를 찾을 수 없습니다. 명단에 등록되어 있는지 확인해 주세요."
            )

        user_name = (str(user_record.get('이름', '') or '').strip() or user.id)

        lock_manager = get_lock_manager()
        with lock_manager.acquire_lock(user.id, timeout=10.0) as acquired:
            if not acquired:
                raise CommandError("다른 처리가 진행 중입니다. 잠시 후 다시 시도해 주세요.")

            # 판정(출석 컬럼 확인)과 기록을 락 안에서 함께 수행한다.
            # 실패하면 시트에 아무것도 쓰지 않으므로 롤백할 상태가 없다.
            current_money, new_money = self._check_and_mark_attendance(
                user_id=user.id,
                amount=amount,
                attendance_name=attendance_name,
            )

        fortune = self._pick_fortune()
        currency = (getattr(config, 'CURRENCY', '') or '').strip()
        message = self._build_message(
            fortune=fortune,
            amount=amount,
            currency=currency,
        )

        payload = {
            'user_id': user.id,
            'user_name': user_name,
            'attendance_name': attendance_name,
            'fortune': fortune,
            'amount': amount,
            'previous_money': current_money,
            'new_money': new_money,
            'currency': currency,
        }
        logger.info(
            "출석 처리 완료: user=%s, amount=%s, %s -> %s",
            user.id, amount, current_money, new_money,
        )
        return message, payload

    def _find_roster_entry(self, roster: List[Dict[str, Any]], user_id: str) -> Optional[Dict[str, Any]]:
        """명단에서 user_id에 해당하는 행 반환"""
        for row in roster:
            if str(row.get(COL_ID, '')).strip() == user_id:
                return row
        return None

    @staticmethod
    def _column_index(target_row: Dict[str, Any], column: str) -> Optional[int]:
        """이미 읽어 온 행의 키 순서에서 1-indexed 열 번호를 산출. 없으면 None.

        `get_worksheet_data`가 `dict(zip(header, row))`로 만들므로 키 순서 = 헤더 순서다.
        추가 API 호출(get_worksheet + row_values)이 없다.

        헤더 **정확 일치**로 찾는다. `find_column_by_header`는 부분 일치라서
        '출석'으로 검색하면 '오늘출석' 같은 다른 컬럼을 잘못 집을 수 있다.
        """
        header = [k for k in target_row.keys() if k != '_row_number']
        try:
            return header.index(column) + 1
        except ValueError:
            return None

    def _attendance_column_index(self, target_row: Dict[str, Any]) -> Optional[int]:
        """'관리' 시트 출석 컬럼의 1-indexed 열 번호. 없으면 None."""
        return self._column_index(target_row, _resolve_attendance_name())

    def _money_column_index(self, target_row: Dict[str, Any]) -> Optional[int]:
        """'관리' 시트 소지금 컬럼의 1-indexed 열 번호. 없으면 None.

        1순위: 행 키에서 정확 일치(API 0회).
        2순위: 헤더 부분 일치(`_find_money_column`) — '소지금(달러)'처럼 헤더가
               변형된 시트를 기존 코드가 흡수하고 있었을 수 있어 폴백으로 남긴다.
               이 경로만 get_worksheet + row_values로 API를 2회 쓴다.
        """
        col = self._column_index(target_row, COL_MONEY)
        if col is not None:
            return col
        logger.debug("'소지금' 정확 일치 실패 - 헤더 부분 일치로 폴백")
        return self._find_money_column()

    def _check_and_mark_attendance(
        self,
        user_id: str,
        amount: int,
        attendance_name: str,
    ) -> Tuple[int, int]:
        """
        출석 컬럼으로 하루 1회를 판정하고, 통과 시 결과를 반영 (한 번의 batch_update).

        - 출석 컬럼이 비어 있지 않으면 이미 출석한 것 → 거절
        - 통과 시 소지금 +amount, 출석 컬럼 'O' 를 같은 배치로 기록

        호출측이 사용자별 락 안에서 부른다는 전제다(read-modify-write 직렬화).

        Returns:
            Tuple[int, int]: (이전 소지금, 새 소지금)

        Raises:
            CommandError: 이미 출석했거나, 컬럼/사용자를 찾을 수 없거나, 쓰기에 실패한 경우
        """
        # 실시간 데이터 조회 (캐시 사용 안 함 - stale 방지)
        management_data = self.sheets_manager.get_worksheet_data(MGMT_SHEET, use_cache=False)
        if not management_data:
            raise CommandError("'관리' 워크시트에서 데이터를 불러올 수 없습니다.")

        target_row = None
        for row in management_data:
            if str(row.get(COL_ID, '')).strip() == user_id:
                target_row = row
                break

        if not target_row:
            raise CommandError("'관리' 워크시트에서 사용자 정보를 찾을 수 없습니다.")

        user_row_number = target_row.get('_row_number')
        if not isinstance(user_row_number, int) or user_row_number <= 0:
            raise CommandError("'관리' 워크시트에서 사용자 행 번호를 확인할 수 없습니다.")

        money_col = self._money_column_index(target_row)
        if money_col is None:
            raise CommandError("'관리' 워크시트에서 '소지금' 컬럼을 찾을 수 없습니다.")

        # ── 하루 1회 판정 (출석 컬럼이 기준) ──
        attendance_col = self._attendance_column_index(target_row)
        if attendance_col is None:
            # 컬럼이 곧 제한이다. 없는 채로 진행하면 일일 제한이 사라져
            # 소지금을 무한히 받을 수 있으므로 막는다(fail closed).
            # 이 상태는 전원 출석 불가라서 로그만 남기면 아무도 모른다 → 관리자에게 알린다.
            logger.error(
                "'관리' 워크시트에 '%s' 컬럼이 없어 출석을 거절합니다. "
                "일일 제한을 판정할 수 없습니다.", attendance_name,
            )
            _notify_admin_once(
                f"attendance-column-missing:{attendance_name}",
                f"⚠️ '관리' 워크시트에 '{attendance_name}' 컬럼이 없습니다. "
                f"이 컬럼이 출석 1일1회 판정의 기준이라, 없으면 **아무도 출석할 수 없습니다**"
                f"(무한 지급을 막기 위한 의도된 차단). 컬럼을 추가해 주세요.",
                api=self.api,
            )
            raise CommandError(
                f"'{attendance_name}' 기능이 아직 준비되지 않았습니다. 관리자에게 문의해 주세요."
            )

        if _is_attended(target_row.get(attendance_name)):
            attendance_with_josa = add_eun_neun(attendance_name)
            raise CommandError(f"{attendance_with_josa} 하루 한 번만 가능합니다.")

        # 현재 소지금 파싱
        raw_money = target_row.get('소지금', 0)
        try:
            if raw_money is None or str(raw_money).strip() == '':
                current_money = 0
            else:
                current_money = int(float(str(raw_money).strip()))
        except (ValueError, TypeError):
            logger.warning("소지금 파싱 실패, 0으로 처리: raw=%s", raw_money)
            current_money = 0

        new_money = current_money + amount

        # 소지금과 출석 기록을 같은 배치로 쓴다. 따로 쓰면 한쪽만 성공했을 때
        # '돈은 받았는데 출석 칸은 빈칸'(= 무한 재출석)이 될 수 있다.
        updates = [
            (user_row_number, money_col, new_money),
            (user_row_number, attendance_col, _ATTENDANCE_MARK),
        ]

        success = self.sheets_manager.batch_update_cells(MGMT_SHEET, updates)
        if not success:
            raise CommandError("출석 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.")

        # 소지금 캐시 무효화
        self._invalidate_user_cache()

        return current_money, new_money

    # ── 운세 문구 (구 fortune_command 로직 인라인) ──

    def _pick_fortune(self) -> str:
        """오늘의 운세 문구 1줄 무작위 선택 (실패 시 폴백)."""
        fortunes = self._load_fortune_list()
        if not fortunes:
            fortunes = _FALLBACK_FORTUNES
        return random.choice(fortunes)

    def _load_fortune_list(self) -> List[str]:
        """'운세' 시트의 '문구' 열 목록 로드 (1시간 캐시)."""
        # 캐시 우선
        try:
            cached = bot_cache.get_fortune_phrases()
            if cached:
                return cached
        except Exception as e:
            logger.debug("운세 캐시 조회 실패: %s", e)

        if not self.sheets_manager:
            return []

        try:
            worksheet_data = self.sheets_manager.get_worksheet_data('운세', use_cache=True)  # 운세는 정적 → 캐시
            if not worksheet_data:
                logger.warning("운세 워크시트 데이터를 불러올 수 없음")
                return []

            fortune_list: List[str] = []
            for row in worksheet_data:
                phrase = str(row.get('문구', '') or '').strip()
                if phrase:
                    fortune_list.append(phrase)

            if fortune_list:
                try:
                    ttl_seconds = getattr(config, 'CACHE_TTL', 3600) or 3600
                    bot_cache.cache_fortune_phrases(fortune_list, ttl_seconds=ttl_seconds)
                except Exception as e:
                    logger.debug("운세 캐시 저장 실패: %s", e)

            return fortune_list
        except Exception as e:
            logger.error("운세 목록 로드 실패: %s", e)
            return []

    def _build_message(self, fortune: str, amount: int, currency: str) -> str:
        """응답 메시지 생성 (운세 문구 + 지급액).

        형식: `{운세}` / `➭ 3달러 획득`
        화폐 단위는 숫자에 붙여 쓴다(CURRENCY=달러 → "3달러 획득").
        """
        return f"{fortune}\n➭ {amount}{currency} 획득"

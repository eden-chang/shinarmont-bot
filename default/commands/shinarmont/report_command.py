"""
[결과 보고/키워드] 명령어 (@STORY, DM 전용)

부탁·지령 수행 결과를 보고한다.

    [결과 보고/까마귀]
    보고 내용: 광장에 앉아 있던 남자에게 ···라고 말해 자리를 뜨게 만들었음.

- `키워드`로 시스템 `부탁지령` 시트의 지령을 찾는다(대상=보고자, 상태='전송됨').
- '보고 내용:' 뒤 텍스트를 `완료 내용` 열에 적는다. 접두사가 없으면 본문 전체를 쓴다.
- 매칭되면 `부탁지령` 행에 상태='완료됨' / 완료 일차 / 완료 시각 / 완료 내용을 쓰고,
  `보상` 컬럼만큼 관리 시트 소지금을 지급한다.

**상태 열이 곧 진행 상태이자 중복 방지 마커다** (2026-07-16 개편, `부탁지령기록` 시트 폐지):
  · 빈칸   = 아직 전송되지 않음 → 보고 불가
  · 전송됨 = 전송됐고 미완료   → **보고 가능**
  · 완료됨 = 전송·완료 끝      → 재보고 불가(이중 지급 차단)
  · 실패   = 마감기한 초과      → 보고 불가

부탁·지령 **전송은 GM이 수동으로** 한다. 봇은 완료 처리만 한다.

docs/시너몬트_구현계획.md §5.12 / docs/코딩_계획.md §6(그룹 C) / docs/명령어_개요.md.
"""

import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

# 경로 설정 (VM 환경 대응)
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))

from utils.imports import *  # noqa: F401,F403  (config, logger, CommandError, BaseCommand, CommandContext, CommandResponse, register_command, invalidate_user_cache, load_user_data, add_i_ga 등)

try:
    from utils.lock_manager import get_lock_manager
    from utils.dm_guard import dm_only
    from utils.dice_parser import evaluate_amount
    from utils import action_log
    from utils import game_day
except ImportError as e:  # pragma: no cover - 부분 환경 폴백
    import logging
    logger = logging.getLogger('shinarmont.report')
    logger.error(f"[결과 보고] 필수 모듈 임포트 실패: {e}")
    raise


# 시스템 시트 이름
# 열: 일차 · 대상 · 키워드 · 내용 · 경중 · 보상 · 상태 · 완료 일차 · 완료 시각 · 완료 내용
DIRECTIVE_SHEET = '부탁지령'
# `부탁지령기록` 시트는 2026-07-16에 폐지됐다. 진행 상태와 중복 방지를 모두
# `부탁지령`의 '상태' 열이 담당한다(시트 하나만 보면 되게).

# '상태' 열 값
STATUS_UNSENT = ''         # 빈칸 — 아직 전송되지 않음
STATUS_SENT = '전송됨'      # 전송됐고 미완료 → 보고 가능
STATUS_DONE = '완료됨'      # 전송·완료 끝
STATUS_FAILED = '실패'      # 마감기한까지 수행하지 못함

# 완료 보고 본문에서 내용을 뽑는 접두사
REPORT_BODY_PREFIX = '보고 내용:'

def _normalize_keyword(value: Any) -> str:
    """키워드 비교용 정규화. 공백·대소문자를 무시한다.

    GM이 시트에 적는 값과 이용자가 타이핑하는 값이 완전히 같기를 기대할 수 없다
    ('까마귀 ' / '까 마귀' / 'Crow' vs 'crow').
    """
    return re.sub(r'\s+', '', str(value or '')).strip().lower()


def _normalize_status(value: Any) -> str:
    """상태 열 정규화. 빈칸/공백은 '' (= 미전송)."""
    return str(value or '').strip()


# 보상 컬럼이 비어 있을 때 경중 문자열 → 기본 보상 폴백
DEFAULT_SEVERITY_REWARD: Dict[str, int] = {
    '상': 100, '중대': 100, '대': 100,
    '중': 50,
    '하': 20, '경': 20, '소': 20,
}


@register_command(
    name="결과 보고",
    aliases=["결과보고", "보고"],
    description="부탁·지령 수행 결과를 보고합니다. [결과 보고/키워드] (DM 전용)",
    category="스토리",
    examples=["[결과 보고/까마귀]\n보고 내용: 광장의 까마귀를 쫓아냈습니다."],
    requires_sheets=True,
    requires_api=False,
)
class ReportCommand(BaseCommand):
    """[결과 보고] + 본문 명령어."""

    @staticmethod
    def get_supported_keywords() -> List[str]:
        return ["결과 보고", "결과보고", "보고"]

    def __init__(self, sheets_manager=None, api=None, **kwargs):
        super().__init__(sheets_manager, api, **kwargs)
        self.system_sheets_manager = kwargs.get('system_sheets_manager')
        logger.debug(
            f"[결과 보고] ReportCommand 초기화: system_sheets_manager="
            f"{'있음' if self.system_sheets_manager else '없음'}"
        )

    # ------------------------------------------------------------------ #
    # 진입점
    # ------------------------------------------------------------------ #
    @dm_only
    def execute(self, context: CommandContext) -> CommandResponse:
        keyword = self._extract_keyword(context)
        if not keyword:
            return CommandResponse.create_error(
                "어느 지령인지 키워드를 함께 보내 주세요.\n"
                "예) [결과 보고/까마귀]"
            )
        body = self._extract_body(context)

        if self.system_sheets_manager is None:
            return CommandResponse.create_error(
                "결과 보고 기능이 설정되지 않았습니다. 관리자에게 문의해 주세요."
            )

        reporter_name = self._get_reporter_name(context.user_id, context.user_name)
        if not reporter_name:
            return CommandResponse.create_error(
                "보고자 정보를 찾을 수 없습니다. 명단에 등록되어 있는지 확인해 주세요."
            )

        today = game_day.current_day()

        lock_manager = get_lock_manager()
        with lock_manager.acquire_lock(context.user_id, timeout=10.0) as acquired:
            if not acquired:
                return CommandResponse.create_error(
                    "다른 처리가 진행 중입니다. 잠시 후 다시 시도해 주세요."
                )
            try:
                return self._process(context.user_id, reporter_name, today, keyword, body)
            except CommandError as e:
                return CommandResponse.create_error(str(e), error=e)
            except Exception as e:  # pragma: no cover - 방어적
                logger.error(f"[결과 보고] 처리 중 오류: {e}", exc_info=True)
                return CommandResponse.create_error(
                    "결과 보고 처리 중 오류가 발생했습니다.", error=e
                )

    # ------------------------------------------------------------------ #
    # 키워드 / 본문 추출
    # ------------------------------------------------------------------ #
    @staticmethod
    def _extract_keyword(context: CommandContext) -> str:
        """`[결과 보고/까마귀]` 의 '까마귀'. 없으면 빈 문자열."""
        keywords = getattr(context, 'keywords', None) or []
        if len(keywords) < 2:
            return ''
        return str(keywords[1] or '').strip()

    def _extract_body(self, context: CommandContext) -> str:
        """대괄호 뒤 본문에서 완료 내용을 뽑는다.

            [결과 보고/까마귀]
            보고 내용: 광장의 까마귀를 쫓아냈습니다.

        '보고 내용:' 뒤 텍스트만 취한다. 접두사가 없으면 본문 전체를 쓴다
        (형식을 틀렸다고 보고를 거절하면 이용자만 답답하다).
        본문이 아예 없어도 보고는 성립한다 — 완료 내용이 비는 것뿐이다.
        """
        raw = getattr(context, 'original_text', '') or ''
        if not raw:
            return ''

        # BBCode 스타일 색상/배경 태그 제거 (router 와 동일 규칙)
        raw = re.sub(r'\[/?(color|bg)(:[0-9a-fA-F]{3,8})?\]', '', raw)
        # 첫 번째 명령어 대괄호 토큰 1개만 제거
        body = re.sub(r'\[[^\]]*\]', '', raw, count=1).strip()
        if not body:
            return ''

        body = self._normalize_body(body)
        idx = body.find(REPORT_BODY_PREFIX)
        if idx >= 0:
            body = body[idx + len(REPORT_BODY_PREFIX):]
        return self._normalize_body(body)

    @staticmethod
    def _normalize_body(body: str) -> str:
        """본문 정리: 앞뒤 공백 제거, 과도한 빈 줄 축약."""
        lines = [ln.rstrip() for ln in body.splitlines()]
        # 선행/후행 빈 줄 제거
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        return '\n'.join(lines).strip()

    # ------------------------------------------------------------------ #
    # 보고자 이름 조회
    # ------------------------------------------------------------------ #
    def _get_reporter_name(self, user_id: str, fallback_name: str = '') -> str:
        """명단(아이디→이름)에서 보고자 이름을 조회한다. 실패 시 fallback."""
        try:
            roster = load_user_data(self.sheets_manager) or []
        except Exception as e:
            logger.warning(f"[결과 보고] 명단 조회 실패: {e}")
            roster = []

        target = self._normalize_acct(user_id)
        for row in roster:
            if self._normalize_acct(row.get('아이디', '')) == target:
                name = str(row.get('이름', '')).strip()
                if name:
                    return name
        # 폴백: 컨텍스트 이름(단, acct 그대로면 부정확하므로 그대로 반환)
        return str(fallback_name or '').strip()

    # ------------------------------------------------------------------ #
    # 매칭 + 보상 지급 + 기록
    # ------------------------------------------------------------------ #
    def _process(
        self,
        user_id: str,
        reporter_name: str,
        today: int,
        keyword: str,
        body: str,
    ) -> CommandResponse:
        system = self.system_sheets_manager

        try:
            directives = system.get_worksheet_data(DIRECTIVE_SHEET, use_cache=False) or []
        except Exception as e:
            logger.error(f"[결과 보고] '{DIRECTIVE_SHEET}' 조회 실패: {e}", exc_info=True)
            raise CommandError("지령 데이터를 불러올 수 없습니다. 잠시 후 다시 시도해 주세요.")

        directive = self._match_directive(directives, reporter_name, keyword)

        directive_day = str(directive.get('일차', '')).strip()
        directive_content = str(directive.get('내용', '')).strip()
        severity = str(directive.get('경중', '')).strip()
        reward_intended = self._compute_reward(directive)

        # === 이중 지급 방지: 지급 '이전에' 상태를 완료됨으로 바꾼다. ===
        # 지급(batch_update)은 성공했는데 상태 기록이 실패하면, 재보고 시 같은 지령이
        # 다시 매칭돼 이중 지급이 된다(결과 보고는 일일 제한이 없어 즉시 재시도 가능).
        # → 상태를 먼저 durable 하게 쓰고(실패 시 지급 없이 종료), 그 다음 지급한다.
        #   지급이 실패해도 이 마커가 재매칭을 막는다(수동 정산으로 처리).
        if not self._mark_completed(system, directive, today, body):
            logger.warning(
                f"[결과 보고] 완료 기록 실패 — 지급 보류 "
                f"(보고자={reporter_name}, 키워드={keyword})"
            )
            raise CommandError("결과 보고 기록 저장에 실패했습니다. 잠시 후 다시 시도해 주세요.")

        # 보상 지급(관리 소지금 batch). 상태가 이미 완료됨이라 재지급 위험이 없다.
        paid, new_balance = self._pay_reward(user_id, reward_intended)
        if reward_intended > 0 and paid <= 0:
            # 상태는 완료로 박혔는데 돈이 안 나갔다 → 조용히 넘어가면 아무도 모른다.
            logger.error(
                f"[결과 보고] 보상 미지급 — 수동 정산 필요: 보고자={reporter_name}, "
                f"키워드={keyword}, 예정액={reward_intended}"
            )

        # 행동로그 (종류=결과보고). 요약 한 줄.
        summary = self._build_log_summary(directive_content, body)
        stat_change = f"소지금 +{paid}" if paid else ''
        detail = f"경중 {severity}" if severity else ''
        action_log.append(
            system,
            action_log.KIND_REPORT,
            reporter_name,
            reporter_name,
            summary,
        )

        message = self._build_response(paid, new_balance, directive_content)
        data = {
            'reporter': reporter_name,
            'keyword': keyword,
            'directive_day': directive_day,
            'severity': severity,
            'reward_intended': reward_intended,
            'reward_paid': paid,
            'new_balance': new_balance,
            'row': directive.get('_row_number'),
        }
        logger.info(
            f"[결과 보고] 보고자={reporter_name}, 키워드={keyword}, "
            f"지령일차={directive_day}, 경중={severity or '-'}, 보상={paid}"
        )
        return CommandResponse.create_success(message, data=data)

    # ------------------------------------------------------------------ #
    # 지령 매칭
    # ------------------------------------------------------------------ #
    def _match_directive(
        self,
        directives: List[Dict[str, Any]],
        reporter_name: str,
        keyword: str,
    ) -> Dict[str, Any]:
        """(대상=보고자, 키워드 일치, 상태='전송됨') 지령 1건. 없으면 CommandError.

        상태별로 안내를 나눈다 — "매칭되는 지령이 없습니다." 하나로 뭉치면
        이용자가 오타를 의심해야 할지 GM을 기다려야 할지 알 수 없다.
        """
        reporter_key = self._normalize_name(reporter_name)
        keyword_key = _normalize_keyword(keyword)

        mine = [
            d for d in directives
            if self._normalize_name(d.get('대상', '')) == reporter_key
            and _normalize_keyword(d.get('키워드', '')) == keyword_key
        ]

        if not mine:
            raise CommandError(
                f"'{keyword}' 지령을 찾을 수 없습니다. "
                "수령한 지령문의 키워드를 그대로 적어 주십시오."
            )

        sent = [d for d in mine if _normalize_status(d.get('상태', '')) == STATUS_SENT]
        if sent:
            # 같은 키워드가 여러 건 열려 있으면 가장 아래 행(최신)을 쓴다.
            return max(sent, key=lambda d: d.get('_row_number', 0) or 0)

        statuses = {_normalize_status(d.get('상태', '')) for d in mine}
        if STATUS_DONE in statuses:
            raise CommandError(f"'{keyword}' 지령은 이미 완료 보고가 접수되었습니다.")
        if STATUS_FAILED in statuses:
            raise CommandError(f"'{keyword}' 지령은 기한이 지나 마감되었습니다.")
        # 빈칸 = 아직 전송 전
        raise CommandError(
            f"'{keyword}' 지령은 아직 전달되지 않았습니다. 지령문을 받은 뒤 보고해 주세요."
        )

    # ------------------------------------------------------------------ #
    # 완료 기록 ('부탁지령' 행 갱신)
    # ------------------------------------------------------------------ #
    def _mark_completed(
        self,
        system,
        directive: Dict[str, Any],
        today: int,
        body: str,
    ) -> bool:
        """지령 행에 상태·완료 일차·완료 시각·완료 내용을 한 번에 쓴다.

        4개 셀을 batch로 묶는 이유: 한 번의 쓰기로 끝나야 중간에 실패해
        '상태만 완료됨, 내용은 빈칸' 같은 어중간한 행이 남지 않는다.
        """
        row_no = directive.get('_row_number')
        if not row_no:
            logger.error("[결과 보고] 지령 행 번호를 알 수 없어 완료 기록 불가")
            return False

        header = [k for k in directive.keys() if k != '_row_number']
        updates: List[Tuple[int, int, Any]] = []

        def _put(column: str, value: Any) -> bool:
            if column not in header:
                return False
            updates.append((row_no, header.index(column) + 1, value))
            return True

        if not _put('상태', STATUS_DONE):
            # 상태 열이 없으면 중복 방지 마커를 남길 수 없다 → 보고를 받지 않는다.
            # (받아 버리면 재보고로 보상을 무한히 탈 수 있다)
            logger.error(f"[결과 보고] '{DIRECTIVE_SHEET}'에 '상태' 열이 없습니다 - 보고 거부")
            return False

        _put('완료 일차', today)
        _put('완료 시각', self._now())
        if body:
            _put('완료 내용', body)

        try:
            return bool(system.batch_update_cells(DIRECTIVE_SHEET, updates))
        except Exception as e:
            logger.error(f"[결과 보고] 완료 기록 실패: {e}", exc_info=True)
            return False

    # ------------------------------------------------------------------ #
    # 보상 계산
    # ------------------------------------------------------------------ #
    def _compute_reward(self, directive: Dict[str, Any]) -> int:
        """
        지령의 보상액을 계산한다.

        1순위: `보상` 컬럼(정수 또는 다이스식) → evaluate_amount.
        2순위: `보상`이 비어 있으면 `경중` 문자열 → DEFAULT_SEVERITY_REWARD.
        둘 다 없으면 0.
        """
        reward_expr = str(directive.get('보상', '')).strip()
        if reward_expr:
            try:
                value, _detail = evaluate_amount(reward_expr)
                return max(0, int(value))
            except (ValueError, TypeError) as e:
                logger.warning(f"[결과 보고] 보상 파싱 실패({reward_expr!r}) - 경중 폴백: {e}")

        severity = str(directive.get('경중', '')).strip()
        if severity in DEFAULT_SEVERITY_REWARD:
            return DEFAULT_SEVERITY_REWARD[severity]

        return 0

    # ------------------------------------------------------------------ #
    # 관리 소지금 지급
    # ------------------------------------------------------------------ #
    def _pay_reward(self, user_id: str, amount: int) -> Tuple[int, Optional[int]]:
        """
        관리 시트 `소지금`에 amount 를 더한다(batch).

        Returns:
            (실제 지급액, 지급 후 잔액). 지급 불가/불필요 시 (0, 잔액 또는 None).
        """
        if amount <= 0:
            return 0, None
        if self.sheets_manager is None:
            logger.warning("[결과 보고] 기본 sheets_manager 없음 - 보상 지급 생략")
            return 0, None

        try:
            management = self.sheets_manager.get_worksheet_data('관리', use_cache=False) or []
        except Exception as e:
            logger.warning(f"[결과 보고] 관리 시트 조회 실패 - 보상 지급 생략: {e}")
            return 0, None

        user_row = None
        target = self._normalize_acct(user_id)
        for row in management:
            if self._normalize_acct(row.get('아이디', '')) == target:
                user_row = row
                break

        if user_row is None:
            logger.info(f"[결과 보고] 관리 시트에 사용자 {user_id} 행 없음 - 보상 지급 생략")
            return 0, None

        header = [k for k in user_row.keys() if k != '_row_number']
        if '소지금' not in header:
            logger.warning("[결과 보고] 관리 시트에 '소지금' 컬럼 없음 - 보상 지급 생략")
            return 0, None

        row_index = user_row.get('_row_number')
        if not row_index:
            logger.warning("[결과 보고] 관리 시트 행 번호 없음 - 보상 지급 생략")
            return 0, None

        current = self._parse_money(user_row.get('소지금', 0))
        new_balance = current + amount
        money_col = header.index('소지금') + 1

        try:
            ok = self.sheets_manager.batch_update_cells(
                '관리', [(row_index, money_col, new_balance)]
            )
        except Exception as e:
            logger.error(f"[결과 보고] 소지금 갱신 실패: {e}", exc_info=True)
            return 0, current

        if not ok:
            logger.warning("[결과 보고] 소지금 batch_update 실패")
            return 0, current

        try:
            invalidate_user_cache()
        except Exception:
            pass

        return amount, new_balance

    # ------------------------------------------------------------------ #
    # 응답/요약 구성
    # ------------------------------------------------------------------ #
    def _build_response(self, paid: int, new_balance: Optional[int], directive_content: str) -> str:
        """접수 응답. [고발]·[출석]과 같은 형식으로 맞춘다(2026-07-16).

        지령 내용을 되읊지 않는다 — 이용자가 방금 수행한 일이라 알고 있고,
        DM이라 해도 지령문이 로그·화면에 한 번 더 남을 이유가 없다.
        """
        currency = getattr(config, 'CURRENCY', '포인트')
        message = "접수 완료"
        if paid > 0:
            message += f"\n➭ {paid:,}{currency} 획득"
        return message

    @staticmethod
    def _build_log_summary(directive_content: str, body: str) -> str:
        """행동로그 요약(한 줄)."""
        base = directive_content or body
        one_line = ' '.join(str(base).split())
        if len(one_line) > 50:
            one_line = one_line[:50] + '…'
        return f"지령 결과 보고: {one_line}" if one_line else "지령 결과 보고"

    # ------------------------------------------------------------------ #
    # 유틸
    # ------------------------------------------------------------------ #
    @staticmethod
    def _now() -> str:
        try:
            from utils.sheets_operations import SheetsManager
            return SheetsManager.get_current_time()
        except Exception:
            from datetime import datetime
            import pytz
            return datetime.now(pytz.timezone('Asia/Seoul')).strftime('%Y-%m-%d %H:%M:%S')

    @staticmethod
    def _normalize_name(name: Any) -> str:
        if not name:
            return ''
        return re.sub(r'\s+', '', str(name)).strip().lower()

    @staticmethod
    def _normalize_acct(acct: Any) -> str:
        if not acct:
            return ''
        return str(acct).strip().lstrip('@').strip().lower()

    # (_normalize_day_str / _parse_day 는 2026-07-16에 제거했다.
    #  옛 '(일차, 대상)' 매칭·중복검사 전용이었는데 키워드+상태 방식으로 바뀌며 쓰이지 않는다.)

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

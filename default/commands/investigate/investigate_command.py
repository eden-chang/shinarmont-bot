"""
[조사/포인트명] 명령어 (가이드 §3.3)

판정 순서:
1. 현재 위치가 설정되어 있는가 → 없으면 "어디서 조사할지부터 정해야 한다."
   (메모리에 없으면 로그 시트의 당일 최근 진입 기록에서 복원 — 재시작 대비, §5.1)
2. 현재 위치 장소 시트에 해당 포인트명의 행이 존재하는가
3. 그 포인트명의 행 중 '가능'이면서 캐릭터가 접근 가능한 행이 있는가
   → 2·3 실패는 **동일한 문구**로 응답한다. 권한 없음을 티 내지 않는다(정보 비대칭 유지).
4. 조건을 만족하는 행이 둘 이상이면 하나를 무작위 선택 (변주)
5. 일일 조사 횟수 확인 (로그 시트 당일 조사 기록 수)
6. 재화 증감이 음수면 잔액 확인 → 부족하면 실패, 아무것도 적용하지 않음
7. 전부 통과 시: 주사위 굴림 → 아이템·스탯·재화 적용 → 로그 기록 → 응답

동시성: 스탯·재화 갱신의 읽기-수정-쓰기 경합을 막기 위해 캐릭터 단위 락으로 직렬화한다 (§7).
락 안에서 관리 시트와 장소 시트를 미캐시로 재조회해 stale read를 방지한다.

주사위는 잔액 검사보다 먼저 굴린다. 재화 증감이 -(1d4+3) 같은 수식일 수 있어
굴려야 금액을 알 수 있기 때문이다. 굴림은 시트를 건드리지 않으므로
잔액 부족으로 중단해도 아무 상태도 바뀌지 않는다.
"""

import os
import sys
from typing import Any, Dict, List, Tuple

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))

try:
    from config.settings import config
    from utils.logging_config import logger
    from utils.lock_manager import get_lock_manager
    from utils.investigation_actor import (
        RewardWriteError, can_afford, commit_reward, load_shop_items, plan_reward,
        roll_reward,
    )
    from utils.investigation_log import (
        append_investigation, count_today_investigations, format_summary,
        last_location_today,
    )
    from utils.investigation_sheet import (
        COL_EXC_TEXT, COL_POINT, COL_POINT_TEXT, InvestigationDataError,
        accessible_point_rows, config_int, display_name, find_exception,
        normalize_name, pick_variant,
    )
    from utils.investigation_state import get_investigation_state
    from commands.base_command import CommandContext, CommandResponse
    from commands.registry import register_command
    from commands.default.custom_command import process_all_custom_substitutions
    from commands.investigate.base_investigate import BaseInvestigateCommand
except ImportError as e:  # pragma: no cover
    import logging
    logger = logging.getLogger('investigate.investigate')
    logger.error(f"[조사] 필수 모듈 임포트 실패: {e}")
    raise


@register_command(
    name="조사",
    aliases=["탐색"],
    description="현재 진입한 장소의 조사 포인트를 조사합니다.",
    category="조사",
    examples=["[조사/수위계]"],
    requires_sheets=True,
    requires_api=False,
)
class InvestigateCommand(BaseInvestigateCommand):
    """장소 내 조사 포인트 명령어."""

    @staticmethod
    def get_supported_keywords() -> List[str]:
        return ["조사", "탐색"]

    # ------------------------------------------------------------------ #
    # 진입점
    # ------------------------------------------------------------------ #
    def execute(self, context: CommandContext) -> CommandResponse:
        blocked = self.disqualified_response(context)
        if blocked is not None:
            return blocked

        point_arg = self.parse_argument(context.keywords)
        if not point_arg:
            return self.refuse('NO_SUCH_POINT')

        lock_manager = get_lock_manager()
        with lock_manager.acquire_lock(context.user_id, timeout=10.0) as acquired:
            if not acquired:
                logger.warning(f"[조사] 락 획득 실패: user={context.user_id}")
                return CommandResponse.create_error(self.msg('TEMPORARY'))

            try:
                return self._run(context, point_arg)
            except InvestigationDataError as e:
                logger.error(f"[조사] 시트 오류: {e}")
                return CommandResponse.create_error(self.msg('TEMPORARY'), error=e)
            except RewardWriteError as e:
                # 로그는 이미 기록됐고 보상이 일부/전부 누락됐다 → 횟수만 소모된 상태.
                # 수동 정산 필요 (§7).
                logger.error(f"[조사] 보상 반영 실패: {e}", exc_info=True)
                self.warn_admin(
                    f"⚠️ 보상 반영 실패 — 로그에는 조사가 기록되어 횟수가 소모됐으나 "
                    f"보상이 반영되지 않았습니다. 수동 정산이 필요합니다. {e}"
                )
                return CommandResponse.create_error(self.msg('TEMPORARY'), error=e)
            except Exception as e:
                logger.error(f"[조사] 처리 중 오류: {e}", exc_info=True)
                return CommandResponse.create_error(self.msg('TEMPORARY'), error=e)

    # ------------------------------------------------------------------ #
    # 본 처리 (캐릭터 락 안)
    # ------------------------------------------------------------------ #
    def _run(self, context: CommandContext, point_arg: str) -> CommandResponse:
        # 관리 시트는 락 안에서 미캐시로 재조회 (스탯·재화 정합성)
        actor = self.load_actor(context.user_id)
        if actor is None:
            return self.refuse('NO_CHARACTER')

        inv_mgr = self.investigation_sheets_manager

        # 1. 현재 위치 (메모리 → 없으면 로그 시트에서 복원)
        location = get_investigation_state().get_or_restore(
            context.user_id,
            lambda: last_location_today(inv_mgr, actor.name),
        )
        if not location:
            return self.refuse('NOT_ENTERED')

        # 1-2. 그 장소가 아직 열려 있는가 (진입 시트 재확인, 미캐시)
        #
        # 가이드 §3.3의 판정 순서에는 없지만, 운영진이 진입 행을 닫으면
        # **이미 안에 있던 러너도 더는 조사할 수 없어야 한다**(운영 결정).
        # 이 확인이 없으면 GM이 "이 장소 닫았다."고 생각한 뒤에도 조사가 계속된다.
        entry_rows = self.repo.entry_rows(fresh=True)
        if not self.repo.open_entry_rows(location, entry_rows):
            logger.info(f"[조사] 닫힌 장소에서 조사 시도: {actor.name} → '{location}'")
            return self.refuse('CANNOT_ENTER', location=location)

        # 2·3. 포인트 존재 + 개방 + 접근 판정 (실패 시 전부 같은 문구)
        point_rows = self.repo.point_rows(location, fresh=True)
        exception_rows = self.repo.exception_rows()

        candidates = accessible_point_rows(
            point_rows, exception_rows, actor.name, location, actor.role,
            point=point_arg,
        )
        if not candidates:
            logger.info(
                f"[조사] 조사 불가: {actor.name}(직군={actor.role}) "
                f"'{location}' / '{point_arg}'"
            )
            # 현재 위치엔 그 포인트가 아예 없고, **지금 이 캐릭터가 갈 수 있는 다른 열린 장소**에
            # 그 포인트가 있으면 자리를 옮기라고 안내한다(어느 장소인지는 밝히지 않는다).
            hint = self._wrong_location_hint(
                actor, location, point_arg, point_rows, exception_rows, entry_rows)
            if hint is not None:
                return hint
            return self.refuse('NO_SUCH_POINT', location=location)

        # 4. 변주 무작위 선택
        point_row = pick_variant(candidates)
        point_name = display_name(point_row.get(COL_POINT))

        # 5. 일일 조사 횟수 (로그 시트가 기준)
        limit, used = self._limit_and_used(actor)
        if used >= limit:
            logger.info(f"[조사] 횟수 초과: {actor.name} {used}/{limit}")
            return self.refuse('DAILY_LIMIT', used=used, limit=limit)

        # 6. 굴림 → 잔액 확인 (아직 시트에 아무것도 쓰지 않는다)
        reward = roll_reward(point_row)
        if not can_afford(actor, reward):
            logger.info(
                f"[조사] 잔액 부족: {actor.name} 보유={actor.money}, "
                f"필요={abs(reward.money_delta)}"
            )
            self.warn_data(reward.warnings, f"'{location}' / '{point_name}'")
            return self.refuse('NO_MONEY', location=location, point=point_name)

        # 7. 계획 수립(쓰기 없음) → 로그 기록 → 보상 반영
        plan = plan_reward(
            actor,
            reward,
            # 아이템 보상이 있을 때만 상점을 읽는다(대부분의 조사는 아이템이 없다)
            shop_items=load_shop_items(self.sheets_manager) if reward.has_item else None,
            extra_updates=self._counter_update(actor, used + 1),
        )
        self.warn_data(plan.warnings, f"'{location}' / '{point_name}'")
        summary = format_summary(plan.summary)

        # 로그를 **먼저** 쓴다. 로그가 일일 제한의 기준이라, 보상을 먼저 주면
        # 로그 실패 시 횟수가 소모되지 않아 무한 조사·무한 재화가 된다.
        # 로그가 실패하면 아무것도 반영하지 않고 중단한다 → 러너는 손해 없이 재시도.
        if not append_investigation(inv_mgr, actor.name, location, point_name, summary):
            self.warn_admin(
                f"⚠️ 로그 기록 실패 — {actor.name}의 '{location}' / '{point_name}' 조사를 "
                f"중단했습니다(보상 미지급). 로그 시트가 일일 횟수 판정의 기준이라 "
                f"기록 없이 진행하면 횟수 제한이 무력화됩니다. 시트 상태를 확인해 주세요."
            )
            return CommandResponse.create_error(self.msg('TEMPORARY'))

        try:
            commit_reward(self.sheets_manager, actor, plan)
        except RewardWriteError:
            # 로그에는 남았는데 보상이 안 나갔다 → 횟수만 소모. 수동 정산 필요 (§7).
            # 반대(보상만 나가고 로그 없음)보다 이 방향이 안전하다.
            raise

        # 행동로그(소문 재료). 조사 `로그` 시트와 별개다 — 저건 일일 제한 판정용.
        # 요약에 수치를 넣지 않는다. 그대로 소문 문구가 될 수 있다.
        from utils import action_log
        from utils.korean_utils import add_i_ga, add_eul_reul
        self.log_action(
            action_log.KIND_INVESTIGATE,
            actor.name,
            location,
            f"{add_i_ga(actor.name)} {location}의 {add_eul_reul(point_name)} 살폈다",
        )

        message = self._build_message(
            point_row, exception_rows, actor, location, point_name, plan.display
        )

        logger.info(
            f"[조사] {actor.name}(직군={actor.role}) '{location}' / '{point_name}' "
            f"→ {summary} ({used + 1}/{limit})"
        )
        return CommandResponse.create_success(
            message,
            data={
                'location': location,
                'point': point_name,
                'changes_summary': summary,
                'used': used + 1,
                'limit': limit,
            },
        )

    # ------------------------------------------------------------------ #
    # 위치 안내 (다른 장소의 포인트를 현재 위치에서 조사하려는 경우)
    # ------------------------------------------------------------------ #
    def _wrong_location_hint(self, actor, location: str, point_arg: str,
                             point_rows: List[Dict[str, Any]],
                             exception_rows: List[Dict[str, Any]],
                             entry_rows: List[Dict[str, Any]]):
        """포인트가 현재 위치엔 없고 **지금 갈 수 있는 다른 열린 장소**에 있으면 이동 안내를 만든다.

        정보 비대칭 유지가 핵심이다:
        - 현재 위치에 그 포인트명이 **행으로라도 존재**하면(접근만 막힌 경우) None → 기존 문구.
          권한 없음을 티 내지 않는다.
        - 다른 장소에 있어도 그곳이 **닫혀 있거나(미개방·미래 콘텐츠)** 이 캐릭터가 **접근 불가**면
          None → 기존 문구. 닫힌/미래 장소의 포인트명이 브루트포스로 새지 않게 한다.
        - 이 캐릭터가 지금 진입해 조사할 수 있는 다른 장소에만 있으면 안내한다. 그건 어차피
          [장소 목록]·[진입]으로 스스로 알아낼 수 있는 정보라 새로 새는 것이 없다.
        판정/조회 실패는 조용히 None으로 폴백한다(기존 문구로).
        """
        if not getattr(config, 'INVESTIGATION_WRONG_LOCATION_HINT', True):
            return None
        try:
            if self._point_in_location(point_rows, point_arg):
                return None
            if not self._point_reachable_elsewhere(
                    actor, point_arg, location, exception_rows, entry_rows):
                return None
            logger.info(f"[조사] 다른 장소의 포인트 조사 시도 → 위치 안내: '{location}' / '{point_arg}'")
            return CommandResponse.create_success(
                self._wrong_location_message(location, point_arg),
                data={'location': location, 'point': point_arg, 'wrong_location': True},
            )
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[조사] 위치 안내 판정 실패(무시): {e}")
            return None

    @staticmethod
    def _point_in_location(point_rows: List[Dict[str, Any]], point_arg: str) -> bool:
        """현재 위치 장소 시트에 이 포인트명 행이 (개방/접근 여부와 무관하게) 있는가."""
        target = normalize_name(point_arg)
        return any(normalize_name(r.get(COL_POINT)) == target for r in point_rows)

    def _point_reachable_elsewhere(self, actor, point_arg: str, current_location: str,
                                   exception_rows: List[Dict[str, Any]],
                                   entry_rows: List[Dict[str, Any]]) -> bool:
        """현재 위치를 뺀 **열린·접근 가능** 다른 장소 중 이 포인트를 조사할 수 있는 곳이 있는가.

        닫힌 장소·미개방(미래) 장소·직군 접근 불가 장소는 세지 않는다 → 그런 포인트명이
        브루트포스로 노출되지 않는다(정보 비대칭 유지). 실패 경로에서만 도는 폴백이라
        캐시 읽기로 훑는다.
        """
        current = normalize_name(current_location)
        repo = self.repo
        for loc in repo.known_locations(entry_rows):
            if normalize_name(loc) == current:
                continue
            # 지금 열려 있는 장소만
            if not repo.open_entry_rows(loc, entry_rows):
                continue
            try:
                rows = repo.point_rows(loc)  # 캐시 사용
            except Exception:
                continue
            # 이 캐릭터가 그 장소에서 실제로 조사할 수 있는 포인트여야 한다
            if accessible_point_rows(
                    rows, exception_rows, actor.name, loc, actor.role, point=point_arg):
                return True
        return False

    def _wrong_location_message(self, location: str, point: str) -> str:
        """이동 안내 문구. config 템플릿({location}/{point})이 있으면 그것을 쓴다."""
        template = str(getattr(config, 'INVESTIGATION_MSG_WRONG_LOCATION', '') or '')
        if template.strip():
            try:
                return template.format(location=location, point=point)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[조사] 위치 안내 템플릿 치환 실패 - 기본 문구 사용: {e}")
        from utils.korean_utils import get_last_char, has_final_consonant
        josa = '은' if has_final_consonant(get_last_char(point)) else '는'
        return (
            f"여기는 '{location}'이다. '{point}'{josa} 이곳에 없다. "
            f"먼저 [진입/장소명]으로 자리를 옮긴 뒤에 살펴야 한다."
        )

    # ------------------------------------------------------------------ #
    # 헬퍼
    # ------------------------------------------------------------------ #
    def _limit_and_used(self, actor) -> Tuple[int, int]:
        """일일 한도와 오늘 사용량 (§5.2 + §8 오픈일 보정).

        오픈일 보정(INVESTIGATION_OPEN_ADJUST)은 개방 시각(21:00) 직후의 신규 조사
        러시를 허용할지에 대한 운영 선택지다. 개방 시각이 지난 개방일에만 적용된다.
        - off      : 보정 없음(기본)
        - reset    : 개방 시각 이후의 조사만 센다 → 사실상 그날 횟수 리셋
        - plus_one : 한도를 1 늘린다
        """
        limit = config_int('INVESTIGATION_DAILY_LIMIT', 2)
        mode = str(getattr(config, 'INVESTIGATION_OPEN_ADJUST', 'off') or 'off').lower()
        since = None

        if mode in ('reset', 'plus_one'):
            try:
                from utils.investigation_open import open_time_passed_today
                opened_at = open_time_passed_today()
            except Exception as e:
                logger.warning(f"[조사] 오픈일 보정 확인 실패 - 보정 없이 진행: {e}")
                opened_at = None

            if opened_at is not None:
                if mode == 'reset':
                    since = opened_at
                else:
                    limit += 1
                logger.debug(f"[조사] 오픈일 보정 적용: mode={mode}, 개방={opened_at}")

        used = count_today_investigations(
            self.investigation_sheets_manager, actor.name, since=since)
        return limit, used

    def _counter_update(self, actor, new_value: int) -> List[Tuple[int, int, Any]]:
        """`관리` 시트 `오늘조사` 카운터 미러링 (GM 트래킹용).

        판정의 기준은 로그 시트이며 이 값은 표시용이다. 로그에서 센 값을 그대로 쓰므로
        카운터가 어긋나 있어도 조사할 때마다 자동으로 교정된다.

        daily_counter 유틸을 쓰지 않는 이유: 그 함수는 자체적으로 사용자 락을 잡는데
        여기는 이미 같은 락 안이고 threading.Lock은 재진입이 불가능하다.
        같은 배치에 실어 API 호출도 아낀다.
        """
        if not getattr(config, 'INVESTIGATION_MIRROR_COUNTER', True):
            return []
        column = getattr(config, 'INVESTIGATION_COUNTER_COLUMN', '오늘조사')
        col_index = actor.column_index(column)
        if col_index is None:
            logger.debug(f"[조사] 관리 시트에 '{column}' 컬럼 없음 - 미러링 생략")
            return []
        return [(actor.row_number, col_index, new_value)]

    def _build_message(
        self,
        point_row: Dict[str, Any],
        exception_rows: List[Dict[str, Any]],
        actor,
        location: str,
        point_name: str,
        display_changes: List[str],
    ) -> str:
        """응답 조립: 포인트명 / 문구 / ➭ 변동 줄.

        ➭ 줄은 실제 발생한 변동만 출력한다. 변동이 없으면 문구만 보낸다.
        출력하는 수치는 주사위를 굴린 확정값이다(수식이 아니라).
        """
        text = self._point_text(point_row, exception_rows, actor, location, point_name)
        text = self._distort(text, actor, location, point_row)

        # 대괄호를 빼는 이유: 이 게임에서 [ ]는 '입력할 수 있는 명령어'를 뜻한다.
        # 결과 제목에 붙이면 조사 결과가 명령어처럼 보인다.
        sections = [point_name, text]
        if display_changes:
            sections.append("\n".join(display_changes))
        return "\n\n".join(sections)

    def _point_text(
        self,
        point_row: Dict[str, Any],
        exception_rows: List[Dict[str, Any]],
        actor,
        location: str,
        point_name: str,
    ) -> str:
        """'조사 시 문구'. 예외 행이 있으면 그 문구로 대체한다 (§2.3-1).

        예외는 문구와 접근만 바꾼다. 아이템·스탯·재화는 기본 행 값을 그대로 따른다.
        """
        exception = find_exception(exception_rows, actor.name, location, point_name)
        if exception is not None:
            text = display_name(exception.get(COL_EXC_TEXT))
            if text:
                logger.debug(f"[조사] 예외 문구 적용: {actor.name} '{location}'/'{point_name}'")
            else:
                # 예외 행은 있는데 문구가 비었다 → 접근 부여만 하고 기본 문구로 폴백
                logger.info(
                    f"[조사] 예외 행의 문구가 비어 있어 기본 문구 사용: "
                    f"{actor.name} '{location}'/'{point_name}'"
                )
                text = display_name(point_row.get(COL_POINT_TEXT))
        else:
            text = display_name(point_row.get(COL_POINT_TEXT))

        if not text:
            logger.warning(f"[조사] '{location}'/'{point_name}'의 조사 시 문구가 비어 있습니다.")
            self.warn_admin_once(
                f"empty-point-text:{location}:{point_name}",
                f"'{location}' 시트의 '{point_name}' 행에 조사 시 문구가 비어 있습니다.",
            )
            return f"{point_name}을(를) 살펴본다. 특별한 것은 눈에 띄지 않는다."

        try:
            return process_all_custom_substitutions(text, actor.name)
        except Exception as e:
            logger.warning(f"[조사] 템플릿 치환 실패 - 원문 반환: {e}")
            return text

    def _distort(self, text: str, actor, location: str, point_row: Dict[str, Any]) -> str:
        """이성 수치에 따른 왜곡(마스킹/오정보)을 조사 문구에 적용.

        서술 문구에만 적용한다. ➭ 변동 줄은 시트에 실제로 반영된 값이므로 왜곡하지 않는다.
        (config.INVESTIGATION_DISTORTION_ENABLED로 끌 수 있다.)
        """
        if not getattr(config, 'INVESTIGATION_DISTORTION_ENABLED', True):
            return text
        try:
            from utils import distortion
            # 후보 풀은 오정보 구간(이성이 아주 낮을 때)에서만 쓰인다.
            # 인자로 즉시 평가하면 이성 100인 러너의 조사마다 진입 시트를 읽게 된다.
            if not self._needs_distortion(actor.sanity):
                return text
            distorted, changes = distortion.apply(
                text, actor.sanity, candidates=self._distortion_candidates(location)
            )
            if changes:
                logger.info(
                    f"[조사] 오정보 치환: {actor.name}(이성={actor.sanity}) "
                    f"'{location}' → {changes}"
                )
            return distorted
        except Exception as e:
            logger.warning(f"[조사] 왜곡 적용 실패 - 원문 사용: {e}")
            return text

    @staticmethod
    def _needs_distortion(sanity) -> bool:
        """이 이성 수치에서 왜곡이 일어나는가 (distortion.apply와 같은 임계).

        왜곡이 없을 구간이면 후보 풀(진입 시트 읽기)을 만들 필요가 없다.
        """
        try:
            value = float(sanity)
        except (TypeError, ValueError):
            return False  # 수치를 모르면 distortion도 원문을 유지한다
        return value <= int(getattr(config, 'SANITY_DISTORTION_THRESHOLD', 40) or 40)

    def _distortion_candidates(self, location: str) -> List[str]:
        """오정보 치환에 쓸 후보 풀 — 이 마을의 다른 장소명.

        같은 세계관 어휘로 바꿔야 러너가 '그럴듯한 거짓'으로 받아들인다.
        """
        try:
            return [n for n in self.repo.known_locations()
                    if normalize_name(n) != normalize_name(location)]
        except Exception:
            return []

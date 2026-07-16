"""
시너몬트 스케줄러 (utils/shinarmont_scheduler.py)

docs/코딩_계획.md §5 유틸 계약:
    - start(sheets_manager, system_sheets_manager, api, owner: bool)
    - 잡: daily_reset(카운터+감소+이성문구스윕), weekly_rumor
    - apscheduler(BackgroundScheduler, KST 크론), OperationPeriodMonitor 스레드 패턴 참고

동작:
    - owner=False 이면 아무것도 하지 않는다(멀티 슬롯 중복 실행 방지).
      SCHEDULER_OWNER=True 인 단일 슬롯에서만 잡을 등록한다.
    - owner=True 이면 BackgroundScheduler(KST)를 기동한다.
        * 매일 00:00 KST → run_daily_reset
            - stat_gate.daily_decay_all (능력치 감소 + 이성문구 스윕)
            - game_state.get_game_state().purge_stale() (지난 기록 정리 — 판정과 무관)
        * 매주 (config.RUMOR_WEEKDAY 또는 `설정` 시트 소문 요일) 지정 시각 → run_weekly_rumor
            - utils.rumor.send_weekly_rumor

설계 원칙:
    - 잡 함수(run_daily_reset / run_weekly_rumor)는 모듈 함수로 분리하여
      스케줄러 없이도 수동(테스트/GM 트리거) 호출이 가능하다.
    - 의존 유틸(stat_gate, game_state, daily_counter, rumor)은 병렬 개발 대상이므로
      import 를 함수 내부로 지연(lazy)하여 순환/부재로 인한 기동 실패를 방지한다.
      개별 잡 단계는 각각 try/except 로 격리하여 한 단계 실패가 다른 단계를 막지 않는다.
"""

from typing import Optional

import pytz

from utils.logging_config import logger

# KST 타임존
KST = pytz.timezone('Asia/Seoul')

# 스케줄러 싱글턴 (중복 기동 방지)
_scheduler = None

# 한국어 요일 → apscheduler day_of_week 표기
_KOREAN_WEEKDAY = {
    '월': 'mon', '화': 'tue', '수': 'wed', '목': 'thu',
    '금': 'fri', '토': 'sat', '일': 'sun',
}
# 영문/숫자 관용 표기도 허용
_ALIAS_WEEKDAY = {
    'mon': 'mon', 'tue': 'tue', 'wed': 'wed', 'thu': 'thu',
    'fri': 'fri', 'sat': 'sat', 'sun': 'sun',
    'monday': 'mon', 'tuesday': 'tue', 'wednesday': 'wed', 'thursday': 'thu',
    'friday': 'fri', 'saturday': 'sat', 'sunday': 'sun',
    # 숫자(월=0 … 일=6, apscheduler 규칙과 동일)
    '0': 'mon', '1': 'tue', '2': 'wed', '3': 'thu', '4': 'fri', '5': 'sat', '6': 'sun',
}

_DEFAULT_WEEKDAY = 'sat'


# --------------------------------------------------------------------------- #
# 잡 함수 (모듈 함수 — 수동 호출 가능)
# --------------------------------------------------------------------------- #
def run_daily_reset(sheets_manager=None, system_sheets_manager=None, api=None) -> None:
    """
    매일 00:00 KST 일일 리셋.

    단계별로 격리(try/except)하여 한 단계 실패가 나머지를 막지 않도록 한다.
    스케줄러 없이 수동으로도 호출 가능(테스트/GM 트리거).

    1) 능력치 일일 감소 + 이성문구 스윕 (stat_gate.daily_decay_all)
    2) 봇 JSON 지난 기록 정리 (purge_stale — 용량 관리일 뿐, 일일 제한 판정과 무관)

    `관리` 시트의 `추적`·`조사`·`출석` 컬럼은 **별도 스케줄러 봇**이 00:00에 초기화한다.
    이 봇은 구글 시트를 건드리지 않는다 — 양쪽이 같은 셀을 리셋하면 한쪽이 이긴 결과를
    다른 쪽이 덮어쓰고, 실패해도 어느 봇 탓인지 알 수 없다. 리셋 주체는 하나여야 한다.
    """
    logger.info("[시너몬트 스케줄러] KST 0시 일일 리셋 시작")

    # 1) 능력치 감소 + 이성문구 스윕
    try:
        from utils import stat_gate
        stat_gate.daily_decay_all(sheets_manager, system_sheets_manager, api)
        logger.info("[시너몬트 스케줄러] 능력치 일일 감소/이성문구 스윕 완료")
    except Exception as e:
        logger.error(f"[시너몬트 스케줄러] 능력치 감소/이성문구 스윕 실패: {e}")

    # 2) 봇 JSON 상태 — **리셋할 것이 없다**.
    #    `오늘*` 키는 날짜 스탬프로 자동 만료되고, '어제대화상대'는 '오늘대화상대'의
    #    날짜에서 파생된다(utils/game_state.py). 예전의 carry/reset 잡은 폐기됐다.
    #    (스케줄러는 BOT1 프로세스의 싱글톤만 리셋해서 @STORY·@DOCTOR에는 닿지 않았다)
    #    여기서는 지난 날짜 기록만 정리해 파일이 계속 커지는 것을 막는다.
    try:
        from utils import game_state
        removed = game_state.get_game_state().purge_stale()
        logger.info(f"[시너몬트 스케줄러] 봇 JSON 지난 기록 정리 완료({removed}건)")
    except Exception as e:
        logger.error(f"[시너몬트 스케줄러] 봇 JSON 정리 실패: {e}")

    # 3) 관리시트 카운터 리셋은 **하지 않는다** (2026-07-16 운영 결정).
    #    `추적`·`조사`·`출석` 컬럼은 별도 스케줄러 봇이 00:00에 0/빈칸으로 되돌린다.
    #    이 봇이 같이 리셋하면 시트 쓰기가 두 배가 되고(쿼터), 경합이 생기며,
    #    무엇보다 "누가 리셋했나"를 추적할 수 없게 된다.

    logger.info("[시너몬트 스케줄러] KST 0시 일일 리셋 종료")


def run_daily_digest(sheets_manager=None, system_sheets_manager=None, api=None,
                     investigation_sheets_manager=None, day=None) -> None:
    """
    일일보고 — 하루치 사건을 모아 @NOTICE 에게 DM 타래로 보낸다(GM 소문 제작용).

    본체는 utils/daily_digest.py. 여기서는 잡으로 감싸기만 한다.
    실패해도 예외를 밖으로 내보내지 않는다 — 보고서가 안 왔다고 스케줄러가
    죽으면 0시 리셋·주간 소문까지 같이 멈춘다.
    """
    logger.info("[시너몬트 스케줄러] 일일보고 시작")
    try:
        from utils import daily_digest
        result = daily_digest.run_daily_digest(
            sheets_manager=sheets_manager,
            system_sheets_manager=system_sheets_manager,
            api=api,
            investigation_sheets_manager=investigation_sheets_manager,
            day=day,
        )
        logger.info(
            f"[시너몬트 스케줄러] 일일보고 완료: {result['day']}일차 "
            f"{result['sent']}/{result['total']}통"
        )
    except Exception as e:
        logger.error(f"[시너몬트 스케줄러] 일일보고 실패: {e}", exc_info=True)


def _resolve_digest_time():
    """일일보고 시각 (config.DIGEST_HOUR/DIGEST_MINUTE, 기본 23:30 KST)."""
    hour, minute = 23, 30
    try:
        from config.settings import config
        hour = int(getattr(config, 'DIGEST_HOUR', 23))
        minute = int(getattr(config, 'DIGEST_MINUTE', 30))
    except Exception:
        pass
    if not (0 <= hour <= 23):
        hour = 23
    if not (0 <= minute <= 59):
        minute = 30
    return hour, minute


def run_weekly_rumor(sheets_manager=None, system_sheets_manager=None, api=None) -> None:
    """
    주간 소문 처리.

    1) send_weekly_rumor: '소문' 시트의 '확정' 소문을 무작위 수신자에게 발송(상태=발송,
       매칭 행동로그 소문화=O).
    2) propose_candidates: 행동로그의 소문화 미표시 항목을 무작위 표본으로 '소문' 시트에
       후보(상태=후보)로 적재 → GM 이 다음 주 발송분으로 확정. (docs §6 AUTO)

    두 단계는 각각 격리(try/except)하여 한 단계 실패가 다른 단계를 막지 않는다.
    propose 는 기존 소문 내용/이미 소문화된 행을 건너뛰므로 반복 실행에 멱등하다.
    스케줄러 없이 수동으로도 호출 가능(테스트/GM 트리거).
    """
    logger.info("[시너몬트 스케줄러] 주간 소문 처리 시작")

    try:
        from utils import rumor
    except Exception as e:  # 모듈 부재 시 전체 격리
        logger.error(f"[시너몬트 스케줄러] utils.rumor 로드 실패 - 소문 처리 생략: {e}")
        return

    # 1) 확정 소문 발송
    try:
        rumor.send_weekly_rumor(sheets_manager, system_sheets_manager, api)
        logger.info("[시너몬트 스케줄러] 주간 소문 발송 완료")
    except Exception as e:
        logger.error(f"[시너몬트 스케줄러] 주간 소문 발송 실패: {e}")

    # 2) 다음 주 발송 후보 적재(멱등)
    try:
        rumor.propose_candidates(system_sheets_manager)
        logger.info("[시너몬트 스케줄러] 소문 후보 적재 완료")
    except Exception as e:
        logger.error(f"[시너몬트 스케줄러] 소문 후보 적재 실패: {e}")


# --------------------------------------------------------------------------- #
# 요일/시각 해석
# --------------------------------------------------------------------------- #
def _normalize_weekday(value) -> Optional[str]:
    """요일 값(한국어/영문/숫자)을 apscheduler day_of_week 표기로 변환. 실패 시 None."""
    if value is None:
        return None
    token = str(value).strip()
    if not token:
        return None
    # '토', '토요일' 등 한국어 우선
    if token[0] in _KOREAN_WEEKDAY:
        return _KOREAN_WEEKDAY[token[0]]
    low = token.lower()
    return _ALIAS_WEEKDAY.get(low)


def _resolve_rumor_weekday(system_sheets_manager=None) -> str:
    """
    소문 발송 요일 해석.

    우선순위: `설정` 시트 '소문 요일' → config.RUMOR_WEEKDAY → 기본값(토/sat).
    (docs/코딩_계획.md §1.6: 소문 발송일은 `설정.소문 요일` 우선)
    """
    # 1) 설정 시트 우선 (best-effort)
    if system_sheets_manager is not None:
        try:
            rows = system_sheets_manager.get_worksheet_data('설정', use_cache=False)
            for row in rows or []:
                for key, val in (row or {}).items():
                    k = str(key).replace(' ', '')
                    if '소문' in k and '요일' in k:
                        wd = _normalize_weekday(val)
                        if wd:
                            logger.info(f"[시너몬트 스케줄러] 소문 요일=설정시트({val}) → {wd}")
                            return wd
        except Exception as e:
            logger.warning(f"[시너몬트 스케줄러] 설정 시트 소문 요일 조회 실패(폴백): {e}")

    # 2) config 폴백
    try:
        from config.settings import config
        wd = _normalize_weekday(getattr(config, 'RUMOR_WEEKDAY', None))
        if wd:
            logger.info(f"[시너몬트 스케줄러] 소문 요일=config.RUMOR_WEEKDAY → {wd}")
            return wd
    except Exception as e:
        logger.warning(f"[시너몬트 스케줄러] config.RUMOR_WEEKDAY 조회 실패(기본값 사용): {e}")

    logger.info(f"[시너몬트 스케줄러] 소문 요일 기본값 사용 → {_DEFAULT_WEEKDAY}")
    return _DEFAULT_WEEKDAY


def _resolve_rumor_time():
    """소문 발송 시각 (config.RUMOR_HOUR/RUMOR_MINUTE, 기본 21:00 KST)."""
    hour, minute = 21, 0
    try:
        from config.settings import config
        hour = int(getattr(config, 'RUMOR_HOUR', 21) or 21)
        minute = int(getattr(config, 'RUMOR_MINUTE', 0) or 0)
    except Exception:
        pass
    # 방어적 범위 보정
    if not (0 <= hour <= 23):
        hour = 21
    if not (0 <= minute <= 59):
        minute = 0
    return hour, minute


# --------------------------------------------------------------------------- #
# start / stop
# --------------------------------------------------------------------------- #
def start(sheets_manager=None, system_sheets_manager=None, api=None, owner: bool = False,
          investigation_sheets_manager=None):
    """
    스케줄러 시작.

    Args:
        sheets_manager: 기본 스프레드시트 매니저
        system_sheets_manager: 시스템 스프레드시트 매니저
        api: 마스토돈 API
        owner: True 인 슬롯에서만 잡을 등록/기동(멀티 슬롯 중복 방지). False 면 no-op.
        investigation_sheets_manager: 조사 스프레드시트 매니저.
            주어지면 조사 자동 개방 잡(가이드 §6)을 함께 등록한다.

    Returns:
        BackgroundScheduler 인스턴스(기동 성공) 또는 None(owner=False/실패).
    """
    global _scheduler

    if not owner:
        logger.info("[시너몬트 스케줄러] owner=False - 스케줄러 기동 생략")
        return None

    if _scheduler is not None and getattr(_scheduler, 'running', False):
        logger.warning("[시너몬트 스케줄러] 이미 실행 중 - 재기동 생략")
        return _scheduler

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError as e:
        logger.error(f"[시너몬트 스케줄러] apscheduler 미설치 - 기동 실패: {e}")
        return None

    scheduler = BackgroundScheduler(timezone=KST)

    # 매일 00:00 KST - 일일 리셋
    scheduler.add_job(
        run_daily_reset,
        trigger=CronTrigger(hour=0, minute=0, timezone=KST),
        kwargs={
            'sheets_manager': sheets_manager,
            'system_sheets_manager': system_sheets_manager,
            'api': api,
        },
        id='daily_reset',
        name='시너몬트 일일 리셋',
        replace_existing=True,
        misfire_grace_time=3600,
        coalesce=True,
    )

    # 매주 지정 요일/시각 - 주간 소문
    weekday = _resolve_rumor_weekday(system_sheets_manager)
    hour, minute = _resolve_rumor_time()
    scheduler.add_job(
        run_weekly_rumor,
        trigger=CronTrigger(day_of_week=weekday, hour=hour, minute=minute, timezone=KST),
        kwargs={
            'sheets_manager': sheets_manager,
            'system_sheets_manager': system_sheets_manager,
            'api': api,
        },
        id='weekly_rumor',
        name='시너몬트 주간 소문',
        replace_existing=True,
        misfire_grace_time=3600,
        coalesce=True,
    )

    # 매일 지정 시각(기본 23:30 KST) - 일일보고 → @NOTICE DM 타래
    # 자정 전에 보낸다. 00:00 리셋보다 뒤면 GM이 '현재 일차'를 넘긴 뒤라
    # 엉뚱한 날을 보고하게 된다.
    digest_hour, digest_minute = _resolve_digest_time()
    scheduler.add_job(
        run_daily_digest,
        trigger=CronTrigger(hour=digest_hour, minute=digest_minute, timezone=KST),
        kwargs={
            'sheets_manager': sheets_manager,
            'system_sheets_manager': system_sheets_manager,
            'investigation_sheets_manager': investigation_sheets_manager,
            'api': api,
        },
        id='daily_digest',
        name='시너몬트 일일보고',
        replace_existing=True,
        misfire_grace_time=3600,
        coalesce=True,
    )

    # 조사 자동 개방 (가이드 §6) — 오픈 일자마다 단발성(DateTrigger) 잡
    opens = _add_investigation_open_jobs(scheduler, investigation_sheets_manager, api)

    try:
        scheduler.start()
    except Exception as e:
        logger.error(f"[시너몬트 스케줄러] 기동 실패: {e}")
        return None

    _scheduler = scheduler
    logger.info(
        f"[시너몬트 스케줄러] 기동 완료 (일일 리셋 00:00 KST, "
        f"주간 소문 {weekday} {hour:02d}:{minute:02d} KST, "
        f"조사 개방 예정 {opens}건)"
    )

    # 놓친 개방 감지는 잡 등록 이후에 수행한다(기동 실패 시 알림만 보내고 끝나지 않도록).
    if investigation_sheets_manager is not None:
        try:
            from utils.investigation_open import handle_missed_opens
            handle_missed_opens(investigation_sheets_manager, api=api)
        except Exception as e:
            logger.error(f"[시너몬트 스케줄러] 놓친 조사 개방 처리 실패: {e}")

    return scheduler


def _add_investigation_open_jobs(scheduler, investigation_sheets_manager, api) -> int:
    """조사 오픈 일자별 개방 잡을 등록한다 (가이드 §6.2).

    각 일차는 정확히 그 시각에 한 번만 실행한다(DateTrigger). 주기적 동기화를 하지 않는
    이유는 운영진의 수동 조작을 되돌리면 안 되기 때문이다 (§6.3-2).
    이미 지나간 일차는 등록하지 않는다 — 놓친 실행은 handle_missed_opens가 따로 처리한다.

    Returns:
        등록된 잡 개수.
    """
    if investigation_sheets_manager is None:
        return 0

    try:
        from apscheduler.triggers.date import DateTrigger
        from utils.investigation_open import run_open, scheduled_opens
    except ImportError as e:
        logger.error(f"[시너몬트 스케줄러] 조사 개방 모듈 로드 실패: {e}")
        return 0

    from datetime import datetime
    now = datetime.now(KST)
    count = 0

    for label, when in scheduled_opens():
        if when <= now:
            continue  # 지난 일정은 handle_missed_opens 담당
        try:
            scheduler.add_job(
                run_open,
                trigger=DateTrigger(run_date=when, timezone=KST),
                kwargs={
                    'investigation_sheets_manager': investigation_sheets_manager,
                    'day_label': label,
                    'api': api,
                },
                id=f'investigation_open:{label}',
                name=f'조사 개방 {label}',
                replace_existing=True,
                misfire_grace_time=3600,
                coalesce=True,
            )
            count += 1
            logger.info(f"[시너몬트 스케줄러] 조사 개방 예약: {label} @ {when:%Y-%m-%d %H:%M} KST")
        except Exception as e:
            logger.error(f"[시너몬트 스케줄러] 조사 개방 잡 등록 실패({label}): {e}")

    return count


def stop() -> None:
    """스케줄러 중지(테스트/종료용)."""
    global _scheduler
    if _scheduler is not None:
        try:
            if getattr(_scheduler, 'running', False):
                _scheduler.shutdown(wait=False)
            logger.info("[시너몬트 스케줄러] 중지 완료")
        except Exception as e:
            logger.error(f"[시너몬트 스케줄러] 중지 실패: {e}")
        finally:
            _scheduler = None


def get_scheduler():
    """현재 스케줄러 인스턴스 반환(없으면 None)."""
    return _scheduler

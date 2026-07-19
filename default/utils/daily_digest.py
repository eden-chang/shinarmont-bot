"""
utils/daily_digest.py — 일일보고 (시너몬트 · GM 전용)

하루치 사건을 모아 @SYSTEM → @NOTICE 로 **DM 타래**로 보낸다.
목적은 하나다: GM이 소문을 짓기 쉽게.

구조
----
    digest_facts   사실 수집 (시트 join, AI 없음)
    digest_report  문서 조립 + 4800자 분할
    ai_client      소문 씨앗 (AI, §7만)
    daily_digest   ← 여기: 묶어서 타래로 전송

사실과 각색을 섞지 않는다
-------------------------
§1~6(팩트 시트·관계망·고발·지령·투표·조용한 사람)은 전부 시트에서 나온다.
§7(소문 씨앗)만 AI가 쓰고, 'AI 제안 — 사실 아님'을 머리에 박는다.
AI가 죽어도 §1~6은 그대로 간다 — 보고서의 값어치는 사실 쪽에 있다.

인용문 속 계정 태그 (2026-07-19 사고)
--------------------------------------
보고서는 시트·슬롯 JSON·AI가 쓴 **남의 글**을 인용한다. 그 안의 `@계정`을 그대로
툿하면 GM에게만 가야 할 DM이 인용된 플레이어 전원을 호출한다. 실제로 그랬다.
막는 지점을 셋으로 나눴다 — 새 필드가 생겨도 어느 하나에는 걸린다:

    1단계  digest_facts._text   시트 자유 서술 필드를 읽을 때
    2단계  digest_report._finalize  완성된 보고서 전문
    3단계  daily_digest.send_thread  통마다 '@' 전멸 + 수신자 멘션만 재부착

3단계가 최후의 보루다. 상세는 utils/mention_guard.py.

타래로 보내는 이유
------------------
상세한 보고서는 한 통에 안 들어간다. 여러 통을 따로 쏘면 GM 타임라인에 흩어지고
순서도 섞인다. `in_reply_to_id` 로 이어붙이면 한 덩어리로 읽힌다.
중간 한 통이 실패하면 **거기서 멈춘다** — 이어붙일 앵커를 잃은 채로 계속 쏘면
조각들이 흩어져서 오히려 더 읽기 어렵다.
"""

import os
import sys
from typing import Any, Dict, List, Optional

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from utils.logging_config import logger
from utils import digest_facts, digest_report, mention_guard


def _cfg(key: str, default):
    try:
        from config.settings import config
        return getattr(config, key, default)
    except Exception:
        return default


def _current_day() -> Optional[int]:
    try:
        from utils.game_day import current_day
        return int(current_day())
    except Exception as e:
        logger.warning(f"[일일보고] 현재 일차를 구하지 못했습니다: {e}")
        return None


def _seeds(facts_text: str, count: int) -> Optional[Dict[str, Any]]:
    """소문 씨앗(AI). 실패는 삼킨다 — 사실 절만으로도 보고서는 성립한다."""
    if not _cfg('DIGEST_AI_SEEDS', True):
        return None
    try:
        from utils import ai_client
        return ai_client.rumor_seeds(facts_text, count=count)
    except Exception as e:
        logger.warning(f"[일일보고] 소문 씨앗 생성 실패(사실 절만 발송): {e}")
        return None


def send_thread(api, recipient: str, chunks: List[str]) -> Dict[str, Any]:
    """
    조각들을 DM 타래로 이어 보낸다.

    Args:
        api: 마스토돈 API (status_post 필요). None 이면 발송 없이 0건 반환.
        recipient: 수신 계정(acct, '@' 없이).
        chunks: 이미 분할·번호매김이 끝난 조각들.

    Returns:
        {'sent': 보낸 통 수, 'total': 전체 통 수, 'root_id': 첫 통 id}
    """
    result = {'sent': 0, 'total': len(chunks), 'root_id': None}
    if api is None:
        logger.warning("[일일보고] api 없음 - 발송 생략")
        return result
    if not recipient:
        logger.warning("[일일보고] 수신 계정이 설정되지 않음(DIGEST_RECIPIENT_ID/SYSTEM_ADMIN_ID)")
        return result

    reply_to = None
    for i, chunk in enumerate(chunks, 1):
        # 3단계(최후) 안전망: 본문의 '@'를 남김없이 죽이고, 수신자 멘션만 새로 붙인다.
        # 앞 단계를 다 빠져나온 '@'가 있어도 여기서 알림이 나가지 못한다.
        # 이 순서를 뒤집지 말 것 — 붙인 다음 defang 하면 수신자 멘션까지 죽는다.
        body = mention_guard.defang_all(chunk)
        if mention_guard.has_live_mention(chunk):
            logger.warning(
                f"[일일보고] {i}통에 살아 있는 멘션이 남아 발송 직전에 무력화했습니다 "
                f"— 앞단(digest_facts/digest_report)에 구멍이 있습니다"
            )
        try:
            status = api.status_post(
                status=f"@{recipient} {body}",
                visibility='direct',
                in_reply_to_id=reply_to,
            )
        except Exception as e:
            # 여기서 멈춘다. 앵커를 잃은 채 계속 쏘면 조각이 흩어져 더 못 읽는다.
            logger.error(
                f"[일일보고] {i}/{len(chunks)} 통째 발송 실패 - 타래 중단: {e}"
            )
            return result

        status_id = None
        if isinstance(status, dict):
            status_id = status.get('id')
        elif status is not None:
            status_id = getattr(status, 'id', None)

        if status_id is None and i < len(chunks):
            # 답글 대상 id를 못 얻으면 다음 통이 타래에 안 붙는다. 그래도 계속 보낸다 —
            # 흩어진 조각이라도 없는 것보단 낫다(번호표가 붙어 있다).
            logger.warning(f"[일일보고] {i}번째 통의 id를 얻지 못함 - 이후는 타래가 끊깁니다")

        if i == 1:
            result['root_id'] = status_id
        reply_to = status_id or reply_to
        result['sent'] += 1

    return result


def run_daily_digest(sheets_manager=None,
                     system_sheets_manager=None,
                     api=None,
                     investigation_sheets_manager=None,
                     day: Optional[int] = None) -> Dict[str, Any]:
    """
    일일보고 생성 + 발송. 스케줄러 잡이자 수동 트리거 대상.

    Args:
        day: 보고할 일차. None 이면 '설정' 시트의 현재 일차.

    Returns:
        {'day','chunks','sent','total','report'} — 'report' 는 전문(테스트/로그용).
    """
    if not _cfg('DIGEST_ENABLED', True):
        logger.info("[일일보고] DIGEST_ENABLED=False - 생략")
        return {'day': day, 'chunks': 0, 'sent': 0, 'total': 0, 'report': ''}

    if day is None:
        day = _current_day()
    if day is None:
        logger.error("[일일보고] 일차를 알 수 없어 보고를 중단합니다")
        return {'day': None, 'chunks': 0, 'sent': 0, 'total': 0, 'report': ''}

    logger.info(f"[일일보고] {day}일차 수집 시작")
    facts = digest_facts.fetch_facts(
        day,
        sheets_manager=sheets_manager,
        system_sheets_manager=system_sheets_manager,
        investigation_sheets_manager=investigation_sheets_manager,
    )

    # 소문 씨앗은 사실 절을 재료로 만든다. 사실 절을 먼저 조립해서 그대로 먹인다 —
    # AI에게 시트 원본을 주면 없는 사건을 지어낼 여지가 생긴다.
    facts_only = digest_report.build_report(facts, seeds=None)
    seeds = None
    if facts.get('actions'):
        seeds = _seeds(facts_only, int(_cfg('DIGEST_SEED_COUNT', 8)))

    report = digest_report.build_report(facts, seeds=seeds)

    recipient = str(_cfg('DIGEST_RECIPIENT_ID', '') or _cfg('SYSTEM_ADMIN_ID', '') or '').strip()
    header = f"[일일보고] {day}일차"
    # 멘션('@계정 ')과 머리표('[일일보고] N일차 (n/N)\n')가 본문 밖에서 자리를 먹는다.
    # 예산에서 미리 빼지 않으면 딱 맞춘 통이 한도를 넘어 실패한다.
    reserve = len(recipient) + 2 + len(header) + len(" (99/99)") + 1

    chunks = digest_report.chunk_report(
        report,
        limit=int(_cfg('DIGEST_CHUNK_LIMIT', digest_report.DEFAULT_CHUNK_LIMIT)),
        reserve=reserve,
    )
    chunks = digest_report.paginate(chunks, header=header)

    sent = send_thread(api, recipient, chunks)
    logger.info(
        f"[일일보고] {day}일차 발송: {sent['sent']}/{sent['total']}통 "
        f"(사건 {len(facts.get('actions') or [])}건, 씨앗 {len((seeds or {}).get('seeds') or [])}개)"
    )
    return {
        'day': day,
        'chunks': len(chunks),
        'sent': sent['sent'],
        'total': sent['total'],
        'report': report,
    }

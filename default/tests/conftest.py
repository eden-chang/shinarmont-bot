"""테스트 전역 안전장치.

**테스트는 실제 Claude API를 호출하지 않는다.**

배경(2026-07-16): `.env`에 ANTHROPIC_API_KEY가 채워지자 전체 테스트가 2.4초 → 23초로
느려졌다. `utils/distortion`·`utils/rumor`·의무실이 **실제 API를 때리고 있었다**.
크레딧을 태우고, 결과가 비결정적이 되고, 레이트리밋에 걸리면 빨개진다.

`tests/test_distortion.py`에는 "AI 미개입(폴백) 경로를 강제"라는 주석이 있었지만
정작 강제하는 코드가 없었다(random.seed만 불렀다). 그 의도를 여기서 실제로 지킨다.

AI 동작 자체를 검증하는 테스트는 자기 setUp에서 **가짜 클라이언트를 주입**한다
(이 픽스처보다 나중에 돌아서 덮어쓴다). 진짜 API로 나가는 경로만 막힌다.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def _no_live_ai_calls():
    """AI 클라이언트를 None으로 고정 → 모든 AI 경로가 규칙 폴백으로 내려간다.

    `get_ai_client()`는 `_client_init_attempted`가 True면 `_client`를 그대로 돌려준다.
    둘을 함께 세팅해 **키가 있어도 클라이언트를 만들지 않게** 한다.
    """
    try:
        from utils import ai_client
    except ImportError:  # pragma: no cover - AI 모듈이 없는 환경
        yield
        return

    saved = (ai_client._client, ai_client._client_init_attempted)
    ai_client._client = None
    ai_client._client_init_attempted = True
    try:
        yield
    finally:
        ai_client._client, ai_client._client_init_attempted = saved

"""utils/ai_client.py 단위 테스트.

anthropic SDK 미설치/미초기화 시 None 폴백 경로와, 가짜 클라이언트를
주입한 성공 경로(마스킹/오정보/의사)를 검증한다.
"""

import json
import pytest

import utils.ai_client as ai


# ---------------------------------------------------------------------
# 가짜 응답/클라이언트
# ---------------------------------------------------------------------

class _FakeBlock:
    def __init__(self, text, btype="text"):
        self.type = btype
        self.text = text


class _FakeResp:
    def __init__(self, blocks):
        self.content = blocks


class _FakeMessages:
    def __init__(self, resp=None, exc=None):
        self._resp = resp
        self._exc = exc
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        if self._exc is not None:
            raise self._exc
        return self._resp


class _FakeClient:
    def __init__(self, resp=None, exc=None):
        self.messages = _FakeMessages(resp=resp, exc=exc)


@pytest.fixture
def inject_client(monkeypatch):
    """가짜 클라이언트를 주입하는 헬퍼. get_ai_client()가 이를 반환하게 한다."""
    def _inject(resp=None, exc=None):
        client = _FakeClient(resp=resp, exc=exc)
        monkeypatch.setattr(ai, "_client", client)
        monkeypatch.setattr(ai, "_client_init_attempted", True)
        return client
    return _inject


@pytest.fixture
def no_client(monkeypatch):
    """클라이언트를 None으로 강제(미초기화/미설치 시뮬레이션)."""
    monkeypatch.setattr(ai, "_client", None)
    monkeypatch.setattr(ai, "_client_init_attempted", True)


# ---------------------------------------------------------------------
# None 폴백 경로 (클라이언트 없음)
# ---------------------------------------------------------------------

def test_distort_mask_none_without_client(no_client):
    assert ai.distort_mask("원문 텍스트", 0.5) is None


def test_distort_false_none_without_client(no_client):
    assert ai.distort_false("원문", ["김철수", "학교"]) is None


def test_doctor_reply_none_without_client(no_client):
    assert ai.doctor_reply([], {"이름": "홍길동"}, "안녕하세요") is None


# ---------------------------------------------------------------------
# 빈 입력 방어
# ---------------------------------------------------------------------

def test_distort_mask_empty_text(inject_client):
    inject_client(resp=_FakeResp([_FakeBlock("무시됨")]))
    assert ai.distort_mask("", 0.5) is None


def test_doctor_reply_empty_utterance(inject_client):
    inject_client(resp=_FakeResp([_FakeBlock("무시됨")]))
    assert ai.doctor_reply([], {"이름": "홍길동"}, "") is None


# ---------------------------------------------------------------------
# 마스킹 성공
# ---------------------------------------------------------------------

def test_distort_mask_success(inject_client):
    client = inject_client(resp=_FakeResp([_FakeBlock("  ▓▓는 …에서 무언가를 보았다.  ")]))
    out = ai.distort_mask("철수는 학교에서 편지를 보았다.", 0.7)
    assert out == "▓▓는 …에서 무언가를 보았다."
    # 마스킹 모델이 사용되었는지 확인
    assert client.messages.last_kwargs["model"] == getattr(ai.config, "AI_MASK_MODEL", "claude-haiku-4-5")
    # 강도가 프롬프트에 포함되는지
    assert "0.70" in client.messages.last_kwargs["messages"][0]["content"]


def test_distort_mask_empty_response_returns_none(inject_client):
    inject_client(resp=_FakeResp([_FakeBlock("   ")]))
    assert ai.distort_mask("원문", 0.5) is None


# ---------------------------------------------------------------------
# 오정보 치환 성공/실패
# ---------------------------------------------------------------------

def test_distort_false_success(inject_client):
    payload = {"distorted": "이상하다… 영희는 도서관에서 칼을 보았다.", "changes": ["철수→영희", "편지→칼"]}
    client = inject_client(resp=_FakeResp([_FakeBlock(json.dumps(payload, ensure_ascii=False))]))
    result = ai.distort_false("철수는 학교에서 편지를 보았다.", ["영희", "도서관", "칼"])
    assert result is not None
    distorted, changes = result
    assert distorted == payload["distorted"]
    assert changes == ["철수→영희", "편지→칼"]
    # 구조화 출력(output_config)이 전달되는지
    assert "output_config" in client.messages.last_kwargs
    assert client.messages.last_kwargs["output_config"]["format"]["type"] == "json_schema"


def test_distort_false_bad_json_returns_none(inject_client):
    inject_client(resp=_FakeResp([_FakeBlock("이건 JSON이 아님")]))
    assert ai.distort_false("원문", []) is None


def test_distort_false_missing_distorted_returns_none(inject_client):
    inject_client(resp=_FakeResp([_FakeBlock(json.dumps({"changes": ["a"]}))]))
    assert ai.distort_false("원문", []) is None


def test_distort_false_none_candidates(inject_client):
    payload = {"distorted": "왜곡", "changes": []}
    client = inject_client(resp=_FakeResp([_FakeBlock(json.dumps(payload, ensure_ascii=False))]))
    result = ai.distort_false("원문", None)
    assert result == ("왜곡", [])
    # candidates None 이어도 크래시 없이 빈 후보 풀로 처리
    assert "[후보 풀] " in client.messages.last_kwargs["messages"][0]["content"]


# ---------------------------------------------------------------------
# 의사 NPC 성공
# ---------------------------------------------------------------------

def test_doctor_reply_success(inject_client):
    payload = {"reply": "의사는 당신의 맥을 짚으며 말했다. 좀 어떠십니까?", "concluding": False}
    client = inject_client(resp=_FakeResp([_FakeBlock(json.dumps(payload, ensure_ascii=False))]))
    history = [{"role": "user", "content": "이전 발화"}, {"role": "assistant", "content": "이전 답변"}]
    out = ai.doctor_reply(
        history, {"이름": "홍길동", "건강": 30, "이성": 55, "입원": True}, "머리가 아파요", turn_no=2
    )
    # chart 는 마무리 턴에만 채워진다 → 진행 중인 턴에서는 빈 문자열
    assert out == {"reply": "의사는 당신의 맥을 짚으며 말했다. 좀 어떠십니까?",
                   "concluding": False, "chart": ""}
    # 구조화 출력(json_schema)로 reply+concluding 판정
    assert client.messages.last_kwargs["output_config"]["format"]["type"] == "json_schema"
    # 의사 모델 사용
    assert client.messages.last_kwargs["model"] == getattr(ai.config, "AI_DOCTOR_MODEL", "claude-sonnet-5")
    # 이력 + 이번 발화가 messages로 전달
    msgs = client.messages.last_kwargs["messages"]
    assert msgs[-1] == {"role": "user", "content": "머리가 아파요"}
    assert len(msgs) == 3
    # 환자 컨텍스트 + 진행(턴) 안내가 시스템 프롬프트 마지막 블록에 주입
    system = client.messages.last_kwargs["system"]
    assert "홍길동" in system[-1]["text"]
    assert "입원 예" in system[-1]["text"]
    assert "2번째 발화" in system[-1]["text"]   # turn_no 반영
    # 고정 페르소나 블록에 cache_control
    assert system[0]["cache_control"] == {"type": "ephemeral"}


def test_doctor_reply_concluding_true(inject_client):
    payload = {"reply": "오늘은 여기까지 하죠.", "concluding": True}
    inject_client(resp=_FakeResp([_FakeBlock(json.dumps(payload, ensure_ascii=False))]))
    out = ai.doctor_reply([], {"이름": "밥"}, "감사합니다.", turn_no=5)
    assert out["concluding"] is True
    assert out["reply"] == "오늘은 여기까지 하죠."


def test_doctor_reply_bad_json_returns_none(inject_client):
    inject_client(resp=_FakeResp([_FakeBlock("이건 JSON이 아님")]))
    assert ai.doctor_reply([], {"이름": "밥"}, "안녕하세요", turn_no=2) is None


def _doctor_ctx_text(client):
    """마지막 doctor_reply 호출의 환자 컨텍스트(system 마지막 블록) 텍스트."""
    return client.messages.last_kwargs["system"][-1]["text"]


def test_doctor_reply_context_includes_job_day_guide(inject_client):
    """직군·일차·관찰 지침이 환자 컨텍스트에 실린다(이번 세션 신규 필드)."""
    client = inject_client(resp=_FakeResp([_FakeBlock("그렇군요.")]))
    ai.doctor_reply(
        [],
        {"이름": "엘리스", "직군": "광부", "건강": 30, "이성": 25, "입원": False, "일차": 7},
        "머리가 아파요",
    )
    ctx = _doctor_ctx_text(client)
    assert "직군 광부" in ctx
    assert "7일차" in ctx
    assert "관찰 지침" in ctx
    # 환자(가변) 블록은 캐시 breakpoint 뒤 → cache_control 없음
    assert "cache_control" not in client.messages.last_kwargs["system"][-1]


def test_doctor_reply_context_omits_day_when_absent(inject_client):
    """일차가 없으면 '일차' 표기를 넣지 않는다."""
    client = inject_client(resp=_FakeResp([_FakeBlock("네.")]))
    ai.doctor_reply([], {"이름": "밥", "건강": 50, "이성": 50}, "안녕하세요")
    assert "일차" not in _doctor_ctx_text(client)


def test_doctor_reply_context_job_defaults_to_unknown(inject_client):
    """직군이 비면 '미상'으로 채운다(크래시 없이)."""
    client = inject_client(resp=_FakeResp([_FakeBlock("네.")]))
    ai.doctor_reply([], {"이름": "밥", "직군": "  ", "건강": 50, "이성": 50}, "안녕하세요")
    assert "직군 미상" in _doctor_ctx_text(client)


def test_doctor_persona_has_format_rules():
    """페르소나에 이번 세션에서 넣은 형식/분량/버릇 규칙이 존재한다(회귀 방지)."""
    persona = ai.DOCTOR_PERSONA[0]["text"]
    for marker in ("[분량]", "[버릇·제스처]", "[말버릇]", "[묘사의 결]",
                   "완성된 한 문장", "연결어미", "300~600", "`···`"):
        assert marker in persona, marker
    # 페르소나 고정 블록은 캐시 대상
    assert ai.DOCTOR_PERSONA[0]["cache_control"] == {"type": "ephemeral"}


# ---------------------------------------------------------------------
# 처치 판정 (대화 종료 시 결과)
# ---------------------------------------------------------------------

def test_doctor_treatment_none_without_client(no_client):
    assert ai.doctor_treatment([], {"이름": "홍길동"}) is None


def test_doctor_treatment_success(inject_client):
    payload = {"treatment": "상담", "health_delta": 2, "sanity_delta": 6, "reason": "차분한 상담이었다."}
    client = inject_client(resp=_FakeResp([_FakeBlock(json.dumps(payload, ensure_ascii=False))]))
    history = [
        {"role": "user", "content": "요즘 잠을 못 자요"},
        {"role": "assistant", "content": "그렇군요."},
    ]
    out = ai.doctor_treatment(history, {"이름": "홍길동", "직군": "광부", "건강": 40, "이성": 30})
    assert out == {"treatment": "상담", "health_delta": 2, "sanity_delta": 6, "reason": "차분한 상담이었다."}
    # 구조화 출력(json_schema)로 판정
    assert client.messages.last_kwargs["output_config"]["format"]["type"] == "json_schema"
    # 의사 모델 사용 + 대화록이 프롬프트에 직렬화
    assert client.messages.last_kwargs["model"] == getattr(ai.config, "AI_DOCTOR_MODEL", "claude-sonnet-5")
    content = client.messages.last_kwargs["messages"][0]["content"]
    assert "요즘 잠을 못 자요" in content
    assert "직군 광부" in content


def test_doctor_treatment_bad_json_returns_none(inject_client):
    inject_client(resp=_FakeResp([_FakeBlock("이건 JSON이 아님")]))
    assert ai.doctor_treatment([], {"이름": "홍길동"}) is None

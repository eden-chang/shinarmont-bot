# AI(Claude API) 활용 설계 — 마스킹 · 오정보 치환 · 의사 NPC

> 시너몬트 봇의 세 지점에 Claude API(Anthropic Python SDK)를 도입하기 위한 설계.
> 대상: (1) 이성 왜곡의 **마스킹**, (2) 이성 왜곡의 **오정보 치환**, (3) **의사 캐릭터**(AI NPC 대화).
> 기준: Python, `anthropic` SDK, 기본 모델 `claude-opus-4-8`. 작성 2026-07-14.

---

## 0. 요약

- 세 기능 모두 하나의 얇은 래퍼 **`utils/ai_client.py`** 를 통해 호출한다. 봇 어디서도 SDK를 직접 부르지 않는다.
- **마스킹 / 오정보 치환**: 조사·추적·소문 출력 텍스트 + 대상 이성 수치를 넣어, 왜곡된 텍스트를 돌려받는다. 단발 호출(무상태).
- **의사 NPC**: DM 대화 세션. 환자의 능력치·기록을 시스템 프롬프트에 주입하고, 플레이어 DM ↔ Claude 응답을 봇이 중계. 대화 이력은 **시트 또는 인메모리 세션**으로 관리.
- **원칙**: API 실패·거부 시 **게임을 막지 않는다**. 마스킹/오정보는 규칙 기반 폴백, 의사는 안전한 고정 대사로 폴백.
- **비용/지연**: 시스템 프롬프트는 고정 → **프롬프트 캐싱**으로 절감. 왜곡은 짧아 단발 호출로 충분. 모델은 env로 교체 가능(§5).

---

## 1. 공통 인프라 — `utils/ai_client.py`

기존 유틸 패턴(`dm_sender`, `api_retry`)과 동일하게 전역 싱글톤 + 편의 함수로 감싼다.

```python
# utils/ai_client.py (설계 스케치)
import os
from anthropic import Anthropic
import anthropic
from utils.logging_config import logger
from config.settings import config

_client = None

def get_ai_client():
    global _client
    if _client is None and config.AI_ENABLED:
        _client = Anthropic()   # ANTHROPIC_API_KEY를 환경에서 자동 로드
    return _client

def _call(system, messages, max_tokens=1024, model=None):
    """단발 호출 공통 경로. 실패 시 None 반환(호출부가 폴백 결정)."""
    client = get_ai_client()
    if client is None:
        return None
    try:
        resp = client.messages.create(
            model=model or config.AI_MODEL,          # 기본 claude-opus-4-8
            max_tokens=max_tokens,
            system=system,                            # 캐시 대상(§5)
            messages=messages,
        )
        return "".join(b.text for b in resp.content if b.type == "text").strip()
    except anthropic.RateLimitError:
        logger.warning("[AI] rate limit")
    except anthropic.APIStatusError as e:
        logger.error(f"[AI] status {e.status_code}: {e.message}")
    except Exception as e:
        logger.error(f"[AI] 호출 실패: {e}", exc_info=True)
    return None
```

- SDK가 429·5xx·네트워크 오류를 자동 재시도(기본 2회)하므로 별도 재시도 루프는 불필요. `max_retries`만 필요 시 조정.
- `config.AI_ENABLED=False`거나 키가 없으면 `get_ai_client()`가 None → 전 기능이 자동으로 폴백 경로로 흐른다(봇은 정상 동작).
- **호출 위치**: 명령어 실행 스레드에서 동기 호출. 왜곡은 조사/추적/소문 응답 직전, 의사는 `[의무실 방문]` 처리 중. 지연은 §5.

---

## 2. 마스킹 (이성 경고 구간)

**목적**: 이성이 왜곡 임계 이하일 때 조사/추적/소문 텍스트의 일부 정보를 가려, "정보가 흐릿하게 새어든다."를 연출.

- **입력**: 원문 + 이성 수치(또는 왜곡 강도 0~1).
- **출력**: 명사·숫자 일부가 가려진 텍스트. 문장 구조·분위기는 유지.
- **구현**: `distort_text(text, sanity, mode="mask")`. 시스템 프롬프트에 규칙을 고정하고, 원문은 user 턴으로.

```python
MASK_SYSTEM = [{
  "type": "text",
  "text": (
    "너는 TRPG 게임의 '흐려진 지각' 필터다. 주어진 서술문에서 핵심 명사와 숫자 일부를 "
    "가려 정보를 흐리게 만든다. 규칙:\n"
    "- 문장 수·어조·분위기는 원문과 동일하게 유지한다.\n"
    "- 가릴 때는 '▓▓' 또는 '…'로 대체한다. 가림 비율은 강도에 비례(강도가 높을수록 더 많이).\n"
    "- 새로운 사실을 추가하거나 거짓을 넣지 않는다(그건 다른 필터의 역할).\n"
    "- 결과 텍스트만 출력한다. 설명·머리말 금지."
  ),
  "cache_control": {"type": "ephemeral"},   # 시스템 프롬프트 캐싱(§5)
}]

def distort_mask(text: str, strength: float) -> str:
    out = _call(
        system=MASK_SYSTEM,
        messages=[{"role": "user",
                   "content": f"[강도 {strength:.2f}]\n\n{text}"}],
        max_tokens=len(text) // 2 + 256,
    )
    return out or _rule_based_mask(text, strength)   # 폴백: 규칙 기반
```

- **폴백** `_rule_based_mask`: AI 없이도 일부 어절을 `▓`로 치환하는 단순 규칙(원래 계획의 규칙 기반 방식). API가 죽어도 왜곡이 아예 사라지지 않도록.
- 마스킹은 사실을 왜곡하지 않으므로 GM 오해 관리 부담 없음.

---

## 3. 오정보 치환 (이성 위험 구간)

**목적**: 이성이 환각 임계 이하일 때, 그럴듯한 **거짓 정보**를 섞고 환각 톤을 덧입힘.

- **입력**: 원문 + (선택) 치환 후보 풀(명단·장소 목록 등, 그럴듯한 대체어 소스).
- **출력**: 일부 정보가 거짓으로 바뀐 텍스트 + 서두 환각 문장 1개.
- **위험**: 완전 자유 생성은 GM이 "무엇이 거짓인지" 통제 못 함 → **바뀐 지점을 구조화 출력으로 함께 받는다.**

**Structured Outputs로 "무엇을 어떻게 바꿨는지"까지 회수** (레퍼런스의 `output_config.format`):

```python
DISTORT_SYSTEM = [{
  "type": "text",
  "text": (
    "너는 TRPG '환각' 필터다. 서술문에 그럴듯한 거짓을 섞고, 맨 앞에 환각을 암시하는 "
    "짧은 문장 하나를 붙인다. 규칙:\n"
    "- 인물/장소/숫자 중 1~2개만 후보 풀의 값으로 바꾼다. 과하게 바꾸지 않는다.\n"
    "- 분위기는 유지하되 불안·왜곡감을 더한다.\n"
    "- 반드시 지정된 JSON 스키마로만 답한다."
  ),
  "cache_control": {"type": "ephemeral"},
}]

DISTORT_SCHEMA = {
  "type": "object",
  "properties": {
    "distorted": {"type": "string"},                 # 플레이어에게 보낼 최종 텍스트
    "changes":   {"type": "array", "items": {"type": "string"}},  # GM 로그용: 바꾼 지점
  },
  "required": ["distorted", "changes"],
  "additionalProperties": False,
}

def distort_false(text, candidates) -> tuple[str, list[str]]:
    client = get_ai_client()
    if client is None:
        return _rule_based_false(text, candidates)   # 폴백
    try:
        resp = client.messages.create(
            model=config.AI_MODEL, max_tokens=len(text)//2 + 512,
            system=DISTORT_SYSTEM,
            messages=[{"role": "user",
                       "content": f"[후보 풀] {', '.join(candidates)}\n\n[원문]\n{text}"}],
            output_config={"format": {"type": "json_schema", "schema": DISTORT_SCHEMA}},
        )
        import json
        data = json.loads(next(b.text for b in resp.content if b.type == "text"))
        return data["distorted"], data["changes"]     # changes는 행동로그/설정 시트에 남겨 GM이 추적
    except Exception as e:
        logger.error(f"[AI] 오정보 치환 실패: {e}")
        return _rule_based_false(text, candidates)
```

- `changes`(무엇을 거짓으로 바꿨는지)를 **행동로그/설정 시트에 기록**하면 GM이 "이 플레이어가 받은 정보 중 X가 환각이었다."를 나중에 확인 가능 → 오해 관리 부담 해소.
- 후보 풀은 `명단`(인물)·`진입`(장소) 시트에서 이미 로드하는 데이터 재사용.

> **결정성 확정(2026-07-14): 매번 다르게.** 같은 정보를 여러 번 조회·추적해도 왜곡을 고정하지 않는다 — 불안정한 환각 느낌을 강화한다. 캐시·시드로 고정하지 않으며, 프롬프트에 시드를 넣지 않는다. 다만 `changes`(무엇을 거짓으로 바꿨는지)는 매 호출마다 로그에 남겨 GM이 추적할 수 있게 한다(정보 교차검증은 어려워지지만, GM 시점에서는 기록으로 파악 가능).

---

## 4. 의사 캐릭터 (AI NPC 대화)

**목적**: `[의무실 방문]`(DM 전용, @DOCTOR)에서 의료진과 실제 대화하듯 처치를 받는다. 대화 내용은 기록되며 소문으로 샐 수 있음(기획).

### 4.1 흐름
1. 플레이어가 @DOCTOR에게 `[의무실 방문]` + 하고 싶은 말을 DM.
2. 봇이 **환자 컨텍스트**(이름·직군·건강·이성·최근 기록·입원 여부·일차)를 조회.
3. 이를 **시스템 프롬프트**에 넣고, 대화 이력 + 이번 발화를 messages로 전달.
4. Claude의 응답(`{reply, concluding}` 구조화 출력)을 봇이 DM으로 중계. 답글 스레드로 이어가며, **의사가 대화 흐름을 보고 `concluding=true`로 스스로 마무리**한다(최소 `DOCTOR_TURNS_MIN`=2, 이상 3~6, 하드 상한 `DOCTOR_MAX_TURNS`=8 — 경계는 코드가 강제).
5. **의사가 마무리하면 처치 결과를 1회 결정·반영**한다. 처치 종류/수치는 `doctor_treatment`가 **대화 맥락으로 결정**(구조화 출력 `{treatment, health_delta, sanity_delta, reason}`) → **코드가 `DOCTOR_TREAT_DELTA_MIN/MAX`로 클램프**하고 능력치 0~100을 지켜 `관리`에 반영(밸런스 상한은 코드가 소유). AI 꺼짐/실패 시 규칙 폴백(낮은 능력치 회복, '과잉심문'은 AI만 판정).
6. 처치 요약을 **행동로그에 기록**(소문 소스).

### 4.2 페르소나 & 컨텍스트 주입

```python
def doctor_reply(history, patient, utterance,
                 turn_no=1, min_turns=2, max_turns=8, ideal=(3, 6)) -> dict | None:
    # system = [DOCTOR_PERSONA(고정·캐시)] + [환자 컨텍스트 + 진행(턴) 안내(가변)]
    #   환자 컨텍스트: 이름/직군/건강/이성/입원/일차/최근요약 + 관찰 지침
    #   진행 안내: 이번이 {turn_no}번째 발화, 보통 {ideal}에 마무리, {min}~{max} 경계
    # 구조화 출력 {reply, concluding}로 받는다.
    resp = client.messages.create(
        model=config.AI_DOCTOR_MODEL, max_tokens=512, system=system,
        messages=history + [{"role": "user", "content": utterance}],
        output_config={"format": {"type": "json_schema", "schema": DOCTOR_REPLY_SCHEMA}},
    )
    data = json.loads(...)
    return {"reply": data["reply"], "concluding": bool(data["concluding"])}  # 실패 시 None → 폴백
```

- **고정 페르소나**는 첫 시스템 블록(캐시), **환자별 컨텍스트+진행 안내**는 마지막 블록(가변)로 분리 → 캐시 적중률↑.
- **대화 이력 관리 (확정)**: 자유 대화. **의사가 흐름을 보며 `concluding=true`로 스스로 마무리**(최소 `DOCTOR_TURNS_MIN`, 이상 `IDEAL`, 하드 상한 `DOCTOR_MAX_TURNS`; 최종 종료 판단은 코드가 강제). 마무리 시 처치 확정.
  - 진행 세션(누적 messages)은 **인메모리**(`investigation_state` 패턴, 방문자별)로 관리 — 재시작 시 소실되나 방문은 1회성이라 허용.
  - **처치 요약 1줄만 행동로그에 영속화**(소문·추적에 노출될 부분). 처치 효과(회복/하락)는 **대화 종료 시 1회** `doctor_treatment`로 결정→클램프→시트 반영.
  - 턴마다 messages를 다시 보내므로 시스템 프롬프트 캐싱이 비용을 눌러 준다(§5).

### 4.2b 처치 판정 (`doctor_treatment`)

```python
def doctor_treatment(history: list, patient: dict) -> dict | None:
    # 진료 대화(history)와 환자 상태를 근거로 처치를 판정(구조화 출력).
    # 반환 {treatment: '진통제|안정제|상담|과잉심문|경과관찰',
    #        health_delta: int, sanity_delta: int, reason: str}
    # 실패/거부/AI 꺼짐 → None(→ 규칙 폴백). 수치 클램프는 호출측(코드)이 소유.
```

- 별도 판정 시스템 프롬프트(`TREATMENT_SYSTEM`)로 진료 대화를 요약·판정. 페르소나 대사와 분리(대사 품질 보존 + 판정은 JSON).
- **밸런스 소유는 코드**: AI가 준 delta를 `DOCTOR_TREAT_DELTA_MIN/MAX`로 클램프하고 능력치 0~100 유지. AI가 과하게 줘도 안전.

### 4.3 안전
- 플레이어 발화는 항상 **user 턴**으로만 들어가고, 규칙은 system에 둔다 → 프롬프트 인젝션 방어.
- Claude가 안전상 거부(`stop_reason == "refusal"`)하면 폴백 대사로 대체하고 로그만 남긴다.

---

## 5. 모델 선택 · 비용 · 지연

기본 모델은 `claude-opus-4-8`. 다만 이 봇의 AI 호출은 **짧고 빈번**(왜곡)하거나 **실시간 대화**(의사)라 지연·비용 특성이 중요하다. `.env`의 `AI_MODEL`로 언제든 교체 가능하게 설계했다 — **모델 하향은 운영자의 선택**이며, 판단 근거는 아래.

| 용도 | 특성 | 후보(운영자 판단) |
|------|------|-------------------|
| 마스킹 | 매우 단순·다빈도 | **`claude-haiku-4-5`(확정)** — 빠름·저렴 |
| 오정보 치환 | 그럴듯함이 중요 | **`claude-haiku-4-5`(확정)** — 왜곡은 매번 달라야 하므로 다빈도, Haiku로 충분 |
| 의사 NPC | 롤플레이 몰입·응답성 | **`claude-sonnet-5`(확정)** — 몰입·속도 균형 |

- **프롬프트 캐싱**: 시스템 프롬프트(왜곡 규칙/의사 페르소나)는 고정 → `cache_control` 적용 시 반복 호출의 입력 비용 대폭 절감. `usage.cache_read_input_tokens`로 적중 확인.
- **thinking 생략**: 세 기능 모두 깊은 추론이 필요 없으므로 `thinking` 파라미터를 넣지 않는다(Opus 4.8은 미지정 시 thinking 없이 실행 → 지연↓).
- **max_tokens**: 왜곡은 입력 길이에 비례(≈절반+여유), 의사는 512. 모두 16k 미만이라 비스트리밍으로 충분(스트리밍 불필요).
- **지연 주의**: 의무실 대화에서 응답이 수 초 걸릴 수 있음 → 봇이 "의사가 차트를 넘긴다…" 같은 대기 연출 DM을 먼저 보내는 것도 방법. 마을 명령 처리 스레드를 오래 잡지 않도록 타임아웃(예: 20~30초) 후 폴백.

---

## 6. 설정(.env) 추가

```env
AI_ENABLED=True
ANTHROPIC_API_KEY=sk-ant-...        # 절대 코드/시트에 하드코딩 금지
AI_MODEL=claude-sonnet-5            # 기본(용도별 미지정 시 폴백)
AI_MASK_MODEL=claude-haiku-4-5      # 마스킹·오정보(빈번·단순) — 확정
AI_DOCTOR_MODEL=claude-sonnet-5     # 의사 NPC(몰입·응답성) — 확정
DOCTOR_MAX_TURNS=8                  # 의사 발화 하드 상한(자기-종료)
DOCTOR_TURNS_MIN=2                  # 최소 발화(그 전엔 종료 안 함)
DOCTOR_TURNS_IDEAL_MIN=3           # 이상적 종료 하한
DOCTOR_TURNS_IDEAL_MAX=6           # 이상적 종료 상한
DOCTOR_TREAT_DELTA_MAX=12          # 종료 시 처치 회복 상한(+)
DOCTOR_TREAT_DELTA_MIN=-6          # 과잉심문 등 하락 하한(-)
```

> 모델 선택 확정(2026-07-14): **마스킹·오정보 치환 = `claude-haiku-4-5`**, **의사 NPC = `claude-sonnet-5`**. `_call`/`distort_*`가 용도별 모델을 인자로 받도록 하고, 위 env에서 로드한다.

`config/settings.py`에 위 값을 로드하는 필드 추가. `AI_ENABLED=False`면 전 기능 폴백.

---

## 7. 구현 체크리스트
- [x] `utils/ai_client.py`: 클라이언트 싱글톤 + `_call` + `distort_mask`/`distort_false` + `doctor_reply`(구조화 `{reply,concluding}`) + `doctor_treatment`(구조화 처치 판정) + 규칙/고정대사 폴백.
- [x] `config/settings.py`: `AI_*` + `DOCTOR_*`(턴·처치 변동) 설정 로드.
- [x] `utils/distortion.py`: 이성 수치 → 마스킹/오정보 분기에서 `ai_client` 호출, 실패 시 규칙 폴백.
- [ ] 조사/추적/소문 출력부에 왜곡 필터 적용, `changes`는 로그에 남김.(진입/조사는 추후)
- [x] `commands/shinarmont/infirmary_command.py`: 환자 컨텍스트(이름/직군/건강/이성/입원/일차/최근요약) → `doctor_reply`(자기-종료) → DM 중계 → 종료 시 `doctor_treatment`→클램프→`관리` 반영 → 행동로그 요약. 다중 타래는 `utils/reply_threads`.
- [x] 거부/오류 폴백: 고정 대사 풀(러셀 톤) + 규칙 기반 처치.

---

## 8. 결정 로그 · 남은 확인

**확정(2026-07-14)**
- 모델: 마스킹·오정보 = `claude-haiku-4-5`, 의사 = `claude-sonnet-5`.
- 의무실 대화: 의사 자기-종료(MIN 2 / IDEAL 3~6 / 하드 상한 `DOCTOR_MAX_TURNS`=8). 세션은 인메모리 + 요약만 영속.
- 왜곡 결정성: 매번 다르게(고정 안 함). `changes`는 매 호출 로그.

**추가 확정(2026-07-14)**
- **NSFW 방침 = 의료적 선 유도.** 의사 시스템 프롬프트에 "의료적·사무적 범위 유지, 민감 전개는 모호히 회피, 처치·심리 추궁에 집중"을 명시. 그래도 거부되면 고정 대사로 폴백. (교류=성적 접촉은 플레이어 간이며 AI 미개입 — AI를 타는 건 의사뿐.)
- **오정보 `changes` 기록 = `기록.상세` 한 줄 요약.** 별도 GM 전용 시트 없이 요약만 남긴다(시트 트래킹 §8과 일치).

**남은 확인(구현 중 결정 가능, 사소)**
- **의무실 대기 연출**: 응답 지연 시 "차트를 넘긴다…" 선행 DM을 보낼지. (권장: 보냄)

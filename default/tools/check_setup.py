# -*- coding: utf-8 -*-
"""기동 전 점검 — 봇을 띄우지 않고 설정만 확인한다.

    python tools/check_setup.py

무엇을 보나:
  1. `.env` 존재 (코드는 `.env`만 읽는다. `.env.shinarmont`는 템플릿일 뿐)
  2. 5슬롯 설정·토큰
  3. 구글 크레덴셜
  4. Claude API 키·모델명 (실제로 API에 물어본다 — 오타면 여기서 잡힌다)

AI가 없어도 봇은 정상 가동한다(규칙 폴백). 그래서 AI 항목은 경고로만 표시한다.
"""

import os
import sys
from pathlib import Path

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))
os.chdir(BASE)

OK, WARN, BAD = '  [OK]  ', '  [경고]', '  [실패]'
problems = []
warnings = []


def bad(msg, fix):
    print(f"{BAD} {msg}")
    problems.append((msg, fix))


def warn(msg, fix):
    print(f"{WARN} {msg}")
    warnings.append((msg, fix))


def ok(msg):
    print(f"{OK} {msg}")


print("=" * 62)
print(" 시너몬트 봇 기동 전 점검")
print("=" * 62)

# ── 1. .env ────────────────────────────────────────────────
print("\n[1] .env 파일")
env_path = BASE / '.env'
if not env_path.exists():
    bad(".env 가 없습니다. 코드는 .env 만 읽습니다(.env.shinarmont는 템플릿).",
        "cp .env.shinarmont .env")
    print("\n" + "=" * 62)
    print(" .env 가 없으면 나머지를 볼 수 없습니다. 위 명령부터 실행하세요.")
    print("=" * 62)
    sys.exit(1)
ok(".env 있음")

from config.settings import config  # noqa: E402  (.env 확인 후 로드)

# ── 2. 멀티봇 5슬롯 ────────────────────────────────────────
print("\n[2] 봇 슬롯")
if not config.ENABLE_MULTI_BOT:
    bad("ENABLE_MULTI_BOT 이 True 가 아닙니다. 단일 봇 모드로 떨어집니다.",
        ".env 에서 ENABLE_MULTI_BOT=True")
else:
    ok("ENABLE_MULTI_BOT=True")

try:
    from bot_manager import BotManager
    configs = BotManager(env_file_path=str(env_path)).load_bot_configs()
except Exception as e:  # noqa: BLE001
    bad(f"봇 설정 로드 실패: {e}", "bot_manager.py / .env 확인")
    configs = []

if len(configs) != 5:
    warn(f"슬롯이 {len(configs)}개입니다(기대: 5).", ".env 의 BOT1~BOT5_ENABLED 확인")
else:
    ok("슬롯 5개 인식")

for c in configs:
    valid, errs = c.validate()
    if valid:
        ok(f"{c.bot_id} {c.bot_name}")
    else:
        for e in errs:
            bad(f"{c.bot_id} {c.bot_name}: {e}",
                f".env 의 {c.bot_id}_ACCESS_TOKEN 에 마스토돈 액세스 토큰 입력")

# ── 3. 구글 크레덴셜 ──────────────────────────────────────
print("\n[3] 구글 시트 크레덴셜")
cred_dir = BASE / getattr(config, 'CREDENTIALS_DIR', 'credentials')
if cred_dir.is_dir():
    found = sorted(p.name for p in cred_dir.glob('*.json'))
    if found:
        ok(f"{cred_dir.name}/ 에 {len(found)}개: {', '.join(found)}")
    else:
        warn(f"{cred_dir.name}/ 가 비어 있습니다.", "서비스 계정 json 을 넣으세요")
else:
    warn(f"{cred_dir.name}/ 디렉터리가 없습니다.",
         "루트 credentials.json 폴백으로 동작하지만 쿼터가 분산되지 않습니다.")

# ── 4. Claude API ─────────────────────────────────────────
print("\n[4] Claude API (없어도 봇은 가동 — 의사 대사가 규칙 폴백이 됨)")
try:
    import anthropic
    ok(f"anthropic SDK {anthropic.__version__}")
    sdk = True
except ImportError:
    warn("anthropic SDK 미설치 → AI 항상 폴백.", "pip install -r requirements.txt")
    sdk = False

if not config.AI_ENABLED:
    warn("AI_ENABLED=False → AI 비활성.", ".env 에서 AI_ENABLED=True")
elif not config.ANTHROPIC_API_KEY:
    warn("ANTHROPIC_API_KEY 가 비어 있음 → AI 항상 폴백.",
         "console.anthropic.com 에서 키를 발급해 .env 의 ANTHROPIC_API_KEY 에 입력")
elif sdk:
    # 모델명이 틀리면 운영 중엔 조용히 폴백된다(로그에만 404). 여기서 미리 잡는다.
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    models = {
        'AI_MODEL': config.AI_MODEL,
        'AI_MASK_MODEL': config.AI_MASK_MODEL,
        'AI_DOCTOR_MODEL': config.AI_DOCTOR_MODEL,
    }
    # AI 문제는 **기동을 막지 않는다** — 폴백으로 도니까. 전부 경고로만 남긴다.
    for key, model in models.items():
        try:
            client.models.retrieve(model)
            ok(f"{key} = {model}")
        except Exception as e:  # noqa: BLE001
            status = getattr(e, 'status_code', None)
            if status == 401:
                warn("API 키가 거부됐습니다(401) → AI 항상 폴백.",
                     ".env 의 ANTHROPIC_API_KEY 확인 (console.anthropic.com 에서 재발급)")
                break
            if status == 404:
                warn(f"{key} = {model} → 그런 모델이 없습니다 → 이 기능만 폴백.",
                     f".env 의 {key} 를 정확한 모델 ID로 수정")
            else:
                warn(f"{key} = {model} 확인 실패: {e}", "네트워크/키 확인")

# ── 요약 ──────────────────────────────────────────────────
print("\n" + "=" * 62)
if problems:
    print(f" 봇을 띄울 수 없습니다 — 먼저 고칠 것 {len(problems)}건")
    for i, (msg, fix) in enumerate(problems, 1):
        print(f"   {i}. {msg}\n      → {fix}")
elif warnings:
    print(" 봇은 뜹니다. 다만 아래는 알고 계세요:")
else:
    print(" 전부 정상입니다. `python main.py` 로 5슬롯을 띄우세요.")

if warnings:
    for i, (msg, fix) in enumerate(warnings, 1):
        print(f"   {i}. {msg}\n      → {fix}")
print("=" * 62)
sys.exit(1 if problems else 0)

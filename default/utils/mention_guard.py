"""
utils/mention_guard.py — 발송 텍스트에서 멘션을 무력화한다 (시너몬트)

왜 필요한가
-----------
일일보고는 시트·슬롯 JSON·AI가 쓴 **남의 글**을 그대로 인용한다. 그 안에는
`@avet` 같은 계정 태그가 섞여 있다(지령 내용의 첫 줄이 `@계정 제목` 형식이다).
그대로 툿하면 **인용된 계정 전원에게 알림이 날아간다** — GM에게만 가야 할
DM 보고서가 플레이어 15명을 호출한 사고가 실제로 있었다(2026-07-19).

인용문에 멘션이 섞이는 경로는 하나가 아니다: 부탁지령.내용 / 완료 내용,
고발.사유, 조사.결과, 행동로그.요약, 의사 대화록, AI 소문 씨앗… 한 군데를 막아도
다음 달에 새 필드가 생기면 또 샌다. 그래서 **겹겹이** 막는다:

    1단계  수집   digest_facts   — 시트에서 읽을 때 자유 서술 필드를 defang
    2단계  조립   digest_report  — 완성된 보고서 전문을 통째로 defang
    3단계  발송   daily_digest   — 통마다 '@' 를 전부 죽이고, 수신자 멘션만 새로 붙임

3단계가 최후의 보루다. 1·2단계를 다 빠져나온 '@'가 있어도 거기서 죽는다.

무력화 방법
-----------
ASCII '@'(U+0040)를 전각 '＠'(U+FF20)으로 바꾼다. 마스토돈의 멘션 파서는
ASCII '@'만 인식하므로 알림이 가지 않고, 사람 눈에는 계정명이 그대로 읽힌다.
지우지 않는 이유는 GM이 "누가 언급됐는지"를 읽을 수 있어야 하기 때문이다.
"""

import re
from typing import Any

# 마스토돈이 멘션으로 파싱하는 '@'만 골라낸다.
# 사용자명은 [A-Za-z0-9_], 원격 계정은 뒤에 @도메인이 붙는다.
# 앞에 단어문자/슬래시가 있으면(이메일 꼬리, URL) 멘션이 아니다.
_MENTION_RE = re.compile(
    r'(?<![\w/])@+([A-Za-z0-9_]+(?:@[A-Za-z0-9.-]+)?)'
)

# 제목을 뽑을 땐 **이미 무력화된** '＠계정'도 지워야 한다. 1단계(수집)가 먼저
# 돌기 때문에, 제목을 뽑는 시점의 텍스트에는 ASCII '@'가 이미 남아 있지 않다.
_ANY_MENTION_RE = re.compile(
    r'(?<![\w/])[@＠]+([A-Za-z0-9_]+(?:[@＠][A-Za-z0-9.-]+)?)'
)

AT = '@'
FULLWIDTH_AT = '＠'


def _text(value: Any) -> str:
    if value is None:
        return ''
    return str(value)


def defang(value: Any) -> str:
    """멘션으로 파싱될 '@'만 전각 '＠'으로 바꾼다 (1·2단계).

    >>> defang('@avet 서명 없는 지시')
    '＠avet 서명 없는 지시'
    >>> defang('a@b.com 은 이메일')        # 앞에 단어문자 → 멘션 아님
    'a@b.com 은 이메일'
    """
    text = _text(value)
    if AT not in text:
        return text
    return _MENTION_RE.sub(lambda m: FULLWIDTH_AT + m.group(1), text)


def defang_all(value: Any) -> str:
    """'@'를 **전부** 전각으로 바꾼다 (3단계, 최후의 보루).

    정규식의 빈틈을 따지지 않는다. 발송 직전이라 과하게 막아도 잃을 게 없고,
    여기서 새면 알림이 실제로 날아간다.
    """
    return _text(value).replace(AT, FULLWIDTH_AT)


def strip_mentions(value: Any) -> str:
    """멘션 토큰을 통째로 지운다 (제목 뽑을 때).

    지령 내용의 첫 줄은 `@avet 서명 없는 지시` 형식이라, 제목만 원할 땐
    계정명이 남으면 지저분하다. 이미 `defang` 을 거친 '＠계정'도 함께 지운다 —
    수집 단계가 먼저 돌아서, 여기 도달할 땐 ASCII '@'가 남아 있지 않다.
    """
    text = _ANY_MENTION_RE.sub('', _text(value))
    return re.sub(r'\s{2,}', ' ', text).strip()


def has_live_mention(value: Any) -> bool:
    """아직 살아 있는(=알림이 갈) 멘션이 남아 있는지. 테스트·점검용."""
    return bool(_MENTION_RE.search(_text(value)))

"""주민 명부(`data/주민_명부.md`) 로더 — 의사 AI가 환자 배경을 알게 한다.

시너몬트는 주민 스무 명뿐인 폐쇄 도시고, 러셀은 그 유일한 의사다.
주치의라면 환자가 누구인지 알고 있어야 한다 — 직업, 성격, 버릇, 기왕력까지.

**명부 전체를 정적 캐시 블록에 싣는다.** 20명이 통째로 들어가지만 프롬프트 캐싱
(cache_control ephemeral)이 걸린 자리라 두 번째 호출부터는 사실상 공짜다.
환자별로 잘라 넣으면 캐시가 매번 깨져서 오히려 비싸고, 러너가 남을 언급했을 때
(`휴고가 어쩌고`) 러셀이 못 알아듣는다.

명부 파일이 없거나 깨져도 **봇은 정상 동작한다** — 배경 없이 진료할 뿐이다.
"""

import os
import re
import threading
from typing import Dict, List, Optional

try:
    from utils.logging_config import logger
except ImportError:  # pragma: no cover - 로깅 미구성 환경 폴백
    import logging
    logger = logging.getLogger('resident_profiles')


_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_PATH = os.path.join(_PROJECT_ROOT, 'data', '주민_명부.md')

# `## 다즈 (데릴 "다즈" 헤니스 / Daryl "Daz" John Hennis)` 에서 키='다즈'를 뽑는다.
# 괄호 앞까지가 키 — `관리` 시트의 '이름' 칸과 같아야 한다.
_HEADER = re.compile(r'^##\s+(.+?)\s*(?:\(|$)', re.M)


# 헤더 괄호 안 이름을 나누는 구분자: 슬래시(한국어/영문/한자), 중점(·), 파이프(|)
_ALIAS_SEP = re.compile(r'[/·|]')
# 애칭을 감싸는 따옴표(반각/전각) — 토큰에서 걷어낸다
_ALIAS_QUOTES = re.compile(r'[\"“”\'‘’]')


def _parse_aliases(key: str, header_line: str) -> List[str]:
    """헤더 한 줄에서 이 주민을 부르는 이름을 전부 뽑는다(첫이름·풀네임·성·애칭·원문).

    예) `## 존 (존 린덴펠스 / John Lindenfels)`
        → ['존', '존 린덴펠스', '린덴펠스', 'John Lindenfels', 'John']
        `## 다즈 (데릴 "다즈" 헤니스 / Daryl "Daz" John Hennis)`
        → ['다즈', '데릴 다즈 헤니스', '데릴', '헤니스', 'Daryl Daz John Hennis', ...]

    성만(린덴펠스) 불러도 같은 사람임을 러셀이 알아보게 하려는 것 — 명부 본문은
    이름을 산문에 흩어 두어 모델이 성↔이름을 놓칠 수 있다. 이 표로 못을 박는다.
    """
    aliases: List[str] = []

    def _add(token: str) -> None:
        token = _ALIAS_QUOTES.sub('', str(token or '')).strip().strip('.·').strip()
        if token and token not in aliases:
            aliases.append(token)

    _add(key)

    m = re.search(r'\((.+?)\)', header_line)
    if not m:
        return aliases

    for segment in _ALIAS_SEP.split(m.group(1)):
        segment = _ALIAS_QUOTES.sub('', segment).strip()
        if not segment:
            continue
        _add(segment)                       # 풀네임 전체(예: '존 린덴펠스')
        for tok in segment.split():         # 각 토큰(성/이름/애칭)
            _add(tok)
    return aliases


def normalize_name(name: str) -> str:
    """이름 매칭용 정규화 — 공백·점·대소문자 차이를 흡수한다.

    실측(2026-07-16)에서 시트는 `CC`, 명부 원문은 `C. C. 라이트너`였다.
    사람이 시트를 손으로 채우는 이상 `C.C.`/`cc `/`C C` 가 언제든 나온다.
    """
    return re.sub(r'[\s.·]+', '', str(name or '')).casefold()


class ResidentRoster:
    """이름 → 프로필 본문. 파일을 1회 읽고 메모리에 든다."""

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or _DEFAULT_PATH
        self._raw: str = ''
        self._by_name: Dict[str, str] = {}       # 정규화 이름 -> 항목 본문
        self._display: Dict[str, str] = {}       # 정규화 이름 -> 표기 이름
        self._aliases: Dict[str, List[str]] = {}  # 정규화 이름 -> 이 주민을 부르는 모든 이름
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.path):
            logger.warning(
                f"[명부] {self.path} 없음 → 의사는 환자 배경 없이 진료합니다.")
            return
        try:
            with open(self.path, 'r', encoding='utf-8') as f:
                text = f.read()
        except OSError as e:
            logger.error(f"[명부] 읽기 실패({e}) → 배경 없이 진료합니다.")
            return

        # 헤더 위치로 잘라 각 항목 본문을 만든다.
        marks = list(_HEADER.finditer(text))
        if not marks:
            logger.error(f"[명부] '## 이름 (…)' 헤더를 하나도 못 찾음 → 배경 없이 진료합니다.")
            return

        for i, m in enumerate(marks):
            key = m.group(1).strip()
            end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
            body = text[m.start():end].strip()
            norm = normalize_name(key)
            if not norm:
                continue
            if norm in self._by_name:
                logger.warning(f"[명부] 이름 중복 '{key}' → 뒤엣것으로 덮어씁니다.")
            self._by_name[norm] = body
            self._display[norm] = key
            # 헤더 첫 줄에서 이 주민을 부르는 모든 이름(첫이름/풀네임/성/애칭/원문)을 뽑는다.
            first_line = body.splitlines()[0] if body else ''
            self._aliases[norm] = _parse_aliases(key, first_line)

        # 명부 본문(헤더 주석 제외)을 캐시 블록용으로 보관
        self._raw = text[marks[0].start():].strip()
        logger.info(f"[명부] 주민 {len(self._by_name)}명 로드: "
                    f"{', '.join(self._display.values())}")

    # ── 조회 ──
    def get(self, name: str) -> Optional[str]:
        """이름으로 프로필 본문. 없으면 None."""
        return self._by_name.get(normalize_name(name))

    def names(self) -> List[str]:
        return list(self._display.values())

    def roster_text(self) -> str:
        """명부 전문(프롬프트 정적 블록용). 없으면 빈 문자열."""
        return self._raw

    def alias_text(self) -> str:
        """이름 대조표(프롬프트 정적 블록용). 없으면 빈 문자열.

        각 주민을 부르는 모든 이름을 ' = '로 이어 한 줄씩 낸다. 성만/이름만/애칭으로
        불러도 러셀이 같은 주민으로 알아보게 하는 못. 부부는 성을 공유하므로(린덴펠스·
        트릴리·아브라하미안) 맥락으로 가리라고 안내한다.
        """
        if not self._aliases:
            return ''
        lines = []
        for norm, display in self._display.items():
            names = self._aliases.get(norm) or [display]
            lines.append('- ' + ' = '.join(names))
        return (
            "[이름 대조표] 아래 각 줄의 이름들은 전부 같은 주민을 가리킨다. "
            "성(예: 린덴펠스)만 부르든, 이름(존)만 부르든, 풀네임이나 원문 이름(John Lindenfels)으로 "
            "부르든 동일인이다. 환자가 어떤 형태로 불러도 이 표로 같은 주민임을 알아본다 — "
            "'그게 누구냐', '처음 듣는 이름이다', '저번엔 다르게 부르지 않았냐'고 되묻지 않는다. "
            "성이 같은 부부(예: 존 린덴펠스와 에블린 린덴펠스)는 대화 맥락으로 누구인지 가린다.\n"
            + '\n'.join(lines)
        )

    def __len__(self) -> int:
        return len(self._by_name)


_roster: Optional[ResidentRoster] = None
_lock = threading.Lock()


def get_roster() -> ResidentRoster:
    """전역 명부 싱글톤(파일 1회 읽기)."""
    global _roster
    if _roster is None:
        with _lock:
            if _roster is None:
                _roster = ResidentRoster()
    return _roster


def set_roster(roster: Optional[ResidentRoster]) -> None:
    """테스트용 주입."""
    global _roster
    with _lock:
        _roster = roster

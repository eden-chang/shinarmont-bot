"""주민 명부 로더 + 의사 프롬프트 주입.

핵심 계약:
  - `data/주민_명부.md` 의 `## <이름> (…)` 헤더 이름이 **`관리` 시트 '이름' 칸**과 같아야 한다.
    (2026-07-16 실측: 시트는 `다즈`·`CC`·`원쥔` 처럼 짧은 이름을 쓴다)
  - 명부가 없거나 깨져도 봇은 죽지 않는다 — 배경 없이 진료한다.
"""

import os
import sys
import shutil
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.resident_profiles import (  # noqa: E402
    ResidentRoster, normalize_name, get_roster, set_roster,
)

# 2026-07-16 실측한 `관리` 시트의 '이름' 20개. 명부는 이 이름으로 찾을 수 있어야 한다.
SHEET_NAMES = [
    '가브리엘라', '다즈', '데보라', '모린', '빌', '아베트', '안톤', '에두아르도', '에릭',
    '에바리스토', '에블린', '엘레노어', '원쥔', '재클린', '존', '찰스', '코넬리아',
    '클라라', '휴고', 'CC',
]


class NormalizeTest(unittest.TestCase):
    def test_absorbs_dots_and_spaces(self):
        """시트는 'CC', 원문은 'C. C. 라이트너'였다. 사람이 채우는 칸이라 표기가 흔들린다."""
        for variant in ('CC', 'C.C.', 'C. C.', 'cc', ' c c '):
            self.assertEqual(normalize_name(variant), 'cc')

    def test_empty_and_none(self):
        self.assertEqual(normalize_name(''), '')
        self.assertEqual(normalize_name(None), '')


class RealRosterTest(unittest.TestCase):
    """실제 `data/주민_명부.md` 를 검증한다 — 이게 깨지면 러셀이 환자를 못 알아본다."""

    def setUp(self):
        set_roster(None)
        self.roster = get_roster()

    def tearDown(self):
        set_roster(None)

    def test_loads_twenty_residents(self):
        self.assertEqual(len(self.roster), 20)

    def test_every_sheet_name_resolves(self):
        """시트 이름으로 전원 조회돼야 한다. 하나라도 실패하면 그 러너만 배경 없이 진료된다."""
        missing = [n for n in SHEET_NAMES if self.roster.get(n) is None]
        self.assertEqual(missing, [], f"명부에서 못 찾은 시트 이름: {missing}")

    def test_no_extra_residents(self):
        """명부에만 있고 시트에 없는 사람 = 이름이 어긋난 것."""
        norm_sheet = {normalize_name(n) for n in SHEET_NAMES}
        extra = [d for d in self.roster.names() if normalize_name(d) not in norm_sheet]
        self.assertEqual(extra, [], f"시트에 없는 명부 이름: {extra}")

    def test_doctor_himself_is_not_a_resident(self):
        """러셀은 NPC라 명부에 없어야 한다."""
        self.assertIsNone(self.roster.get('러셀'))
        self.assertIsNone(self.roster.get('해리슨 러셀'))

    def test_unknown_name_returns_none(self):
        self.assertIsNone(self.roster.get('없는사람'))
        self.assertIsNone(self.roster.get(''))

    def test_profile_body_has_content(self):
        body = self.roster.get('다즈')
        self.assertIn('정신과', body)
        self.assertIn('러셀 참고', body, "동종업계라는 의사용 메모가 있어야 한다.")

    def test_roster_text_contains_all(self):
        text = self.roster.roster_text()
        for name in SHEET_NAMES:
            self.assertIn(f"## {name} ", text + ' ', f"{name} 항목이 명부 전문에 없다.")

    def test_alias_table_maps_last_names(self):
        """이름 대조표에 성(라스트네임)과 이름이 같은 줄에 묶여야 한다.

        의사가 '존'과 '린덴펠스'를 다른 사람으로 오인하던 문제의 핵심 방어.
        """
        alias = self.roster.alias_text()
        # 각 주민 줄은 한 줄 안에 여러 호칭이 ' = '로 이어진다.
        lines = {line.split(' = ')[0].lstrip('- '): line
                 for line in alias.splitlines() if line.startswith('- ')}
        # 존: 이름·풀네임·성이 한 줄에
        self.assertIn('존', lines)
        for token in ('존', '린덴펠스', '존 린덴펠스', 'John Lindenfels'):
            self.assertIn(token, lines['존'], f"'{token}'이 존 줄에 없다.")
        # 다즈: 애칭·성이 한 줄에 (다즈 헤니스)
        self.assertIn('다즈', lines)
        for token in ('다즈', '헤니스'):
            self.assertIn(token, lines['다즈'], f"'{token}'이 다즈 줄에 없다.")

    def test_alias_table_covers_all_residents(self):
        alias = self.roster.alias_text()
        body_lines = [l for l in alias.splitlines() if l.startswith('- ')]
        self.assertEqual(len(body_lines), 20)


class BrokenRosterTest(unittest.TestCase):
    """명부가 없거나 깨져도 **봇은 죽지 않는다**. 배경 없이 진료할 뿐."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='roster_')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(set_roster, None)

    def _write(self, text):
        path = os.path.join(self.tmp, 'r.md')
        with open(path, 'w', encoding='utf-8') as f:
            f.write(text)
        return path

    def test_missing_file(self):
        roster = ResidentRoster(path=os.path.join(self.tmp, 'nope.md'))
        self.assertEqual(len(roster), 0)
        self.assertEqual(roster.roster_text(), '')
        self.assertIsNone(roster.get('다즈'))

    def test_no_headers(self):
        roster = ResidentRoster(path=self._write('머리말만 있고 항목이 없다.'))
        self.assertEqual(len(roster), 0)
        self.assertEqual(roster.roster_text(), '')

    def test_header_without_parens(self):
        """`## 다즈` 처럼 괄호 없이 써도 읽혀야 한다."""
        roster = ResidentRoster(path=self._write('## 다즈\n- 직업: 정신과 전문의\n'))
        self.assertIsNotNone(roster.get('다즈'))

    def test_duplicate_name_does_not_crash(self):
        roster = ResidentRoster(path=self._write(
            '## 다즈 (A)\n- 직업: 첫번째\n\n## 다즈 (B)\n- 직업: 두번째\n'))
        self.assertEqual(len(roster), 1)
        self.assertIn('두번째', roster.get('다즈'))

    def test_body_stops_at_next_header(self):
        roster = ResidentRoster(path=self._write(
            '## 존 (John)\n- 직업: 분광학자\n\n## 휴고 (Hugo)\n- 직업: 경비병\n'))
        self.assertIn('분광학자', roster.get('존'))
        self.assertNotIn('경비병', roster.get('존'), "다음 사람 내용이 섞였다.")


class DoctorPromptTest(unittest.TestCase):
    """명부가 의사 프롬프트에 실제로 실리는지."""

    def setUp(self):
        from utils import ai_client as ai
        self.ai = ai
        set_roster(None)

        class _Msgs:
            last = None

            def create(self, **kw):
                _Msgs.last = kw
                raise RuntimeError('프롬프트만 검사')

        class _Client:
            def __init__(self):
                self.messages = _Msgs()

        self._saved = (ai._client, ai._client_init_attempted)
        ai._client = _Client()
        ai._client_init_attempted = True
        self.msgs = ai._client.messages

    def tearDown(self):
        self.ai._client, self.ai._client_init_attempted = self._saved
        set_roster(None)

    def _system_for(self, name):
        self.ai.doctor_reply(history=None, patient={'이름': name}, utterance='...')
        return self.msgs.last['system']

    def test_roster_block_is_cached(self):
        """명부는 매 호출 같은 내용이다. 캐시가 안 걸리면 매번 1만 4천 자를 새로 문다."""
        system = self._system_for('다즈')
        self.assertEqual(len(system), 3, "페르소나 / 명부 / 환자 3블록")
        self.assertEqual(system[1]['cache_control'], {'type': 'ephemeral'})
        self.assertIn('[주민 명부]', system[1]['text'])

    def test_alias_table_is_in_roster_block(self):
        """이름 대조표가 (캐시되는) 명부 블록 안에 함께 실려야 한다."""
        system = self._system_for('다즈')
        roster_block = system[1]['text']
        self.assertIn('[이름 대조표]', roster_block)
        # 성만 불러도 알아보게 하는 매핑이 실제로 들어 있는지
        self.assertIn('린덴펠스', roster_block)
        self.assertIn('헤니스', roster_block)

    def test_patient_block_stays_uncached(self):
        """환자 블록은 매번 바뀐다 → 캐시 breakpoint 뒤여야 한다."""
        system = self._system_for('다즈')
        self.assertNotIn('cache_control', system[-1])

    def test_known_patient_is_pointed_at_roster(self):
        text = self._system_for('다즈')[-1]['text']
        self.assertIn("'다즈' 항목이 이 환자다.", text)

    def test_unknown_patient_told_not_to_invent(self):
        """명부에 없으면 배경을 지어내지 말라고 명시해야 한다."""
        text = self._system_for('없는사람')[-1]['text']
        self.assertIn('명부에 없다', text)
        self.assertIn('지어내지', text)

    def test_missing_roster_omits_block_entirely(self):
        set_roster(ResidentRoster(path='/nonexistent/roster.md'))
        system = self._system_for('다즈')
        self.assertEqual(len(system), 2, "명부가 없으면 블록을 넣지 않는다.")
        self.assertNotIn('[주민 명부]', ''.join(b['text'] for b in system))
        # 그래도 진료는 진행된다
        self.assertIn('명부에 없다', system[-1]['text'])


if __name__ == '__main__':
    unittest.main()

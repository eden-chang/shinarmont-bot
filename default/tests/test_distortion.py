"""distortion.apply 규칙 폴백 및 임계 분기 테스트 (AI 미설치/비활성 경로)."""

import os
import sys
import random
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from utils import distortion


class TestDistortionThresholds(unittest.TestCase):
    def setUp(self):
        # AI 미개입(폴백) 경로를 강제: ai_client가 없거나 None 반환하도록.
        # ai_client 모듈이 없으면 자연히 폴백. 있더라도 함수가 None 반환하면 폴백.
        random.seed(1234)

    def test_no_distortion_above_threshold(self):
        text = "3번 방에서 김철수를 목격했다."
        out, changes = distortion.apply(text, 80, candidates=["이영희", "박민수"])
        self.assertEqual(out, text)
        self.assertEqual(changes, [])

    def test_boundary_at_threshold_masks(self):
        # 정확히 임계(40)면 마스킹 구간(초과가 아니므로)
        text = "3번 방에서 김철수를 목격했다."
        out, changes = distortion.apply(text, 40)
        self.assertEqual(changes, [])
        # 마스킹은 원문과 달라질 수 있으나 최소한 문자열은 반환
        self.assertIsInstance(out, str)

    def test_masking_region_returns_no_changes(self):
        text = "12시에 창고 뒤편에서 이상한 소리가 났다."
        out, changes = distortion.apply(text, 30)
        self.assertEqual(changes, [])
        self.assertIsInstance(out, str)

    def test_masking_masks_numbers(self):
        # 강한 마스킹 강도에서 숫자가 가려지는 경향 확인(여러 시드로 최소 1회 발생)
        found_masked_number = False
        for seed in range(20):
            random.seed(seed)
            out, _ = distortion.apply("777 방에서 만났다.", 22)
            if '▓' in out:
                found_masked_number = True
                break
        self.assertTrue(found_masked_number)

    def test_hallucination_without_candidates_masks_only(self):
        # 후보 풀 없으면 오정보 구간이라도 마스킹만(changes 빈 리스트)
        text = "5번 통로에서 무언가 보았다."
        out, changes = distortion.apply(text, 10)
        self.assertEqual(changes, [])
        self.assertIsInstance(out, str)

    def test_hallucination_with_candidates_swaps(self):
        text = "3번 방에서 김철수를 목격했다."
        candidates = ["이영희", "박민수", "정지훈"]
        # 여러 시드에서 최소 1회는 치환이 일어나고 changes가 기록되어야 함
        got_change = False
        for seed in range(30):
            random.seed(seed)
            out, changes = distortion.apply(text, 10, candidates=candidates)
            self.assertTrue(out.strip())
            if changes:
                got_change = True
                # changes 포맷: "원본→대체"
                for ch in changes:
                    self.assertIn('→', ch)
                # 치환된 대체어가 결과 텍스트에 포함
                self.assertTrue(any(c in out for c in candidates))
                break
        self.assertTrue(got_change)

    def test_none_text_passthrough(self):
        out, changes = distortion.apply(None, 10, candidates=["x"])
        self.assertIsNone(out)
        self.assertEqual(changes, [])

    def test_invalid_sanity_passthrough(self):
        text = "원문"
        out, changes = distortion.apply(text, None)
        self.assertEqual(out, text)
        self.assertEqual(changes, [])

    def test_rule_based_mask_empty(self):
        self.assertEqual(distortion._rule_based_mask("", 0.5), "")

    def test_rule_based_false_has_prefix(self):
        random.seed(0)
        out, changes = distortion._rule_based_false("김철수를 만났다.", ["이영희"])
        # 환각 서두 + 본문 구조
        self.assertIn("\n\n", out)


class TestCensorshipMasking(unittest.TestCase):
    """새 검열 동작: 핵심 구간을 '▓' 런으로 가리고, 검열 시 최하단 판독 불능 문구."""

    LONG = "당신은 그의 오금이 푸르스름하게 죽어있는 것을 발견한다. 혈관까지 거뭇한 멍으로 가득하다."

    def test_footer_and_mask_when_masked(self):
        random.seed(1)
        out, changes = distortion.apply(self.LONG, 30)
        self.assertIn('▓', out)                                   # ▓로 검열됨
        self.assertTrue(out.rstrip().endswith(distortion._CENSOR_FOOTER))  # 최하단 문구
        self.assertEqual(changes, [])

    def test_no_footer_when_not_masked_high_sanity(self):
        out, _ = distortion.apply(self.LONG, 90)                  # 왜곡 없음 구간
        self.assertEqual(out, self.LONG)
        self.assertNotIn(distortion._CENSOR_FOOTER, out)

    def test_no_footer_for_unmaskable_text(self):
        # 어절이 하나뿐이면 가릴 게 없다 → 검열/문구 없음
        for seed in range(5):
            random.seed(seed)
            out, _ = distortion.apply("네", 30)
            self.assertEqual(out, "네")
            self.assertNotIn(distortion._CENSOR_FOOTER, out)

    def test_span_mask_preserves_leading_context(self):
        # 앞 맥락(첫 어절)은 남고, 뒤 핵심 구간이 ▓ 런으로 통째로 가려진다.
        random.seed(3)
        out, _ = distortion.apply("오금이 푸르스름하게 죽어있다.", 24)
        self.assertIn('▓', out)
        self.assertTrue(out.startswith("오금이"))                  # 앞 맥락 보존
        self.assertNotIn("푸르스름", out)                          # 핵심 서술은 가려짐

    def test_ai_unmasked_output_falls_back_to_rule(self):
        # AI가 원문을 거의 그대로 돌려주면(검열 흔적 없음) 규칙 폴백으로 반드시 검열되어야 한다.
        class _FakeAI:
            @staticmethod
            def distort_mask(text, strength):
                return text  # 검열 안 함(원문 echo)

        random.seed(1)
        with patch.object(distortion, '_get_ai_client', return_value=_FakeAI):
            out, _ = distortion.apply(self.LONG, 30)
        self.assertIn('▓', out)                                   # 규칙 폴백이 검열
        self.assertTrue(out.rstrip().endswith(distortion._CENSOR_FOOTER))

    def test_ai_masked_output_is_accepted(self):
        # AI가 ▓로 제대로 가리면 그 출력을 채택하고 문구를 붙인다.
        class _FakeAI:
            @staticmethod
            def distort_mask(text, strength):
                return "그의 팔이 ▓▓▓▓▓▓ 죽어 있었다."

        with patch.object(distortion, '_get_ai_client', return_value=_FakeAI):
            out, _ = distortion.apply(self.LONG, 30)
        self.assertTrue(out.startswith("그의 팔이 ▓▓▓▓▓▓"))
        self.assertTrue(out.rstrip().endswith(distortion._CENSOR_FOOTER))


if __name__ == '__main__':
    unittest.main()

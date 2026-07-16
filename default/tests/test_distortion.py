"""distortion.apply 규칙 폴백 및 임계 분기 테스트 (AI 미설치/비활성 경로)."""

import os
import sys
import random
import unittest

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


if __name__ == '__main__':
    unittest.main()

"""utils/dice_parser.py 단위 테스트."""

import os
import random
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils.dice_parser import (
    evaluate_amount,
    is_dice_expression,
    parse_and_roll_dice,
)


class IsDiceExpressionTest(unittest.TestCase):
    """is_dice_expression 인식 테스트."""

    def test_plain_dice(self):
        self.assertTrue(is_dice_expression("1d6"))
        self.assertTrue(is_dice_expression("2D10"))

    def test_with_modifier(self):
        self.assertTrue(is_dice_expression("2d6+3"))
        self.assertTrue(is_dice_expression("3d10-5"))

    def test_negative_parenthesized(self):
        self.assertTrue(is_dice_expression("-(1d6+3)"))
        self.assertTrue(is_dice_expression("-(2d4)"))

    def test_non_dice(self):
        self.assertFalse(is_dice_expression(""))
        self.assertFalse(is_dice_expression(None))
        self.assertFalse(is_dice_expression("10"))
        self.assertFalse(is_dice_expression("abc"))
        self.assertFalse(is_dice_expression("1d"))
        self.assertFalse(is_dice_expression("d6"))


class ParseAndRollDiceTest(unittest.TestCase):
    """parse_and_roll_dice 결과값·부호 테스트."""

    def setUp(self):
        random.seed(42)

    def test_range_1d6(self):
        for _ in range(50):
            value, _ = parse_and_roll_dice("1d6")
            self.assertGreaterEqual(value, 1)
            self.assertLessEqual(value, 6)

    def test_modifier_positive(self):
        for _ in range(20):
            value, _ = parse_and_roll_dice("1d6+10")
            self.assertGreaterEqual(value, 11)
            self.assertLessEqual(value, 16)

    def test_modifier_negative(self):
        for _ in range(20):
            value, _ = parse_and_roll_dice("1d6-2")
            self.assertGreaterEqual(value, -1)
            self.assertLessEqual(value, 4)

    def test_negative_parenthesized_yields_negative(self):
        for _ in range(20):
            value, _ = parse_and_roll_dice("-(1d6+3)")
            # -(1~6 + 3) = -(4~9) ⇒ -9 ~ -4
            self.assertGreaterEqual(value, -9)
            self.assertLessEqual(value, -4)

    def test_multiple_dice_sum(self):
        for _ in range(20):
            value, detail = parse_and_roll_dice("3d6")
            self.assertGreaterEqual(value, 3)
            self.assertLessEqual(value, 18)
            self.assertIn("3d6", detail)

    def test_invalid_expression_raises(self):
        with self.assertRaises(ValueError):
            parse_and_roll_dice("not a dice")

    def test_dice_count_limit(self):
        with self.assertRaises(ValueError):
            parse_and_roll_dice("25d6")

    def test_dice_sides_limit(self):
        with self.assertRaises(ValueError):
            parse_and_roll_dice("1d1001")


class EvaluateAmountTest(unittest.TestCase):
    """evaluate_amount: 정수와 다이스 표현식 모두 처리."""

    def setUp(self):
        random.seed(42)

    def test_integer(self):
        value, detail = evaluate_amount("5")
        self.assertEqual(value, 5)
        self.assertEqual(detail, "")

    def test_negative_integer(self):
        value, detail = evaluate_amount("-3")
        self.assertEqual(value, -3)
        self.assertEqual(detail, "")

    def test_float_like(self):
        value, detail = evaluate_amount("7.0")
        self.assertEqual(value, 7)
        self.assertEqual(detail, "")

    def test_dice(self):
        value, detail = evaluate_amount("2d6+1")
        self.assertGreaterEqual(value, 3)
        self.assertLessEqual(value, 13)
        self.assertIn("2d6", detail)

    def test_negative_dice(self):
        value, detail = evaluate_amount("-(1d4)")
        self.assertGreaterEqual(value, -4)
        self.assertLessEqual(value, -1)

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            evaluate_amount("")
        with self.assertRaises(ValueError):
            evaluate_amount(None)

    def test_garbage_raises(self):
        with self.assertRaises(ValueError):
            evaluate_amount("garbage")


if __name__ == '__main__':
    unittest.main()

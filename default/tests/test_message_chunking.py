"""utils/message_chunking.py 단위 테스트.

이 모듈엔 테스트가 아예 없었고, 그래서 2026-07-16까지 두 결함이 살아 있었다:
  1. 연속 마커('(계속)')를 길이 검사 **후에** 붙여서, 나뉜 통이 늘 한도를 +10~17자 넘겼다.
  2. _split_long_line 이 공백으로만 쪼개서, 공백 없는 8000자가 통째로 나갔다.

한도를 넘긴 통은 서버가 거절하고, 그 순간 타래가 거기서 끊긴다.
그래서 여기서 제일 중요한 성질은 하나다: **어떤 입력이 와도 통이 한도를 넘지 않는다.**
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils.message_chunking import (
    MARKER_HEAD, MARKER_TAIL, MARKER_OVERHEAD, MessageChunker,
)


class BudgetTest(unittest.TestCase):
    def test_budget_reserves_marker_space(self):
        c = MessageChunker(max_length=500)
        self.assertEqual(c.split_budget, 500 - MARKER_OVERHEAD)

    def test_marker_overhead_matches_the_markers(self):
        """상수를 손으로 적으면 마커를 고칠 때 어긋난다."""
        self.assertEqual(MARKER_OVERHEAD, len(MARKER_HEAD) + len(MARKER_TAIL))

    def test_tiny_limit_does_not_go_negative(self):
        c = MessageChunker(max_length=5)
        self.assertGreaterEqual(c.split_budget, 20)


class NeverExceedsLimitTest(unittest.TestCase):
    """어떤 입력에도 한도를 넘지 않아야 한다."""

    CASES = {
        '평범한 여러 줄': "\n".join(f"{i}번째 줄입니다. 적당한 길이의 문장." for i in range(200)),
        '공백 없는 통짜': "가" * 9000,
        '줄바꿈 없이 공백만': "철조망이 이어진다 " * 600,
        '아주 긴 낱말 하나': "가" * 4000 + " 끝",
        '긴 낱말 + 짧은 줄 섞임': "짧은 줄\n" + "나" * 3000 + "\n또 짧은 줄",
        '줄바꿈만 잔뜩': "\n" * 3000,
        '한 줄이 딱 한도': "다" * 500,
    }

    def test_all_cases_within_limit(self):
        for limit in (100, 500, 1000, 3000, 5000):
            for name, text in self.CASES.items():
                with self.subTest(limit=limit, case=name):
                    chunks = MessageChunker(max_length=limit).split_message(text)
                    for i, chunk in enumerate(chunks, 1):
                        self.assertLessEqual(
                            len(chunk), limit,
                            f"{name} @limit={limit}: {i}/{len(chunks)}통이 "
                            f"{len(chunk)}자 — 서버가 거절하고 타래가 끊긴다",
                        )

    def test_markers_do_not_push_over(self):
        """마커가 붙은 뒤에도 한도 이내여야 한다(예전엔 +10자 넘었다)."""
        for limit in (100, 500, 1000):
            with self.subTest(limit=limit):
                text = "\n".join("가" * (limit - 5) for _ in range(4))
                chunks = MessageChunker(max_length=limit).split_message(text)
                self.assertGreater(len(chunks), 1, "여러 통으로 나뉘어야 하는 입력")
                self.assertLessEqual(max(len(c) for c in chunks), limit)

    def test_spaceless_line_is_split(self):
        """공백이 없어도 쪼개져야 한다. 예전엔 통짜로 나갔다."""
        chunks = MessageChunker(max_length=500).split_message("가" * 8000)
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertLessEqual(len(c), 500)


class ContentPreservedTest(unittest.TestCase):
    @staticmethod
    def _strip_markers(chunks):
        return "".join(
            c.replace(MARKER_HEAD, "").replace(MARKER_TAIL, "") for c in chunks)

    def test_spaceless_text_loses_nothing(self):
        text = "가" * 8000
        rejoined = self._strip_markers(MessageChunker(max_length=500).split_message(text))
        self.assertEqual(rejoined, text, "강제 분할이 글자를 잃었다")

    def test_words_survive(self):
        text = "\n".join(f"줄{i}번" for i in range(300))
        rejoined = self._strip_markers(MessageChunker(max_length=200).split_message(text))
        for i in range(300):
            self.assertIn(f"줄{i}번", rejoined)


class SingleChunkTest(unittest.TestCase):
    def test_short_message_is_untouched(self):
        c = MessageChunker(max_length=500)
        self.assertEqual(c.split_message("짧은 문구"), ["짧은 문구"])

    def test_no_markers_on_single_chunk(self):
        chunks = MessageChunker(max_length=500).split_message("가" * 400)
        self.assertEqual(len(chunks), 1)
        self.assertNotIn("(계속", chunks[0])

    def test_exactly_at_limit_is_single(self):
        chunks = MessageChunker(max_length=500).split_message("가" * 500)
        self.assertEqual(len(chunks), 1)


class MarkerPlacementTest(unittest.TestCase):
    def test_first_chunk_has_tail_only(self):
        chunks = MessageChunker(max_length=100).split_message("가" * 500)
        self.assertTrue(chunks[0].endswith(MARKER_TAIL))
        self.assertFalse(chunks[0].startswith(MARKER_HEAD))

    def test_last_chunk_has_head_only(self):
        chunks = MessageChunker(max_length=100).split_message("가" * 500)
        self.assertTrue(chunks[-1].startswith(MARKER_HEAD))
        self.assertFalse(chunks[-1].endswith(MARKER_TAIL))

    def test_middle_chunks_have_both(self):
        chunks = MessageChunker(max_length=100).split_message("가" * 500)
        self.assertGreater(len(chunks), 2)
        for c in chunks[1:-1]:
            self.assertTrue(c.startswith(MARKER_HEAD))
            self.assertTrue(c.endswith(MARKER_TAIL))


class ShopInventoryTest(unittest.TestCase):
    def test_shop_chunks_within_limit(self):
        items = [{'name': f'아이템{i}', 'price': 100,
                  'description': '설명이 제법 긴 아이템입니다. ' * 3}
                 for i in range(60)]
        chunks = MessageChunker(max_length=500).split_shop_items(items, '달러')
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertLessEqual(len(c), 500)

    def test_inventory_chunks_within_limit(self):
        inv = {f'아이템{i}': i for i in range(200)}
        chunks = MessageChunker(max_length=500).split_inventory_items(inv, '데보라', '는')
        for c in chunks:
            self.assertLessEqual(len(c), 500)


class ConfigTest(unittest.TestCase):
    def test_reads_max_message_length_from_config(self):
        """.env 의 MAX_MESSAGE_LENGTH 가 실제로 먹어야 한다."""
        from config.settings import config
        c = MessageChunker()
        self.assertEqual(c.max_length, config.MAX_MESSAGE_LENGTH)

    def test_five_thousand_setting(self):
        """운영 설정(5000)에서 조사/추적 같은 긴 문구가 안전한지."""
        text = "\n".join(
            f"{i}. 철조망이 시야 끝까지 이어진다. 초소의 헌병이 이쪽을 오래 쳐다본다."
            for i in range(200))
        chunks = MessageChunker(max_length=5000).split_message(text)
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertLessEqual(len(c), 5000)


class SendPathTest(unittest.TestCase):
    """stream_handler 가 멘션·접두어를 빼고 예산을 잡는지 — 그 계산을 그대로 재현."""

    def _simulate(self, body, mentions, max_length, prefix=''):
        safe = max_length - (len(mentions) + 1) - len(prefix) - 10
        chunks = MessageChunker(max_length=max(50, safe)).split_message(body)
        return [f"{mentions} {prefix}{c}" if mentions.strip() else f"{prefix}{c}"
                for c in chunks]

    def test_mention_and_prefix_fit_inside_the_limit(self):
        body = "\n".join(f"{i}. 철조망이 시야 끝까지 이어진다." for i in range(400))
        for mentions in ('@deborah', '@deborah @hancham @clara @hugo @wonjun'):
            for prefix in ('', '◎ '):
                with self.subTest(mentions=mentions, prefix=prefix):
                    for sent in self._simulate(body, mentions, 5000, prefix):
                        self.assertLessEqual(len(sent), 5000)

    def test_spaceless_body_with_mentions(self):
        for sent in self._simulate("가" * 12000, '@deborah', 5000):
            self.assertLessEqual(len(sent), 5000)


if __name__ == '__main__':
    unittest.main()

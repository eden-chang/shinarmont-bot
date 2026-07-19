"""utils.target_helpers 단위 테스트."""

import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from commands.base_command import CommandContext
import utils.target_helpers as th
from utils.target_helpers import resolve_target, relay_dm, get_target_error


ROSTER = [
    {'이름': '한참', '아이디': 'alice', '은는': '은'},
    {'이름': '밥 이', '아이디': 'Bob', '은는': '는'},
]

MANAGEMENT = [
    {'아이디': 'alice', '소지금': 100, '소지품': '사과', '건강': 50, '이성': 40, '_row_number': 2},
    {'아이디': 'Bob', '소지금': 30, '소지품': '', '건강': 20, '이성': 10, '_row_number': 3},
]


def _make_sheets():
    sm = MagicMock()
    sm.get_roster_data.return_value = ROSTER
    sm.get_worksheet_data.return_value = MANAGEMENT
    return sm


class _FakeStatus:
    def __init__(self, mentions):
        self.mentions = mentions


class ResolveByKeywordTest(unittest.TestCase):
    def test_resolve_by_name(self):
        sm = _make_sheets()
        ctx = CommandContext(user_id='alice', keywords=['교류', '밥 이'])
        target = resolve_target(ctx, sm, keyword_index=1)
        self.assertIsNotNone(target)
        self.assertEqual(target['이름'], '밥 이')
        self.assertEqual(target['아이디'], 'Bob')
        self.assertEqual(target['은는'], '는')
        # 관리 시트 컬럼 병합
        self.assertEqual(target['소지금'], 30)
        self.assertEqual(target['건강'], 20)

    def test_name_matching_ignores_space_and_case(self):
        sm = _make_sheets()
        ctx = CommandContext(user_id='alice', keywords=['교류', '밥이'])
        target = resolve_target(ctx, sm, keyword_index=1)
        self.assertIsNotNone(target)
        self.assertEqual(target['아이디'], 'Bob')

    def test_unregistered_returns_none_with_reason(self):
        sm = _make_sheets()
        ctx = CommandContext(user_id='alice', keywords=['교류', '없는사람'])
        target = resolve_target(ctx, sm, keyword_index=1)
        self.assertIsNone(target)
        self.assertIn('찾을 수 없습니다', get_target_error(ctx))

    def test_self_target_blocked(self):
        sm = _make_sheets()
        ctx = CommandContext(user_id='alice', keywords=['교류', '한참'])
        target = resolve_target(ctx, sm, keyword_index=1)
        self.assertIsNone(target)
        self.assertIn('자기 자신', get_target_error(ctx))

    def test_missing_keyword_index(self):
        sm = _make_sheets()
        ctx = CommandContext(user_id='alice', keywords=['교류'])
        target = resolve_target(ctx, sm, keyword_index=1)
        self.assertIsNone(target)
        self.assertTrue(get_target_error(ctx))


class ResolveByMentionTest(unittest.TestCase):
    def test_resolve_from_mentions_excludes_bot(self):
        sm = _make_sheets()
        os.environ['BOT_ID_TEST_UNUSED'] = ''
        status = _FakeStatus([
            {'acct': 'shinarmont_bot'},   # 봇 (아래에서 config.BOT_ID 로 지정)
            {'acct': 'Bob'},
        ])
        ctx = CommandContext(user_id='alice', keywords=['교류'],
                             metadata={'original_status': status})
        # config.BOT_ID 를 봇으로 지정
        from config.settings import config as cfg
        orig = getattr(cfg, 'BOT_ID', '')
        cfg.BOT_ID = 'shinarmont_bot'
        try:
            target = resolve_target(ctx, sm, keyword_index=None)
        finally:
            cfg.BOT_ID = orig
        self.assertIsNotNone(target)
        self.assertEqual(target['아이디'], 'Bob')

    def test_no_mention_returns_none(self):
        sm = _make_sheets()
        status = _FakeStatus([])
        ctx = CommandContext(user_id='alice', keywords=['교류'],
                             metadata={'original_status': status})
        target = resolve_target(ctx, sm, keyword_index=None)
        self.assertIsNone(target)
        self.assertTrue(get_target_error(ctx))

    def test_mention_self_excluded(self):
        sm = _make_sheets()
        status = _FakeStatus([{'acct': 'alice'}])
        ctx = CommandContext(user_id='alice', keywords=['교류'],
                             metadata={'original_status': status})
        target = resolve_target(ctx, sm, keyword_index=None)
        self.assertIsNone(target)

    def test_bot_self_excluded_by_real_acct(self):
        """봇의 실제 @username 이 BOTn_NAME(디스플레이명)과 달라도 대상에서 제외된다.

        @STORY 를 태그해 명령을 부를 때, 봇 자신을 대상으로 오인하지 않아야 한다.
        `_bot_accts()`(디스플레이명 기반)는 'story_official' 을 모르지만,
        stream_handler 가 넣어 준 실제 acct(bot_acct)로 제외된다.
        """
        sm = _make_sheets()
        status = _FakeStatus([
            {'acct': 'story_official'},   # 봇 자신 (디스플레이명 STORY 와 다른 실제 계정)
            {'acct': 'Bob'},              # 진짜 대상
        ])
        ctx = CommandContext(user_id='alice', keywords=['교류'],
                             metadata={'original_status': status,
                                       'bot_acct': 'story_official'})
        target = resolve_target(ctx, sm, keyword_index=None)
        self.assertIsNotNone(target)
        self.assertEqual(target['아이디'], 'Bob')

    def test_bot_self_excluded_ignores_domain(self):
        """도메인이 붙은 봇 acct(story@instance)도 제외된다."""
        sm = _make_sheets()
        status = _FakeStatus([
            {'acct': 'story@shinarmont.social'},
            {'acct': 'Bob'},
        ])
        ctx = CommandContext(user_id='alice', keywords=['교류'],
                             metadata={'original_status': status,
                                       'bot_acct': 'story'})
        target = resolve_target(ctx, sm, keyword_index=None)
        self.assertIsNotNone(target)
        self.assertEqual(target['아이디'], 'Bob')

    def test_bot_mentioned_first_regardless_of_order(self):
        """봇 멘션이 대상 멘션보다 앞에 와도 대상은 올바르게 해석된다."""
        sm = _make_sheets()
        status = _FakeStatus([
            {'acct': 'story_official'},   # 봇이 먼저 멘션됨
            {'acct': 'Bob'},
        ])
        ctx = CommandContext(user_id='alice', keywords=['교류'],
                             metadata={'original_status': status,
                                       'bot_acct': 'story_official'})
        self.assertEqual(
            resolve_target(ctx, sm, keyword_index=None)['아이디'], 'Bob')


class ParseCommandPositionTest(unittest.TestCase):
    """[교류/농도] 태그가 문장 어디에 있어도(멘션·잡텍스트와 섞여도) 키워드가 추출된다."""

    def _parse(self, text):
        from handlers.command_router import parse_command_from_text
        return parse_command_from_text(text)

    def test_tag_after_bot_mention(self):
        self.assertEqual(self._parse('@STORY [교류/3] @character'), ['교류', '3'])

    def test_tag_before_mentions(self):
        self.assertEqual(self._parse('[교류/3] @STORY @character'), ['교류', '3'])

    def test_tag_with_garbage_text_between(self):
        self.assertEqual(
            self._parse('@STORY [교류/3] 이야래ㅓ햐ㅐ허 @character'), ['교류', '3'])


class RelayDmTest(unittest.TestCase):
    def test_relay_dm_calls_queue_dm(self):
        called = {}

        import utils.dm_sender as dm_sender

        def fake_queue(receiver_id, message):
            called['receiver'] = receiver_id
            called['message'] = message

        orig = dm_sender.queue_dm
        dm_sender.queue_dm = fake_queue
        try:
            ok = relay_dm('Bob', '안녕')
        finally:
            dm_sender.queue_dm = orig
        self.assertTrue(ok)
        self.assertEqual(called['receiver'], 'Bob')
        self.assertEqual(called['message'], '안녕')

    def test_relay_dm_empty_receiver(self):
        self.assertFalse(relay_dm('', '안녕'))


if __name__ == '__main__':
    unittest.main()

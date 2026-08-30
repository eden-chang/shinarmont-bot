# -*- coding: utf-8 -*-
"""DM 채팅(direct) 알림 처리 테스트.

마스토돈 DM 이 채팅 UI 로 바뀌면서 서버가 direct 멘션을 알림 목록에서 감춘다.
이 봇은 폴링만 쓰고 direct 만 처리하므로, 대응이 빠지면 **받을 수 있는 알림이
하나도 없다.** 그때 지켜야 하는 것들을 고정한다.

  - 폴링에 include_direct_messages 를 붙인다 (수신 자체)
  - 자동봇이 쓴 글에는 반응하지 않는다 (봇끼리 무한 루프 방지)
  - 답장은 참여자를 한 명도 빠뜨리지 않는다 (멘션 조합이 곧 방이다)
  - 답장은 방의 루트에 단다 (인용 말풍선 방지)
"""

import os
import re
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.notification_handler import NotificationHandler, StoryCommand
from config.settings import config


def make_handler(me='storybot'):
    """마스토돈·시트 접근 없이 핸들러만 만든다"""
    handler = NotificationHandler.__new__(NotificationHandler)
    handler.command_receiver_username = me
    handler.command_receiver_account_name = 'STORY'
    handler.command_receiver_client = MagicMock()
    handler.command_patterns = {
        'story': re.compile(r'\[스토리/(.+?)\]', re.IGNORECASE),
        'script': re.compile(r'\[스진/(.+?)\]', re.IGNORECASE),
        'story_progress': re.compile(r'\[스토리진행/(.+?)\]', re.IGNORECASE),
    }
    return handler


class FetchNotificationsTest(unittest.TestCase):
    """수신 (DM 채팅 서버에서 알림이 아예 안 보이던 것)"""

    def setUp(self):
        self.handler = make_handler()
        self.api = self.handler.command_receiver_client.mastodon

    def test_include_direct_messages(self):
        """이 파라미터가 없으면 direct 명령어가 통째로 보이지 않는다"""
        self.api._Mastodon__api_request.return_value = [{'id': '1'}]

        result = self.handler._fetch_notifications(limit=20)

        self.assertEqual(result, [{'id': '1'}])
        method, path, params = self.api._Mastodon__api_request.call_args[0]
        self.assertEqual(method, 'GET')
        self.assertEqual(path, '/api/v1/notifications')
        self.assertEqual(params['include_direct_messages'], 'true')
        self.assertEqual(params['limit'], 20)

    def test_falls_back_when_internal_path_gone(self):
        """내부 경로가 막혀도 봇이 통째로 죽지는 않는다"""
        self.api._Mastodon__api_request.side_effect = AttributeError('없음')
        self.api.notifications.return_value = [{'id': '2'}]

        self.assertEqual(self.handler._fetch_notifications(), [{'id': '2'}])

    def test_none_becomes_empty_list(self):
        self.api._Mastodon__api_request.return_value = None
        self.assertEqual(self.handler._fetch_notifications(), [])


class AutomatedAuthorTest(unittest.TestCase):
    """자동봇 판정 (루프 차단)"""

    def setUp(self):
        self.handler = make_handler()

    def test_self_authored(self):
        self.assertTrue(self.handler._is_automated_author({'acct': 'storybot'}))

    def test_bot_flag(self):
        self.assertTrue(self.handler._is_automated_author({'acct': 'other', 'bot': True}))

    def test_configured_account(self):
        original = config.IGNORED_BOT_ACCOUNTS
        config.IGNORED_BOT_ACCOUNTS = ['sto_bot']
        try:
            self.assertTrue(self.handler._is_automated_author({'acct': 'STO_Bot'}))
            self.assertFalse(self.handler._is_automated_author({'acct': 'runner'}))
        finally:
            config.IGNORED_BOT_ACCOUNTS = original

    def test_human(self):
        self.assertFalse(self.handler._is_automated_author({'acct': 'runner'}))

    def test_broken_account_is_treated_as_human(self):
        """판정에 실패했다고 멘션을 버리면 봇이 조용히 먹통이 된다"""
        self.assertFalse(self.handler._is_automated_author({}))
        self.assertFalse(self.handler._is_automated_author(None))


class RoomParticipantsTest(unittest.TestCase):
    """참여자 유지 (멘션 조합이 곧 방이다)"""

    def setUp(self):
        self.handler = make_handler()

    def test_keeps_everyone_and_drops_self(self):
        status = {'mentions': [{'acct': 'storybot'}, {'acct': 'a'}, {'acct': 'b'}]}
        account = {'acct': 'runner'}

        self.assertEqual(
            self.handler._extract_room_participants(status, account),
            ('runner', 'a', 'b'),
        )

    def test_sender_is_not_duplicated(self):
        status = {'mentions': [{'acct': 'runner'}, {'acct': 'a'}]}
        account = {'acct': 'runner'}

        self.assertEqual(
            self.handler._extract_room_participants(status, account),
            ('runner', 'a'),
        )

    def test_no_mentions(self):
        self.assertEqual(
            self.handler._extract_room_participants({}, {'acct': 'runner'}),
            ('runner',),
        )


class ReplyTargetTest(unittest.TestCase):
    """답장 대상 (인용 말풍선 방지)"""

    def setUp(self):
        self.handler = make_handler()

    def test_root_is_carried_into_command(self):
        command = self.handler._parse_command(
            '@storybot [스토리/1일차]', 'runner', '7', '30', '100',
            root_toot_id='90', mentions=('runner', 'a'),
        )

        self.assertIsNotNone(command)
        self.assertEqual(command.root_toot_id, '90')
        self.assertEqual(command.mentions, ('runner', 'a'))

    def test_first_toot_of_room_is_its_own_root(self):
        command = self.handler._parse_command(
            '@storybot [스진/1일차]', 'runner', '7', '30', '100',
        )

        self.assertEqual(command.root_toot_id, '100')

    def test_reply_goes_to_root_with_every_participant(self):
        command = StoryCommand(
            command_type='story', worksheet_name='1일차', sender_username='runner',
            sender_id='7', notification_id='30', toot_id='100', timestamp=None,
            root_toot_id='90', mentions=('runner', 'a'),
        )

        self.handler._send_command_response(command, '워크시트가 없습니다.')

        kwargs = self.handler.command_receiver_client.mastodon.status_post.call_args[1]
        self.assertEqual(kwargs['in_reply_to_id'], '90')
        self.assertEqual(kwargs['visibility'], 'direct')
        self.assertTrue(kwargs['status'].startswith('@runner @a '))

    def test_falls_back_to_sender_when_participants_unknown(self):
        command = StoryCommand(
            command_type='story', worksheet_name='1일차', sender_username='runner',
            sender_id='7', notification_id='30', toot_id='100', timestamp=None,
        )

        self.handler._send_command_response(command, '오류')

        kwargs = self.handler.command_receiver_client.mastodon.status_post.call_args[1]
        self.assertEqual(kwargs['in_reply_to_id'], '100')
        self.assertTrue(kwargs['status'].startswith('@runner '))


if __name__ == '__main__':
    unittest.main()

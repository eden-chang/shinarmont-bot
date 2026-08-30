"""DM 채팅(direct) 멘션 처리 테스트.

마스토돈 DM 이 채팅 UI 로 바뀌면서 명령어가 전부 direct 로 들어온다.
그때 봇이 지켜야 하는 것들을 고정한다.

  - 자동봇이 쓴 글에는 반응하지 않는다 (봇끼리 무한 루프 방지)
  - DM 답장은 참여자를 한 명도 빠뜨리지 않는다 (멘션 조합이 곧 방이다)
  - DM 답장은 방의 루트에 단다 (인용 말풍선 방지)
  - 폴링 폴백의 첫 조회는 기준점만 잡는다 (명령어 재실행 방지)
"""

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from handlers.stream_handler import BotStreamHandler, MentionManager, StreamManager


def make_status(acct='runner', bot=False, visibility='direct', in_reply_to_id=None,
                status_id='100', mentions=None, content='<p>[행동]</p>'):
    """마스토돈 status 객체 흉내"""
    return SimpleNamespace(
        id=status_id,
        account=SimpleNamespace(acct=acct, bot=bot, display_name=acct),
        visibility=visibility,
        in_reply_to_id=in_reply_to_id,
        mentions=mentions if mentions is not None else [{'acct': 'botsys'}],
        content=content,
    )


def make_handler(bot_acct='botsys'):
    """API·시트 접근 없이 핸들러만 만든다"""
    handler = BotStreamHandler.__new__(BotStreamHandler)
    handler.api = MagicMock()
    handler.sheets_manager = None
    handler._bot_acct_cache = bot_acct
    return handler


class AutomatedAuthorTest(unittest.TestCase):
    """자동봇 판정 (루프 차단)"""

    def setUp(self):
        self.handler = make_handler()

    def test_self_authored(self):
        self.assertTrue(self.handler._is_automated_author(make_status(acct='botsys')))

    def test_bot_flag(self):
        self.assertTrue(self.handler._is_automated_author(make_status(acct='other', bot=True)))

    def test_configured_account(self):
        from config.settings import config
        original = config.IGNORED_BOT_ACCOUNTS
        config.IGNORED_BOT_ACCOUNTS = ['botstore']
        try:
            self.assertTrue(self.handler._is_automated_author(make_status(acct='BotStore')))
            self.assertFalse(self.handler._is_automated_author(make_status(acct='runner')))
        finally:
            config.IGNORED_BOT_ACCOUNTS = original

    def test_human(self):
        self.assertFalse(self.handler._is_automated_author(make_status(acct='runner')))

    def test_broken_status_is_treated_as_human(self):
        """판정에 실패했다고 멘션을 버리면 봇이 조용히 먹통이 된다"""
        self.assertFalse(self.handler._is_automated_author(SimpleNamespace()))


class ReplyTargetTest(unittest.TestCase):
    """답장 대상 (인용 말풍선 방지)"""

    def setUp(self):
        self.handler = make_handler()

    def test_direct_reply_goes_to_room_root(self):
        status = make_status(status_id='200', in_reply_to_id='50')
        self.assertEqual(self.handler._reply_target_id(status), '50')

    def test_direct_first_message_is_its_own_root(self):
        status = make_status(status_id='200', in_reply_to_id=None)
        self.assertEqual(self.handler._reply_target_id(status), '200')

    def test_public_mention_replies_to_the_status_itself(self):
        status = make_status(status_id='200', in_reply_to_id='50', visibility='public')
        self.assertEqual(self.handler._reply_target_id(status), '200')

    def test_private_mention_replies_to_the_status_itself(self):
        status = make_status(status_id='200', in_reply_to_id='50', visibility='private')
        self.assertEqual(self.handler._reply_target_id(status), '200')


class MentionPreservationTest(unittest.TestCase):
    """DM 멘션 보존 (방 식별)"""

    def test_keep_all_never_drops_anyone(self):
        members = [f'member{i:02d}' for i in range(19)]
        mentions = MentionManager.format_mentions(members, keep_all=True)

        for member in members:
            self.assertIn(f"@{member}", mentions)
        self.assertNotIn('외', mentions)

    def test_public_reply_still_truncates(self):
        members = [f'member{i:02d}' for i in range(19)]
        mentions = MentionManager.format_mentions(members)

        self.assertLessEqual(
            len(mentions.split()),
            MentionManager.MAX_USERS_TO_MENTION + 2  # "외 N명" 포함
        )

    def test_empty(self):
        self.assertEqual(MentionManager.format_mentions([], keep_all=True), "")


class ExtractMentionedUsersTest(unittest.TestCase):
    """방 참여자 추출 — 다른 봇도 포함해야 방 조합이 유지된다"""

    def setUp(self):
        self.handler = make_handler()

    def test_includes_author_and_other_members(self):
        status = make_status(
            acct='runner',
            mentions=[{'acct': 'botsys'}, {'acct': 'friend'}],
        )
        users = self.handler._extract_mentioned_users(status)

        self.assertIn('runner', users)   # 작성자
        self.assertIn('friend', users)   # 다른 멤버
        self.assertNotIn('botsys', users)  # 나 자신은 글쓴이가 되므로 뺀다


class PollingBaselineTest(unittest.TestCase):
    """폴링 폴백 첫 조회 (명령어 재실행 방지)"""

    def _make_manager(self, last_seen=None):
        manager = StreamManager.__new__(StreamManager)
        manager.api = MagicMock()
        manager.handler = MagicMock()
        manager.handler.last_seen_notification_id = last_seen
        manager.last_notification_id = None
        return manager

    def test_resumes_from_where_streaming_stopped(self):
        """스트리밍이 처리한 지점을 이어받는다 — 재실행도, 누락도 없어야 한다"""
        manager = self._make_manager(last_seen='77')
        fresh = SimpleNamespace(id='78', type='mention', account=SimpleNamespace(acct='runner'))
        manager._fetch_notifications = MagicMock(return_value=[fresh])

        manager._check_new_notifications()

        self.assertEqual(manager._fetch_notifications.call_args.kwargs['since_id'], '77')
        manager.handler.on_notification.assert_called_once_with(fresh)

    def test_first_poll_only_sets_baseline(self):
        manager = self._make_manager()
        old = SimpleNamespace(id='9', type='mention', account=SimpleNamespace(acct='runner'))
        manager._fetch_notifications = MagicMock(return_value=[old])

        manager._check_new_notifications()

        self.assertEqual(manager.last_notification_id, '9')
        manager.handler.on_notification.assert_not_called()

    def test_second_poll_processes(self):
        manager = self._make_manager()
        manager.last_notification_id = '9'
        fresh = SimpleNamespace(id='10', type='mention', account=SimpleNamespace(acct='runner'))
        manager._fetch_notifications = MagicMock(return_value=[fresh])

        manager._check_new_notifications()

        self.assertEqual(manager.last_notification_id, '10')
        manager.handler.on_notification.assert_called_once_with(fresh)

    def test_fetch_asks_for_direct_messages(self):
        manager = self._make_manager()
        manager.api._Mastodon__api_request = MagicMock(return_value=[])

        manager._fetch_notifications(limit=20, since_id='7')

        _, endpoint, params = manager.api._Mastodon__api_request.call_args[0]
        self.assertEqual(endpoint, '/api/v1/notifications')
        self.assertEqual(params['include_direct_messages'], 'true')
        self.assertEqual(params['since_id'], '7')


class MarkRoomsReadTest(unittest.TestCase):
    """채팅방 읽음 처리 — 봇에게 남는 "안 읽은 사람 1" 지우기"""

    def _make_manager(self):
        manager = StreamManager.__new__(StreamManager)
        manager.api = MagicMock()
        manager.handler = MagicMock()
        manager._dm_room_read_supported = True
        return manager

    def test_marks_only_rooms_with_unread(self):
        manager = self._make_manager()
        manager.api._Mastodon__api_request = MagicMock(return_value=[
            {'id': '1', 'unread_count': 3},
            {'id': '2', 'unread_count': 0},
            {'id': '3', 'unread_count': 1},
        ])

        marked = manager.mark_dm_rooms_read()

        self.assertEqual(marked, 2)
        posted = [
            call[0][1] for call in manager.api._Mastodon__api_request.call_args_list
            if call[0][0] == 'POST'
        ]
        self.assertEqual(posted, ['/api/v1/dm_rooms/1/read', '/api/v1/dm_rooms/3/read'])

    def test_stops_forever_when_the_server_has_no_chat(self):
        """채팅이 없는 서버에서 5분마다 404 를 만들면 로그만 더러워진다"""
        import mastodon

        manager = self._make_manager()
        manager.api._Mastodon__api_request = MagicMock(
            side_effect=mastodon.MastodonNotFoundError('404')
        )

        self.assertEqual(manager.mark_dm_rooms_read(), 0)
        self.assertFalse(manager._dm_room_read_supported)

        manager.api._Mastodon__api_request.reset_mock()
        self.assertEqual(manager.mark_dm_rooms_read(), 0)
        manager.api._Mastodon__api_request.assert_not_called()

    def test_one_broken_room_does_not_stop_the_rest(self):
        manager = self._make_manager()

        def request(method, url, params=None):
            if method == 'GET':
                return [{'id': '1', 'unread_count': 1}, {'id': '2', 'unread_count': 1}]
            if url.startswith('/api/v1/dm_rooms/1/'):
                raise RuntimeError('boom')
            return {}

        manager.api._Mastodon__api_request = MagicMock(side_effect=request)

        self.assertEqual(manager.mark_dm_rooms_read(), 1)

    def test_transient_failure_keeps_the_feature_on(self):
        manager = self._make_manager()
        manager.api._Mastodon__api_request = MagicMock(side_effect=RuntimeError('network'))

        self.assertEqual(manager.mark_dm_rooms_read(), 0)
        self.assertTrue(manager._dm_room_read_supported)


if __name__ == '__main__':
    unittest.main()

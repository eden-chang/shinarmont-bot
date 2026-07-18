"""commands/shinarmont/talk_command.py 단위/스모크 테스트 (페이크 매니저).

2026-07-18 개편: **공유 타래 세션** 모델.
- 세션은 session_key(최초 [대화] 툿 id)로 식별한다.
- 이어가기는 핸들러가 실어주는 reply_thread(session_key)로 라우팅된다.
- 하루 1회 제한은 시작자만 소모하고, 연속일 금지도 시작자↔시작 상대에만 적용한다.
"""

import os
import shutil
import tempfile
import sys
import unittest

from utils import action_log
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from commands.base_command import CommandContext
from utils.json_store import JsonStore
from commands.shinarmont import talk_command
from commands.shinarmont.talk_command import TalkCommand


ROSTER = [
    {'이름': '가나', '아이디': 'alice', '은는': '는'},
    {'이름': '다람', '아이디': 'bob', '은는': '은'},
]

MGMT = [
    {'이름': '가나', '아이디': 'alice', '건강': 80, '이성': 50, '_row_number': 3},
    {'이름': '다람', '아이디': 'bob', '건강': 70, '이성': 90, '_row_number': 4},
]


class _FakeStatus:
    def __init__(self, mentions, status_id='s1'):
        self.mentions = mentions
        self.id = status_id


class _FakeGameState:
    """game_state 모듈 대체 (인메모리)."""

    def __init__(self, initial=None):
        self.d = initial or {}

    def get(self, uid, key, default=None):
        return self.d.get(str(uid), {}).get(key, default)

    def set(self, uid, key, val):
        self.d.setdefault(str(uid), {})[key] = val

    def check_and_set(self, uid, key, limit):
        cur = self.d.get(str(uid), {}).get(key, 0)
        try:
            cur = int(cur)
        except (TypeError, ValueError):
            cur = 0
        if cur >= limit:
            return False
        self.d.setdefault(str(uid), {})[key] = cur + 1
        return True


def _make_sheets():
    sm = MagicMock()
    sm.get_roster_data.return_value = ROSTER
    sm.get_worksheet_data.return_value = MGMT
    return sm


def _make_context(keywords, mentions=None, visibility='direct',
                  user_id='alice', user_name='가나', status_id='s1',
                  session_key=None):
    """세션 시작용: mentions 지정. 이어가기용: session_key 지정."""
    meta = {'visibility': visibility, 'status_id': status_id}
    if mentions is not None:
        meta['original_status'] = _FakeStatus(mentions, status_id)
        meta['is_reply'] = False
    if session_key is not None:
        meta['reply_thread'] = {'keyword': '대화', 'session_key': session_key}
    return CommandContext(user_id=user_id, user_name=user_name, keywords=keywords, metadata=meta)


class TalkBaseTest(unittest.TestCase):
    def setUp(self):
        # 세션 싱글톤 초기화 + **임시 파일로 격리**.
        self._tmpdir = tempfile.mkdtemp()
        talk_command._session_manager = talk_command.TalkSessionManager(
            store=JsonStore(os.path.join(self._tmpdir, 'talk_sessions.json'))
        )
        self.gs = _FakeGameState()
        self.system = MagicMock()
        self.sm = _make_sheets()
        # 타래 레지스트리는 디스크를 건드리므로 목으로 대체
        self._rt = MagicMock()
        self._gs_patch = patch.object(talk_command, 'game_state', self.gs)
        self._rt_patch = patch.object(talk_command, 'reply_threads', self._rt)
        self._gs_patch.start()
        self._rt_patch.start()

    def tearDown(self):
        self._gs_patch.stop()
        self._rt_patch.stop()
        talk_command._session_manager = None
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _cmd(self):
        return TalkCommand(sheets_manager=self.sm, api=None, system_sheets_manager=self.system)

    def _mgr(self):
        return talk_command.get_talk_session_manager()


class TalkStartTest(TalkBaseTest):
    def test_dm_only_blocks_public(self):
        ctx = _make_context(['대화'], mentions=[{'acct': 'bob'}], visibility='public')
        resp = self._cmd().execute(ctx)
        self.assertFalse(resp.success)
        self.assertIsNone(self._mgr().get('s1'))

    def test_story_bot_mention_is_ignored_as_target(self):
        # @STORY(이야기 봇 자신)는 대상에서 제외 → 상대(bob)만 지정돼도 진행
        os.environ['BOT9_NAME'] = 'STORY'
        try:
            ctx = _make_context(['대화'], mentions=[{'acct': 'STORY'}, {'acct': 'bob'}])
            resp = self._cmd().execute(ctx)
        finally:
            os.environ.pop('BOT9_NAME', None)
        self.assertTrue(resp.success, resp.message)
        self.assertIn('다람', resp.message)
        session = self._mgr().get('s1')
        self.assertIsNotNone(session)
        self.assertEqual(session.partner_name, '다람')

    def test_consecutive_ban_matches_any_in_partner_list(self):
        self.gs.set('alice', '어제대화상대', '다람,여우')
        ctx = _make_context(['대화'], mentions=[{'acct': 'bob'}])
        resp = self._cmd().execute(ctx)
        self.assertFalse(resp.success)
        self.assertIn('어제', resp.message)

    def test_start_records_partner_as_list(self):
        ctx = _make_context(['대화'], mentions=[{'acct': 'bob'}])
        resp = self._cmd().execute(ctx)
        self.assertTrue(resp.success, resp.message)
        self.assertEqual(self.gs.get('alice', '오늘대화상대'), '다람')

    def test_start_success(self):
        ctx = _make_context(['대화'], mentions=[{'acct': 'bob'}])
        resp = self._cmd().execute(ctx)
        self.assertTrue(resp.success, resp.message)
        self.assertIn('다람', resp.message)
        session = self._mgr().get('s1')  # session_key = status_id 's1'
        self.assertIsNotNone(session)
        self.assertEqual(session.total, 1)
        self.assertEqual(session.initiator_acct, 'alice')
        self.assertEqual(session.partner_acct, 'bob')
        self.assertEqual(self.gs.get('alice', '오늘대화여부'), 1)
        self.assertEqual(self.gs.get('alice', '오늘대화상대'), '다람')
        # 타래 라우팅: 시작 툿 링크 + 봇 답글 예약
        self.assertTrue(self._rt.link.called)
        self.assertTrue(self._rt.stage.called)

    def test_no_mention_rejected(self):
        ctx = _make_context(['대화'], mentions=[])
        resp = self._cmd().execute(ctx)
        self.assertFalse(resp.success)
        self.assertIsNone(self.gs.get('alice', '오늘대화여부'))

    def test_self_target_rejected(self):
        ctx = _make_context(['대화'], mentions=[{'acct': 'alice'}])
        resp = self._cmd().execute(ctx)
        self.assertFalse(resp.success)

    def test_consecutive_day_blocked(self):
        self.gs.set('alice', '어제대화상대', '다람')
        ctx = _make_context(['대화'], mentions=[{'acct': 'bob'}])
        resp = self._cmd().execute(ctx)
        self.assertFalse(resp.success)
        self.assertIsNone(self.gs.get('alice', '오늘대화여부'))

    def test_daily_limit_blocks_second_start(self):
        self.gs.set('alice', '오늘대화여부', 1)
        ctx = _make_context(['대화'], mentions=[{'acct': 'bob'}])
        resp = self._cmd().execute(ctx)
        self.assertFalse(resp.success)
        self.assertIsNone(self._mgr().get('s1'))

    def test_invited_partner_not_charged_daily(self):
        # bob이 alice를 초대해도(=alice가 시작상대), alice의 일일제한은 소모되지 않는다.
        cmd = self._cmd()
        cmd.execute(_make_context(['대화'], mentions=[{'acct': 'alice'}],
                                  user_id='bob', user_name='다람', status_id='b1'))
        self.assertEqual(self.gs.get('bob', '오늘대화여부'), 1)
        self.assertIsNone(self.gs.get('alice', '오늘대화여부'))


class TalkContinueTest(TalkBaseTest):
    def test_shared_session_continues_both_ways_and_ends(self):
        with patch.object(talk_command, '_total_cap', return_value=3):
            cmd = self._cmd()
            # 시작 (alice, 카운트1)
            r1 = cmd.execute(_make_context(['대화'], mentions=[{'acct': 'bob'}], status_id='s1'))
            self.assertTrue(r1.success)
            # 상대(bob)가 이어감 (카운트2) — [대화] 없이, reply_thread 라우팅
            r2 = cmd.execute(_make_context(['대화'], session_key='s1',
                                           user_id='bob', user_name='다람', status_id='s2'))
            self.assertTrue(r2.success)
            self.assertEqual(r2.message, '')  # 중간 턴은 침묵
            self.assertEqual(self._mgr().get('s1').total, 2)
            # 시작자(alice)가 이어감 (카운트3 → 캡 도달 → 종료)
            r3 = cmd.execute(_make_context(['대화'], session_key='s1',
                                           user_id='alice', user_name='가나', status_id='s3'))
            self.assertTrue(r3.success)
            self.assertIn('종료', r3.message)
            self.assertIsNone(self._mgr().get('s1'))  # 세션 제거됨

        # 행동로그 기록됨
        self.system.append_row.assert_called_once()
        log_args = self.system.append_row.call_args[0]
        self.assertEqual(log_args[0], '행동로그')
        self.assertEqual(len(log_args[1]), 7)
        self.assertEqual(log_args[1][action_log.COLUMNS.index('종류')], '대화')
        self.assertEqual(log_args[1][action_log.COLUMNS.index('행위자')], '가나')
        self.assertEqual(log_args[1][4], '다람')

    def test_continue_on_dead_session_is_silent(self):
        # 이미 종료된(없는) 세션으로 라우팅되면 조용히 무시(빈 성공)
        cmd = self._cmd()
        resp = cmd.execute(_make_context(['대화'], session_key='ghost',
                                         user_id='bob', status_id='z9'))
        self.assertTrue(resp.success)
        self.assertEqual(resp.message, '')


if __name__ == '__main__':
    unittest.main()

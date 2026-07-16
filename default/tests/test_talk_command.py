"""commands/shinarmont/talk_command.py 단위/스모크 테스트 (페이크 매니저)."""

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
                  user_id='alice', user_name='가나', status_id='s1'):
    meta = {'visibility': visibility, 'status_id': status_id}
    if mentions is not None:
        meta['original_status'] = _FakeStatus(mentions, status_id)
        meta['is_reply'] = False
    return CommandContext(user_id=user_id, user_name=user_name, keywords=keywords, metadata=meta)


class TalkBaseTest(unittest.TestCase):
    def setUp(self):
        # 세션 싱글톤 초기화 + **임시 파일로 격리**.
        # 세션이 디스크 영속이라 그냥 싱글톤만 None으로 두면 프로젝트의
        # state/{슬롯}/talk_sessions.json을 다시 읽어 이전 테스트의 세션이 새어 든다.
        self._tmpdir = tempfile.mkdtemp()
        talk_command._session_manager = talk_command.TalkSessionManager(
            store=JsonStore(os.path.join(self._tmpdir, 'talk_sessions.json'))
        )
        self.gs = _FakeGameState()
        self.system = MagicMock()
        self.sm = _make_sheets()
        self._gs_patch = patch.object(talk_command, 'game_state', self.gs)
        self._gs_patch.start()

    def tearDown(self):
        self._gs_patch.stop()
        talk_command._session_manager = None
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _cmd(self):
        return TalkCommand(sheets_manager=self.sm, api=None, system_sheets_manager=self.system)


class TalkStartTest(TalkBaseTest):
    def test_dm_only_blocks_public(self):
        ctx = _make_context(['대화'], mentions=[{'acct': 'bob'}], visibility='public')
        resp = self._cmd().execute(ctx)
        self.assertFalse(resp.success)
        # 세션/일일제한 미소모
        self.assertIsNone(talk_command.get_talk_session_manager().get('alice'))

    def test_consecutive_ban_matches_any_in_partner_list(self):
        # 어제대화상대가 쉼표 목록(1차+2차)이면 그 안의 누구와도 연이어 대화 금지
        self.gs.set('alice', '어제대화상대', '다람,여우')
        ctx = _make_context(['대화'], mentions=[{'acct': 'bob'}])  # bob=다람
        resp = self._cmd().execute(ctx)
        self.assertFalse(resp.success)
        self.assertIn('어제', resp.message)

    def test_start_records_partner_as_list(self):
        ctx = _make_context(['대화'], mentions=[{'acct': 'bob'}])
        resp = self._cmd().execute(ctx)
        self.assertTrue(resp.success, resp.message)
        # 오늘대화상대는 쉼표 목록 형식으로 누적(시작 시 1차 상대)
        self.assertEqual(self.gs.get('alice', '오늘대화상대'), '다람')

    def test_start_success(self):
        ctx = _make_context(['대화'], mentions=[{'acct': 'bob'}])
        resp = self._cmd().execute(ctx)
        self.assertTrue(resp.success, resp.message)
        self.assertIn('다람', resp.message)
        # 세션 생성 + 첫 멘션 카운트=1
        session = talk_command.get_talk_session_manager().get('alice')
        self.assertIsNotNone(session)
        self.assertEqual(session.total, 1)
        self.assertEqual(session.per_partner['다람'], 1)
        # 일일제한 소모 + 오늘대화상대 기록
        self.assertEqual(self.gs.get('alice', '오늘대화여부'), 1)
        self.assertEqual(self.gs.get('alice', '오늘대화상대'), '다람')

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
        # 어제 다람과 대화했다면 오늘 다람 재대화 금지
        self.gs.set('alice', '어제대화상대', '다람')
        ctx = _make_context(['대화'], mentions=[{'acct': 'bob'}])
        resp = self._cmd().execute(ctx)
        self.assertFalse(resp.success)
        # 연속일 차단은 일일제한을 소모하지 않는다
        self.assertIsNone(self.gs.get('alice', '오늘대화여부'))

    def test_daily_limit_blocks_second_start(self):
        # 이미 오늘 대화함
        self.gs.set('alice', '오늘대화여부', 1)
        ctx = _make_context(['대화'], mentions=[{'acct': 'bob'}])
        resp = self._cmd().execute(ctx)
        self.assertFalse(resp.success)
        self.assertIsNone(talk_command.get_talk_session_manager().get('alice'))


class TalkContinueTest(TalkBaseTest):
    def test_session_ends_at_per_cap_and_logs(self):
        cmd = self._cmd()
        # 시작(카운트1)
        r1 = cmd.execute(_make_context(['대화'], mentions=[{'acct': 'bob'}]))
        self.assertTrue(r1.success)
        # 이어가기(카운트2)
        r2 = cmd.execute(_make_context(['대화'], mentions=[{'acct': 'bob'}]))
        self.assertTrue(r2.success)
        session = talk_command.get_talk_session_manager().get('alice')
        self.assertEqual(session.total, 2)
        # 이어가기(카운트3 -> 캐릭터당 3회 도달 -> 종료)
        r3 = cmd.execute(_make_context(['대화'], mentions=[{'acct': 'bob'}]))
        self.assertTrue(r3.success)
        self.assertIn('종료', r3.message)
        # 세션 제거됨
        self.assertIsNone(talk_command.get_talk_session_manager().get('alice'))
        # 행동로그 append(kind='대화', 9열) 기록됨
        self.system.append_row.assert_called_once()
        log_args = self.system.append_row.call_args[0]
        self.assertEqual(log_args[0], '행동로그')
        self.assertEqual(len(log_args[1]), 7)  # 7열 고정 (2026-07-16)
        self.assertEqual(log_args[1][action_log.COLUMNS.index('종류')], '대화')   # 종류
        self.assertEqual(log_args[1][action_log.COLUMNS.index('행위자')], '가나')  # 명단 이름
        self.assertEqual(log_args[1][4], '다람')   # 대상

    def test_continue_without_mention_uses_primary(self):
        cmd = self._cmd()
        cmd.execute(_make_context(['대화'], mentions=[{'acct': 'bob'}]))
        # 멘션 없이 이어가기 → 최초 상대(다람) 카운트
        r2 = cmd.execute(_make_context(['대화'], mentions=[]))
        self.assertTrue(r2.success)
        session = talk_command.get_talk_session_manager().get('alice')
        self.assertEqual(session.per_partner['다람'], 2)


if __name__ == '__main__':
    unittest.main()

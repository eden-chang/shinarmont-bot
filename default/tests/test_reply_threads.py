"""utils/reply_threads.py 레지스트리 + 핸들러 답글-스레드 라우팅 결정 테스트."""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils import reply_threads
from utils.json_store import JsonStore


class ReplyThreadRegistryTest(unittest.TestCase):
    def setUp(self):
        # 레지스트리가 디스크 영속이라 임시 파일로 격리한다.
        # (안 하면 프로젝트의 state/{슬롯}/reply_threads.json을 테스트가 오염시킨다)
        self._tmpdir = tempfile.mkdtemp()
        reply_threads.set_registry(reply_threads._ReplyThreadRegistry(
            store=JsonStore(os.path.join(self._tmpdir, 'reply_threads.json'))
        ))

    def tearDown(self):
        reply_threads.set_registry(None)
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_stage_commit_resolve_roundtrip(self):
        reply_threads.stage('alice', '의무실', 'sid-1')
        self.assertTrue(reply_threads.commit('alice', 'R0'))
        entry = reply_threads.resolve('R0')
        self.assertIsNotNone(entry)
        self.assertEqual(entry['keyword'], '의무실')
        self.assertEqual(entry['user_id'], 'alice')
        self.assertEqual(entry['session_key'], 'sid-1')

    def test_commit_without_stage_is_noop(self):
        self.assertFalse(reply_threads.commit('bob', 'R9'))
        self.assertIsNone(reply_threads.resolve('R9'))

    def test_discard_cancels_pending(self):
        reply_threads.stage('alice', '의무실', 'sid-1')
        reply_threads.discard('alice')
        self.assertFalse(reply_threads.commit('alice', 'R0'))
        self.assertIsNone(reply_threads.resolve('R0'))

    def test_resolve_unknown_returns_none(self):
        self.assertIsNone(reply_threads.resolve('nope'))
        self.assertIsNone(reply_threads.resolve(None))

    def test_status_id_coerced_to_str(self):
        reply_threads.stage('alice', '의무실', 'sid-1')
        reply_threads.commit('alice', 12345)      # int status id
        self.assertIsNotNone(reply_threads.resolve('12345'))
        self.assertIsNotNone(reply_threads.resolve(12345))

    def test_latest_stage_wins(self):
        reply_threads.stage('alice', '의무실', 'sid-1')
        reply_threads.stage('alice', '의무실', 'sid-2')   # 덮어씀
        reply_threads.commit('alice', 'R0')
        self.assertEqual(reply_threads.resolve('R0')['session_key'], 'sid-2')

    def test_link_creates_resolvable_entry_with_participants(self):
        # link()는 stage/commit 없이 특정 status_id를 세션에 즉시 매핑(비밀 대화 침묵 턴용).
        ok = reply_threads.link('Z1', '대화', 'alice', 'sk-1',
                                participants=['alice', 'bob'])
        self.assertTrue(ok)
        entry = reply_threads.resolve('Z1')
        self.assertIsNotNone(entry)
        self.assertEqual(entry['keyword'], '대화')
        self.assertEqual(entry['session_key'], 'sk-1')
        self.assertEqual(entry['participants'], ['alice', 'bob'])

    def test_stage_commit_preserves_participants(self):
        reply_threads.stage('alice', '대화', 'sk-1', participants=['alice', 'bob'])
        reply_threads.commit('alice', 'R0')
        self.assertEqual(reply_threads.resolve('R0')['participants'], ['alice', 'bob'])

    def test_link_none_status_returns_false(self):
        self.assertFalse(reply_threads.link(None, '대화', 'alice', 'sk-1'))


# 핸들러의 라우팅 결정(_resolve_routing)은 mastodon 의존 → 임포트 가능할 때만.
try:
    from handlers.stream_handler import BotStreamHandler, IMPORTS_AVAILABLE
    _HANDLER_OK = IMPORTS_AVAILABLE
except Exception:  # noqa: BLE001
    _HANDLER_OK = False


class _FakeStatus:
    def __init__(self, in_reply_to_id=None):
        self.in_reply_to_id = in_reply_to_id


class _FakeAccount:
    def __init__(self, acct):
        self.acct = acct


class _FakeMsgStatus:
    def __init__(self, acct):
        self.account = _FakeAccount(acct)


class _FakeNotification:
    def __init__(self, acct='alice'):
        self.status = _FakeMsgStatus(acct)


@unittest.skipUnless(_HANDLER_OK, "stream_handler(mastodon) 임포트 불가 환경")
class ResolveRoutingTest(unittest.TestCase):
    def setUp(self):
        # 레지스트리가 디스크 영속 → 임시 파일로 격리(프로젝트 state/ 오염 방지)
        self._tmpdir = tempfile.mkdtemp()
        reply_threads.set_registry(reply_threads._ReplyThreadRegistry(
            store=JsonStore(os.path.join(self._tmpdir, 'reply_threads.json'))
        ))
        # __init__ 우회(무거운 의존성 없이 메서드만 사용)
        self.h = BotStreamHandler.__new__(BotStreamHandler)

    def tearDown(self):
        reply_threads.set_registry(None)
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _register(self, status_id='R0', user='alice', keyword='의무실', sid='sid-1'):
        reply_threads.stage(user, keyword, sid)
        reply_threads.commit(user, status_id)

    def test_bare_reply_in_thread_continues(self):
        self._register()
        kws, entry = self.h._resolve_routing('네 아직 아파요', _FakeStatus('R0'), 'alice')
        self.assertEqual(kws, ['의무실'])
        self.assertIsNotNone(entry)
        self.assertEqual(entry['session_key'], 'sid-1')

    def test_bare_reply_not_in_thread_ignored(self):
        kws, entry = self.h._resolve_routing('그냥 잡담', _FakeStatus('UNKNOWN'), 'alice')
        self.assertIsNone(kws)
        self.assertIsNone(entry)

    def test_bracket_command_top_level_routes_normally(self):
        kws, entry = self.h._resolve_routing('[의무실 방문] 아파요', _FakeStatus(None), 'alice')
        self.assertTrue(kws)
        self.assertIsNone(entry)

    def test_explicit_bracket_in_thread_takes_priority(self):
        self._register()
        kws, entry = self.h._resolve_routing('[대화 끝내기]', _FakeStatus('R0'), 'alice')
        self.assertEqual(kws[0].replace(' ', ''), '대화끝내기')
        self.assertIsNotNone(entry)   # 세션정보는 여전히 실림(그 타래를 종료하도록)

    def test_other_users_thread_not_continued(self):
        self._register(user='alice')
        # bob이 alice의 타래 status에 답글(대괄호 없음) → 무시(participants 없음)
        kws, entry = self.h._resolve_routing('내가 끼어들기', _FakeStatus('R0'), 'bob')
        self.assertIsNone(kws)
        self.assertIsNone(entry)

    def test_listed_participant_continues_others_thread(self):
        # 비밀 대화: participants에 든 사람은 원작성자가 아니어도 이어갈 수 있다.
        reply_threads.link('R0', '대화', 'alice', 'sk-1', participants=['alice', 'bob'])
        kws, entry = self.h._resolve_routing('이어감', _FakeStatus('R0'), 'bob')
        self.assertEqual(kws, ['대화'])
        self.assertIsNotNone(entry)
        self.assertEqual(entry['session_key'], 'sk-1')

    def test_participant_match_ignores_at_and_case(self):
        reply_threads.link('R0', '대화', 'alice', 'sk-1', participants=['alice', 'bob'])
        kws, entry = self.h._resolve_routing('이어감', _FakeStatus('R0'), '@BOB')
        self.assertEqual(kws, ['대화'])
        self.assertIsNotNone(entry)

    def test_true_outsider_still_blocked_with_participants(self):
        # participants에도 없고 원작성자도 아닌 제3자는 남의 대화 타래를 가로챌 수 없다.
        reply_threads.link('R0', '대화', 'alice', 'sk-1', participants=['alice', 'bob'])
        kws, entry = self.h._resolve_routing('끼어들기', _FakeStatus('R0'), 'carol')
        self.assertIsNone(kws)
        self.assertIsNone(entry)

    # --- 전송 후 등록: status_id 있으면 commit, 없으면 예약 폐기(Bug1) ---
    def test_register_commits_when_sent_id_present(self):
        reply_threads.stage('alice', '의무실', 'sid-1')
        self.h._register_reply_thread(_FakeNotification('alice'), {'id': 'R0'})
        self.assertIsNotNone(reply_threads.resolve('R0'))

    def test_register_discards_when_no_sent_id(self):
        reply_threads.stage('alice', '의무실', 'sid-1')
        # 전송 status를 못 얻음(빈 응답/실패) → 예약 폐기되어 다음 명령에 안 붙음
        self.h._register_reply_thread(_FakeNotification('alice'), None)
        self.assertFalse(reply_threads.commit('alice', 'R9'))
        self.assertIsNone(reply_threads.resolve('R9'))

    def test_discard_reply_thread_clears_pending(self):
        reply_threads.stage('alice', '의무실', 'sid-1')
        self.h._discard_reply_thread(_FakeNotification('alice'))
        self.assertFalse(reply_threads.commit('alice', 'R0'))


if __name__ == '__main__':
    unittest.main()

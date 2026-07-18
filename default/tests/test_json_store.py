"""utils/json_store.py + 세션 영속화 테스트.

봇을 업데이트하려고 껐다 켜도 진행 중인 대화가 끊기지 않아야 한다.
'재시작'은 **같은 파일 경로로 저장소/매니저를 새로 만드는 것**으로 시뮬레이션한다
(프로세스가 죽고 다시 뜨면 정확히 그 일이 벌어진다).
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils.json_store import JsonStore, slot_id, slot_path


class JsonStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, 'store.json')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _store(self, **kw):
        return JsonStore(self.path, **kw)

    def test_set_get_roundtrip(self):
        s = self._store()
        s.set('a', {'x': 1})
        self.assertEqual(s.get('a'), {'x': 1})

    def test_survives_restart(self):
        """핵심: 새 인스턴스가 파일에서 복원한다."""
        self._store().set('a', {'x': 1})
        self.assertEqual(self._store().get('a'), {'x': 1})

    def test_no_file_until_write(self):
        self._store()
        self.assertFalse(os.path.exists(self.path))

    def test_delete(self):
        s = self._store()
        s.set('a', 1)
        self.assertEqual(s.delete('a'), 1)
        self.assertIsNone(self._store().get('a'))

    def test_missing_returns_default(self):
        self.assertEqual(self._store().get('nope', 'dflt'), 'dflt')

    def test_mutate_read_modify_write(self):
        s = self._store()
        s.set('n', {'count': 1})
        s.mutate('n', lambda v: {'count': v['count'] + 1})
        self.assertEqual(self._store().get('n'), {'count': 2})

    def test_mutate_none_deletes(self):
        s = self._store()
        s.set('n', 1)
        s.mutate('n', lambda v: None)
        self.assertIsNone(self._store().get('n'))

    def test_max_entries_evicts_oldest(self):
        s = self._store(max_entries=2)
        s.set('a', 1); s.set('b', 2); s.set('c', 3)
        self.assertIsNone(s.get('a'))
        self.assertEqual(s.get('c'), 3)

    def test_touching_entry_makes_it_recent(self):
        s = self._store(max_entries=2)
        s.set('a', 1); s.set('b', 2)
        s.set('a', 11)      # a를 최신으로
        s.set('c', 3)       # b가 밀려나야 한다
        self.assertIsNone(s.get('b'))
        self.assertEqual(s.get('a'), 11)

    def test_corrupt_file_starts_empty_instead_of_crashing(self):
        """깨진 파일 때문에 봇이 못 뜨는 것보다 세션을 잃는 편이 낫다."""
        with open(self.path, 'w', encoding='utf-8') as f:
            f.write('{ this is not json')
        s = self._store()
        self.assertEqual(s.items(), {})
        s.set('a', 1)   # 계속 동작해야 한다
        self.assertEqual(self._store().get('a'), 1)

    def test_non_object_toplevel_starts_empty(self):
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump([1, 2, 3], f)
        self.assertEqual(self._store().items(), {})

    def test_write_is_atomic_no_tmp_left(self):
        s = self._store()
        s.set('a', 1)
        leftovers = [f for f in os.listdir(self.tmp) if f.endswith('.tmp')]
        self.assertEqual(leftovers, [], "임시 파일이 남으면 안 된다.")

    def test_korean_stored_readable(self):
        s = self._store()
        s.set('k', {'이름': '한참'})
        with open(self.path, encoding='utf-8') as f:
            self.assertIn('한참', f.read())

    def test_clear(self):
        s = self._store()
        s.set('a', 1)
        s.clear()
        self.assertEqual(self._store().items(), {})

    def test_empty_store_is_falsy_but_must_not_be_treated_as_absent(self):
        """회귀 방지: `store or Default()` 관용구 금지.

        JsonStore는 __len__이 있어 **빈 저장소가 falsy**다. `store or Default()`로 쓰면
        주입한 빈 저장소가 조용히 무시되고 기본 파일이 쓰인다(실제로 겪은 버그).
        """
        s = self._store()
        self.assertFalse(s, "빈 저장소는 falsy — 이 성질 때문에 or 관용구가 위험하다.")
        self.assertIsNotNone(s)


class SlotPathTest(unittest.TestCase):
    def test_slot_path_includes_slot_id(self):
        p = slot_path('x.json')
        self.assertIn(slot_id(), p)
        self.assertTrue(p.endswith('x.json'))

    def test_slot_id_is_filename_safe(self):
        self.assertNotIn('/', slot_id())
        self.assertNotIn('\\', slot_id())
        self.assertNotIn(' ', slot_id())



class RestartSurvivalTest(unittest.TestCase):
    """봇 업데이트로 껐다 켜도 진행 중인 대화가 이어지는가.

    '재시작' = 같은 파일 경로로 매니저를 새로 만드는 것.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _path(self, name):
        return os.path.join(self.tmp, name)

    def test_reply_thread_routing_survives_restart(self):
        """bare 답글 라우팅이 재시작 후에도 살아 있어야 한다.

        이게 없으면 러너가 진료 답글에 이어 써도 봇이 어느 타래인지 몰라 무시한다.
        """
        from utils import reply_threads

        reg = reply_threads._ReplyThreadRegistry(
            store=JsonStore(self._path('rt.json')))
        reg.stage('alice', '의무실', 'sid-1')
        self.assertTrue(reg.commit('alice', 'R0'))

        # --- 봇 재시작 ---
        reborn = reply_threads._ReplyThreadRegistry(
            store=JsonStore(self._path('rt.json')))
        entry = reborn.resolve('R0')
        self.assertIsNotNone(entry, "재시작 후 답글 타래가 유실되면 대화가 끊긴다.")
        self.assertEqual(entry['session_key'], 'sid-1')
        self.assertEqual(entry['keyword'], '의무실')

    def test_doctor_session_survives_restart(self):
        from commands.shinarmont import infirmary_command as inf

        mgr = inf._DoctorSessionManager(store=JsonStore(self._path('ds.json')))
        session = mgr.start('alice', day=3, patient={'이름': '한참'})
        sid = session['id']
        mgr.append_turn(sid, '머리가 아픕니다', '(의사가 차트를 넘긴다) 언제부터죠?')

        # --- 봇 재시작 ---
        reborn = inf._DoctorSessionManager(store=JsonStore(self._path('ds.json')))
        restored = reborn.get(sid)
        self.assertIsNotNone(restored, "재시작 후 진료 세션이 유실되면 대화가 끊긴다.")
        self.assertEqual(restored['turns'], 1)
        self.assertEqual(len(restored['history']), 2)
        self.assertEqual(restored['patient']['이름'], '한참')
        self.assertTrue(restored['active'])

    def test_doctor_finalized_flag_survives_restart(self):
        """재시작으로 처치가 두 번 적용되면 안 된다."""
        from commands.shinarmont import infirmary_command as inf

        mgr = inf._DoctorSessionManager(store=JsonStore(self._path('ds.json')))
        sid = mgr.start('alice', day=3, patient={})['id']
        self.assertTrue(mgr.begin_finalize(sid))

        reborn = inf._DoctorSessionManager(store=JsonStore(self._path('ds.json')))
        self.assertFalse(reborn.begin_finalize(sid),
                         "이미 처치된 세션이 재시작 후 다시 처치되면 안 된다.")

    def test_doctor_external_edit_needs_touch(self):
        """호출측 직접 수정은 touch()로 저장된다."""
        from commands.shinarmont import infirmary_command as inf

        mgr = inf._DoctorSessionManager(store=JsonStore(self._path('ds.json')))
        session = mgr.start('alice', day=3, patient={})
        session['end_at'] = 7
        mgr.touch(session['id'])

        reborn = inf._DoctorSessionManager(store=JsonStore(self._path('ds.json')))
        self.assertEqual(reborn.get(session['id'])['end_at'], 7)

    def _talk_session(self, total=0):
        from commands.shinarmont.talk_command import TalkSession
        return TalkSession(session_key='R1', initiator_name='가나', initiator_acct='alice',
                           partner_name='다람', partner_acct='bob', total=total)

    def test_talk_session_survives_restart(self):
        from commands.shinarmont.talk_command import TalkSessionManager

        mgr = TalkSessionManager(store=JsonStore(self._path('ts.json')))
        mgr.start('R1', self._talk_session(total=2))

        # --- 봇 재시작 ---
        reborn = TalkSessionManager(store=JsonStore(self._path('ts.json')))
        restored = reborn.get('R1')
        self.assertIsNotNone(restored, "재시작 후 비밀 대화 세션이 유실되면 대화가 끊긴다.")
        self.assertEqual(restored.total, 2, "멘션 캡 카운트가 초기화되면 캡을 우회할 수 있다.")
        self.assertEqual(restored.partner_name, '다람')
        self.assertEqual(restored.initiator_acct, 'alice')

    def test_talk_touch_persists_external_mutation(self):
        from commands.shinarmont.talk_command import TalkSessionManager

        mgr = TalkSessionManager(store=JsonStore(self._path('ts.json')))
        mgr.start('R1', self._talk_session(total=0))
        session = mgr.get('R1')
        session.total += 1
        mgr.touch('R1')

        reborn = TalkSessionManager(store=JsonStore(self._path('ts.json')))
        self.assertEqual(reborn.get('R1').total, 1)

    def test_talk_clear_removes_from_disk(self):
        from commands.shinarmont.talk_command import TalkSessionManager

        mgr = TalkSessionManager(store=JsonStore(self._path('ts.json')))
        mgr.start('R1', self._talk_session())
        mgr.clear('R1')
        reborn = TalkSessionManager(store=JsonStore(self._path('ts.json')))
        self.assertIsNone(reborn.get('R1'), "종료된 세션이 재시작 후 되살아나면 안 된다.")

    def test_injected_empty_store_is_used(self):
        """회귀 방지: 빈 저장소를 주입해도 기본 파일로 새면 안 된다."""
        from commands.shinarmont.talk_command import TalkSessionManager

        store = JsonStore(self._path('ts.json'))
        mgr = TalkSessionManager(store=store)
        self.assertIs(mgr._store, store)


if __name__ == '__main__':
    unittest.main()

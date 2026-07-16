"""utils.game_state 단위 테스트: 날짜 스탬프 자동 만료·파생 어제대화상대·재로드 원자성.

핵심 계약(2026-07-16 개편):
  - `오늘*` 키는 **기록한 날짜**와 함께 저장되고, 날짜가 오늘이 아니면 없는 것으로 본다.
    → 스케줄러가 리셋해 주지 않아도 자정이 지나면 저절로 풀린다.
  - `어제대화상대`는 저장하지 않고 `오늘대화상대`의 날짜에서 파생한다.
"""

import os
import sys
import json
import tempfile
import shutil
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import game_state as gs_module  # noqa: E402
from utils.game_state import GameStateManager  # noqa: E402


def _date(offset_days=0):
    return (datetime.now(gs_module.KST) + timedelta(days=offset_days)).strftime('%Y-%m-%d')


class TestGameState(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='gs_test_')
        # 존재하지 않는 하위 디렉터리를 포함해 자동 생성 검증
        self.path = os.path.join(self.tmpdir, 'state', 'game_state.json')
        self.gs = GameStateManager(path=self.path)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _travel(self, days):
        """날짜를 옮긴다(자정을 넘긴 상황 시뮬레이션)."""
        return patch.object(gs_module, '_today', return_value=_date(days))

    # -- 기본 get/set -------------------------------------------------
    def test_get_default(self):
        self.assertIsNone(self.gs.get('u1', '오늘출석'))
        self.assertEqual(self.gs.get('u1', '오늘출석', 0), 0)

    def test_set_and_get_plain_key(self):
        self.gs.set('u1', '누적방문', 3)
        self.assertEqual(self.gs.get('u1', '누적방문'), 3)
        self.assertTrue(os.path.exists(self.path))

    def test_plain_key_is_not_date_stamped(self):
        """'오늘'로 시작하지 않는 키는 날짜와 무관하게 유지된다(누적값 등)."""
        self.gs.set('u1', '의무실방문횟수', 5)
        with self._travel(9):
            self.assertEqual(self.gs.get('u1', '의무실방문횟수'), 5)

    # -- check_and_set 경계 ------------------------------------------
    def test_check_and_set_boundary(self):
        self.assertTrue(self.gs.check_and_set('u1', '오늘출석', 1))
        self.assertEqual(self.gs.get('u1', '오늘출석'), 1)
        self.assertFalse(self.gs.check_and_set('u1', '오늘출석', 1))
        self.assertEqual(self.gs.get('u1', '오늘출석'), 1)  # 변경 없음

    def test_check_and_set_limit_two(self):
        self.assertTrue(self.gs.check_and_set('u1', '오늘조사', 2))
        self.assertTrue(self.gs.check_and_set('u1', '오늘조사', 2))
        self.assertFalse(self.gs.check_and_set('u1', '오늘조사', 2))
        self.assertEqual(self.gs.get('u1', '오늘조사'), 2)

    def test_check_and_set_non_int_current(self):
        self.gs.set('u1', '오늘출석', 'garbage')
        self.assertTrue(self.gs.check_and_set('u1', '오늘출석', 1))
        self.assertEqual(self.gs.get('u1', '오늘출석'), 1)

    # -- 날짜 스탬프 자동 만료 (스케줄러 없이) --------------------------
    def test_daily_key_expires_next_day_without_scheduler(self):
        """PoC로 재현했던 결함: 스케줄러 리셋이 다른 슬롯에 닿지 않아
        [대화]가 이벤트 내내 1회만 가능했다. 이제 날짜가 바뀌면 저절로 풀린다."""
        self.assertTrue(self.gs.check_and_set('u1', '오늘대화여부', 1))
        self.assertFalse(self.gs.check_and_set('u1', '오늘대화여부', 1))

        with self._travel(1):   # 자정을 넘김 — 아무도 리셋해 주지 않았다
            self.assertEqual(self.gs.get('u1', '오늘대화여부', 0), 0)
            self.assertTrue(self.gs.check_and_set('u1', '오늘대화여부', 1),
                            "날짜가 바뀌면 스케줄러 없이도 다시 쓸 수 있어야 한다.")

    def test_daily_key_survives_within_same_day(self):
        self.gs.check_and_set('u1', '오늘고발', 1)
        with self._travel(0):
            self.assertEqual(self.gs.get('u1', '오늘고발'), 1)

    def test_expiry_survives_restart(self):
        """재시작해도 날짜 판정이라 결과가 같다."""
        self.gs.check_and_set('u1', '오늘의무실', 1)
        with self._travel(1):
            reborn = GameStateManager(path=self.path)
            self.assertEqual(reborn.get('u1', '오늘의무실', 0), 0)

    def test_legacy_raw_value_treated_as_expired(self):
        """구버전 파일(스탬프 없는 raw 값)은 날짜를 알 수 없어 만료로 본다.

        한 번 더 풀리는 쪽이 영원히 잠기는 쪽보다 안전하다.
        """
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump({'u1': {'오늘대화여부': 1}}, f)
        gs = GameStateManager(path=self.path)
        self.assertEqual(gs.get('u1', '오늘대화여부', 0), 0)
        self.assertTrue(gs.check_and_set('u1', '오늘대화여부', 1))

    # -- 어제대화상대 파생 --------------------------------------------
    def test_yesterday_partner_derived_from_today_partner(self):
        self.gs.set('u1', '오늘대화상대', '다람')
        with self._travel(1):
            self.assertEqual(self.gs.get('u1', '어제대화상대'), '다람')
            self.assertEqual(self.gs.get('u1', '오늘대화상대', ''), '',
                             "오늘대화상대는 날짜가 지나면 비어야 한다.")

    def test_yesterday_partner_empty_on_same_day(self):
        self.gs.set('u1', '오늘대화상대', '다람')
        self.assertEqual(self.gs.get('u1', '어제대화상대'), '',
                         "오늘 기록은 '어제'가 아니다.")

    def test_yesterday_partner_empty_after_two_days(self):
        self.gs.set('u1', '오늘대화상대', '다람')
        with self._travel(2):
            self.assertEqual(self.gs.get('u1', '어제대화상대'), '')

    def test_yesterday_partner_default_when_never_talked(self):
        self.assertEqual(self.gs.get('u1', '어제대화상대'), '')

    def test_setting_yesterday_partner_is_ignored(self):
        """파생값이라 직접 설정은 무시한다(써도 다음 읽기에서 덮이므로 혼란만 준다)."""
        self.gs.set('u1', '어제대화상대', 'u9')
        self.assertEqual(self.gs.get('u1', '어제대화상대'), '')

    # -- 폐기된 잡 ----------------------------------------------------
    def test_reset_daily_is_noop(self):
        """스케줄러가 아직 부르지만 아무것도 하지 않아야 한다(날짜 스탬프가 대신)."""
        self.gs.check_and_set('u1', '오늘고발', 1)
        self.gs.reset_daily()
        self.assertEqual(self.gs.get('u1', '오늘고발'), 1,
                         "오늘 기록은 리셋 호출로 지워지면 안 된다.")

    def test_carry_talk_partner_is_noop(self):
        self.gs.set('u1', '오늘대화상대', '다람')
        self.gs.carry_talk_partner()
        self.assertEqual(self.gs.get('u1', '오늘대화상대'), '다람')

    # -- purge_stale (용량 정리) --------------------------------------
    def test_purge_removes_old_daily_keys(self):
        self.gs.check_and_set('u1', '오늘고발', 1)
        with self._travel(5):
            self.assertEqual(self.gs.purge_stale(), 1)
            self.assertEqual(self.gs.snapshot().get('u1', {}), {})

    def test_purge_keeps_yesterday_partner_source(self):
        """어제 대화상대는 어제대화상대로 쓰이므로 이틀 치를 남긴다."""
        self.gs.set('u1', '오늘대화상대', '다람')
        with self._travel(1):
            self.gs.purge_stale()
            self.assertEqual(self.gs.get('u1', '어제대화상대'), '다람')

    def test_purge_keeps_plain_keys(self):
        self.gs.set('u1', '의무실방문횟수', 3)
        with self._travel(9):
            self.gs.purge_stale()
            self.assertEqual(self.gs.get('u1', '의무실방문횟수'), 3)

    # -- 재로드(디스크 영속) -----------------------------------------
    def test_reload_persistence(self):
        self.gs.check_and_set('u1', '오늘출석', 1)
        self.gs.set('u1', '누적', 'u3')
        gs2 = GameStateManager(path=self.path)
        self.assertEqual(gs2.get('u1', '오늘출석'), 1)
        self.assertEqual(gs2.get('u1', '누적'), 'u3')

    def test_atomic_write_no_temp_leftover(self):
        self.gs.set('u1', 'k', 'v')
        state_dir = os.path.dirname(self.path)
        leftovers = [f for f in os.listdir(state_dir) if f.endswith('.tmp')]
        self.assertEqual(leftovers, [])

    def test_missing_file_starts_empty(self):
        missing_path = os.path.join(self.tmpdir, 'nope', 'x.json')
        gs = GameStateManager(path=missing_path)
        self.assertEqual(gs.snapshot(), {})

    def test_corrupt_file_starts_empty(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, 'w', encoding='utf-8') as f:
            f.write('{ not valid json ')
        gs = GameStateManager(path=self.path)
        self.assertEqual(gs.snapshot(), {})

    def test_written_json_has_date_stamp(self):
        self.gs.set('u1', '오늘출석', 1)
        with open(self.path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        record = data['u1']['오늘출석']
        self.assertEqual(record['v'], 1)
        self.assertEqual(record['d'], _date(0))


class SlotIsolationTest(unittest.TestCase):
    """슬롯별 파일 분리 — 프로세스 간 덮어쓰기 방지.

    한 파일을 공유하면 @DOCTOR의 저장이 @STORY가 쓴 키를 지웠고,
    @STORY 재시작 시 [대화] 제한이 풀렸다(PoC로 재현한 실제 결함).
    """

    def test_default_path_is_slot_scoped(self):
        from utils.json_store import slot_id
        path = gs_module._default_state_path()
        self.assertIn(slot_id(), path)
        self.assertTrue(path.endswith('game_state.json'))


class LegacyMigrationTest(unittest.TestCase):
    """슬롯 분리 이전의 공유 파일에서 누적값을 이어받는다.

    '오늘*'은 어차피 만료되니 상관없지만, '의무실방문횟수'는 누적이라
    잃으면 러셀이 다시 초진처럼 군다.
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='gs_legacy_')
        self.shared = os.path.join(self.tmpdir, 'game_state.json')
        self.slot = os.path.join(self.tmpdir, 'STORY', 'game_state.json')
        self._patches = [
            patch.object(gs_module, '_DEFAULT_STATE_DIR', self.tmpdir),
            patch.object(gs_module, '_default_state_path', return_value=self.slot),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_shared(self, data):
        with open(self.shared, 'w', encoding='utf-8') as f:
            json.dump(data, f)

    def test_seeds_from_legacy_shared_file(self):
        self._write_shared({'u1': {'의무실방문횟수': 4}})
        gs = GameStateManager()
        self.assertEqual(gs.get('u1', '의무실방문횟수'), 4)

    def test_first_save_goes_to_slot_file_not_shared(self):
        self._write_shared({'u1': {'의무실방문횟수': 4}})
        gs = GameStateManager()
        gs.set('u1', '의무실방문횟수', 5)
        self.assertTrue(os.path.exists(self.slot))
        with open(self.shared, encoding='utf-8') as f:
            self.assertEqual(json.load(f)['u1']['의무실방문횟수'], 4,
                             "구 파일은 건드리지 않는다(다른 슬롯도 이어받아야 한다)")

    def test_slot_file_wins_when_present(self):
        self._write_shared({'u1': {'의무실방문횟수': 4}})
        os.makedirs(os.path.dirname(self.slot), exist_ok=True)
        with open(self.slot, 'w', encoding='utf-8') as f:
            json.dump({'u1': {'의무실방문횟수': 99}}, f)
        self.assertEqual(GameStateManager().get('u1', '의무실방문횟수'), 99)

    def test_injected_path_never_reads_legacy(self):
        """테스트가 임시 경로를 주입하면 구 파일이 새어 들면 안 된다."""
        self._write_shared({'u1': {'의무실방문횟수': 4}})
        other = os.path.join(self.tmpdir, 'injected.json')
        self.assertEqual(GameStateManager(path=other).snapshot(), {})


if __name__ == '__main__':
    unittest.main()

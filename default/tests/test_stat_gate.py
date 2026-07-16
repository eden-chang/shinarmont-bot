"""utils/stat_gate.py 단위 테스트 (페이크 매니저, AI/시트 주입)."""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils import stat_gate


def _mgmt_manager(rows):
    m = MagicMock()
    m.get_worksheet_data.return_value = rows
    m.batch_update_cells.return_value = True
    return m


def _sanity_manager(rows):
    m = MagicMock()
    m.get_worksheet_data.return_value = rows
    m.batch_update_cells.return_value = True
    return m


class IsHospitalizedTest(unittest.TestCase):
    """임계치를 **테스트가 직접 고정한다**.

    안 그러면 GM이 `.env`의 HEALTH_HOSPITALIZE_THRESHOLD를 조정하는 순간 이 테스트가
    빨개진다(2026-07-16에 20→10으로 바뀌며 실제로 겪었다). 그러면 진짜 고장과
    설정 변경을 구분할 수 없다. 검증 대상은 '임계치 이하면 입원'이라는 규칙이지
    임계치 값 자체가 아니다.
    """

    def setUp(self):
        from config.settings import config
        self._saved = config.HEALTH_HOSPITALIZE_THRESHOLD
        config.HEALTH_HOSPITALIZE_THRESHOLD = 20
        self.addCleanup(setattr, config, 'HEALTH_HOSPITALIZE_THRESHOLD', self._saved)

    def test_below_threshold_is_hospitalized(self):
        self.assertTrue(stat_gate.is_hospitalized({'건강': '20'}))   # 경계(이하)
        self.assertTrue(stat_gate.is_hospitalized({'건강': '5'}))

    def test_above_threshold_not_hospitalized(self):
        self.assertFalse(stat_gate.is_hospitalized({'건강': '21'}))
        self.assertFalse(stat_gate.is_hospitalized({'건강': '100'}))

    def test_threshold_is_honored(self):
        """설정을 바꾸면 판정도 따라와야 한다."""
        from config.settings import config
        config.HEALTH_HOSPITALIZE_THRESHOLD = 10
        self.assertFalse(stat_gate.is_hospitalized({'건강': '20'}))
        self.assertTrue(stat_gate.is_hospitalized({'건강': '10'}))

    def test_missing_health_defaults_healthy(self):
        self.assertFalse(stat_gate.is_hospitalized({'이름': 'A'}))

    def test_numeric_arg(self):
        self.assertTrue(stat_gate.is_hospitalized(10))
        self.assertFalse(stat_gate.is_hospitalized(50))

    def test_unparseable_returns_false(self):
        self.assertFalse(stat_gate.is_hospitalized({'건강': ''}))
        self.assertFalse(stat_gate.is_hospitalized('alice'))


class ApplySanityMessagesTest(unittest.TestCase):
    """이성문구 임계치를 **테스트가 고정한다**.

    GM이 `.env`의 SANITY_MSG1_THRESHOLD를 조정하면(2026-07-16: 60→50) 이성 55가
    문구1 구간에서 빠져 빨개진다. 검증 대상은 '구간에 들어오면 문구1만 보낸다'는
    규칙이지 임계치 값이 아니다.
    """

    def setUp(self):
        from config.settings import config
        self._saved = (config.SANITY_MSG1_THRESHOLD, config.SANITY_MSG2_THRESHOLD)
        config.SANITY_MSG1_THRESHOLD = 60
        config.SANITY_MSG2_THRESHOLD = 30
        self.addCleanup(
            lambda: (setattr(config, 'SANITY_MSG1_THRESHOLD', self._saved[0]),
                     setattr(config, 'SANITY_MSG2_THRESHOLD', self._saved[1])))

    def _run(self, mgmt_rows, sanity_rows):
        mgmt = _mgmt_manager(mgmt_rows)
        system = _sanity_manager(sanity_rows)
        with patch.object(stat_gate, 'queue_dm') as qdm:
            stat_gate.apply_sanity_messages(mgmt, system, None, 'alice')
        return mgmt, system, qdm

    def test_sends_msg1_only_when_between_thresholds(self):
        mgmt_rows = [{'이름': 'A', '아이디': 'alice', '건강': '80', '이성': '55', '_row_number': 3}]
        sanity_rows = [{
            '이름': 'A', '이성문구1': '문구하나', '문구1 발송여부': '',
            '이성문구2': '문구둘', '문구2 발송여부': '', '_row_number': 3,
        }]
        mgmt, system, qdm = self._run(mgmt_rows, sanity_rows)
        qdm.assert_called_once_with('alice', '문구하나')
        updates = system.batch_update_cells.call_args[0][1]
        # 문구1 발송여부 = 3번째 컬럼(1-indexed), 행 3, 값 O
        self.assertEqual(updates, [(3, 3, 'O')])

    def test_sends_both_when_below_msg2(self):
        mgmt_rows = [{'이름': 'A', '아이디': 'alice', '건강': '80', '이성': '10', '_row_number': 3}]
        sanity_rows = [{
            '이름': 'A', '이성문구1': '문구하나', '문구1 발송여부': '',
            '이성문구2': '문구둘', '문구2 발송여부': '', '_row_number': 3,
        }]
        mgmt, system, qdm = self._run(mgmt_rows, sanity_rows)
        self.assertEqual(qdm.call_count, 2)
        updates = system.batch_update_cells.call_args[0][1]
        self.assertIn((3, 3, 'O'), updates)   # 문구1 발송여부
        self.assertIn((3, 5, 'O'), updates)   # 문구2 발송여부

    def test_no_resend_when_flag_set(self):
        mgmt_rows = [{'이름': 'A', '아이디': 'alice', '건강': '80', '이성': '10', '_row_number': 3}]
        sanity_rows = [{
            '이름': 'A', '이성문구1': '문구하나', '문구1 발송여부': 'O',
            '이성문구2': '문구둘', '문구2 발송여부': '', '_row_number': 3,
        }]
        mgmt, system, qdm = self._run(mgmt_rows, sanity_rows)
        qdm.assert_called_once_with('alice', '문구둘')
        updates = system.batch_update_cells.call_args[0][1]
        self.assertEqual(updates, [(3, 5, 'O')])

    def test_flag_write_failure_defers_send(self):
        # 발송여부 기록(batch_update)이 실패하면 DM을 보내지 않는다(다음 검사에서 1회만 발송)
        mgmt_rows = [{'이름': 'A', '아이디': 'alice', '건강': '80', '이성': '10', '_row_number': 3}]
        sanity_rows = [{
            '이름': 'A', '이성문구1': '문구하나', '문구1 발송여부': '',
            '이성문구2': '문구둘', '문구2 발송여부': '', '_row_number': 3,
        }]
        mgmt = _mgmt_manager(mgmt_rows)
        system = _sanity_manager(sanity_rows)
        system.batch_update_cells.return_value = False   # 플래그 기록 실패
        with patch.object(stat_gate, 'queue_dm') as qdm:
            stat_gate.apply_sanity_messages(mgmt, system, None, 'alice')
        qdm.assert_not_called()

    def test_no_send_above_all_thresholds(self):
        mgmt_rows = [{'이름': 'A', '아이디': 'alice', '건강': '80', '이성': '90', '_row_number': 3}]
        sanity_rows = [{
            '이름': 'A', '이성문구1': '문구하나', '문구1 발송여부': '',
            '이성문구2': '문구둘', '문구2 발송여부': '', '_row_number': 3,
        }]
        mgmt, system, qdm = self._run(mgmt_rows, sanity_rows)
        qdm.assert_not_called()
        system.batch_update_cells.assert_not_called()

    def test_empty_phrase_not_sent(self):
        mgmt_rows = [{'이름': 'A', '아이디': 'alice', '건강': '80', '이성': '55', '_row_number': 3}]
        sanity_rows = [{
            '이름': 'A', '이성문구1': '', '문구1 발송여부': '',
            '이성문구2': '', '문구2 발송여부': '', '_row_number': 3,
        }]
        mgmt, system, qdm = self._run(mgmt_rows, sanity_rows)
        qdm.assert_not_called()
        system.batch_update_cells.assert_not_called()

    def test_missing_character_noop(self):
        mgmt_rows = [{'이름': 'A', '아이디': 'alice', '건강': '80', '이성': '55', '_row_number': 3}]
        sanity_rows = []  # 이성 시트에 A 없음
        mgmt, system, qdm = self._run(mgmt_rows, sanity_rows)
        qdm.assert_not_called()
        system.batch_update_cells.assert_not_called()

    def test_none_managers_noop(self):
        with patch.object(stat_gate, 'queue_dm') as qdm:
            stat_gate.apply_sanity_messages(None, MagicMock(), None, 'alice')
            stat_gate.apply_sanity_messages(MagicMock(), None, None, 'alice')
        qdm.assert_not_called()


class DailyDecayAllTest(unittest.TestCase):
    def test_decays_health_and_sanity_with_floor(self):
        mgmt_rows = [
            {'이름': 'A', '아이디': 'alice', '건강': '100', '이성': '3', '_row_number': 3},
            {'이름': 'B', '아이디': 'bob', '건강': '2', '이성': '50', '_row_number': 4},
        ]
        mgmt = _mgmt_manager(mgmt_rows)
        system = _sanity_manager([])
        with patch.object(stat_gate, 'apply_sanity_messages') as apply_mock, \
             patch.object(stat_gate, 'invalidate_user_cache') as inval:
            res = stat_gate.daily_decay_all(mgmt, system, None)

        # 사용자별 락 안에서 각자 반영하므로 여러 배치 호출을 합산해 확인
        updates = [u for call in mgmt.batch_update_cells.call_args_list for u in call[0][1]]
        # DAILY_HEALTH_DECAY=5, DAILY_SANITY_DECAY=5
        # alice: 건강 100->95 (col3), 이성 3->0 (col4)
        self.assertIn((3, 3, 95), updates)
        self.assertIn((3, 4, 0), updates)
        # bob: 건강 2->0 (col3), 이성 50->45 (col4)
        self.assertIn((4, 3, 0), updates)
        self.assertIn((4, 4, 45), updates)
        self.assertEqual(res['updated'], 2)
        self.assertEqual(apply_mock.call_count, 2)
        inval.assert_called_once()

    def test_skips_unparseable_values(self):
        mgmt_rows = [{'이름': 'A', '아이디': 'alice', '건강': '', '이성': 'x', '_row_number': 3}]
        mgmt = _mgmt_manager(mgmt_rows)
        with patch.object(stat_gate, 'apply_sanity_messages'), \
             patch.object(stat_gate, 'invalidate_user_cache'):
            res = stat_gate.daily_decay_all(mgmt, _sanity_manager([]), None)
        mgmt.batch_update_cells.assert_not_called()
        self.assertEqual(res['updated'], 0)

    def test_empty_mgmt_noop(self):
        mgmt = _mgmt_manager([])
        res = stat_gate.daily_decay_all(mgmt, _sanity_manager([]), None)
        self.assertEqual(res['updated'], 0)

    def test_none_manager(self):
        res = stat_gate.daily_decay_all(None, None, None)
        self.assertEqual(res['updated'], 0)

    def test_missing_stat_columns(self):
        mgmt_rows = [{'이름': 'A', '아이디': 'alice', '_row_number': 3}]
        mgmt = _mgmt_manager(mgmt_rows)
        with patch.object(stat_gate, 'apply_sanity_messages'), \
             patch.object(stat_gate, 'invalidate_user_cache'):
            res = stat_gate.daily_decay_all(mgmt, _sanity_manager([]), None)
        mgmt.batch_update_cells.assert_not_called()
        self.assertEqual(res['updated'], 0)


if __name__ == '__main__':
    unittest.main()

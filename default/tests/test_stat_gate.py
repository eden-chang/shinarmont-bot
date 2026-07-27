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


class _FakeState:
    """game_state 대체: (uid, key)별 최초 1회만 True를 돌려주는 인메모리 플래그."""

    def __init__(self):
        self.sent = set()

    def check_and_set(self, uid, key, limit):
        k = (uid, key)
        if k in self.sent:
            return False
        self.sent.add(k)
        return True


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
    """이성/건강 저하 경고 문구는 **고정 문구**를 임계값별 최초 1회 발송한다.

    임계치를 테스트가 고정한다(50/20). 검증 대상은 '구간에 들어오면 해당 문구를
    1회만 보낸다'는 규칙이지 임계치 값 자체가 아니다. 재발송 방지는 시트가 아니라
    game_state 플래그로 하므로, 테스트는 인메모리 페이크 상태를 주입한다.
    """

    def setUp(self):
        from config.settings import config
        self._saved = (
            config.SANITY_MSG1_THRESHOLD, config.SANITY_MSG2_THRESHOLD,
            config.HEALTH_MSG1_THRESHOLD, config.HEALTH_MSG2_THRESHOLD,
        )
        config.SANITY_MSG1_THRESHOLD = 50
        config.SANITY_MSG2_THRESHOLD = 20
        config.HEALTH_MSG1_THRESHOLD = 50
        config.HEALTH_MSG2_THRESHOLD = 20

        def _restore():
            (config.SANITY_MSG1_THRESHOLD, config.SANITY_MSG2_THRESHOLD,
             config.HEALTH_MSG1_THRESHOLD, config.HEALTH_MSG2_THRESHOLD) = self._saved
        self.addCleanup(_restore)

        # game_state를 인메모리 페이크로 교체(디스크/싱글톤 오염 방지)
        self._fake_state = _FakeState()
        p = patch.object(stat_gate, 'game_state', self._fake_state)
        p.start()
        self.addCleanup(p.stop)

    def _run(self, health, sanity):
        mgmt_rows = [{'이름': 'A', '아이디': 'alice',
                      '건강': str(health), '이성': str(sanity), '_row_number': 3}]
        mgmt = _mgmt_manager(mgmt_rows)
        with patch.object(stat_gate, 'queue_dm') as qdm:
            stat_gate.apply_sanity_messages(mgmt, None, None, 'alice')
        return mgmt, qdm

    def _sent_messages(self, qdm):
        return [call.args[1] for call in qdm.call_args_list]

    def test_sends_sanity_msg1_only_between_thresholds(self):
        _, qdm = self._run(health=80, sanity=45)  # 20 < 45 <= 50
        qdm.assert_called_once_with('alice', stat_gate.SANITY_MSG1)

    def test_sends_both_sanity_when_below_msg2(self):
        _, qdm = self._run(health=80, sanity=10)  # <= 20 → 문구1+문구2
        self.assertEqual(self._sent_messages(qdm),
                         [stat_gate.SANITY_MSG1, stat_gate.SANITY_MSG2])

    def test_sends_health_msg1_only_between_thresholds(self):
        _, qdm = self._run(health=45, sanity=90)  # 20 < 45 <= 50 → 건강 문구1만
        qdm.assert_called_once_with('alice', stat_gate.HEALTH_MSG1)

    def test_sends_both_health_when_below_msg2(self):
        _, qdm = self._run(health=10, sanity=90)  # <= 20 → 건강 문구1+문구2
        self.assertEqual(self._sent_messages(qdm),
                         [stat_gate.HEALTH_MSG1, stat_gate.HEALTH_MSG2])

    def test_sends_both_stats_together(self):
        _, qdm = self._run(health=15, sanity=15)  # 이성2단·건강2단 모두
        self.assertEqual(
            set(self._sent_messages(qdm)),
            {stat_gate.SANITY_MSG1, stat_gate.SANITY_MSG2,
             stat_gate.HEALTH_MSG1, stat_gate.HEALTH_MSG2},
        )

    def test_no_resend_when_flag_set(self):
        # 최초 호출로 발송된 뒤, 같은 상태로 다시 호출하면 재발송하지 않는다.
        self._run(health=80, sanity=10)
        _, qdm = self._run(health=80, sanity=10)
        qdm.assert_not_called()

    def test_no_send_above_all_thresholds(self):
        _, qdm = self._run(health=90, sanity=90)
        qdm.assert_not_called()

    def test_missing_character_noop(self):
        mgmt = _mgmt_manager([])  # 관리 시트에 alice 없음
        with patch.object(stat_gate, 'queue_dm') as qdm:
            stat_gate.apply_sanity_messages(mgmt, None, None, 'alice')
        qdm.assert_not_called()

    def test_none_sheets_manager_noop(self):
        with patch.object(stat_gate, 'queue_dm') as qdm:
            stat_gate.apply_sanity_messages(None, MagicMock(), None, 'alice')
        qdm.assert_not_called()


class ReconcileStatMessagesTest(unittest.TestCase):
    """봇 시작 시 전 캐릭터 재전송 스윕: 미발송분만, 현재값 기준, 중복 없음."""

    def setUp(self):
        from config.settings import config
        self._saved = (
            config.SANITY_MSG1_THRESHOLD, config.SANITY_MSG2_THRESHOLD,
            config.HEALTH_MSG1_THRESHOLD, config.HEALTH_MSG2_THRESHOLD,
        )
        config.SANITY_MSG1_THRESHOLD = 50
        config.SANITY_MSG2_THRESHOLD = 20
        config.HEALTH_MSG1_THRESHOLD = 50
        config.HEALTH_MSG2_THRESHOLD = 20

        def _restore():
            (config.SANITY_MSG1_THRESHOLD, config.SANITY_MSG2_THRESHOLD,
             config.HEALTH_MSG1_THRESHOLD, config.HEALTH_MSG2_THRESHOLD) = self._saved
        self.addCleanup(_restore)

        self._fake_state = _FakeState()
        p = patch.object(stat_gate, 'game_state', self._fake_state)
        p.start()
        self.addCleanup(p.stop)

    def test_sweep_sends_only_below_threshold_and_is_idempotent(self):
        rows = [
            {'이름': 'A', '아이디': 'alice', '건강': '80', '이성': '38', '_row_number': 2},  # 이성문구1
            {'이름': 'B', '아이디': 'bob', '건강': '90', '이성': '90', '_row_number': 3},    # 없음
            {'이름': 'C', '아이디': 'carol', '건강': '15', '이성': '90', '_row_number': 4},  # 건강1+건강2
        ]
        mgmt = _mgmt_manager(rows)

        with patch.object(stat_gate, 'queue_dm') as qdm:
            sent = stat_gate.reconcile_stat_messages(mgmt, None, None)
        self.assertEqual(sent, 3)  # alice 1 + carol 2
        recipients = [c.args[0] for c in qdm.call_args_list]
        self.assertEqual(recipients.count('alice'), 1)
        self.assertEqual(recipients.count('carol'), 2)
        self.assertNotIn('bob', recipients)

        # 두 번째 스윕(재시작 모사): 플래그가 남아 있어 아무것도 재발송하지 않는다.
        with patch.object(stat_gate, 'queue_dm') as qdm2:
            sent2 = stat_gate.reconcile_stat_messages(mgmt, None, None)
        self.assertEqual(sent2, 0)
        qdm2.assert_not_called()

    def test_sweep_skips_rows_without_id(self):
        rows = [{'이름': 'A', '아이디': '', '건강': '10', '이성': '10', '_row_number': 2}]
        mgmt = _mgmt_manager(rows)
        with patch.object(stat_gate, 'queue_dm') as qdm:
            sent = stat_gate.reconcile_stat_messages(mgmt, None, None)
        self.assertEqual(sent, 0)
        qdm.assert_not_called()

    def test_sweep_none_manager_noop(self):
        with patch.object(stat_gate, 'queue_dm') as qdm:
            self.assertEqual(stat_gate.reconcile_stat_messages(None), 0)
        qdm.assert_not_called()


if __name__ == '__main__':
    unittest.main()

"""config/bot_config.py 슬롯 자동 탐지 테스트."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from config.bot_config import discover_bot_slots, load_bot_configs_from_env


class DiscoverBotSlotsTest(unittest.TestCase):
    def test_empty_dict(self):
        self.assertEqual(discover_bot_slots({}), [])

    def test_contiguous_slots(self):
        env = {
            'BOT1_ENABLED': 'True',
            'BOT2_ENABLED': 'True',
            'BOT3_ENABLED': 'False',
            'OTHER': 'x',
        }
        self.assertEqual(discover_bot_slots(env), [1, 2, 3])

    def test_non_contiguous_slots(self):
        """슬롯 번호가 건너뛰어도 모두 탐지됨."""
        env = {
            'BOT2_ENABLED': 'True',
            'BOT5_NAME': 'inv',
            'BOT5_ACCESS_TOKEN': 'xyz',
        }
        self.assertEqual(discover_bot_slots(env), [2, 5])

    def test_ignores_non_bot_keys(self):
        env = {
            'BOTANY_ENABLED': 'True',  # BOT뒤에 숫자 아님 → 무시
            'BOT_ENABLED': 'True',      # 숫자 없음 → 무시
            'BOT1A_ENABLED': 'True',    # 숫자+문자 혼합 → 무시
            'BOT10_ENABLED': 'True',    # 정상
        }
        self.assertEqual(discover_bot_slots(env), [10])

    def test_multiple_keys_per_slot_counted_once(self):
        env = {
            'BOT1_ENABLED': 'True',
            'BOT1_NAME': 'a',
            'BOT1_ACCESS_TOKEN': 'x',
        }
        self.assertEqual(discover_bot_slots(env), [1])

    def test_large_slot_numbers(self):
        env = {f'BOT{i}_ENABLED': 'True' for i in [1, 99, 123]}
        self.assertEqual(discover_bot_slots(env), [1, 99, 123])


class LoadBotConfigsFromEnvTest(unittest.TestCase):
    """load_bot_configs_from_env는 BOT_COUNT와 무관하게 슬롯을 탐지해야 함."""

    def _write_env(self, tmp_path: str, content: str) -> str:
        env_path = os.path.join(tmp_path, '.env')
        with open(env_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return env_path

    def _load_in_tempdir(self, env_lines):
        """임시 디렉터리에서 .env + credentials.json 만들고 설정 로드."""
        import tempfile
        original_cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            env_path = self._write_env(tmp, '\n'.join(env_lines))
            with open(os.path.join(tmp, 'credentials.json'), 'w') as f:
                f.write('{}')
            os.chdir(tmp)
            try:
                return load_bot_configs_from_env(env_path)
            finally:
                os.chdir(original_cwd)

    def test_non_contiguous_slots_all_loaded(self):
        """BOT1과 BOT5만 정의해도 둘 다 로드된다 (BOT_COUNT=1이어도 무시)."""
        configs = self._load_in_tempdir([
            'ENABLE_MULTI_BOT=True',
            'BOT_COUNT=1',   # 의도적으로 낮게 — 무시되어야 함
            'MASTODON_API_BASE_URL=https://example.com',
            'SHEET_ID=dummy',
            'GOOGLE_CREDENTIALS_PATH=credentials.json',
            '',
            'BOT1_ENABLED=True',
            'BOT1_NAME=first',
            'BOT1_ACCESS_TOKEN=tok1',
            'BOT1_BOT_TYPE=store',
            '',
            'BOT5_ENABLED=True',
            'BOT5_NAME=fifth',
            'BOT5_ACCESS_TOKEN=tok5',
            'BOT5_BOT_TYPE=investigate',
        ])
        self.assertEqual(len(configs), 2)
        bot_ids = sorted(cfg.bot_id for cfg in configs)
        self.assertEqual(bot_ids, ['BOT1', 'BOT5'])

    def test_disabled_slots_skipped(self):
        configs = self._load_in_tempdir([
            'ENABLE_MULTI_BOT=True',
            'BOT_COUNT=5',
            'MASTODON_API_BASE_URL=https://example.com',
            'SHEET_ID=dummy',
            'GOOGLE_CREDENTIALS_PATH=credentials.json',
            '',
            'BOT1_ENABLED=False',
            'BOT1_NAME=first',
            'BOT1_ACCESS_TOKEN=tok1',
            '',
            'BOT3_ENABLED=True',
            'BOT3_NAME=third',
            'BOT3_ACCESS_TOKEN=tok3',
            'BOT3_BOT_TYPE=investigate',
        ])
        self.assertEqual(len(configs), 1)
        self.assertEqual(configs[0].bot_id, 'BOT3')

    def test_no_slots_returns_empty(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            env_path = self._write_env(tmp, 'ENABLE_MULTI_BOT=True\n')
            configs = load_bot_configs_from_env(env_path)
            self.assertEqual(configs, [])

    def test_multi_bot_disabled_returns_empty(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            env_path = self._write_env(tmp, '\n'.join([
                'ENABLE_MULTI_BOT=False',
                'BOT1_ENABLED=True',
                'BOT1_NAME=first',
                'BOT1_ACCESS_TOKEN=tok1',
            ]))
            configs = load_bot_configs_from_env(env_path)
            self.assertEqual(configs, [])


if __name__ == '__main__':
    unittest.main()

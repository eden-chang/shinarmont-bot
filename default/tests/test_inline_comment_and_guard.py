"""
.env 인라인 주석 파싱 + 등록 키워드 보호 가드 테스트.

- load_bot_configs_from_env가 `VAR=value  # comment` 형태의 인라인 주석을 제대로 제거하는지
- registry.is_keyword_registered가 봇 타입 필터를 우회해 "등록 여부"만 반환하는지
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from config.bot_config import load_bot_configs_from_env


class InlineCommentParsingTest(unittest.TestCase):
    def _load(self, env_lines):
        original_cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            env_path = os.path.join(tmp, '.env')
            with open(env_path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(env_lines))
            with open(os.path.join(tmp, 'credentials.json'), 'w') as f:
                f.write('{}')
            os.chdir(tmp)
            try:
                return load_bot_configs_from_env(env_path)
            finally:
                os.chdir(original_cwd)

    def test_inline_comment_stripped_from_bot_type(self):
        """BOT4_BOT_TYPE=investigate    # comment → bot_type='investigate'."""
        configs = self._load([
            'ENABLE_MULTI_BOT=True',
            'MASTODON_API_BASE_URL=https://example.com',
            'SHEET_ID=dummy',
            'GOOGLE_CREDENTIALS_PATH=credentials.json',
            '',
            'BOT4_ENABLED=True',
            'BOT4_NAME=inv',
            'BOT4_ACCESS_TOKEN=tok',
            'BOT4_BOT_TYPE=investigate    # default | store | stats | investigate',
        ])
        self.assertEqual(len(configs), 1)
        self.assertEqual(configs[0].bot_type, 'investigate')
        # to_env_dict도 깨끗해야 함 — 서브프로세스 인젝션 대상
        self.assertEqual(configs[0].to_env_dict()['BOT_TYPE'], 'investigate')

    def test_inline_comment_stripped_from_various_fields(self):
        configs = self._load([
            'ENABLE_MULTI_BOT=True',
            'MASTODON_API_BASE_URL=https://example.com',
            'SHEET_ID=dummy',
            'GOOGLE_CREDENTIALS_PATH=credentials.json',
            '',
            'BOT1_ENABLED=True',
            'BOT1_NAME=sto    # 상점 봇',
            'BOT1_ACCESS_TOKEN=tok1',
            'BOT1_BOT_TYPE=store    # 한줄 주석',
        ])
        self.assertEqual(len(configs), 1)
        self.assertEqual(configs[0].bot_type, 'store')
        self.assertEqual(configs[0].bot_name, 'sto')


class RegisteredKeywordGuardTest(unittest.TestCase):
    """등록된 키워드는 봇 타입 필터로 걸러져도 '등록된 상태'로 판별되어야 한다."""

    def setUp(self):
        # 레지스트리 싱글톤 활용
        os.environ['INVESTIGATION_ENABLED'] = 'True'
        os.environ['BOT_TYPE'] = 'store'
        from config.settings import Config, config
        Config.INVESTIGATION_ENABLED = True
        Config.BOT_TYPE = 'store'
        config.INVESTIGATION_ENABLED = True
        config.BOT_TYPE = 'store'

        from commands.registry import get_registry
        self.registry = get_registry()
        self.registry.discover_commands()

    def test_investigate_keyword_registered_even_on_store_bot(self):
        """상점 봇(BOT_TYPE=store)에서도 [장소 목록]은 '등록됨'으로 판별."""
        self.assertTrue(self.registry.is_keyword_registered('장소 목록'))
        self.assertTrue(self.registry.is_keyword_registered('장소목록'))
        self.assertTrue(self.registry.is_keyword_registered('진입'))
        self.assertTrue(self.registry.is_keyword_registered('조사'))

    def test_get_command_by_keyword_filtered_returns_none_on_store_bot(self):
        """하지만 get_command_by_keyword는 필터링으로 None 반환 (기존 동작 유지)."""
        self.assertIsNone(self.registry.get_command_by_keyword('장소 목록'))
        self.assertIsNone(self.registry.get_command_by_keyword('진입'))

    def test_unknown_keyword_not_registered(self):
        self.assertFalse(self.registry.is_keyword_registered('존재하지않는명령어_xyz'))

    def test_empty_keyword(self):
        self.assertFalse(self.registry.is_keyword_registered(''))
        self.assertFalse(self.registry.is_keyword_registered(None))


if __name__ == '__main__':
    unittest.main()

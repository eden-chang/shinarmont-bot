"""설정 숫자 파싱: 빈 값·오타에 봇이 죽으면 안 된다.

회귀 배경(2026-07-16): `.env.shinarmont`를 `.env`로 복사하면 `BOT_COUNT=`(빈 값) 때문에
`int('')`가 터져 **config 임포트 시점에 5슬롯이 전부 기동 실패**했다.
설정 한 줄 오타로 봇 전체가 안 뜨는 건 과하다 — 기본값 폴백 + 경고가 맞다.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import settings  # noqa: E402

_KEY = '_TEST_ENV_NUM'


class EnvIntTest(unittest.TestCase):
    def tearDown(self):
        os.environ.pop(_KEY, None)

    def _set(self, raw):
        os.environ[_KEY] = raw

    def test_unset_uses_default(self):
        os.environ.pop(_KEY, None)
        self.assertEqual(settings._env_int(_KEY, 21), 21)

    def test_empty_uses_default(self):
        """`.env`의 `KEY=` — 실제로 봇을 죽였던 값."""
        self._set('')
        self.assertEqual(settings._env_int(_KEY, 21), 21)

    def test_whitespace_uses_default(self):
        self._set('   ')
        self.assertEqual(settings._env_int(_KEY, 21), 21)

    def test_garbage_uses_default_instead_of_raising(self):
        self._set('스물하나')
        self.assertEqual(settings._env_int(_KEY, 21), 21)

    def test_valid_value_wins(self):
        self._set('5')
        self.assertEqual(settings._env_int(_KEY, 21), 5)

    def test_zero_survives(self):
        """0이 기본값으로 되돌아가면 안 된다.

        옛 `int(os.getenv(K, D) or D)` 관용구가 정확히 이 함정을 갖고 있었다.
        """
        self._set('0')
        self.assertEqual(settings._env_int(_KEY, 21), 0)

    def test_negative_survives(self):
        self._set('-3')
        self.assertEqual(settings._env_int(_KEY, 21), -3)

    def test_surrounding_whitespace_is_trimmed(self):
        self._set('  7  ')
        self.assertEqual(settings._env_int(_KEY, 21), 7)

    def test_float_variant(self):
        self._set('1.5')
        self.assertEqual(settings._env_float(_KEY, 2.0), 1.5)
        self._set('')
        self.assertEqual(settings._env_float(_KEY, 2.0), 2.0)


class TemplateEnvTest(unittest.TestCase):
    """템플릿(.env.shinarmont)을 그대로 .env로 복사해도 설정이 로드돼야 한다."""

    def test_template_blank_numeric_keys_do_not_crash(self):
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(base, '.env.shinarmont')
        if not os.path.exists(path):
            self.skipTest('.env.shinarmont 없음')

        blanks = []
        with open(path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and line.endswith('='):
                    blanks.append(line[:-1])

        # 빈 값으로 둔 키를 전부 넣고 _env_int를 태워도 예외가 없어야 한다
        for key in blanks:
            os.environ[key] = ''
        try:
            for key in blanks:
                self.assertEqual(settings._env_int(key, 99), 99,
                                 f"{key}=(빈 값) 이 기본값으로 폴백되지 않았다.")
        finally:
            for key in blanks:
                os.environ.pop(key, None)


class ConfigWiringTest(unittest.TestCase):
    """`.env`에 있는 키는 **config에도 정의**돼 있어야 한다.

    회귀 배경(2026-07-16): `config_int()`는 `getattr(config, KEY, default)`라
    **config에 없으면 .env를 보지도 않는다.** 도박 설정 8개가 전부 이 함정에 빠져 있었고,
    값이 코드 기본값과 우연히 같아서 동작으로는 드러나지 않았다.
    GM이 .env를 고쳐도 아무 일도 일어나지 않는 상태였다.
    (앞서 POLLING_INTERVAL 도 같은 이유로 30에 묶여 있었다)
    """

    # 명령어/유틸이 `config_int`·`getattr(config, ...)`로 읽는 키.
    # 새 설정을 추가하면 여기에도 넣을 것 — 그래야 배선을 잊지 않는다.
    MUST_BE_WIRED = [
        # 도박
        'SLOT_BET_MIN', 'SLOT_BET_MAX', 'SLOT_DAILY_LIMIT',
        'CRAPS_BET_MIN', 'CRAPS_BET_MAX', 'CRAPS_MAX_REROLLS', 'CRAPS_DAILY_LIMIT',
        'BLACKJACK_DAILY_LIMIT', 'BLACKJACK_HIT_ON_SOFT_17',
        # 크레덴셜
        'CREDENTIAL_COOLDOWN',
        # 스트리밍
        'POLLING_INTERVAL',
        # 일일보고
        'DIGEST_ENABLED', 'DIGEST_AI_SEEDS', 'DIGEST_RECIPIENT_ID',
        'DIGEST_HOUR', 'DIGEST_MINUTE', 'DIGEST_SEED_COUNT', 'DIGEST_CHUNK_LIMIT',
        'DIGEST_INCLUDE_TRANSCRIPT', 'DIGEST_TRANSCRIPT_CHARS',
        'DIGEST_DOCTOR_SUMMARY',
    ]

    def test_keys_are_defined_on_config(self):
        from config.settings import config
        missing = [k for k in self.MUST_BE_WIRED if not hasattr(config, k)]
        self.assertEqual(
            missing, [],
            f"config에 없는 키 → .env 값이 무시된다: {missing}"
        )

    def test_env_template_keys_reach_config(self):
        """`.env.shinarmont`에 적어 둔 도박 설정이 실제로 config에 도달하는가.

        템플릿에만 있고 코드가 안 읽는 설정은 **거짓 약속**이다.
        """
        import os as _os
        base = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
        path = _os.path.join(base, '.env.shinarmont')
        if not _os.path.exists(path):
            self.skipTest('.env.shinarmont 없음')

        keys = set()
        with open(path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    keys.add(line.split('=', 1)[0].strip())

        from config.settings import config
        gambling = {k for k in keys
                    if k.startswith(('SLOT_', 'CRAPS_', 'BLACKJACK_'))}
        missing = sorted(k for k in gambling if not hasattr(config, k))
        self.assertEqual(missing, [], f".env.shinarmont 에 있으나 config가 모르는 도박 설정: {missing}")


if __name__ == '__main__':
    unittest.main()

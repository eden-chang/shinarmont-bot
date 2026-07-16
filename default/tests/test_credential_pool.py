"""utils/credential_pool.py + SheetsManager 크레덴셜 failover 테스트."""

import json
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from config.settings import config
from utils import credential_pool as cp
from utils.credential_pool import (
    PURPOSE_INVESTIGATION, PURPOSE_MAIN, PURPOSE_SYSTEM, CredentialPool,
    is_permission_error, is_quota_error,
)


class QuotaErrorDetectionTest(unittest.TestCase):
    """429 판별 — 기존 safe_execute가 429를 재시도하지 않아 여기서 잡아야 한다."""

    def test_detects_429(self):
        self.assertTrue(is_quota_error(Exception('APIError: [429]: Quota exceeded')))

    def test_detects_resource_exhausted(self):
        self.assertTrue(is_quota_error(Exception('RESOURCE_EXHAUSTED')))

    def test_detects_rate_limit_text(self):
        self.assertTrue(is_quota_error(Exception('Rate Limit reached')))

    def test_ignores_other_errors(self):
        self.assertFalse(is_quota_error(Exception('APIError: [500]: Internal error')))
        self.assertFalse(is_quota_error(Exception('SpreadsheetNotFound')))

    def test_detects_permission(self):
        self.assertTrue(is_permission_error(Exception('APIError: [403]: PERMISSION_DENIED')))
        self.assertFalse(is_permission_error(Exception('APIError: [429]')))


class PoolBaseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        for name in ('alpha', 'bravo', 'charlie'):
            path = os.path.join(self.tmp, f'{name}_credentials.json')
            with open(path, 'w', encoding='utf-8') as f:
                json.dump({'type': 'service_account', 'project_id': f'proj-{name}'}, f)
        self._saved = {k: getattr(config, k) for k in (
            'CREDENTIAL_MAIN', 'CREDENTIAL_SYSTEM', 'CREDENTIAL_INVESTIGATION',
            'CREDENTIAL_BORROW_ENABLED')}
        config.CREDENTIAL_MAIN = 'alpha'
        config.CREDENTIAL_SYSTEM = 'bravo'
        config.CREDENTIAL_INVESTIGATION = 'charlie'
        config.CREDENTIAL_BORROW_ENABLED = True

    def tearDown(self):
        for k, v in self._saved.items():
            setattr(config, k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)
        cp.set_pool(None)

    def _pool(self, cooldown=60.0):
        return CredentialPool(directory=self.tmp, cooldown=cooldown)


class DiscoveryTest(PoolBaseTest):
    def test_discovers_all(self):
        self.assertEqual(sorted(self._pool()._creds), ['alpha', 'bravo', 'charlie'])

    def test_name_strips_credentials_suffix(self):
        self.assertIn('alpha', self._pool()._creds)

    def test_assignment_from_config(self):
        p = self._pool()
        self.assertEqual(p.acquire(PURPOSE_MAIN), 'alpha')
        self.assertEqual(p.acquire(PURPOSE_SYSTEM), 'bravo')
        self.assertEqual(p.acquire(PURPOSE_INVESTIGATION), 'charlie')

    def test_purposes_get_different_primaries(self):
        """용도를 나누는 이유: 프로젝트마다 쿼터가 따로다."""
        p = self._pool()
        primaries = {p.acquire(x) for x in (PURPOSE_MAIN, PURPOSE_SYSTEM, PURPOSE_INVESTIGATION)}
        self.assertEqual(len(primaries), 3)


class BorrowTest(PoolBaseTest):
    def test_borrows_when_primary_exhausted(self):
        p = self._pool()
        p.report_quota_exhausted('alpha')
        self.assertIn(p.acquire(PURPOSE_MAIN), ('bravo', 'charlie'))

    def test_borrow_disabled_keeps_only_assigned(self):
        config.CREDENTIAL_BORROW_ENABLED = False
        p = self._pool()
        self.assertEqual(p.candidates(PURPOSE_MAIN), ['alpha'])

    def test_cooldown_expires(self):
        p = self._pool(cooldown=0.0)
        p.report_quota_exhausted('alpha')
        self.assertEqual(p.acquire(PURPOSE_MAIN), 'alpha', "쿨다운이 끝나면 다시 쓴다.")

    def test_success_clears_cooldown(self):
        p = self._pool()
        p.report_quota_exhausted('alpha')
        p.report_success('alpha')
        self.assertEqual(p.acquire(PURPOSE_MAIN), 'alpha')

    def test_all_resting_returns_soonest_instead_of_none(self):
        """전부 쉬는 중이어도 멈추지 않는다 — 가장 빨리 깨어나는 것으로 시도."""
        p = self._pool()
        for n in ('alpha', 'bravo', 'charlie'):
            p.report_quota_exhausted(n)
        self.assertIsNotNone(p.acquire(PURPOSE_MAIN))

    def test_no_access_excludes_permanently(self):
        p = self._pool()
        p.report_no_access(PURPOSE_MAIN, 'alpha')
        self.assertNotIn('alpha', p.candidates(PURPOSE_MAIN))

    def test_no_access_is_per_purpose(self):
        """한 시트에 공유가 안 됐다고 다른 시트까지 막으면 안 된다."""
        p = self._pool()
        p.report_no_access(PURPOSE_MAIN, 'alpha')
        self.assertIn('alpha', p.candidates(PURPOSE_SYSTEM))

    def test_exclude_skips_tried(self):
        p = self._pool()
        self.assertNotEqual(p.acquire(PURPOSE_MAIN, exclude=['alpha']), 'alpha')


class LegacyFallbackTest(unittest.TestCase):
    def setUp(self):
        # 루트 크레덴셜을 임시 파일로 가리킨다(실제 파일이 있어야 폴백이 동작).
        self.tmp = tempfile.mkdtemp()
        self.legacy = os.path.join(self.tmp, 'legacy.json')
        with open(self.legacy, 'w', encoding='utf-8') as f:
            json.dump({'type': 'service_account'}, f)
        # get_credentials_path()는 classmethod라 **클래스 속성**을 봐야 한다
        self._saved = type(config).GOOGLE_CREDENTIALS_PATH
        type(config).GOOGLE_CREDENTIALS_PATH = self.legacy

    def tearDown(self):
        type(config).GOOGLE_CREDENTIALS_PATH = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)
        cp.set_pool(None)

    def test_empty_dir_falls_back_to_root_credential(self):
        """credentials/ 가 비면 기존 루트 credentials.json을 쓴다(하위 호환)."""
        empty = tempfile.mkdtemp()
        try:
            p = CredentialPool(directory=empty)
            self.assertIn('default', p._creds)
        finally:
            shutil.rmtree(empty, ignore_errors=True)

    def test_root_not_used_when_dir_has_credentials(self):
        """루트 계정은 '기본' 시트에 읽기 전용이라, 디렉터리가 있으면 쓰지 않는다.

        읽기는 통과하고 쓰기만 403으로 실패하는 계정이 풀에 섞이면
        출석·소지금 쓰기가 조용히 깨진다.
        """
        tmp = tempfile.mkdtemp()
        try:
            with open(os.path.join(tmp, 'x_credentials.json'), 'w') as f:
                json.dump({'type': 'service_account'}, f)
            p = CredentialPool(directory=tmp)
            self.assertNotIn('default', p._creds)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class SheetsManagerFailoverTest(PoolBaseTest):
    """SheetsManager._with_failover — 쿼터/권한 오류 시 크레덴셜 교체."""

    def _manager(self, purpose=PURPOSE_MAIN):
        from utils.sheets_operations import SheetsManager
        cp.set_pool(self._pool())
        m = SheetsManager.__new__(SheetsManager)
        m.purpose = purpose
        m._credential_name = None
        m.credentials_path = 'x'
        m._spreadsheet = None
        m._worksheets_cache = {}
        return m

    def test_no_purpose_runs_operation_directly(self):
        """기존 동작(purpose 없음)은 그대로."""
        m = self._manager(purpose=None)
        self.assertEqual(m._with_failover(lambda: 'ok'), 'ok')

    def test_success_returns_result(self):
        m = self._manager()
        self.assertEqual(m._with_failover(lambda: 'ok'), 'ok')

    def test_quota_error_switches_credential_and_retries(self):
        m = self._manager()
        calls = []

        def flaky():
            calls.append(m._credential_name)
            if len(calls) == 1:
                raise Exception('APIError: [429]: Quota exceeded')
            return 'ok'

        self.assertEqual(m._with_failover(flaky), 'ok')
        self.assertEqual(len(calls), 2)
        self.assertNotEqual(calls[0], calls[1], "다른 크레덴셜로 바꿔 재시도해야 한다.")

    def test_permission_error_switches_and_excludes(self):
        m = self._manager()
        calls = []

        def flaky():
            calls.append(m._credential_name)
            if len(calls) == 1:
                raise Exception('APIError: [403]: PERMISSION_DENIED')
            return 'ok'

        self.assertEqual(m._with_failover(flaky), 'ok')
        self.assertNotIn(calls[0], cp.get_pool().candidates(PURPOSE_MAIN))

    def test_other_errors_are_not_retried(self):
        """크레덴셜을 바꿔도 소용없는 오류는 즉시 올려보낸다."""
        m = self._manager()
        calls = []

        def boom():
            calls.append(1)
            raise ValueError('구조적 오류')

        with self.assertRaises(ValueError):
            m._with_failover(boom)
        self.assertEqual(len(calls), 1, "재시도하면 안 된다.")

    def test_all_credentials_exhausted_raises_last_error(self):
        m = self._manager()

        def always_quota():
            raise Exception('APIError: [429]: Quota exceeded')

        with self.assertRaises(Exception) as ctx:
            m._with_failover(always_quota)
        self.assertTrue(is_quota_error(ctx.exception))

    def test_tries_every_candidate_before_giving_up(self):
        m = self._manager()
        seen = []

        def always_quota():
            seen.append(m._credential_name)
            raise Exception('APIError: [429]')

        with self.assertRaises(Exception):
            m._with_failover(always_quota)
        self.assertEqual(len(set(seen)), 3, "후보 3개를 모두 시도해야 한다.")

    def test_switching_resets_connection(self):
        """인증이 바뀌면 기존 연결 객체는 못 쓴다."""
        m = self._manager()
        m._with_failover(lambda: 'ok')       # alpha 선택
        m._spreadsheet = object()
        m._worksheets_cache['x'] = object()
        m._use_credential('bravo')
        self.assertIsNone(m._spreadsheet)
        self.assertEqual(m._worksheets_cache, {})

    def test_sticky_credential_reused_while_healthy(self):
        """정상이면 계속 같은 크레덴셜을 쓴다(불필요한 재인증 방지)."""
        m = self._manager()
        m._with_failover(lambda: 'ok')
        first = m._credential_name
        m._with_failover(lambda: 'ok')
        self.assertEqual(m._credential_name, first)


class WrappedErrorDetectionTest(unittest.TestCase):
    """감싼 예외에서도 429/403을 알아봐야 한다.

    회귀 배경(2026-07-16 실측): `sheets_operations.connect_to_sheet`가
    `SheetAccessError(f"스프레드시트 연결 실패: {str(e)}")`로 감쌌는데 gspread APIError의
    `str()`이 비어서 로그에 `스프레드시트 연결 실패: ` 만 남았다. 문자열만 보던 판정이
    429를 놓쳐 **페일오버가 돌지 않았고**, 커스텀 시트 읽기가 죽은 뒤
    "데이터가 없습니다."로 조용히 보고됐다.
    """

    class _Resp:
        def __init__(self, code):
            self.status_code = code

    class _APIError(Exception):
        """str()이 비어 있는 gspread APIError 흉내."""

        def __init__(self, code):
            super().__init__()
            self.response = WrappedErrorDetectionTest._Resp(code)

    def _wrapped(self, code, message='스프레드시트 연결 실패'):
        try:
            try:
                raise self._APIError(code)
            except Exception as e:
                raise RuntimeError(f"{message}: {e!r}") from e
        except RuntimeError as w:
            return w

    def test_quota_detected_through_cause_chain(self):
        self.assertTrue(cp.is_quota_error(self._wrapped(429)))

    def test_permission_detected_through_cause_chain(self):
        self.assertTrue(cp.is_permission_error(self._wrapped(403)))

    def test_status_code_wins_when_str_is_empty(self):
        """예외 str()이 비어도 상태 코드로 잡아야 한다."""
        err = self._APIError(429)
        self.assertEqual(str(err), '')
        self.assertTrue(cp.is_quota_error(err))

    def test_plain_message_still_works(self):
        self.assertTrue(cp.is_quota_error(Exception('429 RESOURCE_EXHAUSTED')))
        self.assertTrue(cp.is_quota_error(Exception('Quota exceeded for reads')))
        self.assertTrue(cp.is_permission_error(Exception('403 PERMISSION_DENIED')))

    def test_no_false_positive(self):
        """평범한 오류를 쿼터로 오인하면 멀쩡한 크레덴셜을 쉬게 만든다."""
        for msg in ('워크시트를 찾을 수 없습니다', 'SpreadsheetNotFound', '연결 시간 초과'):
            err = Exception(msg)
            self.assertFalse(cp.is_quota_error(err), msg)
            self.assertFalse(cp.is_permission_error(err), msg)

    def test_500_is_not_quota(self):
        self.assertFalse(cp.is_quota_error(self._wrapped(500)))

    def test_cyclic_cause_chain_terminates(self):
        """__cause__ 순환에도 무한 루프에 빠지면 안 된다."""
        a, b = Exception('a'), Exception('b')
        a.__cause__ = b
        b.__cause__ = a
        self.assertFalse(cp.is_quota_error(a))       # 멈추기만 하면 된다

    def test_deep_chain(self):
        inner = self._APIError(429)
        mid = RuntimeError('중간')
        mid.__cause__ = inner
        outer = RuntimeError('바깥')
        outer.__cause__ = mid
        self.assertTrue(cp.is_quota_error(outer))


class RotationTest(unittest.TestCase):
    """429를 맞으면 다음 후보로 넘어가고, 쿨다운이 끝나면 다시 후보에 들어온다.

    **주 크레덴셜로 굳이 돌아가지 않는다**(운영 결정 2026-07-16).
    지금 쓰는 것이 429를 맞을 때까지 쓰고, 맞으면 다음으로 — 한 바퀴 돌면
    자연히 처음 것으로 되돌아온다.
    """

    def _pool(self, cooldown=300.0):
        import threading
        pool = cp.CredentialPool.__new__(cp.CredentialPool)
        pool._lock = threading.Lock()
        pool._cooldown = cooldown
        pool._creds = {'a': 'a.json', 'b': 'b.json', 'c': 'c.json'}
        pool._resting = {}
        pool._no_access = set()
        pool._assignment = {'main': ['a', 'b', 'c']}
        return pool

    def test_default_cooldown_is_five_minutes(self):
        self.assertEqual(cp._DEFAULT_COOLDOWN, 300.0)

    def test_quota_puts_credential_to_rest(self):
        pool = self._pool()
        pool.report_quota_exhausted('a')
        rest = pool._resting['a'] - time.time()
        self.assertTrue(290 < rest <= 300, f"쿨다운 {rest:.0f}초")

    def test_acquire_skips_resting_and_moves_on(self):
        pool = self._pool()
        self.assertEqual(pool.acquire('main'), 'a')
        pool.report_quota_exhausted('a')
        self.assertEqual(pool.acquire('main'), 'b')
        pool.report_quota_exhausted('b')
        self.assertEqual(pool.acquire('main'), 'c')

    def test_cycles_back_after_cooldown(self):
        """전부 한 번씩 돌고 나면, 쿨다운이 끝난 것이 다시 후보가 된다."""
        pool = self._pool()
        for name in ('a', 'b', 'c'):
            pool.report_quota_exhausted(name)
        # 전부 쉬는 중 → acquire 는 가장 빨리 깨는 것으로 폴백(멈추는 것보다 낫다)
        self.assertIn(pool.acquire('main'), ('a', 'b', 'c'))
        pool._resting['a'] = time.time() - 1        # a 쿨다운 만료
        self.assertEqual(pool.acquire('main'), 'a', "쉬고 나면 다시 후보")

    def test_exclude_prevents_retrying_the_same_one(self):
        pool = self._pool()
        self.assertEqual(pool.acquire('main', exclude=['a']), 'b')
        self.assertEqual(pool.acquire('main', exclude=['a', 'b']), 'c')

    def test_no_access_is_permanent(self):
        """쿼터는 쉬면 풀리지만, 공유 안 된 계정은 영구 제외다."""
        pool = self._pool()
        pool.report_no_access('main', 'a')
        self.assertNotIn('a', pool.candidates('main'))
        self.assertEqual(pool.acquire('main'), 'b')


class RotationIntegrationTest(unittest.TestCase):
    """SheetsManager 가 실제로 순환하는지 — 붙박이면 프로세스가 죽을 때까지 그 크레덴셜만 쓴다."""

    class _Resp:
        def __init__(self, code):
            self.status_code = code

    class _APIError(Exception):
        def __init__(self, code):
            super().__init__()
            self.response = RotationIntegrationTest._Resp(code)

    def setUp(self):
        import threading
        from utils.sheets_operations import SheetsManager

        pool = cp.CredentialPool.__new__(cp.CredentialPool)
        pool._lock = threading.Lock()
        pool._cooldown = 300.0
        pool._creds = {'a': 'a.json', 'b': 'b.json', 'c': 'c.json'}
        pool._resting = {}
        pool._no_access = set()
        pool._assignment = {'main': ['a', 'b', 'c']}
        cp.set_pool(pool)
        self.addCleanup(cp.set_pool, None)
        self.pool = pool

        mgr = SheetsManager.__new__(SheetsManager)
        mgr.purpose = 'main'
        mgr._credential_name = None
        mgr.credentials_path = None
        mgr._spreadsheet = None
        mgr.sheet_id = 'x'
        mgr._header_warned = set()
        mgr._use_credential = self._use
        self.mgr = mgr
        self.quota = set()

    def _use(self, name):
        self.mgr._credential_name = name
        self.mgr.credentials_path = f'{name}.json'
        self.mgr._spreadsheet = None

    def _op(self):
        if self.mgr._credential_name in self.quota:
            raise self._APIError(429)
        return self.mgr._credential_name

    def test_moves_to_next_on_quota(self):
        self.quota = {'a'}
        self.assertEqual(self.mgr._with_failover(self._op), 'b')

    def test_stays_on_borrowed_credential(self):
        """빌린 뒤에는 그게 429를 맞을 때까지 계속 쓴다(주 크레덴셜로 안 돌아간다)."""
        self.quota = {'a'}
        self.mgr._with_failover(self._op)
        self.quota.clear()                              # a 가 멀쩡해져도
        self.pool._resting['a'] = time.time() - 1       # 쿨다운도 끝났지만
        for _ in range(3):
            self.assertEqual(self.mgr._with_failover(self._op), 'b', "b 를 계속 써야 한다")

    def test_cycles_when_borrowed_one_also_fails(self):
        self.quota = {'a'}
        self.assertEqual(self.mgr._with_failover(self._op), 'b')
        self.quota = {'a', 'b'}
        self.assertEqual(self.mgr._with_failover(self._op), 'c')

    def test_full_circle_back_to_first(self):
        """c 까지 갔다가 a 쿨다운이 끝나면 a 로 돌아온다(한 바퀴)."""
        self.quota = {'a', 'b'}
        self.assertEqual(self.mgr._with_failover(self._op), 'c')
        self.quota = {'b', 'c'}
        self.pool._resting['a'] = time.time() - 1       # a 는 쉬고 나왔다
        self.assertEqual(self.mgr._with_failover(self._op), 'a')


if __name__ == '__main__':
    unittest.main()

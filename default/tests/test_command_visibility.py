"""utils/command_visibility.py — 명령어별 공개 범위 강제 테스트.

명령어별 허용표는 운영 정책이므로 **명세를 그대로 표로 고정**한다.
표가 바뀌면 이 테스트가 깨져서 의도적 변경임을 드러내야 한다.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from config.settings import config
from utils import command_visibility as cv


def setUpModule():
    """별칭 해석은 레지스트리의 키워드 맵에 의존한다.

    프로덕션에서는 기동 시 라우터가 discover_commands()를 호출한 뒤에야 멘션을 받으므로
    맵이 항상 채워져 있다. 테스트도 같은 전제를 만든다.
    """
    from commands.registry import get_registry
    get_registry().discover_commands()

DIRECT_ONLY_MSG = (
    "이 명령어는 **다이렉트 메시지** 범위로만 사용할 수 있습니다. "
    "위 툿을 삭제한 후, 범위를 올바르게 설정하여 다시 업로드하세요."
)
PRIVATE_OR_DIRECT_MSG = (
    "이 명령어는 **팔로워 전용** 혹은 **다이렉트 메시지** 범위로만 사용할 수 있습니다. "
    "위 툿을 삭제한 후, 범위를 올바르게 설정하여 다시 업로드하세요."
)
PRIVATE_ONLY_MSG = (
    "이 명령어는 **팔로워 전용** 범위로만 사용할 수 있습니다. "
    "위 툿을 삭제한 후, 범위를 올바르게 설정하여 다시 업로드하세요."
)


class MessageTest(unittest.TestCase):
    """명세에 적힌 3종 문구가 글자 그대로 생성되는지."""

    def test_direct_only_message(self):
        self.assertEqual(cv.message_for(('direct',)), DIRECT_ONLY_MSG)

    def test_private_or_direct_message(self):
        self.assertEqual(cv.message_for(('private', 'direct')), PRIVATE_OR_DIRECT_MSG)

    def test_private_only_message(self):
        self.assertEqual(cv.message_for(('private',)), PRIVATE_ONLY_MSG)

    def test_three_ranges_message(self):
        """양도(unlisted+private+direct)용 — 명세에 없어 규칙에서 파생."""
        self.assertEqual(
            cv.format_ranges(('unlisted', 'private', 'direct')),
            '**미등재**, **팔로워 전용** 혹은 **다이렉트 메시지**',
        )

    def test_label_order_is_wide_to_narrow(self):
        """입력 순서와 무관하게 넓은 범위 → 좁은 범위 순으로 나열."""
        self.assertEqual(
            cv.format_ranges(('direct', 'private')),
            '**팔로워 전용** 혹은 **다이렉트 메시지**',
        )


class RuleTableTest(unittest.TestCase):
    """명세의 허용표를 그대로 고정한다."""

    SPEC = {
        # @SYSTEM
        '상태 확인':   {'private', 'direct'},
        '출석':        {'private'},
        '상점':        {'private', 'direct'},
        '구매':        {'private', 'direct'},
        '아이템 설명': {'private', 'direct'},   # [설명/아이템명]
        '사용':        {'private', 'direct'},
        '양도':        {'unlisted', 'private', 'direct'},
        # @TOWN
        '장소 목록':   {'direct'},
        '진입':        {'direct'},
        '조사':        {'direct'},
        '추적':        {'direct'},
        # @STORY
        '투표':        {'direct'},
        '대화':        {'direct'},
        '고발':        {'direct'},
        '결과 보고':   {'direct'},
        '교류':        {'direct'},
        # @DOCTOR
        '의무실 방문': {'direct'},
        '대화 끝내기': {'direct'},
        # @BAR
        # 2026-07-16 운영 결정: 도박은 팔로워 전용/다이렉트 둘 다 허용
        '블랙잭':      {'private', 'direct'},
        '슬롯머신':    {'private', 'direct'},
        '크랩스':      {'private', 'direct'},
        # 공통 / 관리자
        '도움말':      {'private', 'direct'},
        '캐시 리셋':   {'direct'},
        '소지금 관리': {'direct'},
        '스탯 변경':   {'direct'},
        '조사 개방':   {'direct'},
        '일일보고':    {'direct'},
    }

    def test_table_matches_spec(self):
        self.assertEqual(
            {k: set(v) for k, v in cv.RULES.items()}, self.SPEC,
            "허용표가 명세와 다르다 — 의도한 변경이면 SPEC도 함께 고쳐라",
        )

    def test_public_never_allowed(self):
        for name, allowed in cv.RULES.items():
            with self.subTest(command=name):
                self.assertNotIn('public', allowed)

    def test_every_registered_command_has_a_rule(self):
        """새 명령어가 규칙 없이 추가되면 전역 설정으로 조용히 새어 나간다.

        전역(BOT1=unlisted)은 느슨하므로, 규칙 누락은 곧 '의도치 않은 공개'다.
        명령어를 추가하면 이 테스트가 깨져서 범위를 정하도록 강제한다.
        """
        from commands.registry import get_registry
        registered = set(get_registry()._commands.keys())
        missing = sorted(registered - set(cv.RULES))
        self.assertEqual(missing, [], f"공개 범위 규칙이 없는 명령어: {missing}")


class CheckTest(unittest.TestCase):
    def test_allowed_returns_none(self):
        self.assertIsNone(cv.check('조사', 'direct'))
        self.assertIsNone(cv.check('출석', 'private'))
        self.assertIsNone(cv.check('양도', 'unlisted'))

    def test_direct_only_command_blocks_private(self):
        self.assertEqual(cv.check('조사', 'private'), DIRECT_ONLY_MSG)

    def test_private_only_command_blocks_direct(self):
        """출석은 팔로워 전용만 — DM으로 보내면 막힌다."""
        self.assertEqual(cv.check('출석', 'direct'), PRIVATE_ONLY_MSG)

    def test_private_or_direct_command_blocks_unlisted(self):
        self.assertEqual(cv.check('상점', 'unlisted'), PRIVATE_OR_DIRECT_MSG)

    def test_public_blocked_everywhere(self):
        for name in ('조사', '출석', '상점', '양도', '블랙잭'):
            with self.subTest(command=name):
                self.assertIsNotNone(cv.check(name, 'public'))

    def test_alias_resolves_to_canonical_rule(self):
        """[탐색/…]은 [조사]의 별칭 → 같은 규칙(DM 전용)."""
        self.assertEqual(cv.check('탐색', 'private'), DIRECT_ONLY_MSG)
        self.assertIsNone(cv.check('탐색', 'direct'))

    def test_alias_of_private_only_command(self):
        """별칭도 정식 명령어의 허용 범위를 따른다.

        [출석]은 팔로워 전용이라 DM으로 보내면 거절된다.
        (예전엔 [슬롯]→[슬롯머신]으로 검증했으나 도박이 private+direct로 완화돼
         '팔로워 전용' 사례가 아니게 됐다 — 2026-07-16)
        """
        self.assertEqual(cv.check('출석', 'direct'), PRIVATE_ONLY_MSG)
        self.assertIsNone(cv.check('출석', 'private'))

    def test_gambling_allows_private_and_direct(self):
        """도박 3종은 팔로워 전용/다이렉트 둘 다 가능(별칭 포함)."""
        for kw in ('블랙잭', '슬롯머신', '크랩스', '슬롯', '히트', '더블다운'):
            self.assertIsNone(cv.check(kw, 'private'), kw)
            self.assertIsNone(cv.check(kw, 'direct'), kw)
            self.assertIsNotNone(cv.check(kw, 'public'), kw)

    def test_whitespace_insensitive(self):
        self.assertEqual(cv.check('장소목록', 'private'), DIRECT_ONLY_MSG)
        self.assertEqual(cv.check('상태확인', 'unlisted'), PRIVATE_OR_DIRECT_MSG)

    def test_none_and_empty_visibility_blocked(self):
        self.assertIsNotNone(cv.check('조사', None))
        self.assertIsNotNone(cv.check('조사', ''))

    def test_case_insensitive_visibility(self):
        self.assertIsNone(cv.check('조사', 'DIRECT'))


class FallbackTest(unittest.TestCase):
    """표에 없는 키워드는 기존 전역 정책(BOTn_ALLOWED_VISIBILITY)을 따른다.

    등록된 명령어는 전부 규칙이 있으므로(test_every_registered_command_has_a_rule),
    폴백이 실제로 걸리는 대상은 **커스텀 명령어·미등록 키워드**뿐이다.
    """

    CUSTOM = '커스텀명령어예시'   # 레지스트리에 없는 키워드

    def setUp(self):
        self._orig = config.ALLOWED_VISIBILITY_LEVELS

    def tearDown(self):
        config.ALLOWED_VISIBILITY_LEVELS = self._orig

    def test_custom_command_uses_global(self):
        config.ALLOWED_VISIBILITY_LEVELS = ['private', 'direct']
        self.assertIsNone(cv.check(self.CUSTOM, 'private'))
        self.assertEqual(cv.check(self.CUSTOM, 'unlisted'), PRIVATE_OR_DIRECT_MSG)

    def test_global_direct_only(self):
        config.ALLOWED_VISIBILITY_LEVELS = ['direct']
        self.assertEqual(cv.check(self.CUSTOM, 'private'), DIRECT_ONLY_MSG)

    def test_rule_overrides_global_stricter(self):
        """전역이 느슨해도 표가 이기면 더 엄격해진다 (BOT1: unlisted 허용 → 출석은 private만)."""
        config.ALLOWED_VISIBILITY_LEVELS = ['unlisted', 'private', 'direct']
        self.assertEqual(cv.check('출석', 'direct'), PRIVATE_ONLY_MSG)

    def test_rule_overrides_global_looser(self):
        """전역이 엄격해도 표가 이기면 더 느슨해진다 (양도는 unlisted 허용)."""
        config.ALLOWED_VISIBILITY_LEVELS = ['direct']
        self.assertIsNone(cv.check('양도', 'unlisted'))

    def test_help_has_its_own_rule_not_global(self):
        """도움말은 이제 표에 있다 — 전역이 direct뿐이어도 팔로워 전용을 허용한다."""
        config.ALLOWED_VISIBILITY_LEVELS = ['direct']
        self.assertIsNone(cv.check('도움말', 'private'))

    def test_unknown_command_uses_global(self):
        config.ALLOWED_VISIBILITY_LEVELS = ['direct']
        self.assertIsNotNone(cv.check('존재하지않는명령', 'private'))


class PreBlockTest(unittest.TestCase):
    """범위 위반은 **다른 모든 로직 이전에** 차단돼야 한다.

    시트를 읽거나 명령어를 라우팅한 뒤 거절하면, 이미 노출된 툿에 봇이 결과를
    덧붙이거나 불필요한 API 쿼터를 태우게 된다.
    """

    def _handler(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        import handlers.stream_handler as sh

        h = sh.BotStreamHandler.__new__(sh.BotStreamHandler)
        h.api = MagicMock()
        h.sheets_manager = MagicMock()
        h.command_router = MagicMock()
        h._bot_acct_cache = set()
        h._send_status_with_retry = MagicMock()
        h._send_response = MagicMock()
        h._is_bot_account = lambda acct: False
        return h

    def _notification(self, text, visibility):
        from types import SimpleNamespace
        status = SimpleNamespace(
            id=1, visibility=visibility, content=f"<p>{text}</p>",
            account=SimpleNamespace(acct='alice'), mentions=[],
            in_reply_to_id=None, created_at=None,
        )
        return SimpleNamespace(status=status, type='mention', account=status.account)

    def setUp(self):
        self._orig_start = config.BOT_OPERATION_START
        config.BOT_OPERATION_START = ''

    def tearDown(self):
        config.BOT_OPERATION_START = self._orig_start

    def test_violation_never_reaches_router_or_sheets(self):
        h = self._handler()
        h._process_mention(self._notification('[조사/전압계]', 'private'))
        self.assertFalse(h.command_router.route_command.called, "명령어가 라우팅되면 안 된다.")
        self.assertFalse(h.sheets_manager.get_worksheet_data.called, "시트를 읽으면 안 된다.")
        self.assertFalse(h._send_response.called, "정상 응답이 나가면 안 된다.")

    def test_violation_sends_warning_as_direct(self):
        """경고는 DM으로 보낸다 — 잘못된 범위의 타래에 더 노출시키지 않는다."""
        h = self._handler()
        h._process_mention(self._notification('[조사/전압계]', 'private'))
        sent = h._send_status_with_retry.call_args
        self.assertIsNotNone(sent)
        self.assertEqual(sent.kwargs.get('visibility'), 'direct')
        self.assertIn('다이렉트 메시지', sent.kwargs.get('status'))

    def test_private_only_command_warning(self):
        h = self._handler()
        h._process_mention(self._notification('[출석]', 'direct'))
        self.assertFalse(h.command_router.route_command.called)
        self.assertIn('팔로워 전용', h._send_status_with_retry.call_args.kwargs.get('status'))

    def test_public_blocked(self):
        h = self._handler()
        h._process_mention(self._notification('[조사/전압계]', 'public'))
        self.assertFalse(h.command_router.route_command.called)

    def test_allowed_visibility_proceeds(self):
        h = self._handler()
        h._process_mention(self._notification('[조사/전압계]', 'direct'))
        self.assertTrue(h.command_router.route_command.called)
        self.assertFalse(h._send_status_with_retry.called, "경고가 나가면 안 된다.")

    def test_unlisted_allowed_for_transfer(self):
        """양도는 unlisted 허용 — 전역이 더 엄격해도 표가 이긴다."""
        h = self._handler()
        h._process_mention(self._notification('[양도/3달러/밥]', 'unlisted'))
        self.assertTrue(h.command_router.route_command.called)


if __name__ == '__main__':
    unittest.main()

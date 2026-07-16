"""
DM 전용 명령어 가드

DM(다이렉트 메시지)로만 사용 가능한 명령어의 execute 메서드를 감싸는
데코레이터를 제공한다. 마을/스토리/의사 계열 명령어에 사용한다.

사용 예:
    from utils.dm_guard import dm_only

    class SomeCommand(BaseCommand):
        @dm_only
        def execute(self, context: CommandContext) -> CommandResponse:
            ...
"""

import functools

from commands.base_command import CommandResponse

DM_ONLY_MESSAGE = "이 명령어는 DM(다이렉트 메시지)로만 사용할 수 있습니다."


def dm_only(func):
    """
    execute 메서드를 감싸 DM 전용으로 제한하는 데코레이터.

    감싼 함수 실행 전 context.get_metadata('visibility')가 'direct'가 아니면
    CommandResponse.create_error(DM_ONLY_MESSAGE)를 반환한다.

    execute(self, context) 형태(인스턴스 메서드)를 가정하되, self 없이
    execute(context)로 호출되는 경우도 지원한다.
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        context = _extract_context(args, kwargs)
        if context is None or context.get_metadata('visibility') != 'direct':
            return CommandResponse.create_error(DM_ONLY_MESSAGE)
        return func(*args, **kwargs)

    return wrapper


def _extract_context(args, kwargs):
    """호출 인자에서 CommandContext(가 될 만한 객체)를 찾아 반환."""
    if 'context' in kwargs:
        return kwargs['context']
    # 인스턴스 메서드: (self, context, ...) / 함수: (context, ...)
    for arg in args:
        if hasattr(arg, 'get_metadata'):
            return arg
    return None

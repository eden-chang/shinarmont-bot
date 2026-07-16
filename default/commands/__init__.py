"""
명령어 패키지 (commands)
모든 명령어 관련 모듈을 포함합니다.

핵심:
- base_command.py: 기본 명령어 클래스
- factory.py: 명령어 팩토리 패턴
- registry.py: 명령어 레지스트리

서브 패키지:
- default/: 기본 봇 명령어 (주사위, 카드, 운세, 커스텀)
- store/: 상점 명령어 (돈, 상점, 구매, 인벤토리, 송금)
- system/: 시스템 명령어 (도움말, 캐시 리셋)
"""

# 기본 클래스 및 유틸
from .base_command import BaseCommand, CommandContext, CommandResponse, create_command_context
from .registry import CommandRegistry, get_registry, register_command
from .factory import CommandFactory, get_factory

# 서브 패키지에서 주요 명령어 import
from .default import (
    # CardCommand,  # 비활성화됨
    # FortuneCommand,  # 제거됨(운세는 [출석]에 통합)
    get_custom_command_manager,
    execute_custom_command,
    is_custom_command
)

from .store import (
    MoneyAdminCommand,
    ShopCommand,
    BuyCommand,
    TransferCommand
)

from .system import (
    HelpCommand,
    CacheResetCommand
)

__all__ = [
    # 기본 클래스
    'BaseCommand',
    'CommandContext',
    'CommandResponse',
    'create_command_context',

    # 레지스트리 및 팩토리
    'CommandRegistry',
    'get_registry',
    'register_command',
    'CommandFactory',
    'get_factory',

    # default 명령어
    # 'CardCommand',  # 비활성화됨
    # 'FortuneCommand',  # 제거됨(운세는 [출석]에 통합)
    'get_custom_command_manager',
    'execute_custom_command',
    'is_custom_command',

    # store 명령어
    'MoneyAdminCommand',
    'ShopCommand',
    'BuyCommand',
    'TransferCommand',

    # system 명령어
    'HelpCommand',
    'CacheResetCommand'
]

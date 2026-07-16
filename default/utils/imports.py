"""
자동 Import 관리 모듈
명령어 개발 시 필요한 모든 의존성을 자동으로 import합니다.
"""

import os
import sys

# 경로 설정 (VM 환경 대응)
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))


class CommandImports:
    """
    명령어 개발 시 필요한 모든 import를 제공하는 헬퍼 클래스
    사용법:
        from utils.imports import CommandImports
        
        imports = CommandImports()
        CommandError = imports.CommandError
        logger = imports.logger
        bot_cache = imports.cache
        add_eul_reul = imports.add_eul_reul
    """
    
    def __init__(self):
        """모든 필요한 import 수행"""
        self._import_all()
    
    def _import_all(self):
        """모든 의존성 import"""
        # Core imports
        from config.settings import config
        self.config = config
        
        # Utils imports
        from utils.logging_config import logger
        self.logger = logger
        
        from utils.error_handling import CommandError
        self.CommandError = CommandError
        
        from utils.cache_manager import bot_cache
        self.cache = bot_cache
        self.bot_cache = bot_cache
        
        # Decorators
        from utils.decorators import handle_command_errors, validate_keywords, log_execution
        self.handle_command_errors = handle_command_errors
        self.validate_keywords = validate_keywords
        self.log_execution = log_execution
        
        # User helpers
        from utils.user_helpers import create_user_from_context
        self.create_user_from_context = create_user_from_context
        
        # Korean utils
        from utils.korean_utils import (
            add_eul_reul, add_eun_neun, add_i_ga,
            add_i_ga, get_last_char, has_final_consonant
        )
        self.add_eul_reul = add_eul_reul
        self.add_eun_neun = add_eun_neun
        self.add_i_ga = add_i_ga
        self.get_last_char = get_last_char
        self.has_final_consonant = has_final_consonant
        
        # Store helpers
        from utils.store_helpers import (
            parse_inventory_string,
            invalidate_user_cache,
            load_item_data,
            load_user_data
        )
        self.parse_inventory_string = parse_inventory_string
        self.invalidate_user_cache = invalidate_user_cache
        self.load_item_data = load_item_data
        self.load_user_data = load_user_data
        
        # Models
        from models.user import User, create_empty_user
        self.User = User
        self.create_empty_user = create_empty_user
        
        # Base command
        from commands.base_command import BaseCommand, CommandContext, CommandResponse
        self.BaseCommand = BaseCommand
        self.CommandContext = CommandContext
        self.CommandResponse = CommandResponse
        
        # Registry
        from commands.registry import register_command
        self.register_command = register_command


# 싱글톤 인스턴스 (개발 편의를 위해)
_imports_instance = None


def get_imports() -> CommandImports:
    """
    전역 import 객체 반환 (싱글톤)
    
    Returns:
        CommandImports: import 객체
    """
    global _imports_instance
    if _imports_instance is None:
        _imports_instance = CommandImports()
    return _imports_instance


# 편의를 위한 모듈 레벨 속성
imports = get_imports()

# 직접 사용 가능하도록 노출
CommandError = imports.CommandError
logger = imports.logger
bot_cache = imports.bot_cache
cache = imports.bot_cache
config = imports.config

# Korean utils
add_eul_reul = imports.add_eul_reul
add_eun_neun = imports.add_eun_neun
add_i_ga = imports.add_i_ga
get_last_char = imports.get_last_char
has_final_consonant = imports.has_final_consonant

# Store helpers
parse_inventory_string = imports.parse_inventory_string
invalidate_user_cache = imports.invalidate_user_cache
load_item_data = imports.load_item_data
load_user_data = imports.load_user_data

# Models
User = imports.User
create_empty_user = imports.create_empty_user

# Base command
BaseCommand = imports.BaseCommand
CommandContext = imports.CommandContext
CommandResponse = imports.CommandResponse

# Registry
register_command = imports.register_command

# Decorators
handle_command_errors = imports.handle_command_errors
validate_keywords = imports.validate_keywords
log_execution = imports.log_execution

# User helpers
create_user_from_context = imports.create_user_from_context


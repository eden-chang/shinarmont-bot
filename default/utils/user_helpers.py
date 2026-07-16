"""
User 생성 헬퍼 함수
Context에서 User 객체를 쉽게 생성할 수 있도록 합니다.
"""

import logging
import os
import sys

# 경로 설정
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

logger = logging.getLogger(__name__)

try:
    from models.user import User, create_empty_user
    from commands.base_command import CommandContext
except ImportError as e:
    logger.error("user_helpers 임포트 실패: %s", e)


def create_user_from_context(context: CommandContext) -> User:
    """
    Context에서 User 객체 자동 생성
    
    사용법:
        def execute(self, context: CommandContext) -> CommandResponse:
            user = create_user_from_context(context)
            # user.id, user.name 사용 가능
            ...
    
    Args:
        context: 명령어 실행 컨텍스트
        
    Returns:
        User: 생성된 User 객체
    """
    try:
        return User(id=context.user_id, name=context.user_name)
    except Exception as e:
        logger.warning("User 객체 생성 실패 (id=%s): %s", context.user_id, e)
        return create_empty_user(context.user_id)



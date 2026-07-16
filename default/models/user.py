"""
사용자 데이터 모델
사용자 정보를 관리하는 데이터 클래스들을 정의합니다.
"""

import os
import sys
import time
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
import pytz

# 경로 설정 (VM 환경 대응)
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

try:
    from utils.logging_config import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

try:
    from config.settings import config
    from utils.error_handling import UserNotFoundError, UserValidationError
except ImportError:
    # VM 환경에서 임포트 실패 시 폴백
    import importlib.util
    
    # config 로드
    config_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'settings.py')
    spec = importlib.util.spec_from_file_location("settings", config_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"설정 파일을 로드할 수 없습니다: {config_path}")
    settings_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(settings_module)
    config = settings_module.config
    
    # 기본 예외 클래스들
    class UserNotFoundError(Exception):
        pass
    
    class UserValidationError(Exception):
        pass


@dataclass
class User:
    """사용자 정보 모델"""
    
    id: str                              # 마스토돈 사용자 ID
    name: str                            # 사용자 이름
    created_at: Optional[datetime] = None  # 등록 시간
    last_active: Optional[datetime] = None # 마지막 활동 시간
    command_count: int = 0               # 총 명령어 사용 횟수
    additional_data: Dict[str, Any] = field(default_factory=dict)  # 추가 데이터
    
    def __post_init__(self):
        """초기화 후 처리"""
        if self.created_at is None:
            self.created_at = self._get_current_time()
        if self.last_active is None:
            self.last_active = self.created_at
    
    @classmethod
    def from_sheet_data(cls, data: Dict[str, Any]) -> 'User':
        """
        Google Sheets 데이터에서 User 객체 생성
        
        Args:
            data: 시트에서 가져온 행 데이터
            
        Returns:
            User: 생성된 사용자 객체
            
        Raises:
            UserValidationError: 필수 데이터가 없는 경우
        """
        if not data:
            raise UserValidationError("", "empty_data")
        
        # 필수 필드 검증
        user_id = str(data.get('아이디', '')).strip()
        user_name = str(data.get('이름', '')).strip()
        
        if not user_id:
            raise UserValidationError("", "missing_id")
        
        if not user_name:
            raise UserValidationError(user_id, "missing_name")
        
        # 추가 데이터 수집 (아이디, 이름 제외한 모든 컬럼)
        additional_data = {}
        for key, value in data.items():
            if key not in ['아이디', '이름'] and value:
                additional_data[key] = value
        
        return cls(
            id=user_id,
            name=user_name,
            additional_data=additional_data
        )
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'User':
        """
        딕셔너리에서 User 객체 생성 (캐시 데이터 등에서 사용)
        
        Args:
            data: 사용자 데이터 딕셔너리
            
        Returns:
            User: 생성된 사용자 객체
        """
        # datetime 문자열을 datetime 객체로 변환
        created_at = None
        last_active = None
        
        if 'created_at' in data and data['created_at']:
            created_at = cls._parse_datetime(data['created_at'])
        
        if 'last_active' in data and data['last_active']:
            last_active = cls._parse_datetime(data['last_active'])
        
        return cls(
            id=data.get('id', ''),
            name=data.get('name', ''),
            created_at=created_at,
            last_active=last_active,
            command_count=data.get('command_count', 0),
            additional_data=data.get('additional_data', {})
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """
        User 객체를 딕셔너리로 변환 (캐싱, 직렬화용)
        
        Returns:
            Dict: 사용자 데이터 딕셔너리
        """
        return {
            'id': self.id,
            'name': self.name,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'last_active': self.last_active.isoformat() if self.last_active else None,
            'command_count': self.command_count,
            'additional_data': self.additional_data
        }
    
    def to_sheet_format(self) -> Dict[str, Any]:
        """
        Google Sheets 형식으로 변환
        
        Returns:
            Dict: 시트 저장용 데이터
        """
        sheet_data = {
            '아이디': self.id,
            '이름': self.name
        }
        
        # 추가 데이터 병합
        sheet_data.update(self.additional_data)
        
        return sheet_data
    
    def update_activity(self, command_executed: bool = True) -> None:
        """
        사용자 활동 업데이트
        
        Args:
            command_executed: 명령어 실행 여부
        """
        self.last_active = self._get_current_time()
        if command_executed:
            self.command_count += 1
    
    def is_valid(self) -> bool:
        """
        사용자 데이터 유효성 검사
        
        Returns:
            bool: 유효성 여부
        """
        return bool(self.id and self.id.strip() and self.name and self.name.strip())
    
    def get_display_name(self) -> str:
        """
        표시용 이름 반환 (이름이 없으면 ID 사용)
        
        Returns:
            str: 표시용 이름
        """
        return self.name if self.name else self.id
    
    def get_activity_summary(self) -> Dict[str, Any]:
        """
        사용자 활동 요약 정보 반환
        
        Returns:
            Dict: 활동 요약
        """
        now = self._get_current_time()
        
        # 마지막 활동으로부터 경과 시간 계산
        if self.last_active:
            inactive_duration = now - self.last_active
            inactive_days = inactive_duration.days
            inactive_hours = inactive_duration.seconds // 3600
        else:
            inactive_days = None
            inactive_hours = None
        
        # 등록 후 경과 시간 계산
        if self.created_at:
            member_duration = now - self.created_at
            member_days = member_duration.days
        else:
            member_days = None
        
        return {
            'user_id': self.id,
            'user_name': self.name,
            'command_count': self.command_count,
            'member_days': member_days,
            'inactive_days': inactive_days,
            'inactive_hours': inactive_hours,
            'last_active': self.last_active.strftime('%Y-%m-%d %H:%M:%S') if self.last_active else None
        }
    
    def has_additional_data(self, key: str) -> bool:
        """
        특정 추가 데이터 보유 여부 확인
        
        Args:
            key: 확인할 데이터 키
            
        Returns:
            bool: 보유 여부
        """
        return key in self.additional_data and self.additional_data[key]
    
    def get_additional_data(self, key: str, default: Any = None) -> Any:
        """
        추가 데이터 조회
        
        Args:
            key: 데이터 키
            default: 기본값
            
        Returns:
            Any: 데이터 값
        """
        return self.additional_data.get(key, default)
    
    def set_additional_data(self, key: str, value: Any) -> None:
        """
        추가 데이터 설정
        
        Args:
            key: 데이터 키
            value: 설정할 값
        """
        self.additional_data[key] = value
    
    @staticmethod
    def _get_current_time() -> datetime:
        """현재 KST 시간 반환"""
        return datetime.now(pytz.timezone('Asia/Seoul'))
    
    @staticmethod
    def _parse_datetime(datetime_str: str) -> Optional[datetime]:
        """
        문자열을 datetime 객체로 파싱
        
        Args:
            datetime_str: datetime 문자열
            
        Returns:
            Optional[datetime]: 파싱된 datetime 또는 None
        """
        try:
            # ISO 형식 파싱 시도
            if 'T' in datetime_str:
                return datetime.fromisoformat(datetime_str.replace('Z', '+00:00'))
            else:
                # 일반적인 형식 파싱 시도
                return datetime.strptime(datetime_str, '%Y-%m-%d %H:%M:%S')
        except (ValueError, TypeError):
            return None
    
    def __str__(self) -> str:
        """문자열 표현 - 사용자 ID만 반환"""
        return self.id
    
    def __repr__(self) -> str:
        """개발자용 문자열 표현"""
        return (f"User(id='{self.id}', name='{self.name}', "
                f"command_count={self.command_count}, "
                f"last_active={self.last_active})")
    
    def get_info_string(self) -> str:
        """상세 정보 문자열 반환 (기존 __str__ 기능)"""
        return f"User(id='{self.id}', name='{self.name}', commands={self.command_count})"

@dataclass
class UserStats:
    """사용자 통계 정보"""
    
    total_users: int = 0
    active_users_today: int = 0
    active_users_week: int = 0
    total_commands: int = 0
    most_active_user: Optional[str] = None
    most_active_commands: int = 0
    newest_user: Optional[str] = None
    
    @classmethod
    def from_users(cls, users: List[User]) -> 'UserStats':
        """
        사용자 리스트에서 통계 생성
        
        Args:
            users: 사용자 리스트
            
        Returns:
            UserStats: 통계 객체
        """
        if not users:
            return cls()
        
        now = datetime.now(pytz.timezone('Asia/Seoul'))
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = today_start - timedelta(days=7)
        
        active_today = 0
        active_week = 0
        total_commands = 0
        most_active_user = None
        most_active_commands = 0
        newest_user = None
        newest_time = None
        
        for user in users:
            # 총 명령어 수 누적
            total_commands += user.command_count
            
            # 가장 활발한 사용자 찾기
            if user.command_count > most_active_commands:
                most_active_commands = user.command_count
                most_active_user = user.name
            
            # 가장 최근 사용자 찾기
            if user.created_at and (newest_time is None or user.created_at > newest_time):
                newest_time = user.created_at
                newest_user = user.name
            
            # 활성 사용자 카운트
            if user.last_active:
                if user.last_active >= today_start:
                    active_today += 1
                if user.last_active >= week_start:
                    active_week += 1
        
        return cls(
            total_users=len(users),
            active_users_today=active_today,
            active_users_week=active_week,
            total_commands=total_commands,
            most_active_user=most_active_user,
            most_active_commands=most_active_commands,
            newest_user=newest_user
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리로 변환"""
        return {
            'total_users': self.total_users,
            'active_users_today': self.active_users_today,
            'active_users_week': self.active_users_week,
            'total_commands': self.total_commands,
            'most_active_user': self.most_active_user,
            'most_active_commands': self.most_active_commands,
            'newest_user': self.newest_user
        }
    
    def get_summary_text(self) -> str:
        """
        요약 텍스트 반환
        
        Returns:
            str: 통계 요약 텍스트
        """
        lines = [
            f"📊 사용자 통계",
            f"총 사용자: {self.total_users}명",
            f"오늘 활성: {self.active_users_today}명",
            f"주간 활성: {self.active_users_week}명",
            f"총 명령어: {self.total_commands:,}회"
        ]
        
        if self.most_active_user:
            lines.append(f"최고 활성: {self.most_active_user} ({self.most_active_commands:,}회)")
        
        if self.newest_user:
            lines.append(f"최신 사용자: {self.newest_user}")
        
        return "\n".join(lines)


class UserManager:
    """사용자 관리 클래스"""
    
    def __init__(self):
        """UserManager 초기화"""
        self._users_cache: Dict[str, User] = {}
        self._cache_timestamp = None  # time.time() UNIX epoch (TTL 비교용, KST datetime 아님)
        self._cache_ttl = getattr(config, 'CACHE_TTL', 1800)  # config.CACHE_TTL 사용 (기본값: 30분)
        self._sheets_manager = None
    
    def create_user_from_sheet_data(self, data: Dict[str, Any]) -> User:
        """
        시트 데이터에서 사용자 생성
        
        Args:
            data: 시트 행 데이터
            
        Returns:
            User: 생성된 사용자 객체
            
        Raises:
            UserValidationError: 데이터 검증 실패 시
        """
        return User.from_sheet_data(data)
    
    def validate_user_data(self, user_id: str, user_data: Dict[str, Any]) -> bool:
        """
        사용자 데이터 유효성 검사
        
        Args:
            user_id: 사용자 ID
            user_data: 사용자 데이터
            
        Returns:
            bool: 유효성 여부
        """
        try:
            user = User.from_sheet_data(user_data)
            return user.is_valid() and user.id == user_id
        except (UserValidationError, Exception):
            return False

    def set_sheets_manager(self, sheets_manager):
        """
        SheetsManager 설정

        Args:
            sheets_manager: SheetsManager 인스턴스
        """
        self._sheets_manager = sheets_manager

    def preload_user_data(self) -> bool:
        """
        봇 시작 시 사용자 데이터를 미리 로드하여 캐싱

        Returns:
            bool: 로드 성공 여부
        """
        try:
            if not self._sheets_manager:
                logger.warning("SheetsManager가 설정되지 않아 사용자 데이터 사전 로드를 건너뜁니다.")
                return False

            logger.info("🔄 사용자 명단 데이터 사전 로드 시작...")

            # config 임포트를 지연시켜 순환 임포트 방지
            try:
                from config.settings import config
                roster_sheet_name = config.get_worksheet_name('ROSTER') if hasattr(config, 'get_worksheet_name') else '명단'
            except ImportError:
                roster_sheet_name = '명단'  # 기본값

            # 시트에서 사용자 데이터 가져오기
            user_data = self._sheets_manager.get_worksheet_data(roster_sheet_name)

            if not user_data:
                logger.warning("명단 시트에 데이터가 없습니다.")
                return False

            # 사용자 데이터 파싱 및 캐싱
            loaded_count = 0
            error_count = 0

            for row_data in user_data:
                try:
                    if not isinstance(row_data, dict):
                        continue

                    user = self.create_user_from_sheet_data(row_data)
                    if user and user.is_valid():
                        self._users_cache[user.id] = user
                        loaded_count += 1

                except Exception as e:
                    error_count += 1
                    logger.debug(f"사용자 데이터 파싱 실패: {e}")
                    continue

            # 캐시 타임스탬프 갱신
            self._cache_timestamp = time.time()

            logger.info(f"✅ 사용자 데이터 사전 로드 완료: {loaded_count}명 로드, {error_count}개 오류")
            return True

        except Exception as e:
            logger.error(f"❌ 사용자 데이터 사전 로드 실패: {e}")
            return False
    
    def get_user_display_info(self, user: User) -> Dict[str, str]:
        """
        사용자 표시 정보 반환
        
        Args:
            user: User 객체
            
        Returns:
            Dict: 표시용 정보
        """
        return {
            'id': user.id,
            'name': user.get_display_name(),
            'command_count': f"{user.command_count:,}",
            'last_active': user.last_active.strftime('%Y-%m-%d %H:%M') if user.last_active else '없음'
        }
    
    def create_user_stats(self, users: List[User]) -> UserStats:
        """
        사용자 통계 생성
        
        Args:
            users: 사용자 리스트
            
        Returns:
            UserStats: 통계 객체
        """
        return UserStats.from_users(users)


# 편의 함수들
def create_user_from_sheet(data: Dict[str, Any]) -> User:
    """시트 데이터에서 사용자 생성 (편의 함수)"""
    return User.from_sheet_data(data)


def validate_user_id(user_id: str) -> bool:
    """
    사용자 ID 형식 검증
    
    Args:
        user_id: 검증할 사용자 ID
        
    Returns:
        bool: 유효성 여부
    """
    if not user_id or not isinstance(user_id, str):
        return False
    
    user_id = user_id.strip()
    
    # 기본 검증: 비어있지 않고, 특수문자 제한
    if not user_id or len(user_id) < 1:
        return False
    
    # 마스토돈 사용자명 형식 검증 (선택사항)
    # @ 제거 후 검증
    if user_id.startswith('@'):
        user_id = user_id[1:]
    
    return len(user_id) > 0


def create_empty_user(user_id: str) -> User:
    """
    빈 사용자 객체 생성 (등록되지 않은 사용자용)
    
    Args:
        user_id: 사용자 ID
        
    Returns:
        User: 빈 사용자 객체
    """
    return User(id=user_id, name="", command_count=0)


# 전역 사용자 관리자 인스턴스
user_manager = UserManager()
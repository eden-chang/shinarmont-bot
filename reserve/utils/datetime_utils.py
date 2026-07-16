"""
마스토돈 예약 봇 날짜/시간 유틸리티
다양한 형식의 날짜/시간 파싱 및 변환을 처리합니다.
"""

import os
import sys
import re
from datetime import datetime, date, time, timedelta
from typing import Optional, Tuple, Union, List
import pytz
from dateutil import parser as dateutil_parser

# 프로젝트 루트 경로 설정
current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(current_dir)

try:
    from config.settings import config
    from utils.logging_config import get_logger
except ImportError:
    # config가 없는 경우 기본 설정 사용
    class DefaultConfig:
        TIMEZONE = pytz.timezone('Asia/Seoul')
    config = DefaultConfig()
    
    import logging
    logging.basicConfig(level=logging.INFO)
    get_logger = logging.getLogger

logger = get_logger(__name__)


class DateTimeParser:
    """
    다양한 형식의 날짜/시간 문자열을 파싱하는 클래스
    
    지원하는 날짜 형식:
    - 8/1, 08/01, 8/01, 08/1
    - 2025/8/1, 2025/08/01
    - 8-1, 08-01, 8.1, 08.01
    
    지원하는 시간 형식:
    - 14:00, 14:0, 2:1, 02:01
    - 14시, 14시00분, 오후 2시
    """
    
    def __init__(self, default_timezone: Optional[pytz.BaseTzInfo] = None):
        """
        DateTimeParser 초기화
        
        Args:
            default_timezone: 기본 시간대 (None이면 설정에서 가져옴)
        """
        self.timezone = default_timezone or getattr(config, 'TIMEZONE', pytz.timezone('Asia/Seoul'))
        
        # 날짜 파싱 패턴들
        self.date_patterns = [
            # 기본 형식: M/D, MM/DD 등
            (r'^(\d{1,2})[/\-\.](\d{1,2})$', self._parse_month_day),
            # 연도 포함: YYYY/M/D, YYYY-MM-DD 등
            (r'^(\d{4})[/\-\.](\d{1,2})[/\-\.](\d{1,2})$', self._parse_year_month_day),
            # 한국식: 8월1일, 8월 1일
            (r'^(\d{1,2})월\s*(\d{1,2})일?$', self._parse_korean_month_day),
            # 오늘, 내일, 모레
            (r'^(오늘|내일|모레)$', self._parse_relative_day),
        ]
        
        # 시간 파싱 패턴들
        self.time_patterns = [
            # 기본 형식: H:M, HH:MM
            (r'^(\d{1,2}):(\d{1,2})$', self._parse_hour_minute),
            # 단축 형식: H시, H시M분
            (r'^(\d{1,2})시(?:(\d{1,2})분?)?$', self._parse_korean_time),
            # 오전/오후: 오후 2시, 오전 9시 30분
            (r'^(오전|오후)\s*(\d{1,2})시?(?:\s*(\d{1,2})분?)?$', self._parse_korean_ampm),
            # 24시간 표기: 1400, 0930
            (r'^(\d{2})(\d{2})$', self._parse_military_time),
        ]
    
    def parse_date_string(self, date_str: str, reference_date: Optional[date] = None) -> Optional[date]:
        """
        날짜 문자열을 파싱하여 date 객체로 변환
        
        Args:
            date_str: 파싱할 날짜 문자열
            reference_date: 기준 날짜 (년도가 없는 경우 사용)
        
        Returns:
            Optional[date]: 파싱된 날짜 객체, 실패시 None
        """
        if not date_str or not isinstance(date_str, str):
            return None
        
        # 공백 제거 및 정규화
        date_str = date_str.strip()
        
        if not date_str:
            return None
        
        # 기준 날짜 설정
        if reference_date is None:
            reference_date = self.get_current_date()
        
        # 각 패턴으로 시도
        for pattern, parser_func in self.date_patterns:
            match = re.match(pattern, date_str)
            if match:
                try:
                    parsed_date = parser_func(match, reference_date)
                    if parsed_date:
                        logger.debug(f"날짜 파싱 성공: '{date_str}' -> {parsed_date}")
                        return parsed_date
                except Exception as e:
                    logger.debug(f"날짜 파싱 실패 (패턴 {pattern}): {e}")
                    continue
        
        # 마지막으로 dateutil 사용
        try:
            parsed_dt = dateutil_parser.parse(date_str, default=datetime.combine(reference_date, time()))
            result_date = parsed_dt.date()
            logger.debug(f"날짜 파싱 성공 (dateutil): '{date_str}' -> {result_date}")
            return result_date
        except Exception as e:
            logger.debug(f"날짜 파싱 실패 (dateutil): {e}")
        
        logger.warning(f"날짜 파싱 실패: '{date_str}'")
        return None
    
    def parse_time_string(self, time_str: str) -> Optional[time]:
        """
        시간 문자열을 파싱하여 time 객체로 변환
        
        Args:
            time_str: 파싱할 시간 문자열
        
        Returns:
            Optional[time]: 파싱된 시간 객체, 실패시 None
        """
        if not time_str or not isinstance(time_str, str):
            return None
        
        # 공백 제거 및 정규화
        time_str = time_str.strip()
        
        if not time_str:
            return None
        
        # 각 패턴으로 시도
        for pattern, parser_func in self.time_patterns:
            match = re.match(pattern, time_str)
            if match:
                try:
                    parsed_time = parser_func(match)
                    if parsed_time:
                        logger.debug(f"시간 파싱 성공: '{time_str}' -> {parsed_time}")
                        return parsed_time
                except Exception as e:
                    logger.debug(f"시간 파싱 실패 (패턴 {pattern}): {e}")
                    continue
        
        # 마지막으로 dateutil 사용
        try:
            parsed_dt = dateutil_parser.parse(time_str)
            result_time = parsed_dt.time()
            logger.debug(f"시간 파싱 성공 (dateutil): '{time_str}' -> {result_time}")
            return result_time
        except Exception as e:
            logger.debug(f"시간 파싱 실패 (dateutil): {e}")
        
        logger.warning(f"시간 파싱 실패: '{time_str}'")
        return None
    
    def parse_datetime_strings(self, date_str: str, time_str: str,
                             reference_date: Optional[date] = None) -> Optional[datetime]:
        """
        날짜와 시간 문자열을 조합하여 datetime 객체로 변환
        
        Args:
            date_str: 날짜 문자열
            time_str: 시간 문자열
            reference_date: 기준 날짜
        
        Returns:
            Optional[datetime]: 파싱된 datetime 객체, 실패시 None
        """
        parsed_date = self.parse_date_string(date_str, reference_date)
        parsed_time = self.parse_time_string(time_str)
        
        if parsed_date is None or parsed_time is None:
            return None
        
        try:
            # naive datetime 생성
            naive_dt = datetime.combine(parsed_date, parsed_time)
            
            # 시간대 정보 추가
            localized_dt = self.timezone.localize(naive_dt)
            
            logger.debug(f"DateTime 생성 성공: {date_str} {time_str} -> {localized_dt}")
            return localized_dt
            
        except Exception as e:
            logger.error(f"DateTime 생성 실패: {date_str} {time_str} - {e}")
            return None
    
    # === 날짜 파싱 메서드들 ===
    
    def _parse_month_day(self, match: re.Match, reference_date: date) -> Optional[date]:
        """M/D 형식 파싱"""
        month = int(match.group(1))
        day = int(match.group(2))
        year = reference_date.year
        
        # 월/일이 바뀌었을 수도 있으므로 검증
        if month > 12:
            month, day = day, month
        
        return self._create_date_safe(year, month, day, reference_date)
    
    def _parse_year_month_day(self, match: re.Match, reference_date: date) -> Optional[date]:
        """YYYY/M/D 형식 파싱"""
        year = int(match.group(1))
        month = int(match.group(2))
        day = int(match.group(3))
        
        return self._create_date_safe(year, month, day, reference_date)
    
    def _parse_korean_month_day(self, match: re.Match, reference_date: date) -> Optional[date]:
        """8월1일 형식 파싱"""
        month = int(match.group(1))
        day = int(match.group(2))
        year = reference_date.year
        
        return self._create_date_safe(year, month, day, reference_date)
    
    def _parse_relative_day(self, match: re.Match, reference_date: date) -> Optional[date]:
        """오늘/내일/모레 파싱"""
        relative_word = match.group(1)
        
        if relative_word == '오늘':
            return reference_date
        elif relative_word == '내일':
            return reference_date + timedelta(days=1)
        elif relative_word == '모레':
            return reference_date + timedelta(days=2)
        
        return None
    
    # === 시간 파싱 메서드들 ===
    
    def _parse_hour_minute(self, match: re.Match) -> Optional[time]:
        """H:M 형식 파싱"""
        hour = int(match.group(1))
        minute = int(match.group(2))
        
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return time(hour, minute)
        return None
    
    def _parse_korean_time(self, match: re.Match) -> Optional[time]:
        """H시M분 형식 파싱"""
        hour = int(match.group(1))
        minute_str = match.group(2)
        minute = int(minute_str) if minute_str else 0
        
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return time(hour, minute)
        return None
    
    def _parse_korean_ampm(self, match: re.Match) -> Optional[time]:
        """오전/오후 H시M분 형식 파싱"""
        ampm = match.group(1)
        hour = int(match.group(2))
        minute_str = match.group(3)
        minute = int(minute_str) if minute_str else 0
        
        # 12시간 -> 24시간 변환
        if ampm == '오후' and hour != 12:
            hour += 12
        elif ampm == '오전' and hour == 12:
            hour = 0
        
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return time(hour, minute)
        return None
    
    def _parse_military_time(self, match: re.Match) -> Optional[time]:
        """HHMM 형식 파싱"""
        hour = int(match.group(1))
        minute = int(match.group(2))
        
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return time(hour, minute)
        return None
    
    def _create_date_safe(self, year: int, month: int, day: int, reference_date: date) -> Optional[date]:
        """안전한 날짜 생성 (유효성 검사 포함)"""
        try:
            # 기본 검증
            if not (1 <= month <= 12):
                return None
            if not (1 <= day <= 31):
                return None
            
            # 실제 날짜 생성 시도
            new_date = date(year, month, day)
            
            # 과거 날짜인 경우 다음 해로 추정
            if new_date < reference_date and year == reference_date.year:
                new_date = date(year + 1, month, day)
            
            return new_date
            
        except ValueError:
            return None
    
    def get_current_datetime(self) -> datetime:
        """현재 시간을 설정된 시간대로 반환"""
        return datetime.now(self.timezone)
    
    def get_current_date(self) -> date:
        """현재 날짜를 설정된 시간대로 반환"""
        return self.get_current_datetime().date()
    
    def get_current_time(self) -> time:
        """현재 시간을 설정된 시간대로 반환"""
        return self.get_current_datetime().time()


class ScheduleValidator:
    """
    스케줄 유효성 검증 클래스
    """
    
    def __init__(self, timezone: Optional[pytz.BaseTzInfo] = None):
        self.timezone = timezone or getattr(config, 'TIMEZONE', pytz.timezone('Asia/Seoul'))
        self.parser = DateTimeParser(self.timezone)
    
    def validate_schedule_time(self, scheduled_dt: datetime, 
                             min_advance_minutes: int = 1) -> Tuple[bool, str]:
        """
        예약 시간의 유효성 검증
        
        Args:
            scheduled_dt: 검증할 예약 시간
            min_advance_minutes: 최소 사전 예약 시간 (분)
        
        Returns:
            Tuple[bool, str]: (유효 여부, 메시지)
        """
        current_dt = self.parser.get_current_datetime()
        
        # 과거 시간 체크
        if scheduled_dt <= current_dt:
            return False, f"과거 시간으로 예약할 수 없습니다. (현재: {current_dt.strftime('%Y-%m-%d %H:%M')}, 예약: {scheduled_dt.strftime('%Y-%m-%d %H:%M')})"
        
        # 최소 사전 예약 시간 체크
        min_advance_dt = current_dt + timedelta(minutes=min_advance_minutes)
        if scheduled_dt < min_advance_dt:
            return False, f"최소 {min_advance_minutes}분 이후로 예약해야 합니다."
        
        # 너무 먼 미래 체크 (1년 이후)
        max_future_dt = current_dt + timedelta(days=365)
        if scheduled_dt > max_future_dt:
            return False, "1년 이후로는 예약할 수 없습니다."
        
        return True, "유효한 예약 시간입니다."
    
    def is_business_hours(self, dt: datetime) -> bool:
        """업무 시간 여부 확인 (평일 9-18시)"""
        # 한국 시간대로 변환
        if dt.tzinfo is None:
            dt = self.timezone.localize(dt)
        else:
            dt = dt.astimezone(self.timezone)
        
        # 평일 확인 (0=월요일, 6=일요일)
        if dt.weekday() >= 5:  # 토, 일
            return False
        
        # 시간 확인
        return 9 <= dt.hour < 18
    
    def get_next_business_hour(self, dt: datetime) -> datetime:
        """다음 업무 시간 반환"""
        if dt.tzinfo is None:
            dt = self.timezone.localize(dt)
        else:
            dt = dt.astimezone(self.timezone)
        
        # 현재가 업무 시간이면 그대로 반환
        if self.is_business_hours(dt):
            return dt
        
        # 다음 업무일 찾기
        current = dt.replace(hour=9, minute=0, second=0, microsecond=0)
        
        while not self.is_business_hours(current):
            current += timedelta(days=1)
            current = current.replace(hour=9, minute=0, second=0, microsecond=0)
        
        return current


def format_datetime_korean(dt: datetime) -> str:
    """datetime을 한국어 형식으로 포맷팅"""
    if dt.tzinfo is None:
        tz = getattr(config, 'TIMEZONE', pytz.timezone('Asia/Seoul'))
        dt = tz.localize(dt)
    else:
        tz = getattr(config, 'TIMEZONE', pytz.timezone('Asia/Seoul'))
        dt = dt.astimezone(tz)
    
    weekdays = ['월', '화', '수', '목', '금', '토', '일']
    weekday = weekdays[dt.weekday()]
    
    return f"{dt.year}년 {dt.month}월 {dt.day}일 ({weekday}) {dt.hour:02d}시 {dt.minute:02d}분"


def format_time_until(target_dt: datetime, current_dt: Optional[datetime] = None) -> str:
    """대상 시간까지 남은 시간을 한국어로 포맷팅"""
    if current_dt is None:
        tz = getattr(config, 'TIMEZONE', pytz.timezone('Asia/Seoul'))
        current_dt = datetime.now(tz)
    
    # 시간대 통일
    if target_dt.tzinfo is None:
        tz = getattr(config, 'TIMEZONE', pytz.timezone('Asia/Seoul'))
        target_dt = tz.localize(target_dt)
    
    if current_dt.tzinfo is None:
        tz = getattr(config, 'TIMEZONE', pytz.timezone('Asia/Seoul'))
        current_dt = tz.localize(current_dt)
    
    # 차이 계산
    delta = target_dt - current_dt
    
    if delta.total_seconds() < 0:
        return "이미 지난 시간"
    
    total_seconds = int(delta.total_seconds())
    days = total_seconds // 86400
    hours = (total_seconds % 86400) // 3600
    minutes = (total_seconds % 3600) // 60
    
    parts = []
    if days > 0:
        parts.append(f"{days}일")
    if hours > 0:
        parts.append(f"{hours}시간")
    if minutes > 0:
        parts.append(f"{minutes}분")
    
    if not parts:
        return "곧"
    
    return " ".join(parts) + " 후"


def get_schedule_sync_times(interval_minutes: int = 20) -> List[time]:
    """
    스케줄 동기화 시간 목록 반환 (0분, 20분, 40분)
    
    Args:
        interval_minutes: 동기화 간격 (분)
    
    Returns:
        List[time]: 동기화 시간 목록
    """
    sync_times = []
    
    for hour in range(24):
        minute = 0
        while minute < 60:
            sync_times.append(time(hour, minute))
            minute += interval_minutes
    
    return sync_times


def is_sync_time(current_time: time, interval_minutes: int = 20) -> bool:
    """현재 시간이 동기화 시간인지 확인"""
    sync_times = get_schedule_sync_times(interval_minutes)
    
    # 정확한 시간 매칭 (초는 무시)
    current_hm = time(current_time.hour, current_time.minute)
    return current_hm in sync_times


def get_next_sync_time(current_dt: Optional[datetime] = None, 
                      interval_minutes: int = 20) -> datetime:
    """다음 동기화 시간 반환"""
    if current_dt is None:
        tz = getattr(config, 'TIMEZONE', pytz.timezone('Asia/Seoul'))
        current_dt = datetime.now(tz)
    
    # 현재 시간에서 다음 동기화 시간 찾기
    current_minutes = current_dt.minute
    
    # 다음 동기화 분 계산
    next_sync_minute = ((current_minutes // interval_minutes) + 1) * interval_minutes
    
    if next_sync_minute >= 60:
        # 다음 시간의 0분
        next_sync_dt = current_dt.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    else:
        # 같은 시간의 다음 동기화 분
        next_sync_dt = current_dt.replace(minute=next_sync_minute, second=0, microsecond=0)
    
    return next_sync_dt


# 전역 인스턴스들
default_parser = DateTimeParser()
default_validator = ScheduleValidator()


# 편의 함수들
def parse_date(date_str: str, reference_date: Optional[date] = None) -> Optional[date]:
    """날짜 문자열 파싱 (전역 함수)"""
    return default_parser.parse_date_string(date_str, reference_date)


def parse_time(time_str: str) -> Optional[time]:
    """시간 문자열 파싱 (전역 함수)"""
    return default_parser.parse_time_string(time_str)


def parse_datetime(date_str: str, time_str: str, 
                  reference_date: Optional[date] = None) -> Optional[datetime]:
    """날짜/시간 문자열 파싱 (전역 함수)"""
    return default_parser.parse_datetime_strings(date_str, time_str, reference_date)


def validate_schedule(scheduled_dt: datetime, min_advance_minutes: int = 1) -> Tuple[bool, str]:
    """예약 시간 검증 (전역 함수)"""
    return default_validator.validate_schedule_time(scheduled_dt, min_advance_minutes)


if __name__ == "__main__":
    """날짜/시간 유틸리티 테스트"""
    print("🧪 날짜/시간 유틸리티 테스트 시작...")
    
    parser = DateTimeParser()
    validator = ScheduleValidator()
    
    # 날짜 파싱 테스트
    date_tests = [
        "8/1", "08/01", "8/01", "08/1",
        "2025/8/1", "2025/08/01",
        "8-1", "08.01",
        "8월1일", "8월 1일",
        "오늘", "내일", "모레"
    ]
    
    print("\n📅 날짜 파싱 테스트:")
    for date_str in date_tests:
        result = parser.parse_date_string(date_str)
        print(f"  '{date_str}' -> {result}")
    
    # 시간 파싱 테스트
    time_tests = [
        "14:00", "14:0", "2:1", "02:01",
        "14시", "14시30분", "2시",
        "오후 2시", "오전 9시 30분",
        "1400", "0930"
    ]
    
    print("\n⏰ 시간 파싱 테스트:")
    for time_str in time_tests:
        result = parser.parse_time_string(time_str)
        print(f"  '{time_str}' -> {result}")
    
    # DateTime 조합 테스트
    print("\n🗓️ DateTime 조합 테스트:")
    combined_tests = [
        ("8/1", "14:00"),
        ("내일", "오후 2시"),
        ("08/01", "14시30분")
    ]
    
    for date_str, time_str in combined_tests:
        result = parser.parse_datetime_strings(date_str, time_str)
        if result:
            korean_format = format_datetime_korean(result)
            time_until = format_time_until(result)
            print(f"  '{date_str} {time_str}' -> {korean_format} ({time_until})")
    
    # 스케줄 검증 테스트
    print("\n✅ 스케줄 검증 테스트:")
    test_dt = parser.get_current_datetime() + timedelta(hours=1)
    is_valid, message = validator.validate_schedule_time(test_dt)
    print(f"  1시간 후 예약: {is_valid} - {message}")
    
    past_dt = parser.get_current_datetime() - timedelta(hours=1)
    is_valid, message = validator.validate_schedule_time(past_dt)
    print(f"  1시간 전 예약: {is_valid} - {message}")
    
    # 동기화 시간 테스트
    print("\n🔄 동기화 시간 테스트:")
    current_time = parser.get_current_time()
    is_sync = is_sync_time(current_time, 20)
    next_sync = get_next_sync_time()
    print(f"  현재 동기화 시간 여부: {is_sync}")
    print(f"  다음 동기화 시간: {format_datetime_korean(next_sync)}")
    
    print("\n✅ 날짜/시간 유틸리티 테스트 완료!")
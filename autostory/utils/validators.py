"""
마스토돈 예약 봇 데이터 검증 모듈
다양한 데이터 유형의 유효성을 검증하고 정규화합니다.
"""

import os
import sys
import re
import json
from datetime import datetime, date, time, timedelta
from typing import Dict, List, Optional, Tuple, Any, Union
from pathlib import Path
import pytz
from urllib.parse import urlparse

# 프로젝트 루트 경로 설정
current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(current_dir)

try:
    from config.settings import config
    from utils.logging_config import get_logger
    from utils.datetime_utils import parse_date, parse_time, parse_datetime, validate_schedule
except ImportError as e:
    print(f"❌ 필수 모듈 임포트 실패: {e}")
    sys.exit(1)

logger = get_logger(__name__)


class ValidationResult:
    """
    검증 결과를 나타내는 클래스
    """
    
    def __init__(self, is_valid: bool = True, error_message: str = "", 
                 warnings: Optional[List[str]] = None, 
                 normalized_value: Any = None):
        """
        ValidationResult 초기화
        
        Args:
            is_valid: 유효성 여부
            error_message: 오류 메시지
            warnings: 경고 메시지 목록
            normalized_value: 정규화된 값
        """
        self.is_valid = is_valid
        self.error_message = error_message
        self.warnings = warnings or []
        self.normalized_value = normalized_value
    
    def add_warning(self, warning: str) -> None:
        """경고 메시지 추가"""
        self.warnings.append(warning)
    
    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리로 변환"""
        return {
            'is_valid': self.is_valid,
            'error_message': self.error_message,
            'warnings': self.warnings,
            'normalized_value': self.normalized_value
        }
    
    def __bool__(self) -> bool:
        """불린 값으로 사용 시 유효성 반환"""
        return self.is_valid
    
    def __str__(self) -> str:
        """문자열 표현"""
        if self.is_valid:
            warning_str = f" (경고: {len(self.warnings)}개)" if self.warnings else ""
            return f"✅ 유효{warning_str}"
        else:
            return f"❌ 무효: {self.error_message}"


class TootContentValidator:
    """
    툿 내용 검증 클래스
    마스토돈 게시물 내용의 유효성을 검증합니다.
    """
    
    # 마스토돈 기본 제한
    MAX_TOOT_LENGTH = 430  # 마스토돈 기본 글자 수 제한
    MAX_LINE_COUNT = 50    # 최대 줄 수 (과도한 줄바꿈 방지)
    
    def __init__(self, max_length: Optional[int] = None):
        """
        TootContentValidator 초기화
        
        Args:
            max_length: 최대 글자 수 (None이면 기본값 사용)
        """
        self.max_length = max_length or self.MAX_TOOT_LENGTH
        
        # 금지된 패턴들
        self.forbidden_patterns = [
        ]
        
        # 의심스러운 패턴들 (경고)
        self.suspicious_patterns = [
        ]
    
    def validate(self, content: str) -> ValidationResult:
        """
        툿 내용 검증
        
        Args:
            content: 검증할 툿 내용
        
        Returns:
            ValidationResult: 검증 결과
        """
        if not isinstance(content, str):
            return ValidationResult(False, "툿 내용이 문자열이 아닙니다")
        
        # 기본 정규화
        normalized_content = self._normalize_content(content)
        result = ValidationResult(normalized_value=normalized_content)
        
        # 1. 빈 내용 검사
        if not normalized_content.strip():
            return ValidationResult(False, "툿 내용이 비어있습니다")
        
        # 2. 길이 검사
        if len(normalized_content) > self.max_length:
            return ValidationResult(
                False, 
                f"툿 내용이 너무 깁니다 ({len(normalized_content)}자 > {self.max_length}자)"
            )
        
        # 3. 줄 수 검사
        line_count = normalized_content.count('\n') + 1
        if line_count > self.MAX_LINE_COUNT:
            return ValidationResult(
                False,
                f"툿 내용의 줄 수가 너무 많습니다 ({line_count}줄 > {self.MAX_LINE_COUNT}줄)"
            )
        
        # 4. 금지된 패턴 검사
        for pattern, description in self.forbidden_patterns:
            if re.search(pattern, normalized_content):
                return ValidationResult(False, f"금지된 내용 감지: {description}")
        
        # 5. 의심스러운 패턴 검사 (경고)
        for pattern, description in self.suspicious_patterns:
            if re.search(pattern, normalized_content):
                result.add_warning(f"의심스러운 패턴: {description}")
        
        # 6. URL 검사
        url_validation = self._validate_urls(normalized_content)
        if not url_validation.is_valid:
            return ValidationResult(False, url_validation.error_message)
        result.warnings.extend(url_validation.warnings)
        
        # 7. 멘션 검사
        mention_validation = self._validate_mentions(normalized_content)
        result.warnings.extend(mention_validation.warnings)
        
        # 8. 해시태그 검사
        hashtag_validation = self._validate_hashtags(normalized_content)
        result.warnings.extend(hashtag_validation.warnings)
        
        return result
    
    def _normalize_content(self, content: str) -> str:
        """툿 내용 정규화"""
        # 앞뒤 공백 제거
        content = content.strip()
        
        # 연속된 줄바꿈을 최대 2개로 제한
        content = re.sub(r'\n{3,}', '\n\n', content)
        
        # 탭을 공백으로 변환
        content = content.replace('\t', ' ')
        
        # 연속된 공백을 하나로 통합 (줄바꿈은 제외)
        content = re.sub(r'[ ]+', ' ', content)
        
        return content
    
    def _validate_urls(self, content: str) -> ValidationResult:
        """URL 검증"""
        result = ValidationResult()
        
        # URL 패턴 찾기
        url_pattern = r'https?://[^\s]+'
        urls = re.findall(url_pattern, content)
        
        if len(urls) > 5:
            return ValidationResult(False, f"URL이 너무 많습니다 ({len(urls)}개 > 5개)")
        
        for url in urls:
            try:
                parsed = urlparse(url)
                if not parsed.netloc:
                    result.add_warning(f"유효하지 않은 URL 형식: {url[:50]}...")
                elif parsed.netloc in ['bit.ly', 'tinyurl.com', 't.co']:
                    result.add_warning(f"단축 URL 사용: {url}")
            except Exception:
                result.add_warning(f"URL 파싱 실패: {url[:50]}...")
        
        return result
    
    def _validate_mentions(self, content: str) -> ValidationResult:
        """멘션 검증"""
        result = ValidationResult()
        
        # 마스토돈 멘션 패턴 (@username@instance.com 또는 @username)
        mention_pattern = r'@\w+(?:@[\w\.-]+)?'
        mentions = re.findall(mention_pattern, content)
        
        if len(mentions) > 10:
            result.add_warning(f"멘션이 많습니다 ({len(mentions)}개)")
        
        # 자신에 대한 멘션 확인
        for mention in mentions:
            if mention.lower() in ['@bot', '@자동', '@자동봇']:
                result.add_warning("봇에 대한 멘션이 포함되어 있습니다")
        
        return result
    
    def _validate_hashtags(self, content: str) -> ValidationResult:
        """해시태그 검증"""
        result = ValidationResult()
        
        # 해시태그 패턴
        hashtag_pattern = r'#[\w가-힣]+'
        hashtags = re.findall(hashtag_pattern, content)
        
        if len(hashtags) > 20:
            result.add_warning(f"해시태그가 많습니다 ({len(hashtags)}개)")
        
        # 너무 긴 해시태그 확인
        for hashtag in hashtags:
            if len(hashtag) > 50:
                result.add_warning(f"해시태그가 너무 깁니다: {hashtag[:30]}...")
        
        return result


class DateTimeValidator:
    """
    날짜/시간 검증 클래스
    """
    
    def __init__(self, timezone: Optional[pytz.BaseTzInfo] = None):
        """
        DateTimeValidator 초기화
        
        Args:
            timezone: 기본 시간대
        """
        self.timezone = timezone or getattr(config, 'TIMEZONE', pytz.timezone('Asia/Seoul'))
    
    def validate_date_string(self, date_str: str) -> ValidationResult:
        """
        날짜 문자열 검증
        
        Args:
            date_str: 검증할 날짜 문자열
        
        Returns:
            ValidationResult: 검증 결과
        """
        if not isinstance(date_str, str):
            return ValidationResult(False, "날짜가 문자열이 아닙니다")
        
        date_str = date_str.strip()
        if not date_str:
            return ValidationResult(False, "날짜가 비어있습니다")
        
        # 날짜 파싱 시도
        parsed_date = parse_date(date_str)
        
        if parsed_date is None:
            return ValidationResult(False, f"날짜 형식을 인식할 수 없습니다: '{date_str}'")
        
        result = ValidationResult(normalized_value=parsed_date)
        
        # 과거 날짜 확인
        today = datetime.now(self.timezone).date()
        if parsed_date < today:
            # 1일 이전이면 경고, 7일 이전이면 오류
            days_ago = (today - parsed_date).days
            if days_ago > 7:
                return ValidationResult(False, f"날짜가 너무 과거입니다 ({days_ago}일 전)")
            else:
                result.add_warning(f"과거 날짜입니다 ({days_ago}일 전)")
        
        # 너무 먼 미래 확인
        max_future = today + timedelta(days=365)
        if parsed_date > max_future:
            return ValidationResult(False, "1년 이후 날짜는 사용할 수 없습니다")
        
        return result
    
    def validate_time_string(self, time_str: str) -> ValidationResult:
        """
        시간 문자열 검증
        
        Args:
            time_str: 검증할 시간 문자열
        
        Returns:
            ValidationResult: 검증 결과
        """
        if not isinstance(time_str, str):
            return ValidationResult(False, "시간이 문자열이 아닙니다")
        
        time_str = time_str.strip()
        if not time_str:
            return ValidationResult(False, "시간이 비어있습니다")
        
        # 시간 파싱 시도
        parsed_time = parse_time(time_str)
        
        if parsed_time is None:
            return ValidationResult(False, f"시간 형식을 인식할 수 없습니다: '{time_str}'")
        
        result = ValidationResult(normalized_value=parsed_time)
        
        # 업무 시간 외 경고
        if parsed_time.hour < 6 or parsed_time.hour >= 23:
            result.add_warning(f"업무 시간 외입니다 ({parsed_time.hour:02d}시)")
        
        return result
    
    def validate_datetime_combination(self, date_str: str, time_str: str) -> ValidationResult:
        """
        날짜와 시간 조합 검증
        
        Args:
            date_str: 날짜 문자열
            time_str: 시간 문자열
        
        Returns:
            ValidationResult: 검증 결과
        """
        # 개별 검증
        date_result = self.validate_date_string(date_str)
        if not date_result.is_valid:
            return ValidationResult(False, f"날짜 오류: {date_result.error_message}")
        
        time_result = self.validate_time_string(time_str)
        if not time_result.is_valid:
            return ValidationResult(False, f"시간 오류: {time_result.error_message}")
        
        # 조합 파싱
        parsed_datetime = parse_datetime(date_str, time_str)
        
        if parsed_datetime is None:
            return ValidationResult(False, "날짜/시간 조합을 파싱할 수 없습니다")
        
        result = ValidationResult(normalized_value=parsed_datetime)
        
        # 경고 병합
        result.warnings.extend(date_result.warnings)
        result.warnings.extend(time_result.warnings)
        
        # 예약 시간 검증
        schedule_valid, schedule_message = validate_schedule(parsed_datetime)
        if not schedule_valid:
            return ValidationResult(False, f"예약 시간 오류: {schedule_message}")
        
        return result


class ConfigValidator:
    """
    설정 값 검증 클래스
    """
    
    @staticmethod
    def validate_url(url: str, require_https: bool = True) -> ValidationResult:
        """
        URL 검증
        
        Args:
            url: 검증할 URL
            require_https: HTTPS 필수 여부
        
        Returns:
            ValidationResult: 검증 결과
        """
        if not isinstance(url, str):
            return ValidationResult(False, "URL이 문자열이 아닙니다")
        
        url = url.strip()
        if not url:
            return ValidationResult(False, "URL이 비어있습니다")
        
        try:
            parsed = urlparse(url)
            
            if not parsed.scheme:
                return ValidationResult(False, "URL 스키마가 없습니다 (http:// 또는 https://)")
            
            if not parsed.netloc:
                return ValidationResult(False, "URL 도메인이 없습니다")
            
            if require_https and parsed.scheme != 'https':
                return ValidationResult(False, "HTTPS URL이 필요합니다")
            
            # 일반적인 마스토돈 인스턴스 패턴 확인
            result = ValidationResult(normalized_value=url)
            
            if not re.match(r'^[a-zA-Z0-9.-]+$', parsed.netloc):
                result.add_warning("도메인 형식이 일반적이지 않습니다")
            
            return result
            
        except Exception as e:
            return ValidationResult(False, f"URL 파싱 오류: {e}")
    
    @staticmethod
    def validate_access_token(token: str) -> ValidationResult:
        """
        액세스 토큰 검증
        
        Args:
            token: 검증할 토큰
        
        Returns:
            ValidationResult: 검증 결과
        """
        if not isinstance(token, str):
            return ValidationResult(False, "토큰이 문자열이 아닙니다")
        
        token = token.strip()
        if not token:
            return ValidationResult(False, "토큰이 비어있습니다")
        
        # 마스토돈 토큰은 보통 64자 이상의 영숫자+기호
        if len(token) < 20:
            return ValidationResult(False, "토큰이 너무 짧습니다")
        
        if len(token) > 200:
            return ValidationResult(False, "토큰이 너무 깁니다")
        
        # 안전한 문자만 포함하는지 확인
        if not re.match(r'^[a-zA-Z0-9_-]+$', token):
            return ValidationResult(False, "토큰에 유효하지 않은 문자가 포함되어 있습니다")
        
        return ValidationResult(normalized_value=token)
    
    @staticmethod
    def validate_sheets_id(sheets_id: str) -> ValidationResult:
        """
        Google Sheets ID 검증
        
        Args:
            sheets_id: 검증할 시트 ID
        
        Returns:
            ValidationResult: 검증 결과
        """
        if not isinstance(sheets_id, str):
            return ValidationResult(False, "시트 ID가 문자열이 아닙니다")
        
        sheets_id = sheets_id.strip()
        if not sheets_id:
            return ValidationResult(False, "시트 ID가 비어있습니다")
        
        # Google Sheets ID는 보통 44자의 영숫자+기호
        if len(sheets_id) < 20:
            return ValidationResult(False, "시트 ID가 너무 짧습니다")
        
        if len(sheets_id) > 100:
            return ValidationResult(False, "시트 ID가 너무 깁니다")
        
        # 기본적인 형식 확인
        if not re.match(r'^[a-zA-Z0-9_-]+$', sheets_id):
            return ValidationResult(False, "시트 ID 형식이 유효하지 않습니다")
        
        return ValidationResult(normalized_value=sheets_id)
    
    @staticmethod
    def validate_integer_range(value: Any, min_value: int, max_value: int, name: str) -> ValidationResult:
        """
        정수 범위 검증
        
        Args:
            value: 검증할 값
            min_value: 최소값
            max_value: 최대값
            name: 값의 이름 (오류 메시지용)
        
        Returns:
            ValidationResult: 검증 결과
        """
        try:
            int_value = int(value)
        except (ValueError, TypeError):
            return ValidationResult(False, f"{name}이(가) 정수가 아닙니다: {value}")
        
        if int_value < min_value:
            return ValidationResult(False, f"{name}이(가) 너무 작습니다: {int_value} < {min_value}")
        
        if int_value > max_value:
            return ValidationResult(False, f"{name}이(가) 너무 큽니다: {int_value} > {max_value}")
        
        return ValidationResult(normalized_value=int_value)


class FileValidator:
    """
    파일 관련 검증 클래스
    """
    
    @staticmethod
    def validate_credentials_file(file_path: Union[str, Path]) -> ValidationResult:
        """
        Google 인증 파일 검증
        
        Args:
            file_path: 인증 파일 경로
        
        Returns:
            ValidationResult: 검증 결과
        """
        file_path = Path(file_path)
        
        if not file_path.exists():
            return ValidationResult(False, f"인증 파일을 찾을 수 없습니다: {file_path}")
        
        if not file_path.is_file():
            return ValidationResult(False, f"인증 파일이 파일이 아닙니다: {file_path}")
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                credentials_data = json.load(f)
            
            # 필수 필드 확인
            required_fields = ['type', 'project_id', 'private_key_id', 'private_key', 'client_email']
            missing_fields = [field for field in required_fields if field not in credentials_data]
            
            if missing_fields:
                return ValidationResult(False, f"인증 파일에 필수 필드가 없습니다: {missing_fields}")
            
            # 서비스 계정 타입 확인
            if credentials_data.get('type') != 'service_account':
                return ValidationResult(False, "서비스 계정 인증 파일이 아닙니다")
            
            result = ValidationResult(normalized_value=file_path)
            
            # 경고 사항 확인
            if file_path.stat().st_mode & 0o077:
                result.add_warning("인증 파일의 권한이 너무 관대합니다 (보안 위험)")
            
            return result
            
        except json.JSONDecodeError:
            return ValidationResult(False, "인증 파일이 유효한 JSON이 아닙니다")
        except Exception as e:
            return ValidationResult(False, f"인증 파일 검증 중 오류: {e}")
    
    @staticmethod
    def validate_directory_writable(dir_path: Union[str, Path]) -> ValidationResult:
        """
        디렉토리 쓰기 가능 여부 검증
        
        Args:
            dir_path: 디렉토리 경로
        
        Returns:
            ValidationResult: 검증 결과
        """
        dir_path = Path(dir_path)
        
        if not dir_path.exists():
            try:
                dir_path.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                return ValidationResult(False, f"디렉토리 생성 실패: {e}")
        
        if not dir_path.is_dir():
            return ValidationResult(False, f"경로가 디렉토리가 아닙니다: {dir_path}")
        
        # 쓰기 권한 테스트
        test_file = dir_path / '.write_test'
        try:
            test_file.write_text('test')
            test_file.unlink()
        except Exception as e:
            return ValidationResult(False, f"디렉토리에 쓰기 권한이 없습니다: {e}")
        
        return ValidationResult(normalized_value=dir_path)


# 통합 검증 함수들

def validate_toot_content(content: str) -> ValidationResult:
    """툿 내용 검증 (전역 함수)"""
    validator = TootContentValidator()
    return validator.validate(content)


def validate_schedule_data(date_str: str, time_str: str, content: str) -> ValidationResult:
    """예약 데이터 전체 검증 (전역 함수)"""
    # 개별 검증
    datetime_validator = DateTimeValidator()
    content_validator = TootContentValidator()
    
    # 날짜/시간 검증
    datetime_result = datetime_validator.validate_datetime_combination(date_str, time_str)
    if not datetime_result.is_valid:
        return ValidationResult(False, f"날짜/시간 오류: {datetime_result.error_message}")
    
    # 내용 검증
    content_result = content_validator.validate(content)
    if not content_result.is_valid:
        return ValidationResult(False, f"내용 오류: {content_result.error_message}")
    
    # 성공 시 통합 결과
    result = ValidationResult(normalized_value={
        'scheduled_datetime': datetime_result.normalized_value,
        'content': content_result.normalized_value
    })
    
    # 경고 병합
    result.warnings.extend(datetime_result.warnings)
    result.warnings.extend(content_result.warnings)
    
    return result


def validate_startup_config() -> ValidationResult:
    """시작 시 설정 검증 (전역 함수)"""
    errors = []
    warnings = []
    
    # URL 검증
    url_result = ConfigValidator.validate_url(config.MASTODON_INSTANCE_URL)
    if not url_result.is_valid:
        errors.append(f"마스토돈 URL: {url_result.error_message}")
    warnings.extend(url_result.warnings)
    
    # 토큰 검증
    token_result = ConfigValidator.validate_access_token(config.MASTODON_ACCESS_TOKEN)
    if not token_result.is_valid:
        errors.append(f"액세스 토큰: {token_result.error_message}")
    
    # 시트 ID 검증
    sheets_result = ConfigValidator.validate_sheets_id(config.GOOGLE_SHEETS_ID)
    if not sheets_result.is_valid:
        errors.append(f"시트 ID: {sheets_result.error_message}")
    
    # 인증 파일 검증
    cred_result = FileValidator.validate_credentials_file(config.get_credentials_path())
    if not cred_result.is_valid:
        errors.append(f"인증 파일: {cred_result.error_message}")
    warnings.extend(cred_result.warnings)
    
    # 디렉토리 검증
    for dir_name, dir_path in [
        ('캐시', config.CACHE_DIR),
        ('로그', config.LOG_DIR),
        ('백업', config.BACKUP_DIR)
    ]:
        dir_result = FileValidator.validate_directory_writable(dir_path)
        if not dir_result.is_valid:
            errors.append(f"{dir_name} 디렉토리: {dir_result.error_message}")
    
    # 숫자 설정 검증
    interval_result = ConfigValidator.validate_integer_range(
        config.SYNC_INTERVAL_MINUTES, 1, 60, "동기화 간격"
    )
    if not interval_result.is_valid:
        errors.append(interval_result.error_message)
    
    max_rows_result = ConfigValidator.validate_integer_range(
        config.MAX_ROWS_PER_REQUEST, 10, 1000, "최대 조회 행수"
    )
    if not max_rows_result.is_valid:
        errors.append(max_rows_result.error_message)
    
    # 결과 생성
    if errors:
        return ValidationResult(False, "\n".join(errors))
    
    result = ValidationResult()
    result.warnings = warnings
    return result


if __name__ == "__main__":
    """데이터 검증 모듈 테스트"""
    print("🧪 데이터 검증 모듈 테스트 시작...")
    
    # 툿 내용 검증 테스트
    print("\n📝 툿 내용 검증 테스트:")
    content_tests = [
        "안녕하세요! 좋은 하루 되세요. 😊",
        "이것은 매우 긴 텍스트입니다. " * 50,  # 너무 긴 내용
        "",  # 빈 내용
        "스팸 광고입니다!",  # 금지된 내용
        "링크: https://suspicious-site.com/click-here",  # 의심스러운 링크
        "정상적인 내용\n여러 줄로\n작성된 툿입니다.",  # 멀티라인
    ]
    
    for content in content_tests:
        result = validate_toot_content(content)
        print(f"  '{content[:30]}...' -> {result}")
        if result.warnings:
            for warning in result.warnings:
                print(f"    ⚠️ {warning}")
    
    # 날짜/시간 검증 테스트
    print("\n📅 날짜/시간 검증 테스트:")
    datetime_tests = [
        ("8/1", "14:00"),      # 정상
        ("내일", "9:30"),      # 상대적 날짜
        ("13/30", "25:70"),    # 잘못된 형식
        ("2020/1/1", "12:00"), # 과거 날짜
        ("", "14:00"),         # 빈 날짜
        ("8/1", ""),           # 빈 시간
    ]
    
    datetime_validator = DateTimeValidator()
    for date_str, time_str in datetime_tests:
        result = datetime_validator.validate_datetime_combination(date_str, time_str)
        print(f"  '{date_str}' + '{time_str}' -> {result}")
        if result.warnings:
            for warning in result.warnings:
                print(f"    ⚠️ {warning}")
    
    # 예약 데이터 통합 검증 테스트
    print("\n🗓️ 예약 데이터 통합 검증:")
    schedule_tests = [
        ("내일", "14:00", "안녕하세요! 좋은 하루 되세요."),
        ("8/1", "25:00", "시간이 잘못된 툿"),
        ("내일", "14:00", ""),  # 빈 내용
        ("", "", "내용만 있는 툿"),
    ]
    
    for date_str, time_str, content in schedule_tests:
        result = validate_schedule_data(date_str, time_str, content)
        print(f"  '{date_str}' '{time_str}' '{content[:20]}...' -> {result}")
        if result.warnings:
            for warning in result.warnings:
                print(f"    ⚠️ {warning}")
    
    # 설정 검증 테스트
    print("\n⚙️ 설정 검증 테스트:")
    
    # URL 검증
    url_tests = [
        "https://mastodon.social",
        "http://mastodon.local",
        "not-a-url",
        "ftp://example.com",
        "",
    ]
    
    for url in url_tests:
        result = ConfigValidator.validate_url(url)
        print(f"  URL '{url}' -> {result}")
    
    # 토큰 검증
    token_tests = [
        "valid_token_with_64_chars_abcdefghijklmnopqrstuvwxyz1234567890",
        "short",
        "",
        "invalid-chars!@#$%",
    ]
    
    for token in token_tests:
        result = ConfigValidator.validate_access_token(token)
        print(f"  토큰 '{token[:20]}...' -> {result}")
    
    # 정수 범위 검증
    range_tests = [
        (20, "동기화 간격"),
        (0, "동기화 간격"),
        (100, "동기화 간격"),
        ("not_a_number", "동기화 간격"),
    ]
    
    for value, name in range_tests:
        result = ConfigValidator.validate_integer_range(value, 1, 60, name)
        print(f"  {name} '{value}' -> {result}")
    
    # 파일 검증 (실제 파일 필요)
    print("\n📁 파일 검증 테스트:")
    
    # 임시 디렉토리 테스트
    import tempfile
    with tempfile.TemporaryDirectory() as temp_dir:
        result = FileValidator.validate_directory_writable(temp_dir)
        print(f"  임시 디렉토리 '{temp_dir}' -> {result}")
    
    # 존재하지 않는 디렉토리
    result = FileValidator.validate_directory_writable("/nonexistent/path")
    print(f"  존재하지 않는 디렉토리 -> {result}")
    
    # 전체 시작 설정 검증 (실제 설정 필요)
    print("\n🚀 전체 설정 검증:")
    try:
        result = validate_startup_config()
        print(f"  전체 설정 -> {result}")
        if result.warnings:
            for warning in result.warnings:
                print(f"    ⚠️ {warning}")
    except Exception as e:
        print(f"  전체 설정 검증 실패: {e}")
    
    print("\n✅ 데이터 검증 모듈 테스트 완료!")
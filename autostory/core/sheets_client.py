"""
마스토돈 예약 봇 Google Sheets 클라이언트
Google Sheets API를 통해 예약 툿 데이터를 조회하고 관리합니다.
"""

import os
import sys
import json
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
from pathlib import Path

# Google API 라이브러리
from google.auth.transport.requests import Request
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import google.auth.exceptions

# 프로젝트 루트 경로 설정
current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(current_dir)

try:
    from config.settings import config
    from utils.logging_config import get_logger, log_api_call, log_performance
    from utils.datetime_utils import parse_datetime, validate_schedule, format_datetime_korean
except ImportError as e:
    print(f"❌ 필수 모듈 임포트 실패: {e}")
    sys.exit(1)

logger = get_logger(__name__)

# Google Sheets API 설정
SCOPES = ['https://www.googleapis.com/auth/spreadsheets.readonly']


class SheetsRateLimiter:
    """
    Google Sheets API 호출 제한 관리 클래스
    API 제한을 준수하여 안전한 호출을 보장합니다.
    """
    
    def __init__(self, max_requests_per_100_seconds: int = 100):
        """
        SheetsRateLimiter 초기화
        
        Args:
            max_requests_per_100_seconds: 100초당 최대 요청 수
        """
        self.max_requests = max_requests_per_100_seconds
        self.requests = []  # (timestamp, request_info) 튜플들
        self.last_request_time = 0
        self.min_interval = 1.0  # 최소 요청 간격 (초)
    
    def wait_if_needed(self) -> None:
        """필요시 대기하여 API 제한 준수"""
        current_time = time.time()
        
        # 100초 이전 요청들 제거
        cutoff_time = current_time - 100
        self.requests = [(ts, info) for ts, info in self.requests if ts > cutoff_time]
        
        # 요청 수 제한 체크
        if len(self.requests) >= self.max_requests:
            oldest_request_time = self.requests[0][0]
            wait_time = oldest_request_time + 100 - current_time + 1
            if wait_time > 0:
                logger.warning(f"API 요청 제한으로 {wait_time:.1f}초 대기 중...")
                time.sleep(wait_time)
        
        # 최소 간격 체크
        time_since_last = current_time - self.last_request_time
        if time_since_last < self.min_interval:
            wait_time = self.min_interval - time_since_last
            time.sleep(wait_time)
        
        # 현재 요청 기록
        self.last_request_time = time.time()
        self.requests.append((self.last_request_time, "API call"))
    
    def get_status(self) -> Dict[str, Any]:
        """현재 상태 반환"""
        current_time = time.time()
        cutoff_time = current_time - 100
        recent_requests = [req for req in self.requests if req[0] > cutoff_time]
        
        return {
            'recent_requests_count': len(recent_requests),
            'max_requests': self.max_requests,
            'requests_remaining': self.max_requests - len(recent_requests),
            'last_request_time': self.last_request_time,
            'time_since_last_request': current_time - self.last_request_time
        }


class TootData:
    """
    툿 데이터를 나타내는 클래스
    """
    
    def __init__(self, row_index: int, date_str: str, time_str: str, account: str, content: str):
        """
        TootData 초기화
        
        Args:
            row_index: 시트에서의 행 번호 (1부터 시작)
            date_str: 날짜 문자열
            time_str: 시간 문자열
            account: 계정 이름
            content: 툿 내용
        """
        self.row_index = row_index
        self.date_str = date_str.strip() if date_str else ""
        self.time_str = time_str.strip() if time_str else ""
        # 계정 이름 정규화 (대소문자 구분 없음)
        from config.settings import config
        if account:
            normalized_account = config.get_normalized_account_name(account.strip())
            self.account = normalized_account if normalized_account else account.strip().upper()
        else:
            self.account = ""
        self.content = content.strip() if content else ""
        
        # 파싱된 datetime (지연 로딩)
        self._parsed_datetime = None
        self._parse_error = None
    
    @property
    def scheduled_datetime(self) -> Optional[datetime]:
        """예약 시간 반환 (파싱 결과 캐싱)"""
        if self._parsed_datetime is None and self._parse_error is None:
            try:
                self._parsed_datetime = parse_datetime(self.date_str, self.time_str)
                if self._parsed_datetime is None:
                    self._parse_error = f"날짜/시간 파싱 실패: '{self.date_str}' '{self.time_str}'"
            except Exception as e:
                self._parse_error = f"날짜/시간 파싱 오류: {e}"
        
        return self._parsed_datetime
    
    @property
    def is_valid(self) -> bool:
        """유효한 툿 데이터인지 확인"""
        return (
            bool(self.date_str) and
            bool(self.time_str) and
            bool(self.account) and
            bool(self.content) and
            self.scheduled_datetime is not None and
            self.is_account_valid()
        )
    
    def is_account_valid(self) -> bool:
        """계정 이름이 유효한지 확인"""
        from config.settings import config
        return config.is_valid_account(self.account)
    
    @property
    def validation_error(self) -> Optional[str]:
        """검증 오류 메시지 반환"""
        if not self.date_str:
            return "날짜가 없습니다"
        if not self.time_str:
            return "시간이 없습니다"
        if not self.account:
            return "계정이 없습니다"
        if not self.is_account_valid():
            return f"유효하지 않은 계정: {self.account}"
        if not self.content:
            return "내용이 없습니다"
        if self._parse_error:
            return self._parse_error
        if self.scheduled_datetime is None:
            return "날짜/시간 파싱 실패"
        return None
    
    def is_future(self, reference_time: Optional[datetime] = None) -> bool:
        """미래 시간인지 확인"""
        if not self.scheduled_datetime:
            return False
        
        if reference_time is None:
            from utils.datetime_utils import default_parser
            reference_time = default_parser.get_current_datetime()
        
        return self.scheduled_datetime > reference_time
    
    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리로 변환"""
        return {
            'row_index': self.row_index,
            'date_str': self.date_str,
            'time_str': self.time_str,
            'account': self.account,
            'content': self.content,
            'scheduled_datetime': self.scheduled_datetime.isoformat() if self.scheduled_datetime else None,
            'is_valid': self.is_valid,
            'validation_error': self.validation_error
        }
    
    def __str__(self) -> str:
        """문자열 표현"""
        if self.scheduled_datetime:
            formatted_time = format_datetime_korean(self.scheduled_datetime)
            return f"[행{self.row_index}] {self.account}: {formatted_time}: {self.content[:50]}..."
        else:
            return f"[행{self.row_index}] {self.account}: {self.date_str} {self.time_str}: {self.content[:50]}... (파싱 실패)"


class StoryScriptData:
    """
    스토리 스크립트 데이터를 나타내는 클래스
    """

    def __init__(self, row_index: int, account: str, interval: int, script: str, image_filename: str = ""):
        """
        StoryScriptData 초기화

        Args:
            row_index: 시트에서의 행 번호 (1부터 시작)
            account: 계정 이름
            interval: 간격 (초 단위)
            script: 스크립트 문구
            image_filename: 이미지 파일명 (선택사항)
        """
        self.row_index = row_index
        # 계정 이름 정규화 (대소문자 구분 없음)
        from config.settings import config
        if account:
            normalized_account = config.get_normalized_account_name(account.strip())
            self.account = normalized_account if normalized_account else account.strip().upper()
        else:
            self.account = ""
        self.interval = interval if isinstance(interval, int) else self._parse_interval(interval)
        self.script = script.strip() if script else ""
        self.image_filename = image_filename.strip() if image_filename else ""
    
    def _parse_interval(self, interval_str: str) -> int:
        """간격 문자열을 정수로 파싱"""
        try:
            return int(str(interval_str).strip())
        except (ValueError, AttributeError):
            return 0
    
    @property
    def is_valid(self) -> bool:
        """유효한 스크립트 데이터인지 확인"""
        return (
            bool(self.account) and
            self.interval > 0 and
            bool(self.script) and
            self.is_account_valid()
        )
    
    def is_account_valid(self) -> bool:
        """계정 이름이 유효한지 확인"""
        from config.settings import config
        return config.is_valid_account(self.account)
    
    @property
    def validation_error(self) -> Optional[str]:
        """검증 오류 메시지 반환"""
        if not self.account:
            return "계정이 없습니다"
        if not self.is_account_valid():
            return f"유효하지 않은 계정: {self.account}"
        if self.interval <= 0:
            return f"유효하지 않은 간격: {self.interval}"
        if not self.script:
            return "스크립트 문구가 없습니다"
        return None
    
    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리로 변환"""
        return {
            'row_index': self.row_index,
            'account': self.account,
            'interval': self.interval,
            'script': self.script,
            'image_filename': self.image_filename,
            'is_valid': self.is_valid,
            'validation_error': self.validation_error
        }

    def __str__(self) -> str:
        """문자열 표현"""
        image_info = f" [이미지: {self.image_filename}]" if self.image_filename else ""
        return f"[행{self.row_index}] {self.account}: {self.interval}초마다 '{self.script[:50]}...'{image_info}"


class GoogleSheetsClient:
    """
    Google Sheets API 클라이언트 클래스
    스토리 스크립트 데이터를 조회하고 관리합니다.
    """
    
    def __init__(self, credentials_path: Optional[Path] = None,
                 sheets_id: Optional[str] = None,
                 tab_name: Optional[str] = None):
        """
        GoogleSheetsClient 초기화
        
        Args:
            credentials_path: Google 서비스 계정 인증 파일 경로
            sheets_id: Google Sheets 문서 ID
            tab_name: 시트 탭 이름
        """
        # 설정 로드
        self.credentials_path = credentials_path or config.get_credentials_path()
        self.sheets_id = sheets_id or config.GOOGLE_SHEETS_ID
        self.tab_name = tab_name or config.GOOGLE_SHEETS_TAB
        self.max_rows_per_request = getattr(config, 'MAX_ROWS_PER_REQUEST', 100)
        
        # API 클라이언트
        self.service = None
        self.rate_limiter = SheetsRateLimiter()
        
        # 헤더 정보 캐시
        self._header_info = None
        self._header_cache_time = None
        self._header_cache_duration = 3600  # 1시간
        
        # 데이터 캐시
        self._last_fetch_time = None
        self._cached_data = []
        self._cache_validity_minutes = 5  # 캐시 유효 시간
        
        # 통계
        self.stats = {
            'total_api_calls': 0,
            'successful_calls': 0,
            'failed_calls': 0,
            'total_rows_fetched': 0,
            'last_error': None
        }
        
        logger.info(f"Google Sheets 클라이언트 초기화: {self.sheets_id[:20]}... / {self.tab_name}")
    
    @log_performance
    def authenticate(self) -> bool:
        """
        Google Sheets API 인증
        
        Returns:
            bool: 인증 성공 여부
        """
        try:
            logger.info("Google Sheets API 인증 시작...")
            
            # 인증 파일 존재 확인
            if not self.credentials_path.exists():
                logger.error(f"인증 파일을 찾을 수 없습니다: {self.credentials_path}")
                return False
            
            # 서비스 계정 인증
            credentials = Credentials.from_service_account_file(
                str(self.credentials_path),
                scopes=SCOPES
            )
            
            # API 서비스 빌드
            self.service = build('sheets', 'v4', credentials=credentials)
            
            # 연결 테스트
            test_result = self._test_connection()
            if test_result:
                logger.info("✅ Google Sheets API 인증 성공")
                return True
            else:
                logger.error("❌ Google Sheets 연결 테스트 실패")
                return False
            
        except FileNotFoundError:
            logger.error(f"인증 파일을 찾을 수 없습니다: {self.credentials_path}")
            return False
        except json.JSONDecodeError:
            logger.error("인증 파일 형식이 올바르지 않습니다")
            return False
        except google.auth.exceptions.GoogleAuthError as e:
            logger.error(f"Google 인증 오류: {e}")
            return False
        except Exception as e:
            logger.error(f"인증 중 예상치 못한 오류: {e}")
            return False
    
    @log_api_call
    def _detect_header_columns(self) -> Dict[str, Any]:
        """
        헤더 행을 읽어서 날짜/시간/문구 열의 위치를 자동 감지
        
        Returns:
            Dict[str, Any]: 헤더 정보 (컬럼 인덱스, 검증 결과 등)
        """
        try:
            # 캐시 확인
            current_time = time.time()
            if (self._header_info and self._header_cache_time and
                current_time - self._header_cache_time < self._header_cache_duration):
                return self._header_info
            
            logger.info("헤더 열 위치 자동 감지 시작...")
            
            # 첫 번째 행 전체 읽기 (A1부터 최대 Z1까지)
            header_range = f"{self.tab_name}!A1:Z1"
            self.rate_limiter.wait_if_needed()
            
            result = self.service.spreadsheets().values().get(
                spreadsheetId=self.sheets_id,
                range=header_range
            ).execute()
            
            self.stats['total_api_calls'] += 1
            self.stats['successful_calls'] += 1
            
            headers = result.get('values', [[]])[0] if result.get('values') else []
            
            # 헤더 정보 초기화
            header_info = {
                'date_col': None,      # 날짜 열 인덱스 (0부터 시작)
                'time_col': None,      # 시간 열 인덱스
                'account_col': None,   # 계정 열 인덱스
                'content_col': None,   # 내용 열 인덱스
                'date_letter': None,   # 날짜 열 문자 (A, B, C...)
                'time_letter': None,   # 시간 열 문자
                'account_letter': None, # 계정 열 문자
                'content_letter': None, # 내용 열 문자
                'headers': headers,    # 전체 헤더 목록
                'errors': [],
                'warnings': []
            }
            
            # 헤더 키워드 매핑 (우선순위 순)
            date_keywords = ['날짜', 'date', '일자', '일시', 'when']
            time_keywords = ['시간', 'time', '시각', '타임', 'hour']
            account_keywords = ['계정', 'account', '사용자', '아이디', 'user', 'id']
            content_keywords = ['문구', '내용', 'content', '툿', 'toot', '메시지', 'message', '텍스트', 'text']
            
            # 스토리 스크립트용 키워드 추가
            interval_keywords = ['간격', 'interval', '주기', '텀', '시간간격']
            
            # 각 열 검사
            for col_idx, header in enumerate(headers):
                if not header:  # 빈 헤더 건너뛰기
                    continue
                
                header_lower = header.lower().strip()
                col_letter = chr(65 + col_idx)  # A, B, C...
                
                # 날짜 열 찾기
                if header_info['date_col'] is None:
                    for keyword in date_keywords:
                        if keyword.lower() in header_lower:
                            header_info['date_col'] = col_idx
                            header_info['date_letter'] = col_letter
                            logger.debug(f"날짜 열 발견: {col_letter}열 '{header}'")
                            break
                
                # 시간 열 찾기
                if header_info['time_col'] is None:
                    for keyword in time_keywords:
                        if keyword.lower() in header_lower:
                            header_info['time_col'] = col_idx
                            header_info['time_letter'] = col_letter
                            logger.debug(f"시간 열 발견: {col_letter}열 '{header}'")
                            break
                
                # 계정 열 찾기
                if header_info['account_col'] is None:
                    for keyword in account_keywords:
                        if keyword.lower() in header_lower:
                            header_info['account_col'] = col_idx
                            header_info['account_letter'] = col_letter
                            logger.debug(f"계정 열 발견: {col_letter}열 '{header}'")
                            break
                
                # 내용 열 찾기
                if header_info['content_col'] is None:
                    for keyword in content_keywords:
                        if keyword.lower() in header_lower:
                            header_info['content_col'] = col_idx
                            header_info['content_letter'] = col_letter
                            logger.debug(f"내용 열 발견: {col_letter}열 '{header}'")
                            break
            
            # 검증
            missing_cols = []
            if header_info['date_col'] is None:
                missing_cols.append('날짜')
                header_info['errors'].append("날짜 열을 찾을 수 없습니다. 가능한 키워드: " + ", ".join(date_keywords))
            
            if header_info['time_col'] is None:
                missing_cols.append('시간')
                header_info['errors'].append("시간 열을 찾을 수 없습니다. 가능한 키워드: " + ", ".join(time_keywords))
            
            if header_info['account_col'] is None:
                missing_cols.append('계정')
                header_info['errors'].append("계정 열을 찾을 수 없습니다. 가능한 키워드: " + ", ".join(account_keywords))
            
            if header_info['content_col'] is None:
                missing_cols.append('내용')
                header_info['errors'].append("내용 열을 찾을 수 없습니다. 가능한 키워드: " + ", ".join(content_keywords))
            
            # 결과 로깅
            if not header_info['errors']:
                logger.info(f"✅ 헤더 열 감지 완료:")
                logger.info(f"   - 날짜: {header_info['date_letter']}열 '{headers[header_info['date_col']]}'")
                logger.info(f"   - 시간: {header_info['time_letter']}열 '{headers[header_info['time_col']]}'")
                logger.info(f"   - 계정: {header_info['account_letter']}열 '{headers[header_info['account_col']]}'")
                logger.info(f"   - 내용: {header_info['content_letter']}열 '{headers[header_info['content_col']]}'")
            else:
                logger.error("❌ 헤더 열 감지 실패:")
                for error in header_info['errors']:
                    logger.error(f"   - {error}")
                logger.info(f"발견된 헤더: {headers}")
            
            # 캐시 저장
            self._header_info = header_info
            self._header_cache_time = current_time
            
            return header_info
            
        except Exception as e:
            logger.error(f"헤더 열 감지 중 오류: {e}")
            self.stats['total_api_calls'] += 1
            self.stats['failed_calls'] += 1
            self.stats['last_error'] = str(e)
            return {
                'date_col': None,
                'time_col': None,
                'content_col': None,
                'errors': [f"헤더 감지 오류: {e}"],
                'warnings': []
            }
    def _test_connection(self) -> bool:
        """연결 테스트"""
        try:
            self.rate_limiter.wait_if_needed()
            
            # 시트 메타데이터 조회
            sheet_metadata = self.service.spreadsheets().get(
                spreadsheetId=self.sheets_id
            ).execute()
            
            # 탭 존재 확인
            sheets = sheet_metadata.get('sheets', [])
            tab_names = [sheet['properties']['title'] for sheet in sheets]
            
            if self.tab_name not in tab_names:
                logger.error(f"탭 '{self.tab_name}'을 찾을 수 없습니다. 사용 가능한 탭: {tab_names}")
                return False
            
            self.stats['total_api_calls'] += 1
            self.stats['successful_calls'] += 1
            
            logger.info(f"시트 연결 성공: {len(tab_names)}개 탭 발견")
            return True
            
        except HttpError as e:
            logger.error(f"HTTP 오류: {e}")
            self.stats['total_api_calls'] += 1
            self.stats['failed_calls'] += 1
            self.stats['last_error'] = str(e)
            return False
        except Exception as e:
            logger.error(f"연결 테스트 오류: {e}")
            self.stats['total_api_calls'] += 1
            self.stats['failed_calls'] += 1
            self.stats['last_error'] = str(e)
            return False
    
    @log_api_call
    @log_performance
    def fetch_toot_data(self, start_row: int = 2, max_rows: Optional[int] = None,
                       force_refresh: bool = False) -> List[TootData]:
        """
        시트에서 툿 데이터 조회
        
        Args:
            start_row: 시작 행 번호 (1부터 시작, 보통 2부터 - 헤더 제외)
            max_rows: 최대 조회 행 수
            force_refresh: 캐시 무시하고 강제로 새로 조회
        
        Returns:
            List[TootData]: 조회된 툿 데이터 목록
        """
        # 캐시 확인
        if not force_refresh and self._is_cache_valid():
            logger.debug("캐시된 데이터 사용")
            return self._cached_data
        
        if not self.service:
            if not self.authenticate():
                logger.error("인증 실패로 데이터 조회 불가")
                return []
        
        if max_rows is None:
            max_rows = self.max_rows_per_request
        
        try:
            logger.info(f"툿 데이터 조회 시작: 행 {start_row}부터 최대 {max_rows}개")
            
            # 헤더 정보 감지
            header_info = self._detect_header_columns()
            if header_info['errors']:
                logger.error("헤더 열 감지 실패로 데이터 조회 중단")
                return []
            
            # 동적 범위 계산
            end_row = start_row + max_rows - 1
            
            # 필요한 열만 조회 (date_col, time_col, account_col, content_col)
            cols_needed = [header_info['date_col'], header_info['time_col'], header_info['account_col'], header_info['content_col']]
            start_col_letter = chr(65 + min(cols_needed))  # 가장 앞 열
            end_col_letter = chr(65 + max(cols_needed))    # 가장 뒤 열
            
            range_name = f"{self.tab_name}!{start_col_letter}{start_row}:{end_col_letter}{end_row}"
            
            logger.debug(f"조회 범위: {range_name}")
            
            self.rate_limiter.wait_if_needed()
            
            # API 호출
            result = self.service.spreadsheets().values().get(
                spreadsheetId=self.sheets_id,
                range=range_name
            ).execute()
            
            self.stats['total_api_calls'] += 1
            self.stats['successful_calls'] += 1
            
            # 데이터 파싱
            values = result.get('values', [])
            toot_data_list = []
            
            for i, row in enumerate(values):
                row_index = start_row + i
                
                # 행 데이터를 충분히 확장 (부족한 열은 빈 문자열로 채움)
                max_col_idx = max(cols_needed)
                while len(row) <= max_col_idx:
                    row.append('')
                
                # 헤더 정보를 기반으로 데이터 추출
                date_str = row[header_info['date_col'] - min(cols_needed)] if header_info['date_col'] is not None else ""
                time_str = row[header_info['time_col'] - min(cols_needed)] if header_info['time_col'] is not None else ""
                account = row[header_info['account_col'] - min(cols_needed)] if header_info['account_col'] is not None else ""
                content = row[header_info['content_col'] - min(cols_needed)] if header_info['content_col'] is not None else ""
                
                # 빈 행 건너뛰기
                if not any([date_str.strip(), time_str.strip(), account.strip(), content.strip()]):
                    continue
                
                toot_data = TootData(row_index, date_str, time_str, account, content)
                toot_data_list.append(toot_data)
            
            self.stats['total_rows_fetched'] += len(toot_data_list)
            
            # 캐시 업데이트
            self._cached_data = toot_data_list
            self._last_fetch_time = datetime.now()
            
            logger.info(f"툿 데이터 조회 완료: {len(toot_data_list)}개 발견")
            
            # 유효성 검증 로그
            valid_count = sum(1 for toot in toot_data_list if toot.is_valid)
            invalid_count = len(toot_data_list) - valid_count
            
            if invalid_count > 0:
                logger.warning(f"유효하지 않은 데이터 {invalid_count}개 발견:")
                for toot in toot_data_list:
                    if not toot.is_valid:
                        logger.warning(f"  행 {toot.row_index}: {toot.validation_error}")
            
            logger.info(f"유효한 툿: {valid_count}개, 무효한 툿: {invalid_count}개")
            
            return toot_data_list
            
        except HttpError as e:
            logger.error(f"Google Sheets API 오류: {e}")
            self.stats['total_api_calls'] += 1
            self.stats['failed_calls'] += 1
            self.stats['last_error'] = str(e)
            return []
        except Exception as e:
            logger.error(f"데이터 조회 중 오류: {e}")
            self.stats['total_api_calls'] += 1
            self.stats['failed_calls'] += 1
            self.stats['last_error'] = str(e)
            return []
    
    def get_future_toots(self, reference_time: Optional[datetime] = None,
                        force_refresh: bool = False) -> List[TootData]:
        """
        미래에 예약된 툿만 조회
        
        Args:
            reference_time: 기준 시간 (None이면 현재 시간)
            force_refresh: 캐시 무시하고 강제 새로고침
        
        Returns:
            List[TootData]: 미래 예약 툿 목록 (시간순 정렬)
        """
        all_toots = self.fetch_toot_data(force_refresh=force_refresh)
        
        if reference_time is None:
            from utils.datetime_utils import default_parser
            reference_time = default_parser.get_current_datetime()
        
        # 유효하고 미래인 툿만 필터링
        future_toots = [
            toot for toot in all_toots
            if toot.is_valid and toot.is_future(reference_time)
        ]
        
        # 예약 시간순 정렬
        future_toots.sort(key=lambda t: t.scheduled_datetime)
        
        logger.info(f"미래 예약 툿 {len(future_toots)}개 조회 완료")
        
        return future_toots
    
    def find_next_scheduled_toot(self, reference_time: Optional[datetime] = None) -> Optional[TootData]:
        """
        다음에 예약된 툿 찾기
        
        Args:
            reference_time: 기준 시간
        
        Returns:
            Optional[TootData]: 다음 예약 툿, 없으면 None
        """
        future_toots = self.get_future_toots(reference_time)
        
        if future_toots:
            next_toot = future_toots[0]  # 이미 시간순 정렬됨
            logger.debug(f"다음 예약 툿: {next_toot}")
            return next_toot
        else:
            logger.debug("예약된 툿이 없습니다")
            return None
    
    def get_toots_due_soon(self, minutes_ahead: int = 5,
                          reference_time: Optional[datetime] = None) -> List[TootData]:
        """
        곧 예약 시간이 되는 툿들 조회
        
        Args:
            minutes_ahead: 몇 분 후까지 확인할지
            reference_time: 기준 시간
        
        Returns:
            List[TootData]: 곧 예약 시간이 되는 툿 목록
        """
        if reference_time is None:
            from utils.datetime_utils import default_parser
            reference_time = default_parser.get_current_datetime()
        
        cutoff_time = reference_time + timedelta(minutes=minutes_ahead)
        
        future_toots = self.get_future_toots(reference_time)
        
        due_soon = [
            toot for toot in future_toots
            if toot.scheduled_datetime <= cutoff_time
        ]
        
        logger.debug(f"{minutes_ahead}분 내 예약 툿 {len(due_soon)}개")
        
        return due_soon
    
    def get_worksheet_names(self) -> List[str]:
        """
        시트의 모든 워크시트 이름 조회
        
        Returns:
            List[str]: 워크시트 이름 목록
        """
        try:
            if not self.service:
                if not self.authenticate():
                    logger.error("인증 실패로 워크시트 목록 조회 불가")
                    return []
            
            self.rate_limiter.wait_if_needed()
            
            # 시트 메타데이터 조회
            sheet_metadata = self.service.spreadsheets().get(
                spreadsheetId=self.sheets_id
            ).execute()
            
            # 워크시트 이름 추출
            sheets = sheet_metadata.get('sheets', [])
            worksheet_names = [sheet['properties']['title'] for sheet in sheets]
            
            logger.info(f"워크시트 {len(worksheet_names)}개 발견: {worksheet_names}")
            return worksheet_names
            
        except Exception as e:
            logger.error(f"워크시트 목록 조회 실패: {e}")
            return []
    
    def fetch_story_scripts_from_worksheet(self, worksheet_name: str) -> List[StoryScriptData]:
        """
        특정 워크시트에서 스토리 스크립트 데이터 조회
        '계정', '간격', '문구' 열을 찾아서 데이터를 가져옵니다.
        
        Args:
            worksheet_name: 워크시트 이름
        
        Returns:
            List[StoryScriptData]: 스토리 스크립트 데이터 목록
        """
        try:
            if not self.service:
                if not self.authenticate():
                    logger.error("인증 실패로 스크립트 데이터 조회 불가")
                    return []
            
            logger.info(f"워크시트 '{worksheet_name}'에서 스토리 스크립트 조회 시작...")
            
            # 헤더 행 조회 (A1:Z1)
            header_range = f"{worksheet_name}!A1:Z1"
            self.rate_limiter.wait_if_needed()
            
            result = self.service.spreadsheets().values().get(
                spreadsheetId=self.sheets_id,
                range=header_range
            ).execute()
            
            headers = result.get('values', [[]])[0] if result.get('values') else []
            if not headers:
                logger.warning(f"워크시트 '{worksheet_name}'에서 헤더를 찾을 수 없습니다")
                return []
            
            # 필요한 열 찾기
            account_col = None
            interval_col = None
            script_col = None
            image_col = None  # 이미지 열 추가

            account_keywords = ['계정', 'account', '사용자', '아이디', 'user', 'id']
            interval_keywords = ['간격', 'interval', '주기', '텀', '시간간격']
            script_keywords = ['문구', '내용', 'content', '툿', 'toot', '메시지', 'message', '텍스트', 'text']
            image_keywords = ['이미지', 'image', '그림', 'picture', 'photo', '사진', 'img']

            for col_idx, header in enumerate(headers):
                if not header:
                    continue

                header_lower = header.lower().strip()

                # 계정 열 찾기
                if account_col is None:
                    for keyword in account_keywords:
                        if keyword.lower() in header_lower:
                            account_col = col_idx
                            break

                # 간격 열 찾기
                if interval_col is None:
                    for keyword in interval_keywords:
                        if keyword.lower() in header_lower:
                            interval_col = col_idx
                            break

                # 문구 열 찾기
                if script_col is None:
                    for keyword in script_keywords:
                        if keyword.lower() in header_lower:
                            script_col = col_idx
                            break

                # 이미지 열 찾기 (선택사항)
                if image_col is None:
                    for keyword in image_keywords:
                        if keyword.lower() in header_lower:
                            image_col = col_idx
                            break

            # 필수 열 확인
            if account_col is None or interval_col is None or script_col is None:
                missing = []
                if account_col is None: missing.append('계정')
                if interval_col is None: missing.append('간격')
                if script_col is None: missing.append('문구')
                logger.error(f"워크시트 '{worksheet_name}'에서 필수 열을 찾을 수 없습니다: {missing}")
                return []

            image_info = f", 이미지: {chr(65+image_col)}" if image_col is not None else " (이미지 열 없음)"
            logger.info(f"열 위치 - 계정: {chr(65+account_col)}, 간격: {chr(65+interval_col)}, 문구: {chr(65+script_col)}{image_info}")
            
            # 데이터 조회 (2행부터 끝까지)
            cols_needed = [account_col, interval_col, script_col]
            if image_col is not None:
                cols_needed.append(image_col)
            start_col = min(cols_needed)
            end_col = max(cols_needed)
            
            data_range = f"{worksheet_name}!{chr(65+start_col)}2:{chr(65+end_col)}1000"
            self.rate_limiter.wait_if_needed()
            
            result = self.service.spreadsheets().values().get(
                spreadsheetId=self.sheets_id,
                range=data_range
            ).execute()
            
            values = result.get('values', [])
            script_data_list = []
            
            for i, row in enumerate(values):
                if not row:  # 빈 행 건너뛰기
                    continue
                
                row_index = i + 2  # 2행부터 시작
                
                # 행 데이터를 충분히 확장
                while len(row) <= end_col - start_col:
                    row.append('')
                
                # 데이터 추출
                account = row[account_col - start_col] if account_col >= start_col else ""
                interval_str = row[interval_col - start_col] if interval_col >= start_col else ""
                script = row[script_col - start_col] if script_col >= start_col else ""
                image_filename = row[image_col - start_col] if image_col is not None and image_col >= start_col and len(row) > (image_col - start_col) else ""

                # 빈 행 건너뛰기
                if not any([account.strip(), interval_str.strip(), script.strip()]):
                    continue

                # 간격을 정수로 변환
                try:
                    interval = int(str(interval_str).strip()) if interval_str else 0
                except ValueError:
                    interval = 0

                script_data = StoryScriptData(row_index, account, interval, script, image_filename)
                script_data_list.append(script_data)
            
            logger.info(f"워크시트 '{worksheet_name}'에서 스크립트 {len(script_data_list)}개 조회 완료")
            
            # 유효성 검증 로그
            valid_count = sum(1 for script in script_data_list if script.is_valid)
            invalid_count = len(script_data_list) - valid_count
            
            if invalid_count > 0:
                logger.warning(f"유효하지 않은 스크립트 {invalid_count}개 발견:")
                for script in script_data_list:
                    if not script.is_valid:
                        logger.warning(f"  행 {script.row_index}: {script.validation_error}")
            
            return script_data_list
            
        except Exception as e:
            logger.error(f"워크시트 '{worksheet_name}' 스크립트 조회 실패: {e}")
            return []
    
    def validate_sheet_structure(self) -> Dict[str, Any]:
        """
        시트 구조 검증 (스토리 봇용)
        
        Returns:
            Dict[str, Any]: 검증 결과
        """
        logger.info("시트 구조 검증 시작...")
        
        result = {
            'valid': False,
            'errors': [],
            'warnings': [],
            'header_info': {},
            'sample_data': []
        }
        
        try:
            if not self.service:
                if not self.authenticate():
                    result['errors'].append("Google Sheets 인증 실패")
                    return result
            
            # 워크시트 목록 조회로 기본 검증
            worksheets = self.get_worksheet_names()
            if not worksheets:
                result['errors'].append("워크시트를 찾을 수 없습니다")
                return result
            
            logger.info(f"워크시트 {len(worksheets)}개 발견: {worksheets}")
            
            # 첫 번째 워크시트로 구조 검증
            first_worksheet = worksheets[0]
            sample_scripts = self.fetch_story_scripts_from_worksheet(first_worksheet)
            
            result['header_info'] = {
                'worksheets': worksheets,
                'sample_worksheet': first_worksheet,
                'sample_scripts_count': len(sample_scripts)
            }
            
            if sample_scripts:
                valid_scripts = [script for script in sample_scripts if script.is_valid]
                invalid_scripts = [script for script in sample_scripts if not script.is_valid]
                
                result['sample_data'] = [script.to_dict() for script in sample_scripts[:3]]  # 처음 3개만
                
                if invalid_scripts:
                    result['warnings'].extend([
                        f"워크시트 '{first_worksheet}' 행 {script.row_index}: {script.validation_error}"
                        for script in invalid_scripts[:5]  # 처음 5개 오류만
                    ])
                
                if valid_scripts:
                    result['valid'] = True
                    logger.info(f"✅ 시트 구조 검증 성공 - 유효한 스크립트 {len(valid_scripts)}개")
                else:
                    result['errors'].append(f"워크시트 '{first_worksheet}'에 유효한 스크립트가 없습니다")
            else:
                result['errors'].append(f"워크시트 '{first_worksheet}'에서 스크립트를 찾을 수 없습니다")
            
            return result
            
        except Exception as e:
            logger.error(f"시트 구조 검증 중 오류: {e}")
            result['errors'].append(f"검증 중 오류: {e}")
            return result
    
    def _is_cache_valid(self) -> bool:
        """캐시 유효성 확인"""
        if self._last_fetch_time is None:
            return False
        
        cache_age = (datetime.now() - self._last_fetch_time).total_seconds() / 60
        return cache_age < self._cache_validity_minutes
    
    def clear_cache(self) -> None:
        """캐시 지우기"""
        self._cached_data = []
        self._last_fetch_time = None
        logger.debug("시트 데이터 캐시 클리어")
    
    def get_stats(self) -> Dict[str, Any]:
        """통계 정보 반환"""
        rate_limiter_status = self.rate_limiter.get_status()
        
        return {
            **self.stats,
            'cache_valid': self._is_cache_valid(),
            'cached_items': len(self._cached_data),
            'rate_limiter': rate_limiter_status,
            'last_fetch_time': self._last_fetch_time.isoformat() if self._last_fetch_time else None
        }
    
    def __str__(self) -> str:
        """문자열 표현"""
        return f"GoogleSheetsClient({self.sheets_id[:20]}... / {self.tab_name})"


# 전역 클라이언트 인스턴스
_sheets_client: Optional[GoogleSheetsClient] = None


def get_sheets_manager() -> GoogleSheetsClient:
    """전역 Google Sheets 클라이언트 반환"""
    global _sheets_client
    
    if _sheets_client is None:
        _sheets_client = GoogleSheetsClient()
        
        # 즉시 인증 시도
        if not _sheets_client.authenticate():
            logger.error("Google Sheets 클라이언트 초기화 실패")
            raise RuntimeError("Google Sheets 인증 실패")
    
    return _sheets_client


def test_sheets_connection() -> bool:
    """시트 연결 테스트"""
    try:
        client = get_sheets_manager()
        
        # 구조 검증
        validation_result = client.validate_sheet_structure()
        if not validation_result['valid']:
            logger.error("시트 구조 검증 실패")
            return False
        
        # 샘플 데이터 조회
        sample_data = client.fetch_toot_data(start_row=2, max_rows=5)
        logger.info(f"샘플 데이터 {len(sample_data)}개 조회 성공")
        
        return True
        
    except Exception as e:
        logger.error(f"시트 연결 테스트 실패: {e}")
        return False


if __name__ == "__main__":
    """Google Sheets 클라이언트 테스트"""
    print("🧪 Google Sheets 클라이언트 테스트 시작...")
    
    try:
        # 클라이언트 초기화
        client = GoogleSheetsClient()
        
        # 인증 테스트
        print("🔐 인증 테스트...")
        if client.authenticate():
            print("✅ 인증 성공")
        else:
            print("❌ 인증 실패")
            sys.exit(1)
        
        # 시트 구조 검증
        print("📋 시트 구조 검증...")
        validation = client.validate_sheet_structure()
        print(f"검증 결과: {'✅ 성공' if validation['valid'] else '❌ 실패'}")
        
        if validation['errors']:
            print("오류:")
            for error in validation['errors']:
                print(f"  - {error}")
        
        if validation['warnings']:
            print("경고:")
            for warning in validation['warnings']:
                print(f"  - {warning}")
        
        # 데이터 조회 테스트
        print("📊 데이터 조회 테스트...")
        toots = client.fetch_toot_data(max_rows=10)
        print(f"조회된 툿: {len(toots)}개")
        
        for i, toot in enumerate(toots[:3]):  # 처음 3개만 출력
            print(f"  {i+1}. {toot}")
        
        # 미래 툿 조회
        print("🔮 미래 툿 조회...")
        future_toots = client.get_future_toots()
        print(f"미래 예약 툿: {len(future_toots)}개")
        
        # 다음 예약 툿
        next_toot = client.find_next_scheduled_toot()
        if next_toot:
            print(f"다음 예약: {next_toot}")
        else:
            print("예약된 툿이 없습니다")
        
        # 통계 정보
        print("📈 통계 정보:")
        stats = client.get_stats()
        print(f"  총 API 호출: {stats['total_api_calls']}회")
        print(f"  성공/실패: {stats['successful_calls']}/{stats['failed_calls']}")
        print(f"  조회된 행: {stats['total_rows_fetched']}개")
        
        print("✅ Google Sheets 클라이언트 테스트 완료!")
        
    except Exception as e:
        print(f"❌ 테스트 실패: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
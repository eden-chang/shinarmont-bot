#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Mastodon + Google Sheets 자동봇
관리 시트 체력/정신력 모니터링 기능
"""

import os
import sys
import time
import logging
import re
import signal
import threading
import traceback
import json
import math
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
from enum import Enum
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
import schedule
import gspread
from mastodon import Mastodon
from dotenv import load_dotenv
import pytz
import random

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# 외부 라이브러리 로깅 레벨 조정 (노이즈 감소)
logging.getLogger('schedule').setLevel(logging.WARNING)
logging.getLogger('urllib3').setLevel(logging.WARNING)
logging.getLogger('gspread').setLevel(logging.WARNING)

# 환경변수 로드
load_dotenv()

# 상수 정의
KST = pytz.timezone('Asia/Seoul')

MAX_RETRIES = 5
RETRY_DELAY = 60  # seconds
MAX_CONSECUTIVE_FAILURES = 10  # 연속 실패 허용 횟수
HEALTH_CHECK_INTERVAL = 300  # 헬스체크 간격 (5분)
CRITICAL_ERROR_RESTART_DELAY = 300  # 치명적 오류 시 재시작 대기시간 (5분)
MAX_RESTART_ATTEMPTS = 3  # 최대 재시작 시도 횟수

# Google Sheets API 레이트 리미팅 설정
BASE_API_DELAY = 2.0  # 기본 API 호출 간격 (초)
QUOTA_EXCEEDED_DELAY = 120  # 쿼터 초과 시 대기 시간 (2분)
MAX_BACKOFF_DELAY = 600  # 최대 백오프 대기 시간 (10분)


class BotStatus(Enum):
    """봇 상태 열거형"""
    STARTING = "starting"
    RUNNING = "running"
    RECOVERING = "recovering"
    CRITICAL_ERROR = "critical_error"
    SHUTDOWN = "shutdown"


class CircuitBreakerState(Enum):
    """회로 차단기 상태"""
    CLOSED = "closed"  # 정상 작동
    OPEN = "open"      # 차단됨
    HALF_OPEN = "half_open"  # 복구 시도


class HealthLevel(Enum):
    NORMAL = "normal"
    LIGHT = "경상"
    HEAVY = "중상"
    DEAD = "사망"


class SanityLevel(Enum):
    NORMAL = "normal"
    SHORT = "단기 광기"
    LONG = "장기 광기"
    PERMANENT = "영구 광기"


HEALTH_STATUS_ORDER = {
    HealthLevel.NORMAL: 0,
    HealthLevel.LIGHT: 1,
    HealthLevel.HEAVY: 2,
    HealthLevel.DEAD: 3,
}

SANITY_STATUS_ORDER = {
    SanityLevel.NORMAL: 0,
    SanityLevel.SHORT: 1,
    SanityLevel.LONG: 2,
    SanityLevel.PERMANENT: 3,
}

HEALTH_STATUS_TOKENS = {"경상", "중상", "사망"}
SANITY_STATUS_TOKENS = {"단기 광기", "장기 광기", "영구 광기", "실종"}
NON_CLEARABLE_TOKENS = {"사망", "장기 광기", "영구 광기", "실종"}
STATUS_TOKEN_ORDER = [
    "사망",
    "중상",
    "경상",
    "장기 광기",
    "단기 광기",
    "영구 광기",
    "실종",
]


@dataclass
class ManagementState:
    health: str = HealthLevel.NORMAL.value
    sanity: str = SanityLevel.NORMAL.value
    last_seen: str = ""
    name: str = ""


class CircuitBreaker:
    """회로 차단기 클래스"""
    def __init__(self, failure_threshold: int = 5, timeout: int = 300):
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.failure_count = 0
        self.last_failure_time = None
        self.state = CircuitBreakerState.CLOSED

    def call(self, func, *args, **kwargs):
        """함수 호출 with 회로 차단기"""
        if self.state == CircuitBreakerState.OPEN:
            if time.time() - self.last_failure_time > self.timeout:
                self.state = CircuitBreakerState.HALF_OPEN
                logger.debug("회로 차단기 HALF_OPEN 상태로 전환")
            else:
                raise Exception("회로 차단기가 OPEN 상태입니다")

        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except Exception as e:
            self._on_failure()
            raise e

    def _on_success(self):
        """성공 시 처리"""
        self.failure_count = 0
        if self.state == CircuitBreakerState.HALF_OPEN:
            self.state = CircuitBreakerState.CLOSED
            logger.debug("회로 차단기 CLOSED 상태로 전환")

    def _on_failure(self):
        """실패 시 처리"""
        self.failure_count += 1
        self.last_failure_time = time.time()

        if self.failure_count >= self.failure_threshold:
            self.state = CircuitBreakerState.OPEN
            logger.warning(f"회로 차단기 OPEN 상태로 전환 (실패 횟수: {self.failure_count})")


class AutoBot:
    def __init__(self):
        self.gc = None
        self.mastodon = None
        self.spreadsheet = None
        self.status = BotStatus.STARTING
        self.consecutive_failures = 0
        self.last_successful_operation = time.time()
        self.restart_count = 0
        self.shutdown_requested = False

        # 데이터 캐싱을 위한 변수들
        self._cached_data = {}
        self._cached_mappings = {}
        self._cache_timestamp = None
        self.CACHE_DURATION = 300  # 5분 캐시
        self.MAX_CACHE_SIZE = 10000  # 최대 캐시 항목 수 (메모리 보호)

        # 관리 시트 상태 캐시
        self.status_cache_file = 'management_status_cache.json'
        self.management_status_cache: Dict[str, Dict[str, Any]] = {}
        self._status_cache_loaded = False
        self._status_cache_dirty = False

        # Google Sheets API 레이트 리미팅
        self._last_api_call_time = 0
        self._api_call_count = 0
        self._quota_reset_time = time.time()
        self._backoff_delay = BASE_API_DELAY

        # 회로 차단기 설정
        self.sheets_circuit_breaker = CircuitBreaker(failure_threshold=3, timeout=300)
        self.mastodon_circuit_breaker = CircuitBreaker(failure_threshold=5, timeout=180)

        # 스레드 관련
        self.health_check_thread = None
        self.main_thread_active = True

        # 시그널 핸들러 등록
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)
        # Windows에서는 SIGUSR1이 지원되지 않음
        if hasattr(signal, 'SIGUSR1'):
            signal.signal(signal.SIGUSR1, self._reload_config_handler)  # 설정 리로드

        self._initialize_apis()
        self._load_status_cache()

    def _signal_handler(self, signum, frame):
        """시그널 핸들러"""
        logger.info(f"시그널 {signum} 수신, 안전한 종료 시작...")
        self.shutdown_requested = True
        self.status = BotStatus.SHUTDOWN
        self.main_thread_active = False

    def _reload_config_handler(self, signum, frame):
        """설정 리로드 시그널 핸들러 (SIGUSR1)"""
        logger.info("설정 리로드 시그널 수신")
        try:
            self._reload_configuration()
        except Exception as e:
            logger.error(f"설정 리로드 실패: {e}")

    def _reload_configuration(self):
        """설정 리로드"""
        logger.info("⚙️ 설정 리로드 시작")

        # .env 파일 리로드
        if os.path.exists('.env'):
            load_dotenv(override=True)
            logger.debug(".env 파일 리로드 완료")

        # API 재초기화 시도
        try:
            old_status = self.status
            self.status = BotStatus.RECOVERING

            self._initialize_apis()

            # 상태 복구
            self.status = old_status if old_status != BotStatus.CRITICAL_ERROR else BotStatus.RUNNING

            logger.info("✅ 설정 리로드 완료")

        except Exception as e:
            logger.error(f"API 재초기화 실패: {e}")
            self.status = BotStatus.CRITICAL_ERROR

    def _wait_for_rate_limit(self):
        """Google Sheets API 레이트 리미팅을 위한 대기"""
        current_time = time.time()

        # 매분 초기화 (1분 윈도우)
        if current_time - self._quota_reset_time >= 60:
            self._api_call_count = 0
            self._quota_reset_time = current_time
            self._backoff_delay = BASE_API_DELAY  # 백오프 리셋

        # API 호출 간격 제어
        time_since_last_call = current_time - self._last_api_call_time
        if time_since_last_call < self._backoff_delay:
            sleep_time = self._backoff_delay - time_since_last_call
            # 지터 추가 (10% 랜덤 변동)
            jitter = sleep_time * 0.1 * random.random()
            sleep_time += jitter
            logger.debug(f"API 레이트 리미팅 대기: {sleep_time:.2f}초")
            time.sleep(sleep_time)

        self._last_api_call_time = time.time()
        self._api_call_count += 1

    def _handle_quota_exceeded(self):
        """쿼터 초과 시 처리"""
        logger.warning(f"⚠️ Google Sheets API 쿼터 초과, {QUOTA_EXCEEDED_DELAY}초 대기")

        # 백오프 증가 (최대값까지)
        self._backoff_delay = min(self._backoff_delay * 2, MAX_BACKOFF_DELAY)
        logger.debug(f"백오프 딜레이 증가: {self._backoff_delay:.2f}초")

        # 쿼터 초과 대기
        time.sleep(QUOTA_EXCEEDED_DELAY)

        # 카운터 리셋
        self._api_call_count = 0
        self._quota_reset_time = time.time()

    def _sheets_api_call(self, operation, *args, **kwargs):
        """Google Sheets API 호출 래퍼 (레이트 리미팅 포함)"""
        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                # 레이트 리미팅 대기
                self._wait_for_rate_limit()

                # API 호출
                result = operation(*args, **kwargs)

                # 성공 시 백오프 감소
                if self._backoff_delay > BASE_API_DELAY:
                    self._backoff_delay = max(BASE_API_DELAY, self._backoff_delay * 0.8)

                return result

            except gspread.exceptions.APIError as e:
                error_str = str(e).lower()

                if "quota exceeded" in error_str or "rate limit" in error_str:
                    self._handle_quota_exceeded()
                    if attempt == max_attempts - 1:
                        raise
                    continue
                else:
                    # 다른 API 오류는 바로 재발생
                    raise
            except Exception as e:
                # 네트워크 오류 등은 짧게 대기 후 재시도
                if attempt < max_attempts - 1:
                    wait_time = (attempt + 1) * 2
                    logger.debug(f"API 호출 실패, {wait_time}초 후 재시도: {e}")
                    time.sleep(wait_time)
                    continue
                raise

        raise Exception(f"API 호출 최대 재시도 횟수 초과: {max_attempts}")

    def _create_status_file(self):
        """상태 파일 생성"""
        status_data = {
            'status': self.status.value,
            'last_successful_operation': self.last_successful_operation,
            'consecutive_failures': self.consecutive_failures,
            'restart_count': self.restart_count,
            'uptime': time.time() - self.start_time if hasattr(self, 'start_time') else 0
        }

        try:
            with open('bot_status.json', 'w', encoding='utf-8') as f:
                json.dump(status_data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"상태 파일 생성 실패: {e}")

    @staticmethod
    def _column_index_to_letter(col_index: int) -> str:
        """컬럼 인덱스를 Excel 스타일 문자로 변환 (0=A, 25=Z, 26=AA, ...)"""
        result = ""
        col_index += 1  # 0-based를 1-based로 변환
        while col_index > 0:
            col_index -= 1
            result = chr(ord('A') + (col_index % 26)) + result
            col_index //= 26
        return result

    def _get_column_mapping(self, worksheet, expected_headers):
        """워크시트의 헤더를 읽어 열 매핑 생성"""
        try:
            headers = self._sheets_api_call(worksheet.row_values, 1)
            mapping = {}

            for header_name in expected_headers:
                try:
                    col_index = headers.index(header_name)
                    col_letter = self._column_index_to_letter(col_index)
                    mapping[header_name] = col_letter
                except ValueError:
                    logger.error(f"⚠️ 필수 헤더 '{header_name}' 누락. 사용 가능한 헤더: {headers}")

            return mapping, headers
        except Exception as e:
            logger.error(f"헤더 매핑 생성 실패: {e}")
            return {}, []


    def _is_cache_valid(self) -> bool:
        """캐시 유효성 검사"""
        if self._cache_timestamp is None:
            return False
        return time.time() - self._cache_timestamp < self.CACHE_DURATION

    def _load_cached_data(self, sheet_names: List[str], force_refresh: bool = False) -> Dict[str, Any]:
        """캐시된 데이터 로드 또는 새로 읽기"""
        # spreadsheet 객체 초기화 확인
        if not self.spreadsheet:
            raise RuntimeError("Spreadsheet 객체가 초기화되지 않았습니다")

        if force_refresh or not self._is_cache_valid():
            self._refresh_cache(sheet_names)

        # 캐시 로드 실패 시 빈 결과 반환하지 않고 재시도
        result = {
            'data': {name: self._cached_data.get(name, []) for name in sheet_names},
            'mappings': {name: self._cached_mappings.get(name, {}) for name in sheet_names}
        }

        # 중요한 매핑이 누락된 경우 강제 재로드
        missing_mappings = []
        for name in sheet_names:
            if not result['mappings'][name]:
                missing_mappings.append(name)

        if missing_mappings and not force_refresh:
            logger.debug(f"매핑 누락으로 인한 강제 재로드: {missing_mappings}")
            return self._load_cached_data(sheet_names, force_refresh=True)

        return result

    def _refresh_cache(self, sheet_names: List[str]):
        """캐시 새로고침"""
        successful_sheets = []
        failed_sheets = []

        try:
            # 헤더 정의
            header_definitions = {
                '관리': ['이름', '아이디', '최대 체력', '체력', '최대 정신력', '정신력', '상태이상']
            }

            for sheet_name in sheet_names:
                try:
                    if not self.spreadsheet:
                        raise RuntimeError("Spreadsheet 객체가 초기화되지 않았습니다")

                    worksheet = self.spreadsheet.worksheet(sheet_name)

                    # 헤더 매핑 생성
                    headers = header_definitions.get(sheet_name, [])
                    if not headers:
                        logger.warning(f"알 수 없는 시트: {sheet_name}")
                        failed_sheets.append(sheet_name)
                        continue

                    mapping, actual_headers = self._get_column_mapping(worksheet, headers)

                    # 필수 헤더 검증
                    essential_headers = {
                        '관리': ['이름', '아이디', '최대 체력', '체력', '최대 정신력', '정신력', '상태이상']
                    }

                    missing_essential = []
                    if sheet_name in essential_headers:
                        for essential in essential_headers[sheet_name]:
                            if essential not in mapping or not mapping[essential]:
                                missing_essential.append(essential)

                    if missing_essential:
                        logger.error(f"{sheet_name} 시트 필수 헤더 누락: {missing_essential}")
                        failed_sheets.append(sheet_name)
                        self._cached_data[sheet_name] = []
                        self._cached_mappings[sheet_name] = {}
                        continue

                    # 데이터 읽기
                    if sheet_name == '관리':
                        # 관리 시트는 1행: 헤더, 2행: 설명, 3행부터: 실제 데이터
                        all_values = self._sheets_api_call(worksheet.get_all_values)
                        if len(all_values) < 3:
                            data = []
                        else:
                            # 1행을 헤더로, 3행부터 데이터로 파싱
                            header_row = all_values[0]
                            data_rows = all_values[2:]  # 3행부터
                            
                            # 딕셔너리 리스트로 변환
                            data = []
                            for row in data_rows:
                                row_dict = {}
                                for i, header in enumerate(header_row):
                                    if i < len(row):
                                        row_dict[header] = row[i]
                                    else:
                                        row_dict[header] = ''
                                data.append(row_dict)
                    else:
                        # 다른 시트는 기본 방식 사용
                        data = self._sheets_api_call(worksheet.get_all_records)

                    # 데이터 유효성 검사
                    if not isinstance(data, list):
                        raise ValueError(f"잘못된 데이터 형식: {type(data)}")

                    # 메모리 보호: 데이터 크기 제한
                    if len(data) > self.MAX_CACHE_SIZE:
                        logger.warning(f"⚠️ {sheet_name} 시트 데이터 크기 초과: {len(data)} > {self.MAX_CACHE_SIZE}")
                        data = data[:self.MAX_CACHE_SIZE]

                    self._cached_mappings[sheet_name] = mapping
                    self._cached_data[sheet_name] = data
                    successful_sheets.append(sheet_name)

                except gspread.WorksheetNotFound:
                    logger.error(f"워크시트를 찾을 수 없습니다: {sheet_name}")
                    failed_sheets.append(sheet_name)
                    self._cached_data[sheet_name] = []
                    self._cached_mappings[sheet_name] = {}
                except gspread.exceptions.APIError as e:
                    logger.error(f"{sheet_name} 시트 API 오류: {e}")
                    failed_sheets.append(sheet_name)
                    # API 오류는 이전 캐시 유지 (완전 실패가 아님)
                    if sheet_name not in self._cached_data:
                        self._cached_data[sheet_name] = []
                    if sheet_name not in self._cached_mappings:
                        self._cached_mappings[sheet_name] = {}
                except Exception as e:
                    logger.error(f"{sheet_name} 시트 데이터 로드 실패: {e}")
                    failed_sheets.append(sheet_name)
                    self._cached_data[sheet_name] = []
                    self._cached_mappings[sheet_name] = {}

            # 성공한 시트가 있으면 캐시 타임스탬프 업데이트
            if successful_sheets:
                self._cache_timestamp = time.time()

            if failed_sheets:
                logger.warning(f"⚠️ 캐시 업데이트 실패: {failed_sheets}")

            # 모든 시트 로드 실패 시 예외 발생
            if len(failed_sheets) == len(sheet_names):
                raise Exception(f"모든 시트 로드 실패: {failed_sheets}")

        except Exception as e:
            logger.error(f"캐시 새로고침 치명적 실패: {e}")
            # 전체 실패 시에만 빈 데이터로 초기화
            if not successful_sheets:
                for sheet_name in sheet_names:
                    self._cached_data[sheet_name] = []
                    self._cached_mappings[sheet_name] = {}
            raise

    def _execute_batch_updates(self, updates_by_sheet: Dict[str, List[Dict]]):
        """시트별 배치 업데이트 실행"""
        if not updates_by_sheet:
            return

        successful_updates = {}
        failed_updates = {}

        for sheet_name, updates in updates_by_sheet.items():
            if not updates:
                continue

            try:
                if not self.spreadsheet:
                    raise RuntimeError("Spreadsheet 객체가 초기화되지 않았습니다")

                # 업데이트 유효성 검사
                valid_updates = []
                for update in updates:
                    if not isinstance(update, dict):
                        continue
                    if 'range' not in update or 'values' not in update:
                        continue
                    if not update['range'] or not update['values']:
                        continue
                    valid_updates.append(update)

                if not valid_updates:
                    continue

                worksheet = self.spreadsheet.worksheet(sheet_name)

                # 대량 업데이트를 작은 청크로 분할 (API 한도 방지)
                chunk_size = 50  # 청크 크기 감소로 API 호출 분산
                for i in range(0, len(valid_updates), chunk_size):
                    chunk = valid_updates[i:i + chunk_size]
                    self._sheets_api_call(
                        worksheet.batch_update,
                        chunk,
                        value_input_option='USER_ENTERED'
                    )

                successful_updates[sheet_name] = len(valid_updates)

            except gspread.WorksheetNotFound:
                logger.error(f"워크시트를 찾을 수 없습니다: {sheet_name}")
                failed_updates[sheet_name] = f"워크시트 없음"
            except gspread.exceptions.APIError as e:
                logger.error(f"{sheet_name} 시트 API 오류: {e}")
                failed_updates[sheet_name] = f"API 오류: {e}"
                # API 오류는 일시적일 수 있으므로 재시도 고려
                if "quota" not in str(e).lower():
                    try:
                        time.sleep(5)  # 잠시 대기
                        self._sheets_api_call(
                            worksheet.batch_update,
                            valid_updates,
                            value_input_option='USER_ENTERED'
                        )
                        successful_updates[sheet_name] = len(valid_updates)
                        del failed_updates[sheet_name]  # 실패 목록에서 제거
                    except Exception as retry_e:
                        logger.error(f"⚠️ {sheet_name} 시트 재시도 실패: {retry_e}")
                        failed_updates[sheet_name] = f"재시도 실패: {retry_e}"
            except Exception as e:
                logger.error(f"{sheet_name} 시트 업데이트 실패: {e}")
                failed_updates[sheet_name] = str(e)

        # 결과 요약
        if failed_updates:
            logger.warning(f"⚠️ 업데이트 실패: {failed_updates}")
            # 일부 실패는 허용하지만, 모든 업데이트가 실패하면 예외 발생
            if len(failed_updates) == len([k for k, v in updates_by_sheet.items() if v]):
                raise Exception(f"모든 시트 업데이트 실패: {failed_updates}")

    def _invalidate_cache(self, sheet_names: List[str] = None):
        """캐시 무효화"""
        try:
            if sheet_names:
                # 특정 시트 캐시만 무효화
                invalidated = []
                for sheet_name in sheet_names:
                    if sheet_name in self._cached_data:
                        del self._cached_data[sheet_name]
                        invalidated.append(sheet_name)
                    if sheet_name in self._cached_mappings:
                        del self._cached_mappings[sheet_name]

                if invalidated:
                    logger.debug(f"부분 캐시 무효화: {invalidated}")

                    # 모든 데이터가 무효화되면 타임스탬프도 초기화
                    if not self._cached_data and not self._cached_mappings:
                        self._cache_timestamp = None
                        logger.debug("모든 캐시 데이터 무효화로 인한 타임스탬프 초기화")
            else:
                # 전체 캐시 무효화
                self._cached_data.clear()
                self._cached_mappings.clear()
                self._cache_timestamp = None
                logger.debug("전체 캐시 무효화")

        except Exception as e:
            logger.error(f"캐시 무효화 오류: {e}")
            # 오류 발생 시 안전을 위해 전체 캐시 초기화
            try:
                self._cached_data.clear()
                self._cached_mappings.clear()
                self._cache_timestamp = None
                logger.warning("캐시 무효화 오류로 인한 전체 캐시 초기화")
            except Exception as clear_e:
                logger.critical(f"캐시 초기화마저 실패: {clear_e}")

    def _clamp_stat_to_zero(self, value) -> int:
        """스탯 값을 0 이상으로 보정"""
        try:
            numeric_value = int(value or 0)
            return max(0, numeric_value)
        except (ValueError, TypeError):
            logger.warning(f"스탯 값 변환 실패, 0으로 설정: {value}")
            return 0


    def _initialize_apis(self):
        """API 클라이언트 초기화"""
        max_init_attempts = 3
        for attempt in range(max_init_attempts):
            try:
                if attempt > 0:
                    logger.info(f"API 초기화 재시도 {attempt + 1}/{max_init_attempts}")

                # Google Sheets API 초기화
                credentials_file = 'credentials.json'
                if not os.path.exists(credentials_file):
                    raise FileNotFoundError(f"Google Sheets 인증 파일이 없습니다: {credentials_file}")

                self.gc = gspread.service_account(filename=credentials_file)
                spreadsheet_id = os.getenv('GOOGLE_SHEET_ID')
                if not spreadsheet_id:
                    raise ValueError("GOOGLE_SHEET_ID가 설정되지 않았습니다.")

                try:
                    self.spreadsheet = self.gc.open_by_key(spreadsheet_id)
                except gspread.SpreadsheetNotFound:
                    raise ValueError(f"스프레드시트를 찾을 수 없습니다: {spreadsheet_id}")
                except gspread.exceptions.APIError as e:
                    if attempt < max_init_attempts - 1:
                        logger.warning(f"⚠️ Google Sheets API 오류, 재시도: {e}")
                        time.sleep(30)
                        continue
                    raise ValueError(f"Google Sheets API 오류: {e}")

                # Mastodon API 초기화
                access_token = os.getenv('MASTODON_ACCESS_TOKEN')
                api_base_url = os.getenv('MASTODON_API_BASE_URL')
                if not access_token or not api_base_url:
                    raise ValueError("MASTODON_ACCESS_TOKEN 또는 MASTODON_API_BASE_URL이 설정되지 않았습니다.")

                self.mastodon = Mastodon(
                    access_token=access_token,
                    api_base_url=api_base_url
                )

                logger.info("✅ API 클라이언트 초기화 완료")
                return

            except Exception as e:
                logger.error(f"API 초기화 실패 (시도 {attempt + 1}): {e}")
                if attempt == max_init_attempts - 1:
                    raise
                time.sleep(60)

    def _get_current_time_kst(self) -> datetime:
        """현재 KST 시간 반환"""
        return datetime.now(KST)

    def _parse_datetime(self, date_str: str) -> Optional[datetime]:
        """문자열을 datetime 객체로 변환"""
        if not date_str or date_str.strip() == '' or date_str.strip() == '-':
            return None

        try:
            # 다양한 날짜 형식 지원
            formats = [
                '%Y-%m-%d %H:%M',
                '%m/%d %H:%M',
                '%Y-%m-%d %H:%M:%S',
                '%m/%d %H:%M:%S',
                '%Y/%m/%d %H:%M',
                '%m-%d %H:%M'
            ]

            cleaned_date = date_str.strip()
            current_year = self._get_current_time_kst().year

            for fmt in formats:
                try:
                    dt = datetime.strptime(cleaned_date, fmt)
                    # 년도가 없는 경우 현재 년도 사용
                    if fmt.startswith('%m/'):
                        dt = dt.replace(year=current_year)
                        # 날짜가 과거인 경우 다음 년 고려 (timezone 일치시켜 비교)
                        current_time_naive = self._get_current_time_kst().replace(tzinfo=None).replace(hour=0, minute=0, second=0, microsecond=0)
                        if dt < current_time_naive:
                            dt = dt.replace(year=current_year + 1)

                    # timezone aware datetime으로 변환
                    if dt.tzinfo is None:
                        dt = KST.localize(dt)
                    return dt

                except ValueError:
                    continue

            logger.debug(f"날짜 파싱 실패: {date_str}")
            return None

        except Exception as e:
            logger.error(f"날짜 파싱 오류: {e}")
            return None

    def _format_datetime(self, dt: datetime) -> str:
        """datetime을 문자열로 포맷"""
        return dt.strftime('%m/%d %H:%M')


    def _retry_operation(self, operation, *args, **kwargs):
        """재시도 로직이 있는 작업 실행"""
        last_exception = None

        for attempt in range(MAX_RETRIES):
            try:
                result = operation(*args, **kwargs)
                # 성공 시 연속 실패 카운터 리셋
                self.consecutive_failures = 0
                self.last_successful_operation = time.time()
                return result

            except Exception as e:
                last_exception = e
                logger.debug(f"작업 실패 (시도 {attempt + 1}/{MAX_RETRIES}): {e}")

                # 네트워크 관련 오류 판별
                if self._is_network_error(e):
                    logger.warning("⚠️ 네트워크 오류 감지, API 재연결 시도")
                    self._attempt_api_reconnection()

                if attempt == MAX_RETRIES - 1:
                    self.consecutive_failures += 1
                    logger.error(f"연속 실패 횟수: {self.consecutive_failures}")

                    if self.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                        logger.critical("최대 연속 실패 횟수 도달, 봇 상태를 CRITICAL_ERROR로 변경")
                        self.status = BotStatus.CRITICAL_ERROR

                    raise last_exception

                # 지수 백오프
                delay = min(RETRY_DELAY * (2 ** attempt), 300)
                time.sleep(delay)

        raise last_exception

    def _is_network_error(self, exception) -> bool:
        """네트워크 관련 오류인지 판별"""
        error_indicators = [
            'connection', 'timeout', 'network', 'dns', 'socket',
            'http', 'ssl', 'certificate', 'unreachable'
        ]
        error_str = str(exception).lower()
        return any(indicator in error_str for indicator in error_indicators)

    def _attempt_api_reconnection(self):
        """API 재연결 시도"""
        try:
            logger.warning("🔄 API 재연결 시도 중...")
            self.status = BotStatus.RECOVERING

            # 잠시 대기
            time.sleep(30)

            # API 재초기화
            self._initialize_apis()

            logger.info("✅ API 재연결 성공")
            self.status = BotStatus.RUNNING

        except Exception as e:
            logger.error(f"API 재연결 실패: {e}")
            self.status = BotStatus.CRITICAL_ERROR

    def _send_dm(self, user_id: str, message: str):
        """마스토돈 DM 전송"""
        if not user_id or not message:
            logger.debug(f"DM 전송 실패 - 빈 데이터: user_id='{user_id}', message='{message}'")
            return False

        if not self.mastodon:
            logger.error("Mastodon 클라이언트가 초기화되지 않았습니다")
            return False

        def send_dm_operation():
            # 메시지 길이 제한 (Mastodon 한도: 500자)
            max_length = 450  # @user_id 등을 위한 여유 공간
            if len(message) > max_length:
                truncated_message = message[:max_length] + "..."
                message_to_send = truncated_message
            else:
                message_to_send = message

            return self.mastodon.status_post(
                status=f"@{user_id} {message_to_send}",
                visibility='direct'
            )

        try:
            self.mastodon_circuit_breaker.call(send_dm_operation)
            logger.info(f"📨 DM 전송: @{user_id}")
            return True
        except Exception as e:
            logger.error(f"DM 전송 실패 ({user_id}): {e}")
            # DM 전송 실패는 치명적이지 않으므로 예외를 다시 발생시키지 않음
            return False

    def _send_bulk_dm(self, dm_messages: List[Tuple[str, str]]):
        """대량 DM 전송 with 중복 제거 및 배치 처리"""
        if not dm_messages:
            return

        # 중복 제거 (같은 사용자에게 같은 메시지 중복 방지)
        unique_messages = {}
        for user_id, message in dm_messages:
            if user_id in unique_messages:
                existing_msg = unique_messages[user_id]
                if message not in existing_msg:
                    unique_messages[user_id] = f"{existing_msg}\n{message}"
            else:
                unique_messages[user_id] = message

        logger.debug(f"DM 전송 시작: {len(dm_messages)}개 메시지 -> {len(unique_messages)}개 고유 메시지")

        success_count = 0
        fail_count = 0

        for user_id, message in unique_messages.items():
            try:
                if self._send_dm(user_id, message):
                    success_count += 1
                else:
                    fail_count += 1

                # API 제한을 피하기 위해 메시지 간 간격
                if success_count % 10 == 0:
                    time.sleep(1)

            except Exception as e:
                logger.error(f"예상치 못한 DM 전송 오류 ({user_id}): {e}")
                fail_count += 1

        if fail_count > 0:
            logger.warning(f"⚠️ DM 전송 실패: {fail_count}개")

    def _health_check(self):
        """시스템 헬스체크 수행"""
        try:
            # Google Sheets 연결 체크
            test_sheet = self.spreadsheet.worksheet('관리')
            self._sheets_api_call(test_sheet.get, 'A1')

            # Mastodon 연결 체크
            self.mastodon.account_verify_credentials()

            # 마지막 성공적 작업에서 너무 오래 지났는지 체크 (3시간 이상일 때만 경고)
            time_since_last_success = time.time() - self.last_successful_operation
            if time_since_last_success > 10800:  # 3시간
                logger.warning(f"⚠️ 마지막 성공적 작업에서 {time_since_last_success/60:.1f}분 경과")

            return True

        except Exception as e:
            logger.error(f"헬스체크 실패: {e}")
            return False

    def _start_health_monitor(self):
        """헬스체크 모니터 스레드 시작"""
        def health_monitor():
            while self.main_thread_active and not self.shutdown_requested:
                try:
                    if self.status == BotStatus.RUNNING:
                        if not self._health_check():
                            logger.warning("⚠️ 헬스체크 실패, 복구 시도")
                            self._attempt_api_reconnection()

                    time.sleep(HEALTH_CHECK_INTERVAL)

                except Exception as e:
                    logger.error(f"헬스 모니터 오류: {e}")
                    time.sleep(60)

        self.health_check_thread = threading.Thread(target=health_monitor, daemon=True)
        self.health_check_thread.start()

    def _safe_operation_wrapper(self, operation_name: str, operation_func):
        """안전한 작업 래퍼"""
        if self.shutdown_requested:
            return

        if self.status == BotStatus.CRITICAL_ERROR:
            logger.warning(f"⚠️ {operation_name} 작업 건너뛰기 (치명적 오류 상태)")
            return

        try:
            operation_func()

            # 성공 시 상태 업데이트
            if self.status != BotStatus.RUNNING:
                self.status = BotStatus.RUNNING
                logger.info("✅ 봇 상태 복구: RUNNING")

        except Exception as e:
            logger.error(f"{operation_name} 작업 오류: {e}")
            logger.error(f"Traceback: {traceback.format_exc()}")

            # 실패 시 상태 처리
            if self.status == BotStatus.RUNNING:
                self.status = BotStatus.RECOVERING

    def _restart_bot_if_needed(self):
        """필요시 봇 재시작"""
        if self.status != BotStatus.CRITICAL_ERROR:
            return

        if self.restart_count >= MAX_RESTART_ATTEMPTS:
            logger.critical("❌ 최대 재시작 시도 횟수 초과, 봇 종료")
            self.shutdown_requested = True
            return

        self.restart_count += 1
        logger.warning(f"🔄 봇 재시작 시도 {self.restart_count}/{MAX_RESTART_ATTEMPTS}")

        try:
            # 대기 시간
            time.sleep(CRITICAL_ERROR_RESTART_DELAY)

            # API 재초기화
            self._initialize_apis()

            # 상태 리셋
            self.status = BotStatus.RUNNING
            self.consecutive_failures = 0
            self.last_successful_operation = time.time()

            # 회로 차단기 리셋
            self.sheets_circuit_breaker = CircuitBreaker(failure_threshold=3, timeout=300)
            self.mastodon_circuit_breaker = CircuitBreaker(failure_threshold=5, timeout=180)

            logger.info("✅ 봇 재시작 성공")

        except Exception as e:
            logger.error(f"봇 재시작 실패: {e}")
            # 재시작 실패 시 더 오래 대기
            time.sleep(CRITICAL_ERROR_RESTART_DELAY * 2)



    def management_monitor_job(self, send_notifications: bool = True):
        """30분 주기 관리 시트 체력/정신력 모니터링"""
        self._safe_operation_wrapper("관리 시트 모니터링", lambda: self._monitor_management_sheet(send_notifications))


    def _load_status_cache(self):
        """관리 시트 상태 캐시 로드"""
        if self._status_cache_loaded:
            return

        if not os.path.exists(self.status_cache_file):
            self.management_status_cache = {}
            self._status_cache_loaded = True
            return

        try:
            with open(self.status_cache_file, 'r', encoding='utf-8') as cache_file:
                data = json.load(cache_file)
                if isinstance(data, dict):
                    self.management_status_cache = data
                else:
                    logger.warning("⚠️ 관리 상태 캐시 형식이 잘못되었습니다. 새로 생성합니다.")
                    self.management_status_cache = {}
        except Exception as e:
            logger.error(f"관리 상태 캐시 로드 실패: {e}")
            self.management_status_cache = {}

        self._status_cache_loaded = True
        self._status_cache_dirty = False

    def _save_status_cache(self):
        """관리 시트 상태 캐시 저장"""
        if not self._status_cache_dirty:
            return

        try:
            with open(self.status_cache_file, 'w', encoding='utf-8') as cache_file:
                json.dump(self.management_status_cache, cache_file, ensure_ascii=False, indent=2)
            self._status_cache_dirty = False
        except Exception as e:
            logger.error(f"관리 상태 캐시 저장 실패: {e}")

    @staticmethod
    def _parse_status_tokens(status_str: str) -> List[str]:
        if not status_str:
            return []
        tokens = []
        for token in status_str.split(','):
            cleaned = token.strip()
            if cleaned:
                tokens.append(cleaned)
        return tokens

    @staticmethod
    def _round_half_up(value: float) -> int:
        return int(Decimal(str(value)).quantize(Decimal('1'), rounding=ROUND_HALF_UP))

    @staticmethod
    def _floor_to_int(value: float) -> int:
        return int(math.floor(value))

    @staticmethod
    def _parse_numeric(value, default: float = 0.0) -> float:
        if value is None or value == '':
            return default
        try:
            if isinstance(value, (int, float)):
                return float(value)
            cleaned = str(value).replace(',', '').strip()
            if cleaned in {'', '-', '--'}:
                return default
            return float(cleaned)
        except (ValueError, TypeError):
            logger.debug(f"숫자 변환 실패: {value}, default 사용")
            return default

    def _determine_health_level(self, current_hp: int, max_hp: int) -> HealthLevel:
        if max_hp <= 0:
            return HealthLevel.NORMAL
        if current_hp <= 0:
            return HealthLevel.DEAD
        percent = (current_hp / max_hp) * 100
        if percent <= 20:
            return HealthLevel.HEAVY
        if percent <= 70:
            return HealthLevel.LIGHT
        return HealthLevel.NORMAL

    def _determine_sanity_level(self, current_sanity: int, max_sanity: int) -> SanityLevel:
        if max_sanity <= 0:
            return SanityLevel.NORMAL
        if current_sanity <= 0:
            return SanityLevel.PERMANENT
        percent = (current_sanity / max_sanity) * 100
        if percent <= 20:
            return SanityLevel.LONG
        if percent <= 60:
            return SanityLevel.SHORT
        return SanityLevel.NORMAL

    @staticmethod
    def _health_level_from_tokens(tokens: List[str]) -> Optional[HealthLevel]:
        if '사망' in tokens:
            return HealthLevel.DEAD
        if '중상' in tokens:
            return HealthLevel.HEAVY
        if '경상' in tokens:
            return HealthLevel.LIGHT
        return HealthLevel.NORMAL if tokens else HealthLevel.NORMAL

    @staticmethod
    def _sanity_level_from_tokens(tokens: List[str]) -> SanityLevel:
        if '영구 광기' in tokens or '실종' in tokens:
            return SanityLevel.PERMANENT
        if '장기 광기' in tokens:
            return SanityLevel.LONG
        if '단기 광기' in tokens:
            return SanityLevel.SHORT
        return SanityLevel.NORMAL

    @staticmethod
    def _join_status_tokens(tokens: List[str]) -> str:
        if not tokens:
            return ''
        ordered = []
        seen = set()
        for token in STATUS_TOKEN_ORDER:
            if token in tokens and token not in seen:
                ordered.append(token)
                seen.add(token)
        for token in tokens:
            if token not in seen:
                ordered.append(token)
                seen.add(token)
        return ', '.join(ordered)

    def _update_status_tokens_for_health(self, tokens: List[str], target_level: HealthLevel) -> Tuple[List[str], bool]:
        updated = False
        mutable_tokens = [t for t in tokens if t]

        def remove_token(token: str):
            nonlocal updated, mutable_tokens
            if token in mutable_tokens:
                mutable_tokens = [t for t in mutable_tokens if t != token]
                updated = True

        def add_token(token: str):
            nonlocal updated, mutable_tokens
            if token not in mutable_tokens:
                mutable_tokens.append(token)
                updated = True

        if target_level == HealthLevel.NORMAL:
            remove_token('경상')
            remove_token('중상')
            # 사망은 제거하지 않음 (수동 해제만 허용)
        elif target_level == HealthLevel.LIGHT:
            remove_token('중상')
            add_token('경상')
        elif target_level == HealthLevel.HEAVY:
            remove_token('경상')
            add_token('중상')
        elif target_level == HealthLevel.DEAD:
            remove_token('경상')
            remove_token('중상')
            add_token('사망')

        return mutable_tokens, updated

    def _update_status_tokens_for_sanity(self, tokens: List[str], target_level: SanityLevel) -> Tuple[List[str], bool]:
        updated = False
        mutable_tokens = [t for t in tokens if t]

        def remove_token(token: str):
            nonlocal updated, mutable_tokens
            if token in mutable_tokens:
                mutable_tokens = [t for t in mutable_tokens if t != token]
                updated = True

        def add_token(token: str):
            nonlocal updated, mutable_tokens
            if token not in mutable_tokens:
                mutable_tokens.append(token)
                updated = True

        if target_level == SanityLevel.NORMAL:
            remove_token('단기 광기')
            # 장기/영구/실종은 수동 해제 전까지 유지
        elif target_level == SanityLevel.SHORT:
            add_token('단기 광기')
            remove_token('장기 광기')
            remove_token('영구 광기')
            remove_token('실종')
        elif target_level == SanityLevel.LONG:
            remove_token('단기 광기')
            remove_token('영구 광기')
            remove_token('실종')
            add_token('장기 광기')
        elif target_level == SanityLevel.PERMANENT:
            remove_token('단기 광기')
            remove_token('장기 광기')
            add_token('영구 광기')
            add_token('실종')

        return mutable_tokens, updated

    def _build_health_messages(self, prev_level: HealthLevel, new_level: HealthLevel, current_hp: int) -> List[str]:
        messages = []
        if prev_level == new_level:
            return messages

        if HEALTH_STATUS_ORDER[new_level] > HEALTH_STATUS_ORDER[prev_level]:
            # 악화
            if new_level == HealthLevel.LIGHT:
                messages.append(f"체력이 70% 이하가 되어 '경상' 상태가 되었습니다.\n➭ 현재 체력 {current_hp}")
            elif new_level == HealthLevel.HEAVY:
                messages.append(f"체력이 20% 이하가 되어 '중상' 상태가 되었습니다.\n➭ 현재 체력 {current_hp}")
            elif new_level == HealthLevel.DEAD:
                messages.append(f"체력이 0 이하가 되어 '사망' 상태가 되었습니다.\n➭ 현재 체력 {current_hp}")
        else:
            # 회복 (자동 또는 수동)
            if new_level == HealthLevel.NORMAL:
                if prev_level == HealthLevel.LIGHT:
                    messages.append(f"체력이 70%를 초과하여 '경상'에서 '정상' 상태가 되었습니다.")
                elif prev_level == HealthLevel.HEAVY:
                    messages.append(f"체력이 70%를 초과하여 '중상'에서 '정상' 상태가 되었습니다.")
                elif prev_level == HealthLevel.DEAD:
                    messages.append(f"'사망' 상태가 해제되었습니다.\n➭ 현재 체력 {current_hp}")
            elif new_level == HealthLevel.LIGHT:
                if prev_level == HealthLevel.HEAVY:
                    messages.append(f"체력이 20%를 초과하여 '중상'에서 '경상' 상태가 되었습니다.")
                elif prev_level == HealthLevel.DEAD:
                    messages.append(f"'사망'에서 '경상' 상태로 회복되었습니다.\n➭ 현재 체력 {current_hp}")
            elif new_level == HealthLevel.HEAVY and prev_level == HealthLevel.DEAD:
                messages.append(f"'사망'에서 '중상' 상태로 회복되었습니다.\n➭ 현재 체력 {current_hp}")

        return messages

    def _build_sanity_messages(self, prev_level: SanityLevel, new_level: SanityLevel, current_sanity: int) -> List[str]:
        messages = []
        if prev_level == new_level:
            return messages

        if SANITY_STATUS_ORDER[new_level] > SANITY_STATUS_ORDER[prev_level]:
            # 악화
            if new_level == SanityLevel.SHORT:
                messages.append(f"정신력이 60% 이하가 되어 '단기 광기' 상태가 되었습니다.\n➭ 현재 정신력 {current_sanity}")
            elif new_level == SanityLevel.LONG:
                messages.append(f"정신력이 20% 이하가 되어 '장기 광기' 상태가 되었습니다.\n➭ 현재 정신력 {current_sanity}")
            elif new_level == SanityLevel.PERMANENT:
                messages.append(f"정신력이 0%가 되어 '영구 광기' 및 '실종' 상태가 되었습니다.")
        else:
            # 회복 (자동 또는 수동)
            if new_level == SanityLevel.NORMAL:
                if prev_level == SanityLevel.SHORT:
                    messages.append(f"정신력이 60%를 초과하여 '단기 광기'에서 '정상' 상태가 되었습니다.")
                elif prev_level == SanityLevel.LONG:
                    messages.append(f"'장기 광기' 상태가 해제되었습니다.\n➭ 현재 정신력 {current_sanity}")
                elif prev_level == SanityLevel.PERMANENT:
                    messages.append(f"'영구 광기' 및 '실종' 상태가 해제되었습니다.\n➭ 현재 정신력 {current_sanity}")
            elif new_level == SanityLevel.SHORT:
                if prev_level == SanityLevel.LONG:
                    messages.append(f"'장기 광기'에서 '단기 광기' 상태로 회복되었습니다.\n➭ 현재 정신력 {current_sanity}")
                elif prev_level == SanityLevel.PERMANENT:
                    messages.append(f"'영구 광기'에서 '단기 광기' 상태로 회복되었습니다.\n➭ 현재 정신력 {current_sanity}")
            elif new_level == SanityLevel.LONG and prev_level == SanityLevel.PERMANENT:
                messages.append(f"'영구 광기'에서 '장기 광기' 상태로 회복되었습니다.\n➭ 현재 정신력 {current_sanity}")

        return messages

    def _monitor_management_sheet(self, send_notifications: bool = True):
        """관리 시트 체력/정신력 모니터링

        Args:
            send_notifications: True면 DM 전송, False면 캐시만 업데이트 (초기 실행용)
        """
        if send_notifications:
            logger.info("🔍 관리 시트 모니터링 시작")
        else:
            logger.info("🔍 관리 시트 초기 상태 로드 중...")
        try:
            cached = self._load_cached_data(['관리'])
            management_data = cached['data']['관리']
            management_mapping = cached['mappings']['관리']

            required_headers = ['이름', '아이디', '최대 체력', '체력', '최대 정신력', '정신력', '상태이상']
            missing_headers = [h for h in required_headers if h not in management_mapping]
            if missing_headers:
                logger.error(f"관리 시트 필수 헤더 누락: {missing_headers}")
                return

            updates_by_sheet = {'관리': []}
            dm_messages: List[Tuple[str, str]] = []
            cache_updated = False
            current_time = self._get_current_time_kst().isoformat()

            for idx, row in enumerate(management_data):
                row_num = idx + 3  # 1행 헤더 + 2행 설명 + idx (3행부터 실제 데이터)
                name = (row.get('이름') or '').strip()
                user_id = (row.get('아이디') or '').strip()
                if not name or not user_id:
                    continue

                max_hp_raw = row.get('최대 체력')
                current_hp_raw = row.get('체력')
                max_sanity_raw = row.get('최대 정신력')
                current_sanity_raw = row.get('정신력')
                status_raw = (row.get('상태이상') or '').strip()

                max_hp = int(self._round_half_up(self._parse_numeric(max_hp_raw, default=0)))
                current_hp = self._round_half_up(self._parse_numeric(current_hp_raw, default=0))
                max_sanity = int(self._round_half_up(self._parse_numeric(max_sanity_raw, default=0)))
                current_sanity = self._floor_to_int(self._parse_numeric(current_sanity_raw, default=0))

                max_hp = max(0, max_hp)
                current_hp = max(0, min(current_hp, max_hp if max_hp > 0 else current_hp))
                max_sanity = max(0, max_sanity)
                current_sanity = max(0, min(current_sanity, max_sanity if max_sanity > 0 else current_sanity))

                # 시트의 현재 상태 토큰 파싱
                tokens = self._parse_status_tokens(status_raw)
                sheet_health_level = self._health_level_from_tokens(tokens)
                sheet_sanity_level = self._sanity_level_from_tokens(tokens)

                # 수치 기반으로 계산된 상태
                computed_health_level = self._determine_health_level(current_hp, max_hp)
                computed_sanity_level = self._determine_sanity_level(current_sanity, max_sanity)

                # 목표 상태 결정 (자동 관리 대상만)
                # 체력: 사망 상태는 수동 해제만 허용, 나머지는 자동 관리
                if sheet_health_level == HealthLevel.DEAD and computed_health_level != HealthLevel.DEAD:
                    # 사망 상태는 유지 (수동 해제 필요)
                    target_health_level = HealthLevel.DEAD
                else:
                    target_health_level = computed_health_level

                # 정신력: 장기/영구는 수동 해제만 허용, 단기는 자동 관리
                if sheet_sanity_level in (SanityLevel.LONG, SanityLevel.PERMANENT):
                    # 장기/영구 광기는 수동 해제 전까지 유지
                    if computed_sanity_level in (SanityLevel.LONG, SanityLevel.PERMANENT):
                        # 수치적으로도 장기/영구면 계산값 사용
                        target_sanity_level = computed_sanity_level
                    else:
                        # 수치는 회복됐지만 수동 해제 전까지 유지
                        target_sanity_level = sheet_sanity_level
                else:
                    target_sanity_level = computed_sanity_level

                prev_state_data = self.management_status_cache.get(user_id)
                prev_state = None
                has_cache = isinstance(prev_state_data, dict)

                if has_cache:
                    prev_health = prev_state_data.get('health', HealthLevel.NORMAL.value)
                    prev_sanity = prev_state_data.get('sanity', SanityLevel.NORMAL.value)
                    prev_state = ManagementState(
                        health=prev_health,
                        sanity=prev_sanity,
                        last_seen=prev_state_data.get('last_seen', ''),
                        name=prev_state_data.get('name', '')
                    )
                else:
                    # 캐시가 없을 때는 정상 상태를 이전 상태로 사용 (상태이상 발생 감지를 위해)
                    prev_state = ManagementState(
                        health=HealthLevel.NORMAL.value,
                        sanity=SanityLevel.NORMAL.value,
                        last_seen='',
                        name=name
                    )

                prev_health_level = HealthLevel(prev_state.health) if prev_state.health in HealthLevel._value2member_map_ else HealthLevel.NORMAL
                prev_sanity_level = SanityLevel(prev_state.sanity) if prev_state.sanity in SanityLevel._value2member_map_ else SanityLevel.NORMAL

                # 상태 변화 감지 로직
                # 1) 캐시와 시트 비교 (수동 변경 감지)
                sheet_manually_changed = (prev_health_level != sheet_health_level or prev_sanity_level != sheet_sanity_level)

                # 2) 시트와 목표 상태 비교 (자동 업데이트 필요 여부)
                # 토큰 업데이트 (시트 업데이트용)
                new_tokens, health_updated = self._update_status_tokens_for_health(tokens, target_health_level)
                new_tokens, sanity_updated = self._update_status_tokens_for_sanity(new_tokens, target_sanity_level)
                status_changed = health_updated or sanity_updated

                # DM 발송 기준: 캐시 기준으로 상태가 변했거나, 수동으로 변경된 경우
                # 수동 변경의 경우 시트의 현재 상태를 사용, 자동 변경의 경우 목표 상태 사용
                if sheet_manually_changed:
                    # 수동 변경: 시트 상태 기준으로 DM 발송
                    health_state_changed = (prev_health_level != sheet_health_level)
                    sanity_state_changed = (prev_sanity_level != sheet_sanity_level)
                    dm_health_level = sheet_health_level
                    dm_sanity_level = sheet_sanity_level
                else:
                    # 자동 변경: 목표 상태 기준으로 DM 발송
                    health_state_changed = (prev_health_level != target_health_level)
                    sanity_state_changed = (prev_sanity_level != target_sanity_level)
                    dm_health_level = target_health_level
                    dm_sanity_level = target_sanity_level

                skip_health_dm = False
                skip_sanity_dm = False

                if prev_health_level == HealthLevel.DEAD and target_health_level != HealthLevel.DEAD:
                    skip_health_dm = True
                if prev_sanity_level in (SanityLevel.LONG, SanityLevel.PERMANENT) and SANITY_STATUS_ORDER[target_sanity_level] < SANITY_STATUS_ORDER[prev_sanity_level]:
                    skip_sanity_dm = True

                messages_to_send: List[str] = []
                if prev_state and prev_state.name:
                    name = prev_state.name

                # 캐시 기준 상태 변화가 있으면 알림 전송
                if health_state_changed or sanity_state_changed:
                    health_messages = []
                    sanity_messages = []

                    if health_state_changed and not skip_health_dm:
                        health_messages = self._build_health_messages(prev_health_level, dm_health_level, current_hp)

                    if sanity_state_changed and not skip_sanity_dm:
                        sanity_messages = self._build_sanity_messages(prev_sanity_level, dm_sanity_level, current_sanity)

                    messages_to_send.extend(health_messages)
                    messages_to_send.extend(sanity_messages)

                if status_changed:
                    status_col = management_mapping.get('상태이상')
                    if status_col:
                        updated_status_str = self._join_status_tokens(new_tokens)
                        updates_by_sheet['관리'].append({
                            'range': f'{status_col}{row_num}',
                            'values': [[updated_status_str]]
                        })

                # DM 전송 여부 확인
                if messages_to_send and send_notifications:
                    full_message = '\n'.join(messages_to_send)
                    dm_messages.append((user_id, full_message))

                # 캐시 업데이트: 최종 상태 저장 (수동 변경이면 시트 상태, 아니면 목표 상태)
                final_health_level = dm_health_level if sheet_manually_changed else target_health_level
                final_sanity_level = dm_sanity_level if sheet_manually_changed else target_sanity_level

                if (prev_state.health != final_health_level.value or
                        prev_state.sanity != final_sanity_level.value or
                        prev_state.name != name):
                    self.management_status_cache[user_id] = {
                        'health': final_health_level.value,
                        'sanity': final_sanity_level.value,
                        'last_seen': current_time,
                        'name': name
                    }
                    self._status_cache_dirty = True
                    cache_updated = True

            if updates_by_sheet['관리']:
                self._execute_batch_updates(updates_by_sheet)
                self._invalidate_cache(['관리'])

            if dm_messages and send_notifications:
                self._send_bulk_dm(dm_messages)
                logger.info(f"📬 상태 변화 감지: {len(dm_messages)}명에게 알림 전송")

            if not send_notifications and cache_updated:
                logger.info(f"✅ 초기 상태 로드 완료: {len([k for k in self.management_status_cache.keys()])}명")

            if cache_updated:
                self._save_status_cache()

        except Exception as e:
            logger.error(f"관리 시트 모니터링 오류: {e}")
            raise

    def run(self):
        """봇 실행"""
        logger.info("🤖 자동봇 시작")

        try:
            # 초기 연결 테스트
            if not self._health_check():
                raise Exception("초기 헬스체크 실패")
            logger.info("✅ 초기 연결 테스트 성공")

            # 상태 설정
            self.status = BotStatus.RUNNING
            self.start_time = time.time()

            # 헬스 모니터 시작
            self._start_health_monitor()

            # 스케줄 설정
            monitor_interval = int(os.getenv('MONITOR_INTERVAL_MINUTES', '30'))
            schedule.every(monitor_interval).minutes.do(self.management_monitor_job)

            logger.info(f"⏰ 스케줄: {monitor_interval}분마다 관리 시트 모니터링")

            # 시작 시 즉시 관리 시트 모니터링 실행 (DM 전송 없이 캐시만 로드)
            logger.info("📊 초기 관리 시트 상태 확인 중...")
            try:
                self.management_monitor_job(send_notifications=False)
            except Exception as e:
                logger.error(f"⚠️ 초기 모니터링 실패 (계속 진행): {e}")

            logger.info(f"✅ 봇 준비 완료 (상태: {self.status.value})")

            # 메인 루프
            while not self.shutdown_requested:
                try:
                    # 상태 파일 업데이트 (5분마다)
                    if hasattr(self, 'last_status_update') and time.time() - self.last_status_update > 300:
                        self._create_status_file()
                        self.last_status_update = time.time()
                    elif not hasattr(self, 'last_status_update'):
                        self.last_status_update = time.time()

                    # 재시작이 필요한지 체크
                    self._restart_bot_if_needed()

                    if self.shutdown_requested:
                        break

                    # 스케줄러 실행
                    if self.status != BotStatus.CRITICAL_ERROR:
                        schedule.run_pending()
                    else:
                        logger.warning("⚠️ 치명적 오류 상태, 스케줄 실행 중단")
                        time.sleep(60)
                        continue

                except Exception as e:
                    logger.error(f"메인 루프 오류: {e}")
                    logger.error(f"Traceback: {traceback.format_exc()}")

                    # 치명적 오류가 아닌 경우 계속 실행
                    if not self._is_critical_error(e):
                        time.sleep(30)
                    else:
                        logger.error("❌ 치명적 오류 감지")
                        self.status = BotStatus.CRITICAL_ERROR

                time.sleep(60)  # 1분마다 체크

            logger.info("👋 봇 정상 종료")

        except Exception as e:
            logger.critical(f"봇 치명적 오류: {e}")
            logger.critical(f"Traceback: {traceback.format_exc()}")
            self.status = BotStatus.CRITICAL_ERROR
            raise
        finally:
            # 정리 작업
            self.main_thread_active = False
            if self.health_check_thread and self.health_check_thread.is_alive():
                self.health_check_thread.join(timeout=5)

            # 최종 상태 파일 업데이트
            self._create_status_file()
            self._save_status_cache()

    def _is_critical_error(self, exception) -> bool:
        """치명적 오류인지 판별"""
        critical_indicators = [
            'authentication', 'permission denied', 'access denied',
            'invalid credentials', 'quota exceeded', 'rate limit exceeded'
        ]
        error_str = str(exception).lower()
        return any(indicator in error_str for indicator in critical_indicators)


def main():
    """메인 함수 with 재시작 로직"""
    restart_attempts = 0
    max_main_restarts = 5

    while restart_attempts < max_main_restarts:
        try:
            if restart_attempts > 0:
                logger.info(f"🔄 봇 재시작 (시도 {restart_attempts + 1}/{max_main_restarts})")
            bot = AutoBot()
            bot.run()

            # 정상 종료인 경우
            if bot.shutdown_requested:
                logger.info("👋 정상 종료 요청")
                return 0

            # 비정상 종료인 경우 재시작
            restart_attempts += 1
            if restart_attempts < max_main_restarts:
                wait_time = min(300 * restart_attempts, 1800)  # 최대 30분
                logger.warning(f"⏳ {wait_time}초 후 재시작 ({restart_attempts}/{max_main_restarts})")
                time.sleep(wait_time)

        except KeyboardInterrupt:
            logger.info("👋 사용자 인터럽트")
            return 0
        except Exception as e:
            logger.critical(f"❌ 봇 치명적 오류: {e}")
            logger.critical(f"Traceback: {traceback.format_exc()}")

            restart_attempts += 1
            if restart_attempts < max_main_restarts:
                wait_time = min(600 * restart_attempts, 3600)  # 최대 1시간
                logger.warning(f"⏳ 치명적 오류 후 {wait_time}초 대기 후 재시도")
                time.sleep(wait_time)

    logger.critical(f"❌ 최대 재시작 횟수 ({max_main_restarts}) 초과, 봇 종료")
    return 1


if __name__ == "__main__":
    # 로깅 레벨 설정
    log_level = os.getenv('LOG_LEVEL', 'INFO').upper()
    if hasattr(logging, log_level):
        logger.setLevel(getattr(logging, log_level))

    logger.info("=" * 60)
    logger.info("🤖 마스토돈 + Google Sheets 자동봇")
    logger.info(f"📌 PID: {os.getpid()}")
    logger.info("=" * 60)

    exit_code = main()
    logger.info("=" * 60)
    logger.info(f"👋 봇 종료 (Exit Code: {exit_code})")
    logger.info("=" * 60)
    exit(exit_code)


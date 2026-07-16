#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Mastodon + Google Sheets 자동봇 (shinarmont)

매시 정각(00분)마다 '관리' 워크시트의 '건강'/'이성' 수치를 확인하고,
임계값 이하로 떨어진 캐릭터에게 마스토돈 DM(direct)을 전송한다.

- 이성 <= 50: '이성' 워크시트의 '이성문구1' 전송 후 '문구1 발송여부'에 O 기록
- 이성 <= 30: '이성' 워크시트의 '이성문구2' 전송 후 '문구2 발송여부'에 O 기록
- 건강 <= 50: 고정 문구 전송 (발송여부는 로컬 캐시로 관리)
- 건강 <= 20: 고정 문구 전송 (발송여부는 로컬 캐시로 관리)

각 알림은 한 번만 발송한다(회복돼도 재발송하지 않음).
DM 수신자는 '관리' 워크시트의 '아이디'를 참조한다.
"""

import os
import sys
import time
import math
import logging
import signal
import threading
import traceback
import json
import random
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any
from enum import Enum

import schedule
import gspread
from mastodon import Mastodon
from dotenv import load_dotenv
import pytz

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

# 워크시트 이름
MANAGE_WORKSHEET = os.getenv('MANAGE_WORKSHEET', '관리')
SANITY_WORKSHEET = os.getenv('SANITY_WORKSHEET', '이성')

# 데이터 시작 행 (1행: 헤더, 2행: 설명, 3행부터 실제 데이터)
DATA_START_ROW = 3

# '관리' 워크시트 헤더
COL_NAME = '이름'
COL_ID = '아이디'
COL_HEALTH = '건강'
COL_SANITY = '이성'

# '이성' 워크시트 헤더
COL_S_NAME = '이름'
COL_S_MSG1 = '이성문구1'
COL_S_FLAG1 = '문구1 발송여부'
COL_S_MSG2 = '이성문구2'
COL_S_FLAG2 = '문구2 발송여부'

# 임계값
SANITY_THRESHOLD_1 = 50  # 이성문구1
SANITY_THRESHOLD_2 = 30  # 이성문구2
HEALTH_THRESHOLD_1 = 50  # 건강 문구1
HEALTH_THRESHOLD_2 = 20  # 건강 문구2

# 건강 고정 문구
HEALTH_MSG_1 = '◎ 건강이 50 이하로 떨어졌다. 처치를 하지 않는다면 곤란해질지도 모른다.'
HEALTH_MSG_2 = '건강이 20 이하로 떨어졌다. 추적과 조사 등의 활동을 위해서는 처치가 먼저 필요하다.'

# 이성 문구(시트에서 읽어온 내용) 앞에 자동으로 붙이는 프리픽스
SANITY_MSG_PREFIX = '◎ '

# 발송여부 셀에 기록할 값
SENT_MARK = 'O'


class BotStatus(Enum):
    """봇 상태 열거형"""
    STARTING = "starting"
    RUNNING = "running"
    RECOVERING = "recovering"
    CRITICAL_ERROR = "critical_error"
    SHUTDOWN = "shutdown"


class CircuitBreakerState(Enum):
    """회로 차단기 상태"""
    CLOSED = "closed"        # 정상 작동
    OPEN = "open"            # 차단됨
    HALF_OPEN = "half_open"  # 복구 시도


class CircuitBreaker:
    """회로 차단기: 연속 실패 시 일정 시간 호출을 차단한다."""

    def __init__(self, failure_threshold: int = 5, timeout: int = 300):
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.failure_count = 0
        self.last_failure_time = 0
        self.state = CircuitBreakerState.CLOSED

    def call(self, func, *args, **kwargs):
        if self.state == CircuitBreakerState.OPEN:
            if time.time() - self.last_failure_time >= self.timeout:
                self.state = CircuitBreakerState.HALF_OPEN
                logger.info("회로 차단기 HALF_OPEN 전환, 복구 시도")
            else:
                raise Exception("회로 차단기 OPEN 상태 (호출 차단)")

        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except Exception:
            self._on_failure()
            raise

    def _on_success(self):
        if self.state == CircuitBreakerState.HALF_OPEN:
            logger.info("회로 차단기 CLOSED 복구")
        self.failure_count = 0
        self.state = CircuitBreakerState.CLOSED

    def _on_failure(self):
        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.failure_count >= self.failure_threshold:
            self.state = CircuitBreakerState.OPEN
            logger.warning(f"⚠️ 회로 차단기 OPEN (연속 실패 {self.failure_count}회)")


class AutoBot:
    def __init__(self):
        self.gc = None
        self.mastodon = None
        self.manage_ss = None   # '관리' 워크시트가 있는 스프레드시트
        self.sanity_ss = None   # '이성' 워크시트가 있는 스프레드시트
        self.spreadsheet = None  # 헬스체크 호환용 별칭 (= manage_ss)

        self.status = BotStatus.STARTING
        self.consecutive_failures = 0
        self.last_successful_operation = time.time()
        self.restart_count = 0
        self.shutdown_requested = False

        # 발송여부 로컬 캐시 (건강 알림 중복 방지)
        self.status_cache_file = 'management_status_cache.json'
        self.management_status_cache: Dict[str, Dict[str, Any]] = {}
        self._status_cache_loaded = False
        self._status_cache_dirty = False

        # Google Sheets API 레이트 리미팅
        self._last_api_call_time = 0
        self._api_call_count = 0
        self._quota_reset_time = time.time()
        self._backoff_delay = BASE_API_DELAY

        # API 클라이언트/레이트리밋 상태 보호 락 (헬스 스레드 ↔ 메인 루프 경쟁 방지, 재진입 허용)
        self._api_lock = threading.RLock()

        # 회로 차단기
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
            signal.signal(signal.SIGUSR1, self._reload_config_handler)

        self._initialize_apis()
        self._load_status_cache()

    # ==================== 시그널 / 설정 리로드 ====================

    def _signal_handler(self, signum, frame):
        logger.info(f"시그널 {signum} 수신, 안전한 종료 시작...")
        self.shutdown_requested = True
        self.status = BotStatus.SHUTDOWN
        self.main_thread_active = False

    def _reload_config_handler(self, signum, frame):
        logger.info("설정 리로드 시그널 수신")
        try:
            self._reload_configuration()
        except Exception as e:
            logger.error(f"설정 리로드 실패: {e}")

    def _reload_configuration(self):
        logger.info("⚙️ 설정 리로드 시작")
        if os.path.exists('.env'):
            load_dotenv(override=True)
            logger.debug(".env 파일 리로드 완료")
        try:
            old_status = self.status
            self.status = BotStatus.RECOVERING
            self._initialize_apis()
            self.status = old_status if old_status != BotStatus.CRITICAL_ERROR else BotStatus.RUNNING
            logger.info("✅ 설정 리로드 완료")
        except Exception as e:
            logger.error(f"API 재초기화 실패: {e}")
            self.status = BotStatus.CRITICAL_ERROR

    # ==================== 레이트 리미팅 / API 래퍼 ====================

    def _wait_for_rate_limit(self):
        current_time = time.time()

        # 매분 초기화 (1분 윈도우)
        if current_time - self._quota_reset_time >= 60:
            self._api_call_count = 0
            self._quota_reset_time = current_time
            self._backoff_delay = BASE_API_DELAY

        time_since_last_call = current_time - self._last_api_call_time
        if time_since_last_call < self._backoff_delay:
            sleep_time = self._backoff_delay - time_since_last_call
            jitter = sleep_time * 0.1 * random.random()
            sleep_time += jitter
            logger.debug(f"API 레이트 리미팅 대기: {sleep_time:.2f}초")
            time.sleep(sleep_time)

        self._last_api_call_time = time.time()
        self._api_call_count += 1

    def _handle_quota_exceeded(self):
        logger.warning(f"⚠️ Google Sheets API 쿼터 초과, {QUOTA_EXCEEDED_DELAY}초 대기")
        self._backoff_delay = min(self._backoff_delay * 2, MAX_BACKOFF_DELAY)
        logger.debug(f"백오프 딜레이 증가: {self._backoff_delay:.2f}초")
        self._interruptible_sleep(QUOTA_EXCEEDED_DELAY)
        self._api_call_count = 0
        self._quota_reset_time = time.time()

    @staticmethod
    def _is_retriable_api_error(e) -> bool:
        """쿼터/레이트리밋/일시적 5xx 등 재시도 가치가 있는 APIError인지 판별"""
        code = getattr(getattr(e, 'response', None), 'status_code', None)
        if code in (429, 500, 502, 503, 504):
            return True
        s = str(e).lower()
        return any(k in s for k in ('quota exceeded', 'rate limit', 'internal error',
                                    'backend error', 'try again', 'unavailable'))

    def _interruptible_sleep(self, seconds: float):
        """shutdown 요청 시 조기 종료하는 분할 sleep (대기 중 종료 신호 반영)"""
        remaining = float(seconds)
        while remaining > 0:
            if self.shutdown_requested:
                return
            chunk = 1.0 if remaining > 1.0 else remaining
            time.sleep(chunk)
            remaining -= chunk

    def _sheets_api_call(self, operation, *args, **kwargs):
        """Google Sheets API 호출 래퍼 (레이트 리미팅 + 스레드 락 포함)"""
        max_attempts = 3
        with self._api_lock:
            for attempt in range(max_attempts):
                try:
                    self._wait_for_rate_limit()
                    result = operation(*args, **kwargs)
                    if self._backoff_delay > BASE_API_DELAY:
                        self._backoff_delay = max(BASE_API_DELAY, self._backoff_delay * 0.8)
                    return result
                except gspread.exceptions.APIError as e:
                    if self._is_retriable_api_error(e):
                        self._handle_quota_exceeded()
                        if attempt == max_attempts - 1:
                            raise
                        continue
                    raise
                except Exception as e:
                    if attempt < max_attempts - 1:
                        wait_time = (attempt + 1) * 2
                        logger.debug(f"API 호출 실패, {wait_time}초 후 재시도: {e}")
                        self._interruptible_sleep(wait_time)
                        continue
                    raise
            raise Exception(f"API 호출 최대 재시도 횟수 초과: {max_attempts}")

    # ==================== API 초기화 ====================

    def _initialize_apis(self):
        """API 클라이언트 초기화 (두 스프레드시트 + 마스토돈)"""
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

                manage_id = os.getenv('MANAGE_SHEET_ID')
                sanity_id = os.getenv('SANITY_SHEET_ID')
                if not manage_id:
                    raise ValueError("MANAGE_SHEET_ID가 설정되지 않았습니다.")
                if not sanity_id:
                    raise ValueError("SANITY_SHEET_ID가 설정되지 않았습니다.")

                try:
                    manage_ss = self.gc.open_by_key(manage_id)
                    sanity_ss = self.gc.open_by_key(sanity_id)
                    # 사용 중 스왑 경쟁 방지: 락 안에서 원자적으로 교체
                    with self._api_lock:
                        self.manage_ss = manage_ss
                        self.spreadsheet = manage_ss  # 헬스체크 호환용
                        self.sanity_ss = sanity_ss
                except gspread.SpreadsheetNotFound as e:
                    raise ValueError(f"스프레드시트를 찾을 수 없습니다: {e}")
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

                mastodon = Mastodon(access_token=access_token, api_base_url=api_base_url)
                with self._api_lock:
                    self.mastodon = mastodon

                logger.info("✅ API 클라이언트 초기화 완료")
                return

            except Exception as e:
                logger.error(f"API 초기화 실패 (시도 {attempt + 1}): {e}")
                if attempt == max_init_attempts - 1:
                    raise
                time.sleep(60)

    # ==================== 재시도 / 재연결 ====================

    def _retry_operation(self, operation, *args, **kwargs):
        last_exception = None
        for attempt in range(MAX_RETRIES):
            try:
                result = operation(*args, **kwargs)
                self.consecutive_failures = 0
                self.last_successful_operation = time.time()
                return result
            except Exception as e:
                last_exception = e
                logger.debug(f"작업 실패 (시도 {attempt + 1}/{MAX_RETRIES}): {e}")
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
                delay = min(RETRY_DELAY * (2 ** attempt), 300)
                self._interruptible_sleep(delay)
                if self.shutdown_requested:
                    raise last_exception
        raise last_exception

    def _is_network_error(self, exception) -> bool:
        error_indicators = [
            'connection', 'timeout', 'network', 'dns', 'socket',
            'http', 'ssl', 'certificate', 'unreachable'
        ]
        error_str = str(exception).lower()
        return any(indicator in error_str for indicator in error_indicators)

    def _attempt_api_reconnection(self):
        try:
            logger.warning("🔄 API 재연결 시도 중...")
            self.status = BotStatus.RECOVERING
            time.sleep(30)
            self._initialize_apis()
            logger.info("✅ API 재연결 성공")
            self.status = BotStatus.RUNNING
        except Exception as e:
            logger.error(f"API 재연결 실패: {e}")
            self.status = BotStatus.CRITICAL_ERROR

    # ==================== Mastodon DM ====================

    def _send_dm(self, user_id: str, message: str) -> bool:
        if not user_id or not message:
            logger.debug(f"DM 전송 실패 - 빈 데이터: user_id='{user_id}'")
            return False
        if not self.mastodon:
            logger.error("Mastodon 클라이언트가 초기화되지 않았습니다")
            return False

        def send_dm_operation():
            max_length = 450  # @user_id 등을 위한 여유 공간
            message_to_send = message if len(message) <= max_length else message[:max_length] + "..."
            return self.mastodon.status_post(
                status=f"@{user_id} {message_to_send}",
                visibility='direct'
            )

        try:
            with self._api_lock:
                self.mastodon_circuit_breaker.call(send_dm_operation)
            logger.info(f"📨 DM 전송: @{user_id}")
            return True
        except Exception as e:
            logger.error(f"DM 전송 실패 ({user_id}): {e}")
            return False

    def _deliver_alerts(self, alerts: List[Dict[str, Any]], sanity_ws, current_time: str):
        """알림 전송 후, 실제로 전송에 성공한 건에 대해서만 발송여부를 기록한다.

        핵심 원칙: '전송 시도 → 성공 확인 → 발송여부 기록' 순서.
        - 전송 실패 시 발송여부(시트 O / 캐시 플래그)를 기록하지 않아 다음 실행에 재시도된다.
        - 각 알림은 개별 DM으로 전송하여 발송여부 기록과 1:1로 대응시킨다(병합/절단으로 인한 유실 방지).
        """
        if not alerts:
            logger.info("✅ 임계값 이하 신규 대상 없음 (전송할 알림 없음)")
            return

        sanity_updates: List[Dict[str, Any]] = []
        sent_ok = 0
        sent_fail = 0

        for a in alerts:
            delivered = self._send_dm(a['user_id'], a['message'])
            if not delivered:
                sent_fail += 1
                logger.warning(f"⚠️ 전송 실패 → 발송여부 미기록(다음 실행에 재시도): "
                               f"{a['name']}(@{a['user_id']}) {a.get('label', '')}")
                continue

            sent_ok += 1
            # 전송 성공분만 발송여부 기록
            if a.get('sheet_update'):
                sanity_updates.append(a['sheet_update'])
            if a.get('cache_key'):
                entry = self.management_status_cache.get(a['user_id'])
                entry = entry if isinstance(entry, dict) else {}
                entry[a['cache_key']] = True
                entry['name'] = a['name']
                entry['last_seen'] = current_time
                self.management_status_cache[a['user_id']] = entry
                self._status_cache_dirty = True

            if sent_ok % 10 == 0:
                time.sleep(1)  # API 완화

        # 전송 성공한 이성 알림만 '이성' 시트 발송여부 O 기록 (배치)
        if sanity_updates:
            try:
                chunk_size = 50
                for j in range(0, len(sanity_updates), chunk_size):
                    chunk = sanity_updates[j:j + chunk_size]
                    self._sheets_api_call(
                        sanity_ws.batch_update, chunk, value_input_option='USER_ENTERED'
                    )
                logger.info(f"📝 이성 발송여부 {len(sanity_updates)}건 기록 완료")
            except Exception as e:
                # 전송은 성공했으나 기록 실패 → 다음 실행에 중복 발송 가능(영구 손실보다 안전한 방향)
                logger.error(f"이성 발송여부 기록 실패(다음 실행 중복 발송 가능): {e}")

        # 건강 캐시 저장 (전송 성공분만 반영됨)
        self._save_status_cache()
        logger.info(f"📬 알림 처리 완료: 전송 성공 {sent_ok}건 / 실패 {sent_fail}건 "
                    f"/ 이성기록 {len(sanity_updates)}건")

    # ==================== 헬스체크 / 상태 파일 ====================

    def _health_check(self) -> bool:
        try:
            test_sheet = self.manage_ss.worksheet(MANAGE_WORKSHEET)
            self._sheets_api_call(test_sheet.get, 'A1')
            self.mastodon.account_verify_credentials()
            time_since_last_success = time.time() - self.last_successful_operation
            if time_since_last_success > 10800:  # 3시간
                logger.warning(f"⚠️ 마지막 성공적 작업에서 {time_since_last_success/60:.1f}분 경과")
            return True
        except Exception as e:
            logger.error(f"헬스체크 실패: {e}")
            return False

    def _start_health_monitor(self):
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

    def _create_status_file(self):
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

    def _safe_operation_wrapper(self, operation_name: str, operation_func):
        if self.shutdown_requested:
            return
        if self.status == BotStatus.CRITICAL_ERROR:
            logger.warning(f"⚠️ {operation_name} 작업 건너뛰기 (치명적 오류 상태)")
            return
        try:
            operation_func()
            if self.status != BotStatus.RUNNING:
                self.status = BotStatus.RUNNING
                logger.info("✅ 봇 상태 복구: RUNNING")
        except Exception as e:
            logger.error(f"{operation_name} 작업 오류: {e}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            if self.status == BotStatus.RUNNING:
                self.status = BotStatus.RECOVERING

    def _restart_bot_if_needed(self):
        if self.status != BotStatus.CRITICAL_ERROR:
            return
        if self.restart_count >= MAX_RESTART_ATTEMPTS:
            logger.critical("❌ 최대 재시작 시도 횟수 초과, 봇 종료")
            self.shutdown_requested = True
            return
        self.restart_count += 1
        logger.warning(f"🔄 봇 재시작 시도 {self.restart_count}/{MAX_RESTART_ATTEMPTS}")
        try:
            time.sleep(CRITICAL_ERROR_RESTART_DELAY)
            self._initialize_apis()
            self.status = BotStatus.RUNNING
            self.consecutive_failures = 0
            self.last_successful_operation = time.time()
            self.sheets_circuit_breaker = CircuitBreaker(failure_threshold=3, timeout=300)
            self.mastodon_circuit_breaker = CircuitBreaker(failure_threshold=5, timeout=180)
            logger.info("✅ 봇 재시작 성공")
        except Exception as e:
            logger.error(f"봇 재시작 실패: {e}")
            time.sleep(CRITICAL_ERROR_RESTART_DELAY * 2)

    # ==================== 발송여부 로컬 캐시 (건강용) ====================

    def _load_status_cache(self):
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
                logger.error("⚠️ 상태 캐시 형식 오류(dict 아님) → 손상 파일 보관 후 새로 시작")
                self._quarantine_cache_file()
                self.management_status_cache = {}
        except Exception as e:
            # 파싱 실패: 손상 파일을 보관(증거 보존)하고 새로 시작.
            # 주의: 건강 발송여부가 초기화되어 현재 임계값 이하 대상에게 중복 발송될 수 있음.
            logger.error(f"⚠️ 상태 캐시 로드 실패 → 손상 파일 보관 후 새로 시작(건강 알림 중복 발송 가능): {e}")
            self._quarantine_cache_file()
            self.management_status_cache = {}
        self._status_cache_loaded = True
        self._status_cache_dirty = False

    def _quarantine_cache_file(self):
        """손상된 캐시 파일을 .corrupt로 옮겨 증거를 보존한다(무음 삭제 방지)."""
        try:
            if os.path.exists(self.status_cache_file):
                dst = self.status_cache_file + '.corrupt'
                os.replace(self.status_cache_file, dst)
                logger.error(f"손상된 캐시 파일 보관: {dst}")
        except Exception as e:
            logger.error(f"손상 캐시 파일 보관 실패: {e}")

    def _save_status_cache(self):
        if not self._status_cache_dirty:
            return
        try:
            # 원자적 저장: 임시파일에 기록 후 교체(중간 크래시로 인한 손상 방지)
            tmp = self.status_cache_file + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as cache_file:
                json.dump(self.management_status_cache, cache_file, ensure_ascii=False, indent=2)
                cache_file.flush()
                try:
                    os.fsync(cache_file.fileno())
                except OSError:
                    pass
            os.replace(tmp, self.status_cache_file)
            self._status_cache_dirty = False
        except Exception as e:
            logger.error(f"상태 캐시 저장 실패: {e}")

    # ==================== 유틸 ====================

    def _get_current_time_kst(self) -> datetime:
        return datetime.now(KST)

    @staticmethod
    def _column_index_to_letter(col_index: int) -> str:
        """0-based 컬럼 인덱스를 A1 표기 문자로 변환 (0=A, 25=Z, 26=AA)"""
        result = ""
        col_index += 1
        while col_index > 0:
            col_index -= 1
            result = chr(ord('A') + (col_index % 26)) + result
            col_index //= 26
        return result

    @staticmethod
    def _header_index(headers: List[str], name: str) -> int:
        try:
            return headers.index(name)
        except ValueError:
            return -1

    @staticmethod
    def _parse_number(raw) -> Optional[float]:
        """수치 파싱. 빈 값/비수치/무한대·NaN은 None(판정 건너뜀).

        임계값 비교는 실수 그대로 수행하여 '50.5'가 50 이하로 오판되지 않도록 한다.
        ('50.5' → 50.5, '0' → 0.0, 'inf'/'nan'/'abc'/'' → None)
        """
        if raw is None:
            return None
        if isinstance(raw, bool):
            return None
        if isinstance(raw, (int, float)):
            return float(raw) if math.isfinite(raw) else None
        s = str(raw).replace(',', '').strip()
        if s == '':
            return None
        try:
            value = float(s)
        except (ValueError, TypeError, OverflowError):
            logger.debug(f"수치 변환 실패, 판정 건너뜀: {raw!r}")
            return None
        if not math.isfinite(value):
            logger.debug(f"비유한 수치(inf/nan), 판정 건너뜀: {raw!r}")
            return None
        return value

    @staticmethod
    def _is_marked(flag_value) -> bool:
        """발송여부 셀이 이미 표시돼 있는지 (비어있지 않으면 발송된 것으로 간주)"""
        return bool(str(flag_value or '').strip())

    # ==================== 도메인: 건강/이성 모니터링 ====================

    def monitor_job(self):
        """매시 정각 실행되는 모니터링 작업 (안전 래퍼 경유)"""
        self._safe_operation_wrapper("건강/이성 모니터링", self._check_and_notify)

    def _check_and_notify(self):
        """'관리'의 건강/이성을 확인하고 임계값 이하 캐릭터에게 DM 전송"""
        logger.info("🔍 건강/이성 모니터링 시작")

        # ---- 1) '관리' 워크시트 읽기 (건강/이성/아이디) ----
        manage_ws = self.manage_ss.worksheet(MANAGE_WORKSHEET)
        manage_values = self._sheets_api_call(manage_ws.get_all_values)
        if len(manage_values) < DATA_START_ROW:
            logger.info("관리 시트에 데이터 행이 없습니다.")
            return

        m_headers = manage_values[0]
        m_name = self._header_index(m_headers, COL_NAME)
        m_id = self._header_index(m_headers, COL_ID)
        m_health = self._header_index(m_headers, COL_HEALTH)
        m_sanity = self._header_index(m_headers, COL_SANITY)

        missing = [h for h, idx in [(COL_NAME, m_name), (COL_ID, m_id),
                                    (COL_HEALTH, m_health), (COL_SANITY, m_sanity)] if idx < 0]
        if missing:
            logger.error(f"관리 시트 필수 헤더 누락: {missing} (실제 헤더: {m_headers})")
            return

        # ---- 2) '이성' 워크시트 읽기 (실패해도 건강 알림은 계속 진행) ----
        sanity_ws = None
        sanity_ok = False
        s_name = s_msg1 = s_flag1 = s_msg2 = s_flag2 = -1
        sanity_by_name: Dict[str, Tuple[int, List[str]]] = {}
        try:
            sanity_ws = self.sanity_ss.worksheet(SANITY_WORKSHEET)
            sanity_values = self._sheets_api_call(sanity_ws.get_all_values)
            s_headers = sanity_values[0] if sanity_values else []
            s_name = self._header_index(s_headers, COL_S_NAME)
            s_msg1 = self._header_index(s_headers, COL_S_MSG1)
            s_flag1 = self._header_index(s_headers, COL_S_FLAG1)
            s_msg2 = self._header_index(s_headers, COL_S_MSG2)
            s_flag2 = self._header_index(s_headers, COL_S_FLAG2)

            sanity_ok = all(idx >= 0 for idx in [s_name, s_msg1, s_flag1, s_msg2, s_flag2])
            if not sanity_ok:
                logger.error(f"이성 시트 헤더 누락 (실제 헤더: {s_headers}) — 이성 알림은 건너뜁니다.")
            else:
                # 이름 -> (sheet_row_num, row_values) 매핑 (중복 이름은 경고 후 마지막 행 사용)
                for i, row in enumerate(sanity_values):
                    row_num = i + 1  # 1-based 실제 행 번호
                    if row_num < DATA_START_ROW:
                        continue
                    nm = (row[s_name] if s_name < len(row) else '').strip()
                    if not nm:
                        continue
                    if nm in sanity_by_name:
                        logger.warning(f"⚠️ '이성' 시트 중복 이름 '{nm}' "
                                       f"(행 {sanity_by_name[nm][0]} → {row_num}), 마지막 행 사용")
                    sanity_by_name[nm] = (row_num, row)
        except Exception as e:
            logger.error(f"'이성' 시트 읽기 실패 → 이성 알림 건너뜀(건강 알림은 계속): {e}")
            sanity_ok = False

        alerts: List[Dict[str, Any]] = []
        current_time = self._get_current_time_kst().isoformat()

        def cell(row: List[str], idx: int) -> str:
            return row[idx] if 0 <= idx < len(row) else ''

        # ---- 3) 관리 시트 각 행 처리: 알림 목록 구성 (아직 전송하지 않음) ----
        for i in range(DATA_START_ROW - 1, len(manage_values)):
            row = manage_values[i]
            name = cell(row, m_name).strip()
            user_id = cell(row, m_id).strip()
            if not name or not user_id:
                continue

            hp = self._parse_number(cell(row, m_health))
            sanity = self._parse_number(cell(row, m_sanity))

            # ===== 이성 (문구/발송여부는 '이성' 시트가 기준) =====
            if sanity_ok and sanity is not None and name in sanity_by_name:
                s_row_num, s_row = sanity_by_name[name]

                # 이성문구1: 50 이하
                if sanity <= SANITY_THRESHOLD_1 and not self._is_marked(cell(s_row, s_flag1)):
                    msg1 = cell(s_row, s_msg1).strip()
                    if msg1:
                        alerts.append({
                            'user_id': user_id, 'name': name, 'message': SANITY_MSG_PREFIX + msg1, 'label': '이성문구1',
                            'sheet_update': {
                                'range': f'{self._column_index_to_letter(s_flag1)}{s_row_num}',
                                'values': [[SENT_MARK]]},
                        })
                        logger.info(f"🧠 {name}(@{user_id}) 이성 {sanity:g} <= {SANITY_THRESHOLD_1} → 이성문구1")
                    else:
                        logger.debug(f"{name} 이성문구1 비어있음 → 전송 보류 (문구 입력 시 발송)")

                # 이성문구2: 30 이하
                if sanity <= SANITY_THRESHOLD_2 and not self._is_marked(cell(s_row, s_flag2)):
                    msg2 = cell(s_row, s_msg2).strip()
                    if msg2:
                        alerts.append({
                            'user_id': user_id, 'name': name, 'message': SANITY_MSG_PREFIX + msg2, 'label': '이성문구2',
                            'sheet_update': {
                                'range': f'{self._column_index_to_letter(s_flag2)}{s_row_num}',
                                'values': [[SENT_MARK]]},
                        })
                        logger.info(f"🧠 {name}(@{user_id}) 이성 {sanity:g} <= {SANITY_THRESHOLD_2} → 이성문구2")
                    else:
                        logger.debug(f"{name} 이성문구2 비어있음 → 전송 보류 (문구 입력 시 발송)")
            elif sanity_ok and sanity is not None and name not in sanity_by_name:
                logger.debug(f"{name} → '이성' 시트에 해당 이름 행 없음, 이성 알림 건너뜀")

            # ===== 건강 (발송여부는 로컬 캐시가 기준) =====
            if hp is not None:
                entry = self.management_status_cache.get(user_id)
                entry = entry if isinstance(entry, dict) else {}

                # 건강 문구1: 50 이하
                if hp <= HEALTH_THRESHOLD_1 and not entry.get('health50_sent'):
                    alerts.append({
                        'user_id': user_id, 'name': name, 'message': HEALTH_MSG_1,
                        'label': '건강문구1', 'cache_key': 'health50_sent',
                    })
                    logger.info(f"❤️ {name}(@{user_id}) 건강 {hp:g} <= {HEALTH_THRESHOLD_1} → 건강문구1")

                # 건강 문구2: 20 이하
                if hp <= HEALTH_THRESHOLD_2 and not entry.get('health20_sent'):
                    alerts.append({
                        'user_id': user_id, 'name': name, 'message': HEALTH_MSG_2,
                        'label': '건강문구2', 'cache_key': 'health20_sent',
                    })
                    logger.info(f"❤️ {name}(@{user_id}) 건강 {hp:g} <= {HEALTH_THRESHOLD_2} → 건강문구2")

        # ---- 4) 전송 → 성공한 건만 발송여부 기록 (원자성) ----
        self._deliver_alerts(alerts, sanity_ws, current_time)

    # ==================== 실행 루프 ====================

    def run(self):
        logger.info("🤖 자동봇 시작")
        try:
            if not self._health_check():
                raise Exception("초기 헬스체크 실패")
            logger.info("✅ 초기 연결 테스트 성공")

            self.status = BotStatus.RUNNING
            self.start_time = time.time()

            self._start_health_monitor()

            # 매시 정각(00분) 스케줄
            schedule.every().hour.at(":00").do(self.monitor_job)
            logger.info("⏰ 스케줄: 매시 정각(00분)마다 건강/이성 모니터링")

            # 시작 시 즉시 1회 실행 (발송여부 플래그로 중복 방지)
            logger.info("📊 시작 시 초기 점검 실행...")
            self.monitor_job()

            logger.info(f"✅ 봇 준비 완료 (상태: {self.status.value})")

            self.last_status_update = time.time()
            while not self.shutdown_requested:
                try:
                    if time.time() - self.last_status_update > 300:
                        self._create_status_file()
                        self.last_status_update = time.time()

                    self._restart_bot_if_needed()
                    if self.shutdown_requested:
                        break

                    if self.status != BotStatus.CRITICAL_ERROR:
                        schedule.run_pending()
                    else:
                        logger.warning("⚠️ 치명적 오류 상태, 스케줄 실행 중단")
                        time.sleep(60)
                        continue

                except Exception as e:
                    logger.error(f"메인 루프 오류: {e}")
                    logger.error(f"Traceback: {traceback.format_exc()}")
                    if not self._is_critical_error(e):
                        time.sleep(30)
                    else:
                        logger.error("❌ 치명적 오류 감지")
                        self.status = BotStatus.CRITICAL_ERROR

                time.sleep(30)  # 스케줄 체크 주기

            logger.info("👋 봇 정상 종료")

        except Exception as e:
            logger.critical(f"봇 치명적 오류: {e}")
            logger.critical(f"Traceback: {traceback.format_exc()}")
            self.status = BotStatus.CRITICAL_ERROR
            raise
        finally:
            self.main_thread_active = False
            if self.health_check_thread and self.health_check_thread.is_alive():
                self.health_check_thread.join(timeout=5)
            self._create_status_file()
            self._save_status_cache()

    def _is_critical_error(self, exception) -> bool:
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

            if bot.shutdown_requested:
                logger.info("👋 정상 종료 요청")
                return 0

            restart_attempts += 1
            if restart_attempts < max_main_restarts:
                wait_time = min(300 * restart_attempts, 1800)
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
                wait_time = min(600 * restart_attempts, 3600)
                logger.warning(f"⏳ 치명적 오류 후 {wait_time}초 대기 후 재시도")
                time.sleep(wait_time)

    logger.critical(f"❌ 최대 재시작 횟수 ({max_main_restarts}) 초과, 봇 종료")
    return 1


if __name__ == "__main__":
    log_level = os.getenv('LOG_LEVEL', 'INFO').upper()
    if hasattr(logging, log_level):
        logger.setLevel(getattr(logging, log_level))

    logger.info("=" * 60)
    logger.info("🤖 마스토돈 + Google Sheets 자동봇 (shinarmont)")
    logger.info(f"📌 PID: {os.getpid()}")
    logger.info("=" * 60)

    exit_code = main()
    logger.info("=" * 60)
    logger.info(f"👋 봇 종료 (Exit Code: {exit_code})")
    logger.info("=" * 60)
    sys.exit(exit_code)

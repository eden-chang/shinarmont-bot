"""
Google Sheets 작업 모듈
Google Sheets와 관련된 모든 작업을 통합 관리합니다.
"""

import os
import sys
import gspread
import pytz
import time
import re
from datetime import datetime
from typing import Callable, List, Dict, Any, Optional, Union, Tuple, Set
from gspread.exceptions import APIError
from difflib import SequenceMatcher

# 경로 설정 (VM 환경 대응)
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

try:
    from config.settings import config
    from utils.error_handling import (
        safe_execute, SheetAccessError, UserNotFoundError,
        SheetErrorHandler, ErrorContext
    )
    from utils.logging_config import logger, bot_logger, should_log_debug, log_api_operation
    from utils.cache_manager import cache_roster_data, get_roster_data
except ImportError:
    # VM 환경에서 임포트 실패 시 폴백
    import importlib.util
    
    # config.settings 로드
    config_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'settings.py')
    spec = importlib.util.spec_from_file_location("settings", config_path)
    settings_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(settings_module)
    config = settings_module.config
    
    # 기본 로거 설정 (임포트 실패 시)
    import logging
    logger = logging.getLogger('sheets_operations')
    
    # 캐시 관련 폴백
    def cache_roster_data(data):
        return False
    
    def get_roster_data():
        return None


def normalize_header(name: Any) -> str:
    """헤더(컬럼명)의 공백을 정리한다: 앞뒤 제거 + 내부 연속 공백을 하나로.

    GM이 만든 시트의 헤더에 눈에 안 보이는 공백이 붙는 일이 실제로 반복됐다
    (시트명에서 '추적기록 ', '외곽  시험장'). 헤더에 같은 일이 생기면 더 조용히 위험하다:
    `row.get('소지금')`이 None을 돌려주고 **소지금이 0으로 읽힌다**(오류도 안 난다).

    코드가 쓰는 리터럴('소지금', '조사 포인트')로 수렴시키는 방향이라,
    'ㅤ소지금 ' → '소지금', '조사  포인트' → '조사 포인트'가 된다.
    NBSP 등 유니코드 공백도 str.split()이 처리한다.
    """
    return ' '.join(str(name if name is not None else '').split())


def normalize_text(text: str) -> str:
    """
    텍스트 정규화 - 매칭을 위해 텍스트를 정리
    """
    if not text:
        return ""
    
    # 1. HTML 태그 제거 (이미 되어있을 수도 있지만 재확인)
    text = re.sub(r'<[^>]+>', '', text)
    
    # 2. 연속된 공백을 단일 공백으로 변환
    text = re.sub(r'\s+', ' ', text)
    
    # 3. 앞뒤 공백 제거
    text = text.strip()
    
    # 4. 특수문자 통일 (전각 → 반각)
    text = text.replace('（', '(').replace('）', ')')
    text = text.replace('！', '!').replace('？', '?')
    text = text.replace('【', '[').replace('】', ']')
    
    return text



class SheetsManager:
    """Google Sheets 관리 클래스"""
    
    def __init__(self, sheet_id: str = None, credentials_path: str = None,
                 purpose: str = None):
        """
        SheetsManager 초기화

        Args:
            sheet_id: 스프레드시트 ID
            credentials_path: 인증 파일 경로. purpose를 주면 크레덴셜 풀이 정한다.
            purpose: 크레덴셜 용도('main'/'system'/'investigation').
                주면 **용도별 크레덴셜을 쓰고, 쿼터(429)에 걸리면 다른 계정을 빌려 재시도**한다.
                Sheets 쿼터는 프로젝트 단위라 계정을 나누면 쿼터가 그만큼 늘어난다.
                None이면 기존 동작(단일 credentials_path).
        """
        self.sheet_id = sheet_id or config.SHEET_ID
        self.purpose = purpose
        self._credential_name = None
        self.credentials_path = credentials_path or config.get_credentials_path()
        self._spreadsheet = None
        self._worksheets_cache = {}
        # 워크시트 데이터 TTL 캐시(use_cache=True 전용): name -> (저장시각, records)
        self._data_cache: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}
        # 헤더(1행) 캐시: 헤더는 운영 중 정적 → 프로세스 수명 동안 유지(clear_cache로 초기화)
        self._header_cache: Dict[str, List[str]] = {}
        # 헤더 공백 경고를 워크시트당 1회만 내기 위한 표시
        self._header_warned: Set[str] = set()
        try:
            self._data_cache_ttl = float(getattr(config, 'SHEET_CACHE_TTL', 90) or 90)
        except (TypeError, ValueError):
            self._data_cache_ttl = 90.0

    # ------------------------------------------------------------------ #
    # 크레덴셜 failover
    # ------------------------------------------------------------------ #
    def _normalized_headers(self, worksheet_name: str, raw_headers: List[str]) -> List[str]:
        """헤더의 공백을 정리하고, 실제로 바뀌었으면 한 번 경고한다.

        경고는 워크시트당 1회만 낸다(매 읽기마다 로그를 도배하지 않도록).
        """
        headers = [normalize_header(h) for h in raw_headers]

        if worksheet_name not in self._header_warned:
            changed = [
                (raw, norm) for raw, norm in zip(raw_headers, headers)
                if norm and raw != norm
            ]
            if changed:
                self._header_warned.add(worksheet_name)
                for raw, norm in changed:
                    logger.warning(
                        f"'{worksheet_name}' 헤더 {raw!r}의 공백을 정리해 {norm!r}로 읽습니다. "
                        f"시트의 헤더를 정리하는 편이 좋습니다."
                    )
            # 정리 후 서로 같아진 헤더가 있으면 뒤엣것이 앞엣것을 덮는다 → 반드시 알린다
            named = [h for h in headers if h]
            if len(named) != len(set(named)):
                self._header_warned.add(worksheet_name)
                dupes = sorted({h for h in named if named.count(h) > 1})
                logger.error(
                    f"'{worksheet_name}'에 이름이 겹치는 헤더가 있습니다: {dupes}. "
                    f"뒤쪽 열이 앞쪽 열을 덮어씁니다. 시트를 확인해 주세요."
                )
        return headers

    def _find_worksheet_loosely(self, worksheet_name: str):
        """공백을 무시하고 워크시트를 찾는다. 없으면 None.

        정확한 이름으로 못 찾았을 때만 부르는 폴백이다. GM이 만든 시트명에
        보이지 않는 공백이 섞이는 일이 실제로 있어(‘추적기록 ’, ‘외곽  시험장’)
        그것 때문에 기능이 통째로 죽는 것을 막는다.
        """
        target = ''.join(str(worksheet_name or '').split())
        if not target:
            return None
        try:
            for ws in self.spreadsheet.worksheets():
                if ''.join(str(ws.title).split()) == target:
                    logger.warning(
                        f"워크시트 '{worksheet_name}'을(를) 공백 무시로 매칭했습니다 "
                        f"→ 실제 시트명 '{ws.title}'. 시트명을 정리하는 편이 좋습니다."
                    )
                    return ws
        except Exception as e:
            logger.debug(f"워크시트 관용 매칭 실패({worksheet_name}): {e}")
        return None

    def _ensure_credential(self) -> None:
        """아직 크레덴셜을 고르지 않았으면 풀에서 하나 고른다.

        `_with_failover`를 거치지 않는 경로(예: `.spreadsheet` 프로퍼티 직접 접근,
        main.py의 연결 확인)에서도 풀의 크레덴셜을 쓰게 한다.
        이게 없으면 존재하지 않는 기본 경로(credentials/credentials.json)로 인증을 시도한다.
        """
        if not self.purpose or self._credential_name is not None:
            return
        from utils.credential_pool import get_pool
        name = get_pool().acquire(self.purpose)
        if name:
            self._use_credential(name)

    def _use_credential(self, name: str) -> None:
        """이 크레덴셜로 갈아탄다. 바뀌면 연결·워크시트 캐시를 버린다."""
        from utils.credential_pool import get_pool
        path = get_pool().path_of(name)
        if not path or name == self._credential_name:
            return
        self._credential_name = name
        self.credentials_path = path
        # 인증이 바뀌었으니 기존 연결/워크시트 객체는 못 쓴다(데이터 캐시는 유지 — 내용은 같다)
        self._spreadsheet = None
        self._worksheets_cache.clear()
        logger.info(f"[{self.purpose}] 크레덴셜 전환 → '{name}'")

    def _with_failover(self, operation: Callable) -> Any:
        """작업을 실행하되, 쿼터/권한 문제면 다른 크레덴셜로 바꿔 재시도한다.

        - **429(쿼터 초과)**: 기존 `safe_execute`는 500/503만 재시도해서 429는 그대로 실패한다.
          여기서 다른 프로젝트의 계정을 빌려 즉시 재시도한다(프로젝트마다 쿼터가 따로다).
        - **403(미공유)**: 그 계정을 이 용도에서 영구 제외하고 다음 후보로 넘어간다.
        - 그 외 오류는 그대로 올려보낸다(상위 safe_execute가 처리).

        purpose가 없으면(기존 동작) 그냥 실행한다.
        """
        if not self.purpose:
            return operation()

        from utils.credential_pool import (
            get_pool, is_permission_error, is_quota_error,
        )
        pool = get_pool()
        tried: List[str] = []
        last_error = None

        # **순환 방식**(운영 결정 2026-07-16): 주 크레덴셜로 굳이 돌아가지 않는다.
        # 지금 쓰는 것이 429를 맞을 때까지 계속 쓰고, 맞으면 다음 후보로 넘어간다.
        # 쿨다운(5분)이 지난 것은 후보 목록에 다시 들어오므로, 한 바퀴 돌면
        # 자연히 처음 것으로 되돌아온다 — 명시적 복귀 로직이 필요 없다.
        while True:
            name = self._credential_name
            if name is None or name in tried:
                name = pool.acquire(self.purpose, exclude=tried)
                if name is None:
                    break
                self._use_credential(name)

            try:
                result = operation()
                pool.report_success(name)
                return result
            except Exception as e:
                last_error = e
                if is_quota_error(e):
                    pool.report_quota_exhausted(name)
                elif is_permission_error(e):
                    pool.report_no_access(self.purpose, name)
                else:
                    raise            # 다른 오류는 크레덴셜을 바꿔도 소용없다
                tried.append(name)
                self._credential_name = None   # 다음 루프에서 새로 고른다

        logger.error(
            f"[{self.purpose}] 모든 크레덴셜 실패({len(tried)}개 시도: {tried}). "
            f"쿼터 소진이거나 시트에 공유되지 않았습니다."
        )
        if last_error:
            raise last_error
        raise SheetAccessError(f"'{self.purpose}'에 쓸 수 있는 크레덴셜이 없습니다.")

    def set_data_cache_ttl(self, seconds: float) -> None:
        """이 매니저의 워크시트 데이터 캐시 TTL(초)을 조정한다.

        스프레드시트마다 최신성 요구가 다르다(예: 조사 시트는 30~60초 권장).
        전역 SHEET_CACHE_TTL 대신 인스턴스 단위로 덮어쓸 때 쓴다.
        """
        try:
            value = float(seconds)
        except (TypeError, ValueError):
            logger.warning(f"잘못된 캐시 TTL 무시: {seconds!r}")
            return
        if value < 0:
            logger.warning(f"음수 캐시 TTL 무시: {value}")
            return
        self._data_cache_ttl = value

    def _invalidate_data_cache(self, worksheet_name: str) -> None:
        """해당 워크시트의 데이터 캐시 무효화(쓰기 후 호출)."""
        self._data_cache.pop(worksheet_name, None)
        
    @property
    def spreadsheet(self):
        """스프레드시트 객체 (지연 로딩)"""
        if self._spreadsheet is None:
            self._spreadsheet = self.connect_to_sheet()
        return self._spreadsheet
    
    def connect_to_sheet(self) -> gspread.Spreadsheet:
        """
        스프레드시트 연결 (기존 connect_to_sheet 함수 개선 버전)
        
        Returns:
            gspread.Spreadsheet: 연결된 스프레드시트 객체
            
        Raises:
            SheetAccessError: 연결 실패 시
        """
        # 풀을 쓰는 매니저면 여기서 크레덴셜을 확정한다(.spreadsheet 직접 접근 대비)
        self._ensure_credential()

        def connection_operation():
            try:
                # Google API를 사용한 인증
                gc = gspread.service_account(filename=str(self.credentials_path))
                
                # 스프레드시트 열기 (ID 기반)
                spreadsheet = gc.open_by_key(self.sheet_id)
                logger.info(f"✅ 스프레드시트 ID '{self.sheet_id}' 연결 성공")
                return spreadsheet
                
            except FileNotFoundError:
                raise SheetAccessError(f"인증 파일을 찾을 수 없습니다: {self.credentials_path}")
            except gspread.exceptions.SpreadsheetNotFound:
                raise SheetAccessError(f"스프레드시트 ID '{self.sheet_id}'를 찾을 수 없습니다.")
            except Exception as e:
                # `from e`로 원인을 매달아 둔다. 안 그러면 429/403 신호가 여기서 끊겨
                # 크레덴셜 풀이 쿼터 초과를 못 알아보고 **페일오버가 돌지 않는다**.
                # (2026-07-16: str(e)가 비어 "스프레드시트 연결 실패: "만 남았고,
                #  커스텀 시트 읽기가 그대로 죽은 뒤 "데이터가 없습니다."로 보고됐다)
                # repr도 함께 남긴다 — str()이 빈 예외가 실제로 있었다.
                raise SheetAccessError(f"스프레드시트 연결 실패: {e!r}") from e
        
        with ErrorContext("스프레드시트 연결", sheet_id=self.sheet_id):
            result = safe_execute(
                operation_func=connection_operation,
                max_retries=config.MAX_RETRIES
            )
            
            if result.success:
                return result.result
            else:
                raise result.error or SheetAccessError("스프레드시트 연결 실패")
    
    def get_worksheet(self, worksheet_name: str, use_cache: bool = True) -> gspread.Worksheet:
        """
        워크시트 가져오기 (캐싱 지원)
        
        Args:
            worksheet_name: 워크시트 이름
            use_cache: 캐시 사용 여부
            
        Returns:
            gspread.Worksheet: 워크시트 객체
            
        Raises:
            SheetAccessError: 워크시트를 찾을 수 없을 때
        """
        if use_cache and worksheet_name in self._worksheets_cache:
            return self._worksheets_cache[worksheet_name]
        
        def get_operation():
            try:
                worksheet = self.spreadsheet.worksheet(worksheet_name)
                if use_cache:
                    self._worksheets_cache[worksheet_name] = worksheet
                return worksheet
            except gspread.exceptions.WorksheetNotFound:
                # 시트명 앞뒤/중간 공백 관용 매칭.
                # GM이 만든 시트에 눈에 안 보이는 공백이 붙는 일이 실제로 있다
                # (실측: 시스템 시트의 '추적기록 ', 조사 시트의 '외곽  시험장').
                # 정확한 이름으로 못 찾으면 공백을 무시하고 한 번 더 찾는다.
                worksheet = self._find_worksheet_loosely(worksheet_name)
                if worksheet is not None:
                    if use_cache:
                        self._worksheets_cache[worksheet_name] = worksheet
                    return worksheet
                raise SheetErrorHandler.handle_worksheet_not_found(worksheet_name)
        
        with ErrorContext("워크시트 접근", worksheet=worksheet_name):
            result = safe_execute(lambda: self._with_failover(get_operation))
            
            if result.success:
                return result.result
            else:
                raise result.error or SheetErrorHandler.handle_worksheet_not_found(worksheet_name)
    
    def get_worksheet_data(self, worksheet_name: str, use_cache: bool = False) -> List[Dict[str, Any]]:
        """
        워크시트 데이터 가져오기 (1행: 헤더, 2행: 설명, 3행부터: 데이터)

        Args:
            worksheet_name: 워크시트 이름
            use_cache: 캐시 사용 여부 (데이터는 기본적으로 캐시하지 않음)

        Returns:
            List[Dict]: 워크시트 데이터
        """
        # use_cache=True 이고 TTL 내 캐시가 있으면 사본을 반환(API 호출 생략).
        # 가변 시트는 호출측이 use_cache=False로 항상 최신을 읽는다.
        if use_cache:
            entry = self._data_cache.get(worksheet_name)
            if entry is not None:
                cached_at, cached_records = entry
                if (time.time() - cached_at) < self._data_cache_ttl:
                    return [dict(r) for r in cached_records]

        def get_data_operation():
            worksheet = self.get_worksheet(worksheet_name)
            if worksheet.row_count <= 2:  # 헤더와 설명만 있거나 빈 시트
                return []

            # 수동으로 헤더와 데이터 파싱 (1행: 헤더, 2행: 설명, 3행부터: 데이터)
            all_values = worksheet.get_all_values()
            if len(all_values) < 3:  # 헤더, 설명, 데이터 최소 1개 필요
                return []

            # 1행: 헤더. 공백을 정리해 코드가 쓰는 리터럴과 맞춘다(§normalize_header).
            # 열 **순서**는 그대로라, 행 키 순서에서 열 번호를 산출하는 코드도 그대로 동작한다.
            headers = self._normalized_headers(worksheet_name, all_values[0])
            # all_values[1]은 설명 행 - 무시
            data_rows = all_values[2:]  # 3행부터: 데이터

            # 딕셔너리 리스트로 변환
            # enumerate로 실제 시트 행 번호를 추적 (빈 행 포함)
            records = []
            for idx, row_values in enumerate(data_rows):
                # 빈 행 스킵 (단, idx는 계속 증가하여 실제 행 번호 유지)
                if not any(row_values):
                    continue
                record = dict(zip(headers, row_values))
                # _row_number: 실제 시트 행 번호 (1-indexed)
                # idx=0 → 시트 3행 (헤더1 + 설명1 + idx0 + 1)
                record['_row_number'] = idx + 3
                records.append(record)

            return records

        with ErrorContext("워크시트 데이터 조회", worksheet=worksheet_name):
            result = safe_execute(
                lambda: self._with_failover(get_data_operation), fallback_return=[])

            if result.success:
                bot_logger.log_sheet_operation("데이터 조회", worksheet_name, True)
                records = result.result
                if use_cache:
                    self._data_cache[worksheet_name] = (time.time(), [dict(r) for r in records])
                return records
            else:
                bot_logger.log_sheet_operation("데이터 조회", worksheet_name, False, str(result.error))
                return []

    def get_header(self, worksheet_name: str) -> List[str]:
        """워크시트 1행(헤더)을 반환(캐시). 헤더는 운영 중 정적이라 API 호출을 1회로 줄인다.

        `get_worksheet_data`와 같은 규칙으로 **공백을 정리해서** 돌려준다.
        두 경로가 다른 이름을 주면 열 번호가 어긋난다.
        """
        cached = self._header_cache.get(worksheet_name)
        if cached is not None:
            return cached
        try:
            worksheet = self.get_worksheet(worksheet_name)
            raw = worksheet.row_values(1) or []
        except Exception as e:
            logger.warning(f"헤더 조회 실패({worksheet_name}): {e}")
            return []
        header = self._normalized_headers(worksheet_name, raw)
        self._header_cache[worksheet_name] = header
        return header
    
    def append_row(self, worksheet_name: str, values: List[Any]) -> bool:
        """
        워크시트에 행 추가
        
        Args:
            worksheet_name: 워크시트 이름
            values: 추가할 값들
            
        Returns:
            bool: 성공 여부
        """
        def append_operation():
            worksheet = self.get_worksheet(worksheet_name)
            worksheet.append_row(values)
            return True

        # 쓰기 후 캐시가 낡지 않도록 무효화(성공 여부와 무관하게 안전측)
        self._invalidate_data_cache(worksheet_name)
        with ErrorContext("행 추가", worksheet=worksheet_name, values_count=len(values)):
            result = safe_execute(lambda: self._with_failover(append_operation))

            success = result.success
            bot_logger.log_sheet_operation("행 추가", worksheet_name, success, 
                                         str(result.error) if not success else None)
            return success
    
    def update_cell(self, worksheet_name: str, row: int, col: int, value: Any) -> bool:
        """
        특정 셀 업데이트

        Args:
            worksheet_name: 워크시트 이름
            row: 행 번호 (1부터 시작)
            col: 열 번호 (1부터 시작)
            value: 업데이트할 값

        Returns:
            bool: 성공 여부
        """
        def update_operation():
            worksheet = self.get_worksheet(worksheet_name)
            worksheet.update_cell(row, col, value)
            return True

        self._invalidate_data_cache(worksheet_name)
        with ErrorContext("셀 업데이트", worksheet=worksheet_name, row=row, col=col):
            result = safe_execute(lambda: self._with_failover(update_operation))

            success = result.success
            bot_logger.log_sheet_operation("셀 업데이트", worksheet_name, success,
                                         str(result.error) if not success else None)
            return success

    def batch_update_cells(self, worksheet_name: str, updates: List[Tuple[int, int, Any]]) -> bool:
        """
        여러 셀을 원자적으로 업데이트 (트랜잭션 방식)

        Args:
            worksheet_name: 워크시트 이름
            updates: [(row, col, value), ...] 형태의 업데이트 리스트

        Returns:
            bool: 성공 여부
        """
        def batch_update_operation():
            worksheet = self.get_worksheet(worksheet_name)

            # gspread의 batch_update를 사용하여 한 번에 업데이트
            # A1 표기법으로 변환
            cell_list = []
            for row, col, value in updates:
                col_letter = self._column_number_to_letter(col)
                cell_address = f"{col_letter}{row}"
                cell_list.append({'range': cell_address, 'values': [[value]]})

            # batch_update 실행
            if cell_list:
                worksheet.batch_update(cell_list, value_input_option='RAW')

            return True

        self._invalidate_data_cache(worksheet_name)
        with ErrorContext("배치 셀 업데이트", worksheet=worksheet_name, update_count=len(updates)):
            result = safe_execute(lambda: self._with_failover(batch_update_operation))

            success = result.success
            bot_logger.log_sheet_operation(f"배치 업데이트 ({len(updates)}개 셀)",
                                         worksheet_name, success,
                                         str(result.error) if not success else None)
            return success
    
    def _get_roster_data_cached(self) -> List[Dict[str, Any]]:
        """
        명단 데이터 조회 (2시간 캐시 적용)
        
        Returns:
            List[Dict]: 명단 데이터
        """
        # 캐시에서 조회 시도
        cached_data = get_roster_data()

        if cached_data is not None:
            if should_log_debug():
                logger.debug("캐시에서 명단 데이터 로드")
            return cached_data

        # 캐시에 없으면 시트에서 로드
        if should_log_debug():
            logger.debug("시트에서 명단 데이터 로드 및 캐시 저장")
        roster_data = self.get_worksheet_data(config.get_worksheet_name('ROSTER'))
        
        # 캐시에 저장 (2시간 TTL)
        cache_roster_data(roster_data)
        
        return roster_data
    
    def get_roster_data(self, use_cache: bool = True) -> List[Dict[str, Any]]:
        """
        공개 명단 데이터 조회 메서드 (표준 API)
        
        Args:
            use_cache: 캐시 사용 여부
        
        Returns:
            List[Dict]: 명단 데이터 리스트
        """
        try:
            if use_cache:
                return self._get_roster_data_cached()
            # 캐시 미사용 시 실시간 조회 후 캐시에 저장
            roster_data = self.get_worksheet_data(config.get_worksheet_name('ROSTER'))
            cache_roster_data(roster_data)
            return roster_data
        except Exception as e:
            logger.warning(f"명단 데이터 조회 실패: {e}")
            return []
    
    # 하위 호환: 기존 코드에서 호출하는 이름 유지
    def get_user_data(self) -> List[Dict[str, Any]]:
        return self.get_roster_data(use_cache=True)
    
    def find_user_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        """
        사용자 ID로 사용자 정보 조회 (캐시 적용 - 기존 get_user_data_safe 개선 버전)
        
        Args:
            user_id: 사용자 ID
            
        Returns:
            Optional[Dict]: 사용자 정보 또는 None
        """
        roster_data = self._get_roster_data_cached()
        
        for row in roster_data:
            if str(row.get('아이디', '')).strip() == user_id:
                return row
        
        return None
    
    def user_exists(self, user_id: str) -> bool:
        """
        사용자 존재 여부 확인 (기존 user_id_check 개선 버전)
        
        Args:
            user_id: 사용자 ID
            
        Returns:
            bool: 사용자 존재 여부
        """
        return self.find_user_by_id(user_id) is not None

    def get_currency_setting(self) -> Optional[str]:
        """
        화폐 단위 설정값 반환 (.env 기반 표준)
        """
        try:
            return getattr(config, 'CURRENCY', None)
        except Exception:
            return None

    def get_item_data(self) -> List[Dict[str, Any]]:
        """
        상점 아이템 데이터 조회 (표준 API)
        """
        try:
            return self.get_worksheet_data(config.get_worksheet_name('SHOP'))
        except Exception as e:
            logger.warning(f"아이템 데이터 조회 실패: {e}")
            return []
    
    def log_action(self, user_name: str, command: str, message: str, success: bool = True) -> bool:
        """
        로그 기록 (기존 log_action 개선 버전)
        
        Args:
            user_name: 사용자 이름
            command: 실행된 명령어
            message: 결과 메시지
            success: 성공 여부
            
        Returns:
            bool: 로그 기록 성공 여부
        """
        now = self.get_current_time()
        status = "성공" if success else "실패"
        
        # 로그 시트를 사용하지 않으므로 파일 로그만 기록
        log_message = f"📝 봇 액션 - {now} | {user_name} | {command} | {message} | {status}"
        if success:
            logger.info(log_message)
        else:
            logger.warning(log_message)
        
        return True
    
    @staticmethod
    def get_current_time() -> str:
        """
        현재 KST 기준 시간 반환
        
        Returns:
            str: 현재 시간 (YYYY-MM-DD HH:MM:SS 형식)
        """
        return datetime.now(pytz.timezone('Asia/Seoul')).strftime('%Y-%m-%d %H:%M:%S')
    
    def get_custom_commands(self) -> Dict[str, List[str]]:
        """
        커스텀 명령어와 문구들 조회
        
        Returns:
            Dict[str, List[str]]: {명령어: [문구들]} 형태의 딕셔너리
        """
        # 커스텀 시트를 사용하지 않으므로 빈 딕셔너리 반환
        return {}
        
        # custom_data = self.get_worksheet_data(config.get_worksheet_name('CUSTOM'))
        # commands = {}
        # 
        # for row in custom_data:
        #     command = str(row.get('명령어', '')).strip()
        #     phrase = str(row.get('문구', '')).strip()
        #     
        #     if command and phrase:
        #         if command not in commands:
        #             commands[command] = []
        #         commands[command].append(phrase)
        # 
        # return commands
    
    def get_help_items(self, sheet_name: Optional[str] = None) -> List[Dict[str, str]]:
        """
        도움말 항목들 조회

        Args:
            sheet_name: 도움말 시트 이름 (None이면 기본 HELP 시트 사용)

        Returns:
            List[Dict]: [{'명령어': str, '설명': str}] 형태의 리스트
        """
        # 시트 이름이 지정되지 않으면 기본 HELP 시트 사용
        if sheet_name is None:
            sheet_name = config.get_worksheet_name('HELP')

        help_data = self.get_worksheet_data(sheet_name)
        help_items = []

        for row in help_data:
            command = str(row.get('명령어', '')).strip()
            description = str(row.get('설명', '')).strip()

            if command and description:
                help_items.append({'명령어': command, '설명': description})

        return help_items
    
    def get_fortune_phrases(self) -> List[str]:
        """
        운세 문구들 조회
        
        Returns:
            List[str]: 운세 문구 리스트
        """
        # 운세 시트를 사용하지 않으므로 빈 리스트 반환
        return []
        
        # fortune_data = self.get_worksheet_data(config.get_worksheet_name('FORTUNE'))
        # phrases = []
        # 
        # for row in fortune_data:
        #     phrase = str(row.get('문구', '')).strip()
        #     if phrase:
        #         phrases.append(phrase)
        # 
        # return phrases
    
    def _column_number_to_letter(self, col_num: int) -> str:
        """
        컬럼 번호를 알파벳으로 변환 (1 -> A, 2 -> B, ...)
        
        Args:
            col_num: 컬럼 번호 (1부터 시작)
            
        Returns:
            str: 컬럼 알파벳 (A, B, C, ..., AA, AB, ...)
        """
        result = ""
        while col_num > 0:
            col_num -= 1
            result = chr(col_num % 26 + ord('A')) + result
            col_num //= 26
        return result
    
    def _find_student_row_by_id(self, user_id: str) -> Optional[int]:
        """
        사용자 ID로 학생관리 시트에서 행 번호 찾기 (1행: 헤더, 2행: 설명, 3행부터: 데이터)

        Args:
            user_id: 사용자 ID

        Returns:
            Optional[int]: 행 번호 (1부터 시작) 또는 None
        """
        try:
            worksheet = self.get_worksheet('학생관리')
            all_values = worksheet.get_all_values()

            # 헤더에서 '아이디' 컬럼 찾기
            if not all_values:
                return None

            headers = all_values[0]  # 1행: 헤더
            id_col = None
            for i, header in enumerate(headers):
                if header == '아이디':
                    id_col = i
                    break

            if id_col is None:
                return None

            # 사용자 ID가 있는 행 찾기 (3행부터 데이터)
            for i, row in enumerate(all_values[2:], start=3):  # 3번째 행부터 시작
                if len(row) > id_col and str(row[id_col]).strip() == user_id:
                    return i

            return None

        except Exception as e:
            logger.error(f"학생 행 찾기 실패: {e}")
            return None
    
    # ==================== 기존 메서드들 ====================

    def clear_cache(self):
        """워크시트/데이터/헤더 캐시 초기화"""
        self._worksheets_cache.clear()
        self._data_cache.clear()
        self._header_cache.clear()
        if should_log_debug():
            logger.debug("워크시트/데이터/헤더 캐시가 초기화되었습니다.")
    
    def invalidate_roster_cache(self) -> bool:
        """
        명단 캐시 무효화
        
        Returns:
            bool: 무효화 성공 여부
        """
        try:
            from utils.cache_manager import invalidate_roster_data
            return invalidate_roster_data()
        except ImportError:
            return False
    
    def get_roster_cache_status(self) -> Dict[str, Any]:
        """
        명단 캐시 상태 정보 반환
        
        Returns:
            Dict: 캐시 상태 정보
        """
        try:
            from utils.cache_manager import get_roster_cache_info
            return get_roster_cache_info()
        except ImportError:
            return {'cached': False, 'message': '캐시 시스템을 사용할 수 없습니다'}
    
    def validate_sheet_structure(self) -> Dict[str, Any]:
        """
        시트 구조 검증
        
        Returns:
            Dict: 검증 결과
        """
        validation_results = {
            'valid': True,
            'errors': [],
            'warnings': [],
            'worksheets_found': []
        }
        
        try:
            # 모든 워크시트 이름 가져오기
            all_worksheets = [ws.title for ws in self.spreadsheet.worksheets()]
            validation_results['worksheets_found'] = all_worksheets

            # 필수 워크시트 확인 (봇 타입에 따라 다르게 체크)
            # default 봇: 명단, 도움말, 운세, 커스텀만 필수
            # store 봇: 위 4개 + 상점 필수
            required_worksheets = [
                config.get_worksheet_name('ROSTER'),
                config.get_worksheet_name('HELP'),
                config.get_worksheet_name('FORTUNE'),
                config.get_worksheet_name('CUSTOM'),
            ]

            # store 봇인 경우 상점 시트도 필수
            if config.BOT_TYPE == 'store':
                required_worksheets.append(config.get_worksheet_name('SHOP'))

            for required in required_worksheets:
                if required not in all_worksheets:
                    validation_results['errors'].append(f"필수 워크시트 '{required}'가 없습니다.")
                    validation_results['valid'] = False

            # 각 워크시트 구조 확인
            self._validate_roster_structure(validation_results)
            # self._validate_custom_structure(validation_results)  # 커스텀 시트를 사용하지 않으므로 주석 처리
            self._validate_help_structure(validation_results)
            # self._validate_fortune_structure(validation_results)

        except Exception as e:
            validation_results['errors'].append(f"시트 구조 검증 중 오류: {str(e)}")
            validation_results['valid'] = False
        
        return validation_results
    
    def _validate_roster_structure(self, results: Dict):
        """명단 시트 구조 검증 (1행: 헤더, 2행: 설명)"""
        try:
            worksheet = self.get_worksheet(config.get_worksheet_name('ROSTER'))
            if worksheet.row_count > 1:  # 헤더와 설명 행 필요
                headers = worksheet.row_values(1)

                # 필수 헤더 확인
                required_headers = ['아이디', '이름']
                for header in required_headers:
                    if header not in headers:
                        results['errors'].append(f"'명단' 시트에 '{header}' 헤더가 없습니다.")
                        results['valid'] = False

                # 2행 설명 행 존재 확인
                if worksheet.row_count < 2:
                    results['warnings'].append(f"'명단' 시트에 설명 행(2행)이 없습니다.")
            else:
                results['errors'].append(f"'명단' 시트에 헤더와 설명 행이 필요합니다.")
                results['valid'] = False

        except Exception as e:
            results['errors'].append(f"명단 시트 검증 실패: {str(e)}")
            results['valid'] = False
    
    # def _validate_custom_structure(self, results: Dict):
    #     """커스텀 시트 구조 검증"""
    #     try:
    #         worksheet = self.get_worksheet(config.get_worksheet_name('CUSTOM'))
    #         if worksheet.row_count > 0:
    #             headers = worksheet.row_values(1)
    #             required_headers = ['명령어', '문구']
    #             for header in required_headers:
    #                 if header not in headers:
    #                     results['errors'].append(f"'커스텀' 시트에 '{header}' 헤더가 없습니다.")
    #                     results['valid'] = False
    #     except Exception as e:
    #         results['errors'].append(f"커스텀 시트 검증 실패: {str(e)}")
    #         results['valid'] = False
    
    def _validate_help_structure(self, results: Dict):
        """도움말 시트 구조 검증 (1행: 헤더, 2행: 설명)"""
        try:
            worksheet = self.get_worksheet(config.get_worksheet_name('HELP'))
            if worksheet.row_count > 1:  # 헤더와 설명 행 필요
                headers = worksheet.row_values(1)
                required_headers = ['명령어', '설명']
                for header in required_headers:
                    if header not in headers:
                        results['errors'].append(f"'도움말' 시트에 '{header}' 헤더가 없습니다.")
                        results['valid'] = False

                # 2행 설명 행 존재 확인
                if worksheet.row_count < 2:
                    results['warnings'].append(f"'도움말' 시트에 설명 행(2행)이 없습니다.")
            else:
                results['errors'].append(f"'도움말' 시트에 헤더와 설명 행이 필요합니다.")
                results['valid'] = False
        except Exception as e:
            results['errors'].append(f"도움말 시트 검증 실패: {str(e)}")
            results['valid'] = False
    
    # def _validate_fortune_structure(self, results: Dict):
    #     """운세 시트 구조 검증"""
    #     try:
    #         worksheet = self.get_worksheet(config.get_worksheet_name('FORTUNE'))
    #         if worksheet.row_count > 0:
    #             headers = worksheet.row_values(1)
    #             if '문구' not in headers:
    #                 results['errors'].append("'운세' 시트에 '문구' 헤더가 없습니다.")
    #                 results['valid'] = False
    #     except Exception as e:
    #         results['errors'].append(f"운세 시트 검증 실패: {str(e)}")
    #         results['valid'] = False
    

# 전역 인스턴스 (기존 코드와의 호환성을 위해)
_global_sheets_manager = None


def get_sheets_manager() -> SheetsManager:
    """전역 SheetsManager 인스턴스 반환.

    **purpose=PURPOSE_MAIN으로 만든다(2026-07-19)**: 이 매니저는 커스텀 명령어가
    메인 스프레드시트(SHEET_ID)를 읽는 데 쓰인다. purpose 없이 만들면 크레덴셜 풀을
    우회하고 루트 credentials.json 계정으로만 붙는데, 그 계정은 메인 시트에 공유돼
    있지 않아 open_by_key가 403(→ gspread가 빈 PermissionError로 던짐)을 맞고
    페일오버도 없어 매번 같은 실패를 반복했다. main.py의 관리 매니저와 동일하게
    풀(CREDENTIAL_MAIN: genesis/oblivion/koltsevaya)을 쓰고 쿼터/권한 페일오버를 태운다.
    """
    global _global_sheets_manager
    if _global_sheets_manager is None:
        from utils.credential_pool import PURPOSE_MAIN
        _global_sheets_manager = SheetsManager(purpose=PURPOSE_MAIN)
    return _global_sheets_manager


# 기존 코드와의 호환성을 위한 함수들
def connect_to_sheet(sheet_name: str = None, credentials_file: str = None):
    """기존 connect_to_sheet 함수 호환성 유지"""
    manager = SheetsManager(sheet_name, credentials_file)
    return manager.spreadsheet


def user_id_check(sheet, user_id: str) -> bool:
    """기존 user_id_check 함수 호환성 유지"""
    try:
        # sheet가 SheetsManager 인스턴스인 경우
        if isinstance(sheet, SheetsManager):
            return sheet.user_exists(user_id)
        
        # sheet가 gspread.Spreadsheet 인스턴스인 경우
        manager = SheetsManager()
        manager._spreadsheet = sheet
        return manager.user_exists(user_id)
    except Exception:
        return False


def get_user_data_safe(sheet, user_id: str) -> Optional[Dict[str, Any]]:
    """기존 get_user_data_safe 함수 호환성 유지"""
    try:
        # sheet가 SheetsManager 인스턴스인 경우
        if isinstance(sheet, SheetsManager):
            return sheet.find_user_by_id(user_id)
        
        # sheet가 gspread.Spreadsheet 인스턴스인 경우
        manager = SheetsManager()
        manager._spreadsheet = sheet
        return manager.find_user_by_id(user_id)
    except Exception:
        return None


def get_worksheet_data_safe(sheet, worksheet_name: str) -> List[Dict[str, Any]]:
    """기존 get_worksheet_data_safe 함수 호환성 유지"""
    try:
        # sheet가 SheetsManager 인스턴스인 경우
        if isinstance(sheet, SheetsManager):
            return sheet.get_worksheet_data(worksheet_name)
        
        # sheet가 gspread.Spreadsheet 인스턴스인 경우
        manager = SheetsManager()
        manager._spreadsheet = sheet
        return manager.get_worksheet_data(worksheet_name)
    except Exception:
        return []


def log_action(sheet, user_name: str, command: str, message: str, success: bool = True) -> bool:
    """기존 log_action 함수 호환성 유지"""
    try:
        # sheet가 SheetsManager 인스턴스인 경우
        if isinstance(sheet, SheetsManager):
            return sheet.log_action(user_name, command, message, success)
        
        # sheet가 gspread.Spreadsheet 인스턴스인 경우
        manager = SheetsManager()
        manager._spreadsheet = sheet
        return manager.log_action(user_name, command, message, success)
    except Exception:
        # 로그 실패 시 파일 로그에라도 기록
        logger.warning(f"시트 로그 실패: {user_name} | {command} | {message} | {'성공' if success else '실패'}")
        return False


def find_worksheet_safe(sheet, worksheet_name: str):
    """기존 find_worksheet_safe 함수 호환성 유지"""
    try:
        # sheet가 SheetsManager 인스턴스인 경우
        if isinstance(sheet, SheetsManager):
            return sheet.get_worksheet(worksheet_name)
        
        # sheet가 gspread.Spreadsheet 인스턴스인 경우
        manager = SheetsManager()
        manager._spreadsheet = sheet
        return manager.get_worksheet(worksheet_name)
    except Exception:
        return None
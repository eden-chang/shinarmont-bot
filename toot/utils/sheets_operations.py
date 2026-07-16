"""
Google Sheets 작업 모듈
Google Sheets와 관련된 모든 작업을 통합 관리합니다.
"""

import os
import sys
import gspread
import pytz
import time
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional, Union, Tuple
from gspread.exceptions import APIError, WorksheetNotFound, SpreadsheetNotFound

# VM 환경 대응
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
sys.path.append(project_root)

try:
    from config.settings import config
    from utils.error_handling import (
        safe_execute, SheetAccessError, UserNotFoundError, 
        SheetErrorHandler, ErrorContext, TootMonitorException,
        retry_on_error, log_error_context
    )
    from utils.logging_config import logger, toot_logger
except ImportError as e:
    # 임포트 실패 시 기본 로거
    import logging
    logger = logging.getLogger('sheets_operations')
    toot_logger = None
    print(f"임포트 실패: {e}")


class SheetsManager:
    """Google Sheets 관리 클래스"""
    
    def __init__(self, credentials_path: str = None):
        """
        SheetsManager 초기화
        
        Args:
            credentials_path: 인증 파일 경로
        """
        self.credentials_path = credentials_path or config.get_credentials_path()
        self._client = None
        self._toot_sheet = None
        self._shop_sheet = None
        self._worksheets_cache = {}
        
    @property
    def client(self) -> gspread.Client:
        """Google Sheets 클라이언트 (지연 로딩)"""
        if self._client is None:
            self._client = self._create_client()
        return self._client
    
    @property
    def toot_sheet(self) -> gspread.Spreadsheet:
        """툿수 관리 시트 (지연 로딩)"""
        if self._toot_sheet is None:
            self._toot_sheet = self._open_spreadsheet_by_id(config.TOOT_SHEET_ID, '툿수 관리 시트')
        return self._toot_sheet

    @property
    def shop_sheet(self) -> gspread.Spreadsheet:
        """상점봇 시트 (지연 로딩)"""
        if self._shop_sheet is None:
            self._shop_sheet = self._open_spreadsheet_by_id(config.SHOP_SHEET_ID, '상점봇 시트')
        return self._shop_sheet
    
    def _create_client(self) -> gspread.Client:
        """
        Google Sheets 클라이언트 생성
        
        Returns:
            gspread.Client: 인증된 클라이언트
            
        Raises:
            SheetAccessError: 클라이언트 생성 실패 시
        """
        def client_operation():
            try:
                if not self.credentials_path.exists():
                    raise FileNotFoundError(f"인증 파일을 찾을 수 없습니다: {self.credentials_path}")
                
                client = gspread.service_account(filename=str(self.credentials_path))
                logger.info("✅ Google Sheets 클라이언트 생성 성공")
                return client
                
            except FileNotFoundError as e:
                raise SheetAccessError(f"인증 파일 오류: {str(e)}")
            except Exception as e:
                raise SheetAccessError(f"Google Sheets 클라이언트 생성 실패: {str(e)}")
        
        with ErrorContext("Google Sheets 클라이언트 생성"):
            result = safe_execute(client_operation)
            
            if result.success:
                return result.result
            else:
                raise result.error or SheetAccessError("클라이언트 생성 실패")
    
    def _open_spreadsheet_by_id(self, sheet_id: str, sheet_display_name: str = '') -> gspread.Spreadsheet:
        """
        스프레드시트 ID로 열기

        Args:
            sheet_id: 스프레드시트 ID
            sheet_display_name: 표시용 이름 (로깅용)

        Returns:
            gspread.Spreadsheet: 열린 스프레드시트

        Raises:
            SheetAccessError: 스프레드시트 열기 실패 시
        """
        def open_operation():
            try:
                if not sheet_id:
                    raise SheetAccessError(f"스프레드시트 ID가 설정되지 않았습니다. ({sheet_display_name})")

                spreadsheet = self.client.open_by_key(sheet_id)
                logger.info(f"✅ 스프레드시트 '{sheet_display_name or sheet_id}' 열기 성공")
                return spreadsheet

            except SpreadsheetNotFound:
                raise SheetAccessError(f"스프레드시트 ID '{sheet_id}'를 찾을 수 없습니다. ({sheet_display_name})")
            except Exception as e:
                raise SheetAccessError(f"스프레드시트 '{sheet_display_name or sheet_id}' 열기 실패: {str(e)}")

        with ErrorContext("스프레드시트 열기", sheet_id=sheet_id):
            result = safe_execute(open_operation)

            if result.success:
                return result.result
            else:
                raise result.error or SheetAccessError("스프레드시트 열기 실패")
    
    def get_worksheet(self, spreadsheet: gspread.Spreadsheet, worksheet_name: str, use_cache: bool = True) -> gspread.Worksheet:
        """
        워크시트 가져오기 (캐싱 지원)
        
        Args:
            spreadsheet: 스프레드시트 객체
            worksheet_name: 워크시트 이름
            use_cache: 캐시 사용 여부
            
        Returns:
            gspread.Worksheet: 워크시트 객체
            
        Raises:
            SheetAccessError: 워크시트를 찾을 수 없을 때
        """
        cache_key = f"{spreadsheet.title}_{worksheet_name}"
        
        if use_cache and cache_key in self._worksheets_cache:
            return self._worksheets_cache[cache_key]
        
        def get_operation():
            try:
                worksheet = spreadsheet.worksheet(worksheet_name)
                if use_cache:
                    self._worksheets_cache[cache_key] = worksheet
                return worksheet
            except WorksheetNotFound:
                raise SheetErrorHandler.handle_worksheet_not_found(worksheet_name)
        
        with ErrorContext("워크시트 접근", worksheet=worksheet_name):
            result = safe_execute(get_operation)
            
            if result.success:
                return result.result
            else:
                raise result.error or SheetErrorHandler.handle_worksheet_not_found(worksheet_name)
    
    def get_worksheet_data(self, spreadsheet: gspread.Spreadsheet, worksheet_name: str, use_cache: bool = False, header_rows: int = 1) -> List[Dict[str, Any]]:
        """
        워크시트 데이터 가져오기

        Args:
            spreadsheet: 스프레드시트 객체
            worksheet_name: 워크시트 이름
            use_cache: 캐시 사용 여부 (데이터는 기본적으로 캐시하지 않음)
            header_rows: 헤더 행 수 (기본값: 1, 2행 헤더인 경우 2)

        Returns:
            List[Dict]: 워크시트 데이터
        """
        def get_data_operation():
            if header_rows not in (1, 2):
                raise ValueError(f"header_rows는 1 또는 2만 지원됩니다. (입력값: {header_rows})")

            worksheet = self.get_worksheet(spreadsheet, worksheet_name, use_cache=use_cache)
            if worksheet.row_count <= header_rows:  # 헤더만 있거나 빈 시트
                return []

            # 헤더가 2행인 경우 첫 번째 행을 헤더로 사용하고 2행은 스킵
            if header_rows == 2:
                # 모든 데이터 가져오기
                all_values = worksheet.get_all_values()
                if len(all_values) < 3:  # 헤더 2행 + 데이터 최소 1행
                    return []

                # 첫 번째 행을 헤더로, 3행부터 데이터로
                headers = all_values[0]
                data_rows = all_values[2:]  # 3행부터 (인덱스 2부터)

                # 딕셔너리 리스트로 변환
                return [dict(zip(headers, row)) for row in data_rows]
            else:
                return worksheet.get_all_records()

        with ErrorContext("워크시트 데이터 조회", worksheet=worksheet_name):
            result = safe_execute(get_data_operation, fallback_return=[])

            if result.success:
                if toot_logger:
                    toot_logger.log_sheet_operation("데이터 조회", worksheet_name, True)
                return result.result
            else:
                if toot_logger:
                    toot_logger.log_sheet_operation("데이터 조회", worksheet_name, False, str(result.error))
                return []
    
    def append_row(self, spreadsheet: gspread.Spreadsheet, worksheet_name: str, values: List[Any]) -> bool:
        """
        워크시트에 행 추가
        
        Args:
            spreadsheet: 스프레드시트 객체
            worksheet_name: 워크시트 이름
            values: 추가할 값들
            
        Returns:
            bool: 성공 여부
        """
        def append_operation():
            worksheet = self.get_worksheet(spreadsheet, worksheet_name)
            worksheet.append_row(values)
            return True
        
        with ErrorContext("행 추가", worksheet=worksheet_name, values_count=len(values)):
            result = safe_execute(append_operation)
            
            success = result.success
            if toot_logger:
                toot_logger.log_sheet_operation("행 추가", worksheet_name, success, 
                                             str(result.error) if not success else None)
            return success
    
    def update_cell(self, spreadsheet: gspread.Spreadsheet, worksheet_name: str, row: int, col: int, value: Any) -> bool:
        """
        특정 셀 업데이트
        
        Args:
            spreadsheet: 스프레드시트 객체
            worksheet_name: 워크시트 이름
            row: 행 번호 (1부터 시작)
            col: 열 번호 (1부터 시작)
            value: 업데이트할 값
            
        Returns:
            bool: 성공 여부
        """
        def update_operation():
            worksheet = self.get_worksheet(spreadsheet, worksheet_name)
            worksheet.update_cell(row, col, value)
            return True
        
        with ErrorContext("셀 업데이트", worksheet=worksheet_name, row=row, col=col):
            result = safe_execute(update_operation)
            
            success = result.success
            if toot_logger:
                toot_logger.log_sheet_operation("셀 업데이트", worksheet_name, success,
                                             str(result.error) if not success else None)
            return success
    
    def update_range(self, spreadsheet: gspread.Spreadsheet, worksheet_name: str, range_name: str, values: List[List[Any]]) -> bool:
        """
        범위 업데이트 (배치 처리)
        
        Args:
            spreadsheet: 스프레드시트 객체
            worksheet_name: 워크시트 이름
            range_name: 범위 (예: 'A2:F10')
            values: 업데이트할 값들 (2차원 리스트)
            
        Returns:
            bool: 성공 여부
        """
        def update_operation():
            worksheet = self.get_worksheet(spreadsheet, worksheet_name)
            worksheet.update(range_name, values)
            return True
        
        with ErrorContext("범위 업데이트", worksheet=worksheet_name, range=range_name):
            result = safe_execute(update_operation)
            
            success = result.success
            if toot_logger:
                toot_logger.log_sheet_operation("범위 업데이트", worksheet_name, success,
                                             str(result.error) if not success else None)
            return success
    
    # 툿수 관리 시트 전용 메서드들
    def get_toot_users(self) -> List[Dict[str, Any]]:
        """
        툿수 관리 시트에서 사용자 목록 조회

        Returns:
            List[Dict]: 사용자 데이터 리스트
        """
        try:
            # 캐시를 사용하지 않고 최신 데이터 가져오기, 헤더 2행
            return self.get_worksheet_data(self.toot_sheet, config.TOOT_WORKSHEET_NAME, use_cache=False, header_rows=2)
        except Exception as e:
            logger.error(f"툿수 사용자 목록 조회 실패: {e}")
            return []
    
    def batch_update_toot_sheet(self, updates: List[Dict[str, any]]) -> bool:
        """
        배치로 툿수 시트 업데이트 (API 호출 최소화)

        Args:
            updates: 업데이트 정보 리스트
                [{'user_row': 3, 'new_toots': 100, 'reward_given': 10}, ...]

        Returns:
            bool: 성공 여부
        """
        try:
            if not updates:
                return True

            worksheet = self.get_worksheet(self.toot_sheet, config.TOOT_WORKSHEET_NAME)

            # 컬럼 인덱스
            recent_toots_col = 4  # D열: 최근 확인 툿수
            total_rewards_col = 5  # E열: 누적 지급 재화
            last_update_col = 6   # F열: 마지막 업데이트

            current_time = self.get_current_time()

            # 배치 업데이트용 데이터 준비
            batch_data = []
            for update in updates:
                user_row = update['user_row']

                # 툿수 업데이트
                batch_data.append({
                    'range': f'{self._col_to_letter(recent_toots_col)}{user_row}',
                    'values': [[update['new_toots']]]
                })

                # 누적 재화 업데이트 (있는 경우)
                if 'new_total_rewards' in update:
                    batch_data.append({
                        'range': f'{self._col_to_letter(total_rewards_col)}{user_row}',
                        'values': [[update['new_total_rewards']]]
                    })

                # 마지막 업데이트 시간
                batch_data.append({
                    'range': f'{self._col_to_letter(last_update_col)}{user_row}',
                    'values': [[current_time]]
                })

            # 배치 업데이트 실행 (1회 API 호출)
            worksheet.batch_update(batch_data)
            logger.info(f"툿수 시트 배치 업데이트 완료: {len(updates)}명, {len(batch_data)}개 셀")

            return True

        except Exception as e:
            logger.error(f"툿수 시트 배치 업데이트 실패: {e}")
            return False

    def update_user_toots(self, user_row: int, new_toot_count: int, reward_given: int) -> bool:
        """
        사용자의 툿수 정보 업데이트

        Args:
            user_row: 사용자 행 번호 (1부터 시작)
            new_toot_count: 새로운 툿수
            reward_given: 지급된 재화량 (현재는 툿수만 업데이트, 누적 재화는 별도 처리)

        Returns:
            bool: 성공 여부
        """
        try:
            # 컬럼 인덱스 (헤더 기준)
            recent_toots_col = 4  # D열: 최근 확인 툿수

            # 1. 툿수는 항상 업데이트
            if not self.update_cell(self.toot_sheet, config.TOOT_WORKSHEET_NAME, user_row, recent_toots_col, new_toot_count):
                return False

            logger.debug(f"툿수 업데이트 완료: 행 {user_row}, 툿수 {new_toot_count}")

            return True

        except Exception as e:
            logger.error(f"사용자 툿수 정보 업데이트 실패: {e}")
            return False
    
    def update_user_total_rewards(self, user_row: int, reward_amount: int) -> bool:
        """
        사용자의 누적 지급 재화 업데이트 (재화 지급 성공 후 호출)
        
        Args:
            user_row: 사용자 행 번호 (1부터 시작)
            reward_amount: 추가로 지급된 재화량
            
        Returns:
            bool: 성공 여부
        """
        try:
            # 컬럼 인덱스 (헤더 기준)
            total_rewards_col = 5  # E열: 누적 지급 재화
            last_update_col = 6   # F열: 마지막 업데이트

            current_time = self.get_current_time()
            
            # 현재 누적 재화 조회
            worksheet = self.get_worksheet(self.toot_sheet, config.TOOT_WORKSHEET_NAME)
            current_rewards_value = worksheet.cell(user_row, total_rewards_col).value or 0
            current_rewards = self._to_int(current_rewards_value)
            new_total_rewards = current_rewards + reward_amount
            
            # 누적 재화 업데이트
            if not self.update_cell(self.toot_sheet, config.TOOT_WORKSHEET_NAME, user_row, total_rewards_col, new_total_rewards):
                return False
            
            # 마지막 업데이트 시간 업데이트
            if not self.update_cell(self.toot_sheet, config.TOOT_WORKSHEET_NAME, user_row, last_update_col, current_time):
                return False
            
            logger.debug(f"누적 재화 업데이트 완료: 행 {user_row}, +{reward_amount} → 총 {new_total_rewards}")
            
            return True
            
        except Exception as e:
            logger.error(f"누적 재화 업데이트 실패: {e}")
            return False
    
    def find_user_in_toot_sheet(self, user_id: str) -> Optional[Tuple[Dict[str, Any], int]]:
        """
        툿수 관리 시트에서 사용자 찾기
        
        Args:
            user_id: 사용자 ID
            
        Returns:
            Optional[Tuple[Dict, int]]: (사용자 데이터, 행 번호) 또는 None
        """
        try:
            # 사용자 ID를 문자열로 강제 변환
            user_id = str(user_id).strip()
            
            # 캐시된 데이터 사용 (매번 클리어하지 않음)
            users_data = self.get_toot_users()
            
            # 디버깅: 등록된 사용자 목록 로깅
            if logger.isEnabledFor(logging.DEBUG):
                registered_users = [str(user.get(config.TOOT_SHEET_HEADERS['ID'], '')).strip() for user in users_data]
                logger.debug(f"등록된 사용자 목록: {registered_users}")
            
            for index, user_data in enumerate(users_data):
                sheet_user_id = str(user_data.get(config.TOOT_SHEET_HEADERS['ID'], '')).strip()
                if sheet_user_id == user_id:
                    row_number = index + 3  # 헤더 2행이므로 +3 (3행부터 데이터 시작)
                    logger.debug(f"사용자 '{user_id}' 찾음 (행 {row_number})")
                    return user_data, row_number
            
            # 사용자를 찾지 못한 경우 상세 정보 로깅
            logger.warning(f"사용자 '{user_id}'를 툿수 관리 시트에서 찾을 수 없습니다.")
            logger.warning(f"시트에 등록된 사용자 수: {len(users_data)}명")
            
            # 처음 몇 명의 사용자 ID를 로깅 (디버깅용)
            if users_data:
                sample_users = [str(user.get(config.TOOT_SHEET_HEADERS['ID'], '')).strip() for user in users_data[:5]]
                logger.warning(f"등록된 사용자 샘플: {sample_users}")
            
            return None
            
        except Exception as e:
            logger.error(f"툿수 시트에서 사용자 찾기 실패: {e}")
            return None
    
    # 상점봇 시트 연동 메서드들
    def get_shop_users(self) -> List[Dict[str, Any]]:
        """
        상점봇 시트에서 명단 조회

        Returns:
            List[Dict]: 사용자 데이터 리스트
        """
        try:
            # 상점봇 시트도 헤더 2행
            return self.get_worksheet_data(self.shop_sheet, config.SHOP_WORKSHEET_NAME, header_rows=2)
        except Exception as e:
            logger.error(f"상점봇 사용자 목록 조회 실패: {e}")
            return []
    
    def find_user_in_shop_sheet(self, user_id: str) -> Optional[Tuple[Dict[str, Any], int]]:
        """
        상점봇 시트에서 사용자 찾기

        Args:
            user_id: 사용자 ID

        Returns:
            Optional[Tuple[Dict, int]]: (사용자 데이터, 행 번호) 또는 None
        """
        try:
            # 사용자 ID를 문자열로 강제 변환
            user_id = str(user_id).strip()

            users_data = self.get_shop_users()

            for index, user_data in enumerate(users_data):
                if str(user_data.get('아이디', '')).strip() == user_id:
                    row_number = index + 3  # 헤더 2행이므로 +3 (3행부터 데이터 시작)
                    return user_data, row_number

            return None

        except Exception as e:
            logger.error(f"상점봇 시트에서 사용자 찾기 실패: {e}")
            return None
    
    def get_money_column_index(self) -> Optional[int]:
        """
        상점봇 시트에서 소지금 컬럼 인덱스 찾기

        Returns:
            Optional[int]: 소지금 컬럼 인덱스 (1부터 시작) 또는 None
        """
        try:
            worksheet = self.get_worksheet(self.shop_sheet, config.SHOP_WORKSHEET_NAME)
            headers = worksheet.row_values(1)

            # 소지금 관련 키워드로 검색
            money_keywords = ['소지금', '재화', '포인트']
            for keyword in money_keywords:
                for index, header in enumerate(headers):
                    if keyword in str(header):
                        return index + 1  # 1부터 시작하는 인덱스로 변환

            logger.warning("소지금 컬럼을 찾을 수 없습니다.")
            return None

        except Exception as e:
            logger.error(f"소지금 컬럼 찾기 실패: {e}")
            return None

    def update_user_money(self, user_id: str, amount: int) -> bool:
        """
        상점봇 시트에서 사용자의 소지금 업데이트
        
        Args:
            user_id: 사용자 ID
            amount: 추가할 재화량
            
        Returns:
            bool: 성공 여부
        """
        try:
            # 사용자 찾기
            user_data, row_number = self.find_user_in_shop_sheet(user_id)
            if not user_data:
                logger.warning(f"상점봇 시트에서 사용자를 찾을 수 없습니다: {user_id}")
                return False
            
            # 소지금 컬럼 찾기
            money_col = self.get_money_column_index()
            if not money_col:
                logger.error("소지금 컬럼을 찾을 수 없습니다.")
                return False
            
            # 현재 소지금 조회
            worksheet = self.get_worksheet(self.shop_sheet, config.SHOP_WORKSHEET_NAME)
            current_money = worksheet.cell(row_number, money_col).value or 0
            new_money = int(current_money) + amount
            
            # 소지금 업데이트
            success = self.update_cell(self.shop_sheet, config.SHOP_WORKSHEET_NAME, 
                                     row_number, money_col, new_money)
            
            if success:
                logger.info(f"사용자 {user_id}의 소지금 업데이트: {current_money} → {new_money} (+{amount})")
            
            return success
            
        except Exception as e:
            logger.error(f"사용자 소지금 업데이트 실패: {e}")
            return False
    

    def batch_update_shop_rewards(self, updates: List[Dict[str, any]]) -> Tuple[bool, List[int]]:
        """
        배치로 상점 시트의 소지금 업데이트 (API 호출 최소화)

        Args:
            updates: 업데이트 정보 리스트
                [{'user_row': 3, 'money_col': 4, 'new_money': 20}, ...]

        Returns:
            Tuple[bool, List[int]]: (전체 성공 여부, 검증 실패한 RewardResult 인덱스 리스트)
        """
        try:
            if not updates:
                return True, []

            worksheet = self.get_worksheet(self.shop_sheet, config.SHOP_WORKSHEET_NAME)

            # 배치 업데이트용 데이터 준비
            batch_data = []
            verification_ranges: List[str] = []
            verification_meta: List[Tuple[Optional[int], int, str]] = []
            for update in updates:
                user_row = update['user_row']

                # 소지금 업데이트
                if 'new_money' in update:
                    money_col = update['money_col']
                    batch_data.append({
                        'range': f'{self._col_to_letter(money_col)}{user_row}',
                        'values': [[update['new_money']]]
                    })
                    cell_ref = f'{self._col_to_letter(money_col)}{user_row}'
                    verification_ranges.append(cell_ref)
                    verification_meta.append((update.get('result_index'), update['new_money'], 'money'))

            # 배치 업데이트 실행 (1회 API 호출)
            worksheet.batch_update(batch_data)
            logger.info(f"배치 업데이트 완료: {len(updates)}명, {len(batch_data)}개 셀")

            failed_indices: List[int] = []
            if verification_ranges:
                try:
                    verification_results = worksheet.batch_get(verification_ranges)
                    for meta, fetched in zip(verification_meta, verification_results):
                        result_index, expected_value, field = meta
                        if result_index is None:
                            continue

                        actual_value = 0
                        if fetched and fetched[0]:
                            actual_value = self._to_int(fetched[0][0])

                        if actual_value != expected_value and result_index not in failed_indices:
                            failed_indices.append(result_index)
                            logger.warning(
                                "배치 업데이트 검증 실패: result_index=%s, field=%s, expected=%s, actual=%s",
                                result_index,
                                field,
                                expected_value,
                                actual_value,
                            )
                except Exception as verify_error:
                    logger.error(f"배치 업데이트 검증 중 오류: {verify_error}")
                    failed_indices = [idx for idx, _, _ in verification_meta if idx is not None]

            return len(failed_indices) == 0, failed_indices

        except Exception as e:
            logger.error(f"배치 업데이트 실패: {e}")
            failed_indices = [update.get('result_index') for update in updates if update.get('result_index') is not None]
            return False, failed_indices

    def _col_to_letter(self, col_num: int) -> str:
        """컬럼 번호를 문자로 변환 (1 -> A, 2 -> B, ...)"""
        result = ""
        while col_num > 0:
            col_num -= 1
            result = chr(col_num % 26 + 65) + result
            col_num //= 26
        return result

    @staticmethod
    def _to_int(value: Any) -> int:
        """셀 값을 안전하게 정수로 변환"""
        try:
            if value is None:
                return 0
            return int(str(value).replace(',', '').strip())
        except (ValueError, TypeError):
            return 0

    def update_user_money_optimized(self, user_id: str, amount: int, user_row: int, current_money: int, money_col: int) -> bool:
        """
        캐시된 데이터를 사용한 최적화된 소지금 업데이트 (API 호출 최소화)

        Args:
            user_id: 사용자 ID
            amount: 추가할 재화량
            user_row: 사용자 행 번호 (캐시에서 가져온)
            current_money: 현재 소지금 (캐시에서 가져온)

        Returns:
            bool: 성공 여부
        """
        try:
            new_money = current_money + amount

            # 소지금 업데이트 (단일 API 호출!)
            success = self.update_cell(self.shop_sheet, config.SHOP_WORKSHEET_NAME,
                                     user_row, money_col, new_money)

            if success:
                logger.info(f"사용자 {user_id}의 소지금 업데이트: {current_money} → {new_money} (+{amount})")

            return success

        except Exception as e:
            logger.error(f"최적화된 사용자 소지금 업데이트 실패: {e}")
            return False

    def get_current_money(self, user_id: str) -> Optional[int]:
        """
        상점봇 시트에서 사용자의 현재 소지금 조회
        
        Args:
            user_id: 사용자 ID
            
        Returns:
            Optional[int]: 현재 소지금 또는 None
        """
        try:
            user_data, row_number = self.find_user_in_shop_sheet(user_id)
            if not user_data:
                return None
            
            money_col = self.get_money_column_index()
            if not money_col:
                return None
            
            worksheet = self.get_worksheet(self.shop_sheet, config.SHOP_WORKSHEET_NAME)
            current_money = worksheet.cell(row_number, money_col).value or 0
            
            return int(current_money)
            
        except Exception as e:
            logger.error(f"현재 소지금 조회 실패: {e}")
            return None
    
    @staticmethod
    def get_current_time() -> str:
        """
        현재 KST 기준 시간 반환
        
        Returns:
            str: 현재 시간 (YYYY-MM-DD HH:MM:SS 형식)
        """
        return datetime.now(pytz.timezone('Asia/Seoul')).strftime('%Y-%m-%d %H:%M:%S')
    
    def clear_cache(self):
        """워크시트 캐시 초기화"""
        self._worksheets_cache.clear()
        logger.debug("워크시트 캐시가 초기화되었습니다.")
    
    def clear_toot_sheet_cache(self):
        """툿수 시트 캐시 초기화"""
        cache_key = f"{self.toot_sheet.title}_{config.TOOT_WORKSHEET_NAME}"
        if cache_key in self._worksheets_cache:
            del self._worksheets_cache[cache_key]
            logger.debug("툿수 시트 캐시 초기화 완료")

    def clear_shop_sheet_cache(self):
        """상점봇 시트 캐시 초기화"""
        cache_key = f"{self.shop_sheet.title}_{config.SHOP_WORKSHEET_NAME}"
        if cache_key in self._worksheets_cache:
            del self._worksheets_cache[cache_key]
            logger.debug("상점봇 시트 캐시 초기화 완료")
    
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
            'sheets_found': []
        }
        
        try:
            # 툿수 관리 시트 검증
            self._validate_toot_sheet_structure(validation_results)
            
            # 상점봇 시트 검증
            self._validate_shop_sheet_structure(validation_results)
            
        except Exception as e:
            validation_results['errors'].append(f"시트 구조 검증 중 오류: {str(e)}")
            validation_results['valid'] = False
        
        return validation_results
    
    def _validate_toot_sheet_structure(self, results: Dict):
        """툿수 관리 시트 구조 검증"""
        try:
            worksheet = self.get_worksheet(self.toot_sheet, config.TOOT_WORKSHEET_NAME)
            results['sheets_found'].append(f"툿수 관리 시트 ID: {config.TOOT_SHEET_ID}")

            if worksheet.row_count > 0:
                headers = worksheet.row_values(1)
                expected_headers = list(config.TOOT_SHEET_HEADERS.values())

                for expected_header in expected_headers:
                    if expected_header not in headers:
                        results['errors'].append(f"툿수 관리 시트에 '{expected_header}' 헤더가 없습니다.")
                        results['valid'] = False

        except Exception as e:
            results['errors'].append(f"툿수 관리 시트 검증 실패: {str(e)}")
            results['valid'] = False

    def _validate_shop_sheet_structure(self, results: Dict):
        """상점봇 시트 구조 검증"""
        try:
            worksheet = self.get_worksheet(self.shop_sheet, config.SHOP_WORKSHEET_NAME)
            results['sheets_found'].append(f"상점봇 시트 ID: {config.SHOP_SHEET_ID}")

            if worksheet.row_count > 0:
                headers = worksheet.row_values(1)
                required_headers = ['아이디', '이름']

                for header in required_headers:
                    if header not in headers:
                        results['errors'].append(f"상점봇 명단 시트에 '{header}' 헤더가 없습니다.")
                        results['valid'] = False

                # 소지금 헤더 확인
                money_header_found = False
                money_keywords = ['소지금', '재화', '포인트']
                for keyword in money_keywords:
                    if any(keyword in str(header) for header in headers):
                        money_header_found = True
                        break

                if not money_header_found:
                    results['warnings'].append("상점봇 명단 시트에서 소지금 관련 헤더를 찾을 수 없습니다.")

        except Exception as e:
            results['errors'].append(f"상점봇 시트 검증 실패: {str(e)}")
            results['valid'] = False
    
    def get_system_stats(self) -> Dict[str, Any]:
        """
        시스템 통계 조회
        
        Returns:
            Dict: 시스템 통계
        """
        try:
            toot_users = self.get_toot_users()
            shop_users = self.get_shop_users()
            
            # 툿수 시트 통계
            total_rewards_given = 0
            users_with_toots = 0
            
            for user in toot_users:
                toots = user.get(config.TOOT_SHEET_HEADERS['RECENT_TOOTS'], 0)
                rewards = user.get(config.TOOT_SHEET_HEADERS['TOTAL_REWARDS'], 0)
                
                if toots and int(toots) > 0:
                    users_with_toots += 1
                
                if rewards:
                    total_rewards_given += int(rewards)
            
            return {
                'toot_users_total': len(toot_users),
                'shop_users_total': len(shop_users),
                'users_with_toots': users_with_toots,
                'total_rewards_given': total_rewards_given,
                'timestamp': self.get_current_time()
            }
            
        except Exception as e:
            logger.error(f"시스템 통계 조회 실패: {e}")
            return {}
    
    def check_user_registration(self, user_id: str) -> Dict[str, Any]:
        """
        사용자 등록 상태 확인
        
        Args:
            user_id: 확인할 사용자 ID
            
        Returns:
            Dict: 등록 상태 정보
        """
        try:
            result = {
                'user_id': user_id,
                'registered_in_toot': False,
                'registered_in_shop': False,
                'toot_sheet_data': None,
                'shop_sheet_data': None,
                'total_toot_users': 0,
                'total_shop_users': 0
            }
            
            # 툿수 관리 시트 확인
            try:
                toot_users = self.get_toot_users()
                result['total_toot_users'] = len(toot_users)
                
                # 사용자 ID를 문자열로 강제 변환
                user_id_str = str(user_id).strip()
                
                for user_data in toot_users:
                    if str(user_data.get(config.TOOT_SHEET_HEADERS['ID'], '')).strip() == user_id_str:
                        result['registered_in_toot'] = True
                        result['toot_sheet_data'] = user_data
                        break
            except Exception as e:
                logger.error(f"툿수 시트 사용자 확인 실패: {e}")
            
            # 상점봇 시트 확인
            try:
                shop_users = self.get_shop_users()
                result['total_shop_users'] = len(shop_users)
                
                # 사용자 ID를 문자열로 강제 변환
                user_id_str = str(user_id).strip()
                
                for user_data in shop_users:
                    if str(user_data.get('아이디', '')).strip() == user_id_str:
                        result['registered_in_shop'] = True
                        result['shop_sheet_data'] = user_data
                        break
            except Exception as e:
                logger.error(f"상점봇 시트 사용자 확인 실패: {e}")
            
            return result
            
        except Exception as e:
            logger.error(f"사용자 등록 상태 확인 실패: {e}")
            return {
                'user_id': user_id,
                'error': str(e)
            }


# 전역 인스턴스 (싱글톤 패턴)
_global_sheets_manager: Optional[SheetsManager] = None


def get_sheets_manager() -> SheetsManager:
    """전역 SheetsManager 인스턴스 반환"""
    global _global_sheets_manager
    if _global_sheets_manager is None:
        _global_sheets_manager = SheetsManager()
    return _global_sheets_manager


# 편의 함수들 (하위 호환성)
def connect_to_sheets() -> SheetsManager:
    """시트 연결 (하위 호환성 함수)"""
    return get_sheets_manager()


def test_sheets_connection():
    """시트 연결 테스트"""
    try:
        sheets_manager = get_sheets_manager()
        
        # 기본 연결 테스트
        logger.info("시트 연결 테스트 시작...")
        
        # 툿수 관리 시트 테스트
        toot_users = sheets_manager.get_toot_users()
        logger.info(f"툿수 관리 시트 사용자 수: {len(toot_users)}")
        
        # 상점봇 시트 테스트
        shop_users = sheets_manager.get_shop_users()
        logger.info(f"상점봇 시트 사용자 수: {len(shop_users)}")
        
        # 구조 검증
        validation_result = sheets_manager.validate_sheet_structure()
        if validation_result['valid']:
            logger.info("✅ 시트 구조 검증 성공")
        else:
            logger.warning("⚠️ 시트 구조 문제 발견:")
            for error in validation_result['errors']:
                logger.warning(f"  - {error}")
        
        # 시스템 통계
        stats = sheets_manager.get_system_stats()
        logger.info(f"시스템 통계: {stats}")
        
        logger.info("시트 연결 테스트 완료")
        return True
        
    except Exception as e:
        logger.error(f"시트 연결 테스트 실패: {e}")
        return False


if __name__ == "__main__":
    # 시트 연결 테스트 실행
    test_sheets_connection()
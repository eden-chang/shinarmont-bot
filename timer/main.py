import os
import gspread
from gspread.utils import rowcol_to_a1
from oauth2client.service_account import ServiceAccountCredentials
from apscheduler.schedulers.background import BackgroundScheduler
import time
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv
from datetime import datetime
from colorama import Fore, Style, init

# colorama 초기화 (Windows 호환)
init(autoreset=True)

# .env 파일 로드
load_dotenv()

# 환경 변수 또는 직접 설정
CREDENTIALS_PATH = os.getenv('GOOGLE_CREDENTIALS_PATH', './credentials.json')
BOT_SPREADSHEET_ID = os.getenv('BOT_SPREADSHEET_ID', '1AM5NF7wloj5XkP1KTXhsovquMgdkTq2GrfEkRia-zFY')
RESET_WORKSHEET = os.getenv('RESET_WORKSHEET', '관리')
BOT_WORKSHEET = os.getenv('BOT_WORKSHEET', '메인 시트')
RESET_COLUMN = os.getenv('RESET_COLUMN', '추적,조사')  # 0으로 초기화할 컬럼
BLANK_COLUMN = os.getenv('BLANK_COLUMN', '출석')  # 빈칸으로 초기화할 컬럼

def log_info(message: str):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"{Fore.CYAN}[INFO]{Style.RESET_ALL} {Fore.WHITE}{timestamp}{Style.RESET_ALL} | {message}")

def log_success(message: str):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"{Fore.GREEN}[SUCCESS]{Style.RESET_ALL} {Fore.WHITE}{timestamp}{Style.RESET_ALL} | {message}")

def log_warning(message: str):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"{Fore.YELLOW}[WARNING]{Style.RESET_ALL} {Fore.WHITE}{timestamp}{Style.RESET_ALL} | {message}")

def log_error(message: str):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"{Fore.RED}[ERROR]{Style.RESET_ALL} {Fore.WHITE}{timestamp}{Style.RESET_ALL} | {message}")

class TimerBot:
    def __init__(self):
        self.client = None
        self.scheduler = None
        self.is_running_reset = False
        self._worksheet = None  # 워크시트 핸들 캐시 (반복 메타데이터 요청 방지)

    def _get_cached_worksheet(self):
        # open_by_key 메타데이터 호출을 매번 반복하지 않도록 핸들을 캐싱해서 재사용
        if self._worksheet is None:
            self._worksheet = self.get_worksheet(BOT_SPREADSHEET_ID, RESET_WORKSHEET)
        return self._worksheet

    def initialize(self):
        try:
            scope = ['https://www.googleapis.com/auth/spreadsheets']
            creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_PATH, scope)
            self.client = gspread.authorize(creds)
            self.scheduler = BackgroundScheduler(timezone="Asia/Seoul")
            log_success("Bot initialized successfully")
        except Exception as e:
            log_error(f"Failed to initialize bot: {e}")
            raise e

    def get_worksheet(self, spreadsheet_id: str, worksheet_name: str):
        try:
            spreadsheet = self.client.open_by_key(spreadsheet_id)
            return spreadsheet.worksheet(worksheet_name)
        except gspread.WorksheetNotFound:
            log_error(f"Worksheet '{worksheet_name}' not found")
            return None
        except Exception as e:
            log_error(f"Error accessing spreadsheet or worksheet: {e}")
            return None

    def reset_column_values(self, retry_count: int = 0):
        if self.is_running_reset:
            log_warning('Reset already in progress, skipping')
            return

        self.is_running_reset = True
        max_retries = 3
        retry_delays = [60, 120, 180]
        # 컬럼별 초기화 값 매핑: 0으로 세팅할 컬럼과 빈칸으로 세팅할 컬럼
        zero_columns = [col.strip() for col in RESET_COLUMN.split(',') if col.strip()]
        blank_columns = [col.strip() for col in BLANK_COLUMN.split(',') if col.strip()]
        column_value_map = {col: '0' for col in zero_columns}
        column_value_map.update({col: '' for col in blank_columns})

        try:
            log_info(
                f"Starting reset in worksheet '{RESET_WORKSHEET}' | "
                f"0으로 초기화: {zero_columns} | 빈칸으로 초기화: {blank_columns}"
            )

            # 워크시트 핸들을 캐싱해서 재사용 (open_by_key 반복 호출 방지)
            worksheet = self._get_cached_worksheet()
            if not worksheet:
                raise Exception(f"Worksheet '{RESET_WORKSHEET}' not found")

            # 읽기 API 1회로 헤더와 데이터 행 수를 함께 확보
            sheet_data = worksheet.get_all_values()

            if len(sheet_data) <= 2:
                log_warning("No data rows to reset (need at least 3 rows: header, description, and data)")
                self.is_running_reset = False
                return

            headers = sheet_data[0]
            data_row_count = len(sheet_data) - 2  # 1행(헤더)과 2행(설명) 제외

            # 각 열의 인덱스와 초기화 값 찾기
            col_targets = []  # (col_idx, value) 튜플 목록
            for col_name, value in column_value_map.items():
                if col_name in headers:
                    col_idx = headers.index(col_name) + 1  # gspread는 1-based
                    col_targets.append((col_idx, value))
                else:
                    log_warning(f"Column '{col_name}' not found in headers, skipping")

            if not col_targets:
                log_warning("No valid columns found to reset")
                self.is_running_reset = False
                return

            # 열 단위 A1 범위로 batch_update -> 쓰기 API 1회, 사이 열 침범 없음
            start_row = 3
            end_row = data_row_count + 2
            batch_data = []
            for col_idx, value in col_targets:
                start_a1 = rowcol_to_a1(start_row, col_idx)
                end_a1 = rowcol_to_a1(end_row, col_idx)
                batch_data.append({
                    'range': f'{start_a1}:{end_a1}',
                    'values': [[value] for _ in range(data_row_count)],
                })

            worksheet.batch_update(batch_data, value_input_option='RAW')
            log_success(
                f"Successfully reset {data_row_count} rows | "
                f"0으로 초기화: {zero_columns} | 빈칸으로 초기화: {blank_columns}"
            )

        except Exception as e:
            log_error(f"Error resetting columns (attempt {retry_count + 1}): {e}")
            self._worksheet = None  # 캐시 무효화 -> 다음 시도에서 워크시트 재확보
            if retry_count < max_retries and isinstance(e, gspread.exceptions.APIError) and '429' in str(e):
                delay = retry_delays[retry_count]
                log_warning(f"API limit reached. Retrying in {delay} seconds...")
                time.sleep(delay)
                self.is_running_reset = False
                self.reset_column_values(retry_count + 1)
            else:
                log_error('Max retries reached or non-API-limit error occurred')
        finally:
            self.is_running_reset = False

    def start(self):
        # KST 기준 매일 0시 0분에 리셋 실행
        self.scheduler.add_job(self.reset_column_values, 'cron', hour=0, minute=0)

        self.scheduler.start()
        log_success(f"Timer bot started. Scheduled daily reset at 00:00 KST")
        zero_columns = [col.strip() for col in RESET_COLUMN.split(',') if col.strip()]
        blank_columns = [col.strip() for col in BLANK_COLUMN.split(',') if col.strip()]
        log_info(
            f"Target: Worksheet '{RESET_WORKSHEET}' | "
            f"0으로 초기화: {zero_columns} | 빈칸으로 초기화: {blank_columns}"
        )

    def test_run(self):
        log_info('Running manual test reset')
        self.reset_column_values()


def main():
    print(f"\n{Fore.MAGENTA}{'='*60}{Style.RESET_ALL}")
    print(f"{Fore.MAGENTA}  Timer Bot for Google Sheets{Style.RESET_ALL}")
    print(f"{Fore.MAGENTA}{'='*60}{Style.RESET_ALL}\n")

    bot = TimerBot()
    try:
        bot.initialize()

        # 테스트 실행을 원하면 주석 해제
        # bot.test_run()

        bot.start()

        try:
            while True:
                time.sleep(2)
        except (KeyboardInterrupt, SystemExit):
            print()  # 줄바꿈
            log_warning('Received shutdown signal')
            bot.scheduler.shutdown()
            log_info('Bot shut down gracefully')
    except Exception as e:
        log_error(f'Failed to start bot: {e}')
        os._exit(1)


if __name__ == "__main__":
    main()
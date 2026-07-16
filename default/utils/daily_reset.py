"""
일일 리셋 유틸리티
매일 00:00 KST에 '오늘의 자백' 컬럼을 0으로 리셋하는 기능을 제공합니다.
"""

import os
import sys
import schedule # type: ignore
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

# 경로 설정
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

try:
    from config.settings import config
    from utils.logging_config import logger
    from utils.cache_manager import bot_cache
except ImportError:
    import logging
    logger = logging.getLogger('daily_reset')
    
    # Mock bot_cache for fallback
    class MockBotCache:
        def cleanup_old_fortunes(self, kst_timezone=None):
            return 0
    bot_cache = MockBotCache()


class DailyResetManager:
    """일일 리셋 관리자"""
    
    def __init__(self, sheets_manager=None):
        """
        초기화
        
        Args:
            sheets_manager: Google Sheets 관리자
        """
        self.sheets_manager = sheets_manager
        self.is_running = False
        # KST 타임존 설정
        self.kst = timezone(timedelta(hours=9))
    
    def reset_confession_counters(self) -> bool:
        """
        모든 사용자의 '오늘의 자백' 카운터를 0으로 리셋
        
        Returns:
            bool: 성공 여부
        """
        try:
            if not self.sheets_manager:
                logger.error("Google Sheets 연결이 설정되지 않았습니다.")
                return False
            
            worksheet_name = config.get_worksheet_name('FAVOR')
            if not worksheet_name:
                logger.error("총애도 워크시트 이름을 찾을 수 없습니다.")
                return False
            
            worksheet = self.sheets_manager.get_worksheet(worksheet_name)
            if not worksheet:
                logger.error("워크시트를 찾을 수 없습니다.")
                return False
            
            # 모든 데이터 가져오기
            all_values = worksheet.get_all_values()
            
            # 헤더 행 찾기
            headers = all_values[0] if all_values else []
            confession_col_index = None
            
            for i, header in enumerate(headers):
                if header == '오늘의 자백':
                    confession_col_index = i
                    break
            
            if confession_col_index is None:
                logger.error("'오늘의 자백' 컬럼을 찾을 수 없습니다.")
                return False
            
            # 모든 사용자의 '오늘의 자백' 카운터를 0으로 리셋
            reset_count = 0
            for i, row in enumerate(all_values[2:], start=3):  # 헤더와 설명 제외
                if len(row) > confession_col_index:
                    # 현재 값 확인
                    current_value = row[confession_col_index] if len(row) > confession_col_index else '0'
                    try:
                        current_count = int(str(current_value).strip()) if current_value else 0
                        if current_count > 0:
                            # 0으로 리셋
                            worksheet.update_cell(i, confession_col_index + 1, '0')
                            reset_count += 1
                    except ValueError:
                        # 숫자가 아닌 경우 0으로 설정
                        worksheet.update_cell(i, confession_col_index + 1, '0')
                        reset_count += 1
            
            logger.info(f"일일 자백 카운터 리셋 완료: {reset_count}명의 사용자")
            return True
            
        except Exception as e:
            logger.error(f"일일 자백 카운터 리셋 실패: {e}")
            return False
    
    def cleanup_old_fortune_cache(self) -> bool:
        """
        오래된 운세 캐시 정리 (KST 기준)
        
        Returns:
            bool: 성공 여부
        """
        try:
            cleaned_count = bot_cache.cleanup_old_fortunes(self.kst)
            if cleaned_count > 0:
                logger.info(f"일일 운세 캐시 정리 완료: {cleaned_count}개 삭제")
            else:
                logger.debug("정리할 오래된 운세 캐시가 없음")
            return True
        except Exception as e:
            logger.error(f"운세 캐시 정리 실패: {e}")
            return False
    
    def daily_midnight_reset(self) -> bool:
        """
        매일 자정에 실행되는 리셋 작업
        
        Returns:
            bool: 성공 여부
        """
        logger.info("KST 0시 일일 리셋 시작")
        
        success_count = 0
        
        # 1. 자백 카운터 리셋
        if self.reset_confession_counters():
            success_count += 1
        
        # 2. 오래된 운세 캐시 정리
        if self.cleanup_old_fortune_cache():
            success_count += 1
        
        if success_count == 2:
            logger.info("KST 0시 일일 리셋 모두 완료")
            return True
        else:
            logger.warning(f"KST 0시 일일 리셋 일부 실패: {success_count}/2 성공")
            return False
    
    def schedule_daily_reset(self) -> None:
        """매일 00:00 KST에 일일 리셋 스케줄링"""
        try:
            # 매일 00:00에 리셋 실행 (시스템 시간 기준)
            # KST는 UTC+9이므로, 시스템이 KST로 설정되어 있다고 가정
            schedule.every().day.at("00:00").do(self.daily_midnight_reset)
            
            logger.info("일일 리셋 스케줄 등록 완료 (매일 00:00 KST)")
            
        except Exception as e:
            logger.error(f"일일 리셋 스케줄 등록 실패: {e}")
    
    def start_scheduler(self) -> None:
        """스케줄러 시작"""
        if self.is_running:
            logger.warning("스케줄러가 이미 실행 중입니다.")
            return
        
        self.is_running = True
        logger.info("일일 리셋 스케줄러 시작 (자백 카운터 + 운세 캐시 정리)")
        
        try:
            while self.is_running:
                schedule.run_pending()
                time.sleep(60)  # 1분마다 체크
        except KeyboardInterrupt:
            logger.info("스케줄러 중단 요청됨")
        except Exception as e:
            logger.error(f"스케줄러 실행 중 오류: {e}")
        finally:
            self.is_running = False
            logger.info("일일 리셋 스케줄러 종료")
    
    def stop_scheduler(self) -> None:
        """스케줄러 중지"""
        self.is_running = False
        logger.info("스케줄러 중지 요청됨")


def create_daily_reset_manager(sheets_manager=None) -> DailyResetManager:
    """
    일일 리셋 관리자 생성
    
    Args:
        sheets_manager: Google Sheets 관리자
        
    Returns:
        DailyResetManager: 일일 리셋 관리자 인스턴스
    """
    return DailyResetManager(sheets_manager)


def run_daily_reset_scheduler(sheets_manager=None) -> None:
    """
    일일 리셋 스케줄러 실행 (별도 프로세스로 실행 가능)
    
    Args:
        sheets_manager: Google Sheets 관리자
    """
    manager = create_daily_reset_manager(sheets_manager)
    manager.schedule_daily_reset()
    manager.start_scheduler()


if __name__ == "__main__":
    # 독립 실행 시 테스트
    logger.info("일일 리셋 유틸리티 테스트 시작")
    
    # 스케줄러 실행 (실제 사용 시에는 sheets_manager 필요)
    try:
        run_daily_reset_scheduler()
    except KeyboardInterrupt:
        logger.info("테스트 종료") 
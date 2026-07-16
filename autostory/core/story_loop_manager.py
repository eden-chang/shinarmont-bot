"""
스토리 스크립트 자동 출력 루프 매니저
워크시트의 스크립트를 순차적으로 자동 송출합니다.
"""

import os
import sys
import time
import asyncio
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Set
from dataclasses import dataclass
import pytz

# 프로젝트 루트 경로 설정
current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(current_dir)

try:
    from config.settings import config
    from utils.logging_config import get_logger
    from utils.datetime_utils import format_datetime_korean
    from core.sheets_client import GoogleSheetsClient, StoryScriptData
    from core.mastodon_client import MultiMastodonManager, TootResult
    from core.drive_client import GoogleDriveClient
except ImportError as e:
    print(f"❌ 필수 모듈 임포트 실패: {e}")
    sys.exit(1)

logger = get_logger(__name__)


@dataclass
class StorySession:
    """
    스토리 진행 세션 정보
    """
    worksheet_name: str
    scripts: List[StoryScriptData]
    current_index: int = 0
    start_time: Optional[datetime] = None
    last_post_time: Optional[datetime] = None
    total_posts: int = 0
    is_active: bool = False
    
    def get_current_script(self) -> Optional[StoryScriptData]:
        """현재 송출할 스크립트 반환"""
        if 0 <= self.current_index < len(self.scripts):
            return self.scripts[self.current_index]
        return None
    
    def advance_to_next(self) -> bool:
        """다음 스크립트로 이동, 끝에 도달하면 False 반환"""
        self.current_index += 1
        return self.current_index < len(self.scripts)
    
    def get_progress(self) -> Dict[str, Any]:
        """진행 상황 반환"""
        return {
            'worksheet_name': self.worksheet_name,
            'current_index': self.current_index,
            'total_scripts': len(self.scripts),
            'progress_percent': (self.current_index / max(1, len(self.scripts))) * 100,
            'is_active': self.is_active,
            'total_posts': self.total_posts,
            'start_time': self.start_time.isoformat() if self.start_time else None,
            'last_post_time': self.last_post_time.isoformat() if self.last_post_time else None
        }


class StoryLoopManager:
    """
    스토리 스크립트 자동 출력 루프 매니저
    """
    
    def __init__(self):
        """StoryLoopManager 초기화"""
        self.sheets_client: Optional[GoogleSheetsClient] = None
        self.mastodon_manager: Optional[MultiMastodonManager] = None
        self.drive_client: Optional[GoogleDriveClient] = None

        # 세션 관리
        self.active_sessions: Dict[str, StorySession] = {}
        self.session_threads: Dict[str, threading.Thread] = {}

        # 상태 관리
        self.is_running = False
        self._stop_event = threading.Event()

        # 통계
        self.stats = {
            'total_sessions': 0,
            'completed_sessions': 0,
            'total_posts': 0,
            'successful_posts': 0,
            'failed_posts': 0,
            'start_time': None
        }

        logger.info("스토리 루프 매니저 초기화 완료")
    
    def initialize(self, sheets_client: GoogleSheetsClient, mastodon_manager: MultiMastodonManager) -> bool:
        """
        매니저 초기화

        Args:
            sheets_client: Google Sheets 클라이언트
            mastodon_manager: 마스토돈 매니저

        Returns:
            bool: 초기화 성공 여부
        """
        try:
            self.sheets_client = sheets_client
            self.mastodon_manager = mastodon_manager

            # 연결 테스트
            if not self.sheets_client.service:
                if not self.sheets_client.authenticate():
                    logger.error("Google Sheets 인증 실패")
                    return False

            if not self.mastodon_manager.check_connection():
                logger.error("마스토돈 연결 확인 실패")
                return False

            # 이미지 업로드 기능이 활성화된 경우 드라이브 클라이언트 초기화
            if config.UPLOAD_IMAGE:
                if not config.GOOGLE_DRIVE_IMAGE_FOLDER_ID:
                    logger.error("UPLOAD_IMAGE가 활성화되었지만 GOOGLE_DRIVE_IMAGE_FOLDER_ID가 설정되지 않았습니다")
                    return False

                from core.drive_client import get_drive_client
                try:
                    self.drive_client = get_drive_client()
                    logger.info("✅ 구글 드라이브 클라이언트 초기화 완료")
                except Exception as e:
                    logger.error(f"구글 드라이브 클라이언트 초기화 실패: {e}")
                    return False

            logger.info("스토리 루프 매니저 초기화 성공")
            return True

        except Exception as e:
            logger.error(f"스토리 루프 매니저 초기화 실패: {e}")
            return False
    
    def start_story_session(self, worksheet_name: str) -> tuple[bool, Optional[str]]:
        """
        스토리 세션 시작

        Args:
            worksheet_name: 워크시트 이름

        Returns:
            tuple[bool, Optional[str]]: (성공 여부, 에러 메시지)
        """
        try:
            # 이미 진행 중인 세션이 있는지 확인
            if worksheet_name in self.active_sessions:
                error_msg = f"워크시트 '{worksheet_name}' 세션이 이미 진행 중입니다"
                logger.warning(error_msg)
                return False, error_msg
            
            logger.info(f"워크시트 '{worksheet_name}' 스토리 세션 시작...")
            
            # 워크시트에서 스크립트 데이터 조회
            scripts = self.sheets_client.fetch_story_scripts_from_worksheet(worksheet_name)
            if not scripts:
                error_msg = f"워크시트 '{worksheet_name}'에서 스크립트를 찾을 수 없습니다"
                logger.error(error_msg)
                return False, error_msg
            
            # 유효한 스크립트만 필터링
            valid_scripts = [script for script in scripts if script.is_valid]
            if not valid_scripts:
                error_msg = f"워크시트 '{worksheet_name}'에 유효한 스크립트가 없습니다"
                logger.error(error_msg)
                return False, error_msg

            logger.info(f"워크시트 '{worksheet_name}'에서 유효한 스크립트 {len(valid_scripts)}개 발견")

            # 이미지 업로드 기능이 활성화된 경우 이미지 파일 사전 검증
            if config.UPLOAD_IMAGE and self.drive_client:
                logger.info("이미지 파일 사전 검증 시작...")
                image_filenames = [script.image_filename for script in valid_scripts if script.image_filename]

                if image_filenames:
                    # 모든 이미지 파일이 존재하는지 검증
                    validation_results = self.drive_client.validate_files_exist(image_filenames)
                    missing_files = [filename for filename, exists in validation_results.items() if not exists]

                    if missing_files:
                        error_msg = f"{worksheet_name} 스토리 진행 실패. "
                        for filename in missing_files:
                            error_msg += f"{filename}.png 혹은 {filename}.jpg 파일이 지정된 이미지 폴더에 존재하지 않습니다. "
                        error_msg += "재확인 바랍니다."

                        logger.error(error_msg)

                        # 관리자에게 알림 전송 (이모지 없이)
                        try:
                            admin_id = getattr(config, 'SYSTEM_ADMIN_ID', None)
                            if admin_id and self.mastodon_manager:
                                self.mastodon_manager.post_toot(
                                    content=f"@{admin_id} {error_msg}",
                                    visibility='direct',
                                    validate_content=False
                                )
                        except Exception as e:
                            logger.error(f"관리자 알림 전송 실패: {e}")

                        return False, error_msg

                    logger.info(f"모든 이미지 파일 검증 완료 ({len(image_filenames)}개)")
                else:
                    logger.info("이미지를 포함한 스크립트가 없습니다")
            
            # 세션 생성
            session = StorySession(
                worksheet_name=worksheet_name,
                scripts=valid_scripts,
                start_time=datetime.now(pytz.timezone('Asia/Seoul')),
                is_active=True
            )
            
            self.active_sessions[worksheet_name] = session
            self.stats['total_sessions'] += 1
            
            # 세션 스레드 시작
            thread = threading.Thread(
                target=self._run_story_session,
                args=(session,),
                name=f"StorySession-{worksheet_name}",
                daemon=True
            )
            
            self.session_threads[worksheet_name] = thread
            thread.start()

            logger.info(f"워크시트 '{worksheet_name}' 스토리 세션 시작됨")
            return True, None

        except Exception as e:
            error_msg = f"스토리 세션 시작 실패: {str(e)}"
            logger.error(error_msg)
            return False, error_msg
    
    def _run_story_session(self, session: StorySession) -> None:
        """
        스토리 세션 실행 (스레드에서 실행됨)
        
        Args:
            session: 스토리 세션
        """
        try:
            logger.info(f"스토리 세션 '{session.worksheet_name}' 실행 시작")
            
            while session.is_active and not self._stop_event.is_set():
                current_script = session.get_current_script()
                if not current_script:
                    # 모든 스크립트 송출 완료
                    logger.info(f"워크시트 '{session.worksheet_name}' 모든 스크립트 송출 완료")
                    break
                
                # 첫 번째 스크립트가 아닌 경우 현재 스크립트의 간격만큼 대기
                if session.current_index > 0 and not self._stop_event.is_set():
                    wait_time = current_script.interval
                    logger.info(f"이전 문구 송출 후 현재 문구까지 {wait_time}초 대기...")
                    
                    # 중단 신호 확인하면서 대기
                    for _ in range(wait_time):
                        if self._stop_event.is_set():
                            break
                        time.sleep(1)
                
                # 스크립트 송출
                success = self._send_script(session, current_script)
                
                if success:
                    session.total_posts += 1
                    session.last_post_time = datetime.now(pytz.timezone('Asia/Seoul'))
                    self.stats['successful_posts'] += 1
                    logger.info(f"스크립트 송출 성공: {current_script.account} - '{current_script.script[:50]}...'")
                else:
                    self.stats['failed_posts'] += 1
                    logger.error(f"스크립트 송출 실패: {current_script.account} - '{current_script.script[:50]}...'")
                
                self.stats['total_posts'] += 1
                
                # 다음 스크립트로 이동
                if not session.advance_to_next():
                    break
            
            # 세션 완료 처리
            session.is_active = False
            self.stats['completed_sessions'] += 1
            
            logger.info(f"스토리 세션 '{session.worksheet_name}' 완료 - 총 {session.total_posts}개 송출")
            
        except Exception as e:
            logger.error(f"스토리 세션 '{session.worksheet_name}' 실행 중 오류: {e}")
            session.is_active = False
        finally:
            # 세션 정리
            if session.worksheet_name in self.active_sessions:
                del self.active_sessions[session.worksheet_name]
            if session.worksheet_name in self.session_threads:
                del self.session_threads[session.worksheet_name]
    
    def _send_script(self, session: StorySession, script: StoryScriptData) -> bool:
        """
        스크립트 송출

        Args:
            session: 스토리 세션
            script: 송출할 스크립트

        Returns:
            bool: 송출 성공 여부
        """
        try:
            media_ids = None

            # 이미지 업로드 기능이 활성화되고 이미지 파일명이 있는 경우
            if config.UPLOAD_IMAGE and script.image_filename and self.drive_client:
                try:
                    logger.info(f"이미지 다운로드 시작: {script.image_filename}")

                    # 구글 드라이브에서 이미지 다운로드
                    image_data = self.drive_client.download_file_by_name(script.image_filename)

                    if image_data:
                        # 파일 확장자로 MIME 타입 추정
                        mime_type = 'image/jpeg'  # 기본값
                        filename_lower = script.image_filename.lower()

                        if filename_lower.endswith('.png'):
                            mime_type = 'image/png'
                        elif filename_lower.endswith(('.jpg', '.jpeg')):
                            mime_type = 'image/jpeg'
                        elif filename_lower.endswith('.gif'):
                            mime_type = 'image/gif'
                        elif filename_lower.endswith('.webp'):
                            mime_type = 'image/webp'
                        else:
                            # 확장자가 없는 경우 기본값 사용 (jpeg)
                            logger.debug(f"확장자 없음, 기본 MIME 타입 사용: {mime_type}")

                        # 이미지 크기 확인
                        image_size_mb = len(image_data) / (1024 * 1024)
                        logger.info(f"이미지 크기: {image_size_mb:.2f}MB")

                        if image_size_mb > 10:
                            logger.error(f"이미지 크기 초과: {script.image_filename} ({image_size_mb:.2f}MB > 10MB)")
                            logger.warning("텍스트만 송출합니다")
                        else:
                            # 마스토돈에 이미지 업로드
                            logger.info(f"마스토돈에 이미지 업로드 시작: {script.image_filename}")
                            media_result = self.mastodon_manager.upload_media_for_account(
                                account_name=script.account,
                                media_data=image_data,
                                mime_type=mime_type,
                                description=f"이미지: {script.image_filename}"
                            )

                            if media_result:
                                media_ids = [str(media_result.get('id'))]
                                logger.info(f"이미지 업로드 성공: {script.image_filename}")
                            else:
                                logger.error(f"이미지 업로드 실패: {script.image_filename}")
                                logger.warning("텍스트만 송출합니다")
                    else:
                        logger.error(f"이미지 다운로드 실패: {script.image_filename}")
                        logger.warning("텍스트만 송출합니다")

                except Exception as e:
                    logger.error(f"이미지 처리 중 예상치 못한 오류: {script.image_filename} - {e}")
                    logger.warning("텍스트만 송출합니다")
                    # 이미지 처리 실패해도 텍스트는 송출 계속

            # 마스토돈에 툿 포스팅 (unlisted로)
            result = self.mastodon_manager.post_scheduled_toot(
                content=script.script,
                account_name=script.account,
                visibility='unlisted',  # 요구사항에 따라 unlisted로 설정
                media_ids=media_ids
            )

            if result.success:
                media_info = f" (이미지: {script.image_filename})" if media_ids else ""
                logger.info(f"✅ {script.account} 계정으로 툿 송출 성공: {result.toot_url}{media_info}")
                return True
            else:
                logger.error(f"❌ {script.account} 계정 툿 송출 실패: {result.error_message}")
                return False

        except Exception as e:
            logger.error(f"스크립트 송출 중 오류: {e}")
            return False
    
    def stop_story_session(self, worksheet_name: str) -> tuple[bool, Optional[str]]:
        """
        특정 스토리 세션 중지

        Args:
            worksheet_name: 워크시트 이름

        Returns:
            tuple[bool, Optional[str]]: (중지 성공 여부, 에러 메시지)
        """
        try:
            if worksheet_name not in self.active_sessions:
                error_msg = f"워크시트 '{worksheet_name}' 세션을 찾을 수 없습니다"
                logger.warning(error_msg)
                return False, error_msg

            # 세션 비활성화
            session = self.active_sessions[worksheet_name]
            session.is_active = False

            logger.info(f"워크시트 '{worksheet_name}' 스토리 세션 중지됨")
            return True, None

        except Exception as e:
            error_msg = f"스토리 세션 중지 실패: {str(e)}"
            logger.error(error_msg)
            return False, error_msg
    
    def stop_all_sessions(self) -> None:
        """모든 스토리 세션 중지"""
        try:
            logger.info("모든 스토리 세션 중지 시작...")
            
            # 중지 신호 설정
            self._stop_event.set()
            
            # 모든 세션 비활성화
            for session in self.active_sessions.values():
                session.is_active = False
            
            # 스레드 종료 대기
            for worksheet_name, thread in list(self.session_threads.items()):
                if thread.is_alive():
                    logger.info(f"세션 '{worksheet_name}' 종료 대기...")
                    thread.join(timeout=5.0)  # 최대 5초 대기
            
            # 정리
            self.active_sessions.clear()
            self.session_threads.clear()
            
            logger.info("모든 스토리 세션 중지 완료")
            
        except Exception as e:
            logger.error(f"세션 중지 중 오류: {e}")
    
    def get_session_status(self, worksheet_name: str) -> Optional[Dict[str, Any]]:
        """
        특정 세션 상태 조회
        
        Args:
            worksheet_name: 워크시트 이름
        
        Returns:
            Optional[Dict[str, Any]]: 세션 상태 정보
        """
        if worksheet_name in self.active_sessions:
            return self.active_sessions[worksheet_name].get_progress()
        return None
    
    def get_all_sessions_status(self) -> Dict[str, Dict[str, Any]]:
        """모든 세션 상태 조회"""
        return {
            name: session.get_progress()
            for name, session in self.active_sessions.items()
        }
    
    def get_stats(self) -> Dict[str, Any]:
        """통계 정보 반환"""
        active_count = len(self.active_sessions)
        
        return {
            **self.stats,
            'active_sessions': active_count,
            'session_names': list(self.active_sessions.keys()),
            'is_running': self.is_running
        }
    
    def start(self) -> None:
        """매니저 시작"""
        self.is_running = True
        self.stats['start_time'] = datetime.now(pytz.timezone('Asia/Seoul')).isoformat()
        self._stop_event.clear()
        logger.info("스토리 루프 매니저 시작됨")
    
    def stop(self) -> None:
        """매니저 중지"""
        self.is_running = False
        self.stop_all_sessions()
        logger.info("스토리 루프 매니저 중지됨")
    
    def __del__(self):
        """소멸자 - 모든 세션 정리"""
        try:
            self.stop()
        except:
            pass


# 전역 매니저 인스턴스
_story_loop_manager: Optional[StoryLoopManager] = None


def get_story_loop_manager() -> StoryLoopManager:
    """전역 스토리 루프 매니저 반환"""
    global _story_loop_manager
    
    if _story_loop_manager is None:
        _story_loop_manager = StoryLoopManager()
    
    return _story_loop_manager


if __name__ == "__main__":
    """스토리 루프 매니저 테스트"""
    print("🧪 스토리 루프 매니저 테스트 시작...")
    
    try:
        from core.sheets_client import get_sheets_manager
        from core.mastodon_client import get_mastodon_manager
        
        # 매니저 초기화
        loop_manager = StoryLoopManager()
        sheets_client = get_sheets_manager()
        mastodon_manager = get_mastodon_manager()
        
        if not loop_manager.initialize(sheets_client, mastodon_manager):
            print("❌ 매니저 초기화 실패")
            sys.exit(1)
        
        print("✅ 매니저 초기화 성공")
        
        # 워크시트 목록 조회
        worksheets = sheets_client.get_worksheet_names()
        print(f"📋 사용 가능한 워크시트: {worksheets}")
        
        # 테스트용 세션 시작 (첫 번째 워크시트)
        if worksheets:
            test_worksheet = worksheets[0]
            print(f"🚀 테스트 세션 시작: {test_worksheet}")
            
            if loop_manager.start_story_session(test_worksheet):
                print("✅ 세션 시작 성공")
                
                # 잠시 대기
                time.sleep(10)
                
                # 상태 확인
                status = loop_manager.get_session_status(test_worksheet)
                if status:
                    print(f"📊 세션 상태: {status}")
                
                # 세션 중지
                loop_manager.stop_story_session(test_worksheet)
                print("🛑 세션 중지")
            else:
                print("❌ 세션 시작 실패")
        
        print("🎉 테스트 완료!")
        
    except Exception as e:
        print(f"❌ 테스트 실패: {e}")
        import traceback
        traceback.print_exc()
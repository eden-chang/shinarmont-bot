"""
구글 드라이브 클라이언트
이미지 파일 검색 및 다운로드 기능을 제공합니다.
"""

import os
import sys
import io
import time
from pathlib import Path
from typing import Optional, Dict, List, Any

# Google API 라이브러리
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

# 프로젝트 루트 경로 설정
current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(current_dir)

try:
    from config.settings import config
    from utils.logging_config import get_logger
except ImportError as e:
    print(f"❌ 필수 모듈 임포트 실패: {e}")
    sys.exit(1)

logger = get_logger(__name__)

# Google Drive API 설정
SCOPES = ['https://www.googleapis.com/auth/drive.readonly']


class GoogleDriveClient:
    """
    구글 드라이브 API 클라이언트
    이미지 파일 검색 및 다운로드 기능을 제공합니다.
    """

    def __init__(self, credentials_path: Optional[Path] = None, folder_id: Optional[str] = None):
        """
        GoogleDriveClient 초기화

        Args:
            credentials_path: Google 서비스 계정 인증 파일 경로
            folder_id: 검색할 폴더 ID
        """
        self.credentials_path = credentials_path or config.get_credentials_path()
        self.folder_id = folder_id or config.GOOGLE_DRIVE_IMAGE_FOLDER_ID
        self.service = None

        # 파일 ID 캐시 (파일명 -> 파일 ID)
        self._file_cache: Dict[str, str] = {}

        logger.info(f"구글 드라이브 클라이언트 초기화: 폴더 ID {self.folder_id[:20]}...")

    def authenticate(self) -> bool:
        """
        구글 드라이브 API 인증

        Returns:
            bool: 인증 성공 여부
        """
        try:
            logger.info("구글 드라이브 API 인증 시작...")

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
            self.service = build('drive', 'v3', credentials=credentials)

            logger.info("✅ 구글 드라이브 API 인증 성공")
            return True

        except Exception as e:
            logger.error(f"구글 드라이브 API 인증 실패: {e}")
            return False

    def search_file(self, filename: str, retry_count: int = 0) -> Optional[str]:
        """
        폴더 내에서 파일명으로 파일 검색 (.png 또는 .jpg 자동 검색)

        Args:
            filename: 검색할 파일명 (확장자 없이, 또는 확장자 포함)
            retry_count: 재시도 횟수 (내부용)

        Returns:
            Optional[str]: 파일 ID, 찾지 못하면 None
        """
        try:
            # 캐시 확인
            cache_key = filename
            if cache_key in self._file_cache:
                logger.debug(f"캐시에서 파일 ID 찾음: {filename}")
                return self._file_cache[cache_key]

            if not self.service:
                if not self.authenticate():
                    logger.error("인증 실패로 파일 검색 불가")
                    return None

            # 파일명 정규화 및 검색 패턴 생성
            search_patterns = []

            # 이미 확장자가 있는 경우 (폴백 처리)
            if filename.lower().endswith(('.png', '.jpg', '.jpeg')):
                # 확장자가 이미 있으면 그대로 사용
                search_patterns.append(filename)
                # 확장자 제거한 버전도 시도
                base_name = filename.rsplit('.', 1)[0]
                search_patterns.append(f"{base_name}.png")
                search_patterns.append(f"{base_name}.jpg")
            else:
                # 확장자가 없는 경우 .png와 .jpg를 시도
                search_patterns.append(f"{filename}.png")
                search_patterns.append(f"{filename}.jpg")

            found_file = None

            for pattern in search_patterns:
                # 검색 쿼리 구성
                query = f"name='{pattern}' and '{self.folder_id}' in parents and trashed=false"

                # API 호출
                results = self.service.files().list(
                    q=query,
                    fields='files(id, name, mimeType, size)',
                    pageSize=10
                ).execute()

                files = results.get('files', [])

                if files:
                    found_file = files[0]
                    logger.debug(f"파일 발견 (패턴: {pattern})")
                    break

            if not found_file:
                logger.warning(f"파일을 찾을 수 없습니다: {filename}.png 또는 {filename}.jpg")
                return None

            file_id = found_file['id']
            file_size = found_file.get('size', 'unknown')
            actual_filename = found_file.get('name', filename)

            # 캐시에 저장
            self._file_cache[cache_key] = file_id

            logger.info(f"파일 발견: {actual_filename} (ID: {file_id}, 크기: {file_size} bytes)")
            return file_id

        except HttpError as e:
            # API 제한 오류 처리
            if e.resp.status in [403, 429]:  # Rate limit exceeded
                if retry_count < 4:  # 최대 4회 재시도
                    wait_times = [10, 20, 40, 60]
                    wait_time = wait_times[retry_count]
                    logger.warning(f"구글 API 제한 감지. {wait_time}초 후 재시도... (시도 {retry_count + 1}/4)")
                    time.sleep(wait_time)
                    return self.search_file(filename, retry_count + 1)
                else:
                    logger.error(f"구글 API 제한: 최대 재시도 횟수 초과")
                    return None
            else:
                logger.error(f"구글 드라이브 API 오류: {e}")
                return None
        except Exception as e:
            logger.error(f"파일 검색 중 오류: {e}")
            return None

    def download_file(self, file_id: str, retry_count: int = 0) -> Optional[bytes]:
        """
        파일 ID로 파일 다운로드 (바이너리 데이터)

        Args:
            file_id: 다운로드할 파일 ID
            retry_count: 재시도 횟수 (내부용)

        Returns:
            Optional[bytes]: 파일 바이너리 데이터, 실패 시 None
        """
        try:
            if not self.service:
                if not self.authenticate():
                    logger.error("인증 실패로 파일 다운로드 불가")
                    return None

            # 파일 메타데이터 조회
            file_metadata = self.service.files().get(fileId=file_id, fields='name,mimeType,size').execute()
            filename = file_metadata.get('name', 'unknown')
            file_size = int(file_metadata.get('size', 0))

            # 파일 크기 제한 확인 (10MB)
            max_size = 10 * 1024 * 1024  # 10MB
            if file_size > max_size:
                logger.error(f"파일 크기가 너무 큽니다: {filename} ({file_size} bytes > {max_size} bytes)")
                return None

            logger.info(f"파일 다운로드 시작: {filename} ({file_size} bytes)")

            # 파일 다운로드
            request = self.service.files().get_media(fileId=file_id)
            file_buffer = io.BytesIO()
            downloader = MediaIoBaseDownload(file_buffer, request)

            done = False
            while not done:
                status, done = downloader.next_chunk()
                if status:
                    progress = int(status.progress() * 100)
                    logger.debug(f"다운로드 진행: {progress}%")

            # 바이너리 데이터 반환
            file_data = file_buffer.getvalue()
            logger.info(f"파일 다운로드 완료: {filename} ({len(file_data)} bytes)")
            return file_data

        except HttpError as e:
            # API 제한 오류 처리
            if e.resp.status in [403, 429]:  # Rate limit exceeded
                if retry_count < 4:  # 최대 4회 재시도
                    wait_times = [10, 20, 40, 60]
                    wait_time = wait_times[retry_count]
                    logger.warning(f"구글 API 제한 감지. {wait_time}초 후 재시도... (시도 {retry_count + 1}/4)")
                    time.sleep(wait_time)
                    return self.download_file(file_id, retry_count + 1)
                else:
                    logger.error(f"구글 API 제한: 최대 재시도 횟수 초과")
                    return None
            else:
                logger.error(f"구글 드라이브 API 오류: {e}")
                return None
        except Exception as e:
            logger.error(f"파일 다운로드 중 오류: {e}")
            return None

    def download_file_by_name(self, filename: str) -> Optional[bytes]:
        """
        파일명으로 파일 검색 후 다운로드 (통합 메서드)

        Args:
            filename: 다운로드할 파일명

        Returns:
            Optional[bytes]: 파일 바이너리 데이터, 실패 시 None
        """
        try:
            # 파일 검색
            file_id = self.search_file(filename)
            if not file_id:
                return None

            # 파일 다운로드
            return self.download_file(file_id)

        except Exception as e:
            logger.error(f"파일 다운로드 실패 ({filename}): {e}")
            return None

    def validate_files_exist(self, filenames: List[str]) -> Dict[str, bool]:
        """
        여러 파일들이 존재하는지 일괄 검증

        Args:
            filenames: 검증할 파일명 목록

        Returns:
            Dict[str, bool]: {파일명: 존재 여부} 딕셔너리
        """
        results = {}

        for filename in filenames:
            if not filename:  # 빈 파일명 건너뛰기
                continue

            file_id = self.search_file(filename)
            results[filename] = file_id is not None

            if file_id is None:
                logger.warning(f"❌ 파일 누락: {filename}")
            else:
                logger.debug(f"✅ 파일 존재: {filename}")

        return results

    def clear_cache(self) -> None:
        """파일 ID 캐시 지우기"""
        self._file_cache.clear()
        logger.debug("파일 ID 캐시 클리어")


# 전역 클라이언트 인스턴스
_drive_client: Optional[GoogleDriveClient] = None


def get_drive_client() -> GoogleDriveClient:
    """전역 구글 드라이브 클라이언트 반환"""
    global _drive_client

    if _drive_client is None:
        _drive_client = GoogleDriveClient()

        # 즉시 인증 시도
        if not _drive_client.authenticate():
            logger.error("구글 드라이브 클라이언트 초기화 실패")
            raise RuntimeError("구글 드라이브 인증 실패")

    return _drive_client


if __name__ == "__main__":
    """구글 드라이브 클라이언트 테스트"""
    print("🧪 구글 드라이브 클라이언트 테스트 시작...")

    try:
        # 클라이언트 초기화
        client = GoogleDriveClient()

        # 인증 테스트
        print("🔐 인증 테스트...")
        if client.authenticate():
            print("✅ 인증 성공")
        else:
            print("❌ 인증 실패")
            sys.exit(1)

        # 테스트 파일 검색
        print("🔍 파일 검색 테스트...")
        test_filename = "test.jpg"  # 실제 존재하는 파일명으로 변경
        file_id = client.search_file(test_filename)

        if file_id:
            print(f"✅ 파일 발견: {test_filename} (ID: {file_id})")

            # 다운로드 테스트
            print("📥 파일 다운로드 테스트...")
            file_data = client.download_file(file_id)

            if file_data:
                print(f"✅ 다운로드 성공: {len(file_data)} bytes")
            else:
                print("❌ 다운로드 실패")
        else:
            print(f"❌ 파일을 찾을 수 없습니다: {test_filename}")

        print("🎉 구글 드라이브 클라이언트 테스트 완료!")

    except Exception as e:
        print(f"❌ 테스트 실패: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

"""
구글 드라이브 작업 모듈
구글 드라이브에서 이미지 파일을 검색하고 다운로드하는 기능을 제공합니다.
"""

import os
import sys
from typing import Optional, BinaryIO
from io import BytesIO

# 경로 설정
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

try:
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseDownload
    from google.oauth2.service_account import Credentials
    from config.settings import config
    from utils.logging_config import logger
except ImportError as e:
    import logging
    logger = logging.getLogger('drive_operations')
    logger.error(f"필수 모듈 임포트 실패: {e}")


class DriveManager:
    """구글 드라이브 관리 클래스"""

    def __init__(self, credentials_path: str = None, folder_id: str = None):
        """
        DriveManager 초기화

        Args:
            credentials_path: 인증 파일 경로
            folder_id: 검색할 폴더 ID (선택사항, None이면 전체 드라이브 검색)
        """
        self.credentials_path = credentials_path or config.get_credentials_path()
        self.folder_id = folder_id
        self._service = None

    @property
    def service(self):
        """구글 드라이브 서비스 객체 (지연 로딩)"""
        if self._service is None:
            self._service = self._build_service()
        return self._service

    def _build_service(self):
        """
        구글 드라이브 API 서비스 빌드

        Returns:
            구글 드라이브 서비스 객체
        """
        try:
            # 서비스 계정 인증
            credentials = Credentials.from_service_account_file(
                str(self.credentials_path),
                scopes=['https://www.googleapis.com/auth/drive.readonly']
            )

            # Drive API 서비스 빌드
            service = build('drive', 'v3', credentials=credentials)
            logger.info("구글 드라이브 API 서비스 초기화 완료")
            return service

        except Exception as e:
            logger.error(f"구글 드라이브 API 서비스 초기화 실패: {e}")
            raise

    def search_file_by_name(self, filename: str) -> Optional[str]:
        """
        파일명으로 파일 검색

        Args:
            filename: 검색할 파일명

        Returns:
            Optional[str]: 파일 ID 또는 None (찾지 못한 경우)
        """
        try:
            # 쿼리 구성
            query = f"name='{filename}' and trashed=false"

            # 특정 폴더 내에서만 검색하는 경우
            if self.folder_id:
                query += f" and '{self.folder_id}' in parents"

            logger.debug(f"드라이브 검색 쿼리: {query}")
            logger.debug(f"사용 중인 folder_id: {self.folder_id}")
            logger.debug(f"인증 파일 경로: {self.credentials_path}")

            # 파일 검색
            results = self.service.files().list(
                q=query,
                spaces='drive',
                fields='files(id, name, mimeType, parents, size, createdTime)',
                pageSize=5
            ).execute()

            files = results.get('files', [])
            logger.debug(f"검색 결과 수: {len(files)}, 파일명: {filename}")

            if not files:
                logger.warning(f"파일을 찾을 수 없습니다: {filename}")
                # 폴더 제한 없이 재검색하여 폴더 ID 문제인지 확인
                if self.folder_id:
                    fallback_query = f"name='{filename}' and trashed=false"
                    fallback_results = self.service.files().list(
                        q=fallback_query,
                        spaces='drive',
                        fields='files(id, name, mimeType, parents)',
                        pageSize=5
                    ).execute()
                    fallback_files = fallback_results.get('files', [])
                    if fallback_files:
                        logger.warning(
                            f"폴더 제한 없이 검색 시 {len(fallback_files)}개 발견! "
                            f"파일 위치: {[{f['name']: f.get('parents', ['부모없음'])} for f in fallback_files]} "
                            f"→ folder_id '{self.folder_id}'가 올바른지 확인 필요"
                        )
                    else:
                        logger.debug(f"폴더 제한 없이 검색해도 결과 없음: {filename}")
                return None

            # 첫 번째 결과 반환
            found_file = files[0]
            file_id = found_file['id']
            file_mime_type = found_file.get('mimeType', '')
            file_size = found_file.get('size', 'unknown')
            file_parents = found_file.get('parents', [])
            logger.info(
                f"파일 검색 성공: {filename} "
                f"(ID: {file_id}, MIME: {file_mime_type}, "
                f"크기: {file_size} bytes, 부모폴더: {file_parents})"
            )
            return file_id

        except Exception as e:
            logger.error(f"파일 검색 실패 ({filename}): {type(e).__name__}: {e}", exc_info=True)
            return None

    def download_file(self, file_id: str) -> Optional[bytes]:
        """
        파일 다운로드

        Args:
            file_id: 다운로드할 파일 ID

        Returns:
            Optional[bytes]: 파일 바이너리 데이터 또는 None (실패 시)
        """
        try:
            # 파일 메타데이터 가져오기
            file_metadata = self.service.files().get(fileId=file_id).execute()
            file_name = file_metadata.get('name', 'unknown')

            # 파일 다운로드
            request = self.service.files().get_media(fileId=file_id)
            file_buffer = BytesIO()
            downloader = MediaIoBaseDownload(file_buffer, request)

            done = False
            while not done:
                status, done = downloader.next_chunk()
                if status:
                    logger.debug(f"다운로드 진행률: {int(status.progress() * 100)}%")

            # 바이너리 데이터 반환
            file_buffer.seek(0)
            file_data = file_buffer.read()
            logger.info(f"파일 다운로드 완료: {file_name} ({len(file_data)} bytes)")
            return file_data

        except Exception as e:
            logger.error(f"파일 다운로드 실패 (ID: {file_id}): {e}")
            return None

    def get_file_by_name(self, filename: str) -> Optional[bytes]:
        """
        파일명으로 파일을 검색하고 다운로드

        Args:
            filename: 파일명

        Returns:
            Optional[bytes]: 파일 바이너리 데이터 또는 None
        """
        if not filename:
            return None

        # 파일 검색
        file_id = self.search_file_by_name(filename)
        if not file_id:
            return None

        # 파일 다운로드
        return self.download_file(file_id)


# 전역 인스턴스
_global_drive_manager = None


def get_drive_manager(folder_id: str = None) -> DriveManager:
    """전역 DriveManager 인스턴스 반환"""
    global _global_drive_manager
    if _global_drive_manager is None:
        _global_drive_manager = DriveManager(folder_id=folder_id)
    return _global_drive_manager


# 편의 함수들

def search_drive_file(filename: str, folder_id: str = None) -> Optional[str]:
    """
    구글 드라이브에서 파일 검색

    Args:
        filename: 파일명
        folder_id: 폴더 ID (선택사항)

    Returns:
        Optional[str]: 파일 ID 또는 None
    """
    manager = get_drive_manager(folder_id)
    return manager.search_file_by_name(filename)


def download_drive_file(file_id: str) -> Optional[bytes]:
    """
    구글 드라이브에서 파일 다운로드

    Args:
        file_id: 파일 ID

    Returns:
        Optional[bytes]: 파일 바이너리 데이터 또는 None
    """
    manager = get_drive_manager()
    return manager.download_file(file_id)


def get_drive_file_by_name(filename: str, folder_id: str = None) -> Optional[bytes]:
    """
    파일명으로 구글 드라이브에서 파일 검색 및 다운로드

    Args:
        filename: 파일명
        folder_id: 폴더 ID (선택사항)

    Returns:
        Optional[bytes]: 파일 바이너리 데이터 또는 None
    """
    manager = get_drive_manager(folder_id)
    return manager.get_file_by_name(filename)


def get_image_file_by_name(base_filename: str, folder_id: str = None) -> Optional[bytes]:
    """
    이미지 파일을 확장자 자동 감지하여 다운로드
    여러 이미지 확장자를 시도하여 파일을 찾습니다.

    Args:
        base_filename: 확장자를 제외한 파일명 (예: "겐지의_표창")
        folder_id: 폴더 ID (선택사항)

    Returns:
        Optional[bytes]: 파일 바이너리 데이터 또는 None
    """
    logger.debug(f"get_image_file_by_name 호출: base_filename='{base_filename}', folder_id='{folder_id}'")
    manager = get_drive_manager(folder_id)
    logger.debug(f"DriveManager 인스턴스: folder_id={manager.folder_id}, credentials_path={manager.credentials_path}")

    # 이미지 확장자 목록 (우선순위 순)
    image_extensions = ['.png', '.jpg', '.jpeg', '.gif', '.webp']

    # 확장자가 이미 있는지 확인
    if any(base_filename.lower().endswith(ext) for ext in image_extensions):
        # 확장자가 이미 포함된 경우 그대로 검색
        logger.debug(f"확장자가 포함된 파일명으로 직접 검색: {base_filename}")
        result = manager.get_file_by_name(base_filename)
        logger.debug(f"직접 검색 결과: {'성공 (' + str(len(result)) + ' bytes)' if result else '실패'}")
        return result

    # 1) 확장자 없는 원본 파일명으로 먼저 검색 (드라이브에 확장자 없이 업로드된 경우)
    logger.debug(f"확장자 없는 원본 파일명으로 검색 시도: {base_filename}")
    file_data = manager.get_file_by_name(base_filename)
    if file_data:
        logger.info(f"확장자 없는 파일 발견: {base_filename} ({len(file_data)} bytes)")
        return file_data

    # 2) 각 확장자를 시도
    for ext in image_extensions:
        filename_with_ext = f"{base_filename}{ext}"
        logger.debug(f"이미지 검색 시도: {filename_with_ext}")

        file_data = manager.get_file_by_name(filename_with_ext)
        if file_data:
            logger.info(f"이미지 파일 발견: {filename_with_ext} ({len(file_data)} bytes)")
            return file_data

    # 모든 확장자를 시도했지만 찾지 못함
    logger.warning(f"이미지 파일을 찾을 수 없습니다: {base_filename} (시도한 확장자: {', '.join(image_extensions)})")

    # 디버그: 파일명 부분 매칭으로 재검색 (원인 파악용)
    try:
        partial_query = f"name contains '{base_filename}' and trashed=false"
        if manager.folder_id:
            partial_query += f" and '{manager.folder_id}' in parents"
        results = manager.service.files().list(
            q=partial_query,
            spaces='drive',
            fields='files(id, name, mimeType, parents)',
            pageSize=10
        ).execute()
        partial_files = results.get('files', [])
        if partial_files:
            logger.warning(
                f"부분 매칭으로 {len(partial_files)}개 파일 발견: "
                f"{[f['name'] for f in partial_files]} "
                f"→ 파일명이 정확히 일치하지 않을 수 있음"
            )
        else:
            logger.debug(f"부분 매칭으로도 '{base_filename}' 관련 파일 없음")
    except Exception as e:
        logger.debug(f"부분 매칭 재검색 중 오류 (무시): {e}")

    return None


# 사용 예시 (테스트용)
if __name__ == "__main__":
    # 테스트 코드
    print("=== 구글 드라이브 API 테스트 ===")

    # 파일 검색 테스트
    test_filename = "test_image.png"

    print(f"\n[파일 검색] {test_filename}")
    file_id = search_drive_file(test_filename)

    if file_id:
        print(f"파일 ID: {file_id}")

        # 파일 다운로드 테스트
        print(f"\n[파일 다운로드] ID: {file_id}")
        file_data = download_drive_file(file_id)

        if file_data:
            print(f"다운로드 성공: {len(file_data)} bytes")
        else:
            print("다운로드 실패")
    else:
        print("파일을 찾을 수 없습니다.")

    # 통합 함수 테스트
    print(f"\n[통합 함수 테스트] {test_filename}")
    file_data = get_drive_file_by_name(test_filename)

    if file_data:
        print(f"성공: {len(file_data)} bytes")
    else:
        print("실패")

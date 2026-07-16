"""
Google Drive 이미지 조회 모듈
IMAGE_SETTING=true일 때 툿 첨부용 이미지를 Drive 폴더에서 조회·다운로드합니다.
허용 확장자: .jpg, .png만.
"""

import os
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload
import io

current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(current_dir)

try:
    from config.settings import config
    from utils.logging_config import get_logger
except ImportError as e:
    print(f"❌ 필수 모듈 임포트 실패: {e}")
    sys.exit(1)

logger = get_logger(__name__)

DRIVE_SCOPES = ['https://www.googleapis.com/auth/drive.readonly']
ALLOWED_EXTENSIONS = ('.jpg', '.jpeg', '.png')
ALLOWED_EXTENSIONS_LOOKUP = ('.jpg', '.png')


def _normalize_ext(name: str) -> bool:
    """파일명이 허용 확장자로 끝나는지 확인 (대소문자 무시)."""
    lower = name.lower()
    return any(lower.endswith(ext) for ext in ('.jpg', '.jpeg', '.png'))


def get_image_paths(folder_id: str, image_names: List[str]) -> List[Path]:
    """
    Drive 폴더에서 이미지 이름 목록에 해당하는 파일을 찾아 임시 파일로 다운로드한 경로 목록 반환.
    각 이름에 대해 .jpg, .png 순으로 검색하여 먼저 존재하는 하나만 사용.
    찾지 못한 이름은 스킵하고 로그만 남김 (찾은 파일만 반환).

    Args:
        folder_id: Google Drive 폴더 ID
        image_names: 이미지 기본 이름 목록 (예: ["이미지1", "이미지2"])

    Returns:
        다운로드된 임시 파일 경로 목록 (호출자가 전송 후 삭제해야 함)
    """
    if not folder_id or not folder_id.strip():
        logger.warning("GOOGLE_DRIVE_IMAGE_FOLDER_ID가 비어 있어 이미지 조회를 건너뜁니다.")
        return []

    if not image_names:
        return []

    credentials_path = config.get_credentials_path()
    if not credentials_path.exists():
        logger.error(f"Drive 인증 파일을 찾을 수 없습니다: {credentials_path}")
        return []

    try:
        credentials = Credentials.from_service_account_file(
            str(credentials_path),
            scopes=DRIVE_SCOPES
        )
        service = build('drive', 'v3', credentials=credentials)
    except Exception as e:
        logger.error(f"Drive API 인증 실패: {e}")
        return []

    try:
        response = service.files().list(
            q=f"'{folder_id.strip()}' in parents and trashed=false",
            spaces='drive',
            fields='nextPageToken, files(id, name)',
            pageSize=1000
        ).execute()

        files = response.get('files', [])
        name_to_file: Dict[str, Tuple[str, str]] = {}
        for f in files:
            name = f.get('name', '')
            if not name or not _normalize_ext(name):
                continue
            key = name.lower()
            if key not in name_to_file:
                name_to_file[key] = (f['id'], name)
    except HttpError as e:
        logger.error(f"Drive 폴더 목록 조회 실패: {e}")
        return []
    except Exception as e:
        logger.error(f"Drive 목록 조회 중 오류: {e}")
        return []

    result_paths: List[Path] = []
    for base_name in image_names:
        base_name = (base_name or "").strip()
        if not base_name:
            continue
        found_id: Optional[str] = None
        found_actual_name: Optional[str] = None
        for ext in ALLOWED_EXTENSIONS_LOOKUP:
            candidate = (base_name + ext).lower()
            if candidate in name_to_file:
                found_id, found_actual_name = name_to_file[candidate]
                break
        if not found_id or not found_actual_name:
            logger.warning(f"Drive 폴더에서 이미지를 찾지 못함 (이름: {base_name}, 허용: .jpg, .png)")
            continue

        try:
            request = service.files().get_media(fileId=found_id)
            buf = io.BytesIO()
            downloader = MediaIoBaseDownload(buf, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            buf.seek(0)
            suffix = Path(found_actual_name).suffix
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(buf.getvalue())
                result_paths.append(Path(tmp.name))
        except HttpError as e:
            logger.warning(f"Drive 파일 다운로드 실패 (이름: {base_name}): {e}")
        except Exception as e:
            logger.warning(f"이미지 다운로드 중 오류 (이름: {base_name}): {e}")

    return result_paths

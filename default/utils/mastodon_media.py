"""
마스토돈 미디어 업로드 모듈
마스토돈 API를 통해 이미지를 업로드하고 미디어 ID를 반환하는 기능을 제공합니다.
"""

import os
import sys
from typing import Optional, List, Union
from io import BytesIO

# 경로 설정
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

try:
    import mastodon
    from utils.logging_config import logger
    from utils.api_retry import api_retry
except ImportError as e:
    import logging
    logger = logging.getLogger('mastodon_media')
    logger.error(f"필수 모듈 임포트 실패: {e}")


class MastodonMediaManager:
    """마스토돈 미디어 관리 클래스"""

    def __init__(self, mastodon_api):
        """
        MastodonMediaManager 초기화

        Args:
            mastodon_api: Mastodon API 객체
        """
        self.api = mastodon_api

    def _detect_mime_type(self, filename: str) -> str:
        """
        파일명에서 MIME 타입 자동 감지

        Args:
            filename: 파일명

        Returns:
            str: MIME 타입
        """
        # 확장자 추출
        ext = filename.lower().split('.')[-1] if '.' in filename else ''

        # MIME 타입 매핑
        mime_types = {
            'png': 'image/png',
            'jpg': 'image/jpeg',
            'jpeg': 'image/jpeg',
            'gif': 'image/gif',
            'webp': 'image/webp',
            'bmp': 'image/bmp',
            'svg': 'image/svg+xml'
        }

        return mime_types.get(ext, 'image/png')  # 기본값: image/png

    @api_retry(max_retries=3, delay_seconds=2)
    def upload_image(self, image_data: bytes, filename: str = "image.png",
                     description: str = None, mime_type: str = None) -> Optional[dict]:
        """
        이미지를 마스토돈에 업로드

        Args:
            image_data: 이미지 바이너리 데이터
            filename: 파일명 (기본값: image.png)
            description: 이미지 설명 (대체 텍스트)
            mime_type: MIME 타입 (자동 감지 가능)

        Returns:
            Optional[dict]: 미디어 객체 (id 포함) 또는 None
        """
        try:
            # MIME 타입 자동 감지
            if mime_type is None:
                mime_type = self._detect_mime_type(filename)

            # BytesIO 객체로 변환
            image_file = BytesIO(image_data)
            image_file.name = filename

            # 마스토돈 API로 미디어 업로드
            media = self.api.media_post(
                media_file=image_file,
                mime_type=mime_type,
                description=description
            )

            logger.info(f"이미지 업로드 성공: {filename} (미디어 ID: {media['id']})")
            return media

        except Exception as e:
            logger.error(f"이미지 업로드 실패 ({filename}): {e}")
            return None

    def upload_multiple_images(self, images_data: List[tuple]) -> List[dict]:
        """
        여러 이미지를 마스토돈에 업로드

        Args:
            images_data: [(이미지 데이터, 파일명, 설명), ...] 형태의 리스트

        Returns:
            List[dict]: 업로드된 미디어 객체 리스트
        """
        uploaded_media = []

        for image_info in images_data:
            if len(image_info) == 2:
                image_data, filename = image_info
                description = None
            elif len(image_info) == 3:
                image_data, filename, description = image_info
            else:
                logger.warning(f"잘못된 이미지 정보 형식: {image_info}")
                continue

            media = self.upload_image(image_data, filename, description)
            if media:
                uploaded_media.append(media)

        return uploaded_media

    @api_retry(max_retries=3, delay_seconds=2)
    def post_status_with_media(self, status: str, media_ids: List[Union[str, int]],
                              in_reply_to_id: str = None, visibility: str = 'public') -> Optional[dict]:
        """
        미디어를 첨부하여 status 게시

        Args:
            status: 게시할 텍스트
            media_ids: 미디어 ID 리스트
            in_reply_to_id: 답글 대상 ID
            visibility: 공개 범위 (public, unlisted, private, direct)

        Returns:
            Optional[dict]: 게시된 status 객체 또는 None
        """
        try:
            # 마스토돈 API로 status 게시
            result = self.api.status_post(
                status=status,
                media_ids=media_ids,
                in_reply_to_id=in_reply_to_id,
                visibility=visibility
            )

            logger.info(f"미디어 첨부 게시 성공: {len(media_ids)}개 이미지")
            return result

        except Exception as e:
            logger.error(f"미디어 첨부 게시 실패: {e}")
            return None

    def upload_and_post(self, status: str, image_data: bytes, filename: str = "image.png",
                       description: str = None, in_reply_to_id: str = None,
                       visibility: str = 'public') -> Optional[dict]:
        """
        이미지를 업로드하고 status 게시 (원스톱 함수)

        Args:
            status: 게시할 텍스트
            image_data: 이미지 바이너리 데이터
            filename: 파일명
            description: 이미지 설명
            in_reply_to_id: 답글 대상 ID
            visibility: 공개 범위

        Returns:
            Optional[dict]: 게시된 status 객체 또는 None
        """
        # 1. 이미지 업로드
        media = self.upload_image(image_data, filename, description)
        if not media:
            logger.error("이미지 업로드 실패로 인한 게시 중단")
            return None

        # 2. status 게시
        return self.post_status_with_media(
            status=status,
            media_ids=[media['id']],
            in_reply_to_id=in_reply_to_id,
            visibility=visibility
        )


# 전역 인스턴스 (API 객체를 저장하기 위한 변수)
_global_media_manager = None


def get_media_manager(mastodon_api) -> MastodonMediaManager:
    """전역 MastodonMediaManager 인스턴스 반환"""
    global _global_media_manager
    if _global_media_manager is None or _global_media_manager.api != mastodon_api:
        _global_media_manager = MastodonMediaManager(mastodon_api)
    return _global_media_manager


# 편의 함수들

def upload_image_to_mastodon(mastodon_api, image_data: bytes,
                             filename: str = "image.png",
                             description: str = None) -> Optional[dict]:
    """
    마스토돈에 이미지 업로드

    Args:
        mastodon_api: Mastodon API 객체
        image_data: 이미지 바이너리 데이터
        filename: 파일명
        description: 이미지 설명

    Returns:
        Optional[dict]: 미디어 객체 또는 None
    """
    manager = get_media_manager(mastodon_api)
    return manager.upload_image(image_data, filename, description)


def post_status_with_image(mastodon_api, status: str, image_data: bytes,
                           filename: str = "image.png", description: str = None,
                           in_reply_to_id: str = None, visibility: str = 'public') -> Optional[dict]:
    """
    이미지를 첨부하여 status 게시

    Args:
        mastodon_api: Mastodon API 객체
        status: 게시할 텍스트
        image_data: 이미지 바이너리 데이터
        filename: 파일명
        description: 이미지 설명
        in_reply_to_id: 답글 대상 ID
        visibility: 공개 범위

    Returns:
        Optional[dict]: 게시된 status 객체 또는 None
    """
    manager = get_media_manager(mastodon_api)
    return manager.upload_and_post(status, image_data, filename, description,
                                   in_reply_to_id, visibility)


# 사용 예시 (테스트용)
if __name__ == "__main__":
    print("=== 마스토돈 미디어 업로드 테스트 ===")
    print("이 모듈은 Mastodon API 객체가 필요합니다.")
    print("실제 사용 시에는 Mastodon API 객체를 전달하여 사용하세요.")

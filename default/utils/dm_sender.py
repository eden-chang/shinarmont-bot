"""
DM 전송 모듈
마스토돈 DM(Direct Message) 전송 기능을 제공합니다.
"""

import os
import sys
import time
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from datetime import datetime
import pytz

# 경로 설정 (VM 환경 대응)
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

try:
    import mastodon
    from utils.logging_config import logger
    from config.settings import config
    from utils.api_retry import api_retry
except ImportError:
    # VM 환경에서 임포트 실패 시 폴백
    import logging
    logger = logging.getLogger('dm_sender')
    
    # 더미 마스토돈 클래스
    class mastodon:
        class Mastodon:
            pass


@dataclass
class DMMessage:
    """DM 메시지 데이터 클래스"""
    receiver_id: str
    message: str
    timestamp: datetime
    attempts: int = 0
    max_attempts: int = 3
    success: bool = False
    error: Optional[str] = None
    
    def can_retry(self) -> bool:
        """재시도 가능 여부"""
        return not self.success and self.attempts < self.max_attempts
    
    def mark_attempt(self, success: bool, error: str = None):
        """시도 결과 기록"""
        self.attempts += 1
        self.success = success
        if error:
            self.error = error


class DMSender:
    """DM 전송 클래스"""
    
    def __init__(self, mastodon_client: mastodon.Mastodon):
        """
        DMSender 초기화
        
        Args:
            mastodon_client: 마스토돈 클라이언트
        """
        self.mastodon = mastodon_client
        self.pending_dms: List[DMMessage] = []
        self.stats = {
            'total_sent': 0,
            'successful_sent': 0,
            'failed_sent': 0,
            'retry_attempts': 0
        }
    
    @api_retry(max_retries=3, delay_seconds=60)
    def send_dm(self, receiver_id: str, message: str) -> bool:
        """
        즉시 DM 전송
        
        Args:
            receiver_id: 수신자 마스토돈 ID
            message: 메시지 내용
            
        Returns:
            bool: 전송 성공 여부
        """
        try:
            # DM으로 전송 (visibility='direct')
            status = self.mastodon.status_post(
                status=f"@{receiver_id} {message}",
                visibility='direct'
            )
            
            self.stats['total_sent'] += 1
            self.stats['successful_sent'] += 1
            
            logger.info(f"DM 전송 성공: {receiver_id} -> {message[:50]}...")
            return True
            
        except Exception as e:
            self.stats['total_sent'] += 1
            self.stats['failed_sent'] += 1
            
            logger.error(f"DM 전송 실패: {receiver_id} -> {e}")
            return False
    
    def queue_dm(self, receiver_id: str, message: str) -> None:
        """
        DM을 대기열에 추가
        
        Args:
            receiver_id: 수신자 마스토돈 ID
            message: 메시지 내용
        """
        dm_message = DMMessage(
            receiver_id=receiver_id,
            message=message,
            timestamp=datetime.now(pytz.timezone('Asia/Seoul'))
        )
        
        self.pending_dms.append(dm_message)
        logger.debug(f"DM 대기열 추가: {receiver_id} -> {message[:30]}...")
    
    def process_pending_dms(self) -> Dict[str, int]:
        """
        대기 중인 DM들을 처리
        
        Returns:
            Dict: 처리 결과 통계
        """
        if not self.pending_dms:
            return {'processed': 0, 'success': 0, 'failed': 0, 'retries': 0}
        
        results = {'processed': 0, 'success': 0, 'failed': 0, 'retries': 0}
        remaining_dms = []
        
        for dm in self.pending_dms:
            if not dm.can_retry():
                # 재시도 불가능한 DM은 제거
                if not dm.success:
                    results['failed'] += 1
                continue
            
            results['processed'] += 1
            
            # DM 전송 시도
            if dm.attempts > 0:
                results['retries'] += 1
                self.stats['retry_attempts'] += 1
                # 재시도 시 잠시 대기
                time.sleep(0.5)
            
            success = self.send_dm(dm.receiver_id, dm.message)
            dm.mark_attempt(success, None if success else "전송 실패")
            
            if success:
                results['success'] += 1
                logger.info(f"DM 처리 완료: {dm.receiver_id}")
            else:
                if dm.can_retry():
                    # 재시도 가능하면 대기열에 다시 추가
                    remaining_dms.append(dm)
                    logger.warning(f"DM 재시도 예정: {dm.receiver_id} (시도 {dm.attempts}/{dm.max_attempts})")
                else:
                    results['failed'] += 1
                    logger.error(f"DM 전송 최종 실패: {dm.receiver_id}")
        
        # 실패한 DM들만 대기열에 유지
        self.pending_dms = remaining_dms
        
        if results['processed'] > 0:
            logger.info(f"DM 처리 완료: {results}")
        
        return results
    
    @api_retry(max_retries=3, delay_seconds=60)
    def send_transfer_notification(self, receiver_id: str, giver_name: str, 
                                 giver_subject: str, item_name: str, item_particle: str) -> bool:
        """
        양도 알림 DM 전송
        
        Args:
            receiver_id: 수신자 ID
            giver_name: 양도자 이름
            giver_subject: 양도자 주어 조사 (이/가)
            item_name: 아이템명
            item_particle: 아이템 목적어 조사 (을/를)
            
        Returns:
            bool: 전송 성공 여부
        """
        message = f"{giver_name}{giver_subject} 당신에게 {item_name}{item_particle} 양도했습니다."
        return self.send_dm(receiver_id, message)
    
    def queue_transfer_notification(self, receiver_id: str, giver_name: str,
                                  giver_subject: str, item_name: str, item_particle: str) -> None:
        """
        양도 알림 DM을 대기열에 추가
        
        Args:
            receiver_id: 수신자 ID
            giver_name: 양도자 이름  
            giver_subject: 양도자 주어 조사 (이/가)
            item_name: 아이템명
            item_particle: 아이템 목적어 조사 (을/를)
        """
        message = f"{giver_name}{giver_subject} 당신에게 {item_name}{item_particle} 양도했습니다."
        self.queue_dm(receiver_id, message)
    
    def get_pending_count(self) -> int:
        """대기 중인 DM 개수"""
        return len(self.pending_dms)
    
    def get_stats(self) -> Dict[str, Any]:
        """DM 전송 통계"""
        stats = self.stats.copy()
        stats['pending_dms'] = len(self.pending_dms)
        
        if stats['total_sent'] > 0:
            stats['success_rate'] = (stats['successful_sent'] / stats['total_sent']) * 100
        else:
            stats['success_rate'] = 0
        
        return stats
    
    def clear_failed_dms(self) -> int:
        """
        실패한 DM들을 대기열에서 제거
        
        Returns:
            int: 제거된 DM 개수
        """
        before_count = len(self.pending_dms)
        self.pending_dms = [dm for dm in self.pending_dms if dm.can_retry()]
        after_count = len(self.pending_dms)
        
        cleared = before_count - after_count
        if cleared > 0:
            logger.info(f"실패한 DM {cleared}개 제거됨")
        
        return cleared
    
    def reset_stats(self) -> None:
        """통계 초기화"""
        self.stats = {
            'total_sent': 0,
            'successful_sent': 0,
            'failed_sent': 0,
            'retry_attempts': 0
        }
        logger.info("DM 전송 통계 초기화")
    
    def validate_receiver_id(self, receiver_id: str) -> bool:
        """
        수신자 ID 유효성 검증
        
        Args:
            receiver_id: 수신자 ID
            
        Returns:
            bool: 유효성
        """
        if not receiver_id or not receiver_id.strip():
            return False
        
        # 기본적인 마스토돈 ID 형식 확인
        # 예: user@instance.com 또는 user
        receiver_id = receiver_id.strip()
        
        # 특수 문자나 공백이 포함된 경우 무효
        if ' ' in receiver_id or '\n' in receiver_id or '\t' in receiver_id:
            return False
        
        return True
    
    def format_transfer_message(self, giver_name: str, giver_eun_neun: str, 
                              item_name: str, item_particle: str) -> str:
        """
        양도 메시지 포맷팅
        
        Args:
            giver_name: 양도자 이름
            giver_eun_neun: 양도자 은/는 조사
            item_name: 아이템명
            item_particle: 아이템 을/를 조사
            
        Returns:
            str: 포맷된 메시지
        """
        # 은/는 -> 이/가 변환
        giver_subject = '이' if giver_eun_neun == '은' else '가'
        
        return f"{giver_name}{giver_subject} 당신에게 {item_name}{item_particle} 양도했습니다."
    
    def health_check(self) -> Dict[str, Any]:
        """
        DM 전송기 상태 확인
        
        Returns:
            Dict: 상태 정보
        """
        health_status = {
            'status': 'healthy',
            'errors': [],
            'warnings': []
        }
        
        try:
            # 마스토돈 클라이언트 확인
            if not self.mastodon:
                health_status['errors'].append("마스토돈 클라이언트가 없습니다.")
                health_status['status'] = 'error'
            
            # 대기 중인 DM 확인
            pending_count = len(self.pending_dms)
            if pending_count > 50:  # 임계값
                health_status['warnings'].append(f"대기 중인 DM이 많습니다: {pending_count}개")
                health_status['status'] = 'warning' if health_status['status'] == 'healthy' else health_status['status']
            
            # 실패율 확인
            if self.stats['total_sent'] > 10:  # 최소 10개 이상 전송한 경우
                failure_rate = (self.stats['failed_sent'] / self.stats['total_sent']) * 100
                if failure_rate > 30:  # 30% 이상 실패율
                    health_status['warnings'].append(f"높은 실패율: {failure_rate:.1f}%")
                    health_status['status'] = 'warning' if health_status['status'] == 'healthy' else health_status['status']
            
            # 통계 정보 추가
            health_status['statistics'] = self.get_stats()
            
        except Exception as e:
            health_status['errors'].append(f"상태 확인 중 오류: {str(e)}")
            health_status['status'] = 'error'
        
        return health_status


# 전역 DM 전송기 인스턴스
_global_dm_sender: Optional[DMSender] = None


def initialize_dm_sender(mastodon_client: mastodon.Mastodon) -> DMSender:
    """
    전역 DM 전송기 초기화(멱등).

    시작 시퀀스에서 두 번 호출된다(스트림 매니저 초기화 + BotStreamHandler 생성).
    매번 새 인스턴스로 교체하면, 이미 대기열에 쌓인 DM(예: 시작 재전송 스윕이
    큐잉한 문구)이 버려진 인스턴스에 남아 유실된다. 따라서 이미 초기화돼 있으면
    기존 전송기를 재사용해 대기 큐를 보존한다.

    Args:
        mastodon_client: 마스토돈 클라이언트

    Returns:
        DMSender: 초기화된(또는 기존) DM 전송기
    """
    global _global_dm_sender
    if _global_dm_sender is not None:
        # 재초기화 요청은 무시하고 기존 전송기(대기 큐 포함)를 그대로 쓴다.
        return _global_dm_sender
    _global_dm_sender = DMSender(mastodon_client)
    logger.info("DM 전송기 초기화 완료")
    return _global_dm_sender


def get_dm_sender() -> Optional[DMSender]:
    """전역 DM 전송기 반환"""
    return _global_dm_sender


def send_dm(receiver_id: str, message: str) -> bool:
    """
    편의 함수: DM 전송
    
    Args:
        receiver_id: 수신자 ID
        message: 메시지
        
    Returns:
        bool: 전송 성공 여부
    """
    sender = get_dm_sender()
    if sender:
        return sender.send_dm(receiver_id, message)
    else:
        logger.error("DM 전송기가 초기화되지 않았습니다.")
        return False


def queue_dm(receiver_id: str, message: str) -> None:
    """
    편의 함수: DM 대기열 추가
    
    Args:
        receiver_id: 수신자 ID
        message: 메시지
    """
    sender = get_dm_sender()
    if sender:
        sender.queue_dm(receiver_id, message)
    else:
        logger.error("DM 전송기가 초기화되지 않았습니다.")


def process_pending_dms() -> Dict[str, int]:
    """
    편의 함수: 대기 중인 DM 처리
    
    Returns:
        Dict: 처리 결과
    """
    sender = get_dm_sender()
    if sender:
        return sender.process_pending_dms()
    else:
        logger.error("DM 전송기가 초기화되지 않았습니다.")
        return {'processed': 0, 'success': 0, 'failed': 0, 'retries': 0}


def send_transfer_notification(receiver_id: str, giver_name: str, giver_eun_neun: str,
                             item_name: str, item_particle: str) -> bool:
    """
    편의 함수: 양도 알림 DM 전송
    
    Args:
        receiver_id: 수신자 ID
        giver_name: 양도자 이름
        giver_eun_neun: 양도자 은/는 조사
        item_name: 아이템명
        item_particle: 아이템 을/를 조사
        
    Returns:
        bool: 전송 성공 여부
    """
    sender = get_dm_sender()
    if sender:
        giver_subject = '이' if giver_eun_neun == '은' else '가'
        return sender.send_transfer_notification(
            receiver_id, giver_name, giver_subject, item_name, item_particle
        )
    else:
        logger.error("DM 전송기가 초기화되지 않았습니다.")
        return False


# 테스트 함수
def test_dm_formatting():
    """DM 메시지 포맷팅 테스트"""
    test_cases = [
        ('한참', '은', '반지', '를'),
        ('테스트', '는', '사과', '를'),
        ('울로', '는', '동화책', '을')
    ]
    
    print("=== DM 메시지 포맷팅 테스트 ===")
    for giver_name, eun_neun, item_name, item_particle in test_cases:
        giver_subject = '이' if eun_neun == '은' else '가'
        message = f"{giver_name}{giver_subject} 당신에게 {item_name}{item_particle} 양도했습니다."
        print(f"{giver_name}({eun_neun}) + {item_name}({item_particle}) -> {message}")
    print("=" * 40)


if __name__ == "__main__":
    test_dm_formatting()
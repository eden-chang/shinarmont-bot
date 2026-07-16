"""
소지금 관리 명령어 구현
관리자용 소지금 추가/차감 명령어를 관리하는 클래스입니다.
"""

import os
import sys
import re
from typing import List, Tuple, Any, Optional, Dict

# 경로 설정 (VM 환경 대응)
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

try:
    from config.settings import config
    from utils.logging_config import logger
    from utils.error_handling import CommandError
    from utils.cache_manager import bot_cache
    from commands.store.base_store_command import BaseStoreCommand
    from commands.registry import register_command
    from models.command_result import CommandType
except ImportError as e:
    # VM 환경에서 임포트 실패 시 폴백
    import logging
    logger = logging.getLogger('money_admin_command')
    logger.error(f"필수 모듈 임포트 실패: {e}")
    raise


@register_command(
    name="소지금 관리",
    aliases=[
        "소지금 추가", "소지금 차감", "소지금추가", "소지금차감",
        "재화 추가", "재화추가", "재화 차감", "재화차감",
        "돈 추가", "돈추가", "돈 차감", "돈차감"
    ],
    description="관리자가 여러 캐릭터의 소지금을 일괄 추가/차감합니다. (관리자 전용)",
    category="관리자",
    examples=[
        "[소지금 추가/1000/홍길동]",
        "[소지금 차감/500/전원]",
        "[소지금추가/100/철수,영희]"
    ],
    admin_only=True,
    requires_sheets=True,
    requires_api=False
)
class MoneyAdminCommand(BaseStoreCommand):
    """
    소지금 관리 명령어 클래스

    관리자가 사용자들의 소지금을 일괄 추가/차감하는 시스템을 구현합니다.

    지원하는 형식:
    - [소지금 추가/금액/캐릭터명] : 특정 캐릭터의 소지금 추가
    - [소지금 차감/금액/캐릭터명] : 특정 캐릭터의 소지금 차감
    - [소지금추가/금액/캐릭터명] : 공백 없는 형식
    - [소지금차감/금액/캐릭터명] : 공백 없는 형식
    - [소지금 추가/금액/캐릭터1,캐릭터2,캐릭터3] : 여러 캐릭터 동시 처리
    - [소지금 차감/금액/전원] : 전체 캐릭터 처리

    처리 순서:
    1. 명령어 형식 검증 (추가/차감, 금액, 대상 분석)
    2. 대상 캐릭터 목록 해석 (개별/복수/전원)
    3. 현재 소지금 조회 및 계산
    4. 배치 업데이트로 일괄 적용
    5. 결과 메시지 생성
    """

    _sheet_error_suffix = " (소지금 관리)"
    _generic_error_prefix = "재화 관리 처리 중"

    def get_supported_keywords(self) -> List[str]:
        """
        지원되는 모든 키워드 목록 반환 (동적 화폐 단위 지원)

        Returns:
            List[str]: 지원되는 키워드 목록 (첫 번째가 대표 키워드)
        """
        from config.settings import config

        # 기본 키워드 (대표 키워드 먼저)
        keywords = [
            '소지금 추가',  # 대표 키워드
            '소지금 차감',
            '소지금추가',
            '소지금차감',
            '재화 추가', '재화추가', '재화 차감', '재화차감',
            '돈 추가', '돈추가', '돈 차감', '돈차감'
        ]

        # 동적 화폐 단위 키워드 추가
        try:
            currency = config.CURRENCY
            if currency:
                keywords.extend([
                    f'{currency} 추가',
                    f'{currency}추가',
                    f'{currency} 차감',
                    f'{currency}차감',
                ])
        except Exception as e:
            logger.warning("화폐 단위 키워드 추가 실패: %s", e)

        return keywords

    def __init__(self, sheets_manager=None, api=None, **kwargs):
        """
        MoneyAdminCommand 초기화

        Args:
            sheets_manager: Google Sheets 관리자
            api: 마스토돈 API 인스턴스
            **kwargs: 추가 의존성
        """
        super().__init__(sheets_manager, api, **kwargs)
        logger.debug(f"MoneyAdminCommand 초기화 완료: sheets_manager={self.sheets_manager is not None}")

    def _execute_command_logic(self, user, keywords: List[str]) -> Tuple[str, Any]:
        """
        소지금 관리 명령어 실행

        Args:
            user: 사용자 객체
            keywords: 명령어 키워드 리스트

        Returns:
            Tuple[str, Any]: (결과 메시지, 결과 데이터)

        Raises:
            CommandError: 명령어 실행 오류
        """
        if not self.sheets_manager:
            raise CommandError("시트 관리자가 설정되지 않았습니다.")

        try:
            # 1. 명령어 파싱
            operation, amount, targets = self._parse_money_command(keywords)
            
            # 2. 대상 캐릭터 목록 해석
            target_characters = self._resolve_target_characters(targets)
            if not target_characters:
                raise CommandError("처리할 대상 캐릭터가 없습니다.")
            
            # 3. 배치 업데이트 실행
            results = self._execute_batch_money_update(operation, amount, target_characters)
            
            # 4. 결과 메시지 생성
            result_message = self._generate_result_message(operation, amount, results)
            
            result_data = {
                'operation': operation,
                'amount': amount,
                'total_targets': len(target_characters),
                'successful_updates': len([r for r in results if r['success']]),
                'failed_updates': len([r for r in results if not r['success']]),
                'results': results
            }
            
            logger.info(f"소지금 관리 명령어 실행 완료: {operation} {amount} -> {len(target_characters)}명 대상")
            return result_message, result_data
            
        except CommandError:
            raise
        except Exception as e:
            logger.error(f"소지금 관리 명령어 실행 중 오류: {e}")
            raise CommandError("소지금 관리 명령어 실행 중 오류가 발생했습니다.")
    
    def _parse_money_command(self, keywords: List[str]) -> Tuple[str, int, str]:
        """
        명령어 키워드 파싱
        
        Args:
            keywords: 키워드 리스트
            
        Returns:
            Tuple[str, int, str]: (작업타입, 금액, 대상)
            
        Raises:
            CommandError: 파싱 오류
        """
        if len(keywords) < 3:
            raise CommandError("명령어 형식이 올바르지 않습니다. [소지금 추가/금액/대상] 형식으로 입력해주세요.")
        
        # 첫 번째 키워드에서 작업 타입 추출
        first_keyword = keywords[0].replace(" ", "").lower()
        
        if first_keyword in ['소지금추가', '소지금 추가']:
            operation = "추가"
        elif first_keyword in ['소지금차감', '소지금 차감']:
            operation = "차감"
        else:
            raise CommandError("지원하지 않는 작업입니다. '소지금 추가' 또는 '소지금 차감'을 사용해주세요.")
        
        # 두 번째 키워드에서 금액 추출
        try:
            amount = int(keywords[1])
            if amount <= 0:
                raise ValueError("금액은 양수여야 합니다.")
        except ValueError:
            raise CommandError("금액은 양의 정수로 입력해주세요.")
        
        # 세 번째 키워드에서 대상 추출
        targets = keywords[2]
        
        return operation, amount, targets
    
    def _resolve_target_characters(self, targets: str) -> List[str]:
        """
        대상 문자열을 캐릭터 목록으로 해석
        
        Args:
            targets: 대상 문자열 ("전원", "캐릭터1,캐릭터2" 등)
            
        Returns:
            List[str]: 캐릭터 이름 목록
        """
        try:
            # "전원" 처리
            if targets.strip() == "전원":
                return self._get_all_characters()
            
            # 쉼표로 구분된 캐릭터 목록 처리
            character_list = []
            for char_name in targets.split(','):
                char_name = char_name.strip()
                if char_name:
                    character_list.append(char_name)
            
            return character_list
            
        except Exception as e:
            logger.error(f"대상 캐릭터 해석 실패: {targets} -> {e}")
            return []
    
    def _get_all_characters(self) -> List[str]:
        """
        명단 캐시에서 모든 캐릭터 이름 조회
        
        Returns:
            List[str]: 모든 캐릭터 이름 목록
        """
        try:
            # 캐시 우선 사용
            # 표준화: SheetsManager에서 명단 조회
            user_data = []
            if self.sheets_manager:
                user_data = self.sheets_manager.get_roster_data(use_cache=True)
            characters = []
            
            for row in user_data:
                name = str(row.get('이름', '')).strip()
                if name:
                    characters.append(name)
            
            logger.debug(f"전체 캐릭터 조회: {len(characters)}명")
            return characters
            
        except Exception as e:
            logger.error(f"전체 캐릭터 조회 실패: {e}")
            return []
    
    def _execute_batch_money_update(self, operation: str, amount: int, target_characters: List[str]) -> List[Dict]:
        """
        배치 업데이트로 소지금 일괄 변경 (관리 워크시트 대상)
        
        Args:
            operation: 작업 타입 ("추가" 또는 "차감")
            amount: 변경할 금액
            target_characters: 대상 캐릭터 목록
            
        Returns:
            List[Dict]: 각 캐릭터별 처리 결과
        """
        results = []
        
        try:
            # 1) 관리 워크시트 데이터 조회 (실시간)
            management_data = self.sheets_manager.get_worksheet_data('관리', use_cache=False)
            worksheet = self.sheets_manager.get_worksheet('관리')

            # 2) 소지금 컬럼 찾기 (관리)
            money_col = self._find_money_column()
            if money_col is None:
                for char in target_characters:
                    results.append({
                        'character': char,
                        'success': False,
                        'error': '소지금 컬럼을 찾을 수 없습니다'
                    })
                return results
            
            # 3) 배치 업데이트용 데이터 준비
            batch_updates = []
            
            for char_name in target_characters:
                try:
                    # 명단 캐시에서 해당 캐릭터의 아이디를 찾음
                    target = None
                    roster = self.sheets_manager.get_roster_data(use_cache=True)
                    for r in roster:
                        if str(r.get('이름', '')).strip() == char_name:
                            target = r
                            break

                    if not target:
                        results.append({
                            'character': char_name,
                            'success': False,
                            'error': '명단에서 캐릭터를 찾을 수 없습니다'
                        })
                        continue

                    target_id = str(target.get('아이디', '')).strip()

                    # 관리 워크시트에서 행 찾기 (아이디 컬럼 동적 탐색)
                    id_col = self._find_id_column()
                    if id_col is None:
                        results.append({
                            'character': char_name,
                            'success': False,
                            'error': '아이디 컬럼을 찾을 수 없습니다'
                        })
                        continue
                    id_column = worksheet.col_values(id_col)
                    char_row = None
                    for i, cell in enumerate(id_column):
                        if str(cell).strip() == target_id:
                            char_row = i + 1
                            break
                    
                    if char_row is None:
                        results.append({
                            'character': char_name,
                            'success': False,
                            'error': '관리 워크시트에서 캐릭터를 찾을 수 없습니다'
                        })
                        continue
                    
                    # 현재 금액 조회
                    current_record = None
                    for rec in management_data:
                        if str(rec.get('아이디', '')).strip() == target_id:
                            current_record = rec
                            break

                    current_money = 0
                    if current_record:
                        raw_money = current_record.get('소지금', 0)
                        try:
                            current_money = int(float(str(raw_money).strip())) if str(raw_money).strip() else 0
                        except (ValueError, TypeError):
                            current_money = 0

                    # 새로운 금액 계산
                    if operation == "추가":
                        new_money = current_money + amount
                    else:  # 차감
                        new_money = max(0, current_money - amount)  # 음수 방지
                    
                    # 배치 업데이트 데이터 추가
                    batch_updates.append({
                        'range': f'{self._get_column_letter(money_col)}{char_row}',
                        'values': [[new_money]]
                    })
                    
                    results.append({
                        'character': char_name,
                        'success': True,
                        'old_money': current_money,
                        'new_money': new_money,
                        'change': new_money - current_money
                    })
                    
                except Exception as e:
                    results.append({
                        'character': char_name,
                        'success': False,
                        'error': str(e)
                    })
            
            # 4. 배치 업데이트 실행
            if batch_updates:
                success = self._execute_batch_update(batch_updates)
                if not success:
                    logger.error("배치 업데이트 실패")
                    # 모든 성공 결과를 실패로 변경
                    for result in results:
                        if result.get('success'):
                            result['success'] = False
                            result['error'] = '배치 업데이트 실패'
            
            return results

        except Exception as e:
            logger.error(f"배치 소지금 업데이트 실패: {e}")
            return [{'character': char, 'success': False, 'error': str(e)} for char in target_characters]

    def _get_column_letter(self, col_num: int) -> str:
        """
        컬럼 번호를 알파벳으로 변환 (1->A, 2->B, ...)
        
        Args:
            col_num: 컬럼 번호 (1부터 시작)
            
        Returns:
            str: 컬럼 알파벳
        """
        result = ""
        while col_num > 0:
            col_num -= 1
            result = chr(col_num % 26 + ord('A')) + result
            col_num //= 26
        return result
    
    def _execute_batch_update(self, batch_updates: List[Dict]) -> bool:
        """
        Google Sheets 배치 업데이트 실행 (SheetsManager.batch_update_cells 활용)

        Args:
            batch_updates: 업데이트할 데이터 목록 ({'range': 'D5', 'values': [[val]]})

        Returns:
            bool: 성공 여부
        """
        try:
            # batch_updates를 (row, col, value) 튜플 리스트로 변환
            updates_list = []
            for update in batch_updates:
                range_str = update['range']
                col_letter = ''.join(filter(str.isalpha, range_str))
                row_num = int(''.join(filter(str.isdigit, range_str)))
                col_num = self._column_letter_to_number(col_letter)
                value = update['values'][0][0]
                updates_list.append((row_num, col_num, value))

            success = self.sheets_manager.batch_update_cells('관리', updates_list)
            if success:
                logger.info("배치 업데이트 성공: %d개 셀", len(updates_list))
            return success

        except Exception as e:
            logger.error("배치 업데이트 실패: %s", e)
            return False
    
    def _column_letter_to_number(self, col_letter: str) -> int:
        """
        컬럼 알파벳을 번호로 변환 (A->1, B->2, ...)
        
        Args:
            col_letter: 컬럼 알파벳
            
        Returns:
            int: 컬럼 번호 (1부터 시작)
        """
        result = 0
        for char in col_letter.upper():
            result = result * 26 + (ord(char) - ord('A') + 1)
        return result
    
    def _generate_result_message(self, operation: str, amount: int, results: List[Dict]) -> str:
        """
        결과 메시지 생성
        
        Args:
            operation: 작업 타입
            amount: 변경 금액
            results: 처리 결과 목록
            
        Returns:
            str: 결과 메시지
        """
        successful = [r for r in results if r.get('success')]
        failed = [r for r in results if not r.get('success')]
        
        message_parts = []
        
        # 기본 정보
        currency_unit = config.CURRENCY
        message_parts.append(f"소지금 {operation} 완료")
        message_parts.append(f"변경 금액: {amount:,} {currency_unit}")
        message_parts.append("")
        
        # 성공한 경우
        if successful:
            message_parts.append(f"성공: {len(successful)}명")
            for result in successful[:30]:  # 최대 10명까지만 표시
                char_name = result['character']
                old_money = result.get('old_money', 0)
                new_money = result.get('new_money', 0)
                change = result.get('change', 0)
                change_text = f"+{change:,}" if change >= 0 else f"{change:,}"
                message_parts.append(f"• {char_name}: {old_money:,} → {new_money:,}")
            
            if len(successful) > 10:
                message_parts.append(f"• ... 외 {len(successful) - 10}명")
            message_parts.append("")
        
        # 실패한 경우
        if failed:
            message_parts.append(f"❌ **실패: {len(failed)}명**")
            for result in failed[:5]:  # 최대 5명까지만 표시
                char_name = result['character']
                error = result.get('error', '알 수 없는 오류')
                message_parts.append(f"• {char_name}: {error}")
            
            if len(failed) > 5:
                message_parts.append(f"• ... 외 {len(failed) - 5}명")
        
        return "\n".join(message_parts)
    
    def get_help_text(self) -> str:
        """
        도움말 텍스트 반환
        
        Returns:
            str: 도움말 텍스트
        """
        return (
            "💰 **소지금 관리 명령어**\n"
            "캐릭터들의 소지금을 일괄 추가하거나 차감합니다.\n\n"
            "**사용법:**\n"
            "• `[소지금 추가/금액/캐릭터명]` - 특정 캐릭터 소지금 추가\n"
            "• `[소지금 차감/금액/캐릭터명]` - 특정 캐릭터 소지금 차감\n"
            "• `[소지금 추가/금액/캐릭터1,캐릭터2]` - 여러 캐릭터 동시 처리\n"
            "• `[소지금 차감/금액/전원]` - 전체 캐릭터 처리\n\n"
            "**참고:**\n"
            "• 공백 없이 `[소지금추가/금액/대상]`도 가능합니다\n"
            "• 배치 업데이트로 API 제한을 최소화합니다\n"
            "• 차감 시 소지금이 음수가 되지 않도록 보정됩니다."
        )


def is_money_admin_command(keywords: List[str]) -> bool:
    """
    소지금 관리 명령어 여부 확인
    
    Args:
        keywords: 키워드 리스트
        
    Returns:
        bool: 소지금 관리 명령어 여부
    """
    if not keywords:
        return False
    
    first_keyword = keywords[0].replace(" ", "").lower()
    return first_keyword in ['소지금추가', '소지금차감', '소지금 추가', '소지금 차감']
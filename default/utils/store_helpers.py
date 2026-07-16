"""
상점 관련 명령어 공통 유틸리티 함수
중복 코드를 제거하고 재사용 가능한 헬퍼 함수들을 제공합니다.
"""

import os
import sys
import json
import ast
import re
from typing import Dict, List, Any, Optional, Tuple
from utils.log_sanitizer import sanitize_log_input

# 경로 설정
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

try:
    from utils.cache_manager import bot_cache
    from utils.logging_config import logger
    from config.settings import config
except ImportError as e:
    import logging
    logger = logging.getLogger('store_helpers')


def _parse_simple_format(inventory_str: str) -> Dict[str, int]:
    """
    단순 텍스트 형식 인벤토리 파싱 (개선된 버전)
    형식: "아이템명: 개수, 아이템명2: 개수2"
    예: "큰 가방: 1, 목걸이: 2, 호박 설탕: 1"

    주의사항:
    - 아이템명에 쉼표(,)가 포함되면 안 됩니다
    - 아이템명에 콜론(:)은 허용됩니다 (rsplit 사용)
    - 개수는 1 이상의 정수만 허용됩니다
    - 빈 아이템명은 무시됩니다
    - 콜론 앞뒤 공백은 자동으로 제거됩니다

    Args:
        inventory_str: 단순 텍스트 형식 문자열

    Returns:
        Dict[str, int]: 파싱된 인벤토리 딕셔너리
    """
    result = {}

    if not inventory_str or inventory_str.strip() == '':
        return result

    # 쉼표로 분리
    items = inventory_str.split(',')

    for item in items:
        item = item.strip()

        # 빈 항목 무시
        if not item:
            continue

        # 콜론으로 아이템명과 개수 분리
        if ':' not in item:
            logger.warning(f"단순 형식 파싱 실패 (콜론 없음): '{item}'")
            continue

        # 마지막 콜론을 기준으로 분리 (아이템명에 콜론이 있을 수 있음)
        parts = item.rsplit(':', 1)
        if len(parts) != 2:
            logger.warning(f"단순 형식 파싱 실패 (형식 오류): '{item}'")
            continue

        name = parts[0].strip()
        count_str = parts[1].strip()

        # 빈 아이템명 체크
        if not name:
            logger.warning(f"단순 형식 파싱 실패 (빈 아이템명): '{item}'")
            continue

        # 개수 파싱
        try:
            count = int(count_str)

            # 개수 유효성 검증
            if count <= 0:
                logger.warning(f"단순 형식 파싱: 0 이하 개수 무시: '{name}:{count}'")
                continue

            # 개수가 너무 큰 경우 (10억 이상)
            if count > 1_000_000_000:
                logger.info(f"단순 형식 파싱: 비정상적으로 큰 개수: '{name}:{count}' → 제한")
                count = 1_000_000_000

            # 중복 아이템명 처리 (나중 값으로 덮어씀, 경고 로그)
            if name in result:
                logger.warning(f"단순 형식 파싱: 중복 아이템명 '{name}' (이전: {result[name]}, 새: {count}) → 새 값 사용")

            result[name] = count

        except (ValueError, TypeError) as e:
            logger.warning("단순 형식 파싱 실패 (개수 파싱 오류): '%s' (count_str='%s', error=%s)",
                           sanitize_log_input(item), sanitize_log_input(count_str), e)
            continue
        except OverflowError as e:
            logger.error("단순 형식 파싱 실패 (개수 오버플로우): '%s' (count_str='%s', error=%s)",
                         sanitize_log_input(item), sanitize_log_input(count_str), e)
            continue

    return result


def parse_inventory_string(inventory_str: str) -> Optional[Dict[str, int]]:
    """
    인벤토리 문자열 파싱 (하위 호환성 유지)

    지원 형식:
    1. JSON 형식 (기존): {"아이템명":1,"아이템명2":2}
    2. 단순 텍스트 형식 (신규): "아이템명: 1, 아이템명2: 2"
    3. 단순 텍스트 형식 (구형): "아이템명:1, 아이템명2:2" (띄어쓰기 없음)

    Args:
        inventory_str: 인벤토리 문자열

    Returns:
        Optional[Dict[str, int]]: 파싱된 인벤토리 딕셔너리, 파싱 실패 시 None
    """
    if not inventory_str or inventory_str.strip() in ['{}', '{ }', '']:
        return {}

    inventory_str = inventory_str.strip()

    # 1. JSON 형식 파싱 시도 (기존 데이터 호환)
    try:
        parsed = json.loads(inventory_str)
        if isinstance(parsed, dict):
            logger.debug(f"JSON 형식 파싱 성공: {inventory_str[:50]}...")
            return {str(k): int(v) for k, v in parsed.items() if v > 0}
    except (json.JSONDecodeError, ValueError):
        pass

    # 2. ast.literal_eval 파싱 시도 (Python dict 형식)
    try:
        parsed = ast.literal_eval(inventory_str)
        if isinstance(parsed, dict):
            logger.debug(f"ast.literal_eval 파싱 성공: {inventory_str[:50]}...")
            return {str(k): int(v) for k, v in parsed.items() if v > 0}
    except (SyntaxError, ValueError):
        pass

    # 3. 잘못된 JSON 형식 자동 복구 시도
    # 예: {"아이템^","아이템2^","아이템3":1} → {"아이템^":1,"아이템2^":1,"아이템3":1}
    if inventory_str.startswith('{'):
        try:
            # "아이템명" 다음에 콜론이 없고 콤마나 }가 오면 :1 추가
            fixed = re.sub(r'"([^"]+)"(?=\s*[,}])', r'"\1":1', inventory_str)

            if fixed != inventory_str:
                logger.info(f"잘못된 JSON 형식 자동 복구 시도: {inventory_str[:50]}...")

                # 복구된 문자열로 JSON 파싱 재시도
                try:
                    parsed = json.loads(fixed)
                    if isinstance(parsed, dict):
                        logger.info(f"인벤토리 자동 복구 성공")
                        return {str(k): int(v) for k, v in parsed.items() if v > 0}
                except (json.JSONDecodeError, ValueError):
                    pass
        except Exception as e:
            logger.warning(f"인벤토리 자동 복구 중 오류: {e}")

    # 4. 단순 텍스트 형식 파싱 시도 (신규)
    result = _parse_simple_format(inventory_str)
    if result:
        logger.debug(f"단순 형식 파싱 성공: {inventory_str[:50]}...")
        return result

    logger.warning(f"인벤토리 파싱 실패 (모든 형식): {inventory_str[:100]}...")
    return None


def serialize_inventory(inventory: Dict[str, int]) -> str:
    """
    인벤토리 딕셔너리를 단순 텍스트 형식으로 직렬화 (검증 포함)
    형식: "아이템명: 개수, 아이템명2: 개수2"
    예: {'큰 가방': 1, '목걸이': 2} → "큰 가방: 1, 목걸이: 2"

    주의사항:
    - 아이템명에 쉼표(,)가 포함된 아이템은 스킵됨 (직렬화에서 제외)
    - 개수는 1 이상의 정수만 저장
    - None, 문자열, 음수 등은 자동 필터링
    - 콜론 뒤에 공백이 포함됩니다

    Args:
        inventory: 인벤토리 딕셔너리

    Returns:
        str: 단순 텍스트 형식 문자열

    """
    if not inventory:
        return ""

    # 유효성 검증 및 정제
    valid_items = []

    for name, count in inventory.items():
        # 아이템명 검증
        if not name or not isinstance(name, str):
            logger.warning(f"직렬화 스킵: 유효하지 않은 아이템명: {name}")
            continue

        name = str(name).strip()

        # 빈 이름 체크
        if not name:
            logger.warning(f"직렬화 스킵: 빈 아이템명")
            continue

        # 쉼표 포함 아이템은 직렬화 스킵 (자동 수정 대신 거부)
        if ',' in name:
            logger.error(f"아이템명에 쉼표 포함되어 직렬화 스킵: '{name}'")
            continue

        # 개수 검증
        try:
            count = int(count)
        except (ValueError, TypeError) as e:
            logger.warning(f"직렬화 스킵: 유효하지 않은 개수: {name}={count} (error={e})")
            continue

        # 양수만 저장
        if count <= 0:
            logger.debug(f"직렬화 스킵: 0 이하 개수: {name}={count}")
            continue

        # 개수 제한
        if count > 1_000_000_000:
            logger.warning(f"직렬화: 비정상적으로 큰 개수 제한: {name}={count} → 1,000,000,000")
            count = 1_000_000_000

        valid_items.append((name, count))

    if not valid_items:
        return ""

    # 아이템명 순으로 정렬 (일관성 유지)
    valid_items.sort(key=lambda x: x[0])

    # "아이템명: 개수" 형식으로 변환 (콜론 뒤 공백 포함)
    items = [f"{name}: {count}" for name, count in valid_items]

    # 쉼표와 공백으로 연결
    result = ", ".join(items)

    logger.debug(f"인벤토리 직렬화: {len(valid_items)}개 아이템 → {result[:50]}...")

    return result


def validate_inventory_roundtrip(inventory: Dict[str, int]) -> Tuple[bool, str]:
    """
    인벤토리가 왕복 변환(직렬화 → 파싱)에서 데이터 손실 없이 복원되는지 검증

    Args:
        inventory: 검증할 인벤토리 딕셔너리

    Returns:
        Tuple[bool, str]: (성공 여부, 오류 메시지)
    """
    try:
        # 직렬화
        serialized = serialize_inventory(inventory)

        # 파싱
        parsed = parse_inventory_string(serialized)
        if parsed is None:
            return False, "왕복 변환 실패 - 파싱 결과가 None"

        # 비교 (0 이하 값은 자동 필터링되므로 제외)
        original_filtered = {k: v for k, v in inventory.items() if v > 0}

        if parsed != original_filtered:
            # 차이점 분석
            missing_items = set(original_filtered.keys()) - set(parsed.keys())
            extra_items = set(parsed.keys()) - set(original_filtered.keys())
            changed_items = {k: (original_filtered[k], parsed[k])
                           for k in original_filtered.keys() & parsed.keys()
                           if original_filtered[k] != parsed[k]}

            error_parts = []
            if missing_items:
                error_parts.append(f"누락된 아이템: {missing_items}")
            if extra_items:
                error_parts.append(f"추가된 아이템: {extra_items}")
            if changed_items:
                error_parts.append(f"변경된 개수: {changed_items}")

            error_msg = f"왕복 변환 실패 - {', '.join(error_parts)}"
            logger.error(f"인벤토리 왕복 변환 검증 실패: {error_msg}")
            logger.error(f"원본: {original_filtered}")
            logger.error(f"직렬화: {serialized}")
            logger.error(f"파싱: {parsed}")

            return False, error_msg

        return True, ""

    except Exception as e:
        error_msg = f"왕복 변환 검증 중 예외: {e}"
        logger.error(error_msg, exc_info=True)
        return False, error_msg


def find_column_by_header(worksheet, keyword: str) -> Optional[int]:
    """
    워크시트 헤더 행에서 keyword를 포함하는 컬럼 번호를 찾습니다.

    부분 일치를 유지하되(예: '가격' → '가격(달러)'), **공백은 무시**합니다.
    GM이 만든 헤더에 눈에 안 보이는 공백이 섞이는 일이 실제로 반복돼서,
    그 때문에 컬럼을 못 찾고 기능이 죽는 것을 막습니다.
    ('조사 포인트'는 '조사  포인트'(공백 2개) 안에 부분 문자열로 들어 있지 않다.)

    Args:
        worksheet: gspread 워크시트 객체
        keyword: 검색할 헤더 키워드 (예: '아이디', '소지금', '소지품')

    Returns:
        Optional[int]: 1부터 시작하는 컬럼 번호, 없으면 None
    """
    def _norm(value) -> str:
        return ' '.join(str(value if value is not None else '').split())

    try:
        header_row = worksheet.row_values(1)
        target = _norm(keyword)
        for i, header in enumerate(header_row):
            if target and target in _norm(header):
                return i + 1
        logger.warning(f"'{keyword}' 컬럼을 찾을 수 없습니다.")
        return None
    except Exception as e:
        logger.error(f"'{keyword}' 컬럼 찾기 실패: {e}")
        return None


def find_user_row(worksheet, user_id: str, id_col: Optional[int] = None) -> Optional[int]:
    """
    워크시트에서 사용자 ID에 해당하는 행 번호를 찾습니다.

    Args:
        worksheet: gspread 워크시트 객체
        user_id: 사용자 ID
        id_col: 아이디 컬럼 번호 (None이면 자동 탐색)

    Returns:
        Optional[int]: 1부터 시작하는 행 번호, 없으면 None
    """
    try:
        if id_col is None:
            id_col = find_column_by_header(worksheet, '아이디')
        if id_col is None:
            return None

        id_column = worksheet.col_values(id_col)
        for i, cell_value in enumerate(id_column):
            if str(cell_value).strip() == user_id:
                return i + 1

        return None
    except Exception as e:
        logger.error(f"사용자 행 찾기 실패: {user_id} -> {e}")
        return None


# 가격 헤더에서 화폐 단위를 추출하는 컴파일된 정규식
_PRICE_HEADER_RE = re.compile(r'가격\s*\(([^)]+)\)')


def extract_price_info(item_data: dict) -> Tuple[Optional[str], Optional[int], Optional[str]]:
    """
    아이템 데이터에서 가격 관련 정보를 추출합니다.

    Args:
        item_data: 아이템 행 딕셔너리

    Returns:
        Tuple[Optional[str], Optional[int], Optional[str]]:
            (price_key, price_value, currency_name)
            - price_key: 가격 컬럼 키 (예: '가격(코인)')
            - price_value: 정수 가격 (비매품이면 None)
            - currency_name: 화폐 단위 (예: '코인')
    """
    price_key = None
    currency_name = None

    for key in item_data.keys():
        if '가격' in key:
            price_key = key
            match = _PRICE_HEADER_RE.search(key)
            if match:
                currency_name = match.group(1).strip()
            break

    if price_key is None:
        return None, None, None

    price_str = str(item_data.get(price_key, '0')).strip()

    if '비매품' in price_str:
        return price_key, None, currency_name

    try:
        price_value = int(float(price_str))
    except (ValueError, TypeError):
        price_value = None

    return price_key, price_value, currency_name


def safe_parse_inventory(inventory_str: str) -> Dict[str, int]:
    """
    인벤토리 문자열을 파싱합니다. parse_inventory_string의 안전한 래퍼로,
    None 대신 빈 딕셔너리를 반환합니다.

    Args:
        inventory_str: 인벤토리 문자열

    Returns:
        Dict[str, int]: 파싱된 인벤토리 (실패 시 빈 딕셔너리)
    """
    result = parse_inventory_string(inventory_str)
    if result is None:
        logger.warning(f"인벤토리 파싱 실패, 빈 인벤토리로 처리: {inventory_str[:50]}...")
        return {}
    return result


def invalidate_user_cache():
    """
    사용자 데이터 캐시 무효화 (공통 함수)
    user_data와 all_users_data 캐시를 모두 무효화합니다.
    """
    try:
        bot_cache.command_cache.delete("user_data")
        bot_cache.command_cache.delete("all_users_data")
        logger.debug("사용자 데이터 캐시 무효화")
    except Exception as e:
        logger.warning(f"캐시 무효화 실패: {e}")


def load_item_data(sheets_manager) -> List[Dict[str, Any]]:
    """
    아이템 데이터 로드 (공통 함수)
    
    Args:
        sheets_manager: Google Sheets 관리자
        
    Returns:
        List[Dict]: 아이템 데이터 리스트
    """
    # 캐시 확인
    cached_data = bot_cache.get_item_data()
    if cached_data:
        return cached_data
    
    # 캐시 없으면 시트에서 로드
    try:
        if sheets_manager:
            item_data = sheets_manager.get_item_data()
            if item_data:
                bot_cache.cache_item_data(item_data, ttl_seconds=config.CACHE_TTL)
                return item_data
    except Exception as e:
        logger.warning(f"아이템 데이터 로드 실패: {e}")
    
    return []


def load_user_data(sheets_manager) -> List[Dict[str, Any]]:
    """
    명단 데이터 로드 (공통 함수)
    
    Args:
        sheets_manager: Google Sheets 관리자
        
    Returns:
        List[Dict]: 명단 데이터 리스트
    """
    try:
        if sheets_manager:
            data = sheets_manager.get_roster_data(use_cache=True)
            if data:
                return data
    except Exception as e:
        logger.warning(f"명단 데이터 로드 실패: {e}")
    
    logger.info("명단 데이터 없음")
    return []


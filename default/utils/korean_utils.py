"""
한국어 유틸리티 - 받침에 따른 조사 변경
한국어 문법에 맞게 조사를 자동으로 선택하는 유틸리티입니다.
"""

import re
from typing import Dict, Any


def has_final_consonant(char: str) -> bool:
    """
    한글 문자의 받침(종성) 여부 판단
    
    Args:
        char: 한글 문자 1개
        
    Returns:
        bool: 받침이 있으면 True, 없으면 False
    """
    if not char:
        return False
    
    # 한글이 아닌 경우 (숫자, 영문, 기호 등)
    if not ('가' <= char <= '힣'):
        # 숫자나 영문의 경우 발음을 기준으로 판단
        if char.isdigit():
            # 숫자별 받침 여부: 1,7,8 = 받침 있음, 나머지 = 받침 없음
            return char in '178'
        elif char.isalpha():
            # 영문의 경우 발음을 기준으로 (간단한 규칙)
            # L, M, N, R = 받침 있음으로 처리
            return char.upper() in 'LMNR'
        else:
            # 기타 문자는 받침 없음으로 처리
            return False
    
    # 한글의 경우: 유니코드 계산으로 종성 확인
    code = ord(char) - ord('가')
    final_consonant = code % 28  # 종성 인덱스 (0이면 받침 없음)
    
    return final_consonant != 0


def get_last_char(word: str) -> str:
    """
    단어의 마지막 문자 반환 (공백, 특수문자 제외)
    
    Args:
        word: 단어
        
    Returns:
        str: 마지막 유효 문자
    """
    if not word:
        return ''
    
    # 뒤에서부터 검사하여 첫 번째 유효한 문자 찾기
    for char in reversed(word):
        if char.strip() and char not in '()[]{}.,!?':
            return char
    
    return word[-1] if word else ''


def apply_josa(text: str, **kwargs) -> str:
    """
    텍스트에 조사를 적용
    
    Args:
        text: 조사 플레이스홀더가 포함된 텍스트
        **kwargs: 변수명과 값의 매핑
        
    Returns:
        str: 조사가 적용된 텍스트
        
    Examples:
        >>> apply_josa("{name}{은는} 밥을 먹습니다.", name="철수")
        "철수는 밥을 먹습니다."
        >>> apply_josa("{name}{이가} 좋습니다.", name="영희")  
        "영희가 좋습니다."
    """
    result = text
    
    # 조사 매핑 테이블
    josa_map = {
        '{은는}': ('은', '는'),  # 받침 있음/없음
        '{이가}': ('이', '가'),
        '{을를}': ('을', '를'),
        '{과와}': ('과', '와'),
        '{아야}': ('아', '야'),  # 호격조사 추가
        '{으로로}': ('으로', '로'),  # 방향/수단 조사 추가
    }
    
    # 각 변수에 대해 조사 적용
    for var_name, var_value in kwargs.items():
        var_str = str(var_value)
        placeholder = '{' + var_name + '}'
        
        # 변수 치환
        result = result.replace(placeholder, var_str)
        
        # 해당 변수 뒤에 오는 조사들 처리
        for josa_placeholder, (with_final, without_final) in josa_map.items():
            if josa_placeholder in result:
                # 변수 바로 뒤에 조사가 오는 경우만 처리
                pattern = re.escape(var_str) + re.escape(josa_placeholder)
                matches = re.finditer(pattern, result)
                
                for match in reversed(list(matches)):  # 뒤에서부터 치환
                    start, end = match.span()
                    
                    # 마지막 문자로 받침 판단
                    last_char = get_last_char(var_str)
                    has_final = has_final_consonant(last_char)
                    
                    # 적절한 조사 선택
                    chosen_josa = with_final if has_final else without_final
                    
                    # 치환
                    result = result[:start + len(var_str)] + chosen_josa + result[end:]
    
    return result


def format_korean(template: str, **kwargs) -> str:
    """
    한국어 조사가 포함된 템플릿 문자열 포맷팅
    
    Args:
        template: 템플릿 문자열
        **kwargs: 변수 매핑
        
    Returns:
        str: 포맷된 문자열
        
    Examples:
        >>> format_korean("{user}{은는} {item}{을를} 가지고 있습니다.", user="민수", item="사과")
        "민수는 사과를 가지고 있습니다."
    """
    return apply_josa(template, **kwargs)


# 편의 함수들
def add_eun_neun(word: str) -> str:
    """은/는 조사 추가"""
    last_char = get_last_char(word)
    return word + ('은' if has_final_consonant(last_char) else '는')


def add_i_ga(word: str) -> str:
    """이/가 조사 추가"""
    last_char = get_last_char(word)
    return word + ('이' if has_final_consonant(last_char) else '가')


def add_eul_reul(word: str) -> str:
    """을/를 조사 추가"""
    last_char = get_last_char(word)
    return word + ('을' if has_final_consonant(last_char) else '를')


def add_gwa_wa(word: str) -> str:
    """과/와 조사 추가"""
    last_char = get_last_char(word)
    return word + ('과' if has_final_consonant(last_char) else '와')


# 테스트용 함수
def test_korean_utils():
    """한국어 유틸리티 테스트"""
    test_cases = [
        # 받침 있는 경우
        ("철수", True),
        ("영희", False),
        ("민석", True),
        ("수연", False),
        ("1", True),   # 일
        ("2", False),  # 이
        ("7", True),   # 칠
        ("8", True),   # 팔
        ("9", False),  # 구
        
        # 템플릿 테스트
        ("{name}{은는} 학생입니다.", {"name": "철수"}, "철수는 학생입니다."),
        ("{name}{이가} 좋아요.", {"name": "영희"}, "영희가 좋아요."),
        ("{item}{을를} 먹었어요.", {"item": "사과"}, "사과를 먹었어요."),
        ("{friend}{과와} 놀았어요.", {"friend": "민수"}, "민수와 놀았어요."),
    ]
    
    print("=== 한국어 유틸리티 테스트 ===")
    
    # 받침 판단 테스트
    print("\n[받침 판단 테스트]")
    for word, expected in test_cases[:10]:
        last_char = get_last_char(word)
        result = has_final_consonant(last_char)
        status = "✓" if result == expected else "✗"
        print(f"{status} '{word}' -> {result} (예상: {expected})")
    
    # 템플릿 테스트
    print("\n[템플릿 테스트]")
    for template, kwargs, expected in test_cases[10:]:
        result = format_korean(template, **kwargs)
        status = "✓" if result == expected else "✗"
        print(f"{status} '{template}' -> '{result}'")
        if result != expected:
            print(f"    예상: '{expected}'")


if __name__ == "__main__":
    test_korean_utils()
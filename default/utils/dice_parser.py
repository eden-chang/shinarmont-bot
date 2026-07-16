"""
다이스 표현식 공용 파서

정수, `ndm`, `ndm+k`, `ndm-k`, `-(ndm+k)` 형식의 수치 표현을 평가합니다.
`use_item_command`와 조사 명령어 등에서 공통으로 사용합니다.
"""

import random
import re
from typing import Tuple

try:
    from utils.logging_config import logger
except ImportError:
    import logging
    logger = logging.getLogger('dice_parser')


MIN_DICE_COUNT = 1
MAX_DICE_COUNT = 20
MIN_DICE_SIDES = 2
MAX_DICE_SIDES = 1000

_DICE_BODY = re.compile(r'^\d+[dD]\d+([\+\-]\d+)?$')
_DICE_CORE = re.compile(r'(\d+)[dD](\d+)')
_MODIFIER = re.compile(r'([\+\-]\d+)')
_INT_LITERAL = re.compile(r'^-?\d+$')


def is_dice_expression(value_str: str) -> bool:
    """
    다이스 표현식 여부 확인.

    Args:
        value_str: 검사할 문자열.

    Returns:
        bool: `ndm`, `ndm+k`, `ndm-k`, `-(ndm+k)` 중 하나면 True.
    """
    if not value_str:
        return False

    value_str = value_str.strip()

    if value_str.startswith('-(') and value_str.endswith(')'):
        inner = value_str[2:-1]
        return bool(_DICE_BODY.match(inner))

    return bool(_DICE_BODY.match(value_str))


def parse_and_roll_dice(dice_expression: str) -> Tuple[int, str]:
    """
    다이스 표현식을 굴려 결과값과 상세 로그를 반환합니다.

    Args:
        dice_expression: `1d6`, `2d6+3`, `-(1d6+3)` 등 다이스 표현식.

    Returns:
        Tuple[int, str]: (결과값, 상세 로그).

    Raises:
        ValueError: 표현식이 잘못되었거나 제한 범위를 벗어나는 경우.
    """
    is_negative = False
    base_expr = dice_expression.strip()

    if base_expr.startswith('-(') and base_expr.endswith(')'):
        is_negative = True
        base_expr = base_expr[2:-1]
        logger.debug(f"음수 다이스 표현식: -{base_expr}")

    modifier = 0
    modifier_match = _MODIFIER.search(base_expr)
    if modifier_match:
        modifier = int(modifier_match.group(1))
        base_expr = base_expr.replace(modifier_match.group(1), '')

    match = _DICE_CORE.match(base_expr)
    if not match:
        raise ValueError(f"잘못된 다이스 표현식: {dice_expression}")

    num_dice = int(match.group(1))
    dice_sides = int(match.group(2))

    if num_dice < MIN_DICE_COUNT or num_dice > MAX_DICE_COUNT:
        raise ValueError(f"주사위 개수는 {MIN_DICE_COUNT}~{MAX_DICE_COUNT}개 사이여야 합니다.")
    if dice_sides < MIN_DICE_SIDES or dice_sides > MAX_DICE_SIDES:
        raise ValueError(f"주사위 면수는 {MIN_DICE_SIDES}~{MAX_DICE_SIDES}면 사이여야 합니다.")

    rolls = [random.randint(1, dice_sides) for _ in range(num_dice)]
    rolls_sum = sum(rolls)
    subtotal = rolls_sum + modifier
    total = -subtotal if is_negative else subtotal

    detail = _format_dice_detail(
        num_dice=num_dice,
        dice_sides=dice_sides,
        modifier=modifier,
        rolls=rolls,
        rolls_sum=rolls_sum,
        total=total,
        is_negative=is_negative,
    )
    return total, detail


def evaluate_amount(expression: str) -> Tuple[int, str]:
    """
    정수 또는 다이스 표현식을 평가해 (값, 상세 로그)를 반환합니다.

    Args:
        expression: 평가할 문자열.

    Returns:
        Tuple[int, str]: (결과값, 상세 로그). 정수인 경우 상세 로그는 빈 문자열.

    Raises:
        ValueError: 표현식이 정수도 다이스도 아닌 경우.
    """
    if expression is None:
        raise ValueError("수치 표현식이 비어있습니다.")

    expr = str(expression).strip()
    if not expr:
        raise ValueError("수치 표현식이 비어있습니다.")

    if is_dice_expression(expr):
        return parse_and_roll_dice(expr)

    if _INT_LITERAL.match(expr):
        return int(expr), ""

    try:
        return int(float(expr)), ""
    except (ValueError, TypeError) as exc:
        raise ValueError(f"숫자로 변환할 수 없는 표현식: {expression}") from exc


def _format_dice_detail(
    num_dice: int,
    dice_sides: int,
    modifier: int,
    rolls: list,
    rolls_sum: int,
    total: int,
    is_negative: bool,
) -> str:
    """다이스 상세 로그 문자열 포맷."""
    if num_dice == 1:
        roll_part = f"[{rolls[0]}]"
        if is_negative:
            if modifier != 0:
                return f"-({num_dice}d{dice_sides}{modifier:+d}) → -({roll_part} {modifier:+d}) = {total}"
            return f"-({num_dice}d{dice_sides}) → -{roll_part} = {total}"
        if modifier != 0:
            return f"{num_dice}d{dice_sides}{modifier:+d} → {roll_part} {modifier:+d} = {total}"
        return f"{num_dice}d{dice_sides} → {roll_part} = {total}"

    rolls_str = ", ".join(str(r) for r in rolls)
    if is_negative:
        if modifier != 0:
            return (
                f"-({num_dice}d{dice_sides}{modifier:+d}) → "
                f"-([{rolls_str}] = {rolls_sum} {modifier:+d}) = {total}"
            )
        return f"-({num_dice}d{dice_sides}) → -([{rolls_str}] = {rolls_sum}) = {total}"
    if modifier != 0:
        return (
            f"{num_dice}d{dice_sides}{modifier:+d} → "
            f"[{rolls_str}] = {rolls_sum} {modifier:+d} = {total}"
        )
    return f"{num_dice}d{dice_sides} → [{rolls_str}] = {total}"

"""Accepted SQL-backed money and text values shared by requests and services."""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Annotated

from pydantic import AfterValidator, Field, StringConstraints

CENT = Decimal("0.01")
MONEY_MAX = Decimal("99999999.99")
WEIGHT_STEP = Decimal("0.0001")
PURCHASE_WEIGHT_MAX = Decimal("99999999.9999")
PACKED_WEIGHT_MAX = Decimal("999999.9999")
INT32_MAX = 2_147_483_647


def unicode_text(value: str) -> str:
    if "\x00" in value:
        raise ValueError("Text cannot contain null characters")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("Text must contain valid Unicode characters") from None
    return value


def required_text(value: str, *, limit: int) -> str:
    value = unicode_text(value).strip()
    if not value or len(value) > limit:
        raise ValueError(f"Text must contain between 1 and {limit} characters")
    return value


def money(value) -> Decimal:
    """Round calculated values explicitly; reject nonfinite/overflowed totals."""
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or abs(amount) > MONEY_MAX:
            raise ValueError("Amount must be finite and within the supported range")
        return amount.quantize(CENT, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        raise ValueError("Amount must be a finite decimal number") from None


def positive_money(value) -> Decimal:
    amount = money(value)
    if amount <= 0 or Decimal(str(value)) != amount:
        raise ValueError("Amount must be positive and use at most two decimal places")
    return amount


def fixed_decimal(value, *, quantum: Decimal, maximum: Decimal) -> Decimal:
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or not 0 <= amount <= maximum:
            raise ValueError("Value must be finite, nonnegative and within the supported range")
        normalized = amount.quantize(quantum, rounding=ROUND_HALF_UP)
        if normalized != amount:
            raise ValueError("Value has more decimal places than supported")
        return normalized
    except InvalidOperation:
        raise ValueError("Value must be a finite decimal number") from None


def purchase_weight(value) -> Decimal:
    return fixed_decimal(value, quantum=WEIGHT_STEP, maximum=PURCHASE_WEIGHT_MAX)


def buffer_percentage(value) -> Decimal:
    return fixed_decimal(value, quantum=CENT, maximum=Decimal("100"))


UnicodeText = Annotated[str, AfterValidator(unicode_text)]
BankName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64),
                     AfterValidator(unicode_text)]
BankReference = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
                          AfterValidator(unicode_text)]
PackLabel = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64),
                      AfterValidator(unicode_text)]
PositiveMoney = Annotated[Decimal, Field(gt=0, le=MONEY_MAX, max_digits=10,
                                       decimal_places=2, allow_inf_nan=False)]

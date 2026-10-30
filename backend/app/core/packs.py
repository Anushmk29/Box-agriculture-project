"""Physical pack identity shared by intake, packing and selling prices."""

import re
import unicodedata
from decimal import Decimal


def _bounded_key(value: str) -> str:
    if len(value) > 64 or "\x00" in value:
        raise ValueError("Normalized pack size must fit 64 characters and contain no null characters")
    return value


def pack_key(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).strip().casefold()
    _bounded_key(value)
    if not value or re.fullmatch(r"[\d.,]+", value):
        raise ValueError("Pack size needs a unit or packaging label, for example 100g or 1pc")
    if value in {"pc", "pcs", "piece", "pieces", "1 pc", "1pcs"}:
        return "1pc"
    match = re.fullmatch(r"(~?)(\d+(?:[.,]\d+)?)\s*(kilograms?|kg|grams?|gr\.?|g)\s*(.*)", value)
    if match:
        approximate, amount, unit, packaging = match.groups()
        grams = Decimal(amount.replace(",", ".")) * (1000 if unit.startswith("k") else 1)
        label = format(grams.normalize(), "f")
        return _bounded_key(f"{approximate}{label}g" + (f" {packaging.strip()}" if packaging.strip() else ""))
    return re.sub(r"\s+", " ", value)


def lookup_pack_key(value: str) -> str:
    raw = unicodedata.normalize("NFKC", value).strip().casefold()
    if re.fullmatch(r"[\d.,]+", raw):
        return _bounded_key("raw:" + raw)
    return pack_key(raw)

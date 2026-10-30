"""Canonical business calendar dates; never guess a year or date convention."""

import re
from datetime import date


def canonical_day(value: str) -> str:
    value = value.strip()
    if not re.fullmatch(r"(?:\d{4}-\d{2}-\d{2}|\d{8})", value):
        raise ValueError("Date must be a real calendar day in YYYY-MM-DD format")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise ValueError("Date must be a real calendar day in YYYY-MM-DD format") from None

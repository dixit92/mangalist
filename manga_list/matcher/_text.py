"""Unicode helpers that mirror the .NET semantics the reference matcher relies on.

The C# matcher (MangaPixer 1.26.0) uses ``char.IsLetter`` / ``char.IsLetterOrDigit`` /
``char.IsDigit``, invariant lower-casing and ``StringComparer.OrdinalIgnoreCase``. Python's
``str.isalpha`` / ``str.isalnum`` / ``str.lower`` differ in small ways (``isalnum`` also accepts
non-decimal numbers such as "½", ``lower`` applies the context-sensitive final sigma), so the
port goes through these helpers instead.
"""

from __future__ import annotations

import unicodedata
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable, List


def nfkc(s: str) -> str:
    return unicodedata.normalize("NFKC", s)


def is_letter(ch: str) -> bool:
    """``char.IsLetter``: any Unicode letter category (Lu, Ll, Lt, Lm, Lo)."""
    return unicodedata.category(ch)[0] == "L"


def is_digit(ch: str) -> bool:
    """``char.IsDigit``: a decimal digit (Nd)."""
    return unicodedata.category(ch) == "Nd"


def is_letter_or_digit(ch: str) -> bool:
    """``char.IsLetterOrDigit``: a letter or a decimal digit (not No / Nl)."""
    cat = unicodedata.category(ch)
    return cat[0] == "L" or cat == "Nd"


def any_letter(s: str) -> bool:
    return any(is_letter(c) for c in s)


def any_letter_or_digit(s: str) -> bool:
    return any(is_letter_or_digit(c) for c in s)


def is_null_or_whitespace(s: str | None) -> bool:
    return s is None or not s.strip()


def lower_invariant(s: str) -> str:
    """``ToLowerInvariant``: a simple one-to-one mapping per character (no final sigma)."""
    out = []
    for c in s:
        low = c.lower()
        out.append(low[0] if low else c)
    return "".join(out)


def _upper_simple(c: str) -> str:
    up = c.upper()
    return up if len(up) == 1 else c


def eq_ignore_case(a: str, b: str) -> bool:
    """``StringComparer.OrdinalIgnoreCase`` equality (simple upper-case mapping per character)."""
    if len(a) != len(b):
        return False
    return all(x == y or _upper_simple(x) == _upper_simple(y) for x, y in zip(a, b))


def contains_ignore_case(items: Iterable[str], value: str) -> bool:
    return any(eq_ignore_case(x, value) for x in items)


def distinct_ignore_case(items: Iterable[str]) -> List[str]:
    """``Distinct(StringComparer.OrdinalIgnoreCase)``: first spelling wins, order kept."""
    result: List[str] = []
    for x in items:
        if not contains_ignore_case(result, x):
            result.append(x)
    return result


def split_nonempty(s: str) -> List[str]:
    """``s.Split(' ', StringSplitOptions.RemoveEmptyEntries)`` (space only, not all whitespace)."""
    return [p for p in s.split(" ") if p]


def format_fixed(x: float, decimals: int) -> str:
    """.NET custom numeric format ``0.00`` / ``0.000``: 15 significant digits, then half away from zero."""
    d = Decimal(format(x, ".15g")).quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)
    return f"{d:.{decimals}f}"

"""Small helpers shared by the parser layers."""

from __future__ import annotations

import re
from typing import Optional, Tuple

ARCHIVE_EXTENSIONS = frozenset({
    ".cbz", ".cbr", ".cb7", ".cbt", ".zip", ".rar", ".7z", ".tar", ".pdf", ".epub",
})

# A unit number: whole digits plus any number of decimals, kept as text (Decimal later).
UNIT = r"\d+(?:\.\d+)?"


def split_extension(name: str) -> Tuple[str, str]:
    """``("0012 [Ch. 12.5]", ".cbz")`` for ``"0012 [Ch. 12.5].cbz"``. Only archive extensions are split
    off, so a name without one keeps its decimals (``"Ch. 12.5"`` stays whole)."""
    stem, dot, ext = name.rpartition(".")
    if dot and stem and f".{ext}".lower() in ARCHIVE_EXTENSIONS:
        return stem, f".{ext}"
    return name, ""


def trailing_bracket(text: str) -> Optional[Tuple[str, str]]:
    """Split a trailing balanced ``[...]`` off ``text``: ``("Title", "Team [X]")`` for
    ``"Title [Team [X]]"``; None when ``text`` does not end in a balanced bracket."""
    s = text.rstrip()
    if not s.endswith("]"):
        return None
    depth = 0
    for i in range(len(s) - 1, -1, -1):
        ch = s[i]
        if ch == "]":
            depth += 1
        elif ch == "[":
            depth -= 1
            if depth == 0:
                return s[:i].rstrip(), s[i + 1:-1]
    return None


def encloses_whole(body: str) -> bool:
    """``body`` (the text between an opening ``[`` and the final ``]``) never closes that bracket early:
    ``"Ch. 1 - T [G]"`` yes, ``"Ch. 1] [G"`` no."""
    depth = 0
    for ch in body:
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


_LEADING_SEPARATOR = re.compile(r"^\s*(?:-|\u2013|\u2014|:|\uff1a|_)?\s*")


def clean_title(text: str) -> Optional[str]:
    """A chapter title without its leading separator (`` - ``, ``:``, ``_``); None when empty."""
    t = _LEADING_SEPARATOR.sub("", text, count=1).strip()
    return t or None


EXTRA_WORDS = re.compile(
    r"(?<![A-Za-z])(?:extras?|omake|specials?|side[\s_-]*stor(?:y|ies)|bonus|afterword)(?![A-Za-z])",
    re.IGNORECASE)

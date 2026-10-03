"""Windows-safe file and folder names (the library is shared with Windows over SMB, so every name
MangaList writes must be valid there, whatever the instance's own OS allows)."""

from __future__ import annotations

import re
from typing import Optional

MAX_NAME = 255
ILLEGAL_CHARS = '<>:"/\\|?*'
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL",
             *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10)),
             "COM¹", "COM²", "COM³", "LPT¹", "LPT²", "LPT³"}


def windows_name_problem(name: str) -> Optional[str]:
    """Why *name* (one path component) is not a valid Windows name, or None when it is."""
    if not name:
        return "empty name"
    if name in (".", ".."):
        return f"{name!r} is not a name"
    bad = sorted(set(_ILLEGAL.findall(name)))
    if bad:
        shown = ", ".join(repr(c) if c.isprintable() else f"\\x{ord(c):02x}" for c in bad)
        return f"characters not allowed on Windows: {shown}"
    if name[-1] in ". ":
        return "ends with a dot or a space"
    base = name.split(".", 1)[0].rstrip(" ").upper()
    if base in _RESERVED:
        return f"{base} is a reserved name on Windows"
    if len(name) > MAX_NAME:
        return f"longer than {MAX_NAME} characters"
    return None


def windows_safe_name(name: str, replacement: str = "_") -> str:
    """*name* made valid on Windows: forbidden characters replaced, trailing dots / spaces dropped, a
    reserved base name suffixed with ``_``, cut to 255 characters (keeping the extension)."""
    if _ILLEGAL.search(replacement) or replacement.endswith((".", " ")):
        raise ValueError("the replacement must itself be Windows-safe")
    out = _ILLEGAL.sub(replacement, name or "")
    out = out.rstrip(". ")
    if not out:
        out = replacement or "_"
    base, dot, rest = out.partition(".")
    if base.rstrip(" ").upper() in _RESERVED:
        out = f"{base}_{dot}{rest}"
    if len(out) > MAX_NAME:
        stem, dot, ext = out.rpartition(".")
        if dot and 0 < len(ext) <= 10:
            out = stem[:MAX_NAME - len(ext) - 1].rstrip(". ") + "." + ext
        else:
            out = out[:MAX_NAME].rstrip(". ")
    return out

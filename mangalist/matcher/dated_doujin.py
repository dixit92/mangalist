"""The dated doujin naming of some downloaders (port of MangaPixer 1.34.2 ``DatedDoujinName.cs``):
``[Creator] [yyyy-mm] Character (Tag) (Title)``, the creator tag often without its opening bracket
(``Creator] [yyyy-mm] ...``, see :func:`normalizer.split_unmatched_bracket_tags`).

Gated on a bracketed date - ``[yyyy]``, ``[yyyy-mm]`` or ``[yyyy-mm-dd]`` - RIGHT AFTER the creator tag; the common
convention ``[Creator] Title (Parody)`` has no such date and is read by :mod:`anatomy` as before. After the date, the
LAST parenthesized group that is not a release tag (:func:`anatomy.is_release_tag`: ``(x1600)``, ``(Decensored)``,
...) is the title, with any text after it (``(Title) - Part 2``); every other such group is a tag; the text outside
the groups is the character the work is about. A name with no such group has no title: it is read as before. Square
groups after the date are tags. Pure; sees a display name only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

import regex

from ._text import any_letter, is_null_or_whitespace, nfkc

# An optional (event), the creator tag with or without its "[", then the date group.
_DATED_LEAD = regex.compile(
    r"^\s*(?:\([^()\[\]]{1,80}\)\s*)?\[?(?P<creator>[^\[\]]{1,160}?)\s*\]\s*"
    r"\[(?P<date>(?:19|20)\d{2}(?:-(?:0[1-9]|1[0-2])(?:-(?:0[1-9]|[12]\d|3[01]))?)?)\]\s*(?P<rest>.*)$")
_SQUARE_GROUP = regex.compile(r"\[[^\[\]]*\]")
_TAIL_SEPARATOR = regex.compile(r"^\s*[-–—~:]")
_STRAY_BRACKET = regex.compile(r"[\[\](){}]")
_WHITESPACE = regex.compile(r"\s+")
_FLATTEN_TRIM = " -_,;|/+=~"


@dataclass(frozen=True)
class DatedDoujinName:
    creator: str                    # the creator tag as written (circle, artist or both), without its brackets
    title: Optional[str]            # the last title group plus any text after it, or None
    character: Optional[str]        # the text outside every group (who the work is about), or None
    tags: Tuple[str, ...]           # the other parenthesized groups after the date, in name order


def try_parse(display_name: Optional[str]) -> Optional[DatedDoujinName]:
    """Parse a display name (an archive file name with or without its extension), or None when it does not have the
    dated shape. Never raises."""
    if is_null_or_whitespace(display_name):
        return None
    from .normalizer import _strip_archive_extension  # the normalizer imports this module

    name = _strip_archive_extension(nfkc(display_name).strip())
    m = _DATED_LEAD.search(name)
    if m is None:
        return None
    creator = m.group("creator").strip().lstrip("[").strip()
    if not any_letter(creator):
        return None

    rest = m.group("rest")
    groups = _top_level_paren_groups(rest)
    titled = [g for g in groups if _is_title_text(g[2])]

    title = None
    if titled:
        last = titled[-1]
        # Text after the title group belongs to it ("(Title) - Part 2"); release tags and square tags after it do
        # not. A separator in front of it is kept, so the subtitle split still finds the title alone.
        after = rest[last[1] + 1:]
        tail = _outside_groups(after)
        join = " - " if _TAIL_SEPARATOR.search(after) else " "
        t = _flatten(last[2])
        title = (t + join + tail if tail else t) if t else None

    outside = _outside_groups(rest[:titled[-1][0]] if titled else rest)
    tags = tuple(t for t in (_flatten(g[2]) for g in titled[:-1]) if t)
    return DatedDoujinName(creator, title, outside if any_letter(outside) else None, tags)


def _is_title_text(inner: str) -> bool:
    """A group that can be a title or a tag: has letters and is no release tag."""
    from .anatomy import is_release_tag  # anatomy imports the normalizer, which imports this module

    return any_letter(inner) and not is_release_tag(inner)


def _top_level_paren_groups(s: str) -> List[Tuple[int, int, str]]:
    """Top-level ``(...)`` groups of a text (a nested group stays inside its parent), with their bounds."""
    groups: List[Tuple[int, int, str]] = []
    depth = 0
    start = -1
    for i, ch in enumerate(s):
        if ch == "(":
            if depth == 0:
                start = i
            depth += 1
        elif ch == ")" and depth > 0:
            depth -= 1
            if depth == 0:
                groups.append((start, i, s[start + 1:i].strip()))
    return groups


def _outside_groups(s: str) -> str:
    """The text outside every group: square and top-level ``(...)`` groups removed, then flattened."""
    out = []
    depth = 0
    for ch in _SQUARE_GROUP.sub(" ", s):
        if ch == "(":
            depth += 1
        elif ch == ")" and depth > 0:
            depth -= 1
        elif depth == 0:
            out.append(ch)
    return _flatten("".join(out))


def _flatten(s: str) -> str:
    """A group's text as words: square groups removed, other brackets (a nested ``(Part 2)``) kept as their words."""
    s = _SQUARE_GROUP.sub(" ", s)
    s = _STRAY_BRACKET.sub(" ", s)
    return _WHITESPACE.sub(" ", s).strip().strip(_FLATTEN_TRIM).strip()

"""Doujin naming anatomy of an archive name (port of ``ArchiveNameAnatomy.cs``).

``(event) [circle (artist)] title (parody) [language] ...``. Every part is optional except the
title. Pure; sees a display name only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

import regex

from ._text import any_letter, contains_ignore_case, is_null_or_whitespace, nfkc
from .normalizer import normalize

_I = regex.IGNORECASE

_LEADING_GROUPS = regex.compile(
    r"^\s*(?:\((?P<event>[^()\[\]]{1,80})\)\s*)?\[(?P<tag>[^\[\]]{1,160})\]\s*(?P<rest>.*)$")
_CIRCLE_ARTIST = regex.compile(r"^(?P<circle>.+?)\s*\((?P<artist>[^()]+)\)\s*$")
_PAREN_GROUP = regex.compile(r"\(([^()]{1,120})\)")

# Parenthesized release tags that are never a parody: years, languages, quality, edition and unit
# markers, scan notes.
_RELEASE_TAG = regex.compile(
    r"^(?:(?:19|20)\d{2}.*|\d+(?:\s*-\s*\d+)?|digital|english|eng|en|japanese|jp|raw|decensored|uncensored"
    r"|censored|colou?r(?:ed|ized)?|full\s*colou?r|hd|hq|lq|web|webrip|scan(?:ned)?|translated|complete"
    r"|ongoing|one-?shot|x\d+|\d{3,4}p|v\d+|(?:ch|vol)\.?\s*\d+.*)$", _I)

_UNIT_TOKEN = regex.compile(
    r"(?<![\p{L}\p{N}])(?:v|vol|vols|volume|volumes|ch|chap|chapter|chapters|c)\.?\s*\d+", _I)

_NAME_SEPARATOR = regex.compile(r"\s*(?:,|&|、|/)\s*")


@dataclass(frozen=True)
class ArchiveNameAnatomy:
    event: Optional[str]        # the leading (event) group (a convention name)
    leading_tag: Optional[str]  # the leading [...] group as written
    circle: Optional[str]       # the circle of a [circle (artist)] tag
    artist: Optional[str]       # the artist of a [circle (artist)] tag
    title: str                  # the clean title (normalize(...).primary)
    parody: Optional[str]       # the first trailing (...) group that is not a year or a release tag
    has_unit_token: bool        # the name carries a volume / chapter token

    @property
    def is_doujin_shaped(self) -> bool:
        """An (event) prefix, a [circle (artist)] tag, or a leading tag plus a parody group on a name
        without volume / chapter tokens."""
        return (self.event is not None
                or (self.circle is not None and self.artist is not None)
                or (self.leading_tag is not None and self.parody is not None and not self.has_unit_token))

    @property
    def creator_tags(self) -> Tuple[str, ...]:
        """Creator names the leading tag carries: circle and artist of [circle (artist)], or the whole
        tag otherwise. Multi-name tags are split on , & / (and the ideographic comma)."""
        names: List[str] = []
        for part in (self.circle, self.artist, self.leading_tag if self.circle is None else None):
            if is_null_or_whitespace(part):
                continue
            for n in _NAME_SEPARATOR.split(part):
                t = n.strip()
                if t and not contains_ignore_case(names, t):
                    names.append(t)
        return tuple(names)


def parse(archive_name: Optional[str]) -> ArchiveNameAnatomy:
    """Parse one archive display name. Never raises; an empty name yields an empty title."""
    title = normalize(archive_name).primary
    if is_null_or_whitespace(archive_name):
        return ArchiveNameAnatomy(None, None, None, None, title, None, False)

    name = nfkc(archive_name).strip()
    has_unit = _UNIT_TOKEN.search(name) is not None

    evt = tag = circle = artist = parody = None
    rest = name
    lead = _LEADING_GROUPS.search(name)
    if lead:
        evt = lead.group("event").strip() if lead.group("event") is not None else None
        tag = lead.group("tag").strip()
        rest = lead.group("rest")
        ca = _CIRCLE_ARTIST.search(tag)
        if ca:
            circle = ca.group("circle").strip()
            artist = ca.group("artist").strip()
        if not tag:
            tag = None

    # The parody is the first non-tag paren group AFTER some title text.
    for m in _PAREN_GROUP.finditer(rest):
        if m.start() == 0 or is_null_or_whitespace(rest[:m.start()].replace("[", " ").replace("]", " ")):
            continue
        inner = m.group(1).strip()
        if not inner or _RELEASE_TAG.search(inner) or not any_letter(inner):
            continue
        parody = inner
        break

    return ArchiveNameAnatomy(evt, tag, circle, artist, title, parody, has_unit)

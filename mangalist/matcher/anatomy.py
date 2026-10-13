"""Doujin naming anatomy of an archive name (port of ``ArchiveNameAnatomy.cs``).

``(event) [circle (artist)] title (parody) [language] ...``. Every part is optional except the
title. Pure; sees a display name only.

1.34.2: a dated doujin name (:mod:`dated_doujin`, ``Creator] [yyyy-mm] Character (Tag) (Title)``) is ``is_dated``: its
creator tag counts also without the opening bracket, its title is the last group, and it has no parody - its groups are
tags and the title, never the series it is about.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

import regex

from . import dated_doujin
from ._text import any_letter, contains_ignore_case, is_null_or_whitespace, nfkc
from .normalizer import normalize

_I = regex.IGNORECASE

_LEADING_GROUPS = regex.compile(
    r"^\s*(?:\((?P<event>[^()\[\]]{1,80})\)\s*)?\[(?P<tag>[^\[\]]{1,160})\]\s*(?P<rest>.*)$")
_CIRCLE_ARTIST = regex.compile(r"^(?P<circle>.+?)\s*\((?P<artist>[^()]+)\)\s*$")
_PAREN_GROUP = regex.compile(r"\(([^()]{1,120})\)")

# Parenthesized release tags that are never a parody: years, languages, quality, edition and unit
# markers, scan notes; 1.32.0 comics scan tags: (c2c) cover-to-cover, (Zone-Empire) / (<Group>-Empire)
# scanners, (Webrip), and collected-format words (TPB, HC, GN, OGN). 1.42.0 (MangaList parity): placeholder groups -
# "no group" (MangaDex's word for a chapter without one), "no scanlator", "unknown", "N/A", "none", "unofficial" (a
# site's label where others name the group) - name nobody.
_RELEASE_TAG = regex.compile(
    r"^(?:(?:19|20)\d{2}.*|\d+(?:\s*-\s*\d+)?|c2c|[\p{L}\p{N} ]{1,40}-empire|tpb|hc|gn|ogn"
    r"|digital|english|eng|en|japanese|jp|raw|decensored|uncensored|censored|colou?r(?:ed|ized)?|full\s*colou?r"
    r"|hd|hq|lq|web|webrip|scan(?:ned)?|translated|complete"
    r"|ongoing|one-?shot|x\d+|\d{3,4}p|v\d+|(?:ch|vol)\.?\s*\d+.*"
    r"|no[\s_-]*groups?|no[\s_-]*scanlat(?:or|ion)(?:\s+group)?|unknown(?:\s+group)?|n/?a|none|null|nil|tbd"
    r"|unofficial)$", _I)

def is_release_tag(text: Optional[str]) -> bool:
    """A parenthesized release tag (year, language, quality, edition or unit marker, scan note)."""
    return not is_null_or_whitespace(text) and _RELEASE_TAG.search(text.strip()) is not None


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
    is_dated: bool = False      # the name has the dated doujin shape (1.34.2)

    @property
    def is_doujin_shaped(self) -> bool:
        """An (event) prefix, a [circle (artist)] tag, or a leading tag plus a parody group on a name
        without volume / chapter tokens, or (1.34.2) a dated doujin name without volume / chapter tokens."""
        return (self.event is not None
                or (self.circle is not None and self.artist is not None)
                or (self.leading_tag is not None and self.parody is not None and not self.has_unit_token)
                or (self.is_dated and not self.has_unit_token))

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

    dated = dated_doujin.try_parse(name)
    if dated is not None and tag is None:
        # "Creator] [yyyy-mm] ...": the tag lost its opening bracket.
        tag = dated.creator
        ca = _CIRCLE_ARTIST.search(tag)
        if ca:
            circle = ca.group("circle").strip()
            artist = ca.group("artist").strip()

    # The parody is the first non-tag paren group AFTER some title text (a dated name has none).
    for m in (_PAREN_GROUP.finditer(rest) if dated is None else ()):
        if m.start() == 0 or is_null_or_whitespace(rest[:m.start()].replace("[", " ").replace("]", " ")):
            continue
        inner = m.group(1).strip()
        if not inner or _RELEASE_TAG.search(inner) or not any_letter(inner):
            continue
        parody = inner
        break

    return ArchiveNameAnatomy(evt, tag, circle, artist, title, parody, has_unit, is_dated=dated is not None)

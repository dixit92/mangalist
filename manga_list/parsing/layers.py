"""The five parser layers. Each takes one archive file name and returns a :class:`ParsedName`, or None
when the name is not its shape (the next layer then tries). Pure functions, no Qt, no I/O."""

from __future__ import annotations

import re
from decimal import Decimal
from typing import List, Optional, Tuple, Union

from ..classifier import detect_tokens
from ..models import _RE_CH_NUM, _RE_VOL_NUM
from ._common import EXTRA_WORDS, UNIT, clean_title, encloses_whole, split_extension, trailing_bracket
from .model import Kind, Layer, ParsedName, UnitRange
from .template import Template, compile_template


# --- 1. the root's own scheme, inverted -----------------------------------------------------------

def parse_scheme(name: str, scheme: Union[str, Template]) -> Optional[ParsedName]:
    """Layer 1: a name the scheme produced, read back exactly; None when it does not fit (or the scheme
    cannot be inverted, e.g. ``%O``)."""
    tpl = compile_template(scheme)
    if not tpl.invertible:
        return None
    stem, _ = split_extension(name)
    f = tpl.parse(stem)
    if f is None:
        return None
    chapter = None
    if "chapter_whole" in f:
        chapter = UnitRange.of(f["chapter_whole"] + f.get("chapter_fraction", ""))
    volume = UnitRange.maybe(f.get("volume"))
    kind = Kind.CHAPTER if chapter is not None else Kind.VOLUME if volume is not None else Kind.UNKNOWN
    series = next((f[k] for k in ("series", "series_english", "series_romaji", "series_mu") if k in f), None)
    return ParsedName(
        name=name, kind=kind, layer=Layer.SCHEME, volume=volume, chapter=chapter,
        title=f.get("title"), group=f.get("group"),
        index=int(f["index"]) if "index" in f else None, series=series,
        year=int(f["year"]) if "year" in f else None, edition=f.get("edition"), fix=f.get("fix"),
        notes=(f"scheme {tpl.source!r}",))


# --- 2. FMD2 exact ------------------------------------------------------------------------------

# "NNNN [ ... ]" or "Title - NNNN [ ... ]": the leading number is FMD2's numbering index.
_FMD2 = re.compile(r"^(?:(?P<series>.+?) - )?(?P<index>\d+) \[(?P<body>.*)\]$", re.DOTALL)

# The bracket head: "Vol. V", "Ch. C", "Vol. V Ch. C" (any decimals; a range without spaces: "Ch. 10-12").
_END = r"(?=$|[\s\-–—:：_\[\]])"
_HEAD = re.compile(
    rf"^\s*(?:vol(?:ume)?\.?\s*(?P<v>{UNIT})(?:-(?P<v2>{UNIT}))?{_END})?"
    rf"\s*(?:ch(?:apter)?\.?\s*(?P<c>{UNIT})(?:-(?P<c2>{UNIT}))?{_END})?",
    re.IGNORECASE)

# "Ch. Extra", "Chapter Special": a chapter word followed by an extra word, no number.
_HEAD_EXTRA = re.compile(r"^\s*(?:vol(?:ume)?\.?\s*\d+\s*)?ch(?:apter)?\.?\s*(?=[A-Za-z])", re.IGNORECASE)

# A body without any unit is accepted as FMD2 only for "NNNN [ ... ]" with an index of 3+ digits.
_MIN_INDEX_DIGITS_UNITLESS = 3


def parse_fmd2(name: str) -> Optional[ParsedName]:
    """Layer 2: FMD2's ``%NUMBERING% [%CHAPTER%]`` names. Units come ONLY from the bracket head; the
    chapter title after `` - `` and the group in the last ``[...]`` are never read for numbers."""
    stem, _ = split_extension(name)
    m = _FMD2.match(stem)
    if m is None or not encloses_whole(m.group("body")):
        return None
    body = m.group("body")
    index = int(m.group("index"))
    series = m.group("series")

    head = _HEAD.match(body)
    volume = UnitRange.maybe(head.group("v"), head.group("v2"))
    chapter = UnitRange.maybe(head.group("c"), head.group("c2"))
    if volume is not None or chapter is not None:
        rest, note = body[head.end():], "units from the bracket head"
    elif series is None and len(m.group("index")) >= _MIN_INDEX_DIGITS_UNITLESS:
        rest, note = body, "bracket head has no Vol. / Ch. number"
    else:
        return None

    group = None
    split = trailing_bracket(rest)
    if split is not None:
        rest, group = split[0], split[1].strip() or None
    title = clean_title(rest)
    if volume is None and chapter is None and title is None and group is None:
        return None
    # An extra is a chapter without its own number: "Ch. Extra", or a unit-less "Omake" / "Side Story".
    is_extra = chapter is None and (
        _HEAD_EXTRA.match(body) is not None
        or (volume is None and title is not None and EXTRA_WORDS.search(title) is not None))
    kind = Kind.VOLUME if chapter is None and volume is not None and not is_extra else Kind.CHAPTER
    return ParsedName(
        name=name, kind=kind, layer=Layer.FMD2, volume=volume, chapter=chapter, is_extra=is_extra,
        title=title, group=group, index=index, series=series.strip() if series else None, notes=(note,))


# --- 3. release names ---------------------------------------------------------------------------

_VP = r"(?:v|vol\.?\s*|volume\s+)"
_CP = r"(?:c|ch\.?\s*|chapter\s+)"
_RELEASE = re.compile(
    rf"""^(?P<series>.+?)\s+
    (?:
        (?P<vp>{_VP})(?P<v>{UNIT})(?:-{_VP}?(?P<v2>{UNIT}))?
      | (?P<cp>{_CP})(?P<c>{UNIT})(?:-{_CP}?(?P<c2>{UNIT}))?
      | (?P<n>{UNIT})(?:-(?P<n2>{UNIT}))?
    )
    (?:\s*(?P<po>\()?\+\s*{_CP}?(?P<p>{UNIT})(?:-{_CP}?(?P<p2>{UNIT}))?\s*(?(po)\)))?
    (?:\s+-\s+(?P<sub>[^()\[\]]*?[^\s()\[\]]))?
    (?P<tags>(?:\s*(?:\([^()]*\)|\[[^\[\]]*\]))*)
    \s*$""",
    re.IGNORECASE | re.VERBOSE)

_TAG = re.compile(r"\(([^()]*)\)|\[([^\[\]]*)\]")
_YEAR = re.compile(r"^((?:19|20)\d{2})(?:\s*-\s*(?:19|20)?\d{2})?$")
_EDITION = re.compile(r"^digital(?:\b.*)?$", re.IGNORECASE)
_FIX = re.compile(r"^f\d*$", re.IGNORECASE)
# The series part must not itself end in a unit token ("Title Vol. 1 Ch 3" is not a release name).
_SERIES_ENDS_IN_UNIT = re.compile(r"(?<![A-Za-z])(?:v|vol(?:ume)?\.?|ch(?:apter)?\.?|c)\s*\d+(?:\.\d+)?\s*$",
                                  re.IGNORECASE)


def parse_release(name: str) -> Optional[ParsedName]:
    """Layer 3: ``Title vNN(-MM) (+ chapters) (Year) (Digital|Digital-Compilation) (Group) (fN)`` and the
    older ``Title vol NN`` / ``Title Vol. NN`` / ``Title cNNN (...)`` / ``Title NNN (Year) (Digital)``
    forms. A bare number is accepted only with a year or edition tag; its kind stays unknown."""
    stem, _ = split_extension(name)
    m = _RELEASE.match(stem)
    if m is None:
        return None
    series = m.group("series").rstrip(" -_")
    if not series or _SERIES_ENDS_IN_UNIT.search(series):
        return None

    tags: List[str] = []
    year = edition = fix = None
    candidates: List[Tuple[int, str]] = []
    edition_pos = -1
    for i, t in enumerate(_TAG.finditer(m.group("tags"))):
        text = (t.group(1) if t.group(1) is not None else t.group(2)).strip()
        tags.append(text)
        if not text:
            continue
        y = _YEAR.match(text)
        if y and year is None:
            year = int(y.group(1))
        elif _EDITION.match(text) and edition is None:
            edition, edition_pos = text, i
        elif _FIX.match(text) and fix is None:
            fix = text
        else:
            candidates.append((i, text))
    after_edition = [c for c in candidates if c[0] > edition_pos] if edition_pos >= 0 else []
    group = (after_edition[0] if after_edition else candidates[-1])[1] if candidates else None

    volume = chapter = number = None
    plus = UnitRange.maybe(m.group("p"), m.group("p2"))
    if m.group("vp") is not None:
        volume = UnitRange.maybe(m.group("v"), m.group("v2"))
        chapter = plus
        kind = Kind.BOTH if plus is not None else Kind.VOLUME
    elif m.group("cp") is not None:
        if plus is not None:
            return None
        chapter = UnitRange.maybe(m.group("c"), m.group("c2"))
        kind = Kind.CHAPTER
    else:
        if plus is not None or (year is None and edition is None):
            return None
        number = UnitRange.maybe(m.group("n"), m.group("n2"))
        kind = Kind.UNKNOWN
    return ParsedName(
        name=name, kind=kind, layer=Layer.RELEASE, volume=volume, chapter=chapter, number=number,
        title=m.group("sub"), group=group, series=series, year=year, edition=edition, fix=fix,
        tags=tuple(tags), notes=("release name",))


# --- 4. generic fallback (today's classifier, unchanged) -------------------------------------------

def parse_generic(name: str, file_size: int = 0) -> Optional[ParsedName]:
    """Layer 4: today's parser. The kind comes from ``classifier.detect_tokens`` (chapter wins, as in
    ``FileHit.kind``) and the numbers from the same regexes ``models`` uses for the highest volume /
    chapter, so ``volume.end`` / ``chapter.end`` equal today's per-file maxima. None when today's parser
    sees no token."""
    has_vol, has_ch = detect_tokens(name, file_size=file_size)
    if not (has_vol or has_ch):
        return None
    stem = name.rsplit(".", 1)[0] if "." in name else name      # exactly as today
    volume = chapter = None
    if has_vol:
        vols = [Decimal(m.group(1)) for m in _RE_VOL_NUM.finditer(stem)]
        if vols:
            volume = UnitRange(min(vols), max(vols))
    if has_ch:
        lows: List[Decimal] = []
        highs: List[Decimal] = []
        for m in _RE_CH_NUM.finditer(stem):
            lo = Decimal(m.group(1))
            hi = Decimal(m.group(2)) if m.group(2) else lo
            lows.append(min(lo, hi))
            highs.append(max(lo, hi))
        if lows:
            chapter = UnitRange(min(lows), max(highs))
    kind = Kind.CHAPTER if has_ch else Kind.VOLUME
    return ParsedName(name=name, kind=kind, layer=Layer.GENERIC, volume=volume, chapter=chapter,
                      notes=(f"classifier tokens: volume={has_vol} chapter={has_ch}",))


# --- 5. bare numbers ----------------------------------------------------------------------------

_BARE = re.compile(
    rf"^(?:(?P<series>.*?\S)(?:\s+-\s+|[\s_]+#?|#))?(?P<n>{UNIT})(?:-(?P<n2>{UNIT}))?"
    r"(?P<tags>(?:\s*(?:\([^()]*\)|\[[^\[\]]*\]))*)\s*$")


def parse_bare(name: str, series_title: Optional[str] = None) -> Optional[ParsedName]:
    """Layer 5: ``01.cbz``, ``Title 01.cbz``, ``Title - 01.cbz``: a number whose kind the name does not
    state (``kind`` UNKNOWN, ``number`` set). With ``series_title``, a name that is just the series title
    (``86.cbz`` in the folder ``86``) has no number."""
    stem, _ = split_extension(name)
    text = stem.strip()
    if series_title and series_title.strip():
        st = series_title.strip()
        if text.casefold() == st.casefold():
            return None
        if text.casefold().startswith(st.casefold()):
            rest = text[len(st):]
            if rest[:1] in (" ", "_", "-", "#"):
                rm = _BARE.match(rest.lstrip(" _-#"))
                if rm is not None and rm.group("series") is None:
                    return _bare_result(name, rm, st)
    m = _BARE.match(text)
    if m is None:
        return None
    return _bare_result(name, m, m.group("series"))


def _bare_result(name: str, m: "re.Match[str]", series: Optional[str]) -> ParsedName:
    tags = tuple((t.group(1) if t.group(1) is not None else t.group(2)).strip()
                 for t in _TAG.finditer(m.group("tags")))
    return ParsedName(name=name, kind=Kind.UNKNOWN, layer=Layer.BARE,
                      number=UnitRange.of(m.group("n"), m.group("n2")),
                      series=series.strip() if series else None, tags=tags,
                      notes=("bare number: volumes or chapters?",))

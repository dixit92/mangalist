"""The five parser layers. Each takes one archive file name and returns a :class:`ParsedName`, or None
when the name is not its shape (the next layer then tries). Pure functions, no Qt, no I/O."""

from __future__ import annotations

import re
from dataclasses import replace
from decimal import Decimal
from typing import List, Optional, Tuple, Union

from ..classifier import detect_tokens
from ..models import _RE_CH_NUM, _RE_VOL_NUM
from ._common import (
    CHAPTER_LABELS, EXTRA_WORDS, UNIT, clean_title, encloses_whole, split_extension, trailing_bracket,
)
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
    if "chapter" in f:          # %CN: always two decimals ("0102.00", "0010.50") - the number, not its padding
        chapter = UnitRange.of(_scheme_decimal(f["chapter"]),
                               _scheme_decimal(f["chapter_end"]) if "chapter_end" in f else None)
    elif "chapter_whole" in f:
        chapter = UnitRange.of(f["chapter_whole"] + f.get("chapter_fraction", ""))
    volume = UnitRange.maybe(f.get("volume"), f.get("volume_end"))
    kind = Kind.CHAPTER if chapter is not None else Kind.VOLUME if volume is not None else Kind.UNKNOWN
    series = next((f[k] for k in ("series", "series_english", "series_romaji", "series_mu") if k in f), None)
    return ParsedName(
        name=name, kind=kind, layer=Layer.SCHEME, volume=volume, chapter=chapter,
        title=f.get("title"), group=f.get("group"),
        index=int(f["index"]) if "index" in f else None, series=series,
        year=int(f["year"]) if "year" in f else None, edition=f.get("edition"), fix=f.get("fix"),
        notes=(f"scheme {tpl.source!r}",))


def _scheme_decimal(text: str) -> str:
    """``"0102.00"`` -> ``"102"``, ``"0010.50"`` -> ``"10.5"``: the scheme pads to two decimals, so the zeros it
    added are not part of the number (a chapter ``102.00`` is chapter 102, not a fraction)."""
    whole, _, frac = text.partition(".")
    frac = frac.rstrip("0")
    return f"{whole}.{frac}" if frac else whole


# --- 2. FMD2 exact ------------------------------------------------------------------------------

# "NNNN [ ... ]" or "Title - NNNN [ ... ]": the leading number is FMD2's numbering index.
_FMD2 = re.compile(r"^(?:(?P<series>.+?) - )?(?P<index>\d+) \[(?P<body>.*)\]$", re.DOTALL)

# The bracket head: "Vol. V", "Ch. C" (also "Chapter", "Chap", "Chp"), "Vol. V Ch. C" (any decimals; a range
# without spaces: "Ch. 10-12").
_END = r"(?=$|[\s\-\u2013\u2014:\uff1a_\[\]])"
_HEAD = re.compile(
    rf"^\s*(?:vol(?:ume)?\.?\s*(?P<v>{UNIT})(?:-(?P<v2>{UNIT}))?{_END})?"
    rf"\s*(?:ch(?:apter|ap|p)?\.?\s*(?P<c>{UNIT})(?:-(?P<c2>{UNIT}))?{_END})?",
    re.IGNORECASE)

# "Ch. Extra", "Chapter Special": a chapter word followed by an extra word, no number.
_HEAD_EXTRA = re.compile(r"^\s*(?:vol(?:ume)?\.?\s*\d+\s*)?ch(?:apter)?\.?\s*(?=[A-Za-z])", re.IGNORECASE)

# A labelled chapter after the head's volume (or alone): "Contact. 0001", "episode 0035", "report011.", "Ep #12".
_LABEL_END = r"(?=$|\.(?=\s|$)|[\s\-\u2013\u2014:\uff1a_\[\]])"
_HEAD_LABEL = re.compile(
    rf"\s*(?<![A-Za-z]){CHAPTER_LABELS}\.?\s*[#-]?\s*(?P<c>{UNIT})(?:-(?P<c2>{UNIT}))?{_LABEL_END}", re.IGNORECASE)
# A bracket that starts with a number: the chapter (owner's library, 2026-10-10: arc parts "NNNN [NNNN  <arc title>
# (N)]", "0011 [0011 report011. <title>]"). Accepted when the number is zero-padded ("0076") or clearly ends the head
# (end of the bracket, two spaces, " - ", ". ", ": ", " ["), so "3 Days Later" stays a title.
_HEAD_NUMBER = re.compile(
    rf"^\s*(?P<c>{UNIT})(?:-(?P<c2>{UNIT}))?(?P<after>$|\s{{2,}}|\s+[-\u2013\u2014]\s|[.:]\s|\s*\[|\s)")
_PADDED = re.compile(r"^0\d")

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
    rest_at, note = head.end(), "units from the bracket head"
    if chapter is None:
        labelled = _HEAD_LABEL.match(body, head.end()) if volume is not None else _HEAD_LABEL.match(body)
        if labelled is not None:
            chapter = UnitRange.maybe(labelled.group("c"), labelled.group("c2"))
            rest_at, note = labelled.end(), "a labelled chapter in the bracket head"
        elif volume is None:
            numbered = _head_number(body)
            if numbered is not None:
                chapter, rest_at, note = numbered[0], numbered[1], "the number the bracket starts with"
                same = _HEAD_LABEL.match(body, rest_at)      # "0011 report011. <title>": the label says it again
                if same is not None and UnitRange.maybe(same.group("c"), same.group("c2")) == chapter:
                    rest_at = same.end()
    if volume is not None or chapter is not None:
        rest = body[rest_at:]
        if rest.startswith("."):
            rest = rest[1:]
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


def _head_number(body: str) -> Optional[Tuple[UnitRange, int]]:
    """The chapter a bracket starting with a number names, and where the rest begins; None when the number reads
    as part of a title ("3 Days Later")."""
    m = _HEAD_NUMBER.match(body)
    if m is None:
        return None
    after = m.group("after")
    if after == " " and not _PADDED.match(m.group("c")):
        return None                                     # "1 Year Later": a title, not a chapter
    chapter = UnitRange.maybe(m.group("c"), m.group("c2"))
    return (chapter, m.end("c2") if m.group("c2") else m.end("c")) if chapter is not None else None


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

# The same shape with the volume token as the LAST unit before the tags (a greedy series): "<Series> - Part 5 -
# <Subtitle> v05 (2022) (Digital) (Grp)" is volume 5 - "Part 5" belongs to the series title (owner's library,
# 2026-10-10: ~56 such volumes were read as the bare number 5).
_RELEASE_VOLUME_LAST = re.compile(
    rf"""^(?P<series>.+)\s+
    (?P<vp>{_VP})(?P<v>{UNIT})(?:-{_VP}?(?P<v2>{UNIT}))?
    (?:\s*(?P<po>\()?\+\s*{_CP}?(?P<p>{UNIT})(?:-{_CP}?(?P<p2>{UNIT}))?\s*(?(po)\)))?
    (?:\s+-\s+(?P<sub>[^()\[\]]*?[^\s()\[\]]))?
    (?P<tags>(?:\s*(?:\([^()]*\)|\[[^\[\]]*\]))*)
    \s*$""",
    re.IGNORECASE | re.VERBOSE)
# A bare number right after a chapter label ("<Title> - Episode 35 (2023)") is the labelled layer's.
_SERIES_ENDS_IN_LABEL = re.compile(rf"(?<![A-Za-z]){CHAPTER_LABELS}\.?\s*[#-]?$", re.IGNORECASE)

_TAG = re.compile(r"\(([^()]*)\)|\[([^\[\]]*)\]")
_YEAR = re.compile(r"^((?:19|20)\d{2})(?:\s*-\s*(?:19|20)?\d{2})?$")
_EDITION = re.compile(r"^digital(?:\b.*)?$", re.IGNORECASE)
_FIX = re.compile(r"^f\d*$", re.IGNORECASE)
_VOL_TAG = re.compile(rf"^{_VP}(?P<v>{UNIT})$", re.IGNORECASE)    # "(v01)" after a chapter
# The series part must not itself end in a unit token ("Title Vol. 1 Ch 3" is not a release name).
_SERIES_ENDS_IN_UNIT = re.compile(r"(?<![A-Za-z])(?:v|vol(?:ume)?\.?|ch(?:apter)?\.?|c)\s*\d+(?:\.\d+)?\s*$",
                                  re.IGNORECASE)


def parse_release(name: str) -> Optional[ParsedName]:
    """Layer 3: ``Title vNN(-MM) (+ chapters) (Year) (Digital|Digital-Compilation) (Group) (fN)`` and the
    older ``Title vol NN`` / ``Title Vol. NN`` / ``Title cNNN (...)`` / ``Title NNN (Year) (Digital)``
    forms. A bare number is accepted only with a year or edition tag; its kind stays unknown. The volume is the
    LAST volume token before the tags, so "Part 5" in ``<Series> - Part 5 - <Subtitle> v05 (2022)`` stays in the
    series title."""
    stem, _ = split_extension(name)
    m = _RELEASE.match(stem)
    if m is None:
        return None
    if m.group("vp") is None and m.group("cp") is None:
        last = _RELEASE_VOLUME_LAST.match(stem)
        if last is not None and not _SERIES_ENDS_IN_UNIT.search(last.group("series").rstrip(" -_")):
            m = last
        elif _SERIES_ENDS_IN_LABEL.search(m.group("series")):
            return None
    series = m.group("series").rstrip(" -_")
    if not series or _SERIES_ENDS_IN_UNIT.search(series):
        return None
    group, year, edition, fix, tag_volume, tags = _read_tags(m.group("tags"))

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
        volume = tag_volume
        kind = Kind.CHAPTER
    else:
        if plus is not None or (year is None and edition is None):
            return None
        number = UnitRange.maybe(m.group("n"), m.group("n2"))
        kind = Kind.UNKNOWN
    return ParsedName(
        name=name, kind=kind, layer=Layer.RELEASE, volume=volume, chapter=chapter, number=number,
        title=m.group("sub"), group=group, series=series.rstrip(" .") or series, year=year, edition=edition, fix=fix,
        tags=tuple(tags), notes=("release name",))


def _read_tags(text: str):
    """``(group, year, edition, fix, volume tag, every tag)`` of a name's trailing ``(...)`` / ``[...]`` tags: the
    group is the first other tag after the edition, else the last other tag."""
    tags: List[str] = []
    year = edition = fix = tag_volume = None
    candidates: List[Tuple[int, str]] = []
    edition_pos = -1
    for i, t in enumerate(_TAG.finditer(text or "")):
        tag = (t.group(1) if t.group(1) is not None else t.group(2)).strip()
        tags.append(tag)
        if not tag:
            continue
        y = _YEAR.match(tag)
        if y and year is None:
            year = int(y.group(1))
        elif _EDITION.match(tag) and edition is None:
            edition, edition_pos = tag, i
        elif _FIX.match(tag) and fix is None:
            fix = tag
        elif _VOL_TAG.match(tag) and tag_volume is None:
            tag_volume = UnitRange.of(_VOL_TAG.match(tag).group("v"))
        else:
            candidates.append((i, tag))
    after_edition = [c for c in candidates if c[0] > edition_pos] if edition_pos >= 0 else []
    group = (after_edition[0] if after_edition else candidates[-1])[1] if candidates else None
    return group, year, edition, fix, tag_volume, tags


# --- 3b. labelled chapters ----------------------------------------------------------------------------

_LABELLED = re.compile(
    rf"(?<![A-Za-z0-9]){CHAPTER_LABELS}\.?[\s_]*[#-]?[\s_]*(?P<n>{UNIT})(?:-(?P<n2>{UNIT}))?(?![\d.]\d)", re.IGNORECASE)
_TITLE_SEPARATOR = re.compile(r"^(?:\s{2,}|\s*[-\u2013\u2014:\uff1a]\s+|\.\s+|\s*_\s*)")
_TRAILING_TAGS = re.compile(r"(?:\s*(?:\([^()]*\)|\[[^\[\]]*\]))*\s*$")
_VOLUME_BEFORE = re.compile(rf"(?<![A-Za-z])(?:vol(?:ume)?\.?|v)\s*(?P<v>{UNIT})\s*[-_]?\s*$", re.IGNORECASE)
_CLOSER = {"[": "]", "(": ")"}


def parse_labelled(name: str, series_title: Optional[str] = None) -> Optional[ParsedName]:
    """Layer 3b: a chapter written with a site's own label instead of "Ch." (owner's library, 2026-10-10: ~380 such
    files read as extras or unknown): ``<Title> - Episode 35``, ``<Title> Contact. 0001 - <title> [Group]``,
    ``<Title> [Pact 0015]``, ``<Title> Vol. 2 Lesson 5``. The label must be followed by its number and then by
    nothing, the name's tags, or a title after a separator (`` - ``, two spaces, ``. ``, ``: ``) - so "Level 99 Hero
    03" is not chapter 99. A name with a classifier chapter token ("Ch.", "c012") is left to the other layers."""
    stem, _ = split_extension(name)
    if detect_tokens(name)[1]:
        return None
    start = 0
    if series_title and series_title.strip() and stem.casefold().startswith(series_title.strip().casefold()):
        start = len(series_title.strip())
    for m in _LABELLED.finditer(stem, start):
        parsed = _labelled_at(name, stem, m)
        if parsed is not None:
            return parsed
    return None


def _labelled_at(name: str, stem: str, m: "re.Match[str]") -> Optional[ParsedName]:
    before = stem[:m.start()]
    after = stem[m.end():]
    opener = before.rstrip()[-1:]
    inner_title = None
    if opener in _CLOSER:                                     # "<Title> [Pact 0015 - <title>] (Grp)"
        close = after.find(_CLOSER[opener])
        if close < 0:
            return None
        inner, after = after[:close], after[close + 1:]
        if inner.strip():
            sep = _TITLE_SEPARATOR.match(inner)
            if sep is None:
                return None
            inner_title = clean_title(inner[sep.end():])
        before = before.rstrip()[:-1]
    tags_m = _TRAILING_TAGS.search(after)
    body = after[:tags_m.start()]
    title = inner_title
    if body.strip():
        sep = _TITLE_SEPARATOR.match(body)
        if sep is None or inner_title is not None:
            return None
        title = clean_title(body[sep.end():])
    group, year, edition, fix, tag_volume, tags = _read_tags(tags_m.group(0))
    volume = tag_volume
    vb = _VOLUME_BEFORE.search(before)
    if vb is not None:
        volume, before = UnitRange.of(vb.group("v")), before[:vb.start()]
    series = before.strip(" -_.\u2013\u2014") or None
    return ParsedName(
        name=name, kind=Kind.CHAPTER, layer=Layer.LABELLED, volume=volume,
        chapter=UnitRange.maybe(m.group("n"), m.group("n2")), title=title, group=group, series=series, year=year,
        edition=edition, fix=fix, tags=tuple(tags), notes=(f"labelled chapter {m.group(0).strip()!r}",))


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
    several = False
    if has_vol:
        vols = [Decimal(m.group(1)) for m in _RE_VOL_NUM.finditer(stem)]
        if vols:
            volume = UnitRange(min(vols), max(vols))
            several = len(set(vols)) > 1
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
            several = several or len(lows) > 1
    kind = Kind.CHAPTER if has_ch else Kind.VOLUME
    notes = (f"classifier tokens: volume={has_vol} chapter={has_ch}",)
    if several:
        notes += ("several volume or chapter numbers: the range spans them all",)
    return ParsedName(name=name, kind=kind, layer=Layer.GENERIC, volume=volume, chapter=chapter, ambiguous=several,
                      notes=notes)


# --- 5. bare numbers ----------------------------------------------------------------------------

_BARE = re.compile(
    rf"^(?:(?P<series>.*?\S)(?:\s+-\s+|[\s_]+#?|#))?(?P<n>{UNIT})(?:-(?P<n2>{UNIT}))?"
    r"(?P<tags>(?:\s*(?:\([^()]*\)|\[[^\[\]]*\]))*)\s*$")


# "<Title> 014.6  <chapter title>", "<Title> 07 - <chapter title>": a bare number with a chapter title after it.
# Only a zero-padded or fractional number, or one followed by two spaces, so "Some Series 2 - The Return" stays
# a series title.
_BARE_TITLED = re.compile(
    rf"^(?P<series>.*?\S)(?:\s+-\s+|[\s_]+#?|#)(?P<n>{UNIT})(?:-(?P<n2>{UNIT}))?"
    r"(?P<sep>\s{2,}|\s+[-\u2013\u2014:]\s+|\.\s+)(?P<title>[^\s(\[].*?)"
    r"(?P<tags>(?:\s*(?:\([^()]*\)|\[[^\[\]]*\]))*)\s*$")


def parse_bare(name: str, series_title: Optional[str] = None) -> Optional[ParsedName]:
    """Layer 5: ``01.cbz``, ``Title 01.cbz``, ``Title - 01.cbz``: a number whose kind the name does not
    state (``kind`` UNKNOWN, ``number`` set). With ``series_title``, a name that is just the series title
    (``42.cbz`` in the folder ``42``) has no number."""
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
        return _bare_titled(name, text)
    return _bare_result(name, m, m.group("series"))


def _bare_titled(name: str, text: str) -> Optional[ParsedName]:
    m = _BARE_TITLED.match(text)
    if m is None or _SERIES_ENDS_IN_UNIT.search(m.group("series")):
        return None
    n = m.group("n")
    padded = n.startswith("0") and len(n.partition(".")[0]) > 1
    if not (padded or "." in n or m.group("sep").isspace()):
        return None                                     # "Some Series 2 - The Return": a series title
    r = _bare_result(name, m, m.group("series"))
    return replace(r, title=clean_title(m.group("title")), group=_read_tags(m.group("tags"))[0],
                   notes=("bare number with a chapter title: volumes or chapters?",))


def _bare_result(name: str, m: "re.Match[str]", series: Optional[str]) -> ParsedName:
    tags = tuple((t.group(1) if t.group(1) is not None else t.group(2)).strip()
                 for t in _TAG.finditer(m.group("tags")))
    return ParsedName(name=name, kind=Kind.UNKNOWN, layer=Layer.BARE,
                      number=UnitRange.of(m.group("n"), m.group("n2")),
                      series=series.strip() if series else None, tags=tags,
                      notes=("bare number: volumes or chapters?",))

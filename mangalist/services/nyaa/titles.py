"""A nyaa release title -> :class:`ParsedTitle` (series text, volume range, Digital, group, pack, not-comic, ...).

Nyaa titles are free text, so this is a pipeline in front of the shared release parser
(:func:`mangalist.parsing.parse_release`, which stays untouched):

1. **Normalise** what the shared parser does not read: a leading ``[Group]``, ``_`` for spaces, an archive
   extension, ``Volumes`` / ``Vols``, ``Vol(1-10)``, ``Vol. 16 - Vol. 17``, ``+ Extras``, ``Title, Vol. 7``.
2. **parse_release** on the result: volumes, the ``(Digital)`` edition, the trailing group, the year.
3. **Fallback scan** (own, small) when the shared parser does not recognise the shape: the first ``v``/``Vol`` token
   gives the series and the volumes; no number at all gives a *pack* when the title says so (a year range such as
   ``(2013-2025)``, ``Complete``, ``Collection``) or a chapter release (dropped by the search).

Volume numbers are exact decimal strings (``'1'``, ``'12.5'``), as everywhere in MangaList. Whether a release is a
light novel / audiobook (``not_comic``) and whether it is an edition that re-numbers the volumes (Omnibus, Deluxe,
... - the numbers in the title are then not the series' volume numbers) are read from the whole title.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import List, Optional, Tuple

from ...parsing import parse_release
from ...parsing.model import Kind, plain, to_decimal
from ...parsing._common import split_extension

_I = re.IGNORECASE
_UNIT = r"\d+(?:\.\d+)?"

_LEADING_GROUP = re.compile(r"^\s*\[([^\[\]]+)\]\s*(?=\S)")
_BRACKETS = re.compile(r"\([^()]*\)|\[[^\[\]]*\]")
_TRAILING_TAGS = re.compile(r"((?:\s*(?:\([^()]*\)|\[[^\[\]]*\]))+)\s*$")
_TAG = re.compile(r"\(([^()]*)\)|\[([^\[\]]*)\]")

# Normalising spellings the shared parser does not read.
_VOLUMES_WORD = re.compile(r"\bvol(?:ume)?s\b\.?", _I)
_VOL_PAREN_RANGE = re.compile(rf"\bvol\.?\s*\(\s*({_UNIT})\s*-\s*({_UNIT})\s*\)", _I)
_VOL_DASH_VOL = re.compile(rf"(\bvol\.?\s*{_UNIT})\s*-\s*vol\.?\s*(?={_UNIT})", _I)
_VOL_SPACED_RANGE = re.compile(
    rf"(?<![A-Za-z])((?:v|vol(?:ume)?\.?\s*){_UNIT})\s+-\s+({_UNIT})(?![A-Za-z\d.])", _I)
_PLUS_EXTRAS = re.compile(r"\s*\+\s*extras?\b", _I)
_COMMA_BEFORE_VOL = re.compile(r",\s+(?=vol\b|v\d)", _I)

# The fallback scan: a volume token anywhere ("v05", "Vol. 3", "Vol 1-10", "v01-v12").
_VOL_SCAN = re.compile(
    rf"(?<![A-Za-z])(?:v(?=\d)|vol(?:ume)?\.?\s*)({_UNIT})(?:\s*-\s*(?:v|vol\.?\s*)?({_UNIT}))?(?![A-Za-z\d])", _I)
_CHAPTER_MARK = re.compile(rf"(?<![A-Za-z])(?:chapters?|ch\.?|c(?=\d))\s*{_UNIT}", _I)
_BARE_NUMBER_THEN_SQUARE_TAGS = re.compile(rf"\s{_UNIT}(?:\s*-\s*{_UNIT})?(?:\s*\[[^\[\]]*\])+\s*$")
_YEAR_RANGE = re.compile(r"\(\s*(?:19|20)\d{2}\s*-\s*(?:(?:19|20)\d{2}|\d{2})\s*\)")
_PACK_WORDS = re.compile(r"\b(?:complete|collection|batch|full\s+series|all\s+volumes)\b", _I)

# Whole-title facts.
_NOT_COMIC = re.compile(r"\blight[\s_-]*novels?\b|\bnovels?\b|\bepub\b|\baudio\s*books?\b|\bmp3\b|\bm4b\b|\bflac\b", _I)
_NOT_COMIC_LN = re.compile(r"\bLNs?\b")          # case-sensitive: 'ln' is a word elsewhere
_RENUMBERING_EDITION = re.compile(
    r"\bomnibus\b|\bdeluxe\b|\b(?:master|library|perfect|collector'?s?|ultimate|definitive|anniversary)\s+edition\b"
    r"|\bkanzenban\b|\bbunkoban\b|\b[23][\s-]*in[\s-]*1\b", _I)
_DIGITAL = re.compile(r"\bdigital\b", _I)

# A trailing tag that names no group.
_YEAR_TAG = re.compile(r"^(?:19|20)\d{2}(?:\s*-\s*(?:19|20)?\d{2})?$")
_NOISE_TAG = re.compile(
    r"^(?:digital(?:[\s+/-].*)?|scanlations?|epub|pdf|cbz|cbr|tagged|english|eng|raw|manga|official.*|translat\w*|light novel|"
    r"ln|v\d+|vol(?:ume)?s?\.?\s*\d[\d.\s-]*|f\d*|fixed\b.*|repack|\d+px.*|=.*|complete|ongoing|\+.*|.*\bedition|omnibus|batch)$", _I)

# Words that end a series text without being part of the title ("Overlord Manga", "Overlord LN").
_SERIES_NOISE = re.compile(
    r"(?:[\s,:_-]+(?:manga|comics?|ln|light\s+novels?|novels?|epub|pdf|english|eng|official|translation|translated|"
    r"complete(?:\s+series)?|collection))+\s*$", _I)


@dataclass(frozen=True)
class ParsedTitle:
    """What a release title says. ``series`` is the title as written, without tags and noise words; it is
    compared to the series' names by the search, never trusted."""

    series: str
    vol_from: Optional[str] = None
    vol_to: Optional[str] = None
    digital: bool = False
    group: Optional[str] = None
    is_pack: bool = False                # more than one volume, or a pack that says so without numbers
    not_comic: bool = False              # light novel / EPUB / audiobook
    chapters_only: bool = False          # chapter releases hold no volume
    renumbered: Optional[str] = None     # an edition whose volume numbers are not the series' ('Omnibus', ...)
    year_range: bool = False

    @property
    def has_volumes(self) -> bool:
        return self.vol_from is not None


def _range(first: Optional[str], last: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    a = to_decimal(first)
    if a is None:
        return None, None
    b = to_decimal(last) if last else None
    if b is None:
        b = a
    if b < a:
        a, b = b, a
    return plain(a), plain(b)


def _normalise(text: str) -> str:
    t = _VOL_PAREN_RANGE.sub(lambda m: f"Vol {m.group(1)}-{m.group(2)}", text)
    t = _VOLUMES_WORD.sub("Vol", t)
    t = _VOL_DASH_VOL.sub(lambda m: m.group(1) + "-", t)
    t = _VOL_SPACED_RANGE.sub(lambda m: f"{m.group(1)}-{m.group(2)}", t)
    t = _PLUS_EXTRAS.sub("", t)
    return _COMMA_BEFORE_VOL.sub(" ", t)


def _clean_series(text: str) -> str:
    s = _BRACKETS.sub(" ", text)
    s = re.sub(r"\s+", " ", s).strip(" \t-_,:([{")
    previous = None
    while previous != s:
        previous = s
        s = _SERIES_NOISE.sub("", s).strip(" \t-_,:([{")
    return s


def _group_of(tags: List[str], preferred: Optional[str]) -> Optional[str]:
    def usable(tag: str) -> bool:
        t = tag.strip()
        return bool(t) and not _YEAR_TAG.match(t) and not _NOISE_TAG.match(t)

    if preferred and usable(preferred):
        return preferred.strip()
    for tag in reversed(tags):
        if usable(tag):
            return tag.strip()
    return None


def _tags_of(text: str) -> List[str]:
    return [(m.group(1) if m.group(1) is not None else m.group(2)).strip() for m in _TAG.finditer(text)]


def parse_title(title: str) -> ParsedTitle:
    """Read one nyaa release title. Never raises."""
    text = unicodedata.normalize("NFKC", title or "").strip()
    text, _ = split_extension(text)
    if " " not in text:
        text = text.replace("_", " ")
    lead_group: Optional[str] = None
    m = _LEADING_GROUP.match(text)
    if m is not None:
        lead_group = m.group(1).strip()
        text = text[m.end():]
    text = text.strip()

    not_comic = bool(_NOT_COMIC.search(text) or _NOT_COMIC_LN.search(text))
    renum = _RENUMBERING_EDITION.search(text)
    renumbered = renum.group(0).strip().title() if renum else None
    digital = bool(_DIGITAL.search(text))
    year_range = bool(_YEAR_RANGE.search(text))

    norm = _normalise(text)
    rel = parse_release(norm)
    if rel is not None:
        units = rel.volume or rel.number
        vol_from, vol_to = _range(plain(units.start), plain(units.end)) if units is not None else (None, None)
        chapters_only = vol_from is None and rel.kind is Kind.CHAPTER
        group = _group_of(list(rel.tags), rel.group) or lead_group
        series = _clean_series(rel.series or "")
        is_pack = vol_from is not None and vol_from != vol_to
        return ParsedTitle(series=series, vol_from=vol_from, vol_to=vol_to,
                           digital=digital or bool(rel.edition), group=group, is_pack=is_pack,
                           not_comic=not_comic, chapters_only=chapters_only, renumbered=renumbered,
                           year_range=year_range)
    return _scan(norm, lead_group, digital, not_comic, renumbered, year_range)


def _scan(text: str, lead_group: Optional[str], digital: bool, not_comic: bool, renumbered: Optional[str],
          year_range: bool) -> ParsedTitle:
    tags: List[str] = []
    tail = _TRAILING_TAGS.search(text)
    if tail is not None:
        tags = _tags_of(tail.group(1))
    group = _group_of(tags, None) or lead_group

    vm = _VOL_SCAN.search(text)
    if vm is not None:
        vol_from, vol_to = _range(vm.group(1), vm.group(2))
        series = _clean_series(text[:vm.start()])
        return ParsedTitle(series=series, vol_from=vol_from, vol_to=vol_to, digital=digital, group=group,
                           is_pack=vol_from != vol_to, not_comic=not_comic, renumbered=renumbered,
                           year_range=year_range)

    chapter = _CHAPTER_MARK.search(text) or _BARE_NUMBER_THEN_SQUARE_TAGS.search(text)
    if chapter is not None:
        return ParsedTitle(series=_clean_series(text[:chapter.start()]), digital=digital, group=group,
                           not_comic=not_comic, chapters_only=True, renumbered=renumbered, year_range=year_range)

    pack_word = _PACK_WORDS.search(text)
    is_pack = year_range or pack_word is not None
    cut = [x.start() for x in (_YEAR_RANGE.search(text), pack_word, re.search(r"[(\[]", text)) if x is not None]
    head = text[:min(cut)] if cut else text
    return ParsedTitle(series=_clean_series(head), digital=digital, group=group, is_pack=is_pack,
                       not_comic=not_comic, renumbered=renumbered, year_range=year_range)


__all__ = ["ParsedTitle", "parse_title"]

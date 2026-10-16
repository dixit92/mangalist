"""MangaList's naming scheme: the file name a chapter or volume archive gets in a library (owner, 2026-10-10).

Settled in the Next Cycle Design, sections 10-12 (owner answers 2026-10-10); the renamer (lane B) and Suwayomi filing
(lane C) call these functions. Pure Python: no Qt, no I/O (only :func:`volume_lookup` reads the database).

**Chapters** - ``Ch. 0102.00 Vol. 012 (<chapter title>) [<group>].cbz``:

- the chapter number with 4 integer digits and ALWAYS two decimals (``0010.50``; owner: "Keeps the naming
  consistent"); more decimals only when the number has them (``0291.999``);
- `` Vol. 012`` (3 digits) only when the volume is known; `` (<title>)`` only when the chapter title is known;
  `` [<group>]`` only when the scanlation group is known;
- every chapter name starts ``Ch. `` so a plain alphabetical sort is the reading order, and a volume learned later only
  changes the tail of the name, never its place (owner: "Since C comes before V ...");
- a chapter range (rare: ``c010-012``) is ``Ch. 0010.00-0012.00``: both ends in the same form, so the range still sorts
  at its first chapter.

**Volumes** - ``Vol. 001 [<group>].cbz`` (3-digit volume, any decimals it has: ``Vol. 012.5``; a range
``Vol. 001-003``). No series title (owner, 2026-10-16: "the folder already encodes the romaji and alternate title and
it is the closest to the chapter scheme which already exists"; a title in every volume name made paths of ~330
characters, past Windows' 260). A season / part the file name states is kept in front, ``Season 2 - Vol. 001``,
because volume numbers restart with it (renamer dry run, 2026-10-10) - unless the series title already says it. Until
2026-10-16 volumes were ``<Series title> - Vol. 001 [<group>]`` (:data:`TITLED_VOLUME_SCHEME`): still read back, and
rendered only so the renamer can tell a rename the new format alone causes (:func:`titled_volume_name`). Extras the
volume holds besides its chapters are kept as `` + Extras`` (``Vol. 005 + Extras [<group>].cbz``; the marker word as
the name says it: Extras, Omake, Special(s), Side Story / Stories, Bonus, Afterword).

**A rename never drops what the name says and the scheme can say** (integrator, 2026-10-16): a volume's group and its
extras marker are read from the name even when the parser's layer does not keep them (``Vol. 5.5 [G]``,
``<Title> v01 + Extras (2020) (Digital) (G)``); the series title, the year, a ``(Digital...)`` edition, a ``(f)`` fix and a
``[v2]`` revision are dropped by design (section 11). A name holding anything else - another edition ``(Collector's
Edition)``, a volume title, a second group, a word the scheme has no place for - is left alone, with the reason
(:func:`unkept_reason`). Chapter names read only by the generic layer get the same check (their group is kept; any
other text leaves the file alone).

**Why parentheses for the title** (section 12): MangaPixer reads nothing inside ``( )`` / ``[ ]`` when the rest of the
name states a unit, so a title such as "The Vol 2 Begins" is never read as volume 2 there; MangaList reads its own
scheme back exactly (:data:`CHAPTER_SCHEME` / :data:`VOLUME_SCHEME`, the parser's layer 1 in every root), title and
group as free text.

**Sanitising**: in a chapter title ``( )`` become ``[ ]`` and in a group ``[ ]`` become ``( )`` (so each part's
brackets stay unambiguous); the characters Windows / SMB forbid are replaced (``:`` -> `` - `` before a space, else
``-``; ``/ \\ |`` -> ``-``; ``"`` -> ``'``; ``< >`` -> the part's own brackets; ``? *`` and control characters dropped);
whitespace is collapsed; no part starts or ends with a space and no name ends with a dot or space.

**Length rule** (owner: "overall chapter path should not exceed 255 chars or whatever the default max path is for
Windows/SMB share"): every file name is at most :attr:`NameLimits.max_name_bytes` (255) bytes of UTF-8, and - when a
Windows server name is set - the whole Windows path (``\\\\SERVER\\<share>\\...\\name``, from the container path
``/data/<share>/...``) is at most :attr:`NameLimits.max_windows_path` (259) characters (MAX_PATH 260 minus the
terminator). The group is always capped at :attr:`NameLimits.group_cap` characters (32; owner: "Some group names are
long, so have a cap"). When a name is still too long it is shortened in this order: the chapter title (cut, ending with
``…``, then left out), then the group (cut further, then left out); never the number, the volume or the extension. A
volume name shortens its group. If even the shortest form is too long it is returned anyway and
flagged (:attr:`NameResult.fits` False, logged as a warning).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable, Iterable, Optional, Tuple, Union

from .parsing._common import QUALIFIER, real_group, series_qualifier
from .parsing.model import OWNER_UNITS_NOTE, Kind, Layer, ParsedName, UnitRange, plain, to_decimal
from .parsing.template import (
    MANGALIST_CHAPTER_SCHEME, MANGALIST_TITLED_VOLUME_SCHEME, MANGALIST_VOLUME_SCHEME, compile_template,
)

_log = logging.getLogger(__name__)

CHAPTER_SCHEME = MANGALIST_CHAPTER_SCHEME
"""``Ch. %CN4{-%CE4}{ Vol. %V3}{ (%CT)}{ [%G]}`` - the chapter scheme as a :mod:`mangalist.parsing.template`."""
VOLUME_SCHEME = MANGALIST_VOLUME_SCHEME
"""``{%SQ - }Vol. %V3{-%VE3}{ + %EX}{ [%G]}`` - the volume scheme (no series title)."""
TITLED_VOLUME_SCHEME = MANGALIST_TITLED_VOLUME_SCHEME
"""``%T{ %SQ} - Vol. %V3{-%VE3}{ [%G]}`` - the volume scheme until 2026-10-16 (read back, never written)."""
DEFAULT_SCHEMES: Tuple[str, ...] = (CHAPTER_SCHEME, VOLUME_SCHEME, TITLED_VOLUME_SCHEME)
"""The scheme of every root whose own ``naming_scheme`` is empty (the scanner's scheme hook)."""

ELLIPSIS = "\u2026"

Number = Union[Decimal, str, int]


@dataclass(frozen=True)
class NameLimits:
    """The length rule (see the module docstring). ``windows_server``: the Windows server name the library is shared
    as (``"SERVER"`` or ``"\\\\SERVER"``); None = no Windows path check."""

    max_name_bytes: int = 255
    group_cap: int = 32
    windows_server: Optional[str] = None
    max_windows_path: int = 259


_DEFAULT_LIMITS = NameLimits()


@dataclass(frozen=True)
class NameResult:
    """A scheme name and how it was made: ``shortened`` names the parts that were cut or left out (``"title"``,
    ``"group"``, ``"series"``; a group capped at :attr:`NameLimits.group_cap` counts); ``fits`` is False when even
    the shortest legal form breaks the length rule (the name is returned anyway)."""

    name: str
    shortened: Tuple[str, ...] = ()
    fits: bool = True


# --- numbers ------------------------------------------------------------------------------------------------


def _number(value: Any, what: str) -> Decimal:
    """An exact, non-negative unit number; trailing zeros of the fraction dropped (``10.50`` -> ``10.5``)."""
    if isinstance(value, UnitRange):
        value = value.start
    d = to_decimal(value) if isinstance(value, (Decimal, str, int)) and not isinstance(value, bool) else None
    if d is None or d < 0:
        raise ValueError(f"not a {what} number: {value!r}")
    whole, _, frac = plain(d).partition(".")
    frac = frac.rstrip("0")
    return Decimal(f"{whole}.{frac}" if frac else whole)


def _end(start: Decimal, end: Any, what: str) -> Optional[Decimal]:
    """The end of a range, or None when there is no range (no end, or the end equals the start)."""
    if end is None or (isinstance(end, str) and not end.strip()):
        return None
    e = _number(end, what)
    if e < start:
        raise ValueError(f"the {what} range ends before it starts: {plain(start)}-{plain(e)}")
    return None if e == start else e


# --- sanitising ---------------------------------------------------------------------------------------------

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_COLON_SPACE = re.compile(r"\s*:\s+")
_SPACES = re.compile(r"\s+")
_COMMON = str.maketrans({":": "-", "/": "-", "\\": "-", "|": "-", '"': "'", "?": None, "*": None})
_TITLE_BRACKETS = str.maketrans({"(": "[", ")": "]", "<": "[", ">": "]"})
_GROUP_BRACKETS = str.maketrans({"[": "(", "]": ")", "<": "(", ">": ")"})
_PLAIN_BRACKETS = str.maketrans({"<": "(", ">": ")"})


def _clean(text: Optional[str], brackets: dict) -> Optional[str]:
    if text is None:
        return None
    t = _CONTROL.sub(" ", str(text))
    t = _COLON_SPACE.sub(" - ", t).translate(brackets).translate(_COMMON)
    t = _SPACES.sub(" ", t).strip()
    return t or None


def sanitize_title(text: Optional[str]) -> Optional[str]:
    """A chapter title as the scheme writes it: ``( )`` -> ``[ ]``, Windows-illegal characters replaced, whitespace
    collapsed (``"Part 1: Start (Again)?"`` -> ``"Part 1 - Start [Again]"``); None when nothing is left."""
    return _clean(text, _TITLE_BRACKETS)


def sanitize_group(text: Optional[str]) -> Optional[str]:
    """A group as the scheme writes it: ``[ ]`` -> ``( )`` (``"Team [X]"`` -> ``"Team (X)"``), the rest as titles. A
    placeholder (``no group``, ``Unknown``, ``N/A``, ... - MangaDex's "no group") is no group: None."""
    return real_group(_clean(real_group(text), _GROUP_BRACKETS))


_RESTATED = re.compile(r"^(?:(?:chapter|chap|ch|episode|ep|no|#)\.?\s*#?\s*)?(?P<a>\d+(?:\.\d+)?)"
                       r"(?:\s*-\s*(?P<b>\d+(?:\.\d+)?))?$", re.IGNORECASE)


def restates_number(title: Optional[str], chapter: Decimal, chapter_end: Optional[Decimal] = None) -> bool:
    """The title only says the chapter number again (``Chapter 144``, ``Ch. 0144``, ``Episode 144``, ``Ep. 144``,
    ``#144``, ``No. 144``, ``144`` for chapter 144; zero padding and trailing decimal zeros do not matter) - it is
    dropped from the name (renamer dry run, 2026-10-10: ``Ch. 0144.00 (Chapter 144)``)."""
    m = _RESTATED.match((title or "").strip())
    if m is None:
        return False
    a, b = to_decimal(m.group("a")), to_decimal(m.group("b")) if m.group("b") else None
    return a == chapter and (b == chapter_end if chapter_end is not None else b is None or b == chapter)


def sanitize_qualifier(text: Optional[str]) -> Optional[str]:
    """A volume's season / part of the series as the scheme writes it: ``"season 02"`` -> ``"Season 2"``,
    ``"Part 5"``; None when empty. Raises ValueError for anything else (the scheme reads back only these)."""
    if text is None or not str(text).strip():
        return None
    t = " ".join(str(text).split())
    q = series_qualifier(t)
    if q is None or QUALIFIER.fullmatch(t) is None:
        raise ValueError(f"a volume qualifier is 'Season N' or 'Part N', not {text!r}")
    return q


def _states(series: str, qualifier: str) -> bool:
    """The series title already says the qualifier ("<Title> Season 2" with "Season 2")."""
    word, _, number = qualifier.partition(" ")
    return any(m.group("word").casefold() == word.casefold() and to_decimal(m.group("n")) == to_decimal(number)
               for m in re.finditer(r"(?<![A-Za-z])(?P<word>season|part)\s*(?P<n>\d+(?:\.\d+)?)(?![\d.])", series,
                                    re.IGNORECASE))


def sanitize_series(text: Optional[str]) -> Optional[str]:
    """A series title as the volume scheme writes it: Windows-illegal characters replaced, whitespace collapsed,
    no trailing dot (brackets are kept: the title comes first and the group last, so they cannot be confused)."""
    t = _clean(text, _PLAIN_BRACKETS)
    t = t.rstrip(" .") if t else t
    return t or None


def _cut(text: str, chars: int) -> Optional[str]:
    """``text`` cut to at most ``chars`` characters, ending with ``…`` when cut; None when nothing useful is left."""
    if len(text) <= chars:
        return text
    if chars < 2:
        return None
    head = text[:chars - 1].rstrip(" .,-;_")
    return f"{head}{ELLIPSIS}" if head else None


# --- the length rule ------------------------------------------------------------------------------------------

_SHARE = re.compile(r"^/data/([^/]+)(?:/(.*))?$")


def windows_path(posix_path: str, server: str) -> Optional[str]:
    """The Windows (SMB) path of a container path: ``/data/<share>/rest`` -> ``\\\\server\\<share>\\rest``. None when
    the path is not under ``/data/<share>`` or no server is given. ``server`` may carry its leading ``\\\\``."""
    srv = (server or "").strip().strip("\\/").strip()
    if not srv or not posix_path:
        return None
    path = re.sub(r"/{2,}", "/", str(posix_path).replace("\\", "/"))
    path = path.rstrip("/") if path != "/" else path
    m = _SHARE.match(path)
    if m is None or ".." in path.split("/"):
        return None
    share, rest = m.group(1), m.group(2)
    return f"\\\\{srv}\\{share}" + (f"\\{rest.replace('/', chr(92))}" if rest else "")


def _utf16_length(text: str) -> int:
    """Characters as Windows counts them (UTF-16 code units: an emoji counts twice)."""
    return len(text.encode("utf-16-le")) // 2


def name_fits(name: str, *, folder: Optional[str] = None, limits: NameLimits = _DEFAULT_LIMITS) -> bool:
    """``name`` keeps the length rule in ``folder`` (the target folder's container path; only needed for the Windows
    path check, which is skipped without a server or for a folder outside ``/data/<share>``)."""
    if len(name.encode("utf-8")) > limits.max_name_bytes:
        return False
    if limits.windows_server and folder:
        wp = windows_path(f"{str(folder).rstrip('/')}/{name}", limits.windows_server)
        if wp is not None and _utf16_length(wp) > limits.max_windows_path:
            return False
    return True


def _fit(render: Callable[[Optional[str], Optional[str]], str], first: Optional[str], second: Optional[str],
         labels: Tuple[str, str], fits: Callable[[str], bool], shortened: Tuple[str, ...]) -> NameResult:
    """Shorten ``first`` (cut, then left out), then ``second``, until ``render(first, second)`` fits; ``shortened``:
    what was already cut (the capped group)."""
    name = render(first, second)
    if fits(name):
        return NameResult(name, shortened)
    for which in (0, 1):
        text = (first, second)[which]
        if text is None:
            continue
        shortened = tuple(dict.fromkeys(shortened + (labels[which],)))

        def with_(t: Optional[str]) -> str:
            return render(t, second) if which == 0 else render(first, t)

        lo, hi, best = 1, len(text) - 1, None          # the longest cut that fits (shorter text = shorter name)
        while lo <= hi:
            mid = (lo + hi) // 2
            cut = _cut(text, mid)
            if cut is not None and fits(with_(cut)):
                best, lo = cut, mid + 1
            else:
                hi = mid - 1
        if which == 0:
            first = best
        else:
            second = best
        name = render(first, second)
        if fits(name):
            return NameResult(name, shortened)
    _log.warning("Naming: %r is longer than the length rule allows even without a title or group", name)
    return NameResult(name, shortened, fits=False)


def _ext(ext: Optional[str]) -> str:
    e = (ext or "").strip()
    if not e:
        return ""
    return e if e.startswith(".") else f".{e}"


def _capped_group(group: Optional[str], limits: NameLimits) -> Tuple[Optional[str], bool]:
    g = sanitize_group(group)
    if g is None or len(g) <= limits.group_cap:
        return g, False
    return _cut(g, limits.group_cap), True


# --- names ----------------------------------------------------------------------------------------------------


def chapter_name(chapter: Number, *, chapter_end: Optional[Number] = None, volume: Optional[Number] = None,
                 title: Optional[str] = None, group: Optional[str] = None, ext: str = ".cbz",
                 folder: Optional[str] = None, limits: NameLimits = _DEFAULT_LIMITS) -> NameResult:
    """:func:`chapter_file_name` with how it was made (what was shortened, whether it fits)."""
    start = _number(chapter, "chapter")
    end = _end(start, chapter_end, "chapter")
    vol = _number(volume, "volume") if volume is not None and str(volume).strip() != "" else None
    tpl = compile_template(CHAPTER_SCHEME)
    suffix = _ext(ext)
    base = {"chapter": start, "chapter_end": end, "volume": vol}
    if restates_number(title, start, end):
        title = None

    def render(t: Optional[str], g: Optional[str]) -> str:
        return tpl.render(dict(base, title=t, group=g), sanitize=None) + suffix

    g, capped = _capped_group(group, limits)
    return _fit(render, sanitize_title(title), g, ("title", "group"),
                lambda n: name_fits(n, folder=folder, limits=limits), ("group",) if capped else ())


def chapter_file_name(chapter: Number, *, chapter_end: Optional[Number] = None, volume: Optional[Number] = None,
                      title: Optional[str] = None, group: Optional[str] = None, ext: str = ".cbz",
                      folder: Optional[str] = None, limits: NameLimits = _DEFAULT_LIMITS) -> str:
    """The scheme name of a chapter archive: ``Ch. 0102.00 Vol. 012 (<title>) [<group>].cbz`` (each part after the
    number only when given). ``chapter`` / ``chapter_end`` / ``volume``: Decimal, str or int (``"10.5"``); ``folder``:
    the target folder's container path (``/data/<share>/...``), used for the Windows path length. Raises ValueError for
    a missing or negative number or a range that ends before it starts."""
    return chapter_name(chapter, chapter_end=chapter_end, volume=volume, title=title, group=group, ext=ext,
                        folder=folder, limits=limits).name


def _volume_parts(volume: Number, volume_end: Optional[Number], qualifier: Optional[str],
                  series: Optional[str]) -> Tuple[Decimal, Optional[Decimal], Optional[str]]:
    qual = sanitize_qualifier(qualifier)
    if qual is not None and series and _states(series, qual):
        qual = None
    start = _number(volume, "volume")
    return start, _end(start, volume_end, "volume"), qual


def volume_name(volume: Number, *, volume_end: Optional[Number] = None, group: Optional[str] = None,
                ext: str = ".cbz", folder: Optional[str] = None, limits: NameLimits = _DEFAULT_LIMITS,
                qualifier: Optional[str] = None, series_title: Optional[str] = None,
                extras: Optional[str] = None) -> NameResult:
    """:func:`volume_file_name` with how it was made."""
    start, end, qual = _volume_parts(volume, volume_end, qualifier, sanitize_series(series_title))
    marker = extras_marker(extras) if extras is not None and str(extras).strip() else None
    if extras is not None and str(extras).strip() and marker is None:
        raise ValueError(f"an extras marker is one of the scheme's words, not {extras!r}")
    tpl = compile_template(VOLUME_SCHEME)
    suffix = _ext(ext)

    def render(g: Optional[str], _unused: Optional[str]) -> str:
        return tpl.render({"qualifier": qual, "volume": start, "volume_end": end, "group": g, "extras": marker},
                          sanitize=None) + suffix

    g, capped = _capped_group(group, limits)
    return _fit(render, g, None, ("group", "series"), lambda n: name_fits(n, folder=folder, limits=limits),
                ("group",) if capped else ())


def volume_file_name(volume: Number, *, volume_end: Optional[Number] = None, group: Optional[str] = None,
                     ext: str = ".cbz", folder: Optional[str] = None, limits: NameLimits = _DEFAULT_LIMITS,
                     qualifier: Optional[str] = None, series_title: Optional[str] = None,
                     extras: Optional[str] = None) -> str:
    """The scheme name of a volume archive: ``Vol. 001 [<group>].cbz`` (a range ``Vol. 001-003``, a half volume
    ``Vol. 012.5``) - no series title: the series folder carries it. ``qualifier``: the season / part of the series
    the volume belongs to (``"Season 2"``, ``"Part 5"``), written in front - ``Season 2 - Vol. 001`` - unless
    ``series_title`` (the series folder's title) already says it; volume numbers restart with it, so it keeps two
    seasons' volume 1 apart. ``extras``: the extras the volume holds (``"extras"``, ``"Omake"``, ``"side story"``),
    written `` + Extras`` after the number (:func:`extras_marker`). Raises ValueError without a valid volume number,
    for a qualifier that is not "Season N" / "Part N", or an extras word the scheme does not know."""
    return volume_name(volume, volume_end=volume_end, group=group, ext=ext, folder=folder, limits=limits,
                       qualifier=qualifier, series_title=series_title, extras=extras).name


def titled_volume_name(series_title: str, volume: Number, *, volume_end: Optional[Number] = None,
                       group: Optional[str] = None, ext: str = ".cbz", folder: Optional[str] = None,
                       limits: NameLimits = _DEFAULT_LIMITS, qualifier: Optional[str] = None) -> NameResult:
    """The volume name of the scheme until 2026-10-16, ``<Series title> - Vol. 001 [<group>].cbz``
    (:data:`TITLED_VOLUME_SCHEME`; the group is shortened first, then the title). Never written: the renamer compares
    against it to tell a rename the new volume format alone causes (it waits for the owner's Rename). Raises
    ValueError without a series title or a valid volume number."""
    series = sanitize_series(series_title)
    if series is None:
        raise ValueError(f"a titled volume name needs a series title, not {series_title!r}")
    start, end, qual = _volume_parts(volume, volume_end, qualifier, series)
    tpl = compile_template(TITLED_VOLUME_SCHEME)
    suffix = _ext(ext)
    shortest = _cut(series, 2) or series[:1]           # the titled scheme needs a title: never left out entirely

    def render(g: Optional[str], s: Optional[str]) -> str:
        return tpl.render({"series": s or shortest, "qualifier": qual, "volume": start, "volume_end": end,
                           "group": g}, sanitize=None) + suffix

    g, capped = _capped_group(group, limits)
    return _fit(render, g, series, ("group", "series"), lambda n: name_fits(n, folder=folder, limits=limits),
                ("group",) if capped else ())


# --- what a renamed name keeps (integrator, 2026-10-16: never drop what the scheme can say) ------------------------

_MARKER_WORDS = r"(?:extras?|omake|specials?|side[\s_-]*stor(?:y|ies)|bonus|afterword)"
_MARKER = re.compile(rf"(?<![A-Za-z])(?P<w>{_MARKER_WORDS})(?![A-Za-z])(?!\s*edition)", re.IGNORECASE)
_MARKER_TAG = re.compile(rf"^\+?\s*{_MARKER_WORDS}$", re.IGNORECASE)
_SCHEME_MARKER = re.compile(r"^\s*\+\s*(?P<w>\S.*?)(?=\s*\[|\s*$)")
_VOLUME_AT = re.compile(
    r"(?<![A-Za-z])(?:v|vol(?:ume)?s?\.?|tome|tomo|band|deel|album|livre|(?-i:T))\s*(?P<n>\d+(?:\.\d+)?)"
    r"(?:\s*-\s*(?:v|vol(?:ume)?\.?)?\s*(?P<m>\d+(?:\.\d+)?))?", re.IGNORECASE)
_CHAPTER_AT = re.compile(
    r"(?<![A-Za-z])(?:c|ch(?:apter|ap|p)?s?\.?)\s*(?P<n>\d+(?:\.\d+)?)"
    r"(?:\s*-\s*(?:c|ch(?:apter|ap|p)?\.?)?\s*(?P<m>\d+(?:\.\d+)?))?", re.IGNORECASE)
_BARE_NUMBER = re.compile(r"(?<![\d.])(?P<n>\d+(?:\.\d+)?)(?:-(?P<m>\d+(?:\.\d+)?))?(?![\d.])")
_NOISE = re.compile(r"[\s+\-_.,~:;!#\u2013\u2014]+")
_NEVER_A_GROUP = re.compile(r"(?i)(?<![A-Za-z])(?:edition|omnibus|deluxe|colou?r(?:ed)?|uncensored|raw)(?![A-Za-z])")


def extras_marker(text: Optional[str]) -> Optional[str]:
    """The scheme's spelling of an extras word (``extras`` -> ``Extras``, ``side-story`` -> ``Side Story``,
    ``OMAKE`` -> ``Omake``); None when *text* is not one."""
    t = " ".join(re.split(r"[\s_-]+", (text or "").strip().lstrip("+").strip()))
    if not t or not re.fullmatch(_MARKER_WORDS, t, re.IGNORECASE):
        return None
    return " ".join(w[:1].upper() + w[1:].lower() for w in t.split())


@dataclass(frozen=True)
class KeptParts:
    """What a renamed name keeps of the name it had: its group and extras marker - or why it cannot be renamed
    without losing something (``problem``)."""

    group: Optional[str] = None
    extras: Optional[str] = None
    problem: Optional[str] = None


def _equal(a: Optional[Decimal], b: Optional[Decimal]) -> bool:
    from .parsing.heads import padded_sub_number

    return a is not None and b is not None and (a == b or padded_sub_number(a) == b)


def _tail(stem: str, unit: UnitRange, regex: "re.Pattern[str]") -> Optional[str]:
    """The text after the unit number of *stem* (the last place it is written), None when it is not found."""
    for pattern in (regex, _BARE_NUMBER):
        hits = [m for m in pattern.finditer(stem) if _equal(to_decimal(m.group("n")), unit.start)]
        if hits:
            return stem[hits[-1].end():]
    return None


def _quoted(parts: Iterable[str]) -> str:
    return ", ".join(f"'{p}'" for p in parts)


def kept_parts(parsed: ParsedName) -> KeptParts:
    """The group and extras marker *parsed*'s scheme name carries, read from the name itself after its unit number
    (the series title and the number before it are the folder's and the scheme's): the trailing ``(...)`` / ``[...]``
    tags - the year, a ``(Digital...)`` edition, a ``(f)`` fix, a ``[v2]`` revision, a volume tag and a placeholder
    group are dropped by design; an extras word is the marker; the group is the parser's, else the first other tag after
    the edition, else the last one - and any text outside them that is an extras word. Anything else is a ``problem``:
    the file is left alone rather than renamed lossily. A name in the scheme itself (layer 1) is exact: its group, and
    its marker after `` + ``. Only volumes and chapters read by the generic layer (which keeps no group or title) are
    looked at; other names keep what their layer read."""
    from .parsing._common import split_extension
    from .parsing.layers import _EDITION, _FIX, _REVISION, _TAG, _VOL_TAG, _YEAR

    stem = split_extension(parsed.name)[0]
    group = real_group(parsed.group)
    volume = parsed.kind is Kind.VOLUME
    if parsed.layer is Layer.SCHEME:
        tail = _tail(stem, parsed.volume, _VOLUME_AT) if volume and parsed.volume is not None else None
        m = _SCHEME_MARKER.match(tail or "")
        return KeptParts(group, extras_marker(m.group("w")) if m else None)
    if not volume and not (parsed.kind is Kind.CHAPTER and parsed.layer is Layer.GENERIC):
        return KeptParts(group)
    unit = parsed.volume if volume else parsed.chapter
    if unit is None:
        return KeptParts(group)
    owner = OWNER_UNITS_NOTE in parsed.notes            # the owner set the numbers: the rest of the name is not read
    if group is not None and _NEVER_A_GROUP.search(group):
        group = None                                    # "(Omnibus)" is no group: it is something the scheme cannot keep
    tail = _tail(stem, unit, _VOLUME_AT if volume else _CHAPTER_AT)
    if tail is None:
        if owner:
            return KeptParts(group)
        return KeptParts(group, problem="the name's number could not be found again")
    markers, candidates, digital = [], [], -1
    for i, t in enumerate(_TAG.finditer(tail)):
        tag = (t.group(1) if t.group(1) is not None else t.group(2)).strip()
        if not tag or _YEAR.match(tag) or _FIX.match(tag) or _REVISION.match(tag) or _VOL_TAG.match(tag) \
                or real_group(tag) is None:
            continue
        if _EDITION.match(tag):
            digital = i if digital < 0 else digital
        elif _MARKER_TAG.match(tag):
            markers.append(extras_marker(tag))
        else:
            candidates.append((i, tag))
    rest = _TAG.sub(" ", tail)
    if volume:
        markers += [extras_marker(m.group("w")) for m in _MARKER.finditer(rest)]
        rest = _MARKER.sub(" ", rest)
    problems = []
    text = _NOISE.sub(" ", rest).strip()
    if text and not owner:
        problems.append(text)
    if group is None:
        usable = [c for c in candidates if not _NEVER_A_GROUP.search(c[1])]
        after = [c for c in usable if c[0] > digital] if digital >= 0 else []
        chosen = (after[0] if after else usable[-1]) if usable else None
        group = real_group(chosen[1]) if chosen else None
        candidates = [c for c in candidates if c is not chosen]
    else:
        candidates = [c for c in candidates if c[1] not in (group, parsed.group)]
    problems += [f"({c[1]})" for c in candidates]
    distinct = list(dict.fromkeys(m for m in markers if m))
    if len(distinct) > 1:
        problems.append(" + ".join(distinct))
    if problems:
        return KeptParts(group, None, f"the name has {_quoted(problems)} the scheme cannot keep")
    return KeptParts(group, distinct[0] if distinct else None)


def unkept_reason(parsed: Optional[ParsedName]) -> Optional[str]:
    """Why *parsed* is left alone because its scheme name would lose part of its name (:func:`kept_parts`), else
    None."""
    if parsed is None or parsed.kind not in (Kind.VOLUME, Kind.CHAPTER):
        return None
    try:
        return kept_parts(parsed).problem
    except Exception:  # noqa: BLE001 - an odd name: the other checks decide
        _log.debug("Naming: could not compare %r with its scheme name", parsed.name, exc_info=True)
        return None


# --- a parsed file's scheme name --------------------------------------------------------------------------------


def target_name(parsed: Optional[ParsedName], *, ext: str, series_title: Optional[str],
                volume_of: Optional[Callable[[Decimal], Optional[Decimal]]] = None, folder: Optional[str] = None,
                limits: NameLimits = _DEFAULT_LIMITS, titled: bool = False) -> Optional[str]:
    """The scheme name for a parsed archive, or None to leave the file alone:

    - unreadable (no units), a bare number whose kind the name does not state (``01.cbz``, or a bare number after the
      series title read as a chapter only by guess - the series' "volumes or chapters?" answer settles it), a volume
      with loose chapters (``v10 + 085-086``), or numbers the generic layer found more than once;
    - an extra / omake / one-shot without its own chapter number (owner, 2026-10-10: "leave them"; ``.5`` chapters
      are chapters);
    - a chapter whose own name states a volume range;
    - a name that holds something its scheme name could not keep (:func:`kept_parts`: another edition, a volume title,
      a second group...; :func:`unkept_reason` says what).

    *series_title* (the series folder's title) is not part of a volume name any more: it only keeps a season / part
    the title already says out of the name. ``titled=True`` gives the volume name of the scheme until 2026-10-16
    (``<series_title> - Vol. 001``, :func:`titled_volume_name`; None without a series title) - the renamer's
    comparison, never a name to write.

    A chapter's volume is the one its name states, else ``volume_of(chapter)`` when given (a range only when both
    ends map to the same volume). The title and group come from the name as parsed (a title that only restates the
    number and a placeholder group are left out); a volume keeps the season / part its name states
    (:attr:`~mangalist.parsing.ParsedName.qualifier`). A name cut off after a chapter word (``[Vol. 0001 Ch``) is
    ``ambiguous``: left alone."""
    if parsed is None or parsed.guessed or parsed.ambiguous:
        return None
    try:
        if parsed.kind is Kind.CHAPTER:
            kept = kept_parts(parsed)
            if kept.problem:
                return None
            if kept.group != parsed.group and parsed.layer is Layer.GENERIC:
                parsed = _with_group(parsed, kept.group)
            return _chapter_target(parsed, ext, volume_of, folder, limits)
        if parsed.kind is Kind.VOLUME:
            if parsed.volume is None:
                return None
            kept = kept_parts(parsed)
            if kept.problem:
                return None
            rng = parsed.volume
            kw = dict(volume_end=rng.end if rng.is_range else None, group=kept.group, ext=ext, folder=folder,
                      limits=limits, qualifier=parsed.qualifier)
            if titled:
                if not (series_title or "").strip() or sanitize_series(series_title) is None:
                    return None
                return titled_volume_name(series_title, rng.start, **kw).name
            return volume_file_name(rng.start, series_title=series_title, extras=kept.extras, **kw)
    except ValueError:
        _log.debug("Naming: no scheme name for %r", parsed.name, exc_info=True)
    return None


def _with_group(parsed: ParsedName, group: Optional[str]) -> ParsedName:
    from dataclasses import replace

    return replace(parsed, group=group)


def _chapter_target(parsed: ParsedName, ext: str, volume_of, folder, limits) -> Optional[str]:
    ch = parsed.chapter
    if ch is None or parsed.is_extra:
        return None
    volume: Optional[Decimal] = None
    if parsed.volume is not None:
        if parsed.volume.is_range:
            return None
        volume = parsed.volume.start
    elif volume_of is not None:
        volume = _volume_of(volume_of, ch.start)
        if volume is not None and ch.is_range and _volume_of(volume_of, ch.end) != volume:
            volume = None
    return chapter_file_name(ch.start, chapter_end=ch.end if ch.is_range else None, volume=volume,
                             title=parsed.title, group=parsed.group, ext=ext, folder=folder, limits=limits)


def _volume_of(volume_of: Callable[[Decimal], Optional[Decimal]], chapter: Decimal) -> Optional[Decimal]:
    try:
        v = volume_of(chapter)
    except Exception:  # noqa: BLE001 - a broken lookup means "volume not known", never a guess
        _log.debug("Naming: the volume lookup failed for chapter %s", chapter, exc_info=True)
        return None
    return to_decimal(v) if v is not None else None


# --- the volume a chapter belongs to (MangaPixer's volume lists) ------------------------------------------------


def volumes_from_list(volumes: Iterable[Any]) -> Callable[[Decimal], Optional[Decimal]]:
    """A chapter -> volume lookup from a volume list (:class:`~mangalist.knowledge.VolumeInfo`: ``volume``,
    ``chapters_from``, ``chapters_to``). A chapter maps to a volume only when exactly one volume's range holds it
    (``from <= n <= to``, so ``45.5`` is in ``38-46``, the inventory's and the upgrades' rule); a chapter between two
    volumes' ranges (``10.5`` after a volume ending at 10), in no range, or in two overlapping ranges has no volume.
    Volumes without a chapter range are skipped; the first entry of a repeated volume number wins."""
    spans = []
    seen = set()
    for v in volumes or ():
        number = to_decimal(getattr(v, "volume", None))
        lo = to_decimal(getattr(v, "chapters_from", None))
        hi = to_decimal(getattr(v, "chapters_to", None))
        if number is None or number in seen:
            continue
        seen.add(number)
        if lo is None or hi is None or lo > hi:
            continue
        spans.append((number, lo, hi))

    def lookup(chapter: Decimal) -> Optional[Decimal]:
        c = to_decimal(chapter) if not isinstance(chapter, UnitRange) else chapter.start
        if c is None:
            return None
        hits = {number for number, lo, hi in spans if lo <= c <= hi}
        return hits.pop() if len(hits) == 1 else None

    return lookup


def _no_volume(chapter: Decimal) -> Optional[Decimal]:
    return None


def volume_lookup(db, series_id: int) -> Callable[[Decimal], Optional[Decimal]]:
    """The chapter -> volume lookup of library series ``series_id`` from MangaPixer's volume list (its own item or the
    nearest ancestor's, exactly as the chapter-to-volume upgrades read it:
    :func:`mangalist.upgrades._knowledge_volumes`), by :func:`volumes_from_list`'s rule. Without a list (series
    unknown, not matched, MangaPixer unreadable) every chapter has no volume. Read once: call it again after a
    MangaPixer sync."""
    from .downloads.placement import locate_series
    from .upgrades import _knowledge_volumes

    try:
        series, _root, _folder = locate_series(db, series_id)
        volumes, why = _knowledge_volumes(db, series)
    except Exception:  # noqa: BLE001 - no list means "volume not known", never a guess
        _log.debug("Naming: no volume list for series %s", series_id, exc_info=True)
        return _no_volume
    if not volumes:
        _log.debug("Naming: no volume list for series %s (%s)", series_id, why)
        return _no_volume
    return volumes_from_list(volumes)


__all__ = [
    "CHAPTER_SCHEME", "DEFAULT_SCHEMES", "ELLIPSIS", "NameLimits", "NameResult", "TITLED_VOLUME_SCHEME",
    "VOLUME_SCHEME", "KeptParts", "chapter_file_name", "chapter_name", "extras_marker", "kept_parts", "name_fits",
    "restates_number", "sanitize_group", "sanitize_qualifier", "sanitize_series", "sanitize_title", "target_name",
    "titled_volume_name", "unkept_reason",
    "volume_file_name", "volume_lookup", "volume_name", "volumes_from_list", "windows_path",
]

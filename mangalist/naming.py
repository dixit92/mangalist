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

**Volumes** - ``<Series title> - Vol. 001 [<group>].cbz`` (3-digit volume; a range ``Vol. 001-003``; the series title is
MangaPixer's title, owner 2026-10-10). A season / part the file name states is kept after the title, ``<Series title>
Season 2 - Vol. 001``, because volume numbers restart with it (renamer dry run, 2026-10-10).

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
volume name shortens the group, then the series title. If even the shortest form is too long it is returned anyway and
flagged (:attr:`NameResult.fits` False, logged as a warning).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable, Iterable, Optional, Tuple, Union

from .parsing._common import QUALIFIER, real_group, series_qualifier
from .parsing.model import Kind, ParsedName, UnitRange, plain, to_decimal
from .parsing.template import MANGALIST_CHAPTER_SCHEME, MANGALIST_VOLUME_SCHEME, compile_template

_log = logging.getLogger(__name__)

CHAPTER_SCHEME = MANGALIST_CHAPTER_SCHEME
"""``Ch. %CN4{-%CE4}{ Vol. %V3}{ (%CT)}{ [%G]}`` - the chapter scheme as a :mod:`mangalist.parsing.template`."""
VOLUME_SCHEME = MANGALIST_VOLUME_SCHEME
"""``%T - Vol. %V3{-%VE3}{ [%G]}`` - the volume scheme."""
DEFAULT_SCHEMES: Tuple[str, ...] = (CHAPTER_SCHEME, VOLUME_SCHEME)
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


def volume_name(series_title: str, volume: Number, *, volume_end: Optional[Number] = None,
                group: Optional[str] = None, ext: str = ".cbz", folder: Optional[str] = None,
                limits: NameLimits = _DEFAULT_LIMITS, qualifier: Optional[str] = None) -> NameResult:
    """:func:`volume_file_name` with how it was made."""
    series = sanitize_series(series_title)
    if series is None:
        raise ValueError(f"a volume name needs a series title, not {series_title!r}")
    qual = sanitize_qualifier(qualifier)
    if qual is not None and _states(series, qual):
        qual = None
    start = _number(volume, "volume")
    end = _end(start, volume_end, "volume")
    tpl = compile_template(VOLUME_SCHEME)
    suffix = _ext(ext)

    shortest = _cut(series, 2) or series[:1]           # the volume scheme needs a title: never left out entirely

    def render(g: Optional[str], s: Optional[str]) -> str:
        return tpl.render({"series": s or shortest, "qualifier": qual, "volume": start, "volume_end": end,
                           "group": g}, sanitize=None) + suffix

    g, capped = _capped_group(group, limits)
    return _fit(render, g, series, ("group", "series"), lambda n: name_fits(n, folder=folder, limits=limits),
                ("group",) if capped else ())


def volume_file_name(series_title: str, volume: Number, *, volume_end: Optional[Number] = None,
                     group: Optional[str] = None, ext: str = ".cbz", folder: Optional[str] = None,
                     limits: NameLimits = _DEFAULT_LIMITS, qualifier: Optional[str] = None) -> str:
    """The scheme name of a volume archive: ``<Series title> - Vol. 001 [<group>].cbz`` (a range ``Vol. 001-003``).
    ``qualifier``: the season / part of the series the volume belongs to (``"Season 2"``, ``"Part 5"``), written
    after the title - ``<Series title> Season 2 - Vol. 001`` - unless the title already says it; volume numbers
    restart with it, so it keeps two seasons' volume 1 apart. Raises ValueError without a series title, a valid
    volume number, or for a qualifier that is not "Season N" / "Part N"."""
    return volume_name(series_title, volume, volume_end=volume_end, group=group, ext=ext, folder=folder,
                       limits=limits, qualifier=qualifier).name


# --- a parsed file's scheme name --------------------------------------------------------------------------------


def target_name(parsed: Optional[ParsedName], *, ext: str, series_title: Optional[str],
                volume_of: Optional[Callable[[Decimal], Optional[Decimal]]] = None, folder: Optional[str] = None,
                limits: NameLimits = _DEFAULT_LIMITS) -> Optional[str]:
    """The scheme name for a parsed archive, or None to leave the file alone:

    - unreadable (no units), a bare number whose kind the name does not state (``01.cbz``, or a bare number after the
      series title read as a chapter only by guess - the series' "volumes or chapters?" answer settles it), a volume
      with loose chapters (``v10 + 085-086``), or numbers the generic layer found more than once;
    - an extra / omake / one-shot without its own chapter number (owner, 2026-10-10: "leave them"; ``.5`` chapters
      are chapters);
    - a chapter whose own name states a volume range, or a volume file without a ``series_title``.

    A chapter's volume is the one its name states, else ``volume_of(chapter)`` when given (a range only when both
    ends map to the same volume). The title and group come from the name as parsed (a title that only restates the
    number and a placeholder group are left out); a volume keeps the season / part its name states
    (:attr:`~mangalist.parsing.ParsedName.qualifier`). A name cut off after a chapter word (``[Vol. 0001 Ch``) is
    ``ambiguous``: left alone."""
    if parsed is None or parsed.guessed or parsed.ambiguous:
        return None
    try:
        if parsed.kind is Kind.CHAPTER:
            return _chapter_target(parsed, ext, volume_of, folder, limits)
        if parsed.kind is Kind.VOLUME:
            if parsed.volume is None or not (series_title or "").strip() or sanitize_series(series_title) is None:
                return None
            rng = parsed.volume
            return volume_file_name(series_title, rng.start, volume_end=rng.end if rng.is_range else None,
                                    group=parsed.group, ext=ext, folder=folder, limits=limits,
                                    qualifier=parsed.qualifier)
    except ValueError:
        _log.debug("Naming: no scheme name for %r", parsed.name, exc_info=True)
    return None


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
    "CHAPTER_SCHEME", "DEFAULT_SCHEMES", "ELLIPSIS", "NameLimits", "NameResult", "VOLUME_SCHEME", "chapter_file_name",
    "chapter_name", "name_fits", "restates_number", "sanitize_group", "sanitize_qualifier", "sanitize_series",
    "sanitize_title", "target_name",
    "volume_file_name", "volume_lookup", "volume_name", "volumes_from_list", "windows_path",
]

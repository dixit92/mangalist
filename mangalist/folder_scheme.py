"""The series-folder name scheme: ``<romaji> [<English>]`` (owner, 2026-10-15, answer 6: "Always use "<romaji>
[<English>]", similar to animefetch. Folders not in this scheme are rename candidates."). Pure Python: no Qt, no I/O.

The rules follow animefetch's show folders (``animefetch/placement.py``: ``clean_name`` / ``propose_folder``, and
``plex_titles.english_from_mal``), adapted to manga:

- **romaji** - the series' MangaUpdates title (MangaPixer's record title, else MangaList's own confirmed match; the
  caller looks it up). A title longer than :data:`LONG_TITLE` characters is shortened to its part before the first
  subtitle separator (``: ``, `` - ``, `` ~``), when that part is a real name - animefetch's "official short name" for a
  long romaji title (``Kaguya-sama wa Kokurasetai: Tensai-tachi no Ren'ai Zunousen`` -> ``Kaguya-sama wa Kokurasetai``).
- **English** - the English title, in square brackets after the romaji. It is LEFT OUT when it is unknown or when it is
  the romaji again written differently (compared on letters and digits only, accents folded: ``86 Eighty-Six`` /
  ``86 -Eighty Six-``), so a series without an English title is just ``<romaji>``. MangaUpdates and MangaPixer do not
  say which of a series' alternative titles is the English one, so the English title comes from (see
  :func:`english_from_folder`): the folder's own ``[English]`` part (the owner's), else - for a top-level folder - a
  final ``(English)`` after a romaji name, or the folder's whole name when it reads as English (more English words
  than romaji particles, or one of the record's alternative titles that is not romaji), else MangaDex's English title
  when the caller has one (Add series).
- **Characters** (animefetch's ``clean_name``, plus Windows' rules): ``:`` dropped (``Re:Zero`` -> ``ReZero``), ``/``
  -> ``-``, ``\\ * ? " < > |`` and control characters dropped, ``[ ]`` inside a part -> ``( )`` (the scheme's own
  brackets stay unambiguous), white space collapsed, no leading dot (a hidden folder is never scanned), no trailing dot
  or space, a Windows reserved name suffixed (:func:`mangalist.store.names.windows_safe_name`). Articles and other
  punctuation stay as the titles write them.
- **Length**: the name is at most :data:`MAX_FOLDER_BYTES` (255) bytes of UTF-8; when a Windows server name is set the
  folder's whole Windows path must also leave room for a chapter file below it (:func:`fit_name`). A name too long is
  cut in this order: the English part (ending with ``…``, then left out), then the romaji.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence, Tuple

LONG_TITLE = 50                 # animefetch's LONG_TITLE: a longer romaji title is shortened to its main part
MAX_FOLDER_BYTES = 255
ELLIPSIS = "…"
#: The shortest file name the length rule must leave room for under the folder (a chapter in the file scheme).
PROBE_FILE = "Ch. 0000.00 Vol. 000.cbz"

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_DROPPED = re.compile(r'[\\*?"<>|]')
_SPACES = re.compile(r"\s+")
_SEPARATOR = re.compile(r"\s*(?::\s|\s-\s|\s~|～)")
_FOLDER = re.compile(r"^\s*(?P<title>.+?)(?:\s*\[(?P<eng>[^\]]+)\])?\s*$")
_WORD = re.compile(r"[a-z]+(?:'[a-z]+)?")

#: English words that tell an English title from a romaji one ("to" and "no" are both, so neither counts).
ENGLISH_WORDS = frozenset("""
    the a an of and in is are was were my i i'm me you your with for on at but as be been from who how what when where
    why this that it it's its not into after before his her their our we they by or if about can't don't won't isn't
    am has have had will would all than then there here our us he she him them do does did over under every
""".split())
#: Japanese particles and words that mark a romaji title (words that are English too - "made", "to" - are left out).
ROMAJI_WORDS = frozenset("""
    wa ga wo ni de ka mo yo ne desu deshita shita suru shite nai naru natta kara dake demo kedo node sae datta
    ja nan nani tte kun chan sama san senpai sensei boku ore watashi atashi kimi anata kanojo kareshi isekai tensei
    koi shoujo shounen monogatari hime oujo yuusha maou mahou seikatsu gakuen ojou onee onii imouto nichijou sekai
    tachi saikyou no o
""".split())


# --- comparing and reading titles --------------------------------------------------------------------------------

def title_key(text: Optional[str]) -> str:
    """The comparison key of a title: accents folded, lower case, letters and digits only (animefetch's ``norm``);
    a title with no Latin letter or digit keeps its case-folded text."""
    t = unicodedata.normalize("NFKD", text or "")
    key = re.sub(r"[^0-9a-z]+", "", t.encode("ascii", "ignore").decode().lower())
    return key or re.sub(r"\s+", "", (text or "").casefold())


def same_title(a: Optional[str], b: Optional[str]) -> bool:
    """*a* and *b* are one title written differently (punctuation, spacing, case, accents)."""
    return bool(a and b and title_key(a) and title_key(a) == title_key(b))


def _words(text: str) -> Sequence[str]:
    folded = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return _WORD.findall(folded.replace("’", "'"))


def _hits(text: Optional[str]) -> Tuple[int, int]:
    words = _words(text or "")
    return sum(w in ENGLISH_WORDS for w in words), sum(w in ROMAJI_WORDS for w in words)


def romaji_like(text: Optional[str]) -> bool:
    """The title holds more Japanese particles / romaji words (``no``, ``wa``, ``-sama``, ``isekai``) than English
    words."""
    english, romaji = _hits(text)
    return romaji > english


def looks_english(text: Optional[str]) -> bool:
    """The title reads as English: more English words (``the``, ``of``, ``is``, ``my`` ...) than romaji particles. A
    heuristic - MangaUpdates does not label its alternative titles by language."""
    english, romaji = _hits(text)
    return english > romaji


def latin(text: str) -> bool:
    return bool(text) and all(ord(c) < 0x250 or not c.isalpha() for c in text)


def parse_folder(name: str) -> Tuple[str, Optional[str]]:
    """``"<romaji> [<English>]"`` -> (romaji, English); a name without a final bracket -> (name, None). The same
    reading as :func:`mangalist.classifier.parse_folder_name`."""
    m = _FOLDER.match(name or "")
    if not m:
        return (name or "").strip(), None
    eng = m.group("eng")
    return m.group("title").strip(), (eng.strip() or None) if eng else None


# --- cleaning --------------------------------------------------------------------------------------------------

def clean_part(text: Optional[str]) -> str:
    """One part of the folder name (the romaji or the English title) in the scheme's characters: see the module
    docstring. Empty when nothing usable is left."""
    t = unicodedata.normalize("NFC", text or "")
    t = _CONTROL.sub(" ", t).replace(":", "").replace("/", "-")
    t = _DROPPED.sub("", t).replace("[", "(").replace("]", ")")
    t = _SPACES.sub(" ", t).strip().lstrip(".").strip()
    return t.rstrip(". ")


def short_romaji(title: Optional[str]) -> str:
    """The romaji part of the folder name: *title* cleaned, cut to its part before the first subtitle separator when it
    is longer than :data:`LONG_TITLE` characters and that part is a real name (two characters or more, not only digits
    or punctuation)."""
    raw = (title or "").strip()
    if len(clean_part(raw)) > LONG_TITLE:
        m = _SEPARATOR.search(raw)
        if m:
            head = clean_part(raw[:m.start()])
            if len(head) >= 2 and re.search(r"[^\W\d_]", head):
                return head
    return clean_part(raw)


# --- the name --------------------------------------------------------------------------------------------------

def _render(romaji: str, english: Optional[str]) -> str:
    return f"{romaji} [{english}]" if english else romaji


def _cut(text: str, chars: int) -> Optional[str]:
    if len(text) <= chars:
        return text
    if chars < 2:
        return None
    head = text[:chars - 1].rstrip(" .,-;_~")
    return f"{head}{ELLIPSIS}" if head else None


def _windows_safe(name: str) -> str:
    """*name* with a Windows reserved name suffixed and no trailing dot / space (never cut: the length is
    :func:`fit_name`'s)."""
    from .store.names import MAX_NAME, windows_safe_name

    if not name or len(name) > MAX_NAME:
        return name or ""
    return windows_safe_name(name, "_")


def _fits_default(name: str) -> bool:
    return len(name.encode("utf-8")) <= MAX_FOLDER_BYTES


def fit_name(romaji: str, english: Optional[str], fits=None) -> str:
    """``<romaji> [<English>]`` cut until *fits* (``name -> bool``; default: at most 255 bytes) says yes: the English
    part first (cut with ``…``, then left out), then the romaji. Returned as it is when even the shortest form fails."""
    fits = fits or _fits_default
    name = _windows_safe(_render(romaji, english))
    if fits(name):
        return name
    if english:
        for chars in range(len(english) - 1, 0, -1):
            cut = _cut(english, chars)
            if cut is not None and fits(_windows_safe(_render(romaji, cut))):
                return _windows_safe(_render(romaji, cut))
        if fits(_windows_safe(romaji)):                   # the English part left out
            return _windows_safe(romaji)
    for chars in range(len(romaji) - 1, 0, -1):
        cut = _cut(romaji, chars)
        if cut is not None and fits(_windows_safe(cut)):
            return _windows_safe(cut)
    return _windows_safe(romaji)


def scheme_name(romaji: Optional[str], english: Optional[str] = None, *, fits=None) -> Optional[str]:
    """The scheme's folder name for a series called *romaji* (its MangaUpdates title) with the English title *english*
    (None: unknown); None without a usable romaji title. The bracket is left out when the English title is the romaji
    (or its short form) written differently."""
    full = clean_part(romaji)
    if not full:
        return None
    short = short_romaji(romaji)
    eng = clean_part(english) if english else ""
    if eng and (same_title(eng, short) or same_title(eng, full)):
        eng = ""
    return fit_name(short, eng or None, fits) or None


def windows_fits(root_path: str, server: Optional[str]):
    """The length rule of a folder in library folder *root_path*: at most 255 bytes, and - with a Windows server name -
    the Windows path of a short chapter file below it at most 259 characters (:mod:`mangalist.naming`'s rule)."""
    from .naming import NameLimits, windows_path

    limits = NameLimits(windows_server=server or None)

    def fits(name: str) -> bool:
        if len(name.encode("utf-8")) > MAX_FOLDER_BYTES:
            return False
        if limits.windows_server and root_path:
            wp = windows_path(f"{str(root_path).rstrip('/')}/{name}/{PROBE_FILE}", limits.windows_server)
            if wp is not None and len(wp.encode("utf-16-le")) // 2 > limits.max_windows_path:
                return False
        return True

    return fits


# --- where the English title comes from --------------------------------------------------------------------------

_BRACKETS = re.compile(r"\[([^\[\]]+)\]")
_CUT_TAG = re.compile(r"^[^\[\]]*\]\s*")
_TRAILING_PAREN = re.compile(r"^(?P<head>.+?)\s*\((?P<inner>[^()]+)\)\s*$")


def _in_title(part: str, romaji: Optional[str]) -> bool:
    """*part* is a piece of the romaji title itself (``Skill [Name] de ...``), not a translation of it."""
    key = title_key(part)
    return bool(romaji and key and key in title_key(romaji))


def english_from_folder(folder_name: str, romaji: Optional[str], alt_titles: Iterable[str] = (), *,
                        top_level: bool = True) -> Optional[str]:
    """The English title a series folder already carries, or None:

    - its ``[English]`` part - the owner's convention, kept whatever it says; a bracket anywhere in the name counts,
      as animefetch reads show folders (the last one first), except a bracket that is a piece of the romaji title
      itself (``Skill [Name] de ...``);
    - for a top-level folder without one: a final ``(...)`` that reads as English after a romaji name
      (``Romaji no Title (The English Title)``); else the whole name when it is not the romaji (nor its short form) and
      reads as English (:func:`looks_english`), or is one of the record's alternative titles and does not read as
      romaji (``Solo Leveling`` for ``Na Honjaman Level Up``);
    - a subseries folder (``Side 1 - ...``) gives nothing but its bracket."""
    name = _CUT_TAG.sub("", (folder_name or "").strip())     # "Author] Title": the rest of a cut "[Author]"
    for bracket in reversed(_BRACKETS.findall(name)):
        text = bracket.strip()
        if text and not _in_title(text, romaji):
            return text
    if not top_level or not name:
        return None
    if romaji and (same_title(name, romaji) or same_title(name, short_romaji(romaji))):
        return None
    latin_alts = [a for a in alt_titles or () if isinstance(a, str) and latin(a)]
    paren = _TRAILING_PAREN.match(name)
    if paren:
        inner = paren.group("inner").strip()
        if (not romaji_like(inner) and not looks_english(paren.group("head")) and not _in_title(inner, romaji)
                and (looks_english(inner) or any(same_title(inner, a) for a in latin_alts))):
            return inner
    if romaji_like(name):
        return None
    if looks_english(name):
        return name
    if any(same_title(name, a) for a in latin_alts):
        return name
    return None


@dataclass(frozen=True)
class SeriesTitles:
    """The titles a series folder is named by: *romaji* (MangaUpdates' title) and *english* (None: not known)."""

    romaji: str
    english: Optional[str] = None
    source: str = ""                 # where the romaji came from ("MangaPixer", "MangaUpdates match", ...)

    def name(self, fits=None) -> Optional[str]:
        return scheme_name(self.romaji, self.english, fits=fits)


def titles_for(folder_name: str, romaji: Optional[str], alt_titles: Iterable[str] = (), *, top_level: bool = True,
               english: Optional[str] = None, source: str = "") -> Optional[SeriesTitles]:
    """A series' :class:`SeriesTitles`: *romaji* from its record, the English title from the folder
    (:func:`english_from_folder`), else *english* (a title the caller knows, e.g. MangaDex's). None without a romaji
    title (the folder cannot be named: it is left alone)."""
    if not clean_part(romaji):
        return None
    eng = english_from_folder(folder_name, romaji, alt_titles, top_level=top_level) if folder_name else None
    return SeriesTitles(str(romaji).strip(), eng or (english.strip() if english and english.strip() else None), source)


def english_candidates(alt_titles: Iterable[str], romaji: Optional[str], limit: int = 6) -> Sequence[str]:
    """Alternative titles that read as English (for the Add series name list: ``<romaji> [<one of these>]``), in the
    record's order, without the romaji again."""
    out = []
    for a in alt_titles or ():
        if not isinstance(a, str) or not latin(a) or not looks_english(a):
            continue
        if romaji and same_title(a, romaji):
            continue
        if not any(same_title(a, b) for b in out):
            out.append(a.strip())
        if len(out) >= limit:
            break
    return out


__all__ = [
    "ELLIPSIS", "ENGLISH_WORDS", "LONG_TITLE", "MAX_FOLDER_BYTES", "ROMAJI_WORDS", "SeriesTitles", "clean_part",
    "english_candidates", "english_from_folder", "fit_name", "looks_english", "parse_folder", "romaji_like",
    "same_title", "scheme_name", "short_romaji", "title_key", "titles_for", "windows_fits",
]

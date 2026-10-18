"""The series-folder name scheme: ``<romaji> [<English>]`` (owner, 2026-10-15, answer 6: "Always use "<romaji>
[<English>]", similar to animefetch. Folders not in this scheme are rename candidates."). Pure Python: no Qt, no I/O.

The rules follow animefetch's show folders (``animefetch/placement.py``: ``clean_name`` / ``propose_folder``, and
``plex_titles.english_from_mal``), adapted to manga:

- **romaji** - the series' MangaUpdates title (MangaPixer's record title, else MangaList's own confirmed match; the
  caller looks it up). A title longer than :data:`LONG_TITLE` characters is replaced by an **official short name** when
  the series has one, animefetch's rule (``placement.short_name_candidates``; owner, 2026-10-16: "a long romaji stays
  full if official name isn't there"): see :func:`short_name_candidates`. Without one the full romaji stays - a title
  is never cut at a subtitle separator any more.
- **English** - the series' **official English title**, in square brackets after the romaji (owner, 2026-10-16,
  answer 76: "How does animefetch do it?" - animefetch takes MyAnimeList's official English title): AniList's English
  title (``title.english``, the licensed one), looked up through MangaDex's links and cached
  (:mod:`mangalist.english_titles`). Only when it is not known (no AniList record, or no English title there) the
  older sources are used, in this order (:func:`titles_for`): the folder's own ``[English]`` part, else - for a
  top-level folder - a final ``(English)`` after a romaji name, or the folder's whole name when it reads as English
  (more English words than romaji particles, or one of the record's alternative titles that is not romaji)
  (:func:`english_from_folder`), else MangaDex's English title (its only English alternative title; Add series:
  MangaDex's English). The bracket is LEFT OUT when there is no English title, or when the folder's romaji part - the
  short name or the full romaji - is the English title written differently (compared on letters and digits only,
  accents folded: ``86 Eighty-Six`` / ``86 -Eighty Six-``; owner, answer 86: "As long as it doesn't become "Tearmoon
  Empire [Tearmoon Empire]""), so a series without an English title is just ``<romaji>``. An official title that is
  the full romaji or its short name again (AniList lists no English name) is no English title: the next source is used. An English title
  that repeats the romaji part in front of a subtitle separator keeps only the subtitle (``Noragami: Stray God`` ->
  ``Noragami [Stray God]``, the owner's form; :func:`english_remainder`).
- **Characters** (animefetch's ``clean_name``, plus Windows' rules): typographic quotes, dashes, the ellipsis
  character and non-breaking spaces in plain ASCII (:func:`fold_typography`), ``:`` dropped (``Re:Zero`` -> ``ReZero``), ``/``
  -> ``-``, ``\\ * ? " < > |`` and control characters dropped, ``[ ]`` inside a part -> ``( )`` (the scheme's own
  brackets stay unambiguous), white space collapsed, no leading dot (a hidden folder is never scanned), no trailing dot
  or space, a Windows reserved name suffixed (:func:`mangalist.store.names.windows_safe_name`). Articles and other
  punctuation stay as the titles write them.
- **Length**: the name is at most :data:`MAX_FOLDER_BYTES` (255) bytes of UTF-8; when a Windows server name is set the
  folder's whole Windows path must also leave room for a chapter file below it (:func:`fit_name`). A name too long is
  cut in this order: the English part (ending with ``…``, then left out), then the romaji.
- **Long paths** (owner, 2026-10-16, answer 85: "Cut the English for those."): when the full path of the series'
  longest file - ``<library folder>/<folder name>/<file>`` as MangaList sees it, the file under the name the scheme
  gives it (a file in a subfolder with its subfolder) - would be longer than :data:`MAX_PATH_CHARS` (255) characters
  with the ``[English]`` part, the part is left out for that series (whole, never cut with ``…``). The caller works
  out the room the name has (:func:`name_room`) and passes it as *max_chars* to :func:`scheme_name`. 255 characters
  exactly still keeps the English part.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence, Tuple

LONG_TITLE = 50                 # animefetch's LONG_TITLE: a longer romaji title uses an official short name
MAX_FOLDER_BYTES = 255
MAX_PATH_CHARS = 255            # answer 85: a longer path of the series' longest file loses the folder's [English] part
ELLIPSIS = "…"
#: The shortest file name the length rule must leave room for under the folder (a chapter in the file scheme).
PROBE_FILE = "Ch. 0000.00 Vol. 000.cbz"

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_DROPPED = re.compile(r'[\\*?"<>|]')
_SPACES = re.compile(r"\s+")
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

#: Typographic punctuation folded to plain ASCII (integrator, 2026-10-16: AniList's curly apostrophes and dashes must
#: never rename a folder by themselves): quotes, apostrophes, dashes, the ellipsis character, non-breaking spaces.
_TYPOGRAPHY = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'", "\u2032": "'", "\u02bc": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"', "\u2033": '"',
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-", "\u2015": "-", "\u2212": "-",
    "\u2026": "...", "\u00a0": " ", "\u202f": " ", "\u2007": " ", "\u2009": " ",
})


def fold_typography(text: Optional[str]) -> str:
    """*text* with typographic quotes, apostrophes, dashes, the ellipsis character and non-breaking spaces written in
    plain ASCII (``Let’s Run — An Inn…`` -> ``Let's Run - An Inn...``)."""
    return (text or "").translate(_TYPOGRAPHY)


def clean_part(text: Optional[str]) -> str:
    """One part of the folder name (the romaji or the English title) in the scheme's characters: see the module
    docstring. Empty when nothing usable is left."""
    t = fold_typography(unicodedata.normalize("NFC", text or ""))
    # a subtitle's colon (": ") becomes " - " like the owner's own names and Add series' cleaning ("My Monster Secret -
    # Actually, I Am", not "...Secret Actually, I Am"); a colon inside a word goes, as animefetch does ("ReZero")
    t = _CONTROL.sub(" ", t)
    t = re.sub(r"\s*:(?=\s)", " -", t).replace(":", "").replace("/", "-")
    t = _DROPPED.sub("", t).replace("[", "(").replace("]", ")")
    t = _SPACES.sub(" ", t).strip().lstrip(".").strip()
    return t.rstrip(". ")


_ACRONYM = re.compile(r"^[A-Z0-9&.\-]+$")
_NOT_A_NAME = re.compile(r"[()\[\]{}]")
MIN_SHORT_LETTERS = 4           # an official short name has at least this many letters ("Bofuri", "KakaKano")


def _letters(text: str) -> int:
    return sum(c.isalpha() for c in text)


def _short_tier(name: str, full: str) -> Optional[int]:
    """How a short-name candidate relates to the full romaji, best first; None: it does not look like a short romaji
    name at all (an English translation such as ``Garbage Hero`` or ``Devil's New Wife``, which reads like neither).

    0. the romaji's own head, ending at a word (``Garbage Brave`` for ``Garbage Brave: Isekai ni ...``);
    1. it reads as romaji (``Mikkakan no Koufuku``) or shares a word of four letters or more with the romaji
       (``Kimi no Hoshi`` for ``Hoshi o Miru Hi ...``);
    2. a single word - the coined short names MangaUpdates lists (``KakaKano``, ``RoshiDere``, ``Oregairu``)."""
    key, full_key = name.casefold(), full.casefold()
    if full_key.startswith(key) and (len(full_key) == len(key) or not full_key[len(key)].isalnum()):
        return 0
    full_words = {w for w in _words(full) if len(w) >= 4 and w not in ENGLISH_WORDS}
    if romaji_like(name) or any(w in full_words for w in _words(name)):
        return 1
    if len(name.split()) == 1:
        return 2
    return None


def short_name_candidates(title: Optional[str], english: Optional[str] = None,
                          alt_titles: Iterable[str] = ()) -> Sequence[str]:
    """Official short names for a long romaji *title*, best first (animefetch's ``short_name_candidates``, adapted to
    MangaUpdates: it lists every language's titles together and labels none of them, where animefetch reads MAL's
    short synonyms):

    - an alternative title that looks like a short romaji name: ASCII only, at most four words, shorter than the full
      title, at least :data:`MIN_SHORT_LETTERS` letters, no brackets (``Beast Tamer (AUTHOR)`` is a disambiguation,
      not a name), not all capitals (an acronym), not reading as English (:func:`looks_english`) nor holding the
      English title - unless it IS the English title (owner, answer 86: the folder is then that name alone, never
      ``Tearmoon Empire [Tearmoon Empire]``), and related to the romaji (:func:`_short_tier`: its head, romaji words or a shared word, or one
      coined word). Cleaned like the romaji (``DanMachi: Side Story`` -> ``DanMachi Side Story``);
    - the part before a colon of an English title, when it is one or two words (``BOFURI: I Don't Want to Get Hurt...``
      -> ``Bofuri``: an all-capitals head longer than three letters is written in title case, as animefetch does). The
      English titles are the series' English title, the alternative titles that read as English (MangaUpdates lists
      its English titles among them, unlabelled) and an alternative title that is ``<head>: <the English title>``
      (``Made-sama: Love Story`` with the English title ``Love Story`` -> ``Made-sama``).

    Order (deterministic): the relation to the romaji (:func:`_short_tier`: its head first, then romaji-like or
    sharing a word, then one coined word), then the shortest, then alphabetical (case-folded), then as written. An
    empty list: the series has no official short name and keeps its full romaji."""
    full = clean_part(title)
    if not full:
        return []
    eng = clean_part(english) if english else ""
    alts = [a.strip() for a in alt_titles or () if isinstance(a, str) and a.strip()]
    found: dict = {}

    def offer(text: str) -> None:
        name = clean_part(text)
        if (not name or not name.isascii() or len(name) >= len(full) or len(name.split()) > 4
                or _letters(name) < MIN_SHORT_LETTERS or _NOT_A_NAME.search(name) or same_title(name, full)
                or looks_english(name) or (eng and title_key(eng) in title_key(name) and not same_title(name, eng))):
            return
        tier = _short_tier(name, full)
        if tier is not None and not any(same_title(name, k) for k in found):
            found[name] = tier

    for alt in alts:
        if not _ACRONYM.match(alt) and not _NOT_A_NAME.search(alt.replace(":", "")):
            offer(alt)
    english_titles = [english] + [a for a in alts if looks_english(a)
                                  or (eng and ":" in a and same_title(a.split(":", 1)[1], eng))]
    for source in english_titles:
        if source and ":" in source:
            head = source.split(":", 1)[0].strip()
            if head and len(head.split()) <= 2:
                offer(head[:1] + head[1:].lower() if head.isupper() and len(head) > 3 else head)
    return sorted(found, key=lambda n: (found[n], len(n), n.casefold(), n))


def short_romaji(title: Optional[str], english: Optional[str] = None, alt_titles: Iterable[str] = ()) -> str:
    """The romaji part of the folder name: *title* cleaned; when it is longer than :data:`LONG_TITLE` characters, the
    series' first official short name (:func:`short_name_candidates`) instead, else the full title - never cut."""
    full = clean_part(title)
    if len(full) > LONG_TITLE:
        names = short_name_candidates(title, english, alt_titles)
        if names:
            return clean_part(names[0])
    return full


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


def scheme_name(romaji: Optional[str], english: Optional[str] = None, *, fits=None,
                alt_titles: Iterable[str] = (), max_chars: Optional[int] = None) -> Optional[str]:
    """The scheme's folder name for a series called *romaji* (its MangaUpdates title) with the English title *english*
    (None: unknown) and the alternative titles *alt_titles* (where an official short name of a long romaji comes from:
    :func:`short_romaji`); None without a usable romaji title. The bracket is left out when the English title is the
    romaji, its short form, or the short name is the English title (answer 86), written differently; and when the name
    with it would be longer than *max_chars* characters (answer 85: the room :func:`name_room` leaves for the folder
    name below the path limit; None: no path limit)."""
    full = clean_part(romaji)
    if not full:
        return None
    short = short_romaji(romaji, english, alt_titles)
    eng = clean_part(english_remainder(english, (full, short))) if english else ""
    if eng and (same_title(eng, short) or same_title(eng, full)):
        eng = ""
    if eng and max_chars is not None and len(_windows_safe(_render(short, eng))) > max_chars:
        eng = ""
    return fit_name(short, eng or None, fits) or None


_SUBTITLE_SEP = re.compile(r"^(?:\s*[:：]\s*|\s+-\s+|\s*[~～]\s*)")


def english_remainder(english: Optional[str], romaji_parts: Iterable[str]) -> str:
    """*english* without a repeated title in front (integrator, 2026-10-16: AniList's ``Noragami: Stray God`` in a
    ``Noragami`` folder is ``Noragami [Stray God]``, the owner's own form): when it starts with one of *romaji_parts*
    (the full romaji, the short name; compared like :func:`same_title`) followed by a subtitle separator (``: ``,
    `` - ``, `` ~``, ``～``), only what follows. A title that merely goes on (``Grand Blue Dreaming`` for ``Grand
    Blue``) is kept whole. Typography folded first (:func:`fold_typography`); "" when nothing meaningful remains."""
    eng = fold_typography(english).strip()
    for part in sorted({p for p in romaji_parts if p and title_key(p)}, key=len, reverse=True):
        want = title_key(part)
        for i in range(1, len(eng)):
            head = title_key(eng[:i])
            if head == want and eng[i].isalnum() is False:
                m = _SUBTITLE_SEP.match(eng[i:])
                if m:
                    rest = eng[i + m.end():].strip(" ~～-:：")
                    return rest if title_key(rest) and rest != eng else ""
            if len(head) > len(want):
                break
    return eng


def name_room(root_path: Optional[str], longest_file: Optional[str]) -> Optional[int]:
    """How many characters a series folder's name may have so that ``<root_path>/<name>/<longest_file>`` stays within
    :data:`MAX_PATH_CHARS` (answer 85); *longest_file* is the longest path of a file below the folder, relative to it
    (``Vol. 001 [Group].cbz``, ``Extras/Side Story.cbz``). None without a library folder or a file."""
    if not root_path or not longest_file:
        return None
    return MAX_PATH_CHARS - len(str(root_path).rstrip("/\\")) - len(longest_file) - 2


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


def _after_the_title(name: str, bracket: str, romaji: Optional[str]) -> bool:
    """The folder is the whole romaji title and then this ``[bracket]``: the owner's English, even when it repeats a
    piece of the romaji (owner's ``Long Title ~Subtitle~ [Long Title]``: read as part of the title, the whole folder
    name was taken for the English and renamed to ``Long Title ~Subtitle~ [(Long Title)]``, 2026-10-18)."""
    return bool(romaji) and same_title(name.replace(f"[{bracket}]", " "), romaji)


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
        if text and (not _in_title(text, romaji) or _after_the_title(name, bracket, romaji)):
            return text
    if not top_level or not name:
        return None
    alt_titles = tuple(alt_titles or ())
    if romaji and (same_title(name, romaji) or same_title(name, short_romaji(romaji, None, alt_titles))):
        return None
    latin_alts = [a for a in alt_titles if isinstance(a, str) and latin(a)]
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


ENGLISH_OFFICIAL, ENGLISH_FOLDER, ENGLISH_OTHER = "official", "folder", "other"   # where SeriesTitles.english came from


@dataclass(frozen=True)
class SeriesTitles:
    """The titles a series folder is named by: *romaji* (MangaUpdates' title) and *english* (None: not known);
    *english_from* says where the English title came from (:data:`ENGLISH_OFFICIAL`: AniList's official title,
    :data:`ENGLISH_FOLDER`: the folder's name, :data:`ENGLISH_OTHER`: a title the caller knew, e.g. MangaDex's)."""

    romaji: str
    english: Optional[str] = None
    source: str = ""                 # where the romaji came from ("MangaPixer", "MangaUpdates match", ...)
    alt_titles: Tuple[str, ...] = ()  # the record's alternative titles (an official short name of a long romaji)
    english_from: str = ""

    def name(self, fits=None, max_chars: Optional[int] = None) -> Optional[str]:
        return scheme_name(self.romaji, self.english, fits=fits, alt_titles=self.alt_titles, max_chars=max_chars)


def titles_for(folder_name: str, romaji: Optional[str], alt_titles: Iterable[str] = (), *, top_level: bool = True,
               english: Optional[str] = None, source: str = "", official: Optional[str] = None
               ) -> Optional[SeriesTitles]:
    """A series' :class:`SeriesTitles`: *romaji* from its record; the English title is *official* (AniList's official
    English title, :mod:`mangalist.english_titles`) when known, else the folder's (:func:`english_from_folder`), else
    *english* (a title the caller knows, e.g. MangaDex's only English title). None without a romaji title (the folder
    cannot be named: it is left alone)."""
    if not clean_part(romaji):
        return None
    alts = tuple(a.strip() for a in alt_titles or () if isinstance(a, str) and a.strip())
    eng = english_from_folder(folder_name, romaji, alts, top_level=top_level) if folder_name else None
    parts = (clean_part(romaji), short_romaji(romaji, official, alts)) if official else ()
    if official and clean_part(official) and not any(same_title(official, p) for p in parts):
        # AniList's "English" title that is only the romaji (or its short name) again is no English title: the next
        # source is asked. The folder's own English that is the same title (case and punctuation aside) keeps the
        # owner's spelling: casing or a hyphen alone never renames a folder.
        if eng and (same_title(eng, official) or same_title(eng, english_remainder(official, parts))):
            return SeriesTitles(str(romaji).strip(), eng, source, alts, ENGLISH_OFFICIAL)
        return SeriesTitles(str(romaji).strip(), official.strip(), source, alts, ENGLISH_OFFICIAL)
    if eng:
        return SeriesTitles(str(romaji).strip(), eng, source, alts, ENGLISH_FOLDER)
    other = english.strip() if english and english.strip() else None
    return SeriesTitles(str(romaji).strip(), other, source, alts, ENGLISH_OTHER if other else "")


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
    "ELLIPSIS", "ENGLISH_FOLDER", "ENGLISH_OFFICIAL", "ENGLISH_OTHER", "ENGLISH_WORDS", "LONG_TITLE",
    "MAX_FOLDER_BYTES", "MAX_PATH_CHARS", "ROMAJI_WORDS", "name_room", "SeriesTitles", "english_remainder", "fold_typography", "clean_part",
    "english_candidates", "english_from_folder", "fit_name", "looks_english", "parse_folder", "romaji_like",
    "same_title", "scheme_name", "short_name_candidates", "short_romaji", "title_key", "titles_for", "windows_fits",
]

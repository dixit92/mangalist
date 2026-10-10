"""Small helpers shared by the parser layers."""

from __future__ import annotations

import re
from typing import Optional, Tuple

ARCHIVE_EXTENSIONS = frozenset({
    ".cbz", ".cbr", ".cb7", ".cbt", ".zip", ".rar", ".7z", ".tar", ".pdf", ".epub",
})

# A unit number: whole digits plus any number of decimals, kept as text (Decimal later).
UNIT = r"\d+(?:\.\d+)?"


def split_extension(name: str) -> Tuple[str, str]:
    """``("0012 [Ch. 12.5]", ".cbz")`` for ``"0012 [Ch. 12.5].cbz"``. Only archive extensions are split
    off, so a name without one keeps its decimals (``"Ch. 12.5"`` stays whole)."""
    stem, dot, ext = name.rpartition(".")
    if dot and stem and f".{ext}".lower() in ARCHIVE_EXTENSIONS:
        return stem, f".{ext}"
    return name, ""


def trailing_bracket(text: str) -> Optional[Tuple[str, str]]:
    """Split a trailing balanced ``[...]`` off ``text``: ``("Title", "Team [X]")`` for
    ``"Title [Team [X]]"``; None when ``text`` does not end in a balanced bracket."""
    s = text.rstrip()
    if not s.endswith("]"):
        return None
    depth = 0
    for i in range(len(s) - 1, -1, -1):
        ch = s[i]
        if ch == "]":
            depth += 1
        elif ch == "[":
            depth -= 1
            if depth == 0:
                return s[:i].rstrip(), s[i + 1:-1]
    return None


def encloses_whole(body: str) -> bool:
    """``body`` (the text between an opening ``[`` and the final ``]``) never closes that bracket early:
    ``"Ch. 1 - T [G]"`` yes, ``"Ch. 1] [G"`` no."""
    depth = 0
    for ch in body:
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


_LEADING_SEPARATOR = re.compile(r"^\s*(?:-|\u2013|\u2014|:|\uff1a|_)?\s*")


def clean_title(text: str) -> Optional[str]:
    """A chapter title without its leading separator (`` - ``, ``:``, ``_``); None when empty."""
    t = _LEADING_SEPARATOR.sub("", text, count=1).strip()
    return t or None


EXTRA_WORDS = re.compile(
    r"(?<![A-Za-z])(?:extras?|omake|specials?|side[\s_-]*stor(?:y|ies)|bonus|afterword)(?![A-Za-z])",
    re.IGNORECASE)


# Words some sites use for a chapter (owner's library, 2026-10-10: "Contact. 0001", "episode 0035", "report011",
# "Pact 0015" ...): a label followed by a number is that chapter. "Ch." / "Chapter" are the classifier's own tokens
# and are not listed here; neither are "Part" (arcs, series parts), "Story" ("Side Story 2" is an extra) and "#"
# ("Title #3" stays a bare number: issue or volume?).
CHAPTER_LABELS = (
    r"(?:contact|episodes?|episodio|epis\u00f3dio|eps?|report|pact|act|lesson|stage|round|night|scene|phase|step"
    r"|level|file|case|mission|quest|trip|log|karte|bout|sequence|track|song|cap(?:[i\u00ed]tulo)?)"
)

# A text that ends in a unit word without its number ("Vol.", "Chapter"): the number after it is that unit's, so a
# bare number there is never guessed to be a chapter ("Vol. 01" as a folder name).
ENDS_IN_UNIT_WORD = re.compile(r"(?<![A-Za-z])(?:v|vol(?:ume)?|ch(?:apter|ap|p)?|c)\.?$", re.IGNORECASE)

# Group values that mean "no group" (MangaDex writes "no group" when a chapter has none; renamer dry run, 2026-10-10:
# 151 names would have kept "[no group]"). Compared case-insensitively against the whole value.
PLACEHOLDER_GROUPS = frozenset({
    "no group", "no-group", "nogroup", "no groups", "no scanlator", "no scanlation group", "unknown", "unknown group",
    "none", "n/a", "na", "null", "nil", "-", "?", "tbd",
})


def real_group(group: Optional[str]) -> Optional[str]:
    """``group`` unless it is empty or a placeholder (``[no group]``, ``(Unknown)``, ``N/A``): then None."""
    if group is None:
        return None
    g = group.strip()
    return None if not g or " ".join(g.casefold().split()) in PLACEHOLDER_GROUPS else g


# A season / part of the series stated in a name ("<Series> Season 2 v01", "<Series> - Part 5 - <Subtitle> v05"):
# volume numbers restart with it, so the volume name keeps it (renamer dry run, 2026-10-10: seasons collapsed onto
# one "Vol. 001"). It must end the series text or be followed by " - <subtitle>".
QUALIFIER = re.compile(r"(?<![A-Za-z])(?P<word>season|part)\s*(?P<n>\d+(?:\.\d+)?)(?=\s*$|\s*[-\u2013\u2014:]\s)",
                       re.IGNORECASE)


def series_qualifier(series: Optional[str]) -> Optional[str]:
    """The last "Season N" / "Part N" of a series text, normalised (``"season 02"`` -> ``"Season 2"``); None
    without one."""
    if not series:
        return None
    found = list(QUALIFIER.finditer(series))
    if not found:
        return None
    m = found[-1]
    whole, _, frac = m.group("n").partition(".")
    frac = frac.rstrip("0")
    return f"{m.group('word').capitalize()} {int(whole)}{'.' + frac if frac else ''}"


# A name cut off after a chapter word ("0001 [Vol. 0001 Ch.cbz"): its chapter number is lost.
DANGLING_CHAPTER = re.compile(r"(?<![A-Za-z])(?:ch(?:apter|ap|p)?)\.?\s*\]*\s*$", re.IGNORECASE)

# Words that open an FMD2 bracket without being a chapter label: units of something else, and extras.
NOT_CHAPTER_LABELS = frozenset({
    "v", "vol", "volume", "volumes", "season", "seasons", "part", "parts", "tome", "book", "books", "arc", "cour",
    "saga", "series", "issue", "extra", "extras", "omake", "special", "specials", "side", "bonus", "afterword",
    "oneshot", "one", "epilogue", "prologue", "interlude", "intermission",
})

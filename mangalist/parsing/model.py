"""Result model of the layered parser: units read from one archive name.

Numbers are :class:`decimal.Decimal`, never float: ``291.999``, ``12.5`` and ``3.10`` keep exactly the
digits the name gives (``Decimal("3.10")`` prints ``3.10``); leading zeros are dropped (``0012`` -> ``12``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Optional, Tuple, Union


class Kind(str, Enum):
    """What one archive holds.

    - ``VOLUME``: whole volume(s) and nothing else.
    - ``CHAPTER``: chapter(s); a volume number, when present, is the volume the chapter belongs to
      (``Vol. 3 Ch. 12`` is one chapter of volume 3).
    - ``BOTH``: whole volume(s) plus loose chapters in one archive (``Title v10 + 085-086``).
    - ``UNKNOWN``: no units, or a bare number whose kind the caller did not decide.
    """

    VOLUME = "volume"
    CHAPTER = "chapter"
    BOTH = "both"
    UNKNOWN = "unknown"


class Layer(str, Enum):
    """The parser layer that produced a result (diagnostics), in precedence order."""

    SCHEME = "scheme"      # 1. the root's own naming scheme, inverted
    FMD2 = "fmd2"          # 2. FMD2 exact: NNNN [ ... ] / Title - NNNN [ ... ]
    RELEASE = "release"    # 3. release names: Title vNN (Year) (Digital) (Group)
    LABELLED = "labelled"  # 3b. a site's own chapter label: Title - Episode 35, Title [Pact 0015]
    GENERIC = "generic"    # 4. today's classifier tokens, unchanged
    BARE = "bare"          # 5. bare numbers: 01.cbz, Title 01.cbz
    NONE = "none"          # nothing recognised


def to_decimal(text: Union[str, int, Decimal, None]) -> Optional[Decimal]:
    """A unit number as a Decimal (``"0012.50"`` -> ``Decimal("12.50")``); None for empty / invalid."""
    if text is None:
        return None
    if isinstance(text, Decimal):
        return text
    if isinstance(text, int):
        return Decimal(text)
    t = str(text).strip()
    if not t:
        return None
    try:
        d = Decimal(t)
    except InvalidOperation:
        return None
    if not d.is_finite():
        return None
    # Drop leading zeros of the whole part but keep every digit after the point.
    whole, _, frac = format(d, "f").partition(".")
    whole = whole.lstrip("0") or "0"
    return Decimal(f"{whole}.{frac}") if frac else Decimal(whole)


def plain(d: Decimal) -> str:
    """A Decimal as plain digits (no exponent): ``Decimal("12.50")`` -> ``"12.50"``."""
    return format(d, "f")


@dataclass(frozen=True)
class UnitRange:
    """One unit number or an inclusive range (``v01-03``, ``Ch. 10-12``). ``end == start`` for one unit."""

    start: Decimal
    end: Decimal

    @classmethod
    def of(cls, start: Union[str, int, Decimal], end: Union[str, int, Decimal, None] = None) -> "UnitRange":
        s = to_decimal(start)
        if s is None:
            raise ValueError(f"not a unit number: {start!r}")
        e = to_decimal(end) if end is not None else None
        return cls(s, e if e is not None else s)

    @classmethod
    def maybe(cls, start: Optional[str], end: Optional[str] = None) -> Optional["UnitRange"]:
        """``of`` for optional regex groups; None when ``start`` is missing."""
        if start is None or to_decimal(start) is None:
            return None
        return cls.of(start, end if end is not None and to_decimal(end) is not None else None)

    @property
    def is_range(self) -> bool:
        return self.end != self.start

    @property
    def whole(self) -> int:
        """The whole part of the start (``12.5`` -> 12)."""
        return int(plain(self.start).partition(".")[0])

    @property
    def fraction(self) -> str:
        """The fraction of the start as written, with its point (``12.5`` -> ``".5"``, ``3.10`` -> ``".10"``,
        ``12`` -> ``""``)."""
        frac = plain(self.start).partition(".")[2]
        return f".{frac}" if frac else ""

    def __str__(self) -> str:
        return plain(self.start) if not self.is_range else f"{plain(self.start)}-{plain(self.end)}"


@dataclass(frozen=True)
class ParsedName:
    """Units read from one archive file name.

    ``index`` is FMD2's numbering index (its download-list position, ``%NUMBERING%``), never the chapter
    number. ``number`` is a bare number whose kind is not stated in the name (``01.cbz``); when the
    caller's kind hint decides it, the same range is also put into ``volume`` or ``chapter``. A bare
    number after the series title (``Title 07.cbz``) with no hint is read as a chapter by guess
    (``guessed``; Next Cycle Design section 10, 2026-10-10: most such files in the owner's library are
    chapters) - a "volumes" answer turns it into a volume.
    """

    name: str                                   # the file name as given
    kind: Kind = Kind.UNKNOWN
    layer: Layer = Layer.NONE
    volume: Optional[UnitRange] = None
    chapter: Optional[UnitRange] = None
    number: Optional[UnitRange] = None          # bare number (kind not stated in the name)
    is_extra: bool = False                      # an extra / special / omake without its own chapter number
    title: Optional[str] = None                 # chapter (or volume) title, never read for numbers
    group: Optional[str] = None                 # scanlation / release group
    index: Optional[int] = None                 # FMD2 numbering index (separate from the chapter number)
    series: Optional[str] = None                # series title written in the name (release / "Title - NNNN")
    year: Optional[int] = None
    edition: Optional[str] = None               # "Digital", "Digital-Compilation", ...
    fix: Optional[str] = None                   # "f", "f2" (release fix suffix)
    tags: Tuple[str, ...] = ()                  # every trailing (...) / [...] tag of a release name, as written
    guessed: bool = False                       # the kind is a guess (a bare number after the series title read
                                                # as a chapter); the series' "volumes or chapters?" answer settles it
    ambiguous: bool = False                     # the generic layer found several volume or chapter numbers
    notes: Tuple[str, ...] = field(default=(), compare=False)  # diagnostics: why this layer read it so

    @property
    def is_fraction(self) -> bool:
        """The chapter number has a fractional part (``12.5``, ``291.999``)."""
        return self.chapter is not None and self.chapter.fraction != ""

    @property
    def has_units(self) -> bool:
        return self.volume is not None or self.chapter is not None or self.number is not None

    @property
    def legacy_kind(self) -> str:
        """The kind in today's ``FileHit.kind`` vocabulary: ``"chapter"`` / ``"volume"`` / ``"ambiguous"``
        (chapter wins, as in ``Vol. X Ch. Y``)."""
        if self.kind in (Kind.CHAPTER, Kind.BOTH):
            return "chapter"
        if self.kind is Kind.VOLUME:
            return "volume"
        return "ambiguous"

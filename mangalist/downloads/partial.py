"""Partial downloads: which files of a pack hold the volumes the owner is missing. No Qt, no I/O.

A pack (``Series v01-23``) is often much bigger than the three volumes a series lacks. :func:`choose_files` maps every
file of the torrent to the volume(s) it holds - with the same parser and the same reading of names the arrivals pass
files by (:func:`mangalist.downloads.arrivals.volumes_of`), so what is downloaded is what will be filed - and decides
which files to keep. The rules, in order, for one file:

1. Not a volume archive (a cover image, a text file) -> skipped. Arrivals files archives only.
2. Holds a wanted volume -> kept. A file that holds a wanted volume *and* one the owner has (``v02-03`` when only 3 is
   missing) is kept too, and said so: it is a file that might hold a wanted volume, and nothing is dropped silently.
3. Holds volumes, none wanted -> skipped.
4. Names no volume: an extra / omake or chapter files -> skipped (they are not whole volumes); anything else - a name
   the parser cannot read - is KEPT, and said so.

When no file is positively a wanted volume (a numberless pack, names nothing like the usual) the choice does not narrow
at all: the whole pack is kept, with the reason. The selection is computed twice with the same function: from the
``.torrent`` read from nyaa (the preview in the release panel) and from the file list qBittorrent reports (the
priorities actually set).
"""

from __future__ import annotations

import logging
import posixpath
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Sequence, Tuple

from ..parsing import Kind, ParseContext, parse_name
from ..parsing.model import plain
from ..scanner import ARCHIVE_EXTS
from .arrivals import volumes_of
from .contracts import NyaaCandidate, TorrentFile

_log = logging.getLogger(__name__)

MAX_LOGGED_FILES = 60       # more files than this: the per-file lines are left out of the log, the summary stays


@dataclass(frozen=True)
class FileChoice:
    name: str
    size: int
    volumes: Tuple[str, ...]            # exact decimal strings, ascending ('' when the name holds no volume)
    keep: bool
    reason: str                         # plain words, shown in the log and the tooltip
    unknown: bool = False               # kept only because the name could not be read


@dataclass(frozen=True)
class PackSelection:
    """The files of one pack and which to keep for *wanted* volumes. ``problem`` is set instead of files when the
    file list could not be read (the whole pack is then downloaded); ``whole_reason`` says why a readable list is
    kept whole."""

    files: Tuple[FileChoice, ...] = ()
    wanted: Tuple[str, ...] = ()
    problem: Optional[str] = None
    whole_reason: Optional[str] = None

    @property
    def readable(self) -> bool:
        return self.problem is None

    @property
    def total_files(self) -> int:
        return len(self.files)

    @property
    def kept_files(self) -> int:
        return sum(1 for f in self.files if f.keep)

    @property
    def skipped_files(self) -> int:
        return self.total_files - self.kept_files

    @property
    def total_bytes(self) -> int:
        return sum(f.size for f in self.files)

    @property
    def kept_bytes(self) -> int:
        return sum(f.size for f in self.files if f.keep)

    @property
    def unknown_kept(self) -> int:
        return sum(1 for f in self.files if f.unknown)

    @property
    def narrows(self) -> bool:
        """True when some files would not be downloaded."""
        return self.readable and self.skipped_files > 0

    @property
    def not_found(self) -> Tuple[str, ...]:
        """Wanted volumes no file names (only worth saying when every name was readable)."""
        held = {v for f in self.files for v in f.volumes}
        return tuple(v for v in self.wanted if v not in held)


@dataclass(frozen=True)
class PackOutcome:
    """What a send did with a pack (the release panel says it): ``partial`` when files were skipped."""

    partial: bool
    selection: PackSelection
    note: str = ""                      # why the whole pack was sent, when it was


def hint_for(candidate: NyaaCandidate) -> Optional[str]:
    """The kind hint arrivals reads the torrent's names with: a release whose title states volumes holds volumes."""
    return "volumes" if candidate.vol_from else None


def _fmt(values: Sequence[Decimal]) -> str:
    return ", ".join("v" + plain(v) for v in sorted(values))


def _classify(name: str, size: int, wanted: set, hint: Optional[str]) -> FileChoice:
    base = posixpath.basename(name)
    if posixpath.splitext(base)[1].lower() not in ARCHIVE_EXTS:
        return FileChoice(name, size, (), False, "not a volume archive")
    vols = volumes_of(base, hint)
    if vols:
        texts = tuple(plain(v) for v in sorted(vols))
        mine, other = vols & wanted, vols - wanted
        if mine and other:
            return FileChoice(name, size, texts, True,
                              f"holds {_fmt(mine)} (wanted) and {_fmt(other)} (not wanted); kept so nothing wanted is lost")
        if mine:
            return FileChoice(name, size, texts, True, f"holds {_fmt(mine)} (wanted)")
        return FileChoice(name, size, texts, False, f"holds {_fmt(vols)}, not wanted")
    parsed = parse_name(base, ParseContext(kind_hint=hint))
    if parsed.is_extra:
        return FileChoice(name, size, (), False, "an extra, not a volume")
    if parsed.kind is Kind.CHAPTER:
        return FileChoice(name, size, (), False, "chapter files, not a volume")
    return FileChoice(name, size, (), True, "kept: its name does not say which volume it holds", unknown=True)


def choose_files(files: Sequence[Tuple[str, int]], wanted_volumes: Sequence[str],
                 hint: Optional[str] = None) -> PackSelection:
    """Decide which of *files* (``(name, size)`` pairs, names '/'-separated) to keep for *wanted_volumes* (exact
    decimal strings). The choices come back in the order given."""
    wanted_set = {Decimal(str(v)) for v in wanted_volumes if str(v).strip()}
    wanted = tuple(plain(v) for v in sorted(wanted_set))
    choices = tuple(_classify(name, size, wanted_set, hint) for name, size in files)
    if not choices:
        return PackSelection((), wanted, problem="the torrent lists no files")
    if not any(c.keep and c.volumes for c in choices):
        reason = ("none of the files names a missing volume, so the whole pack is kept" if wanted
                  else "the missing volumes are not known, so the whole pack is kept")
        kept = tuple(FileChoice(c.name, c.size, c.volumes, True, c.reason, c.unknown) for c in choices)
        return PackSelection(kept, wanted, whole_reason=reason)
    return PackSelection(choices, wanted)


def choose_from_live(files: Sequence[TorrentFile], wanted_volumes: Sequence[str],
                     hint: Optional[str] = None) -> PackSelection:
    """:func:`choose_files` over qBittorrent's own file list (same order, so choice *i* is file *i*)."""
    return choose_files([(f.name, f.size) for f in files], wanted_volumes, hint)


def priority_changes(live: Sequence[TorrentFile], selection: PackSelection) -> Tuple[Tuple[int, ...], Tuple[int, ...]]:
    """``(skip, enable)``: the ids of the files to set to "do not download", and the kept files that are at 0 and
    need to be set to normal. Only the files that are not already so."""
    skip, enable = [], []
    for position, (f, choice) in enumerate(zip(live, selection.files)):
        fid = f.index if f.index >= 0 else position         # a client that reports no id: the list position
        if not choice.keep and f.priority != 0:
            skip.append(fid)
        elif choice.keep and f.priority == 0:
            enable.append(fid)
    return tuple(skip), tuple(enable)


def log_selection(title: str, selection: PackSelection, where: str) -> None:
    """One summary line, then one line per file (every step is logged; a very long list only gets the summary)."""
    if not selection.readable:
        _log.info("Partial: %s (%s): file list not available: %s", title, where, selection.problem)
        return
    _log.info("Partial: %s (%s): %d of %d files kept (%d of %d bytes) for volumes %s%s", title, where,
              selection.kept_files, selection.total_files, selection.kept_bytes, selection.total_bytes,
              ", ".join(selection.wanted) or "none",
              f"; {selection.whole_reason}" if selection.whole_reason else "")
    if selection.total_files <= MAX_LOGGED_FILES:
        for f in selection.files:
            _log.info("Partial: %s: %s %s (%d bytes): %s", title, "keep" if f.keep else "skip", f.name, f.size, f.reason)


__all__ = ["FileChoice", "MAX_LOGGED_FILES", "PackOutcome", "PackSelection", "choose_files", "choose_from_live",
           "hint_for", "log_selection", "priority_changes"]

"""Filing a replacement: an other release sent to replace the owner's copies (owner, 2026-10-24). No Qt here.

The owner's request: "It should work similarly to upgrade, where you can replace a collection downloaded over time with an
updated collection of chapters/torrents which is more recent for example." Their answer for a file the release covers:
**"Replace, keep old"** - the release's file replaces the owner's and the OLD copy goes to the holding folder
(restorable); files the release does not cover stay.

A replacement is a download record with ``replace_volumes`` (the held volumes of its ``wanted_volumes`` the owner chose
to replace; the others it holds are added like missing ones). When its torrent has finished, the arrivals pass
(:meth:`mangalist.downloads.arrivals._Pass._file_replacement`) files it in this order:

1. **What is replaced** (:func:`plan_replacement`): the series' archives (its own and its nested rows', as the scan
   stored them) that hold volumes only, every one of them among ``replace_volumes`` AND among the volumes the release's
   filed files hold, each still a plain file in the library. A file holding anything else (chapters beside a volume, a
   volume the release does not file because the owner unticked it, a volume outside the chosen ones) is never replaced -
   and a release file whose volume such a file still holds is not filed (the volume stays as the owner has it).
2. **The old copies to the holding folder first** (:func:`mangalist.upgrades.hold_replaced_volumes`): one replaced-files
   batch, one journal plan of ``hold`` steps (no-replace, write-ahead, the root's lock) - the upgrades' own mechanism,
   so Restore / the holding period / Empty now work the same, and :meth:`~mangalist.store.journal.MoveFollower.held`
   tells the torrent-removal check that an earlier download's filed file was replaced on the owner's word. First,
   because the new file may take the old one's name (the naming scheme). The batch id is kept on the record, so a pass
   that stops in between resumes there.
3. **Then the release's files** are linked as any arrival's (one ``link`` plan; the old copies no longer count as held).
   Nothing filed at all: the batch is restored (the old copies put back).

Chapter files a filed volume covers then follow the upgrade rules as for any filed download
(:func:`mangalist.upgrades.after_filing` looks at the record afterwards).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Collection, Dict, FrozenSet, Iterable, List, Optional, Sequence, Set, Tuple

from ..scanner import ARCHIVE_EXTS
from .arrivals import _expand, volumes_of
from .placement import series_units


@dataclass(frozen=True)
class HeldFile:
    """One archive of the series as the scan stored it (``rel``: relative to the series folder)."""

    path: str
    rel: str
    volumes: FrozenSet[Decimal]
    pure: bool                          # every unit of it is a whole volume (no chapters, extras, unknowns)


def _key(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def held_files(db, series, series_dir: str) -> List[HeldFile]:
    """The series' archives with the volumes they hold (from the stored units of the series and its nested rows)."""
    by_rel: Dict[str, List] = {}
    for u in series_units(db, series):
        by_rel.setdefault(u.rel_path, []).append(u)
    out: List[HeldFile] = []
    for rel, units in sorted(by_rel.items()):
        vols: Set[Decimal] = set()
        pure = True
        for u in units:
            if u.kind != "volume" or u.vol_from is None:
                pure = False
                continue
            vols |= _expand(Decimal(u.vol_from), Decimal(u.vol_to) if u.vol_to is not None else None)
        out.append(HeldFile(os.path.join(series_dir, *rel.split("/")), rel, frozenset(vols), pure and bool(vols)))
    return out


def held_volumes_except(db, series, series_dir: str, away: Collection[str]) -> Set[Decimal]:
    """:func:`mangalist.downloads.arrivals.held_volumes` without the files at the paths in *away* (moved, or about to
    be moved, to the holding folder): the stored units of every other archive plus every other archive in the folder,
    read again now."""
    gone = {_key(p) for p in away}
    held: Set[Decimal] = set()
    for f in held_files(db, series, series_dir):
        if _key(f.path) not in gone:
            held |= f.volumes
    title = os.path.basename(series_dir)
    for folder, dirs, files in os.walk(series_dir):
        dirs[:] = [d for d in dirs if not d.startswith((".", "@", "#"))]
        for name in files:
            path = os.path.join(folder, name)
            if os.path.splitext(name)[1].lower() in ARCHIVE_EXTS and _key(path) not in gone:
                held |= volumes_of(name, series.kind_hint, title)
    return held


@dataclass
class ReplacementPlan:
    links: List[Tuple[str, str]]                # (torrent file, library name) to file
    notes: List[str]                            # why other torrent files are not filed
    away: List[HeldFile]                        # the owner's copies to the holding folder first
    replacing: List[Tuple[str, str]]            # those of *links* that stand in for an old copy

    @property
    def volumes(self) -> List[Decimal]:
        """The volumes whose old copies are replaced, ascending."""
        return sorted({v for f in self.away for v in f.volumes})


PickFn = Callable[[Set[Decimal], Collection[str]], Tuple[List[Tuple[str, str]], List[str]]]


def plan_replacement(files: Sequence[HeldFile], replace: Iterable[Decimal], pick: PickFn,
                     volumes_of_link: Callable[[str], Set[Decimal]],
                     held_without: Callable[[Collection[str]], Set[Decimal]],
                     exists: Callable[[str], bool] = os.path.isfile) -> ReplacementPlan:
    """Which old copies go and which release files are filed (see the module docstring, step 1). *pick(held, free)*:
    the arrivals pass's choice of torrent files against the volumes *held* (names at the paths in *free* count as
    free); *volumes_of_link(src)*: what a torrent file holds; *held_without(paths)*: the volumes still held once the
    files at *paths* are gone."""
    wanted = set(replace)
    away = [f for f in files if f.pure and f.volumes <= wanted and exists(f.path)]
    links: List[Tuple[str, str]] = []
    notes: List[str] = []
    for _ in range(len(away) + 2):                  # *away* only shrinks: this settles
        paths = [f.path for f in away]
        links, notes = pick(held_without(paths), {_key(p) for p in paths})
        covered: Set[Decimal] = set()
        for src, _dst in links:
            covered |= volumes_of_link(src)
        kept = [f for f in away if f.volumes <= covered]
        if len(kept) == len(away):
            break
        away = kept
    taken = {v for f in away for v in f.volumes}
    replacing = [(src, dst) for src, dst in links if volumes_of_link(src) & taken]
    return ReplacementPlan(links=links, notes=notes, away=away, replacing=replacing)


__all__ = ["HeldFile", "ReplacementPlan", "held_files", "held_volumes_except", "plan_replacement"]

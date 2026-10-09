"""The redesigned window's seams (UI cycle, owner-approved mockup 2026-10-08): what the shell (lane A), the Download
tab and Settings (lane B) and the duplicates view (lane C) pass between each other. Qt-free: plain data, plus the
signatures each lane provides, so the three can be built in parallel and wired at merge.

Provided by lane B
------------------
``mangalist.gui.download_tab.DownloadTab(QWidget)``
    ``__init__(self, backend: DownloadsBackend, parent=None)``
    ``set_wanted(self, series: Sequence[WantedSeries]) -> None`` - the "To get" list (the shell sends it after every scan / sync)
    ``focus(self, folder: str) -> None`` - select that series ("Get the missing volumes" in the List tab)
    signals ``count_changed(int)`` (the tab's badge), ``show_in_list(str)`` (a folder)
    ``stop(self) -> None`` - on close (abandon background calls)
``mangalist.gui.settings_dialog.open_settings(parent, db, backend, section=None) -> SettingsResult``
    One dialog, the SECTIONS below; *backend* is None where downloads are off (Download sources / qBittorrent
    then say so). Absorbs the Roots, MangaPixer and qBittorrent dialogs.

Provided by lane C
------------------
``mangalist.duplicates.find_duplicate_files(db, root_ids=None) -> List[DuplicateGroup]`` - Qt-free: MangaPixer's
    duplicate-numbers rule (same chapter / volume number in two files of ONE folder; split parts, ranges and number-less
    names are not duplicates) applied to the library database's units.
``mangalist.gui.duplicates_view.DuplicatesView(QWidget)``
    ``__init__(self, db, parent=None)``
    ``set_series_duplicates(self, groups: Sequence[DuplicateSeries]) -> None`` - series in more than one folder (the shell
    knows them from its table)
    ``refresh(self) -> None`` - re-read the duplicate files
    signals ``show_in_list(str)`` (a folder), ``files_deleted(list)`` (absolute paths; the shell rescans)
    Discarding deletes the chosen files after an explicit confirmation (owner, 2026-10-08: no holding folder).

Provided by lane A
------------------
The shell: top bar (List / Download tabs, Rescan, Settings), the List tab (state chips incl. "Duplicates", which shows
the DuplicatesView in place of the table), the collapsible details panel, the theme. It builds :class:`WantedSeries` from
the table model and hosts B's and C's widgets; until they merge, a placeholder stands in for each.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

# --- the Download tab's "To get" list -------------------------------------------------------------------

GROUP_VOLUMES = "volumes"       # Missing volumes (nyaa)
GROUP_CHAPTERS = "chapters"     # Missing chapters (Suwayomi, later)
GROUP_UPGRADES = "upgrades"     # volumes for chapters held (later)
GROUPS = (GROUP_VOLUMES, GROUP_CHAPTERS, GROUP_UPGRADES)


@dataclass(frozen=True)
class WantedSeries:
    """One series in the Download tab's "To get" list."""

    series_id: Optional[int]            # the library database's series row (None: not scanned yet)
    folder: str                         # absolute
    title: str
    group: str                          # GROUP_*
    gaps: str                           # display text, e.g. "Vol. 21-23", "Ch. 41-44", "Upgrade vol. 13"
    missing: Tuple[str, ...] = ()       # exact numbers (volumes for GROUP_VOLUMES / GROUP_UPGRADES, chapters otherwise)
    held: Tuple[str, ...] = ()          # volumes held (for nyaa's ranking)
    titles: Tuple[str, ...] = ()        # the search titles (English / folder names first, as volumes_target.search_titles)
    findable: bool = False              # releases can be searched now (MangaPixer-matched, licensed, downloads on, ...)
    reason: str = ""                    # why not, when not findable (e.g. "needs Suwayomi")


# --- Settings ---------------------------------------------------------------------------------------------

SECTION_LIBRARY = "library"
SECTION_SERVICES = "services"           # Connected services: MangaPixer, qBittorrent, Suwayomi
SECTION_SOURCES = "sources"             # Download sources: nyaa (needs qBittorrent), Suwayomi sources (needs Suwayomi)
SECTION_MATCHING = "matching"
SECTION_AUTOMATION = "automation"
SECTIONS = (SECTION_LIBRARY, SECTION_SERVICES, SECTION_SOURCES, SECTION_MATCHING, SECTION_AUTOMATION)


@dataclass(frozen=True)
class SettingsResult:
    roots_changed: bool = False         # the shell rescans
    mangapixer_changed: bool = False    # the shell re-reads MangaPixer's data
    downloads_changed: bool = False     # the Download tab reloads


# --- duplicates -------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class DuplicateFile:
    path: str                           # absolute
    size: int
    modified: str                       # ISO 8601
    group: Optional[str] = None         # scanlation / release group from the name, when it says


@dataclass(frozen=True)
class DuplicateGroup:
    """One number held by more than one file of one folder."""

    series_id: Optional[int]
    folder: str                         # absolute
    title: str
    kind: str                           # "chapter" | "volume"
    number: str                         # exact, e.g. "12", "12.5"
    files: Tuple[DuplicateFile, ...]
    usual_group: Optional[str] = None   # the group the neighbouring numbers come from, when the copies differ by group


@dataclass(frozen=True)
class DuplicateSeries:
    """One series held in more than one folder (the same MangaUpdates series)."""

    title: str
    folders: Tuple[str, ...]            # absolute

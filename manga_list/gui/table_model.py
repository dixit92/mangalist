"""QAbstractTableModel for the MangaEntry list."""

from __future__ import annotations

import datetime
from typing import List, Optional

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor

from ..models import MangaEntry
from ..handoff.wanted import is_omnibus_complete
from ..mu_match import match_tooltip, needs_review
from ..mu_progress import behind_sort_key, format_behind, format_behind_tooltip

COLUMNS = [
    "✓",
    "Dupe",  # Duplicate indicator
    "Title",
    "Alternative Title",
    "Files",
    "Subfolders",
    "Vol %",
    "Ch %",
    "Both %",
    "Verdict",
    "Last Modified",
    "MU Title",
    "Licensed",
    "Behind",
    "Completed",
]

COL_EXAMINED = 0
COL_DUPE = 1
COL_TITLE = 2
COL_ENG = 3
COL_FILES = 4
COL_SUBS = 5
COL_VOL = 6
COL_CH = 7
COL_BOTH = 8
COL_VERDICT = 9
COL_MTIME = 10
COL_MU_TITLE = 11
COL_LICENSED = 12
COL_BEHIND = 13
COL_COMPLETED = 14

# Saturated dark green for examined rows — contrasts strongly with white text
# on dark themes, distinct from the table's alternating rows and selection highlight.
EXAMINED_ROW_BG = QColor(38, 110, 55)      # saturated forest green
# Saturated orange for MU matches the matcher put in the review tier — opaque so it reads well on dark themes.
WEAK_MATCH_BG = QColor(196, 96, 30)         # saturated burnt orange
# Deep blue-purple highlight for the row currently being fetched from MU.
MU_PROCESSING_BG = QColor(60, 80, 160)      # deep blue-purple


class MangaTableModel(QAbstractTableModel):
    def __init__(self, entries: Optional[List[MangaEntry]] = None, parent=None):
        super().__init__(parent)
        self._entries: List[MangaEntry] = list(entries or [])
        self._mu_processing_row: Optional[int] = None
        # Map of mu_title -> list of row indices (for duplicate detection)
        self._dupe_map: dict[str, List[int]] = {}
        # Build initial dupe map if entries provided
        if self._entries:
            self._rebuild_dupe_map()

    # --- data plumbing -------------------------------------------------------

    def set_entries(self, entries: List[MangaEntry]) -> None:
        self.beginResetModel()
        self._entries = list(entries)
        self._mu_processing_row = None
        self._rebuild_dupe_map()
        self.endResetModel()

    def _rebuild_dupe_map(self) -> None:
        """Build a map of mu_title -> list of row indices with that MU title."""
        self._dupe_map.clear()
        for i, e in enumerate(self._entries):
            if e.mu_title is not None:
                # Normalize for comparison (case-insensitive, strip whitespace)
                key = e.mu_title.strip().lower()
                if key not in self._dupe_map:
                    self._dupe_map[key] = []
                self._dupe_map[key].append(i)

    def is_duplicate(self, row: int) -> bool:
        """Return True if this row shares an MU title with another row."""
        if row < 0 or row >= len(self._entries):
            return False
        e = self._entries[row]
        if e.mu_title is None:
            return False
        key = e.mu_title.strip().lower()
        return len(self._dupe_map.get(key, [])) > 1

    def get_duplicate_rows(self, row: int) -> List[int]:
        """Return list of other row indices that share the same MU title."""
        if row < 0 or row >= len(self._entries):
            return []
        e = self._entries[row]
        if e.mu_title is None:
            return []
        key = e.mu_title.strip().lower()
        all_dups = self._dupe_map.get(key, [])
        return [r for r in all_dups if r != row]

    def set_mu_processing_row(self, row: Optional[int]) -> None:
        old = self._mu_processing_row
        self._mu_processing_row = row
        for r in (r for r in (old, row) if r is not None):
            left = self.index(r, 0)
            right = self.index(r, self.columnCount() - 1)
            self.dataChanged.emit(left, right, [Qt.BackgroundRole])

    def entry_at(self, row: int) -> Optional[MangaEntry]:
        if 0 <= row < len(self._entries):
            return self._entries[row]
        return None

    def set_examined(self, row: int, examined: bool) -> bool:
        """Update the examined flag for a row; returns True if it changed."""
        e = self.entry_at(row)
        if e is None or e.examined == examined:
            return False
        e.examined = examined
        left = self.index(row, 0)
        right = self.index(row, self.columnCount() - 1)
        self.dataChanged.emit(left, right, [Qt.DisplayRole, Qt.BackgroundRole, Qt.UserRole])
        return True

    def set_mu_confirmed(self, row: int, confirmed: bool) -> bool:
        """Toggle mu_confirmed for a row; returns True if it changed."""
        e = self.entry_at(row)
        if e is None or e.mu_title is None or e.mu_confirmed == confirmed:
            return False
        e.mu_confirmed = confirmed
        left = self.index(row, COL_MU_TITLE)
        right = self.index(row, COL_MU_TITLE)
        self.dataChanged.emit(left, right, [Qt.DisplayRole, Qt.BackgroundRole,
                                            Qt.ToolTipRole, Qt.UserRole])
        return True

    def clear_mu_match(self, row: int) -> bool:
        """Clear all MU data from a row; returns True if there was anything to clear."""
        e = self.entry_at(row)
        if e is None or e.mu_id is None:
            return False
        e.mu_id = None
        e.mu_title = None
        e.mu_url = None
        e.licensed = None
        e.mu_confirmed = False
        e.mu_associated = []
        e.mu_score = 0.0
        e.mu_band = None
        e.mu_reasons = []
        e.scan_latest_chapter = None
        e.publisher_name = None
        e.publisher_chapters = None
        e.publisher_volumes = None
        e.publisher_status = None
        e.scan_latest_volume = None
        e.anilist_id = None
        e.anilist_chapters = None
        e.anilist_volumes = None
        e.completed_in_origin = None
        left = self.index(row, 0)
        right = self.index(row, self.columnCount() - 1)
        self.dataChanged.emit(left, right, [Qt.DisplayRole, Qt.BackgroundRole,
                                            Qt.ToolTipRole, Qt.UserRole])
        return True

    # --- Qt overrides --------------------------------------------------------

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: B008
        if parent.isValid():
            return 0
        return len(self._entries)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: B008
        if parent.isValid():
            return 0
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal:
            return COLUMNS[section]
        return section + 1

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        e = self._entries[index.row()]
        col = index.column()

        if role == Qt.DisplayRole or role == Qt.EditRole:
            if col == COL_EXAMINED:
                return "✓" if e.examined else ""
            if col == COL_DUPE:
                if self.is_duplicate(index.row()):
                    return "⚠"
                return ""
            if col == COL_TITLE:
                if e.parent_folder is not None:
                    return f"{e.parent_folder.name} / {e.title}"
                return e.title
            if col == COL_ENG:
                return e.english_title or ""
            if col == COL_FILES:
                return e.n_files
            if col == COL_SUBS:
                return e.n_subfolders
            if col == COL_VOL:
                return f"{e.vol_pct:.1f}"
            if col == COL_CH:
                return f"{e.ch_pct:.1f}"
            if col == COL_BOTH:
                return f"{e.both_pct:.1f}"
            if col == COL_VERDICT:
                return e.verdict.value
            if col == COL_MTIME:
                if e.last_modified:
                    return datetime.datetime.fromtimestamp(e.last_modified).strftime("%Y-%m-%d %H:%M")
                return ""
            if col == COL_MU_TITLE:
                if e.mu_title is None:
                    return ""
                prefix = "✔ " if e.mu_confirmed else ""
                return prefix + e.mu_title
            if col == COL_LICENSED:
                if e.licensed is None:
                    return "" if e.mu_id is None else "…"
                return "Yes" if e.licensed else "No"
            if col == COL_BEHIND:
                return _behind_text(e)
            if col == COL_COMPLETED:
                return _completed_text(e)

        if role == Qt.TextAlignmentRole:
            if col in (COL_FILES, COL_SUBS, COL_VOL, COL_CH, COL_BOTH, COL_MTIME):
                return int(Qt.AlignRight | Qt.AlignVCenter)
            if col in (COL_VERDICT, COL_EXAMINED, COL_LICENSED, COL_BEHIND, COL_COMPLETED, COL_DUPE):
                return int(Qt.AlignCenter)

        if role == Qt.BackgroundRole:
            if self._mu_processing_row == index.row():
                return MU_PROCESSING_BG
            if e.examined:
                return EXAMINED_ROW_BG
            if col == COL_MU_TITLE and needs_review(e):
                return WEAK_MATCH_BG

        if role == Qt.ToolTipRole:
            if col == COL_EXAMINED:
                return "Examined" if e.examined else "Not examined"
            if col == COL_DUPE:
                if self.is_duplicate(index.row()):
                    dupes = self.get_duplicate_rows(index.row())
                    other_folders = [str(self._entries[r].folder.name) for r in dupes]
                    return f"Duplicate MU match\nAlso found in: {', '.join(other_folders)}"
                return None
            if col == COL_MU_TITLE:
                tip = match_tooltip(e)
                if tip is not None:
                    return tip
            if col == COL_LICENSED and e.licensed is True:
                return "Licensed in English"
            if col == COL_BEHIND:
                return _behind_tooltip(e) or str(e.folder)
            if col == COL_COMPLETED:
                return _completed_tooltip(e)
            # Default tooltip: folder path, with franchise info if applicable
            if e.parent_folder is not None:
                return f"Subseries of: {e.parent_folder.name}\n{e.folder}"
            return str(e.folder)

        # Sort by numeric value for percentage / count columns
        if role == Qt.UserRole:
            if col == COL_EXAMINED:
                return 1 if e.examined else 0
            if col == COL_DUPE:
                # Sort duplicates first (1), non-duplicates last (0)
                return 1 if self.is_duplicate(index.row()) else 0
            if col == COL_FILES:
                return e.n_files
            if col == COL_SUBS:
                return e.n_subfolders
            if col == COL_VOL:
                return e.vol_pct
            if col == COL_CH:
                return e.ch_pct
            if col == COL_BOTH:
                return e.both_pct
            if col == COL_VERDICT:
                return e.verdict.value
            if col == COL_TITLE:
                # Sort by parent name first (if subseries), then title
                if e.parent_folder is not None:
                    return f"{e.parent_folder.name.lower()} / {e.title.lower()}"
                return e.title.lower()
            if col == COL_ENG:
                return (e.english_title or "").lower()
            if col == COL_MTIME:
                return e.last_modified
            if col == COL_MU_TITLE:
                # Confirmed entries sort before unconfirmed, then alphabetically.
                prefix = "0" if e.mu_confirmed else "1"
                return f"{prefix}{(e.mu_title or '').lower()}"
            if col == COL_LICENSED:
                if e.licensed is True:
                    return 1
                if e.licensed is False:
                    return 0
                return -1
            if col == COL_BEHIND:
                return _behind_sort(e)
            if col == COL_COMPLETED:
                return _completed_sort(e)

        return None


def _is_mixed_layout(e: MangaEntry) -> bool:
    """True when volumes live in subfolders and chapters are also present.

    This signals we should compare volume counts (not chapters) against the
    official publisher release for the Behind column.
    """
    subfolder_vols = e.n_volume_files - e.parent_volume_files
    return subfolder_vols > 0 and e.n_chapter_files > 0


def _is_omnibus_complete(e: MangaEntry) -> bool:
    """True when disk files signal omnibus/compilation AND the translation is done."""
    return is_omnibus_complete(e)


def _behind_text(e: MangaEntry) -> str:
    if e.behind_override == "done":
        return ""
    if _is_omnibus_complete(e):
        return ""
    return format_behind(
        licensed=e.licensed,
        publisher_chapters=e.publisher_chapters,
        publisher_volumes=e.publisher_volumes,
        publisher_status=e.publisher_status,
        scan_latest_chapter=e.scan_latest_chapter,
        scan_latest_volume=e.scan_latest_volume,
        disk_max_chapter=e.max_disk_chapter,
        disk_max_volume=e.max_disk_volume,
        anilist_chapters=e.anilist_chapters,
        anilist_volumes=e.anilist_volumes,
        prefer_volumes=_is_mixed_layout(e),
    )


def _behind_tooltip(e: MangaEntry) -> str:
    prefix = ""
    if e.behind_override == "done":
        prefix = "Marked as up to date (manual override)\n"
    elif _is_omnibus_complete(e):
        prefix = "Omnibus/compilation files detected — assumed up to date\n"
    detail = format_behind_tooltip(
        licensed=e.licensed,
        publisher_name=e.publisher_name,
        publisher_chapters=e.publisher_chapters,
        publisher_volumes=e.publisher_volumes,
        publisher_status=e.publisher_status,
        scan_latest_chapter=e.scan_latest_chapter,
        scan_latest_volume=e.scan_latest_volume,
        disk_max_chapter=e.max_disk_chapter,
        disk_max_volume=e.max_disk_volume,
        anilist_chapters=e.anilist_chapters,
        anilist_volumes=e.anilist_volumes,
        prefer_volumes=_is_mixed_layout(e),
    )
    return (prefix + detail).strip() or None


def _behind_sort(e: MangaEntry) -> float:
    if e.behind_override == "done" or _is_omnibus_complete(e):
        return 0.0
    return behind_sort_key(
        licensed=e.licensed,
        publisher_chapters=e.publisher_chapters,
        publisher_volumes=e.publisher_volumes,
        scan_latest_chapter=e.scan_latest_chapter,
        scan_latest_volume=e.scan_latest_volume,
        disk_max_chapter=e.max_disk_chapter,
        disk_max_volume=e.max_disk_volume,
        anilist_chapters=e.anilist_chapters,
        anilist_volumes=e.anilist_volumes,
        prefer_volumes=_is_mixed_layout(e),
    )


# --- Completed column helpers ----------------------------------------------

# Symbol → numeric rank for sorting (higher = more complete).
_COMPLETED_RANK = {
    "☆": 3,      # completed + official volumes
    "✓✓": 2,     # completed + translation available
    "✓": 1,      # completed in origin only
    "": 0,        # not completed / unknown
}


def _completed_text(e: MangaEntry) -> str:
    """Return the tier symbol for the Completed column."""
    if not e.completed_in_origin:
        return ""
    # Highest tier: completed + official translation fully released in volumes.
    pub_done = (e.publisher_status or "").strip().lower() in ("completed", "complete")
    if e.licensed is True and e.publisher_volumes is not None and pub_done:
        return "☆"
    # Middle tier: completed + translation available (scan or official chapters/volumes).
    if (e.scan_latest_chapter is not None or e.scan_latest_volume is not None
            or e.publisher_chapters is not None or e.publisher_volumes is not None):
        return "✓✓"
    # Lowest tier: completed in origin, no translation info.
    return "✓"


def _completed_tooltip(e: MangaEntry) -> str:
    sym = _completed_text(e)
    if sym == "☆":
        return "Completed in country of origin\nOfficial English translation (volumes)"
    if sym == "✓✓":
        return "Completed in country of origin\nTranslation available (scan or official, not yet fully released)"
    if sym == "✓":
        return "Completed in country of origin\nNo translation info available"
    return "Not completed / unknown"


def _completed_sort(e: MangaEntry) -> int:
    return _COMPLETED_RANK.get(_completed_text(e), 0)

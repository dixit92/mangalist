"""QAbstractTableModel for the MangaEntry list."""

from __future__ import annotations

import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor

from ..knowledge import (LINK_CONFIRMED, SOURCE_MANGAPIXER, OfficialLink, SeriesKnowledge, fmt_num, from_own_matcher,
                         to_decimal)
from ..models import MangaEntry
from ..mu_match import match_tooltip, needs_review
from ..mu_progress import behind_sort_key, format_behind, format_behind_tooltip
from ..official_sources import KIND_SEARCH, official_links, primary_label
from .list_text import english_text
from .shell import DuplicateSeries
from ..states import MISSING_STATES, STATE_ORDER, WANTED_STATES, SeriesState, State, compute_state, \
    fallback_inventory_from_entry

COLUMNS = [
    "✓",
    "Dupe",  # Duplicates: numbers held by more than one file, and the series in more than one folder
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
    "State",
    "Gaps",
    "Official source",
    "English",
]

# What the header shows where it differs from the column's name (the name is what the settings remember).
HEADER_LABELS = {"Verdict": "Kind", "Dupe": "Duplicates"}
# What the show / hide menu calls a column whose header is a symbol.
MENU_LABELS = {"✓": "Examined"}

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
COL_STATE = 15
COL_GAPS = 16
COL_OFFICIAL = 17
COL_ENGLISH = 18
STATE_COLUMNS = (COL_STATE, COL_GAPS, COL_OFFICIAL)

# Extra roles for the List tab's delegates (list_delegates.py).
SUBTITLE_ROLE = Qt.UserRole + 1     # Title: the matched series' title, shown under the folder's
REVIEW_ROLE = Qt.UserRole + 2       # Title: the match needs review (the subtitle turns orange)
STATE_ROLE = Qt.UserRole + 3        # State: the SeriesState (the badge's colour)

# State filters (key -> label), in the order the toolbar lists them; None = every row.
STATE_FILTERS: List[Tuple[Optional[str], str]] = (
    [(None, "All states"), ("wanted", "Wanted (any)"), ("missing", "Missing (volumes or chapters)")]
    + [(st.value, st.value) for st in STATE_ORDER]
    + [("upcoming", "Upcoming"), ("attention", "Needs attention")]
)


def state_matches(state: Optional[SeriesState], key: Optional[str]) -> bool:
    """Does a row's *state* pass the state filter *key* (see STATE_FILTERS)?"""
    if key is None:
        return True
    if state is None:
        return False
    if key == "wanted":
        return state.state in WANTED_STATES
    if key == "missing":
        return state.state in MISSING_STATES
    if key == "upcoming":
        return state.upcoming
    if key == "attention":
        return bool(state.needs_attention)
    if key == State.UPGRADE.value:  # incl. Complete + Upgrade available (owner rule c)
        return state.state == State.UPGRADE or state.complete_with_upgrade
    return state.state.value == key

# Row tints in the light theme's colours (gui/theme.py): examined rows green, a match in the review tier orange,
# the row being fetched from MangaUpdates blue - each distinct from the picked row (#eef3fd).
EXAMINED_ROW_BG = QColor("#e7f3ec")
WEAK_MATCH_BG = QColor("#fdf0e3")
MU_PROCESSING_BG = QColor("#dce6fa")


class MangaTableModel(QAbstractTableModel):
    def __init__(self, entries: Optional[List[MangaEntry]] = None, parent=None):
        super().__init__(parent)
        self._entries: List[MangaEntry] = list(entries or [])
        self._mu_processing_row: Optional[int] = None
        # Map of mu_title -> list of row indices (for duplicate detection)
        self._dupe_map: dict[str, List[int]] = {}
        # series folder -> (volume numbers, chapter numbers) held by more than one file (lane C's finder, counted by
        # the window off the UI thread)
        self._dupe_files: Dict[str, Tuple[int, int]] = {}
        # Rescan state + official sources per row, computed on first use and dropped when the row changes.
        self._state_cache: Dict[int, Tuple[SeriesState, List[OfficialLink]]] = {}
        self._today: Optional[datetime.date] = None
        # Where a row's inventory and knowledge come from. FALLBACK defaults: the inventory read from the
        # entry's file names (states.fallback_inventory_from_entry) and the own matcher's knowledge; the
        # integrator swaps in the inventory lane's Inventory and the MangaPixer source via
        # set_state_providers().
        self._inventory_for: Callable[[MangaEntry], Any] = fallback_inventory_from_entry
        self._knowledge_for: Callable[[MangaEntry], Optional[SeriesKnowledge]] = from_own_matcher
        self._needs_kind_for: Callable[[MangaEntry], bool] = lambda e: False
        self.dataChanged.connect(self._on_data_changed)
        self.modelReset.connect(self._state_cache.clear)
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

    # --- rescan state ----------------------------------------------------------

    def set_state_providers(self, *, inventory_for: Optional[Callable[[MangaEntry], Any]] = None,
                            knowledge_for: Optional[Callable[[MangaEntry], Optional[SeriesKnowledge]]] = None,
                            needs_kind_for: Optional[Callable[[MangaEntry], bool]] = None,
                            today: Optional[datetime.date] = None) -> None:
        """Swap where the State / Gaps / Official source columns read from (None keeps the current
        provider). *inventory_for(entry)* returns an InventoryLike (or None for the fallback),
        *knowledge_for(entry)* a SeriesKnowledge (or None for the own matcher's)."""
        if inventory_for is not None:
            self._inventory_for = inventory_for
        if knowledge_for is not None:
            self._knowledge_for = knowledge_for
        if needs_kind_for is not None:
            self._needs_kind_for = needs_kind_for
        if today is not None:
            self._today = today
        self.refresh_states()

    def refresh_states(self) -> None:
        """Recompute every row's state (e.g. after new knowledge arrived)."""
        self._state_cache.clear()
        if self._entries:
            self.dataChanged.emit(self.index(0, COL_STATE), self.index(len(self._entries) - 1, COL_ENGLISH),
                                  [Qt.DisplayRole, Qt.ToolTipRole, Qt.UserRole])

    def _on_data_changed(self, top_left, bottom_right, roles=()) -> None:
        rows = range(top_left.row(), bottom_right.row() + 1)
        if not any(r in self._state_cache for r in rows):
            return
        for r in rows:
            self._state_cache.pop(r, None)
        if top_left.column() > COL_STATE or bottom_right.column() < COL_ENGLISH:
            # The row changed elsewhere (MU match, override): repaint its state cells too.
            self.dataChanged.emit(self.index(top_left.row(), COL_STATE), self.index(bottom_right.row(), COL_ENGLISH),
                                  [Qt.DisplayRole, Qt.ToolTipRole, Qt.UserRole])

    def _evaluate(self, row: int) -> Optional[Tuple[SeriesState, List[OfficialLink], SeriesKnowledge]]:
        cached = self._state_cache.get(row)
        if cached is not None:
            return cached
        e = self.entry_at(row)
        if e is None:
            return None
        knowledge = self._knowledge_for(e) or from_own_matcher(e)
        inventory = self._inventory_for(e)
        if inventory is None:
            inventory = fallback_inventory_from_entry(e)
        st = compute_state(inventory, knowledge, folder_empty=e.n_files == 0, needs_kind=self._needs_kind_for(e),
                           behind_override=e.behind_override, today=self._today)
        links = official_links(knowledge, title=knowledge.search_title or e.english_title or e.title)
        self._state_cache[row] = (st, links, knowledge)
        return st, links, knowledge

    def state_at(self, row: int) -> Optional[SeriesState]:
        got = self._evaluate(row)
        return got[0] if got else None

    def links_at(self, row: int) -> List[OfficialLink]:
        got = self._evaluate(row)
        return list(got[1]) if got else []

    def knowledge_at(self, row: int) -> Optional[SeriesKnowledge]:
        """The knowledge behind *row*'s state (MangaPixer's or the own matcher's)."""
        got = self._evaluate(row)
        return got[2] if got else None

    def held_volumes_at(self, row: int) -> Tuple[str, ...]:
        """The volume numbers *row*'s folder holds as volume archives (exact decimal strings, ascending)."""
        e = self.entry_at(row)
        if e is None:
            return ()
        inventory = self._inventory_for(e) or fallback_inventory_from_entry(e)
        nums = [d for d in (to_decimal(v) for v in inventory.held_volumes) if d is not None]
        return tuple(fmt_num(d) for d in sorted(set(nums)))

    def held_chapters_at(self, row: int) -> Tuple[Any, ...]:
        """The chapters *row*'s folder holds as chapter archives (numbers, or ``(from, to)`` for a range archive)."""
        e = self.entry_at(row)
        if e is None:
            return ()
        inventory = self._inventory_for(e) or fallback_inventory_from_entry(e)
        return tuple(inventory.held_chapters or ())

    def mu_view(self, row: int):
        """What the MU Title / Licensed / Behind / Completed columns read for *row*: the entry itself (own
        matcher), the entry with MangaPixer's values when MangaPixer knows the folder, or None when MangaPixer
        says it is not a series (those columns stay empty - no stale own-matcher numbers)."""
        e = self.entry_at(row)
        got = self._evaluate(row)
        if e is None or not got:
            return e
        return mangapixer_view(e, got[2])

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

    def set_duplicate_file_counts(self, counts: Dict[str, Tuple[int, int]]) -> None:
        """Per series folder: how many volume and chapter numbers are held by more than one file."""
        self._dupe_files = {str(k): (int(v[0]), int(v[1])) for k, v in counts.items()}
        if self._entries:
            self.dataChanged.emit(self.index(0, COL_DUPE), self.index(len(self._entries) - 1, COL_DUPE),
                                  [Qt.DisplayRole, Qt.ToolTipRole, Qt.UserRole])

    def duplicate_files_at(self, row: int) -> Tuple[int, int]:
        """(volume numbers, chapter numbers) of row *row*'s folder held by more than one file."""
        if row < 0 or row >= len(self._entries):
            return (0, 0)
        return self._dupe_files.get(str(self._entries[row].folder), (0, 0))

    def _duplicates_text(self, row: int) -> str:
        vols, chs = self.duplicate_files_at(row)
        parts = [f"{vols} vol." if vols else "", f"{chs} ch." if chs else ""]
        if self.is_duplicate(row):
            others = len(self.get_duplicate_rows(row))
            parts.append(f"+{others} folder{'s' if others != 1 else ''}")
        return " · ".join(p for p in parts if p)

    def _duplicates_tip(self, row: int) -> Optional[str]:
        vols, chs = self.duplicate_files_at(row)
        lines = []
        if vols or chs:
            what = " and ".join(t for t in (f"{vols} volume number{'s' if vols != 1 else ''}" if vols else "",
                                             f"{chs} chapter number{'s' if chs != 1 else ''}" if chs else "") if t)
            lines.append(f"{what} held by more than one file (right-click: Review duplicate files)")
        if self.is_duplicate(row):
            other_folders = [str(self._entries[r].folder.name) for r in self.get_duplicate_rows(row)]
            lines.append(f"The same series (MangaUpdates match) is also in: {', '.join(other_folders)}")
        return "\n".join(lines) or None

    def duplicate_series(self) -> List[DuplicateSeries]:
        """The series held in more than one folder (the same MangaUpdates title), for the Duplicates view."""
        out = []
        for rows in self._dupe_map.values():
            if len(rows) > 1:
                title = self._entries[rows[0]].mu_title or self._entries[rows[0]].title
                out.append(DuplicateSeries(title=title, folders=tuple(str(self._entries[r].folder) for r in rows)))
        return sorted(out, key=lambda d: d.title.lower())

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
            return column_label(section)
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
                return self._duplicates_text(index.row())
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
            if col in (COL_MU_TITLE, COL_LICENSED, COL_BEHIND, COL_COMPLETED):
                v = self.mu_view(index.row())
                if v is None:
                    return ""
                if col == COL_MU_TITLE:
                    if v.mu_title is None:
                        return ""
                    prefix = "✔ " if v.mu_confirmed else ""
                    return prefix + v.mu_title
                if col == COL_LICENSED:
                    if v.licensed is None:
                        return "" if v.mu_id is None else "…"
                    return "Yes" if v.licensed else "No"
                if col == COL_BEHIND:
                    return _behind_text(v)
                return _completed_text(v)
            if col == COL_STATE:
                st = self.state_at(index.row())
                return state_text(st) if st else ""
            if col == COL_GAPS:
                st = self.state_at(index.row())
                return st.gaps_text if st else ""
            if col == COL_OFFICIAL:
                return _official_text(self.links_at(index.row()))
            if col == COL_ENGLISH:
                return english_text(self.knowledge_at(index.row()))

        if role == SUBTITLE_ROLE and col == COL_TITLE:
            v = self.mu_view(index.row())
            return (v.mu_title or "") if v is not None else ""
        if role == REVIEW_ROLE and col == COL_TITLE:
            return needs_review(e)
        if role == STATE_ROLE and col == COL_STATE:
            return self.state_at(index.row())

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
                return self._duplicates_tip(index.row())
            if col == COL_MU_TITLE:
                tip = match_tooltip(e)
                if tip is not None:
                    return tip
            if col in (COL_LICENSED, COL_BEHIND, COL_COMPLETED):
                v = self.mu_view(index.row())
                if v is None:
                    st = self.state_at(index.row())
                    return (st.reasons[0] if st and st.reasons else None)
                if col == COL_LICENSED:
                    return "Licensed in English" if v.licensed is True else None
                if col == COL_BEHIND:
                    return _behind_tooltip(v) or str(e.folder)
                return _completed_tooltip(v)
            if col == COL_STATE:
                st = self.state_at(index.row())
                return st.tooltip() if st else None
            if col == COL_GAPS:
                st = self.state_at(index.row())
                return (st.gaps_tooltip() or "No gaps") if st else None
            if col == COL_OFFICIAL:
                return _official_tooltip(self.links_at(index.row())) or None
            # Default tooltip: folder path, with franchise info if applicable
            if e.parent_folder is not None:
                return f"Subseries of: {e.parent_folder.name}\n{e.folder}"
            return str(e.folder)

        # Sort by numeric value for percentage / count columns
        if role == Qt.UserRole:
            if col == COL_EXAMINED:
                return 1 if e.examined else 0
            if col == COL_DUPE:
                # the most duplicated first: numbers held twice, plus the other folders of the same series
                vols, chs = self.duplicate_files_at(index.row())
                return vols + chs + (len(self.get_duplicate_rows(index.row())) if self.is_duplicate(index.row()) else 0)
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
            if col in (COL_MU_TITLE, COL_LICENSED, COL_BEHIND, COL_COMPLETED):
                v = self.mu_view(index.row())   # sort what the cells show (MangaPixer's values when it knows the folder)
                if col == COL_MU_TITLE:
                    if v is None:
                        return "2"
                    # Confirmed entries sort before unconfirmed, then alphabetically.
                    prefix = "0" if v.mu_confirmed else "1"
                    return f"{prefix}{(v.mu_title or '').lower()}"
                if col == COL_LICENSED:
                    if v is not None and v.licensed is True:
                        return 1
                    if v is not None and v.licensed is False:
                        return 0
                    return -1
                if col == COL_BEHIND:
                    return _behind_sort(v) if v is not None else -1.0
                return _completed_sort(v) if v is not None else -1
            if col == COL_STATE:
                st = self.state_at(index.row())
                # STATE_ORDER rank first, then more missing units first (one number: Qt sorts it).
                rank, neg_missing = st.sort_key if st else (len(STATE_ORDER), 0)
                return rank * 1_000_000 + neg_missing
            if col == COL_GAPS:
                st = self.state_at(index.row())
                return st.n_missing if st else -1
            if col == COL_OFFICIAL:
                return _official_text(self.links_at(index.row())).lower()
            if col == COL_ENGLISH:
                return english_text(self.knowledge_at(index.row())).lower()

        return None


def column_label(col: int, menu: bool = False) -> str:
    """What the header (or, with *menu*, its show / hide menu) calls column *col*."""
    name = COLUMNS[col]
    if menu and name in MENU_LABELS:
        return MENU_LABELS[name]
    return HEADER_LABELS.get(name, name)


# --- State / Gaps / Official source helpers -------------------------------------


def state_text(st: SeriesState) -> str:
    """The state, with its flags: ``Up to date · Upcoming``; for a folder that is not a series, why (e.g.
    ``Collection about <series>``)."""
    if st.state == State.NOT_A_SERIES and st.reasons:
        return st.reasons[0]
    return "  ·  ".join((st.state.value,) + st.flags)


class _MangaPixerView:
    """A MangaEntry seen through MangaPixer's knowledge: the own-matcher fields the old columns read are
    replaced by MangaPixer's; everything else (files, disk numbers, override) is the entry's."""

    def __init__(self, entry: MangaEntry, fields: dict):
        self._entry = entry
        self._fields = fields

    def __getattr__(self, name):
        if name in self._fields:
            return self._fields[name]
        return getattr(self._entry, name)


def _f(d) -> Optional[float]:
    return float(d) if d is not None else None


def mangapixer_view(e: MangaEntry, k: Optional[SeriesKnowledge]):
    """See :meth:`MangaTableModel.mu_view`."""
    if k is None or k.source != SOURCE_MANGAPIXER:
        return e
    if k.not_a_series:
        return None
    pub = next((p for p in k.english_publishers if p.volumes is not None or p.chapters is not None),
               k.english_publishers[0] if k.english_publishers else None)
    return _MangaPixerView(e, {
        "mu_id": k.mu_id, "mu_title": k.title, "mu_url": k.mu_url,
        "mu_confirmed": k.link_state == LINK_CONFIRMED,
        "licensed": k.licensed_en if k.licensed_en is not None else (True if k.english_publishers else None),
        "publisher_name": pub.name if pub else None,
        "publisher_volumes": _f(pub.volumes) if pub else None,
        "publisher_chapters": _f(pub.chapters) if pub else None,
        "publisher_status": pub.status if pub else None,
        "scan_latest_chapter": _f(k.latest_chapter_decimal),
        "scan_latest_volume": None,
        "anilist_chapters": _f(k.anilist_chapters), "anilist_volumes": _f(k.anilist_volumes),
        "completed_in_origin": k.finished_in_origin,
    })


def _official_text(links: List[OfficialLink]) -> str:
    label = primary_label(links)
    more = sum(1 for link in links if link.kind != KIND_SEARCH) - 1
    return f"{label} (+{more})" if more > 0 else label


def _official_tooltip(links: List[OfficialLink]) -> str:
    lines = []
    for link in links:
        where = link.url or "(no page known)"
        lines.append(f"{link.label} [{link.kind}, {link.source}]: {where}")
    return "\n".join(lines)


def _is_mixed_layout(e: MangaEntry) -> bool:
    """True when volumes live in subfolders and chapters are also present.

    This signals we should compare volume counts (not chapters) against the
    official publisher release for the Behind column.
    """
    subfolder_vols = e.n_volume_files - e.parent_volume_files
    return subfolder_vols > 0 and e.n_chapter_files > 0


def _is_omnibus_complete(e: MangaEntry) -> bool:
    """True when disk files signal omnibus/compilation AND the translation is done."""
    if not e.has_compilation_files:
        return False
    pub_done = (e.publisher_status or "").strip().lower() in ("completed", "complete")
    origin_done = e.completed_in_origin is True
    return pub_done or origin_done


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

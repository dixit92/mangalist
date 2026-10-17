"""QAbstractTableModel for the MangaEntry list."""

from __future__ import annotations

import datetime
import os
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QColor

from ..knowledge import LINK_CONFIRMED, SOURCE_MANGAPIXER, OfficialLink, SeriesKnowledge, from_own_matcher
from ..models import MangaEntry
from ..mu_match import match_tooltip, needs_review
from ..mu_progress import behind_sort_key, format_behind, format_behind_tooltip
from ..official_sources import KIND_SEARCH, official_links, primary_label
from .colors import qcolor
from .list_text import english_text
from .palette import current
from .shell import DuplicateSeries
from ..states import MISSING_STATES, STATE_ORDER, WANTED_STATES, SeriesState, State, fallback_inventory_from_entry
from ..wanted import evaluate, held_volumes

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
    "Library",  # the root the series lies in (hidden by default; useful under "All libraries")
    "Rename",   # files not named by the root's naming scheme (Rename pending; hidden by default)
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
COL_LIBRARY = 19
COL_RENAME = 20
STATE_COLUMNS = (COL_STATE, COL_GAPS, COL_OFFICIAL)
# Columns a series' state does not read: a change of one of them alone keeps the state (the Rename counts are an input,
# but set_rename_counts drops the states of the series whose count changed itself).
NOT_STATE_INPUTS = frozenset({COL_DUPE, COL_LIBRARY, COL_RENAME})

# Extra roles for the List tab's delegates (list_delegates.py).
SUBTITLE_ROLE = Qt.UserRole + 1     # Title: the matched series' title, shown under the folder's
REVIEW_ROLE = Qt.UserRole + 2       # Title: the match needs review (the subtitle turns orange)
STATE_ROLE = Qt.UserRole + 3        # State: the SeriesState (the badge's colour)
PENDING_ROLE = Qt.UserRole + 21    # State: True while the row has no state to show and its state is being worked out
                                    # (the cell paints a busy indicator, :mod:`.busy`)
MODE_CHIP_ROLE = Qt.UserRole + 20   # Title: the series' own automatic-downloads mode where it differs from the default
                                    # (a small chip after the title; "" for none)
# Roles a series' state does not read: a dataChanged for these alone keeps the cached state.
NOT_STATE_ROLES = frozenset({int(Qt.BackgroundRole), int(MODE_CHIP_ROLE), int(PENDING_ROLE)})
# The columns a pushed state changes (the Title's subtitle is MangaPixer's title; MU Title to English read the state or
# its knowledge): one dataChanged over them per push (UI performance stage 2).
STATE_PUSH_COLUMNS = (COL_TITLE, COL_ENGLISH)

# The roles as plain ints: data() runs for every painted cell and every sort comparison, and comparing an int with a
# PySide enum member costs a Python call each time (UI performance stage 2).
_DISPLAY = int(Qt.DisplayRole)
_EDIT = int(Qt.EditRole)
_TOOLTIP = int(Qt.ToolTipRole)
_USER = int(Qt.UserRole)
_BACKGROUND = int(Qt.BackgroundRole)
_ALIGNMENT = int(Qt.TextAlignmentRole)
_SUBTITLE = int(SUBTITLE_ROLE)
_REVIEW = int(REVIEW_ROLE)
_STATE = int(STATE_ROLE)
_MODE_CHIP = int(MODE_CHIP_ROLE)
_PENDING = int(PENDING_ROLE)
_ALIGN_RIGHT = int(Qt.AlignRight | Qt.AlignVCenter)
_ALIGN_CENTER = int(Qt.AlignCenter)
_RIGHT_COLUMNS = frozenset({COL_FILES, COL_SUBS, COL_VOL, COL_CH, COL_BOTH, COL_MTIME, COL_RENAME})
_CENTER_COLUMNS = frozenset({COL_VERDICT, COL_EXAMINED, COL_LICENSED, COL_BEHIND, COL_COMPLETED, COL_DUPE})
_MU_COLUMNS = frozenset({COL_MU_TITLE, COL_LICENSED, COL_BEHIND, COL_COMPLETED})
_STATE_ROLES = [Qt.DisplayRole, Qt.ToolTipRole, Qt.UserRole]
_MISSING = object()


def _comparable(value: Any):
    """A sort value (``data(UserRole)``) as a key Python compares the way Qt's proxy compares the value: an empty value
    after every other (``QVariant`` invalid), text by UTF-16 code unit (``QString::compare``, case-sensitive), numbers
    as numbers."""
    if value is None:
        return (1,)
    if isinstance(value, str):
        return (0, value.encode("utf-16-be"))
    return (0, value)

# State filters (key -> label), in the order the toolbar lists them; None = every row.
STATE_FILTERS: List[Tuple[Optional[str], str]] = (
    [(None, "All states"), ("wanted", "Wanted (any)"), ("missing", "Missing (volumes or chapters)")]
    + [(st.value, st.value) for st in STATE_ORDER]
    + [("upcoming", "Upcoming"), ("attention", "Needs attention"), ("rename", "Rename pending")]
    + [("unbundled", "Unbundled chapters")]     # the series' setting (owner, 2026-10-15: under More filters)
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
    if key == "rename":
        return state.rename_pending
    if key == "unbundled":
        return state.unbundled
    if key == State.UPGRADE.value:  # incl. Complete + Upgrade available (owner rule c)
        return state.state == State.UPGRADE or state.complete_with_upgrade
    return state.state.value == key

# Row tints, palette tokens read at data() time (a switch of Appearance shows at the next repaint): examined rows
# green, a match in the review tier orange, the row being fetched from MangaUpdates blue - each distinct from the
# picked row.
ROW_TINTS = {"EXAMINED_ROW_BG": "ok_bg", "WEAK_MATCH_BG": "warn_bg", "MU_PROCESSING_BG": "busy_row"}


def __getattr__(name: str):
    if name in ROW_TINTS:                       # the old constants: the tint in the current palette
        return QColor(getattr(current(), ROW_TINTS[name]))
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def under_any(folder: str, tops: Iterable[str]) -> bool:
    """Is the absolute *folder* one of *tops* (absolute top-level folders of a folder scan) or inside one?"""
    f = os.path.normcase(str(folder))
    for t in tops:
        t = os.path.normcase(str(t)).rstrip("/\\")
        if f == t or f.startswith(t + os.sep):
            return True
    return False


def merge_folder_scan(entries: List[MangaEntry], root_id: Optional[int], root_folder, scope: Iterable[str],
                      scanned: Iterable[MangaEntry]) -> List[MangaEntry]:
    """*entries* (the table's rows) with a folder-level scan merged in: the rows of root *root_id* below the scanned
    top-level folders (*scope*, names of the root's direct children - a subseries is below its franchise parent) are
    replaced by *scanned*; the root's rows stay in the scan's order (by top-level folder name, as a whole-root scan
    lists them), the other roots' rows stay as they are (selective scanning, Stage 1)."""
    names = set(scope)
    base = Path(root_folder)

    def top(e: MangaEntry) -> str:
        try:
            return Path(e.folder).relative_to(base).parts[0]
        except (ValueError, IndexError):
            return ""

    others = [e for e in entries if e.root_id != root_id]
    groups: Dict[str, List[MangaEntry]] = {}
    for e in [e for e in entries if e.root_id == root_id and top(e) not in names] + list(scanned):
        groups.setdefault(top(e), []).append(e)
    return others + [e for name in sorted(groups, key=str.lower) for e in groups[name]]


class MangaTableModel(QAbstractTableModel):
    """The List's rows: one :class:`MangaEntry` per series, with its state (State / Gaps / Official source / English
    and the MangaUpdates columns) computed from the providers the window sets.

    **States in the background** (UI performance stage 2). With :attr:`deferred` on (the window turns it on when it has
    a background runner), nothing that paints, filters, sorts or counts computes a state on the UI thread: a row
    without an up-to-date state shows the one it had before (the same entry's, or the previous scan's entry of the same
    folder) - or nothing for a series never seen - and :attr:`states_wanted` asks the window to compute the missing
    ones (:meth:`take_states_request` / :meth:`compute_states` off the UI thread / :meth:`push_states`). A push stores
    them in one step and emits one ``dataChanged`` over the rows and columns they change. :meth:`state_at` and the
    other single-row readers still compute at once (the details panel, a row's menu). Off (the default, and the tests
    that hand the window a synchronous runner), every state is computed on first use as before."""

    # Rows without an up-to-date state (deferred mode): the window computes them off the UI thread. Emitted once until
    # the window takes the request.
    states_wanted = Signal()

    def __init__(self, entries: Optional[List[MangaEntry]] = None, parent=None):
        super().__init__(parent)
        self._entries: List[MangaEntry] = list(entries or [])
        self._mu_processing_row: Optional[int] = None
        # Map of mu_title -> list of row indices (for duplicate detection)
        self._dupe_map: dict[str, List[int]] = {}
        # The library picked in the List tab (a root id; None = all libraries): duplicates are looked for inside it.
        self._scope: Optional[int] = None
        self._root_names: Dict[int, str] = {}
        # series folder -> (volume numbers, chapter numbers) held by more than one file (lane C's finder, counted by
        # the window off the UI thread)
        self._dupe_files: Dict[str, Tuple[int, int]] = {}
        # series folder -> how many files the renamer would rename (Rename pending; counted off the UI thread)
        self._rename_counts: Dict[str, int] = {}
        # folder -> the Title's automatic-downloads chip: the series' own mode where it differs from the default
        self._mode_chips: Dict[str, str] = {}
        # Rescan state + official sources + knowledge per series, computed on first use. Keyed by the entry object
        # (``id(entry)`` -> (entry, result); the entry is kept so the id cannot be reused): a reset that keeps a row's
        # entry - another library's scan, a library switch - keeps its state; a rescanned series is a new entry. Dropped
        # when the row changes (dataChanged on a column the state reads), the rename counts or the providers change.
        self._state_cache: Dict[int, Tuple[MangaEntry, Tuple[SeriesState, List[OfficialLink], SeriesKnowledge]]] = {}
        self.evaluations = 0            # how many states were computed on the UI thread (the work-counter tests)
        self.pushed = 0                 # how many states arrived from the background (deferred mode)
        # Deferred mode (see the class docstring): what a row shows until its new state arrives - the same entry's old
        # state (``id(entry)`` -> (entry, result)) or, for a rescanned series, its folder's last one.
        self.deferred = False
        self._stale: Dict[int, Tuple[MangaEntry, Tuple[SeriesState, List[OfficialLink], SeriesKnowledge]]] = {}
        self._stale_folders: Dict[str, Tuple[SeriesState, List[OfficialLink], SeriesKnowledge]] = {}
        self._generation: Dict[int, int] = {}       # id(entry) -> bumped whenever its state goes out of date
        self._in_flight: set = set()                # id(entry) of the states being computed in the background
        self._failed: set = set()                   # ... that could not be computed there (computed here on use)
        self._asked = False                         # states_wanted emitted, the window has not taken the request yet
        self._pushing = False                       # a push's own dataChanged (keeps the states it just stored)
        # Sort keys per row (``id(entry)`` -> (entry, {column: key})): the proxy's lessThan compares these instead of
        # asking data() twice per comparison. Dropped for a row with every dataChanged that touches it.
        self._sort_keys: Dict[int, Tuple[MangaEntry, Dict[int, Any]]] = {}
        # Which state filters a state passes (``id(state)`` -> (state, keys asked, keys passed)), for the chips' counts.
        self._filter_cache: Dict[int, Tuple[SeriesState, tuple, frozenset]] = {}
        self._today: Optional[datetime.date] = None
        # Where a row's inventory and knowledge come from. FALLBACK defaults: the inventory read from the
        # entry's file names (states.fallback_inventory_from_entry) and the own matcher's knowledge; the
        # integrator swaps in the inventory lane's Inventory and the MangaPixer source via
        # set_state_providers().
        self._inventory_for: Callable[[MangaEntry], Any] = fallback_inventory_from_entry
        self._knowledge_for: Callable[[MangaEntry], Optional[SeriesKnowledge]] = from_own_matcher
        self._needs_kind_for: Callable[[MangaEntry], bool] = lambda e: False
        self._unbundled_for: Callable[[MangaEntry], bool] = lambda e: False     # mangalist.unbundled
        self.dataChanged.connect(self._on_data_changed)
        # Build initial dupe map if entries provided
        if self._entries:
            self._rebuild_dupe_map()

    # --- data plumbing -------------------------------------------------------

    def set_entries(self, entries: List[MangaEntry]) -> None:
        """Show *entries*. The states of entries shown before are kept (see ``_state_cache``); an entry changed in place
        outside the model's setters must be handed to :meth:`forget_states`.

        Only the rows that changed are replaced (UI performance stage 2): the rows before the first and after the last
        changed entry stay (the same objects), the ones between are removed and the new ones inserted - one library's
        scan no longer resets the other libraries' 600 rows (the proxy re-sorts only what was inserted, the selection
        and the scroll position of the other rows stay). The same list again repaints every row (entries changed in
        place)."""
        new = list(entries)
        old = self._entries
        n_old, n_new = len(old), len(new)
        head = 0
        while head < n_old and head < n_new and old[head] is new[head]:
            head += 1
        tail = 0
        while tail < n_old - head and tail < n_new - head and old[n_old - 1 - tail] is new[n_new - 1 - tail]:
            tail += 1
        self._forget_gone(new)
        processing, self._mu_processing_row = self._mu_processing_row, None
        if head == n_old == n_new:                      # the same rows: what changed in place repaints
            self._rebuild_dupe_map()
            if new:
                self._pushing = True                    # keep the states (forget_states names the changed ones)
                try:
                    self.dataChanged.emit(self.index(0, 0), self.index(n_new - 1, self.columnCount() - 1))
                finally:
                    self._pushing = False
        else:
            if n_old - tail > head:
                self.beginRemoveRows(QModelIndex(), head, n_old - tail - 1)
                self._entries = old[:head] + old[n_old - tail:]
                self._rebuild_dupe_map()
                self.endRemoveRows()
            if n_new - tail > head:
                self.beginInsertRows(QModelIndex(), head, n_new - tail - 1)
                self._entries = new
                self._rebuild_dupe_map()
                self.endInsertRows()
            self._entries = new
            self._rebuild_dupe_map()
            if processing is not None and processing < min(head, n_new):     # the row being looked up stays
                idx = self.index(processing, 0)
                self.dataChanged.emit(idx, self.index(processing, self.columnCount() - 1), [Qt.BackgroundRole])
        if self.deferred and any(id(e) not in self._state_cache for e in new):
            self._want_states()

    def _forget_gone(self, new: List[MangaEntry]) -> None:
        """Drop what is kept for entries that are not in *new*; a gone entry's state stays shown for its folder until the
        folder's new entry has its own (deferred mode)."""
        kept = {id(e) for e in new}
        for cache in (self._state_cache, self._stale):
            for key in [k for k in cache if k not in kept]:
                e, result = cache.pop(key)
                if self.deferred:
                    self._stale_folders[str(e.folder)] = result
        for key in [k for k in self._sort_keys if k not in kept]:
            del self._sort_keys[key]
        for store in (self._generation, ):
            for key in [k for k in store if k not in kept]:
                del store[key]
        self._failed &= kept
        if self._stale_folders:
            folders = {str(e.folder) for e in new}
            for folder in [f for f in self._stale_folders if f not in folders]:
                del self._stale_folders[folder]
        if len(self._filter_cache) > 4 * max(len(new), 64):
            self._filter_cache.clear()

    def forget_states(self, entries: Iterable[MangaEntry]) -> None:
        """*entries* were changed in place (their MangaUpdates data, kind or folder): compute their states again."""
        keys = [id(e) for e in entries]
        for k in keys:
            self._sort_keys.pop(k, None)
        self._invalidate(keys)

    # --- rescan state ----------------------------------------------------------

    def set_state_providers(self, *, inventory_for: Optional[Callable[[MangaEntry], Any]] = None,
                            knowledge_for: Optional[Callable[[MangaEntry], Optional[SeriesKnowledge]]] = None,
                            needs_kind_for: Optional[Callable[[MangaEntry], bool]] = None,
                            today: Optional[datetime.date] = None,
                            unbundled_for: Optional[Callable[[MangaEntry], bool]] = None) -> None:
        """Swap where the State / Gaps / Official source columns read from (None keeps the current
        provider). *inventory_for(entry)* returns an InventoryLike (or None for the fallback),
        *knowledge_for(entry)* a SeriesKnowledge (or None for the own matcher's), *unbundled_for(entry)* the series'
        Unbundled Chapters setting."""
        if inventory_for is not None:
            self._inventory_for = inventory_for
        if knowledge_for is not None:
            self._knowledge_for = knowledge_for
        if needs_kind_for is not None:
            self._needs_kind_for = needs_kind_for
        if today is not None:
            self._today = today
        if unbundled_for is not None:
            self._unbundled_for = unbundled_for
        self.refresh_states()

    def refresh_states(self) -> None:
        """Recompute every row's state (e.g. after new knowledge arrived). Deferred: the rows keep showing their states
        until the new ones are pushed."""
        if self.deferred:
            self._invalidate(set(self._state_cache) | self._in_flight | self._failed)
            if any(id(e) not in self._state_cache for e in self._entries):
                self._want_states()
            return
        self._state_cache.clear()
        self._generation.clear()
        if self._entries:
            self.dataChanged.emit(self.index(0, COL_STATE), self.index(len(self._entries) - 1, COL_ENGLISH),
                                  _STATE_ROLES)

    def _invalidate(self, keys: Iterable[int]) -> None:
        """The states of the entries *keys* (``id(entry)``) are out of date: drop them (deferred: keep them shown until
        the new ones arrive, and drop a result still being computed from the old inputs)."""
        any_key = False
        for k in keys:
            any_key = True
            got = self._state_cache.pop(k, None)
            if self.deferred:
                if got is not None:
                    self._stale[k] = got
                self._generation[k] = self._generation.get(k, 0) + 1
                self._failed.discard(k)
        if any_key and self.deferred:
            self._want_states()

    def _on_data_changed(self, top_left, bottom_right, roles=()) -> None:
        first, last = top_left.row(), bottom_right.row()
        n = len(self._entries)
        keys = [id(self._entries[r]) for r in range(max(first, 0), min(last, n - 1) + 1)]
        for k in keys:                  # what a cell shows changed: its sort keys go (cheap; any column)
            self._sort_keys.pop(k, None)
        if self._pushing:
            return                      # a push's own repaint (or the same rows again): the states stay
        if top_left.column() == bottom_right.column() and top_left.column() in NOT_STATE_INPUTS:
            return                      # the Dupe / Library / Rename column alone: the state does not read them
        if roles and {int(r) for r in roles} <= NOT_STATE_ROLES:
            return                      # a tint (the row being looked up) or the mode chip
        if not any(k in self._state_cache or k in self._in_flight or k in self._stale for k in keys):
            return
        self._invalidate(keys)
        if not self.deferred and (top_left.column() > COL_STATE or bottom_right.column() < COL_ENGLISH):
            # The row changed elsewhere (MU match, override): repaint its state cells too.
            self.dataChanged.emit(self.index(first, COL_STATE), self.index(last, COL_ENGLISH), _STATE_ROLES)

    def _compute(self, e: MangaEntry, rename_count: int):
        """One series' (state, official links, knowledge) from the providers (any thread)."""
        st, knowledge = evaluate(e, knowledge=self._knowledge_for(e), inventory=self._inventory_for(e),
                                 needs_kind=self._needs_kind_for(e), rename_count=rename_count, today=self._today,
                                 unbundled=self._unbundled_for(e))
        links = official_links(knowledge, title=knowledge.search_title or e.english_title or e.title)
        return st, links, knowledge

    def _evaluate(self, row: int) -> Optional[Tuple[SeriesState, List[OfficialLink], SeriesKnowledge]]:
        e = self.entry_at(row)
        if e is None:
            return None
        cached = self._state_cache.get(id(e))
        if cached is not None and cached[0] is e:
            return cached[1]
        self.evaluations += 1
        result = self._compute(e, self._rename_counts.get(str(e.folder), 0))
        self._state_cache[id(e)] = (e, result)
        self._stale.pop(id(e), None)
        return result

    def _shown(self, row: int) -> Optional[Tuple[SeriesState, List[OfficialLink], SeriesKnowledge]]:
        """What *row* shows: its state; deferred, the state it showed before while the new one is computed (None for a
        series never seen), never computed here."""
        e = self._entries[row] if 0 <= row < len(self._entries) else None
        if e is None:
            return None
        k = id(e)
        cached = self._state_cache.get(k)
        if cached is not None and cached[0] is e:
            return cached[1]
        if not self.deferred or k in self._failed:
            return self._evaluate(row)
        if k not in self._in_flight:
            self._want_states()
        stale = self._stale.get(k)
        if stale is not None and stale[0] is e:
            return stale[1]
        return self._stale_folders.get(str(e.folder))

    def state_at(self, row: int) -> Optional[SeriesState]:
        """*row*'s state, computed now if it is not known (one row: the details panel, a menu, an action)."""
        got = self._evaluate(row)
        return got[0] if got else None

    def shown_state_at(self, row: int) -> Optional[SeriesState]:
        """The state *row* shows (deferred: possibly the one before, or None while it is computed) - for the filters and
        the counts, which never compute a state on the UI thread."""
        got = self._shown(row)
        return got[0] if got else None

    def ready_state_at(self, row: int) -> Optional[SeriesState]:
        """*row*'s up-to-date state, or None when it is not known yet (never computed here)."""
        e = self.entry_at(row)
        cached = self._state_cache.get(id(e)) if e is not None else None
        return cached[1][0] if cached is not None and cached[0] is e else None

    def ready_state_of(self, e: MangaEntry) -> Optional[SeriesState]:
        """*e*'s up-to-date state, or None when it is not known (never computed here)."""
        cached = self._state_cache.get(id(e))
        return cached[1][0] if cached is not None and cached[0] is e else None

    def states_pending(self, rows: Optional[Iterable[int]] = None) -> bool:
        """Deferred: does a row of *rows* (all when None) wait for its state? Always False otherwise (computed on
        use)."""
        if not self.deferred:
            return False
        cache, failed = self._state_cache, self._failed
        entries = self._entries
        it = entries if rows is None else (entries[r] for r in rows if 0 <= r < len(entries))
        return any(id(e) not in cache and id(e) not in failed for e in it)

    def state_pending_at(self, row: int) -> bool:
        """Deferred: is *row* a series whose state is being worked out and which has none to show meanwhile (never seen
        before - the State cell would be empty)? The cell paints a busy indicator for it. A row that shows its old state
        while the new one is computed is not pending in this sense, and nothing here computes a state."""
        if not self.deferred or not 0 <= row < len(self._entries):
            return False
        e = self._entries[row]
        k = id(e)
        cached = self._state_cache.get(k)
        if (cached is not None and cached[0] is e) or k in self._failed:
            return False
        stale = self._stale.get(k)
        if stale is not None and stale[0] is e:
            return False
        return str(e.folder) not in self._stale_folders

    def _want_states(self) -> None:
        if not self._asked:
            self._asked = True
            self.states_wanted.emit()

    def take_states_request(self) -> List[Tuple[MangaEntry, int, int]]:
        """The rows to compute in the background: ``(entry, its generation, its rename count)`` for each entry without
        an up-to-date state that is not being computed already, in row order. They count as in flight from here."""
        self._asked = False
        cache, flight = self._state_cache, self._in_flight
        out = []
        for e in self._entries:
            k = id(e)
            if k in cache or k in flight or k in self._failed:
                continue
            flight.add(k)
            out.append((e, self._generation.get(k, 0), self._rename_counts.get(str(e.folder), 0)))
        return out

    def compute_states(self, request: Iterable[Tuple[MangaEntry, int, int]]) -> List[Tuple[MangaEntry, int, Any]]:
        """Off the UI thread: the results of :meth:`take_states_request` (``None`` for a series whose state could not be
        computed - it is computed on the UI thread on use, where the error shows as before)."""
        out = []
        for e, generation, rename_count in request:
            try:
                result = self._compute(e, rename_count)
            except Exception:  # noqa: BLE001 - computed again on use, where it fails visibly as before
                result = None
            out.append((e, generation, result))
        return out

    def push_states(self, computed: Iterable[Tuple[MangaEntry, int, Any]]) -> List[int]:
        """Store the background's results in one step: the ones still current (the entry still shown, nothing changed
        since the request) replace what their rows showed; then one ``dataChanged`` over those rows' state columns.
        Returns the rows that changed."""
        where = {id(e): r for r, e in enumerate(self._entries)}
        rows = []
        for e, generation, result in computed:
            k = id(e)
            self._in_flight.discard(k)
            r = where.get(k)
            if r is None or self._entries[r] is not e or self._generation.get(k, 0) != generation:
                continue                # gone, or changed meanwhile (a newer request computes it)
            if result is None:
                self._failed.add(k)
                continue
            if k not in self._state_cache:
                self._state_cache[k] = (e, result)
                self.pushed += 1
            self._stale.pop(k, None)
            self._stale_folders.pop(str(e.folder), None)
            rows.append(r)
        if rows:
            first, last = min(rows), max(rows)
            self._pushing = True
            try:
                self.dataChanged.emit(self.index(first, STATE_PUSH_COLUMNS[0]),
                                      self.index(last, STATE_PUSH_COLUMNS[1]), _STATE_ROLES)
            finally:
                self._pushing = False
        if self.deferred and any(id(e) not in self._state_cache and id(e) not in self._in_flight
                                 and id(e) not in self._failed for e in self._entries):
            self._want_states()
        return rows

    def abandon_states(self, request: Iterable[Tuple[MangaEntry, int, int]]) -> None:
        """The background computation of *request* failed as a whole: those rows are computed on use instead."""
        for e, _generation, _count in request:
            self._in_flight.discard(id(e))
            self._failed.add(id(e))

    def filter_matches(self, st: SeriesState, keys: tuple) -> frozenset:
        """Which of the state filters *keys* *st* passes (kept per state object: the chips' counts read it per row)."""
        got = self._filter_cache.get(id(st))
        if got is not None and got[0] is st and got[1] == keys:
            return got[2]
        passed = frozenset(k for k in keys if state_matches(st, k))
        self._filter_cache[id(st)] = (st, keys, passed)
        return passed

    def links_at(self, row: int) -> List[OfficialLink]:
        got = self._evaluate(row)
        return list(got[1]) if got else []

    def knowledge_at(self, row: int) -> Optional[SeriesKnowledge]:
        """The knowledge behind *row*'s state (MangaPixer's or the own matcher's)."""
        got = self._evaluate(row)
        return got[2] if got else None

    def ready_knowledge_at(self, row: int) -> Optional[SeriesKnowledge]:
        """The knowledge behind *row*'s up-to-date state, or None when the state is not known yet."""
        e = self.entry_at(row)
        cached = self._state_cache.get(id(e)) if e is not None else None
        return cached[1][2] if cached is not None and cached[0] is e else None

    def held_volumes_of(self, e: MangaEntry) -> Tuple[str, ...]:
        """The volume numbers *e*'s folder holds as volume archives (exact decimal strings, ascending; any thread)."""
        return held_volumes(self._inventory_for(e) or fallback_inventory_from_entry(e))

    def held_volumes_at(self, row: int) -> Tuple[str, ...]:
        """The volume numbers *row*'s folder holds as volume archives (exact decimal strings, ascending)."""
        e = self.entry_at(row)
        if e is None:
            return ()
        return self.held_volumes_of(e)

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
        got = self._shown(row)
        if e is None or not got:
            return e
        return mangapixer_view(e, got[2])

    def sort_key(self, row: int, col: int):
        """The key *row* sorts by in column *col* (its ``data(UserRole)`` as :func:`_comparable` makes it), kept until
        the row changes: the proxy's lessThan compares these (UI performance stage 2)."""
        e = self._entries[row]
        k = id(e)
        keys = self._sort_keys.get(k)
        if keys is None or keys[0] is not e:
            keys = (e, {})
            self._sort_keys[k] = keys
        got = keys[1].get(col, _MISSING)
        if got is _MISSING:
            got = _comparable(self._sort_value(row, e, col))
            keys[1][col] = got
        return got

    # --- library scope ---------------------------------------------------------------------------------------

    def set_root_names(self, names: Dict[int, str]) -> None:
        """Root id -> the name the Library column shows."""
        self._root_names = dict(names)
        if self._entries:
            self.dataChanged.emit(self.index(0, COL_LIBRARY), self.index(len(self._entries) - 1, COL_LIBRARY),
                                  [Qt.DisplayRole, Qt.UserRole])

    def library_name(self, root_id: Optional[int]) -> str:
        return self._root_names.get(root_id, "") if root_id is not None else ""

    def scope(self) -> Optional[int]:
        return self._scope

    def set_scope(self, root_id: Optional[int]) -> None:
        """Look at one library (a root id) or all (None): a series counts as a duplicate when its twin lies in the
        same library, so the Dupe column and the Duplicates chip agree with the rows the picker lets through."""
        if root_id == self._scope:
            return
        before = [self._dupe_signature(r) for r in range(len(self._entries))]
        self._scope = root_id
        self._rebuild_dupe_map()
        # only the rows whose Duplicates cell changes repaint (a library switch otherwise re-sorts and repaints 700)
        changed = [r for r, old in enumerate(before) if self._dupe_signature(r) != old]
        if changed:
            self.dataChanged.emit(self.index(changed[0], COL_DUPE), self.index(changed[-1], COL_DUPE),
                                  [Qt.DisplayRole, Qt.ToolTipRole, Qt.UserRole])

    def search_text(self, row: int) -> str:
        """What the List's filter box matches for *row*, case-folded: the Title as shown (``Parent / Title`` for a
        subseries), the alternative (English) title and the Kind - what its placeholder promises, read straight from
        the entry (no state is computed)."""
        e = self.entry_at(row)
        if e is None:
            return ""
        title = f"{e.parent_folder.name} / {e.title}" if e.parent_folder is not None else e.title
        return f"{title}\n{e.english_title or ''}\n{e.verdict.value}".casefold()

    def in_scope(self, row: int) -> bool:
        e = self.entry_at(row)
        return e is not None and (self._scope is None or e.root_id == self._scope)

    def scope_rows(self) -> List[int]:
        return [r for r in range(len(self._entries)) if self.in_scope(r)]

    def entries(self) -> List[MangaEntry]:
        return list(self._entries)

    def _rebuild_dupe_map(self) -> None:
        """Build a map of mu_title -> list of row indices with that MU title (inside the library picked, if any)."""
        self._dupe_map.clear()
        for i, e in enumerate(self._entries):
            if e.mu_title is not None and (self._scope is None or e.root_id == self._scope):
                # Normalize for comparison (case-insensitive, strip whitespace)
                self._dupe_map.setdefault(e.mu_title.strip().lower(), []).append(i)

    def _dupe_signature(self, row: int):
        """What *row*'s Duplicates cell depends on that the scope changes: in scope, and the other rows of its title."""
        if not self.in_scope(row):
            return None
        e = self._entries[row]
        if e.mu_title is None:
            return ()
        return tuple(self._dupe_map.get(e.mu_title.strip().lower(), ()))

    def is_duplicate(self, row: int) -> bool:
        """Return True if this row shares an MU title with another row (of the library picked, if any)."""
        if row < 0 or row >= len(self._entries) or not self.in_scope(row):
            return False
        e = self._entries[row]
        if e.mu_title is None:
            return False
        key = e.mu_title.strip().lower()
        return len(self._dupe_map.get(key, [])) > 1

    def get_duplicate_rows(self, row: int) -> List[int]:
        """Return list of other row indices that share the same MU title."""
        if row < 0 or row >= len(self._entries) or not self.in_scope(row):
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

    def set_rename_counts(self, counts: Dict[str, int]) -> None:
        """Per series folder: how many files the renamer would rename (Rename pending: a flag in the State column, the
        Rename column and the "Rename pending" filter). Folders not listed have nothing pending."""
        new = {str(k): int(v) for k, v in counts.items() if int(v) > 0}
        if new == self._rename_counts:
            return
        flipped = {f for f in set(new) | set(self._rename_counts) if new.get(f) != self._rename_counts.get(f)}
        self._rename_counts = new
        rows = [r for r, e in enumerate(self._entries) if str(e.folder) in flipped]
        # only the series whose count changed are evaluated again (deferred: in the background, pushed in one step)
        self._invalidate([id(self._entries[r]) for r in rows])
        if rows and not self.deferred:
            self._pushing = True        # one repaint over them (the rows between keep their states)
            try:
                self.dataChanged.emit(self.index(min(rows), COL_STATE), self.index(max(rows), COL_ENGLISH),
                                      _STATE_ROLES)
            finally:
                self._pushing = False
        if self._entries:
            self.dataChanged.emit(self.index(0, COL_RENAME), self.index(len(self._entries) - 1, COL_RENAME),
                                  [Qt.DisplayRole, Qt.ToolTipRole, Qt.UserRole])

    def set_mode_chips(self, chips: Mapping[str, str]) -> None:
        """folder -> the automatic-downloads chip its Title cell shows (the series' own mode, where it differs from
        the default)."""
        new = {str(f): str(t) for f, t in chips.items() if t}
        if new == self._mode_chips:
            return
        flipped = {f for f in set(new) | set(self._mode_chips) if new.get(f) != self._mode_chips.get(f)}
        self._mode_chips = new
        for row, entry in enumerate(self._entries):
            if str(entry.folder) in flipped:
                idx = self.index(row, COL_TITLE)
                self.dataChanged.emit(idx, idx, [MODE_CHIP_ROLE])

    def rename_counts(self) -> Dict[str, int]:
        """The Rename pending counts per series folder (a folder scan's recount is merged into them)."""
        return dict(self._rename_counts)

    def rename_count_at(self, row: int) -> int:
        """How many files of row *row*'s folder the renamer would rename (0: none, or not counted yet)."""
        if row < 0 or row >= len(self._entries):
            return 0
        return self._rename_counts.get(str(self._entries[row].folder), 0)

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
        if role.__class__ is not int:
            role = int(role)
        row = index.row()
        e = self._entries[row]
        col = index.column()

        if role == _DISPLAY or role == _EDIT:
            if col == COL_EXAMINED:
                return "✓" if e.examined else ""
            if col == COL_DUPE:
                return self._duplicates_text(row)
            if col == COL_TITLE:
                if e.parent_folder is not None:
                    return f"{e.parent_folder.name} / {e.title}"
                return e.title
            if col == COL_ENG:
                return e.english_title or ""
            if col == COL_LIBRARY:
                return self.library_name(e.root_id)
            if col == COL_RENAME:
                n = self.rename_count_at(row)
                return f"{n} file{'s' if n != 1 else ''}" if n else ""
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
            if col in _MU_COLUMNS:
                v = self.mu_view(row)
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
                got = self._shown(row)
                return state_text(got[0]) if got else ""
            if col == COL_GAPS:
                got = self._shown(row)
                return got[0].gaps_text if got else ""
            if col == COL_OFFICIAL:
                got = self._shown(row)
                return _official_text(list(got[1]) if got else [])
            if col == COL_ENGLISH:
                got = self._shown(row)
                return english_text(got[2] if got else None)
            return None

        if role == _USER:
            return self._sort_value(row, e, col)

        if role == _SUBTITLE:
            if col != COL_TITLE:
                return None
            v = self.mu_view(row)
            return (v.mu_title or "") if v is not None else ""
        if role == _REVIEW:
            return needs_review(e) if col == COL_TITLE else None
        if role == _MODE_CHIP:
            return self._mode_chips.get(str(e.folder), "") if col == COL_TITLE else None
        if role == _STATE:
            if col != COL_STATE:
                return None
            got = self._shown(row)
            return got[0] if got else None
        if role == _PENDING:
            return self.state_pending_at(row) if col == COL_STATE else None

        if role == _ALIGNMENT:
            if col in _RIGHT_COLUMNS:
                return _ALIGN_RIGHT
            if col in _CENTER_COLUMNS:
                return _ALIGN_CENTER
            return None

        if role == _BACKGROUND:
            if self._mu_processing_row == row:
                return qcolor("busy_row")
            if e.examined:
                return qcolor("ok_bg")
            if col == COL_MU_TITLE and needs_review(e):
                return qcolor("warn_bg")
            return None

        if role == _TOOLTIP:
            if col == COL_EXAMINED:
                return "Examined" if e.examined else "Not examined"
            if col == COL_DUPE:
                return self._duplicates_tip(row)
            if col == COL_RENAME:
                n = self.rename_count_at(row)
                return (f"{n} file{'s' if n != 1 else ''} not named by the naming scheme (right-click: Rename to the "
                        "scheme)") if n else None
            if col == COL_MU_TITLE:
                tip = match_tooltip(e)
                if tip is not None:
                    return tip
            if col in (COL_LICENSED, COL_BEHIND, COL_COMPLETED):
                v = self.mu_view(row)
                if v is None:
                    got = self._shown(row)
                    st = got[0] if got else None
                    return (st.reasons[0] if st and st.reasons else None)
                if col == COL_LICENSED:
                    return "Licensed in English" if v.licensed is True else None
                if col == COL_BEHIND:
                    return _behind_tooltip(v) or str(e.folder)
                return _completed_tooltip(v)
            if col == COL_STATE:
                got = self._shown(row)
                return got[0].tooltip() if got else None
            if col == COL_GAPS:
                got = self._shown(row)
                return (got[0].gaps_tooltip() or "No gaps") if got else None
            if col == COL_OFFICIAL:
                got = self._shown(row)
                return _official_tooltip(list(got[1]) if got else []) or None
            # Default tooltip: folder path, with franchise info if applicable
            if e.parent_folder is not None:
                return f"Subseries of: {e.parent_folder.name}\n{e.folder}"
            return str(e.folder)

        return None

    def _sort_value(self, row: int, e: MangaEntry, col: int):
        """``data(UserRole)``: what column *col* sorts by (numbers for the counts and percentages)."""
        if col == COL_EXAMINED:
            return 1 if e.examined else 0
        if col == COL_DUPE:
            # the most duplicated first: numbers held twice, plus the other folders of the same series
            vols, chs = self.duplicate_files_at(row)
            return vols + chs + (len(self.get_duplicate_rows(row)) if self.is_duplicate(row) else 0)
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
        if col == COL_LIBRARY:
            return self.library_name(e.root_id).lower()
        if col == COL_RENAME:
            return self.rename_count_at(row)
        if col == COL_MTIME:
            return e.last_modified
        if col in _MU_COLUMNS:
            v = self.mu_view(row)   # sort what the cells show (MangaPixer's values when it knows the folder)
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
            got = self._shown(row)
            # STATE_ORDER rank first, then more missing units first (one number: Qt sorts it).
            rank, neg_missing = got[0].sort_key if got else (len(STATE_ORDER), 0)
            return rank * 1_000_000 + neg_missing
        if col == COL_GAPS:
            got = self._shown(row)
            return got[0].n_missing if got else -1
        if col == COL_OFFICIAL:
            got = self._shown(row)
            return _official_text(list(got[1]) if got else []).lower()
        if col == COL_ENGLISH:
            got = self._shown(row)
            return english_text(got[2] if got else None).lower()
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

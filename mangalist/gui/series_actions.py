"""The per-series actions every screen that shows a series offers, written once (owner, 2026-10-13, on the Download
tab's To get list: "Can I set auto download mode directly from here instead of going back to the List? Can you audit all
these missed connections?" - vault note "Manga-list - Series Actions Everywhere").

Each screen builds its own :class:`QMenu` and adds the shared entries through a :class:`SeriesMenu` - Automatic
downloads ▸, Unbundled chapters, Mark Behind as up to date / Clear it, Move to library ▸, Show in List, Open folder,
Copy title, Open MangaUpdates page, Open in MangaPixer, Fix MangaUpdates match… - in the order that screen wants, then its own entries
(the List's Exclude / Rename / Set aside / Review duplicates, the To get list's Check for new chapters / Change source,
a Found card's Show in To get / Skip all) next to them. After the menu ran, :meth:`SeriesMenu.run` does the chosen shared
action and returns the status line for it (None: not one of the shared entries - the screen's own).

What needs the List's data - the Unbundled and Behind settings, the MangaUpdates match, MangaPixer's page, selecting a
row - goes through a **series host**: the main window, handed to the screens (``set_series_host``). Duck-typed; a
missing method simply leaves its entry out, so a screen without a host (a test, the Download tab alone) still offers
what it can do itself::

    show_folder_in_list(folder)              select the series in the List tab
    open_series_folder(folder)               a file manager on it (the path copied where none opens)
    copy_series_title(title)
    series_mu_url(folder) -> str | None      its MangaUpdates page
    mangapixer_series_url(folder) -> str | None
    fix_mu_match_for(folder)                 the List's "Fix MangaUpdates match…" dialog
    unbundled_on(folders) -> bool            every one has Unbundled chapters on (the tick)
    set_unbundled_for(folders, on) -> str    the status line; the List's states and the To get list follow
    unbundled_first_ask(folders) -> ask|None its "first chapter to get" question (one series, the list unmapped)
    ask_unbundled_first_for(folder) -> str   asks it (again); the status line
    behind_counts(folders) -> (can mark, marked)
    set_behind_done(folders, done) -> str    Mark Behind as up to date / clear it; the states follow
    folder_for_series(series_id) -> str | None
    library_targets(folders) -> [(root id, name, path)]   Move to library ▸: the other library folders
    move_to_library(folders, root id) -> str              asks, moves, rescans; the status line
    last_library_move() -> str | None                     the "Undo last move (…)" label (None: nothing to undo)
    undo_library_move() -> str

The modes go straight to the downloads backend (``own_series_mode`` / ``set_series_mode`` / ``default_mode`` and,
schema 8, ``series_defaults``): :func:`add_mode_menu` is the one "Automatic downloads" submenu, and its "Use the default
(…)" entry names whose default it is - the library's or the one in Settings > Automation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from PySide6.QtCore import QPoint
from PySide6.QtGui import QAction, QActionGroup, QGuiApplication
from PySide6.QtWidgets import QMenu, QPushButton, QWidget

from .automation_rules import (
    DEFAULT_KEY,
    MODE_HELP,
    MODE_LABELS,
    MODES,
    common_choice,
    default_entry_tip,
    defaults_of,
    has_automation,
    selection_default,
    set_mode_text,
)
from .background import describe_error
from .download_widgets import button
from .links import OPEN_FOLDER_LABEL, open_folder, open_link
from .list_text import UNBUNDLED_FIRST_HELP, UNBUNDLED_FIRST_MENU, UNBUNDLED_HELP, UNBUNDLED_MENU

_log = logging.getLogger(__name__)

MENU_TITLE = "Automatic downloads"
NOT_SCANNED = "Not available until the folder is scanned"
SHOW_IN_LIST = "Show in List"
SHOW_IN_TO_GET = "Show in To get"
COPY_TITLE = "Copy title"
OPEN_MU = "Open MangaUpdates page"
OPEN_MANGAPIXER = "Open in MangaPixer"
FIX_MU = "Fix MangaUpdates match…"
CHECK_NEW_CHAPTERS = "Check for new chapters…"
CHANGE_SOURCE = "Change source…"
CHECK_NEW_TIP = ("Ask the series' Suwayomi source for chapters newer than the newest one you have - also before "
                 "MangaUpdates lists them")
CHANGE_SOURCE_TIP = ("Open the series' chapters and pick where they come from: MangaDex or your other Suwayomi sources. "
                     "Remembered for the series")
BEHIND_DONE_TIP = ("Treat the series as up to date: the List shows it Up to date, it leaves To get, and find passes skip "
                   "it (Clear 'up to date' override in the List brings it back)")


MOVE_MENU = "Move to library"
MOVE_TIP = ("Move the series folder into another library folder (one rename, undoable): it keeps its MangaUpdates link, "
            "settings and download history")
NO_OTHER_LIBRARY = "No other library folder"
UNDO_MOVE_TIP = "Put the series of the last move back where they were"


MENU_BUTTON_TEXT = "⋯"
PANEL_MENU_TIP = "This series: Show in List, Open folder, Automatic downloads"


def menu_button(tip: str = PANEL_MENU_TIP) -> QPushButton:
    """The small "⋯" button that opens a series menu (a Found card, the Download tab's series panels)."""
    out = button(MENU_BUTTON_TEXT, tip=tip)
    out.setObjectName("seriesMenuButton")
    out.setAccessibleName("Series actions")
    out.setFixedWidth(40)
    return out


def below(widget: QWidget) -> QPoint:
    """Where a menu opened from *widget* goes: under its left edge (global coordinates)."""
    return widget.mapToGlobal(widget.rect().bottomLeft())


def behind_labels(n: int, can_mark: int, marked: int) -> Tuple[str, str]:
    """The Behind pair as the List words it: ``Mark Behind as up to date`` / ``Clear 'up to date' override``, with the
    counts on a selection of several."""
    if n == 1:
        return "Mark Behind as up to date", "Clear 'up to date' override"
    return f"Mark Behind as up to date ({can_mark})", f"Clear 'up to date' override ({marked})"


@dataclass(frozen=True)
class SeriesRef:
    """A series as a screen knows it: its folder (None: this screen does not know it), its store id (None: not
    scanned yet) and the title it shows."""

    folder: Optional[str]
    series_id: Optional[int] = None
    title: str = ""


# --- the automatic-download mode ----------------------------------------------------------------------------

def add_mode_menu(menu: QMenu, backend, series_ids: Sequence[int],
                  title: str = MENU_TITLE) -> Dict[QAction, Optional[str]]:
    """Add the "Automatic downloads" submenu for *series_ids* to *menu*: Off / Notify / Auto, then "Use the default
    (Notify)" / "Use the library's default (Off)"; the entry every series has is ticked. Returns action -> mode (None:
    the default); {} with the submenu off when the series are not scanned or the modes cannot be read."""
    sub = menu.addMenu(title)
    ids = sorted({int(i) for i in series_ids if i is not None})
    if not ids or not has_automation(backend):
        sub.setEnabled(False)
        sub.setToolTip(NOT_SCANNED)
        return {}
    try:
        owns = [backend.own_series_mode(sid) for sid in ids]
        default_label, library = selection_default(defaults_of(backend, ids).values())
    except Exception as exc:  # noqa: BLE001 - the submenu says why it is off
        _log.warning("Reading the automatic-download modes failed: %s", describe_error(exc))
        sub.setEnabled(False)
        return {}
    tick = common_choice(owns)
    group = QActionGroup(sub)
    group.setExclusive(True)
    out: Dict[QAction, Optional[str]] = {}
    for mode in MODES:
        act = sub.addAction(MODE_LABELS[mode])
        act.setCheckable(True)
        act.setChecked(tick == mode)
        act.setToolTip(MODE_HELP[mode])
        group.addAction(act)
        out[act] = mode
    sub.addSeparator()
    act = sub.addAction(default_label)
    act.setCheckable(True)
    act.setChecked(tick == DEFAULT_KEY)
    act.setToolTip(default_entry_tip(library))
    group.addAction(act)
    out[act] = None
    sub.setToolTipsVisible(True)
    return out


def apply_mode(backend, series_ids: Sequence[int], mode: Optional[str]) -> Tuple[str, bool]:
    """Set the own mode of *series_ids* (None: back to their default); (the status line, True when it was set)."""
    ids = sorted({int(i) for i in series_ids if i is not None})
    if not ids:
        return "Automatic downloads: these folders are not scanned yet.", False
    try:
        backend.set_series_mode(ids, mode)
        pairs = set(defaults_of(backend, ids).values()) if mode is None else set()
    except Exception as exc:  # noqa: BLE001 - shown, nothing assumed changed
        return f"Could not change the automatic downloads: {describe_error(exc)}", False
    _log.info("Automatic downloads: %s for %d series", mode or "the default", len(ids))
    default, library = pairs.pop() if len(pairs) == 1 else (None, False)
    return set_mode_text(mode, len(ids), default, library), True


# --- the shared menu entries ---------------------------------------------------------------------------------

class SeriesMenu:
    """The shared per-series entries of one menu over *refs* (one series, or a selection of several): add the ones the
    screen wants, where it wants them, then :meth:`run` the chosen action.

    *show_in_list*: the screen's own way to the List (the Download tab's signal); else the host's. *on_change*: called
    with ``"modes"`` / ``"unbundled"`` / ``"behind"`` after such a change, so the screen refreshes what it shows (the
    host refreshes the List itself)."""

    def __init__(self, menu: QMenu, refs: Sequence[SeriesRef], backend=None, host=None,
                 show_in_list: Optional[Callable[[str], object]] = None,
                 on_change: Optional[Callable[[str], object]] = None):
        self.menu = menu
        self.refs = list(refs)
        self.backend = backend
        self.host = host
        self._show_in_list = show_in_list or self._host_fn("show_folder_in_list")
        self._on_change = on_change
        self._do: Dict[QAction, Callable[[], Optional[str]]] = {}
        self._modes: Dict[QAction, Optional[str]] = {}
        self._apply_mode: Optional[Callable[[Optional[str]], str]] = None
        menu.setToolTipsVisible(True)

    # --- what the menu is about

    @property
    def single(self) -> Optional[SeriesRef]:
        return self.refs[0] if len(self.refs) == 1 else None

    def folders(self) -> List[str]:
        return [r.folder for r in self.refs if r.folder]

    def series_ids(self) -> List[int]:
        return sorted({r.series_id for r in self.refs if r.series_id is not None})

    def _host_fn(self, name: str) -> Optional[Callable]:
        fn = getattr(self.host, name, None) if self.host is not None else None
        return fn if callable(fn) else None

    def _add(self, text: str, do: Callable[[], Optional[str]], tip: str = "", enabled: bool = True,
             menu: Optional[QMenu] = None) -> QAction:
        act = (menu or self.menu).addAction(text)
        act.setToolTip(tip)
        act.setEnabled(enabled)
        self._do[act] = do
        return act

    def _changed(self, kind: str) -> None:
        if self._on_change is not None:
            self._on_change(kind)

    # --- the entries (each returns its action, or None when this screen cannot offer it)

    def add_modes(self, apply: Optional[Callable[[Optional[str]], str]] = None) -> Dict[QAction, Optional[str]]:
        """Automatic downloads ▸ for the series (*apply*: the screen's own setter - the List's keeps its chips' cache -
        returning the status line; else :func:`apply_mode`)."""
        if self.backend is None or not has_automation(self.backend):
            return {}
        self._modes = add_mode_menu(self.menu, self.backend, self.series_ids())
        self._apply_mode = apply
        return self._modes

    def add_unbundled(self) -> Optional[QAction]:
        """Unbundled chapters (a tick: every series has it on), then - for one series with it on whose volume list does
        not say where its newest volume ends - "First chapter to get…" (owner, 2026-10-15: asked once, editable)."""
        is_on, set_on = self._host_fn("unbundled_on"), self._host_fn("set_unbundled_for")
        folders = self.folders()
        if is_on is None or set_on is None or not folders:
            return None
        on = bool(is_on(folders))
        act = self._add(UNBUNDLED_MENU, lambda: self._unbundled(set_on, folders, not on), UNBUNDLED_HELP)
        act.setCheckable(True)
        act.setChecked(on)
        first_ask, ask_first = self._host_fn("unbundled_first_ask"), self._host_fn("ask_unbundled_first_for")
        if on and len(folders) == 1 and first_ask is not None and ask_first is not None and first_ask(folders):
            self._add(UNBUNDLED_FIRST_MENU, lambda: self._unbundled_first(ask_first, folders[0]), UNBUNDLED_FIRST_HELP)
        return act

    def _unbundled_first(self, ask_first, folder) -> str:
        text = ask_first(folder)
        self._changed("unbundled")
        return text or ""

    def _unbundled(self, set_on, folders, on: bool) -> str:
        text = set_on(folders, on)
        self._changed("unbundled")
        return text or ""

    def add_behind(self) -> Tuple[Optional[QAction], Optional[QAction]]:
        """Mark Behind as up to date / Clear 'up to date' override (a series marked up to date is not searched by the
        find pass - :func:`mangalist.downloads.finder.up_to_date_folders`)."""
        counts, set_done = self._host_fn("behind_counts"), self._host_fn("set_behind_done")
        folders = self.folders()
        if counts is None or set_done is None or not folders:
            return None, None
        can_mark, marked = counts(folders)
        mark_text, clear_text = behind_labels(len(folders), can_mark, marked)
        mark = self._add(mark_text, lambda: self._behind(set_done, folders, True), BEHIND_DONE_TIP, can_mark > 0)
        clear = self._add(clear_text, lambda: self._behind(set_done, folders, False),
                          "Count the series' chapter gaps again", marked > 0)
        return mark, clear

    def _behind(self, set_done, folders, done: bool) -> str:
        text = set_done(folders, done)
        self._changed("behind")
        return text or ""

    def add_move_to_library(self) -> Optional[QMenu]:
        """Move to library ▸: the other library folders by name (each moves the series there, after the host's
        confirmation), then "Undo last move (…)" while the last move can be undone (owner, 2026-10-15)."""
        targets, move = self._host_fn("library_targets"), self._host_fn("move_to_library")
        folders = self.folders()
        if targets is None or move is None or not folders:
            return None
        sub = self.menu.addMenu(MOVE_MENU)
        sub.setToolTipsVisible(True)
        sub.menuAction().setToolTip(MOVE_TIP)
        try:
            found = list(targets(folders))
        except Exception as exc:  # noqa: BLE001 - the submenu says why it is off
            _log.warning("Reading the library folders failed: %s", describe_error(exc))
            found = []
        for root_id, name, path in found:
            self._add(name, lambda rid=root_id: move(folders, rid) or "", str(path), menu=sub)
        if not found:
            sub.addAction(NO_OTHER_LIBRARY).setEnabled(False)
        last, undo = self._host_fn("last_library_move"), self._host_fn("undo_library_move")
        label = last() if last is not None and undo is not None else None
        if label:
            sub.addSeparator()
            self._add(label, lambda: undo() or "", UNDO_MOVE_TIP, menu=sub)
        return sub

    def add_show_in_list(self) -> Optional[QAction]:
        ref = self.single
        if self._show_in_list is None or ref is None:
            return None
        return self._add(SHOW_IN_LIST, lambda: self._call(self._show_in_list, ref.folder),
                         "Select this series in the List tab", bool(ref.folder))

    def add_open_folder(self) -> Optional[QAction]:
        ref = self.single
        if ref is None:
            return None
        return self._add(OPEN_FOLDER_LABEL, lambda: self._open_folder(ref.folder), "", bool(ref.folder))

    def _open_folder(self, folder: str) -> str:
        own = self._host_fn("open_series_folder")
        if own is not None:
            own(folder)
            return ""
        if open_folder(folder):
            return ""
        QGuiApplication.clipboard().setText(str(folder))
        return f"No file manager here - folder path copied: {folder}"

    def add_copy_title(self) -> Optional[QAction]:
        ref = self.single
        if ref is None or not ref.title:
            return None
        return self._add(COPY_TITLE, lambda: self._copy_title(ref.title))

    def _copy_title(self, title: str) -> str:
        own = self._host_fn("copy_series_title")
        if own is not None:
            own(title)
            return ""
        QGuiApplication.clipboard().setText(title)
        return f"Copied title: {title}"

    def add_mu_page(self, url: Optional[str] = None) -> Optional[QAction]:
        """Open MangaUpdates page (off when the series has no match); *url*: the screen knows it already."""
        ref = self.single
        lookup = self._host_fn("series_mu_url")
        if ref is None or (url is None and (lookup is None or not ref.folder)):
            return None
        if url is None:
            url = lookup(ref.folder) or ""
        return self._add(OPEN_MU, lambda: self._open_url(url), "", bool(url))

    def add_mangapixer(self) -> Optional[QAction]:
        """Open in MangaPixer (only when MangaPixer knows the series)."""
        ref = self.single
        lookup = self._host_fn("mangapixer_series_url")
        if ref is None or lookup is None or not ref.folder:
            return None
        url = lookup(ref.folder)
        return self._add(OPEN_MANGAPIXER, lambda: self._open_url(url)) if url else None

    def _open_url(self, url: str) -> str:
        return "" if open_link(url) else f"No browser here - link copied: {url}"

    def add_fix_mu(self) -> Optional[QAction]:
        ref = self.single
        fix = self._host_fn("fix_mu_match_for")
        if ref is None or fix is None or not ref.folder:
            return None
        return self._add(FIX_MU, lambda: self._call(fix, ref.folder), "Pick the series on MangaUpdates yourself")

    def add_action(self, text: str, do: Callable[[], Optional[str]], tip: str = "", enabled: bool = True) -> QAction:
        """A screen's own entry, run by :meth:`run` like the shared ones."""
        return self._add(text, do, tip, enabled)

    @staticmethod
    def _call(fn, *args) -> str:
        fn(*args)
        return ""

    # --- running the chosen one

    def run(self, chosen: Optional[QAction]) -> Optional[str]:
        """Do *chosen* when it is one of this menu's entries; its status line ('' when it has none). None: not ours."""
        if chosen is None:
            return None
        if chosen in self._modes:
            mode = self._modes[chosen]
            if self._apply_mode is not None:
                text = self._apply_mode(mode)
            else:
                text, _ok = apply_mode(self.backend, self.series_ids(), mode)
            self._changed("modes")
            return text
        do = self._do.get(chosen)
        return do() if do is not None else None

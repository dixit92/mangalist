"""The List tab's side of automatic downloads (owner decisions 2026-10-10): each series' Off / Notify / Auto mode - the
row menu's "Automatic downloads" submenu (also on a multi-selection; built by :func:`.series_actions.add_mode_menu`,
the one every screen uses), the details panel's picker and a small chip with the series' own mode on the rows whose
mode differs from the default it follows (owner, 2026-10-11) - and the bulk find's menu entry.

The default a series follows is its library's when the library sets one (Settings > Library > Edit, schema 8; owner,
2026-10-13: "Can I set Automatic Downloads [Off/Notify/Auto] per library?"), else the one in Settings > Automation; every
line that shows it says whose it is.

:class:`AutoModes` keeps folder -> mode in force (and the default it follows) for the rows shown, read off the UI thread
after each scan (one ``series_id_for`` per folder, then one ``series_modes`` and one ``series_defaults``); the menu and
the details read the series' own mode with the contract's quick calls. Writing goes through ``set_series_mode`` (quick).
Nothing here touches the library.
"""

from __future__ import annotations

import logging
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu

from .automation_rules import MODE_AUTO, MODE_HELP, MODE_LABELS, default_of, defaults_of, has_automation, mode_text
from .background import describe_error, start_call
from .series_actions import MENU_TITLE, NOT_SCANNED, add_mode_menu, apply_mode

_log = logging.getLogger(__name__)

__all__ = ["AutoModes", "MENU_TITLE"]


class AutoModes(QObject):
    changed = Signal()              # the modes were (re-)read or set: the chips and the details follow

    def __init__(self, backend, parent: Optional[QObject] = None,
                 background: Optional[Callable] = None):
        super().__init__(parent)
        self._backend = backend
        self._background = background or start_call
        self._ids: Dict[str, Optional[int]] = {}        # folder -> series id (None: not scanned into the store yet)
        self._modes: Dict[str, str] = {}                # folder -> the mode in force
        self._defaults: Dict[str, str] = {}             # folder -> the default it follows (its library's or the setting)
        self._default: Optional[str] = None             # the default setting, as of the last read
        self._signature: Optional[tuple] = None
        self._call = None
        self._running = False
        self._pending: Optional[List[str]] = None       # folders asked for while a read ran

    @staticmethod
    def available(backend) -> bool:
        return has_automation(backend)

    # --- reading -------------------------------------------------------------------------------------

    def refresh(self, folders: Sequence[str], force: bool = False) -> bool:
        """Re-read the modes of *folders* off the UI thread (skipped when the folders did not change, unless *force*).
        Returns True when a read started (or was queued behind the running one)."""
        folders = [str(f) for f in folders]
        signature = tuple(sorted(folders))
        if signature == self._signature and not force:
            return False
        self._signature = signature
        if self._running:
            self._pending = folders
            return True
        backend = self._backend

        def run():
            ids = {}
            for folder in folders:
                try:
                    ids[folder] = backend.series_id_for(folder)
                except Exception:  # noqa: BLE001 - that row gets no chip
                    ids[folder] = None
            known = sorted({i for i in ids.values() if i is not None})
            modes = dict(backend.series_modes(known)) if known else {}
            return ids, modes, defaults_of(backend, known), backend.default_mode()

        self._running = True
        call = self._background(run, self._loaded,
                                lambda message: _log.warning("Reading the automatic-download modes: %s", message),
                                self._finished)
        self._call = call if self._running else None       # a synchronous runner (tests) has finished already
        return True

    def _loaded(self, result) -> None:
        ids, modes, defaults, self._default = result
        self._ids.update(ids)
        self._modes = {folder: modes[sid] for folder, sid in ids.items() if sid is not None and sid in modes}
        self._defaults = {folder: defaults[sid][0] for folder, sid in ids.items() if sid is not None and sid in defaults}
        self.changed.emit()

    def _finished(self) -> None:
        self._call = None
        self._running = False
        if self._pending is not None:
            folders, self._pending = self._pending, None
            self.refresh(folders, force=True)

    def auto_folders(self) -> set:
        """The folders whose mode in force is Auto."""
        return {folder for folder, mode in self._modes.items() if mode == MODE_AUTO}

    def default_of(self, folder: str) -> Optional[str]:
        """The default the folder's series follows (its library's, else the setting), as of the last read."""
        return self._defaults.get(str(folder), self._default)

    def chips(self) -> Dict[str, str]:
        """folder -> the chip its row shows: the series' own mode (``Auto`` / ``Notify`` / ``Off``) where it differs
        from the default it follows; the rows on their default show none (owner, 2026-10-11: with an Auto default every
        row would carry the chip - likewise every series of a library set to Off)."""
        return {folder: MODE_LABELS.get(mode, mode) for folder, mode in self._modes.items()
                if self.default_of(folder) is not None and mode != self.default_of(folder)}

    def mode_of(self, folder: str) -> Optional[str]:
        return self._modes.get(str(folder))

    def series_id(self, folder: str) -> Optional[int]:
        """The folder's series id (cached; a quick backend read the first time)."""
        folder = str(folder)
        if self._ids.get(folder) is None:
            try:
                self._ids[folder] = self._backend.series_id_for(folder)
            except Exception:  # noqa: BLE001 - the menu says it cannot
                self._ids[folder] = None
        return self._ids[folder]

    def folder_of(self, series_id: int) -> Optional[str]:
        """The folder of a series id, from the ids read so far (None: not among the List's rows read)."""
        return next((folder for folder, sid in self._ids.items() if sid == series_id), None)

    def series_ids(self, folders: Iterable[str]) -> List[int]:
        return sorted({sid for sid in (self.series_id(f) for f in folders) if sid is not None})

    def detail(self, folder: str) -> Tuple[str, str]:
        """(text, tooltip) of the details panel's line: ``Auto`` / ``Notify (the default)`` / ``Off (the library's
        default)``; a folder not scanned into the store yet says so."""
        state = self.detail_state(folder)
        if state is None:
            return NOT_SCANNED, ""
        if isinstance(state, str):
            return f"Not known ({state})", ""
        own, default, library = state
        tip = MODE_HELP.get(own or default, "") + "\nChange it here or in the row menu: Automatic downloads."
        return mode_text(own, default, library), tip

    def detail_state(self, folder: str):
        """(the series' own mode or None, the default it follows, True when that is its library's) for the details
        panel's picker; None when the folder is not scanned yet, the error's text when the modes cannot be read."""
        sid = self.series_id(folder)
        if sid is None:
            return None
        try:
            own = self._backend.own_series_mode(sid)
            default, library = default_of(self._backend, sid)
        except Exception as exc:  # noqa: BLE001 - a line only
            return describe_error(exc)
        return own, default, library

    # --- the row menu --------------------------------------------------------------------------------

    def add_menu(self, menu: QMenu, folders: Sequence[str]) -> Dict[QAction, Optional[str]]:
        """Add the "Automatic downloads" submenu for *folders* to *menu* (:func:`.series_actions.add_mode_menu`).
        Returns action -> mode (None: the default)."""
        return add_mode_menu(menu, self._backend, self.series_ids(folders))

    def apply(self, folders: Sequence[str], mode: Optional[str]) -> str:
        """Set the own mode of *folders*' series (None: back to their default); the status line to show."""
        ids = self.series_ids(folders)
        text, ok = apply_mode(self._backend, ids, mode)
        if not ok:
            return text
        for folder in folders:
            folder = str(folder)
            if self.series_id(folder) is not None:
                self._modes[folder] = mode or self.default_of(folder) or self._backend.default_mode()
        self.changed.emit()
        return text

    def stop(self) -> None:
        if self._call is not None:
            self._call.abandon()
            self._call = None
        self._running = False
        self._pending = None

"""The List tab's side of automatic downloads (owner decisions 2026-10-10): each series' Off / Notify / Auto mode - the
row menu's "Automatic downloads" submenu (also on a multi-selection), the details panel's line and the small "Auto" chip
on the rows whose mode in force is Auto - and the bulk find's menu entry.

:class:`AutoModes` keeps folder -> mode in force for the rows shown, read off the UI thread after each scan (one
``series_id_for`` per folder, then one ``series_modes``); the menu and the details read the series' own mode with the
contract's quick calls. Writing goes through ``set_series_mode`` (quick). Nothing here touches the library.
"""

from __future__ import annotations

import logging
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QMenu

from .automation_rules import (
    DEFAULT_KEY,
    MODE_AUTO,
    MODE_HELP,
    MODE_LABELS,
    MODES,
    common_choice,
    default_entry_label,
    has_automation,
    mode_text,
    set_mode_text,
)
from .background import describe_error, start_call

_log = logging.getLogger(__name__)

MENU_TITLE = "Automatic downloads"


class AutoModes(QObject):
    changed = Signal()              # the modes were (re-)read or set: the chips and the details follow

    def __init__(self, backend, parent: Optional[QObject] = None,
                 background: Optional[Callable] = None):
        super().__init__(parent)
        self._backend = backend
        self._background = background or start_call
        self._ids: Dict[str, Optional[int]] = {}        # folder -> series id (None: not scanned into the store yet)
        self._modes: Dict[str, str] = {}                # folder -> the mode in force
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
            return ids, (dict(backend.series_modes(known)) if known else {})

        self._running = True
        call = self._background(run, self._loaded,
                                lambda message: _log.warning("Reading the automatic-download modes: %s", message),
                                self._finished)
        self._call = call if self._running else None       # a synchronous runner (tests) has finished already
        return True

    def _loaded(self, result) -> None:
        ids, modes = result
        self._ids.update(ids)
        self._modes = {folder: modes[sid] for folder, sid in ids.items() if sid is not None and sid in modes}
        self.changed.emit()

    def _finished(self) -> None:
        self._call = None
        self._running = False
        if self._pending is not None:
            folders, self._pending = self._pending, None
            self.refresh(folders, force=True)

    def auto_folders(self) -> set:
        """The folders whose mode in force is Auto (their rows show the chip)."""
        return {folder for folder, mode in self._modes.items() if mode == MODE_AUTO}

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

    def series_ids(self, folders: Iterable[str]) -> List[int]:
        return sorted({sid for sid in (self.series_id(f) for f in folders) if sid is not None})

    def detail(self, folder: str) -> Tuple[str, str]:
        """(text, tooltip) of the details panel's line: ``Auto`` / ``Notify (the default)``; a folder not scanned into
        the store yet says so."""
        sid = self.series_id(folder)
        if sid is None:
            return "Not available until the folder is scanned", ""
        try:
            own = self._backend.own_series_mode(sid)
            default = self._backend.default_mode()
        except Exception as exc:  # noqa: BLE001 - a line only
            return f"Not known ({describe_error(exc)})", ""
        tip = MODE_HELP.get(own or default, "") + "\nChange it in the row menu: Automatic downloads."
        return mode_text(own, default), tip

    # --- the row menu --------------------------------------------------------------------------------

    def add_menu(self, menu: QMenu, folders: Sequence[str]) -> Dict[QAction, Optional[str]]:
        """Add the "Automatic downloads" submenu for *folders* to *menu*: Off / Notify / Auto, then "Use the default
        (Notify)"; the entry every series of the selection has is ticked. Returns action -> mode (None: the default)."""
        sub = menu.addMenu(MENU_TITLE)
        ids = self.series_ids(folders)
        if not ids:
            sub.setEnabled(False)
            sub.setToolTip("Not available until the folder is scanned")
            return {}
        try:
            owns = [self._backend.own_series_mode(sid) for sid in ids]
            default = self._backend.default_mode()
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
        act = sub.addAction(default_entry_label(default))
        act.setCheckable(True)
        act.setChecked(tick == DEFAULT_KEY)
        act.setToolTip("Follow the default set in Settings > Automation")
        group.addAction(act)
        out[act] = None
        sub.setToolTipsVisible(True)
        return out

    def apply(self, folders: Sequence[str], mode: Optional[str]) -> str:
        """Set the own mode of *folders*' series (None: back to the default); the status line to show."""
        ids = self.series_ids(folders)
        if not ids:
            return "Automatic downloads: these folders are not scanned yet."
        try:
            self._backend.set_series_mode(ids, mode)
            default = self._backend.default_mode()
        except Exception as exc:  # noqa: BLE001 - shown, nothing assumed changed
            return f"Could not change the automatic downloads: {describe_error(exc)}"
        _log.info("Automatic downloads: %s for %d series", mode or "the default", len(ids))
        in_force = mode or default
        for folder in folders:
            if self.series_id(folder) is not None:
                self._modes[str(folder)] = in_force
        self.changed.emit()
        return set_mode_text(mode, len(ids), default)

    def stop(self) -> None:
        if self._call is not None:
            self._call.abandon()
            self._call = None
        self._running = False
        self._pending = None

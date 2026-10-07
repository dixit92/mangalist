"""The volumes MVP's part of the main window: toolbar buttons, the row-menu action, the Wanted panel and detail
panel hooks, and the download records behind the status texts.

The main window builds one :class:`VolumesController` only when downloads are switched on and a backend exists
(:func:`~mangalist.gui.downloads_backend.create_backend`); without one nothing of the volumes GUI is created.
The records are read off the UI thread (at start, after the dialogs close, after a send, and every minute while
the window is open) and cached; the panels read the cache.
"""

from __future__ import annotations

import logging
from typing import Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import QObject, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QToolBar, QWidget

from ..downloads.contracts import DownloadRecord
from ..models import MangaEntry
from .background import BackgroundCall, start_call
from .downloads_backend import DownloadsBackend
from .volumes_target import (
    Availability,
    find_volumes_availability,
    latest_by_series,
    status_text,
    status_tooltip,
)

_log = logging.getLogger(__name__)

REFRESH_MS = 60_000
FIND_VOLUMES_LABEL = "Find volumes on nyaa…"


class VolumesController(QObject):
    def __init__(self, window: QWidget, backend: DownloadsBackend, model, wanted, detail,
                 set_status: Callable[[str], None]):
        super().__init__(window)
        self._window = window
        self.backend = backend
        self._model = model
        self._wanted = wanted
        self._detail = detail
        self._set_status = set_status
        self._series_ids: Dict[str, Optional[int]] = {}
        self.records: List[DownloadRecord] = []
        self._latest: Dict[int, DownloadRecord] = {}
        self._call: Optional[BackgroundCall] = None
        self._again = False                     # a refresh was asked for while one was running
        self._detail_row: Optional[int] = None
        # The dialog openers: tests (and the integrator) can swap them.
        from .downloads_dialog import open_downloads_dialog
        from .nyaa_dialog import open_nyaa_dialog
        from .qbittorrent_dialog import open_qbittorrent_dialog

        self.run_nyaa = open_nyaa_dialog
        self.run_qbittorrent = open_qbittorrent_dialog
        self.run_downloads = open_downloads_dialog

        wanted.set_downloads(self._wanted_availability, self.status_for_row)
        wanted.find_volumes_requested.connect(self.open_find_volumes)
        detail.set_downloads_enabled(True)
        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self.refresh_records)
        self._timer.start()
        QTimer.singleShot(0, self.refresh_records)

    # --- toolbar and menu ----------------------------------------------------------------------------

    def add_toolbar_buttons(self, toolbar: QToolBar, make_button: Callable[[str], QWidget],
                            spacer: Callable[[int], QWidget], before: Optional[QAction] = None) -> None:
        """qBittorrent… and Downloads…, inserted before the *before* action (appended when None)."""
        btn_qbt = make_button("qBittorrent…")
        btn_qbt.setToolTip("Where MangaList sends the volumes you pick on nyaa (qBittorrent Web UI)")
        btn_qbt.clicked.connect(self.open_qbittorrent)
        add = (lambda w: toolbar.insertWidget(before, w)) if before is not None else toolbar.addWidget
        add(btn_qbt)
        add(spacer(6))
        btn_dl = make_button("Downloads…")
        btn_dl.setToolTip("Volumes sent to qBittorrent and where each one stands")
        btn_dl.clicked.connect(self.open_downloads)
        add(btn_dl)
        add(spacer(6))
        self.btn_qbittorrent, self.btn_downloads = btn_qbt, btn_dl

    def add_row_action(self, menu: QMenu, src_row: int) -> QAction:
        """"Find volumes on nyaa…" in a one-series row menu: enabled only where the rule allows, otherwise
        disabled with the reason as its tooltip."""
        availability = self.availability(src_row)
        action = menu.addAction(FIND_VOLUMES_LABEL)
        action.setEnabled(availability.enabled)
        action.setToolTip("Search nyaa for this series' missing volumes" if availability.enabled
                          else availability.reason)
        menu.setToolTipsVisible(True)
        return action

    # --- the rule ------------------------------------------------------------------------------------

    def series_id_for(self, entry: MangaEntry) -> Optional[int]:
        key = str(entry.folder)
        if key not in self._series_ids:
            try:
                self._series_ids[key] = self.backend.series_id_for(key)
            except Exception as exc:  # noqa: BLE001 - a broken lookup disables the action, nothing more
                _log.warning("series lookup failed: %s", type(exc).__name__)
                self._series_ids[key] = None
        return self._series_ids[key]

    def forget_series_ids(self) -> None:
        """A scan changed the store's series rows: look the folders up again."""
        self._series_ids.clear()

    def availability(self, src_row: int) -> Availability:
        entry = self._model.entry_at(src_row)
        if entry is None:
            return Availability(False, "")
        return find_volumes_availability(
            series_id=self.series_id_for(entry), folder=str(entry.folder), title=entry.title,
            english_title=entry.english_title, knowledge=self._model.knowledge_at(src_row),
            state=self._model.state_at(src_row), held=self._model.held_volumes_at(src_row))

    def _wanted_availability(self, src_row: int) -> Tuple[bool, str]:
        got = self.availability(src_row)
        return got.enabled, got.reason

    # --- actions -------------------------------------------------------------------------------------

    def open_find_volumes(self, src_row: int) -> bool:
        availability = self.availability(src_row)
        if not availability.enabled or availability.target is None:
            self._set_status(availability.reason or "Nothing to look for.")
            return False
        dlg = self.run_nyaa(self._window, self.backend, availability.target)
        if getattr(dlg, "sent_records", None):
            self._set_status(f"Sent to qBittorrent: {dlg.sent_records[-1].title}")
        self.refresh_records()
        return True

    def open_qbittorrent(self) -> None:
        self.run_qbittorrent(self._window, self.backend)

    def open_downloads(self) -> None:
        self.run_downloads(self._window, self.backend, self._series_name)
        self.refresh_records()

    def _series_name(self, series_id: int) -> str:
        for row in range(self._model.rowCount()):
            entry = self._model.entry_at(row)
            if entry is not None and self.series_id_for(entry) == series_id:
                return entry.title
        return f"Series #{series_id}"

    # --- download status -----------------------------------------------------------------------------

    def refresh_records(self) -> bool:
        """Read every record off the UI thread, then repaint the status texts. One read at a time: a request
        made while one runs (a send just finished) is run right after it, so the newest state is never missed."""
        if self._call is not None:
            self._again = True
            return True
        backend = self.backend
        self._call = start_call(lambda: list(backend.records()), self._on_records, self._on_records_failed)
        self._call.finished.connect(self._on_call_finished)
        return True

    def _on_records(self, records) -> None:
        self.records = list(records)
        self._latest = latest_by_series(self.records)
        self._wanted.refresh_downloads()
        self.show_in_detail(self._detail_row)

    def _on_records_failed(self, message: str) -> None:
        _log.warning("could not read the download records: %s", message)

    def _on_call_finished(self) -> None:
        self._call = None
        if self._again:
            self._again = False
            self.refresh_records()

    def status_for_row(self, src_row: int) -> Optional[Tuple[str, str]]:
        """(text, tooltip) of the newest download of the series in *src_row*, or None."""
        if not self._latest:
            return None
        entry = self._model.entry_at(src_row)
        if entry is None:
            return None
        series_id = self.series_id_for(entry)
        record = self._latest.get(series_id) if series_id is not None else None
        return (status_text(record), status_tooltip(record)) if record is not None else None

    def show_in_detail(self, src_row: Optional[int]) -> None:
        """Show the download status of the series selected in the table (None: no selection)."""
        self._detail_row = src_row
        got = self.status_for_row(src_row) if src_row is not None else None
        self._detail.set_download(*(got or (None, "")))

    def stop(self) -> None:
        self._timer.stop()
        self._again = False
        if self._call is not None:
            self._call.abandon()

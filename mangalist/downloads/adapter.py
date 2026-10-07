"""The volumes GUI's backend over the real pieces (integrator wiring, volumes MVP). No Qt here.

:mod:`mangalist.gui.downloads_backend` imports this module when downloads are switched on and calls
:func:`create_backend` with the main window's store. Each :class:`~mangalist.gui.downloads_backend.DownloadsBackend`
method maps to one piece: the library store (series lookup), :func:`~mangalist.downloads.placement.placement_for`,
:class:`~mangalist.services.nyaa.NyaaSearch`, :func:`~mangalist.downloads.service.send_pick`, the
:class:`~mangalist.store.downloads.DownloadLedger` (records, connection, settings) and the qBittorrent client.
Failures the owner should read become :class:`~mangalist.gui.downloads_backend.BackendError` with the service's own
message (the clients never put a password in one).
"""

from __future__ import annotations

import logging
import threading
from dataclasses import replace
from typing import Callable, Optional, Sequence

from ..gui.downloads_backend import BackendError, QbtSettings
from ..services.nyaa import NyaaError, NyaaSearch
from ..services.qbittorrent import QbtError, client_from_connection, normalize_base_url
from ..store.downloads import DownloadLedger
from .contracts import DownloadRecord, NyaaCandidate, Placement, QbtConnection, TorrentClient
from .placement import placement_for
from .service import SendRefused, send_pick

_log = logging.getLogger(__name__)


class Backend:
    def __init__(self, db, *, search: Optional[NyaaSearch] = None,
                 client_factory: Callable[[QbtConnection], TorrentClient] = client_from_connection):
        self.db = db
        self.ledger = DownloadLedger(db)
        self._search = search
        self._search_lock = threading.Lock()    # one search at a time: nyaa's politeness delay is per client
        self._client_factory = client_factory

    # --- library ---------------------------------------------------------------------------------------

    def series_id_for(self, folder: str) -> Optional[int]:
        series = self.db.series_for_folder(folder)
        return series.id if series is not None else None

    def placement(self, series_id: int) -> Placement:
        try:
            return placement_for(self.db, series_id)
        except LookupError as exc:
            raise BackendError(str(exc)) from None

    # --- nyaa ------------------------------------------------------------------------------------------

    def search(self, titles: Sequence[str], missing: Sequence[str], held: Sequence[str]) -> Sequence[NyaaCandidate]:
        with self._search_lock:
            if self._search is None:
                self._search = NyaaSearch()
            try:
                return self._search.search(titles, missing, held)
            except NyaaError as exc:
                raise BackendError(f"nyaa: {exc}") from None

    # --- qBittorrent -----------------------------------------------------------------------------------

    def send(self, series_id: int, candidate: NyaaCandidate, wanted_volumes: Sequence[str],
             target_dir: str) -> DownloadRecord:
        conn = self.ledger.connection()
        if conn is None or not conn.base_url:
            raise BackendError("qBittorrent is not set up yet (toolbar: qBittorrent...)")
        placement = replace(self.placement(series_id), target_dir=target_dir, options=())
        try:
            return send_pick(self._client_factory(conn), self.ledger, series_id, candidate, wanted_volumes,
                             placement, self.ledger.save_path())
        except (SendRefused, QbtError) as exc:
            raise BackendError(str(exc)) from None

    def records(self, series_id: Optional[int] = None) -> Sequence[DownloadRecord]:
        return self.ledger.for_series(series_id) if series_id is not None else self.ledger.all_records()

    def load_settings(self) -> QbtSettings:
        conn = self.ledger.connection()
        return QbtSettings(
            base_url=conn.base_url if conn else "",
            username=conn.username if conn else "",
            has_password=bool(conn and conn.password),
            verify_tls=conn.verify_tls if conn else True,
            save_path=self.ledger.save_path(),
            remove_completed=self.ledger.remove_completed(),
        )

    def save_settings(self, settings: QbtSettings, password: Optional[str]) -> None:
        conn = self._connection(settings, password)
        self.ledger.save_connection(conn)
        self.ledger.set_save_path(settings.save_path)
        self.ledger.set_remove_completed(settings.remove_completed)

    def test_connection(self, settings: QbtSettings, password: Optional[str]) -> str:
        try:
            return self._client_factory(self._connection(settings, password)).version()
        except QbtError as exc:
            raise BackendError(str(exc)) from None

    def _connection(self, settings: QbtSettings, password: Optional[str]) -> QbtConnection:
        try:
            base_url = normalize_base_url(settings.base_url)
        except ValueError as exc:
            raise BackendError(str(exc)) from None
        if not password:
            stored = self.ledger.connection()
            password = stored.password if stored is not None else ""
        return QbtConnection(base_url=base_url, username=settings.username.strip(), password=password,
                             verify_tls=settings.verify_tls)


def create_backend(db) -> Backend:
    return Backend(db)

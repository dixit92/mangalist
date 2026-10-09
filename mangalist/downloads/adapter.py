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

import json
import logging
import threading
from dataclasses import replace
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from .. import paths
from ..gui.downloads_backend import BackendError, QbtSettings
from ..services.nyaa import NyaaError, NyaaSearch
from ..services.nyaa.client import NyaaClient
from ..services.nyaa.ranking import order
from ..services.qbittorrent import QbtError, client_from_connection, normalize_base_url
from ..store.downloads import DownloadLedger
from .contracts import DownloadRecord, NyaaCandidate, Placement, QbtConnection, TorrentClient
from .options import NyaaOptions, load_nyaa_options, save_nyaa_options
from .placement import placement_for
from .service import SendRefused, send_pick

_log = logging.getLogger(__name__)


class Backend:
    def __init__(self, db, *, search: Optional[NyaaSearch] = None,
                 client_factory: Callable[[QbtConnection], TorrentClient] = client_from_connection):
        self.db = db
        self.ledger = DownloadLedger(db)
        self._search = search                   # given: used as it is (tests); else one NyaaSearch per category
        self._searches: Dict[tuple, NyaaSearch] = {}
        self._nyaa_client: Optional[NyaaClient] = None
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
        options = load_nyaa_options(self.db)
        if not options.enabled:
            raise BackendError("nyaa is switched off (Settings > Download sources)")
        with self._search_lock:
            found: Dict[str, NyaaCandidate] = {}
            searches = self._searches_for(options)
            try:
                for search in searches:
                    for candidate in search.search(titles, missing, held):
                        found.setdefault(candidate.info_hash, candidate)
            except NyaaError as exc:
                raise BackendError(f"nyaa: {exc}") from None
        results = [c for c in found.values() if c.trusted or not options.trusted_only]
        return order(results) if len(searches) > 1 else results      # one search is ranked already

    def _searches_for(self, options: NyaaOptions) -> List[NyaaSearch]:
        """The searches to run: the injected one, else one per category the options ask for, sharing one client (so
        nyaa's politeness delay holds across them)."""
        if self._search is not None:
            return [self._search]
        if self._nyaa_client is None:
            self._nyaa_client = NyaaClient()
        out = []
        for category in options.categories():
            key = (category, not options.hide_light_novels)
            if key not in self._searches:
                self._searches[key] = NyaaSearch(self._nyaa_client, category=category,
                                                 include_not_comic=not options.hide_light_novels)
            out.append(self._searches[key])
        return out

    def nyaa_options(self) -> NyaaOptions:
        return load_nyaa_options(self.db)

    def set_nyaa_options(self, options: NyaaOptions) -> None:
        save_nyaa_options(self.db, options)

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

    def set_remove_completed(self, on: bool) -> None:
        self.ledger.set_remove_completed(on)

    def series_titles(self, series_ids: Sequence[int]) -> Dict[int, str]:
        """The folder name of each library series row (what the Downloads list calls the series)."""
        wanted = sorted({int(i) for i in series_ids})
        if not wanted:
            return {}
        marks = ",".join("?" * len(wanted))
        with self.db.connect() as con:
            rows = con.execute(f"SELECT id, rel_path FROM series WHERE id IN ({marks})", wanted).fetchall()
        return {int(r["id"]): Path(r["rel_path"]).name or r["rel_path"] for r in rows}

    def next_check(self) -> Optional[str]:
        """When the container's scheduler next runs the downloads job (ISO 8601, UTC), or None when not known - the
        scheduler's state file is only there where the headless runner runs."""
        from ..headless.downloads_job import JOB_NAME
        from ..headless.state import STATE_NAME

        try:
            data = json.loads((paths.data_dir() / STATE_NAME).read_text(encoding="utf-8"))
            value = data["jobs"][JOB_NAME]["next_run"]
        except (OSError, ValueError, KeyError, TypeError):
            return None
        return value if isinstance(value, str) and value else None

    def check_now(self) -> str:
        """The scheduled downloads job, once, now (the hourly schedule is unchanged). A pass the scheduler runs at the
        same moment is harmless: record changes are conditional and filing holds the root lock."""
        from ..headless.downloads_job import make_downloads_job
        from ..headless.jobs import JobContext

        result = make_downloads_job(open_ledger=lambda: self.ledger, client_factory=self._client_factory)(JobContext())
        if result.status == "error":
            raise BackendError(result.message)
        return result.message

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

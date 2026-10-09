"""The volumes GUI's backend over the real pieces (integrator wiring, volumes MVP). No Qt here.

:mod:`mangalist.gui.downloads_backend` imports this module when downloads are switched on and calls
:func:`create_backend` with the main window's store. Each :class:`~mangalist.gui.downloads_backend.DownloadsBackend`
method maps to one piece: the library store (series lookup), :func:`~mangalist.downloads.placement.placement_for`,
:class:`~mangalist.services.nyaa.NyaaSearch`, :func:`~mangalist.downloads.service.send_pick`, the
:class:`~mangalist.store.downloads.DownloadLedger` (records, connection, settings) and the qBittorrent client.
Failures the owner should read become :class:`~mangalist.gui.downloads_backend.BackendError` with the service's own
message (the clients never put a password in one).

**Partial downloads.** :meth:`Backend.inspect_pack` reads a release's ``.torrent`` through the nyaa client (its
politeness rules apply: one client, one lock) and returns the :class:`~mangalist.downloads.partial.PackSelection` the
release panel shows before sending; it never raises for a file list that cannot be read - the selection says so and the
whole pack is what a send then downloads. :meth:`Backend.send` takes ``only_missing`` (False unless the caller says so)
and keeps what the send did with the pack for :meth:`Backend.take_pack_outcome`.
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
from ..torrent_files import TorrentError, read_torrent
from .contracts import DownloadRecord, NyaaCandidate, Placement, QbtConnection, TorrentClient
from .options import KEY_PARTIAL_DOWNLOADS, NyaaOptions, get_flag, load_nyaa_options, save_nyaa_options, set_flag
from .partial import PackOutcome, PackSelection, choose_files, hint_for, log_selection
from .placement import placement_for
from .service import PackSetupError, SendRefused, send_pick

_log = logging.getLogger(__name__)


class Backend:
    def __init__(self, db, *, search: Optional[NyaaSearch] = None, nyaa_client: Optional[NyaaClient] = None,
                 client_factory: Callable[[QbtConnection], TorrentClient] = client_from_connection):
        self.db = db
        self.ledger = DownloadLedger(db)
        self._search = search                   # given: used as it is (tests); else one NyaaSearch per category
        self._searches: Dict[tuple, NyaaSearch] = {}
        self._nyaa_client = nyaa_client            # one client for every category: nyaa's politeness delay is per client
        self._search_lock = threading.Lock()    # one search at a time: nyaa's politeness delay is per client
        self._client_factory = client_factory
        self._packs: Dict[tuple, PackSelection] = {}      # readable file lists already looked at, newest last
        self._outcomes: Dict[str, PackOutcome] = {}       # what the last send of a torrent did with its pack

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
        out = []
        for category in options.categories():
            key = (category, not options.hide_light_novels)
            if key not in self._searches:
                self._searches[key] = NyaaSearch(self._nyaa(), category=category,
                                                 include_not_comic=not options.hide_light_novels)
            out.append(self._searches[key])
        return out

    def _nyaa(self) -> NyaaClient:
        if self._nyaa_client is None:
            self._nyaa_client = NyaaClient()
        return self._nyaa_client

    def nyaa_options(self) -> NyaaOptions:
        return load_nyaa_options(self.db)

    def set_nyaa_options(self, options: NyaaOptions) -> None:
        save_nyaa_options(self.db, options)

    # --- partial downloads -----------------------------------------------------------------------------

    def partial_default(self) -> bool:
        """Settings > Download sources: does a pack's release panel start with "only the missing volumes" ticked?"""
        return get_flag(self.db, KEY_PARTIAL_DOWNLOADS)

    def set_partial_default(self, on: bool) -> None:
        set_flag(self.db, KEY_PARTIAL_DOWNLOADS, on)

    def inspect_pack(self, candidate: NyaaCandidate, wanted_volumes: Sequence[str]) -> PackSelection:
        """Which files of the release hold *wanted_volumes*, from its ``.torrent`` on nyaa. A file list that cannot be
        read is a selection with ``problem`` set, not an error: the whole pack is then what a send downloads."""
        wanted = tuple(str(v) for v in wanted_volumes)
        key = (candidate.info_hash.lower(), wanted, hint_for(candidate))
        if key in self._packs:
            return self._packs[key]
        problem = self._pack_problem(candidate)
        listing = None
        if problem is None:
            try:
                with self._search_lock:         # nyaa's politeness delay is per client
                    data = self._nyaa().torrent(candidate.torrent_url)
                listing = read_torrent(data)
            except NyaaError as exc:
                problem = f"nyaa did not give the torrent file ({exc})"
            except TorrentError as exc:
                problem = f"the torrent file could not be read ({exc})"
            if listing is not None and not listing.has_hash(candidate.info_hash):
                problem, listing = "the torrent file on nyaa is not the release that was listed", None
        if listing is None:
            _log.info("Partial: %s: file list not read: %s", candidate.title, problem)
            return PackSelection(wanted=wanted, problem=problem or "the file list is not available")
        selection = choose_files([(f.name, f.size) for f in listing.files], wanted, hint_for(candidate))
        log_selection(candidate.title, selection, "the .torrent on nyaa")
        self._packs[key] = selection
        while len(self._packs) > 32:
            del self._packs[next(iter(self._packs))]
        return selection

    @staticmethod
    def _pack_problem(candidate: NyaaCandidate) -> Optional[str]:
        if not (candidate.torrent_url or "").lower().startswith(("http://", "https://")):
            return "the release has only a magnet link, so its files cannot be listed before it is sent"
        return None

    def take_pack_outcome(self, info_hash: str) -> Optional[PackOutcome]:
        """What the last send of this torrent did with its pack (once: it is forgotten after)."""
        return self._outcomes.pop(info_hash.lower(), None)

    # --- qBittorrent -----------------------------------------------------------------------------------

    def send(self, series_id: int, candidate: NyaaCandidate, wanted_volumes: Sequence[str], target_dir: str,
             only_missing: bool = False) -> DownloadRecord:
        conn = self.ledger.connection()
        if conn is None or not conn.base_url:
            raise BackendError("qBittorrent is not set up yet (toolbar: qBittorrent...)")
        placement = replace(self.placement(series_id), target_dir=target_dir, options=())
        key = candidate.info_hash.lower()
        self._outcomes.pop(key, None)
        try:
            return send_pick(self._client_factory(conn), self.ledger, series_id, candidate, wanted_volumes,
                             placement, self.ledger.save_path(), only_missing=only_missing,
                             on_pack=lambda outcome: self._outcomes.__setitem__(key, outcome))
        except (SendRefused, QbtError, PackSetupError) as exc:
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

    def remove_now(self, record_id: int) -> DownloadRecord:
        """Remove one filed download's torrent and its downloaded copy now (the owner's choice; library files stay)."""
        from .arrivals import RemoveRefused, remove_now

        conn = self.ledger.connection()
        if conn is None or not conn.base_url:
            raise BackendError("qBittorrent is not set up yet")
        try:
            return remove_now(self._client_factory(conn), self.ledger, record_id)
        except (RemoveRefused, QbtError) as exc:
            raise BackendError(f"not removed: {exc}") from None

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

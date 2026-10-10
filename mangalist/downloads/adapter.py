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

**The download budget.** :meth:`Backend.send` goes through :func:`~mangalist.downloads.queueing.submit`: it sends when
the release fits under the cap, else queues it - or sends it past the cap when the owner chose so (``over_cap``).
:meth:`Backend.budget_status` is the cap and the usage (the release panel's confirmation, the In progress list, Settings);
:meth:`Backend.send_queued_now`, :meth:`Backend.move_to_front` and :meth:`Backend.remove_from_queue` are the In progress
list's overrides. The hand-over of the queue runs in :meth:`Backend.check_now` (the downloads job).

**Chapters through Suwayomi** (the Suwayomi MVP): :meth:`Backend.chapter_lookup` finds the series in Suwayomi (MangaDex by
the id MangaPixer links, else title candidates the owner confirms - :meth:`Backend.confirm_match`), lists its missing
chapters with their groups and the default group, and :meth:`Backend.send_chapters` records and enqueues the picks
(:mod:`mangalist.downloads.chapters`). Suwayomi's connection, sources and per-series choices live in
:class:`~mangalist.store.downloads.SuwayomiSettings`. :meth:`Backend.records` returns every tool's records.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import replace
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from .. import paths
from ..gui.downloads_backend import BackendError, QbtSettings, SuwayomiCheck, SuwayomiSettingsView
from ..services.suwayomi import SuwayomiError
from ..services.nyaa import NyaaError, NyaaSearch
from ..services.nyaa.client import NyaaClient
from ..services.nyaa.ranking import order
from ..services.qbittorrent import QbtError, client_from_connection, normalize_base_url
from ..store.downloads import DownloadLedger, StatusConflict
from ..torrent_files import TorrentError, read_torrent
from ..store.downloads import SuwayomiSettings
from .contracts import (
    TOOL_SUWAYOMI,
    ChapterClient,
    DownloadRecord,
    NyaaCandidate,
    Placement,
    QbtConnection,
    SuwayomiConnection,
    SuwayomiSource,
    TorrentClient,
)
from .budget import OVER_CAP_QUEUE, SIZE_SELECTED, BudgetState
from .chapters import (
    HOW_CONFIRMED,
    HOW_MANGADEX,
    ChapterLookup,
    ChapterSendOutcome,
    ChapterSendRefused,
    MangaMatch,
    allowed_sources,
    build_lookup,
    chapter_placement_for,
    find_manga,
    held_chapter_groups,
    mangadex_id_for,
    send_chapters,
    title_candidates,
)
from .options import (
    KEY_PARTIAL_DOWNLOADS,
    NyaaOptions,
    get_budget_gb,
    get_flag,
    load_nyaa_options,
    save_nyaa_options,
    set_budget_gb,
    set_flag,
)
from .partial import PackOutcome, PackSelection, choose_files, hint_for, log_selection
from .placement import placement_for
from .queueing import ClientDown, budget_state, move_to_front, refresh, remove_from_queue, send_now, submit
from .service import PackSetupError, SendRefused

_log = logging.getLogger(__name__)


class Backend:
    def __init__(self, db, *, search: Optional[NyaaSearch] = None, nyaa_client: Optional[NyaaClient] = None,
                 client_factory: Callable[[QbtConnection], TorrentClient] = client_from_connection,
                 chapter_client_factory: Optional[Callable[[SuwayomiConnection], ChapterClient]] = None,
                 namer=None):
        self.db = db
        self.ledger = DownloadLedger(db)
        self.chapter_ledger = DownloadLedger(db, tool=TOOL_SUWAYOMI)
        self.suwayomi = SuwayomiSettings(db)
        self._chapter_client_factory = chapter_client_factory
        self._namer = namer
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
             only_missing: bool = False, over_cap: str = OVER_CAP_QUEUE,
             size_bytes: Optional[int] = None) -> DownloadRecord:
        """Send the release - or queue it when it would go over the download budget (``over_cap="queue"``, the
        default), or send it past the cap (``"send"``, the owner's override). *size_bytes*: what it counts (a partial
        send: the selected files' total; default the release's size). Returns the record: SENT or QUEUED."""
        conn = self.ledger.connection()
        if conn is None or not conn.base_url:
            raise BackendError("qBittorrent is not set up yet (toolbar: qBittorrent...)")
        placement = replace(self.placement(series_id), target_dir=target_dir, options=())
        key = candidate.info_hash.lower()
        self._outcomes.pop(key, None)
        client = self._client_factory(conn)
        try:
            refresh(client, self.ledger)            # the budget with qBittorrent's own sizes, when it answers
        except Exception as exc:  # noqa: BLE001 - the send itself reports an unreachable qBittorrent
            _log.info("Budget: sizes not refreshed before the send (%s)", type(exc).__name__)
        try:
            return submit(client, self.ledger, series_id, candidate, wanted_volumes, placement,
                          self.ledger.save_path(), only_missing=only_missing, size_bytes=size_bytes,
                          size_source=SIZE_SELECTED if only_missing and size_bytes else None, over_cap=over_cap,
                          on_pack=lambda outcome: self._outcomes.__setitem__(key, outcome))
        except (SendRefused, QbtError, PackSetupError) as exc:
            raise BackendError(str(exc)) from None

    # --- the download budget ---------------------------------------------------------------------------------

    def budget_status(self) -> BudgetState:
        """The cap and what counts against it now (one database read: quick)."""
        return budget_state(self.ledger)

    def budget_gb(self) -> float:
        return get_budget_gb(self.db)

    def set_budget_gb(self, gb: float) -> None:
        set_budget_gb(self.db, gb)

    def send_queued_now(self, record_id: int) -> DownloadRecord:
        """A queued download, to qBittorrent now, past the cap (the owner's override)."""
        conn = self.ledger.connection()
        if conn is None or not conn.base_url:
            raise BackendError("qBittorrent is not set up yet")
        try:
            return send_now(self._client_factory(conn), self.ledger, record_id)
        except (SendRefused, ClientDown, QbtError) as exc:
            raise BackendError(str(exc)) from None

    def move_to_front(self, record_id: int) -> DownloadRecord:
        try:
            return move_to_front(self.ledger, record_id)
        except StatusConflict as exc:
            raise BackendError(f"not moved: {exc}") from None

    def remove_from_queue(self, record_id: int) -> DownloadRecord:
        try:
            return remove_from_queue(self.ledger, record_id)
        except StatusConflict as exc:
            raise BackendError(f"not removed: {exc}") from None

    def records(self, series_id: Optional[int] = None) -> Sequence[DownloadRecord]:
        """Every tool's records (torrents and chapter downloads)."""
        every = self.ledger.for_tool(None)
        return every.for_series(series_id) if series_id is not None else every.all_records()

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

        result = make_downloads_job(open_ledger=lambda: self.ledger, client_factory=self._client_factory,
                                    chapter_client_factory=self._chapter_client_factory, namer=self._namer)(JobContext())
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


    # --- Suwayomi (chapters) -----------------------------------------------------------------------------------

    def suwayomi_ready(self) -> bool:
        conn = self.suwayomi.connection()
        return conn is not None and bool(conn.base_url)

    def load_suwayomi(self) -> SuwayomiSettingsView:
        conn = self.suwayomi.connection()
        if conn is None:
            return SuwayomiSettingsView()
        return SuwayomiSettingsView(base_url=conn.base_url, username=conn.username, has_password=bool(conn.password),
                                    download_dir=conn.download_dir)

    def save_suwayomi(self, view: SuwayomiSettingsView, password: Optional[str]) -> None:
        self.suwayomi.save_connection(self._suwayomi_connection(view, password))

    def forget_suwayomi(self) -> None:
        self.suwayomi.forget_connection()

    def test_suwayomi(self, view: SuwayomiSettingsView, password: Optional[str]) -> SuwayomiCheck:
        conn = self._suwayomi_connection(view, password)
        client = self._chapter_client(conn)
        try:
            info = client.server_settings() if hasattr(client, "server_settings") else {"version": client.version()}
        except SuwayomiError as exc:
            raise BackendError(str(exc)) from None
        finally:
            _close(client)
        folder = conn.download_dir
        found = os.path.isdir(os.path.join(folder, "mangas")) if folder else None
        return SuwayomiCheck(version=str(info.get("version") or "?"),
                             download_as_cbz=info.get("download_as_cbz", True) is not False,
                             flaresolverr=bool(info.get("flaresolverr")),
                             flaresolverr_url=str(info.get("flaresolverr_url") or ""),
                             downloads_path=str(info.get("downloads_path") or ""), folder_found=found)

    def _suwayomi_connection(self, view: SuwayomiSettingsView, password: Optional[str]) -> SuwayomiConnection:
        from ..services.suwayomi import normalize_base_url as suwayomi_url

        try:
            base_url = suwayomi_url(view.base_url)
        except ValueError as exc:
            raise BackendError(str(exc)) from None
        if not password:
            stored = self.suwayomi.connection()
            password = stored.password if stored is not None and view.username.strip() else ""
        return SuwayomiConnection(base_url=base_url, username=view.username.strip(), password=password or "",
                                  download_dir=(view.download_dir or "").strip())

    def _chapter_client(self, conn: Optional[SuwayomiConnection] = None) -> ChapterClient:
        conn = conn if conn is not None else self.suwayomi.connection()
        if conn is None or not conn.base_url:
            raise BackendError("Suwayomi is not set up yet (Settings > Connected services > Suwayomi)")
        if self._chapter_client_factory is not None:
            return self._chapter_client_factory(conn)
        from ..services.suwayomi import client_from_connection as suwayomi_client

        return suwayomi_client(conn)

    def suwayomi_sources(self) -> List[tuple]:
        """``[(SuwayomiSource, allowed)]``: the allowed sources first, in the owner's order, then the other installed
        ones (English first). Asks Suwayomi."""
        client = self._chapter_client()
        try:
            installed = list(client.sources())
        except SuwayomiError as exc:
            raise BackendError(str(exc)) from None
        finally:
            _close(client)
        allowed = allowed_sources(installed, self.suwayomi.sources(), self._source_languages())
        ids = {s.id for s in allowed}
        rest = sorted((s for s in installed if s.id not in ids),
                      key=lambda s: (s.lang not in ("en", "all"), not s.is_mangadex, s.display_name.casefold()))
        return [(s, True) for s in allowed] + [(s, False) for s in rest]

    def set_suwayomi_sources(self, source_ids: Sequence[str]) -> None:
        self.suwayomi.set_sources(source_ids)

    def reset_suwayomi_sources(self) -> None:
        """Forget the owner's choice: MangaDex in the owner's languages again (Settings > "Reset to default")."""
        self.suwayomi.reset_sources()

    def _source_languages(self) -> tuple:
        from .options import load_nyaa_options

        try:
            return load_nyaa_options(self.db).source_languages()
        except Exception:  # noqa: BLE001 - English, the default, when the options cannot be read
            return ("all", "en")

    def chapter_lookup(self, series_id: int, missing: Sequence[str], titles: Sequence[str]) -> ChapterLookup:
        """Find the series in Suwayomi and list its missing chapters (see :mod:`mangalist.downloads.chapters`)."""
        lookup = ChapterLookup(series_id=series_id, missing=tuple(missing))
        try:
            lookup.placement = chapter_placement_for(self.db, series_id)
        except LookupError as exc:
            lookup.placement_error = str(exc)
        client = self._chapter_client()
        try:
            sources = allowed_sources(list(client.sources()), self.suwayomi.sources(), self._source_languages())
            if not sources:
                lookup.error = ("no Suwayomi source is allowed (Settings > Download sources > Suwayomi sources), or "
                                "the MangaDex extension is not installed in Suwayomi")
                return lookup
            stored = self.suwayomi.series_choice(series_id)
            found = find_manga(client, sources, mangadex_id=mangadex_id_for(self.db, series_id), titles=titles,
                               stored=stored)
            lookup.notes = list(found.notes)
            lookup.candidates = list(found.candidates)
            if found.match is None:
                return lookup
            in_hand = self._chapters_in_hand(series_id)
            built = build_lookup(client, series_id=series_id, missing=missing, match=found.match,
                                 held=self._held_groups(series_id), stored_group=stored.get("group"), in_hand=in_hand)
            built.placement, built.placement_error = lookup.placement, lookup.placement_error
            built.notes = lookup.notes
            if found.match.how == HOW_MANGADEX and stored.get("manga_id") != found.match.manga.id:
                self.suwayomi.set_series_choice(series_id, source_id=found.match.source.id,
                                                manga_id=found.match.manga.id, manga_title=built.manga_title,
                                                how=HOW_MANGADEX)
            if not built.available and not built.queued_in_suwayomi and found.match.how == HOW_MANGADEX:
                # MangaDex has none of them: the owner's other sources, by title (owner: "when MangaDex has nothing")
                others = [s for s in sources if not s.is_mangadex]
                if others:
                    built.candidates = title_candidates(client, others, titles)
                    built.notes.append("MangaDex has none of the missing chapters")
            return built
        except SuwayomiError as exc:
            raise BackendError(str(exc)) from None
        finally:
            _close(client)

    def _held_groups(self, series_id: int):
        try:
            return held_chapter_groups(self.db, series_id)
        except LookupError:
            return {}

    def _chapters_in_hand(self, series_id: int) -> Dict[str, DownloadRecord]:
        from .contracts import DownloadStatus

        keep = (DownloadStatus.SENT, DownloadStatus.DOWNLOADED, DownloadStatus.FILED, DownloadStatus.REMOVED)
        out: Dict[str, DownloadRecord] = {}
        for rec in self.chapter_ledger.for_series(series_id):
            if rec.status in keep:
                for number in rec.wanted_chapters:
                    out[number] = rec
        return out

    def confirm_match(self, series_id: int, match: MangaMatch) -> None:
        """The owner confirmed a title match: it is the series' Suwayomi manga from now on."""
        self.suwayomi.set_series_choice(series_id, source_id=match.source.id, manga_id=int(match.manga.id),
                                        manga_title=match.manga.title, how=HOW_CONFIRMED)
        _log.info("Chapters: series %s matched to %s manga %s by the owner", series_id, match.source.display_name,
                  match.manga.id)

    def forget_match(self, series_id: int) -> None:
        self.suwayomi.set_series_choice(series_id, source_id=None, manga_id=None, manga_title=None, how=None)

    def set_series_group(self, series_id: int, group: Optional[str]) -> None:
        self.suwayomi.set_series_choice(series_id, group=group or None)

    def send_chapters(self, series_id: int, match: MangaMatch, picks: Sequence, target_dir: str,
                      manga_title: str = "", source_name: str = "") -> ChapterSendOutcome:
        client = self._chapter_client()
        try:
            return send_chapters(client, self.chapter_ledger, series_id, match, picks, target_dir,
                                 manga_title=manga_title, source_name=source_name)
        except ChapterSendRefused as exc:
            raise BackendError(str(exc)) from None
        except SuwayomiError as exc:
            raise BackendError(str(exc)) from None
        finally:
            _close(client)


def _close(client) -> None:
    close = getattr(client, "close", None)
    if callable(close):
        close()


def create_backend(db) -> Backend:
    return Backend(db)

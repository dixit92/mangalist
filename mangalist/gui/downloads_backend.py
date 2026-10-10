"""What the volumes GUI needs from the rest of MangaList, as one small interface.

The Download tab, the Settings dialog, the dialogs that still wrap their panels (:mod:`.nyaa_dialog`,
:mod:`.qbittorrent_dialog`, :mod:`.downloads_dialog`) and the main window talk only to a :class:`DownloadsBackend`;
they never import the nyaa / qBittorrent services or the store. The shapes that cross it are
:mod:`mangalist.downloads.contracts` (``NyaaCandidate``, ``Placement``, ``DownloadRecord``) plus :class:`QbtSettings`
below.

**For the integrator.** Write the real adapter at merge and expose it as
``mangalist.downloads.adapter.create_backend(db)`` (``db`` is the main window's store); :func:`create_backend`
imports that and returns None when it does not exist (the volumes GUI then stays hidden). Each method maps to
one existing piece:

========================================  =======================================================================
``series_id_for(folder)``                 the store's series row of an absolute series folder (``store.get_series``
                                          through the root); None when the folder is not scanned. Cheap: it runs on
                                          the UI thread, for every row of the Wanted panel.
``placement(series_id)``                  the arrivals lane's layout inference -> ``Placement``.
``search(titles, missing, held)``         ``VolumeSearch.search`` of the nyaa service (the same signature).
``send(series_id, candidate, wanted,      add the torrent to qBittorrent in the ``mangalist`` category and create
 target_dir)``                            the ``DownloadRecord`` (``DownloadStore.create``); returns the record.
``records(series_id=None)``               ``DownloadStore.for_series`` (or every record, newest last).
``load_settings()`` / ``save_settings``   the qBittorrent connection (address, user, TLS) and its two settings
                                          (save path, Remove Completed); the password goes to the secrets table.
``test_connection(settings, password)``   ``TorrentClient.version()`` against the typed values (a password of None
                                          means "the stored one").
``check_now()``                           the scheduled downloads job, run once now (file finished downloads, Remove
                                          Completed); returns its one-line summary.
========================================  =======================================================================

Optional extras (the Download tab and Settings use them when the backend has them, via ``getattr``):
``nyaa_options()`` / ``set_nyaa_options(options)`` (the nyaa source's switches), ``set_remove_completed(on)``,
``series_titles(series_ids)`` (names for the downloads list) and ``next_check()`` (the next scheduled downloads
check, ISO 8601 UTC, or None).

Partial downloads (only a pack's missing volumes) add four more, all optional - a backend without ``inspect_pack`` simply
never shows the "Only the missing volumes" box: ``inspect_pack(candidate, wanted_volumes)`` (blocking; reads the release's
``.torrent`` and returns a :class:`~mangalist.downloads.partial.PackSelection`, with ``problem`` set - not raised - when
the file list cannot be read), ``partial_default()`` / ``set_partial_default(on)`` (the Settings default; quick) and
``take_pack_outcome(info_hash)`` (what the last send did with the pack, once). ``send`` then takes ``only_missing=True``
(the panel passes it only when the box is ticked and the pack can be narrowed; otherwise it is called with the four
arguments above, so older backends keep working).

The download budget (owner, 2026-10-09; :mod:`mangalist.downloads.budget`) adds these, all optional - without
``budget_status`` the release panel asks nothing about a cap and calls ``send`` as above: ``budget_status()`` (quick: one
database read; a :class:`~mangalist.downloads.budget.BudgetState` - the cap, the usage, the queue), ``send`` then takes
``over_cap`` (``"queue"``: queue it when it would go over the cap - the default; ``"send"``: send it now, past the cap)
and ``size_bytes`` (what it counts: a partial send's selected files) and returns a SENT or a QUEUED record;
``send_queued_now(record_id)`` / ``move_to_front(record_id)`` / ``remove_from_queue(record_id)`` (the In progress list's
row menu on a queued download; each returns the record). Settings reads and writes the cap itself
(:func:`mangalist.downloads.options.get_budget_gb`).

**Chapters through Suwayomi** (the Suwayomi MVP, 2026-10-10; :mod:`mangalist.downloads.chapters`) adds these, all
optional - without ``suwayomi_ready`` the Missing chapters group stays "needs Suwayomi" and Settings shows Suwayomi as not
available:

========================================  =======================================================================
``suwayomi_ready()``                      quick: a Suwayomi connection is stored (the Missing chapters group can be
                                          looked up).
``load_suwayomi()`` / ``save_suwayomi``   quick: :class:`SuwayomiSettingsView` (address, user, download folder; the
 ``(view, password)``                     password write-only: None or '' keeps the stored one).
``test_suwayomi(view, password)``         Suwayomi's version and the settings MangaList cares about
                                          (:class:`SuwayomiCheck`); :class:`BackendError` with a readable reason.
``suwayomi_sources()``                    the installed sources with the owner's choice: ``[(SuwayomiSource, allowed)]``
                                          in the order MangaList tries them (asks Suwayomi).
``set_suwayomi_sources(ids)``             quick: the allowed source ids, in order.
``chapter_lookup(series_id, missing,      find the series in Suwayomi and list its missing chapters with the groups
 titles)``                                that have them -> :class:`~mangalist.downloads.chapters.ChapterLookup`.
``confirm_match(series_id, match)``       the owner confirmed a title match (stored for the series).
``forget_match(series_id)``               look the series up again from scratch.
``set_series_group(series_id, group)``    the series' scanlation group (None: back to the default rule).
``send_chapters(series_id, match, picks,  record and enqueue the picked chapters ->
 target_dir, manga_title, source_name)``  :class:`~mangalist.downloads.chapters.ChapterSendOutcome`.
========================================  =======================================================================

``records()`` then returns every tool's records (chapter downloads have ``tool == 'suwayomi'``), and ``check_now()``
files finished chapters too.

Every method may block (network, database): the GUI calls them off the UI thread, except ``series_id_for`` and
the settings getter / setter, which must be quick. A failure the owner should read raises :class:`BackendError`
with a message that is safe to show (never a password, token or URL with credentials); anything else is shown
as "unexpected error (TypeName)".

Qt-free on purpose: the GUI test fakes and the adapter need not import PySide6.
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass
from typing import Optional, Protocol, Sequence

from ..downloads.contracts import DownloadRecord, NyaaCandidate, Placement, downloads_enabled

_log = logging.getLogger(__name__)

DEFAULT_SAVE_PATH = "/data/appdata/torrents/mangalist"
ADAPTER_MODULE = "mangalist.downloads.adapter"


class BackendError(Exception):
    """A failure with a message fit to show the owner (no secrets in it)."""


@dataclass(frozen=True)
class QbtSettings:
    """The qBittorrent settings the dialog edits. The password is not part of it: it is write-only
    (``has_password`` says one is stored) and travels separately."""

    base_url: str = ""
    username: str = ""
    has_password: bool = False
    verify_tls: bool = True
    save_path: str = DEFAULT_SAVE_PATH
    remove_completed: bool = True


@dataclass(frozen=True)
class SuwayomiSettingsView:
    """Suwayomi's connection as Settings edits it. The password is write-only (``has_password``), like qBittorrent's."""

    base_url: str = ""
    username: str = ""
    has_password: bool = False
    download_dir: str = ""              # Suwayomi's downloads folder (the one holding mangas/), as MangaList sees it


@dataclass(frozen=True)
class SuwayomiCheck:
    """What Test connection learned from Suwayomi."""

    version: str
    download_as_cbz: bool = True
    flaresolverr: bool = False
    flaresolverr_url: str = ""
    downloads_path: str = ""            # as Suwayomi sees it ('' = its default, <data>/downloads)
    folder_found: Optional[bool] = None     # the download folder MangaList reads holds a "mangas" folder (None: not set)

    def notes(self) -> list:
        """Warnings for the owner (empty when all is well)."""
        out = []
        if not self.download_as_cbz:
            out.append("Suwayomi saves chapters as folders of images: turn on \"Download as CBZ\" in Suwayomi's "
                       "settings (MangaList files CBZ archives only).")
        if self.folder_found is False:
            out.append("The download folder has no \"mangas\" folder yet: check it is Suwayomi's downloads folder as "
                       "MangaList sees it (it appears with the first download).")
        if not self.flaresolverr:
            out.append("FlareSolverr is off in Suwayomi: sources behind Cloudflare may fail (set it in Suwayomi's "
                       "settings, e.g. http://<unraid-ip>:8191).")
        return out


class DownloadsBackend(Protocol):
    def series_id_for(self, folder: str) -> Optional[int]: ...

    def placement(self, series_id: int) -> Placement: ...

    def search(self, titles: Sequence[str], missing: Sequence[str], held: Sequence[str]) -> Sequence[NyaaCandidate]: ...

    def send(self, series_id: int, candidate: NyaaCandidate, wanted_volumes: Sequence[str],
             target_dir: str) -> DownloadRecord:
        """Add the release to qBittorrent and record it. Backends that read file lists also take ``only_missing``."""
        ...

    def records(self, series_id: Optional[int] = None) -> Sequence[DownloadRecord]: ...

    def load_settings(self) -> QbtSettings: ...

    def save_settings(self, settings: QbtSettings, password: Optional[str]) -> None:
        """Store the settings. ``password`` None or '' keeps the stored one."""
        ...

    def test_connection(self, settings: QbtSettings, password: Optional[str]) -> str:
        """Log in with the given values (password None or '' = the stored one) and return the client's
        version text. Raises :class:`BackendError` with a readable reason."""
        ...

    def check_now(self) -> str:
        """Run the downloads job once now - the same check the container runs every hour - and return its summary
        ("1 checked: 1 filed, 0 removed, ..."). Raises :class:`BackendError` with a readable reason."""
        ...


def create_backend(db) -> Optional[DownloadsBackend]:
    """The real backend when downloads are switched on and the adapter exists, else None."""
    if not downloads_enabled():
        return None
    try:
        module = importlib.import_module(ADAPTER_MODULE)
    except ModuleNotFoundError as exc:
        if exc.name != ADAPTER_MODULE:
            raise                       # the adapter exists but one of its own imports is broken
        _log.warning("Downloads are switched on but %s is not available; the volumes GUI stays hidden", ADAPTER_MODULE)
        return None
    return module.create_backend(db)

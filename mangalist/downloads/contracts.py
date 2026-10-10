"""The volumes MVP's shared shapes: what nyaa search, the qBittorrent client, the arrivals and the GUI pass
between each other. Qt-free, no I/O beyond reading one environment variable.

Flow (D4-D6, 2026-10-06): for a series **matched in MangaPixer** with missing English volumes, the owner
searches nyaa (:class:`VolumeSearch` -> :class:`NyaaCandidate`), picks one, MangaList adds it to qBittorrent
in the ``mangalist`` category (:class:`TorrentClient`) and records a :class:`DownloadRecord`. When the
torrent has finished, the arrivals job hard-links the missing volumes' files into the folder
:class:`Placement` chose (the series' existing layout), through the journal. When qBittorrent has stopped
the torrent at its seed goal and the library files are verified, "Remove Completed" asks qBittorrent to
delete the torrent and its data - only ever in the ``mangalist`` category (the Sonarr / Radarr pattern).

Volume numbers are exact decimal strings (``'1'``, ``'12.5'``), as everywhere in MangaList: never floats.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping, Optional, Protocol, Sequence, Tuple

# --- switches and constants --------------------------------------------------------------------------

ENV_DOWNLOADS = "MANGALIST_DOWNLOADS"  # the same switch as mangalist.headless.settings
_TRUE = {"1", "true", "yes", "y", "on", "enable", "enabled"}

#: The download clients a ledger record can belong to (the ledger's ``tool`` column). The volumes MVP's records are all
#: qBittorrent's; the Suwayomi MVP (chapters, 2026-10-10) adds Suwayomi's.
TOOL_QBITTORRENT = "qbittorrent"
TOOL_SUWAYOMI = "suwayomi"
TOOLS = (TOOL_QBITTORRENT, TOOL_SUWAYOMI)

#: The only qBittorrent category MangaList adds to, watches, or deletes from.
QBITTORRENT_CATEGORY = "mangalist"

#: qBittorrent states that mean "finished downloading and stopped" - v5 names, plus v4's for older clients.
STOPPED_COMPLETE_STATES = frozenset({"stoppedUP", "pausedUP"})

#: ... and "stopped, not finished": what a torrent added stopped (to be configured) shows until it is started.
STOPPED_DOWNLOADING_STATES = frozenset({"stoppedDL", "pausedDL"})


def downloads_enabled(env: Optional[Mapping[str, str]] = None) -> bool:
    """True when downloads are switched on (``MANGALIST_DOWNLOADS``; the Unraid container only, for the MVP)."""
    value = (os.environ if env is None else env).get(ENV_DOWNLOADS)
    return value is not None and value.strip().lower() in _TRUE


# --- nyaa ------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class NyaaCandidate:
    """One nyaa result, parsed and ranked for one series."""

    title: str
    view_url: str                       # the nyaa page
    torrent_url: str                    # the .torrent download link
    info_hash: str                      # lowercase hex; the key MangaList tracks the torrent by
    size_bytes: int
    seeders: int
    leechers: int
    downloads: int
    trusted: bool
    remake: bool
    published: str                      # ISO 8601, UTC
    category: str                       # nyaa category id, e.g. '3_1'
    # Parsed from the title (None when the title does not say):
    vol_from: Optional[str] = None
    vol_to: Optional[str] = None
    digital: bool = False
    group: Optional[str] = None
    is_pack: bool = False               # more than one volume
    not_comic: bool = False             # e.g. a light novel / EPUB release; hidden by default
    # Ranking against the series (D4):
    covers_missing: Tuple[str, ...] = ()  # the series' missing volumes this release holds
    covers_held: Tuple[str, ...] = ()     # volumes the owner already holds (labelled, still allowed)
    rank: float = 0.0                     # higher is better
    reasons: Tuple[str, ...] = ()         # short human-readable ranking reasons

    @property
    def magnet(self) -> str:
        return f"magnet:?xt=urn:btih:{self.info_hash}"


class VolumeSearch(Protocol):
    def search(self, titles: Sequence[str], missing: Sequence[str], held: Sequence[str]) -> Sequence[NyaaCandidate]:
        """Search nyaa (English-translated literature only) for a series known by ``titles`` (main title
        first, then alternatives); drop 0-seeder results; rank best first."""
        ...


# --- qBittorrent -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class TorrentInfo:
    info_hash: str                      # lowercase hex
    name: str
    category: str
    state: str                          # qBittorrent's raw state string
    progress: float                     # 0.0 - 1.0
    save_path: str                      # as qBittorrent sees it (the same path inside MangaList's container)
    content_path: str
    ratio: float
    seeding_time: int                   # seconds
    # The seed goal qBittorrent applies to this torrent (its own limit, else the global one; None or negative: none):
    max_ratio: Optional[float] = None
    max_seeding_time: Optional[int] = None    # minutes
    # The bytes of the files selected for download (qBittorrent's ``size``; a partial download counts only its kept
    # files). 0: not known yet (a magnet before its metadata) - the download budget then keeps the size it had.
    size: int = 0

    @property
    def complete(self) -> bool:
        return self.progress >= 1.0

    @property
    def stopped_complete(self) -> bool:
        return self.state in STOPPED_COMPLETE_STATES

    @property
    def seed_goal_reached(self) -> bool:
        """The ratio or the seeding time has reached qBittorrent's goal for it - so a stop was the seed goal's, not
        the owner's own pause (the Sonarr / Radarr rule). No goal at all: never reached."""
        by_ratio = self.max_ratio is not None and self.max_ratio >= 0 and self.ratio >= self.max_ratio - 0.005
        by_time = (self.max_seeding_time is not None and self.max_seeding_time >= 0
                   and self.seeding_time >= self.max_seeding_time * 60)
        return by_ratio or by_time


@dataclass(frozen=True)
class TorrentFile:
    name: str                           # path inside the torrent, '/'-separated, as qBittorrent reports it
    size: int
    progress: float
    index: int = -1                     # qBittorrent's file id (what ``filePrio`` takes); -1: not reported
    priority: int = 1                   # 0 = do not download; 1 and up = download


class TorrentClient(Protocol):
    def version(self) -> str:
        """Log in if needed and return the client's version (the connection test)."""
        ...

    def ensure_category(self, name: str, save_path: str) -> None: ...

    def add(self, url: str, *, category: str, stopped: bool = False) -> None:
        """Add a torrent by ``.torrent`` URL or magnet link into ``category`` (``stopped``: added stopped, to be
        configured before it starts)."""
        ...

    def torrents(self, category: str) -> Sequence[TorrentInfo]: ...

    def files(self, info_hash: str) -> Sequence[TorrentFile]: ...

    # A partial download (only a pack's missing volumes) is the only user of these three:

    def set_file_priority(self, info_hash: str, file_ids: Sequence[int], priority: int) -> None:
        """Set the priority of the files with these ids (0 = do not download, 1 = normal)."""
        ...

    def start(self, info_hash: str) -> None:
        """Start the torrent (qBittorrent 5's ``start``; 4.x's ``resume``)."""
        ...

    def stop(self, info_hash: str) -> None:
        """Stop the torrent (qBittorrent 5's ``stop``; 4.x's ``pause``)."""
        ...

    def delete(self, info_hash: str, *, delete_files: bool) -> None: ...


@dataclass(frozen=True)
class QbtConnection:
    base_url: str                       # e.g. http://192.168.1.10:8080
    username: str
    password: str = field(repr=False)   # never logged, never repr'd
    verify_tls: bool = True


# --- arrivals --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Placement:
    """Where a series' new volume archives go, inferred from its existing layout."""

    series_dir: str                     # absolute
    target_dir: Optional[str]           # absolute; None when ambiguous
    reason: str                         # e.g. 'volumes live in the series folder', 'volumes live in "Volumes"'
    options: Tuple[str, ...] = ()       # candidate folders (absolute, inside series_dir) when ambiguous

    @property
    def ambiguous(self) -> bool:
        return self.target_dir is None


class DownloadStatus:
    # Owner, 2026-10-09 (the download budget): a send that would take MangaList past its size cap is not handed to the
    # client but waits here, in a queue, until Remove Completed frees room (or the owner sends it past the cap). The
    # ledger's own generic 'queued' value (schema 1) is the same word, so no migration was needed.
    QUEUED = "queued"                   # waiting for room under the download budget; nothing is in qBittorrent yet
    SENT = "sent"                       # added to qBittorrent
    DOWNLOADED = "downloaded"           # torrent complete; filing pending
    FILED = "filed"                     # missing volumes linked (or copied) into the library
    REMOVED = "removed"                 # stopped at its seed goal; torrent + data deleted by qBittorrent
    FAILED = "failed"                   # see ``error``
    CANCELLED = "cancelled"

    ALL = (QUEUED, SENT, DOWNLOADED, FILED, REMOVED, FAILED, CANCELLED)


@dataclass(frozen=True)
class DownloadRecord:
    id: int
    series_id: int
    info_hash: str
    title: str
    wanted_volumes: Tuple[str, ...]     # the missing volumes the owner picked this release for
    target_dir: str                     # absolute, inside the series folder
    status: str                         # DownloadStatus
    created_at: str
    updated_at: str
    filed_files: Tuple[str, ...] = ()   # library paths created, relative to the series folder
    copied: bool = False                # a copy fallback was used (double space; warned)
    error: Optional[str] = None
    # The download budget (mangalist.downloads.budget):
    size_bytes: int = 0                 # what the download counts against the cap (0: not known)
    size_source: str = ""               # where that size comes from: 'release' | 'selected files' | 'qbittorrent'
    in_client: bool = True              # as last seen, the torrent is still in the client (only a FAILED one can say no)
    queue_position: int = 0             # QUEUED: its place in the queue, 1 = handed over next; 0 otherwise
    # The Suwayomi MVP (chapters; :mod:`mangalist.downloads.chapters`). A chapter download is one record per chapter:
    # ``info_hash`` then holds Suwayomi's chapter id (the ledger's ``external_ref``), ``wanted_volumes`` is empty.
    tool: str = TOOL_QBITTORRENT        # TOOL_QBITTORRENT | TOOL_SUWAYOMI
    wanted_chapters: Tuple[str, ...] = ()   # a chapter download: its chapter number (exact strings)
    batch: str = ""                     # a chapter download: the Send it came from (the GUI shows a batch as one row)
    source: str = ""                    # a chapter download: the Suwayomi source's name (e.g. 'MangaDex (EN)')
    group: str = ""                     # a chapter download: the scanlation group ('' when the source names none)

    @property
    def is_chapters(self) -> bool:
        return self.tool == TOOL_SUWAYOMI


class DownloadStore(Protocol):
    def create(self, series_id: int, candidate: NyaaCandidate, wanted_volumes: Sequence[str],
               target_dir: str, *, status: str = DownloadStatus.SENT, only_missing: bool = False,
               size_bytes: Optional[int] = None, size_source: Optional[str] = None) -> DownloadRecord: ...

    def get(self, record_id: int) -> Optional[DownloadRecord]: ...

    def for_series(self, series_id: int) -> Sequence[DownloadRecord]: ...

    def active(self) -> Sequence[DownloadRecord]:
        """Every record in qBittorrent's hands: SENT / DOWNLOADED / FILED (not QUEUED: nothing was added yet)."""
        ...


# --- Suwayomi (the Suwayomi MVP: chapters, 2026-10-10) ---------------------------------------------------------


@dataclass(frozen=True)
class SuwayomiConnection:
    """Suwayomi-Server's address, its optional basic-auth login, and the folder MangaList reads its downloads from
    (as MangaList sees it: Suwayomi's ``downloads`` folder, the one holding ``mangas/``)."""

    base_url: str                       # e.g. http://192.168.1.10:4567
    username: str = ""
    password: str = field(default="", repr=False)   # never logged, never repr'd
    download_dir: str = ""


@dataclass(frozen=True)
class SuwayomiSource:
    """One source Suwayomi has installed (from an extension), stored by its numeric id - stable, unlike its name."""

    id: str                             # Suwayomi's source id: a 64-bit integer as a string
    name: str                           # e.g. 'MangaDex'
    display_name: str                   # e.g. 'MangaDex (EN)' - also the folder Suwayomi downloads into
    lang: str                           # e.g. 'en'; 'localsourcelang' for the local source
    extension: str = ""                 # the extension's package name (e.g. eu.kanade.tachiyomi.extension.all.mangadex)

    @property
    def is_mangadex(self) -> bool:
        return self.extension.endswith(".mangadex") or self.name.casefold() == "mangadex"


@dataclass(frozen=True)
class SuwayomiManga:
    id: int                             # Suwayomi's manga id
    title: str
    url: str                            # the source's own path, e.g. /manga/<uuid> on MangaDex
    source_id: str
    in_library: bool = False


@dataclass(frozen=True)
class SuwayomiChapter:
    id: int                             # Suwayomi's chapter id: what is enqueued, and the ledger's external_ref
    manga_id: int
    name: str                           # as the source names it (MangaDex: 'Vol.1 Ch.2 - <title>')
    number: str                         # exact decimal string ('2', '10.5'); '-1' when the source gives none
    scanlator: Optional[str]            # the scanlation group, None when the source names none
    url: str = ""
    real_url: str = ""
    upload_date: str = ""               # epoch milliseconds, as Suwayomi gives it
    downloaded: bool = False
    source_order: int = 0


@dataclass(frozen=True)
class MangaChapters:
    """A manga's details and chapters as Suwayomi fetched them from the source just now."""

    manga: SuwayomiManga
    source_name: str                    # the source's display name ('MangaDex (EN)'): Suwayomi's download folder
    chapters: Tuple[SuwayomiChapter, ...] = ()


@dataclass(frozen=True)
class QueuedChapter:
    """One entry of Suwayomi's download queue. A finished chapter leaves the queue (and reads ``downloaded``)."""

    chapter_id: int
    state: str                          # QUEUED | DOWNLOADING | FINISHED | ERROR
    progress: float = 0.0
    tries: int = 0


class ChapterClient(Protocol):
    """What MangaList asks Suwayomi (:class:`mangalist.services.suwayomi.SuwayomiClient`)."""

    def version(self) -> str: ...

    def sources(self) -> Sequence[SuwayomiSource]: ...

    def search(self, source_id: str, query: str) -> Sequence[SuwayomiManga]: ...

    def chapters(self, manga_id: int) -> MangaChapters:
        """The manga and its chapters, fetched from the source now (Suwayomi refreshes its own list)."""
        ...

    def add_to_library(self, manga_id: int) -> None: ...

    def enqueue(self, chapter_ids: Sequence[int]) -> Sequence[QueuedChapter]: ...

    def queue(self) -> Sequence[QueuedChapter]: ...

    def chapters_by_id(self, chapter_ids: Sequence[int]) -> Sequence[SuwayomiChapter]: ...

    def delete_downloaded(self, chapter_ids: Sequence[int]) -> None: ...

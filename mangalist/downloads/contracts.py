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

#: The only qBittorrent category MangaList adds to, watches, or deletes from.
QBITTORRENT_CATEGORY = "mangalist"

#: qBittorrent states that mean "finished downloading and stopped" - v5 names, plus v4's for older clients.
STOPPED_COMPLETE_STATES = frozenset({"stoppedUP", "pausedUP"})


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


class TorrentClient(Protocol):
    def version(self) -> str:
        """Log in if needed and return the client's version (the connection test)."""
        ...

    def ensure_category(self, name: str, save_path: str) -> None: ...

    def add(self, url: str, *, category: str) -> None:
        """Add a torrent by ``.torrent`` URL or magnet link into ``category``."""
        ...

    def torrents(self, category: str) -> Sequence[TorrentInfo]: ...

    def files(self, info_hash: str) -> Sequence[TorrentFile]: ...

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
    SENT = "sent"                       # added to qBittorrent
    DOWNLOADED = "downloaded"           # torrent complete; filing pending
    FILED = "filed"                     # missing volumes linked (or copied) into the library
    REMOVED = "removed"                 # stopped at its seed goal; torrent + data deleted by qBittorrent
    FAILED = "failed"                   # see ``error``
    CANCELLED = "cancelled"

    ALL = (SENT, DOWNLOADED, FILED, REMOVED, FAILED, CANCELLED)


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


class DownloadStore(Protocol):
    def create(self, series_id: int, candidate: NyaaCandidate, wanted_volumes: Sequence[str],
               target_dir: str) -> DownloadRecord: ...

    def get(self, record_id: int) -> Optional[DownloadRecord]: ...

    def for_series(self, series_id: int) -> Sequence[DownloadRecord]: ...

    def active(self) -> Sequence[DownloadRecord]:
        """Every record not yet REMOVED / FAILED / CANCELLED."""
        ...

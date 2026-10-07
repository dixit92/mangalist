"""Sending the owner's pick to qBittorrent (the GUI calls :func:`send_pick`). No Qt here.

The arrivals pass (:mod:`mangalist.downloads.arrivals`) takes the record from there.
"""

from __future__ import annotations

import logging
import os
from typing import Optional, Sequence

from .contracts import QBITTORRENT_CATEGORY, DownloadRecord, DownloadStore, NyaaCandidate, Placement, TorrentClient
from .placement import same_or_inside

_log = logging.getLogger(__name__)


class SendRefused(ValueError):
    """The pick cannot be sent as given (nothing was added to qBittorrent)."""


def send_pick(client: TorrentClient, store: DownloadStore, series_id: int, candidate: NyaaCandidate,
              wanted_volumes: Sequence[str], placement: Placement, save_path: str) -> DownloadRecord:
    """Add *candidate* to qBittorrent in the ``mangalist`` category (saving to *save_path*) and record it as SENT.

    *placement* must be resolved: an ambiguous one is refused - the GUI asks the owner and passes a
    :class:`~mangalist.downloads.contracts.Placement` with the chosen ``target_dir`` (inside ``series_dir``).
    Refused too: no wanted volume, a torrent already tracked, and a *save_path* inside a library root (Remove
    Completed would then delete library files).
    """
    target = placement.target_dir
    if not target:
        raise SendRefused(f"where to file the volumes is not decided ({placement.reason}); choose a folder first")
    if not os.path.isabs(target) or not same_or_inside(target, placement.series_dir):
        raise SendRefused("the target folder must be the series folder or inside it")
    if not [v for v in wanted_volumes if str(v).strip()]:
        raise SendRefused("pick at least one wanted volume")
    if not (save_path or "").strip() or not os.path.isabs(save_path):
        raise SendRefused("the qBittorrent save path must be an absolute folder")
    active_for_hash = getattr(store, "active_for_hash", None)
    if active_for_hash is not None and active_for_hash(candidate.info_hash) is not None:
        raise SendRefused("this torrent is already being downloaded for MangaList")
    library: Optional[object] = getattr(store, "store", None)
    for root in (library.list_roots() if library is not None and hasattr(library, "list_roots") else ()):
        if same_or_inside(save_path, root.path) or same_or_inside(root.path, save_path):
            raise SendRefused(f"the qBittorrent save path overlaps the library root {root.path}; downloads must be "
                              "saved outside the library")
    client.ensure_category(QBITTORRENT_CATEGORY, save_path)
    client.add(candidate.torrent_url or candidate.magnet, category=QBITTORRENT_CATEGORY)
    record = store.create(series_id, candidate, wanted_volumes, target)
    _log.info("Sent download %d to qBittorrent: %s (volumes %s) -> %s", record.id, candidate.title,
              ", ".join(record.wanted_volumes), target)
    return record

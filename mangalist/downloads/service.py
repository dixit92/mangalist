"""Sending the owner's pick to qBittorrent (the GUI calls :func:`send_pick`). No Qt here.

The arrivals pass (:mod:`mangalist.downloads.arrivals`) takes the record from there.

**Only the missing volumes** (``only_missing=True``, the default of the GUI): the torrent is added STOPPED, qBittorrent's
own file list is read, the files that hold none of the wanted volumes get priority 0 ("do not download", decided by
:func:`mangalist.downloads.partial.choose_from_live`), the priorities are read back, and only then is the torrent
started. Any failure on the way stops and removes the torrent MangaList added in this call, so nothing half-configured
ever runs (and a leftover stopped torrent of an earlier failed send is picked up and finished by the next send). A
release that cannot be narrowed - a magnet link (no file list until it has peers), a name list that names no wanted
volume - is sent whole, and the outcome says why.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

from .contracts import (
    QBITTORRENT_CATEGORY,
    STOPPED_DOWNLOADING_STATES,
    DownloadRecord,
    DownloadStore,
    NyaaCandidate,
    Placement,
    TorrentClient,
    TorrentFile,
    TorrentInfo,
)
from .partial import PackOutcome, PackSelection, choose_from_live, hint_for, log_selection, priority_changes
from .placement import same_or_inside

_log = logging.getLogger(__name__)


def _in_category(client: TorrentClient, info_hash: str) -> bool:
    try:
        return any(t.info_hash.lower() == info_hash.lower() for t in client.torrents(QBITTORRENT_CATEGORY))
    except Exception:  # noqa: BLE001 - cannot tell: the add's own error stands
        return False


class SendRefused(ValueError):
    """The pick cannot be sent as given (nothing was added to qBittorrent)."""


class PackSetupError(RuntimeError):
    """A partial download could not be set up; the message says why and that the torrent was not started."""


@dataclass
class PackWait:
    """How long to wait for qBittorrent to list a torrent added by link and to know its files (it fetches the
    ``.torrent`` itself, after answering the add). Injectable so tests never sleep."""

    timeout: float = 60.0
    interval: float = 0.5
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic


def _find(client: TorrentClient, info_hash: str) -> Optional[TorrentInfo]:
    for t in client.torrents(QBITTORRENT_CATEGORY):
        if t.info_hash.lower() == info_hash.lower():
            return t
    return None


def _add_whole(client: TorrentClient, candidate: NyaaCandidate) -> None:
    """The whole release, started at once (what a send always did before partial downloads)."""
    try:
        client.add(candidate.torrent_url or candidate.magnet, category=QBITTORRENT_CATEGORY)
    except Exception:
        # Already there? (sent before, but the record was not written - a crash, or an answer MangaList did not
        # understand): a torrent with this hash in MangaList's own category is recorded, not refused.
        if not _in_category(client, candidate.info_hash):
            raise
        _log.info("Sent download: %s is already in qBittorrent's %r category; recording it", candidate.title,
                  QBITTORRENT_CATEGORY)
        _reopen(client, candidate.info_hash, None, None)


def _reopen(client: TorrentClient, info_hash: str, wanted: Optional[Sequence[str]], hint: Optional[str]) -> None:
    """A torrent that was there already may have had files switched off by an earlier partial send: switch on the
    ones this send wants (every one when *wanted* is None) and start it. Best effort - a torrent is never stopped,
    removed or narrowed here."""
    try:
        live = list(client.files(info_hash))
        if wanted is None:
            enable = [f.index if f.index >= 0 else i for i, f in enumerate(live) if f.priority == 0]
        else:
            enable = list(priority_changes(live, choose_from_live(live, wanted, hint))[1])
        if not enable:
            return
        client.set_file_priority(info_hash, enable, 1)
        client.start(info_hash)
        _log.info("Sent download: torrent %s was added before with %d file(s) switched off; they are on now",
                  info_hash, len(enable))
    except Exception as exc:  # noqa: BLE001 - the send itself already worked
        _log.warning("Sent download: could not switch on the skipped files of torrent %s: %s: %s", info_hash,
                     type(exc).__name__, exc)


def _wait_for_files(client: TorrentClient, info_hash: str, wait: PackWait) -> Sequence[TorrentFile]:
    """The torrent's file list once qBittorrent has the torrent listed and knows its files."""
    deadline = wait.clock() + wait.timeout
    last = "qBittorrent did not list the torrent"
    while True:
        try:
            if _find(client, info_hash) is not None:
                files = list(client.files(info_hash))
                if files:
                    return files
                last = "qBittorrent has not read the torrent's file list yet"
        except LookupError:
            last = "qBittorrent does not know the torrent yet"
        if wait.clock() >= deadline:
            raise PackSetupError(f"{last} within {wait.timeout:.0f} s")
        wait.sleep(wait.interval)


def _add_partial(client: TorrentClient, candidate: NyaaCandidate, wanted: Sequence[str], wait: PackWait) -> PackOutcome:
    """Add stopped -> read files -> priorities -> verify -> start. Returns what was done."""
    link = candidate.torrent_url
    hint = hint_for(candidate)
    if not link.lower().startswith(("http://", "https://")):
        _log.info("Partial: %s: no .torrent link (a magnet link has no file list before it has peers); sending the "
                  "whole pack", candidate.title)
        _add_whole(client, candidate)
        return PackOutcome(False, PackSelection(wanted=tuple(wanted)),
                           "the release has only a magnet link, so its files are not known before it starts")
    h = candidate.info_hash
    existing = _find(client, h)
    added_here = adopted = False
    if existing is None:
        try:
            client.add(link, category=QBITTORRENT_CATEGORY, stopped=True)
            added_here = True
            _log.info("Partial: %s: added to qBittorrent stopped", candidate.title)
        except Exception:
            existing = _find_quietly(client, h)
            if existing is None:
                raise
    if existing is not None:
        if existing.state in STOPPED_DOWNLOADING_STATES and not existing.complete:
            adopted = True          # a leftover of a failed earlier send: finish configuring it
            _log.info("Partial: %s: found stopped in qBittorrent from an earlier send; configuring it now",
                      candidate.title)
        else:
            _log.info("Partial: %s: already in qBittorrent (%s); left as it is", candidate.title, existing.state)
            _reopen(client, h, wanted, hint)
            return PackOutcome(False, PackSelection(wanted=tuple(wanted)),
                               "it was in qBittorrent already, so it was left as it is")
    try:
        live = _wait_for_files(client, h, wait)
        selection = choose_from_live(live, wanted, hint)
        log_selection(candidate.title, selection, "qBittorrent's file list")
        skip, enable = priority_changes(live, selection)
        if skip:
            client.set_file_priority(h, skip, 0)
            _log.info("Partial: %s: %d file(s) set to 'do not download'", candidate.title, len(skip))
        if enable:
            client.set_file_priority(h, enable, 1)
            _log.info("Partial: %s: %d wanted file(s) set to normal priority", candidate.title, len(enable))
        if skip or enable:
            _verify(client, h, selection)
        client.start(h)
        _log.info("Partial: %s: started (%s)", candidate.title,
                  f"{selection.kept_files} of {selection.total_files} files" if selection.narrows else "the whole pack")
    except Exception as exc:
        _undo(client, candidate, added_here, exc)
        if isinstance(exc, PackSetupError):
            raise PackSetupError(f"{exc}. The torrent was not started.") from None
        raise PackSetupError(f"{exc}. The torrent was not started.") from exc
    return PackOutcome(selection.narrows, selection, selection.whole_reason or "")


def _find_quietly(client: TorrentClient, info_hash: str) -> Optional[TorrentInfo]:
    try:
        return _find(client, info_hash)
    except Exception:  # noqa: BLE001 - cannot tell: the add's own error stands
        return None


def _verify(client: TorrentClient, info_hash: str, selection: PackSelection) -> None:
    """Read the priorities back: every skipped file at 0, every kept file above 0. A mismatch is an error - the
    torrent is not started on a guess."""
    after = list(client.files(info_hash))
    if len(after) != selection.total_files:
        raise PackSetupError("qBittorrent's file list changed while the priorities were set")
    for f, choice in zip(after, selection.files):
        if (f.priority == 0) == choice.keep:
            raise PackSetupError(f"qBittorrent did not keep the priority of {f.name}")


def _undo(client: TorrentClient, candidate: NyaaCandidate, added_here: bool, error: Exception) -> None:
    """The configuration failed: make sure the torrent is not running with the wrong files, and take away the one
    this call added (nothing was downloaded - it never started). A leftover of an earlier send stays, stopped."""
    _log.warning("Partial: %s: configuration failed (%s: %s); stopping the torrent", candidate.title,
                 type(error).__name__, error)
    h = candidate.info_hash
    try:
        client.stop(h)
    except Exception as exc:  # noqa: BLE001
        _log.warning("Partial: %s: could not stop it: %s: %s", candidate.title, type(exc).__name__, exc)
    if not added_here:
        return
    try:
        client.delete(h, delete_files=False)
        _log.info("Partial: %s: the torrent added for it was removed again", candidate.title)
    except Exception as exc:  # noqa: BLE001 - it stays stopped; the next send finishes it
        _log.warning("Partial: %s: could not remove it (it stays stopped): %s: %s", candidate.title,
                     type(exc).__name__, exc)


def send_pick(client: TorrentClient, store: DownloadStore, series_id: int, candidate: NyaaCandidate,
              wanted_volumes: Sequence[str], placement: Placement, save_path: str, *, only_missing: bool = False,
              wait: Optional[PackWait] = None,
              on_pack: Optional[Callable[[PackOutcome], None]] = None) -> DownloadRecord:
    """Add *candidate* to qBittorrent in the ``mangalist`` category (saving to *save_path*) and record it as SENT.

    ``only_missing``: download only the files that hold *wanted_volumes* (see the module docstring); *on_pack*
    receives what was done with the pack, before the record is written.

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
    outcome: Optional[PackOutcome] = None
    if only_missing:
        outcome = _add_partial(client, candidate, [str(v) for v in wanted_volumes], wait or PackWait())
    else:
        _add_whole(client, candidate)
    if outcome is not None and on_pack is not None:
        on_pack(outcome)
    record = store.create(series_id, candidate, wanted_volumes, target)
    _log.info("Sent download %d to qBittorrent: %s (volumes %s) -> %s", record.id, candidate.title,
              ", ".join(record.wanted_volumes), target)
    return record

"""Ask MangaPixer to rescan the libraries MangaList filed into (MangaPixer 1.36.0, the token's ``library:scan`` scope).

After a downloads pass filed volumes into series folders, each MangaPixer library those folders belong to (the roots'
mappings) gets one full-scan request, so MangaPixer sees the new files within minutes instead of at its next scheduled
scan. A request MangaPixer cannot start now (a scan running, the 5-minute cooldown, rate limits, MangaPixer unreachable)
is kept as *pending* with the time it may be tried again; every later pass retries the due ones - never sooner, never in
a loop. A token without the scope (403) stops all requests until a new token is entered. No Qt.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable, List, Optional, Set, Tuple

from ...store.mangapixer import MangaPixerCache
from . import client as mpc

_log = logging.getLogger(__name__)

#: When MangaPixer could not be reached (or answered oddly): try again this much later.
RETRY_LATER = timedelta(minutes=5)


@dataclass
class ScanReport:
    started: List[str] = field(default_factory=list)                    # library ids
    pending: List[Tuple[str, str]] = field(default_factory=list)        # (library id, not before)
    dropped: List[Tuple[str, str]] = field(default_factory=list)        # (library id, why)
    skipped: Optional[str] = None                                       # why nothing was asked at all

    def summary(self) -> str:
        if self.skipped:
            return f"MangaPixer scans: {self.skipped}"
        parts = []
        if self.started:
            parts.append(f"{len(self.started)} started")
        if self.pending:
            parts.append(f"{len(self.pending)} pending")
        if self.dropped:
            parts.append(f"{len(self.dropped)} dropped")
        return "MangaPixer scans: " + (", ".join(parts) if parts else "none needed")


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse(value: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def libraries_for_series(cache: MangaPixerCache, series_ids: Iterable[int]) -> Set[str]:
    """The MangaPixer libraries the given library-database series belong to (through their roots' mappings)."""
    db = cache.store
    out: Set[str] = set()
    with db.connect() as con:
        for sid in {int(s) for s in series_ids if s is not None}:
            row = con.execute("SELECT root_id FROM series WHERE id = ?", (sid,)).fetchone()
            if row is None:
                continue
            mapping = cache.mapping(int(row["root_id"]))
            if mapping is not None and mapping.library_id:
                out.add(str(mapping.library_id))
    return out


def request_scans(cache: MangaPixerCache, library_ids: Iterable[str] = (), *,
                  client: Optional[mpc.MangaPixerClient] = None,
                  client_factory: Optional[Callable[[MangaPixerCache], mpc.MangaPixerClient]] = None,
                  now: Optional[datetime] = None) -> ScanReport:
    """Ask for a scan of *library_ids* plus every pending library that is due; keep what cannot start now."""
    now = now or datetime.now(timezone.utc)
    report = ScanReport()
    for lib in library_ids:                       # new requests join the pending ones, due now
        if lib not in cache.pending_scans():
            cache.set_scan_pending(lib, _iso(now))
    pending = cache.pending_scans()
    if not pending:
        return report
    conn = cache.connection()
    if not conn.base_url or not conn.has_token:
        report.skipped = "no MangaPixer connection"
        return report
    if conn.token_rejected_at:
        report.skipped = "MangaPixer refused the token; enter a new one"
        return report
    if cache.scan_forbidden_at():
        for lib in list(pending):
            cache.clear_scan_pending(lib)
        report.skipped = "this token cannot request library scans (create one with 'Request library scans')"
        return report
    due = sorted(lib for lib, when in pending.items() if (_parse(when) or now) <= now)
    for lib, when in pending.items():
        if lib not in due:
            report.pending.append((lib, when))
    if not due:
        return report
    own = client is None
    if own:
        if client_factory is None:
            from .sync import client_from_cache as client_factory
        client = client_factory(cache)
    try:
        for lib in due:
            try:
                answer = client.request_scan(lib)
            except mpc.TokenRejected:
                cache.mark_token_rejected()
                report.skipped = "MangaPixer refused the token; enter a new one"
                _log.warning("MangaPixer scans: the token was refused; no more requests until a new token")
                break
            except mpc.LibraryNotFound:
                cache.clear_scan_pending(lib)
                report.dropped.append((lib, "the library is gone"))
                continue
            except mpc.MangaPixerError as exc:
                later = _iso(now + RETRY_LATER)
                cache.set_scan_pending(lib, later)
                report.pending.append((lib, later))
                _log.info("MangaPixer scans: %s - trying again after %s", exc, later)
                continue
            if answer.outcome == mpc.SCAN_STARTED:
                cache.clear_scan_pending(lib)
                report.started.append(lib)
                _log.info("MangaPixer scans: started a scan of library %s (run %s)", lib, answer.run_id or "?")
            elif answer.outcome == mpc.SCAN_FORBIDDEN:
                cache.mark_scan_forbidden()
                for other in list(cache.pending_scans()):
                    cache.clear_scan_pending(other)
                report.skipped = "this token cannot request library scans (create one with 'Request library scans')"
                _log.warning("MangaPixer scans: the token lacks the library:scan scope; no requests until a new token")
                break
            else:                                  # busy / cooldown: as MangaPixer says, not sooner
                later = _iso(now + timedelta(seconds=answer.retry_after or 60))
                cache.set_scan_pending(lib, later)
                report.pending.append((lib, later))
                _log.info("MangaPixer scans: library %s %s; trying again after %s", lib, answer.outcome, later)
    finally:
        if own and client is not None:
            client.close()
    return report

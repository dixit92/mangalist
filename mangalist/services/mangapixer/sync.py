"""Keeping the local copy of MangaPixer's export current (per library).

- **Full sync** (first connect, after ``409 fullSyncRequired``, or on request): no ``updatedSince``;
  follow ``nextCursor``; afterwards rows whose node the export no longer lists are dropped.
- **Incremental sync**: ``updatedSince`` = the FIRST page's ``serverTime`` kept from the last complete
  sync (MangaPixer's clock, never ours). Inclusive, so items may repeat: every item is an upsert.
  ``removed`` (first page) is applied before that page's items. An item with ``carriedFrom`` re-keys the
  old node's row instead of a remove + add; the old node's trail is kept first (``mangapixer_carries``) so a
  missing MangaList series can follow it (:mod:`mangalist.identity.mangapixer`, applied after the sync).
- The kept ``serverTime`` only moves after the last page arrived, so an interrupted sync simply
  repeats from the previous time.
- Only ``nodeKind == "folder"`` items are kept (MangaList is series-only, A6); archive items are
  dropped and never take part in inheritance. The item JSON is stored exactly as MangaPixer sent it.
- Libraries: those of kind ``manga, manhwa, manhua, webtoon`` or no kind are synced; ``comic,
  graphic-novel, novel`` (and unknown kinds) only when a root's mapping overrides the kind.
- **401 stops everything** - no further request in this run, and the connection is marked "token
  rejected" so no scheduled run tries the same token again (a new token or a manual retry clears it).
- 404 ``libraryNotFound``: the libraries are re-read, the library is marked gone, the owner re-maps.

After the libraries are synced, roots without a manual mapping are mapped automatically again
(:func:`mangalist.services.mangapixer.mapping.refresh_auto_mappings`).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from ...store.db import utcnow
from ...store.mangapixer import LibraryRow, MangaPixerCache
from . import client as mpc

_log = logging.getLogger(__name__)

ProgressFn = Callable[[int, int, str], None]   # (items done, items expected (0 = unknown), label)
StopFn = Callable[[], bool]


class SyncStopped(Exception):
    """The caller asked to stop (between pages)."""


@dataclass
class LibrarySync:
    library_id: str
    name: str = ""
    mode: str = ""                 # full | incremental
    upserted: int = 0
    archives_dropped: int = 0
    removed: int = 0
    pruned: int = 0                # rows a full sync no longer saw
    rekeyed: List[Tuple[str, str]] = field(default_factory=list)
    pages: int = 0
    server_time: Optional[str] = None
    fell_back_to_full: bool = False
    error: Optional[str] = None


@dataclass
class SyncResult:
    status: str = "ok"             # ok | error | skipped
    message: str = ""
    libraries: List[LibrarySync] = field(default_factory=list)
    token_rejected: bool = False
    mappings: Dict[int, Any] = field(default_factory=dict)
    carried: List[Any] = field(default_factory=list)   # mangalist.identity.carry.CarryResult (carriedFrom)

    @property
    def renamed(self) -> List[Tuple[Any, Any]]:
        """``(old folder, new folder)`` of the MangaList series that followed MangaPixer's carriedFrom."""
        return [(c.old_folder, c.new_folder) for c in self.carried if c.carried]

    def summary(self) -> Dict[str, Any]:
        return {"libraries": [{"id": s.library_id, "name": s.name, "mode": s.mode, "items": s.upserted,
                               "removed": s.removed, "pruned": s.pruned, "rekeyed": len(s.rekeyed),
                               "archives_dropped": s.archives_dropped, "error": s.error}
                              for s in self.libraries],
                "token_rejected": self.token_rejected,
                "series_carried": sum(1 for c in self.carried if c.carried)}


def is_folder(item: Dict[str, Any]) -> bool:
    """True for a folder item. Items without ``nodeKind`` (none in 1.33.0) count as folders."""
    return (item.get("nodeKind") or "folder") == "folder"


def client_from_cache(cache: MangaPixerCache, **kwargs) -> mpc.MangaPixerClient:
    """A client for the stored connection (raises ``ValueError`` without an address)."""
    conn = cache.connection()
    return mpc.MangaPixerClient(conn.base_url, cache.token(), verify=conn.verify, **kwargs)


def sync_library(cache: MangaPixerCache, client: mpc.MangaPixerClient, library: LibraryRow,
                 force_full: bool = False, include: Optional[Sequence[str]] = None, limit: Optional[int] = None,
                 progress: Optional[ProgressFn] = None, should_stop: Optional[StopFn] = None) -> LibrarySync:
    """Sync one library. Raises :class:`~mangalist.services.mangapixer.client.TokenRejected` (and other
    client errors) to the caller; a 409 turns into one full sync here."""
    state = cache.sync_state(library.id)
    since = None if force_full else state.server_time
    out = LibrarySync(library.id, library.display_name)
    try:
        _run(cache, client, library, since, out, include, limit, progress, should_stop)
    except mpc.FullSyncRequired:
        _log.info("MangaPixer: %s asks for a full sync", library.display_name)
        state.server_time = None          # a failed full sync must not fall back to the stale time
        cache.save_sync_state(state)
        out = LibrarySync(library.id, library.display_name, fell_back_to_full=True)
        _run(cache, client, library, None, out, include, limit, progress, should_stop)
    now = utcnow()
    state.server_time = out.server_time
    state.last_sync_at = now
    state.last_status = "ok"
    state.last_error = None
    state.last_mode = out.mode
    if out.mode == "full":
        state.last_full_at = now
    cache.save_sync_state(state)
    _log.info("MangaPixer: %s synced (%s): %d items, %d removed, %d re-keyed, %d archives skipped",
              library.display_name, out.mode, out.upserted, out.removed + out.pruned, len(out.rekeyed),
              out.archives_dropped)
    return out


def _run(cache, client, library, since, out: LibrarySync, include, limit, progress, should_stop) -> None:
    out.mode = "incremental" if since else "full"
    seen: List[str] = []
    expected = library.item_count or 0
    done = 0
    for page in client.export_pages(library.id, updated_since=since, include=include, limit=limit):
        if should_stop is not None and should_stop():
            raise SyncStopped()
        if page.first:
            out.server_time = page.server_time
        folders = [i for i in page.items if is_folder(i)]
        out.archives_dropped += len(page.items) - len(folders)
        removed = [str(r["nodeId"]) for r in page.removed] if since else []
        carries = _carried_trails(cache, library.id, folders)
        applied = cache.apply_page(library.id, removed, folders)
        if carries:
            _record_carries(cache, library.id, carries)
        out.upserted += applied.upserted
        out.removed += applied.removed
        out.rekeyed.extend(applied.rekeyed)
        out.pages += 1
        seen.extend(str(i["nodeId"]) for i in folders)
        done += len(page.items)
        if progress is not None:
            progress(done, max(expected, done) if since is None else 0, library.display_name)
    if since is None:
        out.pruned = cache.keep_only(library.id, seen)
    if not out.server_time:
        raise mpc.ServerError("the export's first page had no serverTime")


def _carried_trails(cache: MangaPixerCache, library_id: str, folders) -> List[Tuple[str, str, List[str], List[str]]]:
    """``(old nodeId, new nodeId, old trail, new trail)`` for every item with ``carriedFrom`` whose old node is
    still in the cache - read BEFORE the page re-keys the old row away (series identity, :mod:`mangalist.identity`)."""
    out = []
    for item in folders:
        old, new = item.get("carriedFrom"), str(item["nodeId"])
        if not old or str(old) == new:
            continue
        prev = cache.item(library_id, str(old))
        if prev is not None and isinstance(prev.get("trail"), list):
            out.append((str(old), new, [str(p) for p in prev["trail"]], [str(p) for p in (item.get("trail") or [])]))
    return out


def _record_carries(cache: MangaPixerCache, library_id: str, carries) -> None:
    try:
        from ...identity.mangapixer import record_carries

        record_carries(cache.store, library_id, carries)
    except Exception:  # noqa: BLE001 - the sync itself must not fail on the identity layer
        _log.warning("MangaPixer: keeping the carriedFrom pairs failed", exc_info=True)


def _apply_carries(cache: MangaPixerCache, result: "SyncResult") -> None:
    """MangaList series whose folder MangaPixer carried (``carriedFrom``) follow it, when MangaList already
    sees the old folder missing and the new one live (else the pair waits for MangaList's next scan)."""
    try:
        from ...identity.mangapixer import apply_pending

        result.carried = apply_pending(cache.store)
    except Exception:  # noqa: BLE001
        _log.warning("MangaPixer: applying the carriedFrom pairs to MangaList's series failed", exc_info=True)


def sync_all(cache: MangaPixerCache, client: Optional[mpc.MangaPixerClient] = None, force_full: bool = False,
             progress: Optional[ProgressFn] = None, should_stop: Optional[StopFn] = None,
             manual: bool = False, list_series: Optional[Callable[[int], List[str]]] = None,
             include: Optional[Sequence[str]] = None, limit: Optional[int] = None) -> SyncResult:
    """Sync every library MangaList uses, then refresh the automatic root mappings.

    *manual*: the owner pressed "Sync now" (a stored "token rejected" stop is lifted for this one try).
    A scheduled run (``manual=False``) is skipped while the stop is set.

    Logged: INFO when it starts and how it ended (``skipped`` is INFO too, a failed run WARNING); the details are
    logged by :func:`sync_library` and the client. Never the token.
    """
    started = time.monotonic()
    _log.info("MangaPixer: sync started (%s%s)", "on the owner's request" if manual else "scheduled",
              ", full" if force_full else "")
    result = _sync_all(cache, client, force_full, progress, should_stop, manual, list_series, include, limit)
    took = time.monotonic() - started
    if result.status == "ok":
        _log.info("MangaPixer: sync finished in %.1f s: %s", took, result.message)
    elif result.status == "skipped":
        _log.info("MangaPixer: sync skipped: %s", result.message)
    else:
        _log.warning("MangaPixer: sync ended with %s after %.1f s: %s", result.status, took, result.message)
    return result


def _sync_all(cache: MangaPixerCache, client, force_full, progress, should_stop, manual, list_series, include,
              limit) -> SyncResult:
    from .mapping import refresh_auto_mappings

    conn = cache.connection()
    result = SyncResult()
    if not conn.base_url or not conn.has_token:
        result.status, result.message = "skipped", "no MangaPixer source configured"
        return result
    if conn.token_rejected_at and not manual:
        result.status = "skipped"
        result.message = ("MangaPixer refused the token at " + conn.token_rejected_at +
                          "; enter a new token (no automatic retry)")
        return result
    own_client = client is None
    try:
        if client is None:
            client = client_from_cache(cache)
    except ValueError as exc:
        result.status, result.message = "error", str(exc)
        return result
    try:
        try:
            cache.save_libraries(client.libraries())
        except mpc.TokenRejected as exc:
            return _token_rejected(cache, result, exc)
        except mpc.MangaPixerError as exc:
            result.status, result.message = "error", str(exc)
            return result
        if conn.token_rejected_at:
            cache.mark_token_rejected(False)
        failed = 0
        for lib in cache.libraries_to_sync():
            if should_stop is not None and should_stop():
                result.status, result.message = "error", "stopped"
                return result
            try:
                result.libraries.append(sync_library(cache, client, lib, force_full=force_full, include=include,
                                                     limit=limit, progress=progress, should_stop=should_stop))
            except mpc.TokenRejected as exc:
                return _token_rejected(cache, result, exc)
            except SyncStopped:
                result.status, result.message = "error", "stopped"
                return result
            except mpc.LibraryNotFound as exc:
                failed += 1
                cache.mark_library_gone(lib.id)
                _record_error(cache, result, lib, str(exc))
                try:
                    cache.save_libraries(client.libraries())
                except mpc.TokenRejected as exc2:
                    return _token_rejected(cache, result, exc2)
                except mpc.MangaPixerError:
                    pass
            except mpc.UnsupportedSchema as exc:
                _record_error(cache, result, lib, str(exc))
                result.status, result.message = "error", str(exc)
                return result
            except mpc.RateLimited as exc:
                _record_error(cache, result, lib, str(exc))
                result.status, result.message = "error", str(exc)
                return result
            except mpc.MangaPixerError as exc:
                failed += 1
                _record_error(cache, result, lib, str(exc))
        try:
            result.mappings = refresh_auto_mappings(cache, list_series=list_series)
        except Exception:  # noqa: BLE001 - a mapping problem must not lose the synced data
            _log.exception("MangaPixer: automatic mapping failed")
        _apply_carries(cache, result)
        n = len(result.libraries)
        if failed:
            result.status = "error"
            result.message = f"{n - failed} of {n} libraries synced"
        else:
            result.message = f"{n} libraries synced"
        return result
    finally:
        if own_client and client is not None:
            client.close()


def _record_error(cache: MangaPixerCache, result: SyncResult, lib: LibraryRow, message: str) -> None:
    _log.warning("MangaPixer: sync of %s failed: %s", lib.display_name, message)
    state = cache.sync_state(lib.id)
    state.last_status, state.last_error = "error", message
    cache.save_sync_state(state)
    result.libraries.append(LibrarySync(lib.id, lib.display_name, error=message))


def _token_rejected(cache: MangaPixerCache, result: SyncResult, exc: Exception) -> SyncResult:
    _log.warning("MangaPixer: %s", exc)
    cache.mark_token_rejected(True)
    result.status, result.message, result.token_rejected = "error", str(exc), True
    return result

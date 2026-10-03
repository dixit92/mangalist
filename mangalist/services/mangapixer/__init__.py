"""MangaPixer as a data source (MangaPixer 1.33.0+ metadata export, read-only, API token).

- :mod:`.client`: the HTTP client (typed errors; 401 never retried; the token never logged).
- :mod:`.sync`: full / incremental sync of each library into the library database.
- :mod:`.mapping`: MangaList root -> MangaPixer library + trail prefix (automatic, or the owner's override).
- :mod:`.resolve`: a series folder's effective MangaPixer item (own or inherited) - the states' input.

The cache tables live in :mod:`mangalist.store.mangapixer`. No Qt anywhere in this package.
"""

from __future__ import annotations

from ...store.mangapixer import MangaPixerCache
from .client import (
    BadRequest,
    ConnectionFailed,
    Forbidden,
    FullSyncRequired,
    LibraryNotFound,
    MangaPixerClient,
    MangaPixerError,
    NotFound,
    RateLimited,
    ServerError,
    TokenRejected,
    UnsupportedSchema,
    usable_record,
)
from .resolve import Resolution, Resolver, resolve
from .sync import SyncResult, sync_all, sync_library


def open_cache(store=None) -> MangaPixerCache:
    """The MangaPixer tables of *store* (default: the data folder's database)."""
    if store is None:
        from ... import store as _store

        store = _store.get_store()
    return MangaPixerCache(store)


__all__ = [
    "MangaPixerCache", "MangaPixerClient", "MangaPixerError", "ConnectionFailed", "TokenRejected", "Forbidden",
    "NotFound", "LibraryNotFound", "FullSyncRequired", "RateLimited", "BadRequest", "UnsupportedSchema",
    "ServerError", "usable_record", "Resolution", "Resolver", "resolve", "SyncResult", "sync_all", "sync_library",
    "open_cache",
]

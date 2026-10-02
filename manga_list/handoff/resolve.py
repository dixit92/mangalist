"""Finds the MangaDex record of each wanted series, through the cache first (no Qt).

A network failure leaves that series unresolved for this export without caching the miss, so the next export tries
again; a search that ran and found no MangaDex record linking to the series is cached (``mu_cache``).
"""

from __future__ import annotations

import logging
from typing import Callable, List, Optional, Sequence

from .. import mu_cache
from .exports import Resolved
from .mangadex import GetJson, resolve
from .wanted import Wanted

_log = logging.getLogger(__name__)

# progress(done, total, title) -> False to stop early
Progress = Callable[[int, int, str], bool]


def resolve_all(items: Sequence[Wanted], get_json: GetJson, progress: Optional[Progress] = None) -> List[Resolved]:
    """``(item, MangaDex UUID or None)`` for each item, in order; stops early when ``progress`` returns False (the
    rest are returned unresolved)."""
    result: List[Resolved] = []
    stopped = False
    for i, item in enumerate(items):
        if not stopped and progress is not None and progress(i, len(items), item.mu_title) is False:
            stopped = True
        if stopped:
            result.append((item, None))
            continue
        cached = mu_cache.load_mangadex_id(item.mu_id)
        if cached is not mu_cache.UNKNOWN:
            result.append((item, cached))
            continue
        try:
            uuid = resolve(item.mu_id, item.titles, get_json)
        except Exception as exc:  # noqa: BLE001 - one series failing must not end the export
            _log.warning("MangaDex lookup failed for MangaUpdates %s: %s", item.mu_id, type(exc).__name__)
            result.append((item, None))
            continue
        mu_cache.save_mangadex_id(item.mu_id, uuid)
        result.append((item, uuid))
    if progress is not None and not stopped:
        progress(len(items), len(items), "")
    return result

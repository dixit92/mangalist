"""MangaDex's public API, as far as MangaList asks it itself (no Qt, no account).

- :mod:`.client`: :class:`MangaDexClient` - the latest uploaded chapter of many manga in one request (``GET /manga``
  with up to 100 ids), for the scheduled chapter check's gate (:mod:`mangalist.downloads.chapter_gate`; owner-approved
  2026-10-18, "Shared Chapter Signal").

Adding a series reads one MangaDex record on its own (:func:`mangalist.add_series.fetch_mangadex`).
"""

from __future__ import annotations

from .client import (
    API_URL,
    CONTENT_RATINGS,
    MAX_IDS,
    LatestUploads,
    MangaDexClient,
    MangaDexError,
    RateLimited,
    UnexpectedResponse,
    Unreachable,
)

__all__ = ["API_URL", "CONTENT_RATINGS", "LatestUploads", "MAX_IDS", "MangaDexClient", "MangaDexError", "RateLimited",
           "UnexpectedResponse", "Unreachable"]

"""Nyaa as a source of volume releases (public RSS search; no account; no Qt).

- :mod:`.client`: the polite HTTP client (>= 2 s between requests, timeout, Retry-After, typed errors, injectable ``get``).
- :mod:`.rss`: the feed -> :class:`RssItem` (``nyaa:`` fields, size, date).
- :mod:`.titles`: a release title -> series, volume range, Digital, group, pack, not-comic.
- :mod:`.ranking`: series match (normalised-title equality) and the D4 ranking.
- :mod:`.search`: :class:`NyaaSearch`, the :class:`~mangalist.downloads.contracts.VolumeSearch` implementation.
"""

from __future__ import annotations

from .client import (
    CATEGORY_ENGLISH_TRANSLATED,
    CATEGORY_RAW,
    NyaaClient,
    NyaaError,
    RateLimited,
    UnexpectedResponse,
    Unreachable,
)
from .rss import FeedError, RssItem, parse_feed
from .search import NyaaSearch
from .titles import ParsedTitle, parse_title

__all__ = [
    "CATEGORY_ENGLISH_TRANSLATED", "CATEGORY_RAW", "FeedError", "NyaaClient", "NyaaError", "NyaaSearch",
    "ParsedTitle", "RateLimited", "RssItem", "UnexpectedResponse", "Unreachable", "parse_feed", "parse_title",
]

"""Suwayomi-Server as MangaList's chapter download client (GraphQL; pinned to v2.4.2366; no Qt).

:class:`SuwayomiClient` implements :class:`mangalist.downloads.contracts.ChapterClient`. See :mod:`.client` for the
operations used and the rules (basic auth only, the password never shown, exact chapter numbers).
"""

from __future__ import annotations

from .client import (
    PINNED_VERSION,
    AuthFailed,
    GraphQLError,
    SuwayomiClient,
    SuwayomiError,
    UnexpectedResponse,
    Unreachable,
    client_from_connection,
    normalize_base_url,
)

__all__ = [
    "PINNED_VERSION", "AuthFailed", "GraphQLError", "SuwayomiClient", "SuwayomiError", "UnexpectedResponse",
    "Unreachable", "client_from_connection", "normalize_base_url",
]

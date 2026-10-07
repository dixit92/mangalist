"""qBittorrent as MangaList's download client (Web API v2; 5.x, compatible with 4.x; no Qt).

:class:`QbtClient` implements :class:`mangalist.downloads.contracts.TorrentClient`. See :mod:`.client` for the rules
(no retry of wrong credentials, one re-login, the password never shown, ``delete`` only in the ``mangalist`` category).
"""

from __future__ import annotations

from .client import (
    AuthFailed,
    DeleteRefused,
    IpBanned,
    QbtClient,
    QbtError,
    TorrentNotFound,
    TorrentRejected,
    UnexpectedResponse,
    Unreachable,
    normalize_base_url,
)


def client_from_connection(conn) -> QbtClient:
    """The client for a stored :class:`~mangalist.downloads.contracts.QbtConnection` (the downloads job and the GUI's
    backend build theirs here)."""
    return QbtClient(conn)

__all__ = [
    "AuthFailed", "DeleteRefused", "IpBanned", "QbtClient", "QbtError", "TorrentNotFound", "TorrentRejected",
    "UnexpectedResponse", "Unreachable", "client_from_connection", "normalize_base_url",
]

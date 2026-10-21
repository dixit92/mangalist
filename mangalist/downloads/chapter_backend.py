"""Which program downloads chapters for MangaList, and the ONE place its client is built (prototype/mihon-mangalist).
No Qt.

``main`` downloads chapters through Suwayomi-Server only. This branch adds a built-in alternative: MangaList's own
extension host (``extension-host/``: Mihon / Tachiyomi extensions on a JVM, without Suwayomi), reached through its
Suwayomi-compatible ``/compat`` API by :class:`mangalist.services.extensions.compat.ExtensionsChapterClient`. The owner
chooses in Settings > Connected services ("Chapter downloads: Suwayomi | Built-in extensions"; default Suwayomi;
:meth:`mangalist.store.downloads.SuwayomiSettings.backend`). The stored connection says which program it is
(:attr:`~mangalist.downloads.contracts.SuwayomiConnection.backend`) and :func:`client_from_connection` builds that
program's client - the GUI's backend, the downloads job, the find pass, the chapter check and Add a series all build
theirs here. Both clients answer the same dataclasses and raise the Suwayomi client's error types.
"""

from __future__ import annotations

from typing import Optional

from .contracts import CHAPTER_BACKEND_EXTENSIONS, CHAPTER_BACKEND_SUWAYOMI, ChapterClient, SuwayomiConnection

LABELS = {CHAPTER_BACKEND_SUWAYOMI: "Suwayomi", CHAPTER_BACKEND_EXTENSIONS: "Built-in extensions"}
#: What a message calls the program that answers ("Suwayomi did not answer", "the extension host did not answer").
NAMES = {CHAPTER_BACKEND_SUWAYOMI: "Suwayomi", CHAPTER_BACKEND_EXTENSIONS: "the extension host"}


def client_from_connection(conn: SuwayomiConnection, **kwargs) -> ChapterClient:
    """The chapter client of *conn*'s program (Suwayomi's GraphQL, or the extension host's ``/compat``)."""
    if getattr(conn, "backend", CHAPTER_BACKEND_SUWAYOMI) == CHAPTER_BACKEND_EXTENSIONS:
        from ..services.extensions.compat import client_from_connection as extensions_client

        return extensions_client(conn, **kwargs)
    from ..services.suwayomi import client_from_connection as suwayomi_client

    return suwayomi_client(conn, **kwargs)


def name_of(conn: Optional[SuwayomiConnection]) -> str:
    backend = getattr(conn, "backend", CHAPTER_BACKEND_SUWAYOMI) if conn is not None else CHAPTER_BACKEND_SUWAYOMI
    return NAMES.get(backend, "Suwayomi")


def not_set_up(builtin: bool) -> str:
    """The sentence for "no chapter client can be built" (where the owner sets it up)."""
    if builtin:
        return "the extension host is not set up yet (Settings > Connected services > Built-in extensions)"
    return "Suwayomi is not set up yet (Settings > Connected services > Suwayomi)"

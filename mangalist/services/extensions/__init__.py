"""MangaList's own Mihon extension host (prototype branch ``prototype/mihon-extensions``; no Qt).

Runs Mihon / Tachiyomi extensions without Suwayomi: ``extension-host/`` is a small JVM program (Suwayomi's Android
compatibility layer and Tachiyomi source API, vendored) and :class:`ExtensionHostClient` talks to it over its local
JSON API. Prototype scope (owner, 2026-10-21): the host and this client only - search, chapters, pages and images;
nothing in MangaList uses it yet.
"""

from __future__ import annotations

from .client import (
    DEFAULT_URL,
    Chapter,
    Extension,
    ExtensionHostClient,
    ExtensionHostError,
    HostUnreachable,
    Manga,
    MangasPage,
    NotFound,
    Page,
    RepoExtension,
    Source,
    SourceFailed,
)

__all__ = [
    "DEFAULT_URL", "Chapter", "Extension", "ExtensionHostClient", "ExtensionHostError", "HostUnreachable", "Manga",
    "MangasPage", "NotFound", "Page", "RepoExtension", "Source", "SourceFailed",
]

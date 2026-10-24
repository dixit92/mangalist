"""MangaList's own Mihon extension host (prototype branches ``prototype/mihon-extensions`` / ``-mangalist``; no Qt).

Runs Mihon / Tachiyomi extensions without Suwayomi: ``extension-host/`` is a small JVM program (Suwayomi's Android
compatibility layer and Tachiyomi source API, vendored). :class:`ExtensionHostClient` talks to its own JSON API (the
repository, installing / updating / removing extensions, the sources and their settings, search / chapters / pages);
:class:`~.compat.ExtensionsChapterClient` (``compat.py``) is MangaList's chapter client over its Suwayomi-compatible
``/compat`` API - what the "Built-in extensions" chapter backend uses (:mod:`mangalist.downloads.chapter_backend`).
:mod:`.supervisor` starts and watches the host in the container. :mod:`.carry_over` carries the owner's per-series Suwayomi
choices over to the host once (Settings, or ``python -m mangalist.services.extensions carry-over``).
"""

from __future__ import annotations

from .client import (
    DEFAULT_URL,
    Busy,
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
    Repository,
    Source,
    SourceFailed,
    SourcePreference,
)

__all__ = [
    "DEFAULT_URL", "Busy", "Chapter", "Extension", "ExtensionHostClient", "ExtensionHostError", "HostUnreachable", "Manga",
    "MangasPage", "NotFound", "Page", "RepoExtension", "Repository", "Source", "SourceFailed", "SourcePreference",
]

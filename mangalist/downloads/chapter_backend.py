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


def is_builtin(client) -> bool:
    """The client is the built-in extension host's (:class:`~mangalist.services.extensions.compat.ExtensionsChapterClient`)."""
    return getattr(client, "backend", "") == CHAPTER_BACKEND_EXTENSIONS


def backend_of(client) -> str:
    """The program *client* asks (:data:`CHAPTER_BACKEND_EXTENSIONS` for the host's, else Suwayomi's): what a record or a
    choice made from its answers belongs to - never the setting at the time of writing, which a switch may have changed
    meanwhile (transition audit B5)."""
    return CHAPTER_BACKEND_EXTENSIONS if is_builtin(client) else CHAPTER_BACKEND_SUWAYOMI


def name_of_backend(backend: Optional[str]) -> str:
    """"Suwayomi" / "the extension host" for a record's or a choice's program ('' / unknown: Suwayomi)."""
    return NAMES.get(backend or CHAPTER_BACKEND_SUWAYOMI, "Suwayomi")


def record_extra(client) -> dict:
    """What a chapter record keeps about the program's database besides its ids: the extension host's database id
    (``host_instance``, transition audit B4) when the host reports one."""
    instance = host_instance(client)
    return {"host_instance": instance} if instance else {}


def host_instance(client) -> str:
    """The id of the extension host's database *client* talks to ('' for Suwayomi, or a host too old to report one)."""
    if not is_builtin(client):
        return ""
    fn = getattr(client, "instance", None)
    if not callable(fn):
        return ""
    try:
        return str(fn() or "")
    except Exception:  # noqa: BLE001 - asked again at the next call; nothing is compared without it
        return ""


_WORDING = (("Settings > Connected services > Suwayomi", "Settings > Connected services > Built-in extensions"),
            ("Suwayomi's settings", "the extension host's settings"), ("Suwayomi's", "the extension host's"),
            ("Suwayomi", "the extension host"))


def wording(text: Optional[str], builtin: bool) -> Optional[str]:
    """A message written for Suwayomi, worded for the program that answers: unchanged for Suwayomi; for the built-in
    extensions "Suwayomi" becomes "the extension host" (capitalized at the start of a sentence)."""
    if not text or not builtin or "Suwayomi" not in text:
        return text
    for old, new in _WORDING:
        text = text.replace(old, new)
    text = text[:1].upper() + text[1:] if text.startswith("the extension host") else text
    return text.replace(". the extension host", ". The extension host")


def not_set_up(builtin: bool) -> str:
    """The sentence for "no chapter client can be built" (where the owner sets it up)."""
    if builtin:
        return "the extension host is not set up yet (Settings > Connected services > Built-in extensions)"
    return "Suwayomi is not set up yet (Settings > Connected services > Suwayomi)"

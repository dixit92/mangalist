"""Which extension host database MangaList's stored host ids belong to (transition audit B4, 2026-10-24). No Qt.

The built-in extension host numbers mangas and chapters in its own database (``<data>/extension-host/host.db``; ids
from 1, never reused). MangaList keeps those ids: each series' choice (``extensions.series.<id>``: the host's manga)
and each chapter download (``external_ref``: the host's chapter). If that database is deleted, lost or restored from
another day, the same numbers name OTHER mangas and chapters - and nothing would notice: a series would be offered,
sent and filed chapters of another manga on the same source.

So the host keeps a random id of its database (made when the database is made; ``GET /compat/about`` -> ``instance``)
and MangaList remembers the one its ids belong to (``meta`` ``extensions.host_instance``; a choice and a record also
carry the id they were made with, ``host_instance``). :func:`check` compares them whenever MangaList starts working with
the host (the downloads job, the find pass, the chapter check, the chapters panel, Test connection):

- the first id ever seen is adopted (the ids stored before this check existed were made with that database);
- the same id: nothing to do;
- another id: the database was replaced. Every host choice made with another database forgets its manga id - it keeps
  the source, the manga's url, the title, how it was found and the group, so the next lookup finds the manga again on
  its own (by the MangaDex id, or by registering the url with the host: ``POST /compat/manga``) without asking the owner.
  Every chapter download still on its way with the host (sent, downloaded) is FAILED - its chapter id names another
  chapter now - and one filed but not yet deleted from the host's folder is marked done without asking the host to
  delete anything (that id names another chapter too: its file stays in the host's folder). Logged as a WARNING.

A host too old to report an id is never compared (nothing changes).
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from ..store.downloads import META_HOST_INSTANCE, DownloadLedger, StatusConflict, SuwayomiSettings
from .chapter_backend import host_instance
from .contracts import CHAPTER_BACKEND_EXTENSIONS, TOOL_SUWAYOMI, DownloadStatus

_log = logging.getLogger(__name__)

RESET_NOTE = ("the extension host's database changed since this was sent (it was reset or restored): its chapter id "
              "names another chapter now; send it again")
FILED_NOTE = ("filed; the extension host's copy was not deleted: its database changed since (it was reset or "
              "restored), so its chapter id names another chapter now - delete the file from its folder by hand")


def known(db) -> str:
    return db.get_meta(META_HOST_INSTANCE) or ""


def check(db, client) -> Optional[str]:
    """Compare the host *client* talks to with the database MangaList's host ids belong to (see the module docstring).
    Returns what was done when the database changed, else None. Never raises for a host that does not answer."""
    current = host_instance(client)
    if not current:
        return None
    before = known(db)
    if not before:
        db.set_meta(META_HOST_INSTANCE, current)
        _log.info("Extension host: its database is %s (the stored host ids belong to it)", current)
        return None
    if before == current:
        return None
    choices = _forget_choices(db, before, current)
    failed, settled = _settle_records(db, before, current)
    db.set_meta(META_HOST_INSTANCE, current)
    text = (f"The extension host's database changed ({before} -> {current}: reset or restored): {choices} series "
            f"choice(s) will find their manga again, {failed} chapter download(s) failed, {settled} filed one(s) "
            "kept without deleting the host's copy")
    _log.warning("Extension host: %s", text)
    return text


def _stale(stamp: object, before: str, current: str) -> bool:
    """A host id made with another database than *current* (no stamp: made with *before*)."""
    return (str(stamp) if stamp else before) != current


def _forget_choices(db, before: str, current: str) -> int:
    settings = SuwayomiSettings(db)
    n = 0
    for sid, choice in settings.extensions_series_choices().items():
        if choice.get("manga_id") is None or not _stale(choice.get("host_instance"), before, current):
            continue
        kept = {k: v for k, v in choice.items() if k not in ("manga_id", "host_instance")}
        kept["stale_manga_id"] = {"manga_id": choice.get("manga_id"), "host_instance": choice.get("host_instance")
                                  or before}
        db.set_meta(f"extensions.series.{int(sid)}", json.dumps(kept, ensure_ascii=False))
        n += 1
    return n


def _settle_records(db, before: str, current: str):
    ledger = DownloadLedger(db, tool=TOOL_SUWAYOMI)
    failed = settled = 0
    for rec in ledger.active((CHAPTER_BACKEND_EXTENSIONS,)):
        if not _stale(ledger.request(rec.id).get("host_instance"), before, current):
            continue
        try:
            if rec.status == DownloadStatus.FILED:
                ledger.set_status(rec.id, DownloadStatus.REMOVED, expect=(DownloadStatus.FILED,), error=FILED_NOTE)
                settled += 1
            else:
                ledger.set_status(rec.id, DownloadStatus.FAILED, expect=(rec.status,), error=RESET_NOTE)
                failed += 1
        except StatusConflict as exc:
            _log.info("Extension host: %s", exc)
    return failed, settled


__all__ = ["FILED_NOTE", "RESET_NOTE", "check", "known"]

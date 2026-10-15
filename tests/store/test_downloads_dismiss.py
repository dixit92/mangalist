"""Dismissing a FAILED download record (the In progress list's "Dismiss", owner 2026-10-15): marked cancelled with the
failure kept readable, only from FAILED; ``cancel`` still refuses a failed record. Made-up names only."""

from __future__ import annotations

import pytest

from mangalist.downloads.contracts import DownloadStatus as S
from mangalist.store.downloads import DownloadLedger, StatusConflict


def _record(db, status, tool="qbittorrent", error=None):
    with db.connect() as con:
        cur = con.execute("INSERT INTO ledger (created_at, updated_at, series_id, request, tool, status, error,"
                          " external_ref) VALUES ('2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z', NULL, '{}', ?, ?, ?,"
                          " 'abc')", (tool, status, error))
        return int(cur.lastrowid)


@pytest.mark.parametrize("tool", ["qbittorrent", "suwayomi"])
def test_a_failed_record_is_dismissed_as_cancelled(db, tool):
    rid = _record(db, S.FAILED, tool, error="already in the folder")
    ledger = DownloadLedger(db, tool=None)
    with pytest.raises(StatusConflict):
        ledger.cancel(rid)                                          # cancel keeps refusing a failed record
    rec = ledger.dismiss(rid)
    assert rec.status == S.CANCELLED and rec.error == "dismissed by the owner (it had failed: already in the folder)"


@pytest.mark.parametrize("status", [S.QUEUED, S.SENT, S.DOWNLOADED, S.FILED, S.REMOVED, S.CANCELLED])
def test_only_a_failed_record_can_be_dismissed(db, status):
    rid = _record(db, status)
    with pytest.raises(StatusConflict):
        DownloadLedger(db, tool=None).dismiss(rid)
    assert DownloadLedger(db, tool=None).get(rid).status == status


def test_the_backend_dismisses_failed_records_of_every_tool_and_leaves_the_rest(db):
    from mangalist.downloads.adapter import Backend

    torrent, chapter = _record(db, S.FAILED), _record(db, S.FAILED, "suwayomi")
    running = _record(db, S.SENT)
    assert Backend(db).dismiss_downloads([torrent, chapter, running]) == 2
    ledger = DownloadLedger(db, tool=None)
    assert [ledger.get(i).status for i in (torrent, chapter, running)] == [S.CANCELLED, S.CANCELLED, S.SENT]

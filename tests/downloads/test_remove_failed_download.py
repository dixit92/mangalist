"""Remove from qBittorrent for a FAILED torrent download (owner, 2026-10-24: a failed torrent stayed in qBittorrent for a
week): the torrent is deleted - with its files or not - and the record is cancelled; nothing else. Made-up names only."""

from __future__ import annotations

import pytest

from mangalist.downloads.adapter import Backend, BackendError
from mangalist.downloads.contracts import DownloadStatus as S, QbtConnection
from mangalist.store.downloads import DownloadLedger

from .fakes import FakeQbt

HASH = "c" * 40


def _record(db, status, tool="qbittorrent", ref=HASH):
    with db.connect() as con:
        cur = con.execute("INSERT INTO ledger (created_at, updated_at, series_id, request, tool, status, error,"
                          " external_ref) VALUES ('2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z', NULL,"
                          " '{\"title\": \"Made Up 001-158\"}', ?, ?, 'nothing filed', ?)", (tool, status, ref))
        return int(cur.lastrowid)


def _backend(db, qbt):
    b = Backend(db, client_factory=lambda conn: qbt)
    b.ledger.save_connection(QbtConnection("http://qbt.example:8080", "owner", "pw"))
    return b


@pytest.mark.parametrize("files", [True, False])
def test_a_failed_torrent_is_removed_and_its_record_cancelled(db, tmp_path, files):
    qbt = FakeQbt(tmp_path / "dl")
    info = qbt.put(HASH, "Made Up 001-158", {"Made Up 001.cbz": b"x" * 10})
    rid = _record(db, S.FAILED)
    rec = _backend(db, qbt).remove_failed_download(rid, delete_files=files)
    assert qbt.calls[-1] == ("delete", HASH, files)
    assert rec.status == S.CANCELLED and rec.error.startswith("removed from qBittorrent by the owner")
    assert ("with its files" in rec.error) is files and "nothing filed" in rec.error
    assert (tmp_path / "dl" / "Made Up 001-158").exists() is (not files)
    assert info.info_hash not in qbt.infos


def test_a_torrent_already_gone_only_cancels_the_record(db, tmp_path):
    from mangalist.services.qbittorrent import TorrentNotFound

    class Gone(FakeQbt):
        def delete(self, info_hash, *, delete_files):
            raise TorrentNotFound("qBittorrent has no torrent with that info hash")

    rid = _record(db, S.FAILED)
    assert _backend(db, Gone(tmp_path)).remove_failed_download(rid, delete_files=True).status == S.CANCELLED


@pytest.mark.parametrize("status,tool", [(S.SENT, "qbittorrent"), (S.FILED, "qbittorrent"), (S.FAILED, "suwayomi")])
def test_only_a_failed_torrent_download_can_be_removed(db, tmp_path, status, tool):
    qbt = FakeQbt(tmp_path)
    rid = _record(db, status, tool)
    with pytest.raises(BackendError):
        _backend(db, qbt).remove_failed_download(rid, delete_files=True)
    assert qbt.deleted == [] and DownloadLedger(db, tool=None).get(rid).status == status

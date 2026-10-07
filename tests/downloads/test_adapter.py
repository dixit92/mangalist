"""The volumes GUI's real backend (mangalist.downloads.adapter) over the store, the ledger and a FAKE qBittorrent."""

from __future__ import annotations

import pytest

from mangalist.downloads.adapter import Backend
from mangalist.downloads.contracts import DownloadStatus, QbtConnection
from mangalist.gui.downloads_backend import BackendError, QbtSettings
from mangalist.services.nyaa import NyaaError
from mangalist.services.qbittorrent import AuthFailed

from .fakes import candidate


class _Search:
    def __init__(self, fail=False):
        self.fail, self.calls = fail, []

    def search(self, titles, missing, held):
        self.calls.append((tuple(titles), tuple(missing), tuple(held)))
        if self.fail:
            raise NyaaError("nyaa.si did not answer")
        return [candidate()]


def _backend(db, qbt, **kw):
    seen = []

    def factory(conn):
        seen.append(conn)
        return qbt
    b = Backend(db, client_factory=factory, **kw)
    b.seen = seen
    return b


def test_series_lookup_and_placement(db, series, qbt):
    sid, sdir = series
    b = _backend(db, qbt)
    assert b.series_id_for(str(sdir)) == sid
    assert b.series_id_for(str(sdir.parent / "Not scanned")) is None
    assert b.placement(sid).target_dir == str(sdir)
    with pytest.raises(BackendError):
        b.placement(999)


def test_search_passes_through_and_wraps_errors(db, qbt):
    s = _Search()
    assert _backend(db, qbt, search=s).search(["Series A"], ["2"], ["1"])[0].info_hash == candidate().info_hash
    assert s.calls == [(("Series A",), ("2",), ("1",))]
    with pytest.raises(BackendError, match="nyaa"):
        _backend(db, qbt, search=_Search(fail=True)).search(["x"], [], [])


def test_settings_round_trip_keeps_the_password_write_only(db, qbt):
    b = _backend(db, qbt)
    assert b.load_settings() == QbtSettings()
    b.save_settings(QbtSettings(base_url="box:8080", username="admin", save_path="/data/t/ml", remove_completed=False),
                    "s3cret")
    got = b.load_settings()
    assert (got.base_url, got.username, got.has_password, got.save_path, got.remove_completed) == \
        ("http://box:8080", "admin", True, "/data/t/ml", False)
    b.save_settings(got, None)                       # empty password field: keep the stored one
    assert b.ledger.connection().password == "s3cret"
    assert "s3cret" not in repr(got)


def test_bad_address_and_failed_login_are_readable(db, qbt):
    b = _backend(db, qbt)
    with pytest.raises(BackendError, match="user name or password in the address"):
        b.save_settings(QbtSettings(base_url="http://u:p@box:8080"), "x")

    def refuse(conn):
        raise AuthFailed("qBittorrent refused the user name or password")
    with pytest.raises(BackendError, match="refused"):
        Backend(db, client_factory=refuse).test_connection(QbtSettings(base_url="box:8080"), "x")


def test_connection_test_uses_the_stored_password_when_none_typed(db, qbt):
    b = _backend(db, qbt)
    b.save_settings(QbtSettings(base_url="box:8080", username="admin"), "s3cret")
    assert b.test_connection(b.load_settings(), "") == qbt.version()
    assert b.seen[-1] == QbtConnection("http://box:8080", "admin", "s3cret", True)


def test_send_needs_a_connection_then_records(db, series, qbt, tmp_path):
    sid, sdir = series
    b = _backend(db, qbt)
    with pytest.raises(BackendError, match="not set up"):
        b.send(sid, candidate(), ["2"], str(sdir))
    b.save_settings(QbtSettings(base_url="box:8080", username="admin", save_path=str(tmp_path / "torrents")), "pw")
    rec = b.send(sid, candidate(), ["2"], str(sdir))
    assert rec.status == DownloadStatus.SENT and rec.target_dir == str(sdir)
    assert [r.id for r in b.records(sid)] == [rec.id] == [r.id for r in b.records()]
    with pytest.raises(BackendError, match="already"):
        b.send(sid, candidate(), ["2"], str(sdir))
    with pytest.raises(BackendError, match="inside it"):
        b.send(sid, candidate(info_hash="cd" * 20), ["2"], str(tmp_path))


def test_the_gui_finds_this_adapter(monkeypatch, db):
    from mangalist.gui import downloads_backend

    monkeypatch.setenv("MANGALIST_DOWNLOADS", "1")
    assert isinstance(downloads_backend.create_backend(db), Backend)
    monkeypatch.setenv("MANGALIST_DOWNLOADS", "0")
    assert downloads_backend.create_backend(db) is None

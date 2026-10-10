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


def test_check_now_runs_the_downloads_job_once(db, series, qbt, tmp_path):
    from mangalist.downloads.contracts import DownloadStatus

    sid, sdir = series
    b = _backend(db, qbt)
    assert b.check_now() == "no downloads in progress"
    b.save_settings(QbtSettings(base_url="box:8080", username="admin", save_path=str(tmp_path / "torrents")), "pw")
    rec = b.send(sid, candidate(), ["2"], str(sdir))
    qbt.put(rec.info_hash, "Series A v02 (Digital)", {"Series A v02 (Digital).cbz": b"v02" * 400}, state="uploading")
    assert b.check_now().startswith("torrents: 1 checked: 1 filed")
    assert b.ledger.get(rec.id).status == DownloadStatus.FILED and (sdir / "Series A v02 (Digital).cbz").exists()


# --- the Settings dialog's switches and the Download tab's extras -----------------------------------------


class _Nyaa:
    """A nyaa client with a canned feed per category (what ``rss`` is asked for is recorded)."""

    def __init__(self, feeds):
        self.feeds, self.asked = feeds, []

    def rss(self, query, category="3_1", filter_=0):
        self.asked.append((query, category))
        return list(self.feeds.get(category, []))


def _item(title, category="3_1", seeders=5, trusted=False):
    from mangalist.services.nyaa.rss import RssItem

    h = f"{abs(hash(title)):040x}"[:40]
    return RssItem(title=title, torrent_url=f"https://nyaa.example/dl/{h[:6]}", view_url=f"https://nyaa.example/{h[:6]}",
                   info_hash=h, published="2026-10-01T00:00:00+00:00", seeders=seeders, category=category,
                   trusted=trusted, size_bytes=1000)


def test_nyaa_options_default_to_english_and_round_trip(db, qbt):
    from mangalist.downloads.options import NyaaOptions

    b = _backend(db, qbt)
    assert b.nyaa_options() == NyaaOptions() and NyaaOptions().categories() == ("3_1",)
    b.set_nyaa_options(NyaaOptions(english=False, raw=True, hide_light_novels=False, trusted_only=True))
    got = b.nyaa_options()
    assert (got.english, got.raw, got.hide_light_novels, got.trusted_only) == (False, True, False, True)
    assert got.digital_first and got.hide_no_seeders                      # fixed: how the search always works
    assert got.categories() == ("3_3",)
    b.set_nyaa_options(NyaaOptions(english=False, raw=False))
    assert b.nyaa_options().categories() == ("3_1",)                       # never a search of nothing


def test_search_follows_the_options_categories_and_trusted_filter(db, qbt):
    from mangalist.downloads.options import NyaaOptions

    client = _Nyaa({"3_1": [_item("Series A v02 (Digital) (G1)", "3_1", trusted=False)],
                    "3_3": [_item("Series A v03 (Digital) (G2)", "3_3", trusted=True)]})
    b = _backend(db, qbt, nyaa_client=client)
    assert [c.title for c in b.search(["Series A"], ["2", "3"], ["1"])] == ["Series A v02 (Digital) (G1)"]
    assert {cat for _q, cat in client.asked} == {"3_1"}
    b.set_nyaa_options(NyaaOptions(english=True, raw=True))
    got = b.search(["Series A"], ["2", "3"], ["1"])
    assert sorted(c.title for c in got) == ["Series A v02 (Digital) (G1)", "Series A v03 (Digital) (G2)"]
    assert {cat for _q, cat in client.asked} == {"3_1", "3_3"}
    b.set_nyaa_options(NyaaOptions(english=True, raw=True, trusted_only=True))
    assert [c.title for c in b.search(["Series A"], ["2", "3"], ["1"])] == ["Series A v03 (Digital) (G2)"]


def test_light_novels_follow_the_hide_switch(db, qbt):
    from mangalist.downloads.options import NyaaOptions

    client = _Nyaa({"3_1": [_item("Series A Light Novel v02 (EPUB)"), _item("Series A v02 (Digital)")]})
    b = _backend(db, qbt, nyaa_client=client)
    assert [c.title for c in b.search(["Series A"], ["2"], [])] == ["Series A v02 (Digital)"]
    b.set_nyaa_options(NyaaOptions(hide_light_novels=False))
    assert len(b.search(["Series A"], ["2"], [])) == 2


def test_switching_nyaa_off_stops_the_search_with_a_readable_reason(db, qbt):
    from mangalist.downloads.options import NyaaOptions

    s = _Search()
    b = _backend(db, qbt, search=s)
    b.set_nyaa_options(NyaaOptions(enabled=False))
    with pytest.raises(BackendError, match="switched off"):
        b.search(["Series A"], ["2"], [])
    assert s.calls == []


def test_remove_completed_series_titles_and_next_check(db, series, qbt):
    import json

    sid, sdir = series
    b = _backend(db, qbt)
    assert b.load_settings().remove_completed is True
    b.set_remove_completed(False)
    assert b.load_settings().remove_completed is False
    assert b.series_titles([sid, 999]) == {sid: "Series A"} and b.series_titles([]) == {}

    from mangalist import paths

    state = paths.data_dir() / "headless-state.json"
    assert b.next_check() is None                                          # no scheduler state here
    state.write_text(json.dumps({"version": 1, "jobs": {"downloads": {"next_run": "2026-10-08T11:21:00+00:00"}}}),
                     encoding="utf-8")
    assert b.next_check() == "2026-10-08T11:21:00+00:00"
    state.write_text("{not json", encoding="utf-8")
    assert b.next_check() is None


# --- the download budget ------------------------------------------------------------------------------------


def test_send_queues_over_the_cap_and_the_overrides(db, series, qbt, tmp_path):
    from mangalist.downloads.budget import GB

    sid, sdir = series
    b = _backend(db, qbt)
    b.save_settings(QbtSettings(base_url="box:8080", username="admin", save_path=str(tmp_path / "torrents")), "pw")
    assert b.budget_gb() == 50
    b.set_budget_gb(20)
    first = b.send(sid, candidate(size_bytes=15 * GB), ["2"], str(sdir))
    second = b.send(sid, candidate(info_hash="cd" * 20, size_bytes=10 * GB), ["2"], str(sdir))
    third = b.send(sid, candidate(info_hash="ef" * 20, size_bytes=1 * GB), ["2"], str(sdir), over_cap="send")
    assert (first.status, second.status, third.status) == (DownloadStatus.SENT, DownloadStatus.QUEUED,
                                                           DownloadStatus.SENT)
    state = b.budget_status()
    assert state.usage_text() == "using 16 GB of 20 GB" and [r.id for r in state.queued] == [second.id]
    with pytest.raises(BackendError, match="already queued"):
        b.send(sid, candidate(info_hash="cd" * 20, size_bytes=10 * GB), ["2"], str(sdir))
    with pytest.raises(BackendError, match="bigger than the download budget of 20 GB"):
        b.send(sid, candidate(info_hash="12" * 20, size_bytes=21 * GB), ["2"], str(sdir))
    fourth = b.send(sid, candidate(info_hash="34" * 20, size_bytes=1 * GB), ["2"], str(sdir))
    assert b.move_to_front(fourth.id).queue_position == 1
    assert b.remove_from_queue(fourth.id).status == DownloadStatus.CANCELLED
    with pytest.raises(BackendError, match="not removed"):
        b.remove_from_queue(fourth.id)
    with pytest.raises(BackendError, match="not moved"):
        b.move_to_front(first.id)
    assert b.send_queued_now(second.id).status == DownloadStatus.SENT
    with pytest.raises(BackendError, match="only a queued download"):
        b.send_queued_now(second.id)


def test_a_partial_send_counts_the_size_the_panel_gives(db, series, qbt, tmp_path):
    from mangalist.downloads.budget import GB, SIZE_SELECTED

    sid, sdir = series
    b = _backend(db, qbt)
    b.save_settings(QbtSettings(base_url="box:8080", username="admin", save_path=str(tmp_path / "torrents")), "pw")
    b.set_budget_gb(20)
    b.send(sid, candidate(size_bytes=15 * GB), ["2"], str(sdir))
    rec = b.send(sid, candidate(info_hash="cd" * 20, size_bytes=60 * GB), ["2"], str(sdir), only_missing=True,
                 size_bytes=6 * GB)                # 15 + 6 > 20: queued (never sent here)
    assert rec.status == DownloadStatus.QUEUED and (rec.size_bytes, rec.size_source) == (6 * GB, SIZE_SELECTED)
    assert b.ledger.request(rec.id)["only_missing"] is True

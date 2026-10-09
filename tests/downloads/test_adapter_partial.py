"""The backend's partial-download calls: inspect_pack (the .torrent read through the nyaa client), the Settings default,
and send(only_missing=...) with the outcome handed to the panel."""

from __future__ import annotations

import pytest

from mangalist.downloads.adapter import Backend
from mangalist.downloads.contracts import QbtConnection
from mangalist.downloads.options import KEY_PARTIAL_DOWNLOADS, get_flag
from mangalist.downloads.service import PackWait
from mangalist.gui.downloads_backend import BackendError
from mangalist.services.nyaa import NyaaError, UnexpectedResponse

from .fakes import HASH, candidate
from .torrents import MB, PartialQbt, make_torrent

URL = "https://nyaa.example/download/1.torrent"
ROOT = "Series A v01-08 (Digital)"
NAMES = [f"Series A v{n:02d} (Digital).cbz" for n in range(1, 9)]


def torrent_bytes(files=None):
    return make_torrent(ROOT, files or [(n, 10 * MB) for n in NAMES])


class FakeNyaa:
    """Stands in for NyaaClient: ``torrent(url)`` answers from a dict or raises."""

    def __init__(self, answers=None, error=None):
        self.answers, self.error, self.calls = answers or {}, error, []

    def torrent(self, url):
        self.calls.append(url)
        if self.error is not None:
            raise self.error
        return self.answers[url]


def cand(data=None, **kw):
    from mangalist.torrent_files import read_torrent

    h = read_torrent(data or torrent_bytes()).info_hash
    return candidate(info_hash=h, title=ROOT, vol_from="1", vol_to="8", torrent_url=URL, **kw)


def backend(db, nyaa, qbt=None):
    return Backend(db, nyaa_client=nyaa, client_factory=lambda conn: qbt)


# --- inspect_pack ----------------------------------------------------------------------------------------------

def test_inspect_pack_reads_the_torrent_and_chooses_the_files(db):
    data = torrent_bytes()
    nyaa = FakeNyaa({URL: data})
    sel = backend(db, nyaa).inspect_pack(cand(data), ["2", "3"])
    assert nyaa.calls == [URL] and sel.readable and sel.narrows
    assert (sel.kept_files, sel.total_files) == (2, 8) and sel.kept_bytes == 20 * MB


def test_inspect_pack_remembers_what_it_read(db):
    data = torrent_bytes()
    nyaa = FakeNyaa({URL: data})
    b = backend(db, nyaa)
    first = b.inspect_pack(cand(data), ["2", "3"])
    assert b.inspect_pack(cand(data), ["2", "3"]) is first and nyaa.calls == [URL]
    b.inspect_pack(cand(data), ["4"])                                           # other wanted volumes: read again
    assert len(nyaa.calls) == 2


def test_inspect_pack_cache_is_bounded(db):
    data = torrent_bytes()
    b = backend(db, FakeNyaa({URL: data}))
    for n in range(40):
        b.inspect_pack(cand(data), [str(n + 1)])
    assert len(b._packs) == 32


@pytest.mark.parametrize("error, text", [
    (NyaaError("nyaa.si did not answer"), "nyaa did not give the torrent file"),
    (UnexpectedResponse("nyaa refused the request (HTTP 404)", 404), "HTTP 404"),
])
def test_inspect_pack_turns_a_nyaa_failure_into_a_problem(db, error, text):
    sel = backend(db, FakeNyaa(error=error)).inspect_pack(cand(), ["2"])
    assert not sel.readable and text in sel.problem and not sel.narrows


def test_inspect_pack_failures_are_not_remembered(db):
    nyaa = FakeNyaa(error=NyaaError("down"))
    b = backend(db, nyaa)
    b.inspect_pack(cand(), ["2"])
    nyaa.error, nyaa.answers = None, {URL: torrent_bytes()}
    assert b.inspect_pack(cand(), ["2"]).readable


def test_inspect_pack_with_unreadable_bytes_is_a_problem(db):
    sel = backend(db, FakeNyaa({URL: b"d4:infod4:name1:aee"})).inspect_pack(cand(), ["2"])
    assert not sel.readable and "could not be read" in sel.problem


def test_inspect_pack_refuses_a_torrent_that_is_not_the_listed_release(db):
    other = make_torrent("Another Release", [("Series A v02.cbz", 1)])
    sel = backend(db, FakeNyaa({URL: other})).inspect_pack(cand(), ["2"])
    assert not sel.readable and "not the release that was listed" in sel.problem


def test_inspect_pack_of_a_magnet_only_release_does_not_ask_nyaa(db):
    nyaa = FakeNyaa()
    sel = backend(db, nyaa).inspect_pack(candidate(torrent_url="", vol_from="1"), ["2"])
    assert nyaa.calls == [] and not sel.readable and "magnet" in sel.problem


def test_inspect_pack_uses_the_one_politeness_client(db, monkeypatch):
    made = []

    class Made(FakeNyaa):
        def __init__(self):
            super().__init__({URL: torrent_bytes()})
            made.append(self)

    monkeypatch.setattr("mangalist.downloads.adapter.NyaaClient", Made)
    b = Backend(db)
    b.inspect_pack(cand(), ["2"])
    b.inspect_pack(cand(), ["5"])
    assert len(made) == 1 and len(made[0].calls) == 2                            # one client, so one pace


def test_the_file_list_never_reaches_a_log_line_with_a_link(db, caplog):
    import logging

    with caplog.at_level(logging.INFO):
        backend(db, FakeNyaa({URL: torrent_bytes()})).inspect_pack(cand(), ["2"])
    text = caplog.text
    assert "keep Series A v02" in text or "keep " + ROOT in text
    assert "https://" not in text and URL not in text


# --- the Settings default ---------------------------------------------------------------------------------------

def test_the_default_is_on_and_can_be_changed(db):
    b = backend(db, FakeNyaa())
    assert b.partial_default() is True and get_flag(db, KEY_PARTIAL_DOWNLOADS) is True
    b.set_partial_default(False)
    assert b.partial_default() is False and backend(db, FakeNyaa()).partial_default() is False
    b.set_partial_default(True)
    assert b.partial_default() is True


# --- send ---------------------------------------------------------------------------------------------------------

@pytest.fixture
def wired(db, series, tmp_path, monkeypatch):
    save = tmp_path / "torrents" / "mangalist"
    save.mkdir(parents=True)
    q = PartialQbt(save)
    q.offer(URL, cand().info_hash, ROOT, [(n, 10 * MB) for n in NAMES])
    b = backend(db, FakeNyaa({URL: torrent_bytes()}), q)
    b.ledger.save_connection(QbtConnection("http://qbt.example:8080", "owner", "pw"))
    b.ledger.set_save_path(str(save))
    monkeypatch.setattr("mangalist.downloads.service.PackWait", lambda: PackWait(timeout=2, interval=0.01))
    sid, sdir = series
    return b, q, sid, str(sdir)


def test_send_without_the_flag_is_the_whole_pack_as_before(wired):
    b, q, sid, sdir = wired
    rec = b.send(sid, cand(), ["2", "3"], sdir)
    assert q.calls[-1][0] == "add" and q.calls[-1][3] is False and q.calls_named("set_file_priority") == []
    assert b.take_pack_outcome(rec.info_hash) is None and rec.status == "sent"


def test_send_only_missing_does_the_partial_flow_and_keeps_the_outcome_once(wired):
    b, q, sid, sdir = wired
    rec = b.send(sid, cand(), ["2", "3"], sdir, only_missing=True)
    assert [c[0] for c in q.calls] == ["ensure_category", "add", "set_file_priority", "start"]
    outcome = b.take_pack_outcome(rec.info_hash.upper())
    assert outcome.partial and outcome.selection.kept_files == 2
    assert b.take_pack_outcome(rec.info_hash) is None                              # handed over once


def test_a_partial_setup_failure_is_a_readable_backend_error(wired):
    b, q, sid, sdir = wired
    q.fail["set_file_priority"] = RuntimeError("qBittorrent refused filePrio")
    with pytest.raises(BackendError, match="refused filePrio.*not started"):
        b.send(sid, cand(), ["2", "3"], sdir, only_missing=True)
    assert q.calls_named("start") == [] and b.ledger.active() == []
    assert b.take_pack_outcome(cand().info_hash) is None

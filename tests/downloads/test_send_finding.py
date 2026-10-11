"""Sending one waiting finding later (mangalist.downloads.finder.send_finding, the backend's ``send_finding``): what is
sent, and what is refused with a readable reason. Fakes only, made-up names."""

from __future__ import annotations

import pytest

from mangalist.downloads.contracts import DownloadStatus
from mangalist.downloads.finder import run_find
from mangalist.downloads.finding import TRIGGER_NOW
from mangalist.downloads.service import PackWait
from mangalist.store.findings import FindingStore

from .finder_fakes import pack_release, single_release, wanted, wire
from .suwayomi_fakes import MANGA, MANGADEX, chapter

NAMES = [f"Series A v{n:02d} (Digital).cbz" for n in range(1, 9)]


@pytest.fixture(autouse=True)
def quick_packs(monkeypatch):
    monkeypatch.setattr("mangalist.downloads.service.PackWait", lambda: PackWait(timeout=2, interval=0.01))


@pytest.fixture
def w(db, series, tmp_path):
    return wire(db, series, tmp_path, suwayomi=True)


def found(db, w):
    run_find(db, trigger=TRIGGER_NOW, wanted=w.wanted, backend=w.backend)
    (f,) = FindingStore(db).list()
    return f


def status(db, f):
    return FindingStore(db).get(f.id).status


def test_a_waiting_release_is_sent_and_marked_sent(db, w):
    w.search.results = [single_release(1, w.qbt, "2")]
    w.entries = wanted(w.series_id, w.folder, volumes=["2"])
    f = found(db, w)
    text = w.backend.send_finding(f.id)
    (rec,) = w.backend.ledger.all_records()
    assert text == f"Sent to qBittorrent: {w.search.results[0].title}."
    assert FindingStore(db).get(f.id).record_id == rec.id and status(db, f) == "sent"


def test_a_waiting_pack_is_sent_narrowed_for_what_is_still_missing(db, w):
    w.search.results = [pack_release(1, w.nyaa, w.qbt, NAMES, "1", "8", ("2", "3"))]
    w.entries = wanted(w.series_id, w.folder, volumes=["2", "3"])
    f = found(db, w)
    w.entries = wanted(w.series_id, w.folder, volumes=["3"])                         # 2 arrived another way
    w.backend.send_finding(f.id)
    (rec,) = w.backend.ledger.all_records()
    assert rec.wanted_volumes == ("3",) and status(db, f) == "sent"
    assert [c[3] for c in w.qbt.calls if c[0] == "set_file_priority"] == [0]


def test_a_filled_gap_is_refused_and_the_finding_is_gone(db, w):
    w.search.results = [single_release(1, w.qbt, "2")]
    w.entries = wanted(w.series_id, w.folder, volumes=["2"])
    f = found(db, w)
    w.entries = []
    assert w.backend.send_finding(f.id) == "Not sent: the gap is filled (vol. 2 no longer missing)."
    assert status(db, f) == "gone" and w.backend.ledger.all_records() == []


def test_a_release_that_is_gone_is_refused_and_dropped(db, w):
    from mangalist.services.qbittorrent import TorrentRejected

    w.search.results = [single_release(1, w.qbt, "2")]
    w.entries = wanted(w.series_id, w.folder, volumes=["2"])
    f = found(db, w)
    w.qbt.fail["add"] = TorrentRejected("the link is not a valid torrent")
    text = w.backend.send_finding(f.id)
    assert text.startswith("Not sent: the release is gone") and status(db, f) == "gone"


def test_qbittorrent_down_keeps_the_finding_waiting(db, w):
    w.search.results = [single_release(1, w.qbt, "2")]
    w.entries = wanted(w.series_id, w.folder, volumes=["2"])
    f = found(db, w)
    w.qbt.fail["add"] = ConnectionError("connection refused")
    assert w.backend.send_finding(f.id) == "Not sent: qBittorrent could not be reached (ConnectionError)."
    assert status(db, f) == "waiting"


def test_a_release_sent_meanwhile_from_the_releases_panel_is_marked_sent(db, w):
    w.search.results = [single_release(1, w.qbt, "2")]
    w.entries = wanted(w.series_id, w.folder, volumes=["2"])
    f = found(db, w)
    rec = w.backend.send(w.series_id, w.search.results[0], ["2"], str(w.folder))
    assert w.backend.send_finding(f.id) == f"Already sent: {w.search.results[0].title} (download {rec.id})."
    assert FindingStore(db).get(f.id).record_id == rec.id


def test_findings_that_are_not_waiting_or_hold_no_pick_are_refused(db, w):
    w.entries = wanted(w.series_id, w.folder, chapters=["5"])
    f = found(db, w)                                                                 # no saved source
    assert w.backend.send_finding(f.id).startswith("Not sent: no Suwayomi source is saved")
    w.backend.skip_finding(f.id)
    assert w.backend.send_finding(f.id) == "Not sent: it is skipped already."
    assert w.backend.send_finding(98765) == "Not sent: this finding no longer exists."


def test_a_dropped_source_is_refused_with_change_source(db, w):
    w.backend.suwayomi.set_series_choice(w.series_id, source_id=MANGADEX.id, manga_id=MANGA.id,
                                         manga_title=MANGA.title, how="confirmed")
    w.suwayomi.chapter_list = [chapter(101, "4"), chapter(107, "8")]
    w.entries = wanted(w.series_id, w.folder, chapters=["5"])
    f = found(db, w)
    assert "Change source" in w.backend.send_finding(f.id) and status(db, f) == "waiting"


def test_waiting_chapters_are_sent(db, w):
    w.backend.suwayomi.set_series_choice(w.series_id, source_id=MANGADEX.id, manga_id=MANGA.id,
                                         manga_title=MANGA.title, how="confirmed")
    w.suwayomi.chapter_list = [chapter(102, "5"), chapter(103, "6")]
    w.entries = wanted(w.series_id, w.folder, chapters=["5", "6"])
    f = found(db, w)
    assert w.backend.send_finding(f.id) == "Sent ch 5-6 to Suwayomi."
    assert ("enqueue", [102, 103]) in w.suwayomi.calls and status(db, f) == "sent"
    assert all(r.status == DownloadStatus.SENT for r in w.backend.chapter_ledger.all_records())


def test_chapters_that_left_the_source_are_refused_and_dropped(db, w):
    w.backend.suwayomi.set_series_choice(w.series_id, source_id=MANGADEX.id, manga_id=MANGA.id,
                                         manga_title=MANGA.title, how="confirmed")
    w.suwayomi.chapter_list = [chapter(102, "5")]
    w.entries = wanted(w.series_id, w.folder, chapters=["5"])
    f = found(db, w)
    w.suwayomi.chapter_list = [chapter(302, "5")]                                    # re-uploaded under a new id
    assert w.backend.send_finding(f.id) == "Not sent: the chapters are not on MangaDex (EN) any more."
    assert status(db, f) == "gone"

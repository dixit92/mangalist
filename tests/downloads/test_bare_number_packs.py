"""A bare-numbered release whose candidate was rebuilt without ``numbers_only`` (owner's report, 2026-10-17: a finding
recorded on 2026-10-13 - before the flag existed - for a side story whose numbers the owner had answered are chapters
held the release "<Series> 001-010 (2018-2019) (Digital)". Its stored payload had no ``numbers_only``, so the send read
the title's bare numbers as volumes, kept the files "... 001 ...cbz" and "... 002 ...cbz" as volumes 1 and 2 and
skipped the rest - chapters 1 and 2 downloaded for an "Upgrade vol. 1-2"). The release's names are read from its title
again wherever a candidate is rebuilt (a finding's payload, a queued record, the arrivals pass), so bare numbers are
never volumes because a stored record lacks the flag. Made-up names, fakes only."""

from __future__ import annotations

import pytest

from mangalist.downloads.arrivals import run_arrivals
from mangalist.downloads.contracts import DownloadStatus as S
from mangalist.downloads.finder import KIND_UPGRADES, PAYLOAD_NYAA, candidate_from, candidate_payload, run_find
from mangalist.downloads.finding import TRIGGER_NOW
from mangalist.downloads.partial import CHAPTERS_ONLY, choose_files, hint_for
from mangalist.downloads.queueing import candidate_of
from mangalist.downloads.service import PackWait
from mangalist.services.nyaa.ranking import build_candidate
from mangalist.services.nyaa.rss import RssItem
from mangalist.services.nyaa.titles import parse_title
from mangalist.store.findings import FindingStore, NewFinding

from .fakes import candidate, data
from .finder_fakes import wanted, wire
from .torrents import MB, info_hash_of, make_torrent

BARE = "Quiet Harbor - Side Days 001-010 (2018-2019) (Digital) (Group)"
NUMBERS = ["001", "002", "003", "004", "005", "006", "006.5", "007", "008", "009", "009.5", "010"]
FILES = [f"Quiet Harbor - Side Days {n} ({2018 if n < '006.5' else 2019}) (Digital) (Group).cbz" for n in NUMBERS]
URL = "https://nyaa.example/download/7.torrent"
BARE_HASH = "7" * 40


def stale(info_hash: str = BARE_HASH):
    """The release as a payload recorded before ``numbers_only`` existed rebuilds it: the flag at its default."""
    payload = candidate_payload(candidate(info_hash, title=BARE, vol_from="1", vol_to="10", torrent_url=URL,
                                          covers_missing=("1", "2")))
    payload.pop("numbers_only")
    return payload


def old_finding(store: FindingStore, series_id: int, info_hash: str = BARE_HASH):
    """The waiting "Upgrade vol. 1-2" finding an earlier pass recorded with that payload."""
    run_id = store.start_run("now")
    f, _ = store.offer(NewFinding(series_id=series_id, kind=KIND_UPGRADES, pick_key=f"nyaa:{info_hash}", title=BARE,
                                  fills=("1", "2"), size_bytes=20 * MB, reason="the series is on Notify",
                                  payload={"type": PAYLOAD_NYAA, "candidate": stale(info_hash), "wanted": ["1", "2"],
                                           "only_missing": True}), run_id=run_id)
    store.finish_run(run_id, series_searched=1, found=1, sent=0, errors=0, summary="")
    return f


@pytest.fixture(autouse=True)
def quick_packs(monkeypatch):
    monkeypatch.setattr("mangalist.downloads.service.PackWait", lambda: PackWait(timeout=2, interval=0.01))


def test_a_candidate_rebuilt_without_the_flag_does_not_read_bare_numbers_as_volumes():
    rebuilt = candidate_from(stale())
    assert rebuilt.numbers_only is False                    # what the old payload gives back
    assert hint_for(rebuilt) is None
    sel = choose_files([(f"Quiet Harbor - Side Days (Digital) (Group)/{n}", 10 * MB) for n in FILES], ["1", "2"],
                       hint_for(rebuilt))
    assert sel.chapters_only and sel.problem == CHAPTERS_ONLY and sel.kept_files == 0
    # a title that states its volumes still reads its files as volumes
    assert hint_for(candidate(title="Quiet Harbor v01-03 (Digital) (Group)", vol_from="1", vol_to="3")) == "volumes"


def test_a_finding_recorded_before_the_flag_is_not_sent_for_volumes(db, series, tmp_path):
    w = wire(db, series, tmp_path)
    w.entries = wanted(w.series_id, w.folder, upgrades=["1", "2"])
    w.qbt.offer(URL, BARE_HASH, "Quiet Harbor - Side Days (Digital) (Group)", [(n, 10 * MB) for n in FILES])
    store = FindingStore(db)
    f = old_finding(store, w.series_id)
    text = w.backend.send_finding(f.id)
    assert text.startswith("Not sent") and "chapters, not volumes" in text
    assert w.backend.ledger.all_records() == [] and "start" not in [c[0] for c in w.qbt.calls]
    assert store.get(f.id).status == "waiting"


def test_a_find_pass_offers_no_bare_number_chapter_pack_and_retires_the_stale_finding(db, series, tmp_path):
    """The series' "Upgrade vol. 1-2" (volumes held only as chapters): the bare-numbered release, ranked live, is
    uncertain; its files read as chapters, so it is not offered - and the finding of the old reading goes."""
    w = wire(db, series, tmp_path)
    w.entries = wanted(w.series_id, w.folder, upgrades=["1", "2"], held=())
    root = "Quiet Harbor - Side Days (Digital) (Group)"
    files = [(n, 10 * MB) for n in FILES]
    torrent = make_torrent(root, files)
    h = info_hash_of(torrent)
    w.nyaa.files[URL] = torrent
    w.qbt.offer(URL, h, root, files)
    item = RssItem(title=BARE, torrent_url=URL, view_url="https://nyaa.example/view/7", info_hash=h,
                   published="2026-10-01T00:00:00Z", seeders=2, category="3_1")
    live = build_candidate(item, parse_title(BARE), ["1", "2"], [])
    assert live.numbers_only and live.covers_missing == ("1", "2")
    w.search.results = [live]
    store = FindingStore(db)
    old = old_finding(store, w.series_id, h)
    run_find(db, trigger=TRIGGER_NOW, wanted=w.wanted, backend=w.backend)
    assert store.list("waiting") == [] and store.get(old.id).status == "gone"
    assert w.nyaa.calls == [URL] and w.backend.ledger.all_records() == []


def test_a_queued_bare_number_pack_is_rebuilt_without_volume_reading(ledger, series):
    sid, sdir = series
    rec = ledger.create(sid, candidate(BARE_HASH, title=BARE, vol_from="1", vol_to="10", numbers_only=True,
                                       torrent_url=URL), ["2"], str(sdir), status=S.QUEUED)
    assert hint_for(candidate_of(rec, ledger.request(rec.id))) is None


def test_arrivals_never_files_a_bare_numbered_chapter_as_a_volume(ledger, series, qbt):
    """A whole send of the release (no partial selection): the arrivals pass reads its files the same way."""
    sid, sdir = series
    rec = ledger.create(sid, candidate(BARE_HASH, title=BARE, vol_from="1", vol_to="10", numbers_only=True,
                                       torrent_url=URL), ["2", "3"], str(sdir))
    qbt.put(BARE_HASH, "Quiet Harbor - Side Days (Digital) (Group)", {n: data(n) for n in FILES})
    run_arrivals(qbt, ledger)
    rec = ledger.get(rec.id)
    assert rec.status == S.FAILED and rec.filed_files == () and "no volume number" in rec.error
    assert not (sdir / FILES[1]).exists() and not (sdir / FILES[2]).exists()

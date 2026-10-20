"""The scheduled chapter check's MangaDex gate (mangalist.downloads.chapter_gate; owner-approved 2026-10-18, "Shared
Chapter Signal", lane GT): a MangaDex series unchanged since its last successful check is not fetched; changed, unknown
and first-time series are; the kept value moves only after a successful check; MangaPixer's fresh value saves the own
request; a stale or absent one falls back to it (100 per request, every content rating); a failed request falls back to
fetching; other sources are always fetched; the manual check is never gated; a sync runs first. A fake Suwayomi, a fake
MangaDex, made-up names - no network."""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone

import pytest

from mangalist.downloads import chapter_check as cc
from mangalist.downloads import chapter_gate as gate
from mangalist.downloads import chapters as chm
from mangalist.downloads.contracts import DownloadStatus
from mangalist.services.mangadex import LatestUploads, MangaDexClient
from mangalist.services.mangapixer import open_cache
from mangalist.store.findings import FindingStore

from .fakes import data, scan
from .finder_fakes import wire
from .suwayomi_fakes import MANGA, MANGADEX, MD_ID, WEEB, chapter

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)
C1 = "00000000-0000-4000-8000-0000000d0001"
C2 = "00000000-0000-4000-8000-0000000d0002"


class FakeMangaDex:
    """MangaDex's bulk answer from a dict (uuid -> latest upload); the asked ids are kept."""

    def __init__(self, values=None, fail=False):
        self.values = dict(values or {})
        self.fail = fail
        self.calls = []

    def latest_uploads(self, ids, should_stop=None):
        ids = list(ids)
        self.calls.append(ids)
        if self.fail:
            return LatestUploads(requests=1, failed=1, error="MangaDex could not be reached (ConnectionError)")
        return LatestUploads(values={i: self.values[i] for i in ids if i in self.values},
                             requests=math.ceil(len(ids) / 100))


@pytest.fixture
def lib(db, tmp_path):
    """'Series C' holding chapters 3-4, on Auto, saved to the fake Suwayomi's MangaDex manga (url /manga/<MD_ID>)."""
    root = tmp_path / "library" / "Manga"
    folder = root / "Series C"
    folder.mkdir(parents=True)
    for n in ("3", "4"):
        (folder / f"{n.zfill(4)} [Ch. {n.zfill(4)} - Title {n} [Alpha Scans]].cbz").write_bytes(data(f"c{n}"))
    r = db.add_root(str(root), "Manga")
    scan(db)
    sid = db.get_series(r.id, "Series C").id
    w = wire(db, (sid, folder), tmp_path, suwayomi=True)
    w.suwayomi.chapter_list = [chapter(101, "3"), chapter(102, "4")]
    w.backend.suwayomi.set_series_choice(sid, source_id=MANGADEX.id, manga_id=MANGA.id, manga_title=MANGA.title,
                                         how="confirmed")
    FindingStore(db).set_mode([sid], "auto")
    return w


def check(db, w, mangadex=None, now=NOW, **kw):
    kw.setdefault("pause", 0)
    return cc.run_chapter_check(db, backend=w.backend, mangadex=mangadex if mangadex is not None else FakeMangaDex(),
                                now=now, **kw)


def fetches(w):
    return sum(1 for c in w.suwayomi.calls if c[0] == "chapters")


def enqueued(w):
    return [ids for name, *rest in w.suwayomi.calls if name == "enqueue" for ids in rest]


def link(monkeypatch, mangadex_id=MD_ID, latest=None, library="lib0manga"):
    """MangaPixer's item for every series, in *library*: its MangaDex id, and (*latest*) its
    ``mangadexLatestUpload`` block."""
    item = {"companions": {"mangadex": mangadex_id}}
    if latest is not None:
        item["companions"]["mangadexLatestUpload"] = latest
    monkeypatch.setattr(chm, "linked", lambda db, sid: chm.Linked(library, item))
    return item


def swept(db, at, library="lib0manga"):
    """MangaPixer's library sweep time as the last complete sync kept it (1.45.0: ``sweeps.mangadexLatestUpload``)."""
    open_cache(db).set_sweeps(library, {"mangadexLatestUpload": at})


# --- unchanged / changed / unknown -------------------------------------------------------------------------------

def test_an_unchanged_series_is_not_fetched(db, lib, caplog, monkeypatch):
    link(monkeypatch)                                # MangaPixer links the MangaDex id (no latest upload exported)
    md = FakeMangaDex({MD_ID: C1})
    first = check(db, lib, md)                       # first time: no value kept yet - fetched, then C1 kept
    assert fetches(lib) == 1 and (first.fetched, first.unchanged) == (1, 0)
    caplog.set_level(logging.DEBUG, logger="mangalist.downloads.chapter_check")
    second = check(db, lib, md, now=NOW + timedelta(hours=6))
    assert fetches(lib) == 1                          # not asked again
    assert (second.series_checked, second.fetched, second.unchanged, second.own_requests) == (1, 0, 1, 1)
    assert second.summary == ("1 series checked (0 fetched, 1 unchanged on MangaDex), 0 with new chapters: 0 sent, "
                              "0 waiting; MangaDex latest uploads: from MangaPixer 0, own requests 1")
    assert f"Series C: not fetched: unchanged on MangaDex since its last check (latest upload {C1}, from MangaDex)" \
        in caplog.text
    assert md.calls == [[MD_ID], [MD_ID]]


def test_a_changed_latest_upload_is_fetched_and_its_new_chapter_sent(db, lib, monkeypatch):
    link(monkeypatch)
    md = FakeMangaDex({MD_ID: C1})
    check(db, lib, md)
    md.values[MD_ID] = C2
    lib.suwayomi.chapter_list.append(chapter(103, "5"))
    report = check(db, lib, md, now=NOW + timedelta(hours=6))
    assert fetches(lib) == 2 and report.fetched == 1 and enqueued(lib) == [[103]]
    assert gate.load(db, lib.series_id).latest == C2


def test_first_time_and_unknown_are_fetched_every_run(db, lib):
    md = FakeMangaDex({})                            # MangaDex's answer lacks the id
    for hours in (0, 6, 12):
        check(db, lib, md, now=NOW + timedelta(hours=hours))
    assert fetches(lib) == 3
    kept = gate.load(db, lib.series_id)
    assert kept.mangadex_id == MD_ID and not kept.known      # the manga learned from Suwayomi's answer, no value


def test_a_series_whose_mangadex_id_is_not_known_yet_is_fetched_and_learned_from_suwayomis_answer(db, lib):
    md = FakeMangaDex({MD_ID: C1})
    check(db, lib, md)                               # no MangaPixer link, nothing kept: nothing asked
    assert md.calls == [] and fetches(lib) == 1 and gate.load(db, lib.series_id).mangadex_id == MD_ID
    check(db, lib, md, now=NOW + timedelta(hours=6))         # now asked; the value is new: fetched, then kept
    check(db, lib, md, now=NOW + timedelta(hours=12))        # unchanged
    assert md.calls == [[MD_ID], [MD_ID]] and fetches(lib) == 2


def test_the_kept_value_moves_only_after_a_successful_check(db, lib, monkeypatch):
    link(monkeypatch)
    md = FakeMangaDex({MD_ID: C1})
    check(db, lib, md)
    md.values[MD_ID] = C2
    real = lib.suwayomi.chapters

    def broken(manga_id):
        lib.suwayomi.calls.append(("chapters",))
        raise RuntimeError("the source answered 503")

    lib.suwayomi.chapters = broken                   # the source fails: not kept, tried next time
    failed = check(db, lib, md, now=NOW + timedelta(hours=6))
    assert failed.unreadable == 1 and gate.load(db, lib.series_id).latest == C1
    lib.suwayomi.chapters = real
    lib.suwayomi.chapter_list.append(chapter(103, "5"))
    lib.suwayomi.enqueue_error = RuntimeError("Suwayomi refused")      # the send fails: not kept either
    sent_failed = check(db, lib, md, now=NOW + timedelta(hours=12))
    assert sent_failed.errors == 1 and gate.load(db, lib.series_id).latest == C1
    lib.suwayomi.enqueue_error = None
    check(db, lib, md, now=NOW + timedelta(hours=18))
    assert gate.load(db, lib.series_id).latest == C2 and fetches(lib) == 4


def test_mangalists_own_side_changing_makes_it_fetch_even_when_mangadex_shows_nothing_new(db, lib):
    # the gate must never hide a chapter: when MangaList's side changes (here the owner deleted ch 4, so it is "new"
    # again), the series is fetched as it would be without the gate
    md = FakeMangaDex({MD_ID: C1})
    check(db, lib, md)                               # learns the manga
    check(db, lib, md, now=NOW + timedelta(hours=1))           # value kept
    assert check(db, lib, md, now=NOW + timedelta(hours=2)).unchanged == 1 and enqueued(lib) == []
    (lib.folder / "0004 [Ch. 0004 - Title 4 [Alpha Scans]].cbz").unlink()
    scan(db)
    report = check(db, lib, md, now=NOW + timedelta(hours=3))
    assert report.unchanged == 0 and report.fetched == 1 and enqueued(lib) == [[102]]


def test_an_unchanged_series_is_fetched_anyway_after_about_a_day(db, lib):
    md = FakeMangaDex({MD_ID: C1})
    check(db, lib, md)
    check(db, lib, md, now=NOW + timedelta(hours=1))           # kept at +1 h
    check(db, lib, md, now=NOW + timedelta(hours=18))          # skipped
    assert fetches(lib) == 2
    check(db, lib, md, now=NOW + timedelta(hours=25))          # 24 h after the last fetch
    assert fetches(lib) == 3


# --- where the value comes from ----------------------------------------------------------------------------------

def test_mangapixers_fresh_value_is_used_and_no_own_request_is_made(db, lib, monkeypatch):
    link(monkeypatch, latest={"chapterId": C1, "fetchedAt": "2026-10-18T10:00:00Z"})
    md = FakeMangaDex({MD_ID: C2})
    check(db, lib, md)
    assert gate.load(db, lib.series_id).latest == C1
    report = check(db, lib, md, now=NOW + timedelta(hours=1))
    assert md.calls == [] and fetches(lib) == 1
    assert (report.unchanged, report.from_mangapixer, report.own_requests) == (1, 1, 0)
    assert "MangaDex latest uploads: from MangaPixer 1, own requests 0" in report.summary


@pytest.mark.parametrize("block", [
    {"chapterId": C1, "fetchedAt": "2026-10-18T05:00:00Z"},     # 7 h old: older than the 6 h interval
    None,                                                          # MangaPixer does not export it (yet)
    {"chapterId": 42, "fetchedAt": "2026-10-18T11:00:00Z"},      # malformed
    {"chapterId": C1, "fetchedAt": "yesterday"},
    {"fetchedAt": "2026-10-18T11:00:00Z"},
])
def test_a_stale_absent_or_malformed_mangapixer_value_falls_back_to_the_own_request(db, lib, monkeypatch, block):
    link(monkeypatch, latest=block)
    asked = []

    def get(url, params, timeout):
        asked.append((url, list(params)))

        class Resp:
            status_code = 200

            @staticmethod
            def json():
                return {"result": "ok", "data": [{"id": MD_ID, "type": "manga",
                                                  "attributes": {"latestUploadedChapter": C2}}]}

        return Resp()

    report = check(db, lib, MangaDexClient(get=get, sleep=lambda s: None), interval=timedelta(hours=6))
    assert report.from_mangapixer == 0 and report.own_requests == 1
    ((url, params),) = asked
    assert url == "https://api.mangadex.org/manga" and ("ids[]", MD_ID) in params
    assert [v for k, v in params if k == "contentRating[]"] == ["safe", "suggestive", "erotica", "pornographic"]
    assert gate.load(db, lib.series_id).latest == C2


# --- MangaPixer 1.45.0: the library sweep time ---------------------------------------------------------------

def test_an_old_value_confirmed_by_a_recent_library_sweep_is_used(db, lib, monkeypatch, caplog):
    """1.45.0: ``fetchedAt`` is when MangaPixer first saw the chapter, so an unchanged series' value grows old;
    the library's sweep time says it was confirmed since."""
    link(monkeypatch, latest={"chapterId": C1, "fetchedAt": "2026-10-10T00:00:00Z"})     # first seen 8 days ago
    swept(db, "2026-10-18T11:00:00.000Z")                                                  # confirmed 1 h ago
    md = FakeMangaDex({MD_ID: C2})
    check(db, lib, md)
    assert gate.load(db, lib.series_id).latest == C1
    with caplog.at_level(logging.INFO, logger="mangalist.downloads.chapter_gate"):
        report = check(db, lib, md, now=NOW + timedelta(hours=1))
    assert md.calls == [] and fetches(lib) == 1
    assert (report.unchanged, report.from_mangapixer, report.own_requests) == (1, 1, 0)
    assert "1 from MangaPixer (1 confirmed by its library sweep)" in caplog.text


@pytest.mark.parametrize("library, at", [
    ("lib0manga", "2026-10-18T05:00:00.000Z"),     # the sweep is 7 h old too: older than the 6 h interval
    ("lib0manga", None),                           # no complete sweep yet
    ("lib1other", "2026-10-18T11:00:00.000Z"),     # another library's sweep
    ("lib0manga", "an hour ago"),                  # malformed
])
def test_an_old_value_without_a_recent_sweep_of_its_library_falls_back_to_the_own_request(db, lib, monkeypatch,
                                                                                          library, at):
    link(monkeypatch, latest={"chapterId": C1, "fetchedAt": "2026-10-10T00:00:00Z"})
    swept(db, at, library=library)
    md = FakeMangaDex({MD_ID: C2})
    report = check(db, lib, md, interval=timedelta(hours=6))
    assert (report.from_mangapixer, report.own_requests) == (0, 1)
    assert gate.load(db, lib.series_id).latest == C2


def test_a_recent_value_is_used_without_any_sweep(db, lib, monkeypatch):
    """A chapter MangaPixer saw an hour ago is fresh on its own (the sweep that saw it may not have read the whole
    library yet)."""
    link(monkeypatch, latest={"chapterId": C1, "fetchedAt": "2026-10-18T11:00:00Z"})
    md = FakeMangaDex({MD_ID: C2})
    report = check(db, lib, md)
    assert (report.from_mangapixer, report.own_requests) == (1, 0)


def test_a_null_chapter_from_mangapixer_is_a_value_none(db, lib, monkeypatch):
    link(monkeypatch, latest={"chapterId": None, "fetchedAt": "2026-10-18T11:30:00Z"})
    check(db, lib)
    kept = gate.load(db, lib.series_id)
    assert kept.known and kept.latest is None
    assert check(db, lib, now=NOW + timedelta(minutes=30)).unchanged == 1


def test_mangapixers_value_for_another_manga_is_not_used(db, lib, monkeypatch):
    other = "00000000-0000-4000-8000-0000000e0000"
    link(monkeypatch, mangadex_id=other, latest={"chapterId": C1, "fetchedAt": "2026-10-18T11:00:00Z"})
    md = FakeMangaDex({MD_ID: C2, other: C1})
    check(db, lib, md)                               # learns MD_ID from Suwayomi's answer (MangaPixer says other)
    kept = gate.load(db, lib.series_id)
    assert kept.mangadex_id == MD_ID and not kept.known      # the value read was for the other manga: not kept
    report = check(db, lib, md, now=NOW + timedelta(hours=1))
    assert md.calls[-1] == [MD_ID] and report.from_mangapixer == 0 and gate.load(db, lib.series_id).latest == C2


def test_a_failed_own_request_falls_back_to_fetching(db, lib):
    check(db, lib, FakeMangaDex({MD_ID: C1}))
    check(db, lib, FakeMangaDex({MD_ID: C1}), now=NOW + timedelta(hours=1))
    down = FakeMangaDex(fail=True)
    report = check(db, lib, down, now=NOW + timedelta(hours=2))
    assert down.calls == [[MD_ID]] and report.fetched == 1 and report.unchanged == 0 and fetches(lib) == 3


def test_a_gate_that_cannot_be_prepared_fetches_everything(db, lib, monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(gate.Gate, "prepare", classmethod(lambda cls, *a, **k: broken()))
    for hours in (0, 6):
        assert check(db, lib, now=NOW + timedelta(hours=hours)).fetched == 1


def test_the_own_request_asks_100_series_at_a_time(db):
    from types import SimpleNamespace

    md = FakeMangaDex()
    ids = [f"00000000-0000-4000-8000-{n:012d}" for n in range(150)]
    cands = [cc.Candidate(n + 1, f"Series {n}", "auto", ids[n]) for n in range(150)]
    g = gate.Gate.prepare(db, cands, [MANGADEX], client=md, now=NOW)
    assert md.calls == [ids] and g.own_requests == 2 and len(g.planned) == 150
    real_calls = []
    MangaDexClient(get=lambda url, params, timeout: real_calls.append(params) or SimpleNamespace(
        status_code=200, json=lambda: {"result": "ok", "data": []}), sleep=lambda s: None).latest_uploads(ids)
    assert [sum(1 for k, _v in p if k == "ids[]") for p in real_calls] == [100, 50]


# --- never gated -------------------------------------------------------------------------------------------------

def test_a_series_on_another_source_is_fetched_every_run(db, lib):
    lib.backend.suwayomi.set_sources([MANGADEX.id, WEEB.id])
    lib.backend.suwayomi.set_series_choice(lib.series_id, source_id=WEEB.id)
    md = FakeMangaDex({MD_ID: C1})
    for hours in (0, 6, 12):
        check(db, lib, md, now=NOW + timedelta(hours=hours))
    assert fetches(lib) == 3 and md.calls == [] and gate.load(db, lib.series_id) is None


def test_the_manual_check_for_new_chapters_is_never_gated(db, lib):
    md = FakeMangaDex({MD_ID: C1})
    check(db, lib, md)
    check(db, lib, md, now=NOW + timedelta(hours=1))
    assert check(db, lib, md, now=NOW + timedelta(hours=2)).unchanged == 1
    before = fetches(lib)
    lookup = lib.backend.chapter_lookup(lib.series_id, (), titles=(), newer=True)
    assert fetches(lib) == before + 1 and lookup.newer and len(md.calls) == 2


# --- the sync before the check -----------------------------------------------------------------------------------

def test_the_sync_runs_before_the_check_and_its_failure_does_not_stop_it(db, lib, caplog):
    order = []
    views = cc.select_series

    def sync():
        order.append("sync")
        return "ok: 1 libraries synced"

    def selecting(db, **kw):
        order.append("select")
        return views(db, **kw)

    import mangalist.downloads.chapter_check as module

    orig = module.select_series
    module.select_series = selecting
    try:
        report = check(db, lib, sync=sync)
        assert order == ["sync", "select"] and report.synced == "ok: 1 libraries synced" and report.fetched == 1

        def failing():
            raise ConnectionError("MangaPixer is down")

        caplog.set_level(logging.INFO)
        report = check(db, lib, sync=failing, now=NOW + timedelta(hours=6))
        assert report.synced == "failed (ConnectionError)" and report.series_checked == 1
        assert "the MangaPixer sync before the check failed" in caplog.text
    finally:
        module.select_series = orig


def test_sync_mangapixer_runs_the_incremental_sync_only_when_connected(db, monkeypatch):
    from mangalist.services.mangapixer import open_cache
    from mangalist.services.mangapixer import sync as mp_sync

    calls = []

    def fake_sync_all(cache, **kw):
        calls.append(kw)
        return mp_sync.SyncResult(status="ok", message="1 libraries synced")

    monkeypatch.setattr(mp_sync, "sync_all", fake_sync_all)
    assert cc.sync_mangapixer(db) == "MangaPixer is not connected" and calls == []
    cache = open_cache(db)
    cache.set_connection(base_url="http://192.0.2.20:8080", token="t0k3n-not-real")
    assert cc.sync_mangapixer(db) == "ok: 1 libraries synced"
    assert calls[0]["manual"] is False                   # the scheduled kind: a refused token stays a stop
    cache.mark_token_rejected(True)
    assert cc.sync_mangapixer(db).startswith("MangaPixer refused the token") and len(calls) == 1


# --- MangaPixer's block ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("companions, expected", [
    ({"mangadexLatestUpload": {"chapterId": C1.upper(), "fetchedAt": "2026-10-18T11:00:00Z"}}, C1),
    ({"mangadexLatestUpload": {"chapterId": None, "fetchedAt": "2026-10-18T11:00:00+00:00"}}, None),
])
def test_mangapixer_signal_of(companions, expected):
    sig = gate.mangapixer_signal_of({"companions": companions})
    assert sig.chapter == expected and sig.fetched_at == datetime(2026, 10, 18, 11, tzinfo=timezone.utc)


@pytest.mark.parametrize("item", [None, {}, {"companions": None}, {"companions": {"mangadexLatestUpload": []}},
                                  {"companions": {"mangadexLatestUpload": {"chapterId": C1}}}])
def test_mangapixer_signal_of_absent_or_malformed_is_none(item):
    assert gate.mangapixer_signal_of(item) is None

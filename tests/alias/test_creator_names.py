"""The background lookup of the series' creators and the authors' other names (``mangalist.creator_names``) with a fake
MangaUpdates: the requests made and their pace, the cache, 404s and failures, a stop. No network, no sleeping."""

from __future__ import annotations

import datetime as dt
import json

import pytest

from mangalist import creator_names as cn

START = dt.datetime(2026, 10, 21, 12, 0, tzinfo=dt.timezone.utc)


class Db:
    """What the lookup needs of the library database: the ``meta`` values."""

    def __init__(self):
        self.meta = {}

    def get_meta(self, key):
        return self.meta.get(key)

    def set_meta(self, key, value):
        self.meta[key] = value


class NotFound(Exception):
    pass


class Fake:
    """MangaUpdates: series 1 and 2 are by Naoki Urasawa (author 7) and series 2 also by an artist without a number;
    series 3 has no authors; series 404 does not exist; series 500 fails."""

    def __init__(self):
        self.calls = []
        self.fail = set()
        self.gone = {"404"}

    def get_series(self, sid):
        self.calls.append(("series", sid))
        if str(sid) in self.gone:
            raise NotFound()
        if sid in self.fail or sid == 500:
            raise OSError("down")
        return {"1": {"authors": [{"name": "Naoki Urasawa", "author_id": 7, "type": "Author"}]},
                "2": {"authors": [{"name": "Naoki Urasawa", "author_id": 7, "type": "Author"},
                                  {"name": "Naoki Urasawa", "author_id": 7, "type": "Author"},
                                  {"name": "Free Artist", "author_id": None, "type": "Artist"},
                                  {"name": "  ", "author_id": 9, "type": "Artist"}]},
                "3": {"authors": []}}.get(str(sid), {"authors": [{"name": f"Author {sid}", "author_id": 100 + sid,
                                                                  "type": "Author"}]})

    def get_author(self, aid):
        self.calls.append(("author", aid))
        if aid in self.fail or aid == 666:
            raise OSError("down")
        if aid == 404:
            raise NotFound()
        return {"7": {"id": 7, "name": "Naoki Urasawa", "associated": [{"name": "Urasawa Naoki"}, {"name": "naoki urasawa"}],
                      "actualname": "浦沢直樹"}}.get(str(aid), {"name": f"Name {aid}"})


class Clock:
    def __init__(self):
        self.now = START
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)


def run(db, ids, fake=None, clock=None, **kw):
    fake, clock = fake or Fake(), clock or Clock()
    result = cn.fill(db, ids, get_series=fake.get_series, get_author=fake.get_author, not_found=lambda e: isinstance(e, NotFound),
                     sleep=clock.sleep, now=clock, **kw)
    return result, fake, clock


def test_the_series_credits_and_the_authors_names_are_read_and_cached():
    db = Db()
    result, fake, _ = run(db, [1, 2, 3])
    assert (result.series, result.authors, result.failed, result.stopped) == (3, 1, 0, "")
    assert fake.calls == [("series", 1), ("series", 2), ("series", 3), ("author", 7)]    # author 7 once for two series
    series, authors = cn.load_cache(db)
    assert series["1"]["a"] == [[7, "Naoki Urasawa", "author"]]
    assert series["2"]["a"] == [[7, "Naoki Urasawa", "author"], [None, "Free Artist", "artist"]]    # a repeat and a blank dropped
    assert series["3"]["a"] == []
    assert authors["7"]["n"] == ["Naoki Urasawa", "Urasawa Naoki", "浦沢直樹"]            # the main name, others, own script
    index = cn.load_index(db)
    assert [c.name for c in index.creators_of(2)] == ["Naoki Urasawa", "Free Artist"]
    assert "浦沢直樹" in index.creators_of(1)[0].names


def test_a_second_run_asks_for_nothing_new():
    db = Db()
    run(db, [1, 2, 3])
    result, fake, _ = run(db, [1, 2, 3])
    assert fake.calls == [] and result.requests == 0
    assert cn.pending(db, [1, 2, 3], START) == 0
    result, fake, _ = run(db, [1, 4])                       # a new series asks for it (and its author) alone
    assert fake.calls == [("series", 4), ("author", 104)]


def test_a_record_is_asked_again_after_180_days():
    db = Db()
    run(db, [1])
    clock = Clock()
    clock.now = START + cn.REFRESH - dt.timedelta(minutes=1)
    assert cn.pending(db, [1], clock.now) == 0
    clock.now = START + cn.REFRESH
    result, fake, _ = run(db, [1], clock=clock)
    assert fake.calls == [("series", 1), ("author", 7)]


def test_requests_keep_their_pace_and_the_first_waits_for_nothing():
    _, _, clock = run(Db(), [1, 2, 3])
    # three series at the client's pace, then the author at one a second (MangaPixer's)
    assert clock.sleeps == [0.3, 0.3, 1.0]
    _, _, clock = run(Db(), [1, 2], series_delay=0.5, author_delay=2.0)
    assert clock.sleeps == [0.5, 2.0]


def test_the_pace_is_never_faster_than_the_clients():
    from mangalist import mu_client

    assert cn.AUTHOR_DELAY >= 1.0 and cn.AUTHOR_DELAY >= mu_client.REQUEST_DELAY


def test_a_record_mangaupdates_does_not_have_is_stored_empty_and_not_asked_again():
    db = Db()
    result, fake, _ = run(db, [404, 1])
    assert result.failed == 0
    series, _authors = cn.load_cache(db)
    assert series["404"]["a"] == [] and "f" not in series["404"]
    _, fake, _ = run(db, [404, 1])
    assert fake.calls == []


def test_an_author_mangaupdates_does_not_have_is_stored_without_names():
    db = Db()
    fake = Fake()
    fake.get_series = lambda sid: {"authors": [{"name": "Gone Author", "author_id": 404, "type": "Author"}]}
    result, _, _ = run(db, [5], fake=fake)
    assert cn.load_cache(db)[1]["404"]["n"] == [] and result.authors == 1
    assert cn.pending(db, [5], START) == 0


def test_a_failed_request_is_kept_apart_and_asked_again_after_twelve_hours():
    db = Db()
    result, fake, clock = run(db, [500, 1])
    assert result.failed == 1 and result.series == 1 and result.stopped == ""         # the others still go on
    series, _ = cn.load_cache(db)
    assert series["500"]["f"] == 1 and series["1"]["a"]
    assert cn.pending(db, [500], START + cn.RETRY_FAILED - dt.timedelta(minutes=1)) == 0
    assert cn.pending(db, [500], START + cn.RETRY_FAILED) == 1
    fake.calls.clear()
    clock.now = START + cn.RETRY_FAILED
    fake.fail.add(500)
    result, fake, _ = run(db, [500], fake=fake, clock=clock)
    assert fake.calls == [("series", 500)]


def test_the_run_gives_up_after_five_failures_in_a_row_and_keeps_what_it_read():
    db = Db()
    ids = [1] + list(range(10, 30))
    fake = Fake()
    fake.fail = set(range(10, 30))
    result, fake, _ = run(db, ids, fake=fake)
    assert result.stopped == "failures" and result.failed == cn.MAX_FAILURES and result.series == 1
    assert len([c for c in fake.calls if c[0] == "series"]) == 1 + cn.MAX_FAILURES
    series, _ = cn.load_cache(db)
    assert "1" in series                                    # written before the run ended
    assert cn.pending(db, ids, START) > 0                   # the next run goes on


def test_a_success_ends_a_streak_of_failures():
    db = Db()
    fake = Fake()
    fake.fail = {10, 11, 12, 14, 15, 16}
    result, _, _ = run(db, [10, 11, 12, 13, 14, 15, 16, 17], fake=fake)
    assert result.stopped == "" and result.failed == 6


def test_a_stop_ends_the_run_before_the_next_request():
    db = Db()
    fake = Fake()
    result, _, _ = run(db, [1, 2, 3], fake=fake, should_stop=lambda: len(fake.calls) >= 2)
    assert result.stopped == "stop" and fake.calls == [("series", 1), ("series", 2)]
    assert "2" in cn.load_cache(db)[0]
    result, fake2, _ = run(db, [1, 2, 3])                    # and the next run goes on
    assert fake2.calls[0] == ("series", 3)


def test_progress_counts_the_requests_made_over_the_requests_planned():
    seen = []
    run(Db(), [1, 3], progress=lambda done, total: seen.append((done, total)))
    assert seen == [(1, 2), (2, 2), (3, 3)]        # the author is counted once the series are read


def test_the_cache_is_written_in_batches_and_at_the_end(monkeypatch):
    monkeypatch.setattr(cn, "SAVE_EVERY", 2)
    writes = []
    db = Db()
    original = cn.save_cache
    monkeypatch.setattr(cn, "save_cache", lambda d, s, a: (writes.append(len(s)), original(d, s, a)))
    run(db, [10, 11, 12, 13, 14])
    assert writes[0] == 2 and writes[-1] == 5 and len(cn.load_cache(db)[0]) == 5


def test_a_broken_cache_reads_as_empty():
    db = Db()
    db.meta[cn.KEY_SERIES] = "not json"
    db.meta[cn.KEY_AUTHORS] = json.dumps([1, 2])
    assert cn.load_cache(db) == ({}, {})
    assert not cn.load_index(db)
    result, fake, _ = run(db, [1])
    assert result.series == 1


def test_the_names_of_an_author_record():
    assert cn.names_of({"name": "A  B", "associated": [{"name": "a b"}, {"name": "C"}, "x", {}], "actualname": "漢字"}) == [
        "A B", "C", "漢字"]
    assert cn.names_of({}) == []
    many = {"name": "M", "associated": [{"name": f"n{i}"} for i in range(80)]}
    assert len(cn.names_of(many)) == cn.MAX_NAMES + 1


def test_the_credits_of_a_series_record():
    got = cn.credits_of({"authors": [{"name": "A", "author_id": "12", "type": "Author"}, {"name": "A", "type": "Author"},
                                     {"name": "A", "author_id": 12, "type": "Artist"}, None, {"author_id": 3}]})
    assert got == [[12, "A", "author"], [12, "A", "artist"]]


def test_the_cache_lives_in_the_meta_table_of_the_library_database(tmp_path, monkeypatch):
    from mangalist import paths, store

    monkeypatch.setenv(paths.ENV_DATA_DIR, str(tmp_path / "data"))
    db = store.get_store()
    run(db, [1, 2])
    assert json.loads(db.get_meta(cn.KEY_SERIES))["1"]["a"]
    assert cn.KEY_SERIES not in db.all_settings()           # not a setting: the window reads every setting at startup
    assert cn.load_index(db).known(1)

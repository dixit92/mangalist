"""Official English titles (owner, 2026-10-16, answer 76: "How does animefetch do it?"): AniList's English title of
each series, found through MangaPixer's AniList companion or the AniList record MangaDex links, cached in the database
and looked up politely in batches. Fake MangaDex / AniList answers only (no network); made-up titles and ids."""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest

from mangalist import english_titles as E

NOW = dt.datetime(2026, 10, 16, 12, 0, tzinfo=dt.timezone.utc)


def md(uuid, mu_id=None, al=None, english_alts=()):
    """A MangaDex record as :func:`mangalist.add_series.mangadex_from_record` gives it (only what the lookup reads)."""
    alts = list(english_alts)
    return SimpleNamespace(id=uuid, mu_id=mu_id, anilist_id=al, english_alts=alts,
                           english_title=alts[0] if len(alts) == 1 else None)


class Net:
    """Fake MangaDex / AniList: what they know, what they were asked, and failures on demand."""

    def __init__(self, records=(), search=None, anilist=None):
        self.records = {r.id: r for r in records}
        self.search = dict(search or {})             # mu id -> MangaDex record
        self.anilist = dict(anilist or {})           # AniList id -> English title (None: none)
        self.md_batches, self.searches, self.al_batches, self.sleeps = [], [], [], []
        self.fail_md = self.fail_al = self.fail_search = False

    def many(self, ids):
        self.md_batches.append(list(ids))
        if self.fail_md:
            raise OSError("offline")
        return {i: self.records[i] for i in ids if i in self.records}

    def find(self, mu_id, title):
        self.searches.append((mu_id, title))
        if self.fail_search:
            raise OSError("offline")
        return self.search.get(mu_id)

    def al(self, ids):
        self.al_batches.append(list(ids))
        if self.fail_al:
            raise OSError("offline")
        return {i: self.anilist[i] for i in ids if i in self.anilist}

    def fill(self, db, refs, **kw):
        return E.fill(db, refs, now=kw.pop("now", NOW), mangadex_many=self.many, mangadex_find=self.find,
                      anilist_many=self.al, sleep=self.sleeps.append, **kw)


def ref(mu=None, mdid=None, al=None, title="Made Up Romaji"):
    return E.SeriesRef.of(title, {"mu_id": mu, "mangadex_id": mdid, "anilist_id": al})


def cached(db):
    return E.EnglishTitles(db).load()


def test_keys_and_refs():
    assert E.ref_key(12, "ABC") == "mu:12" and E.ref_key("12") == "mu:12"
    assert E.ref_key(None, "ABC-def") == "md:abc-def" and E.ref_key(None, None) is None
    r = ref(mu="77", mdid="AB-CD", al="9")
    assert (r.key, r.mu_id, r.mangadex_id, r.anilist_id) == ("mu:77", 77, "ab-cd", 9)
    assert E.SeriesRef.of("Made Up", {}) is None


def test_the_english_title_comes_from_the_anilist_record_mangadex_links(db):
    net = Net([md("md-1", mu_id=1, al=101, english_alts=["A Fan Title", "The Licensed One"])],
              anilist={101: "The Licensed One"})
    report = net.fill(db, [ref(mu=1, mdid="md-1")])
    assert (report.looked, report.found, report.failed, report.changed) == (1, 1, 0, ["mu:1"])
    entry = cached(db)["mu:1"]
    assert (entry.english, entry.status, entry.anilist_id, entry.mangadex_id) == ("The Licensed One", E.FOUND, 101,
                                                                                 "md-1")
    assert entry.mangadex_english is None                         # two English titles on MangaDex: neither is kept


def test_mangapixers_anilist_companion_comes_first(db):
    net = Net([md("md-2", mu_id=2, al=202, english_alts=["The Only English"])], anilist={202: "Other", 909: "Chosen"})
    net.fill(db, [ref(mu=2, mdid="md-2", al=909)])
    entry = cached(db)["mu:2"]
    assert (entry.english, entry.anilist_id, entry.mangadex_english) == ("Chosen", 909, "The Only English")


def test_without_a_mangadex_id_a_title_search_must_link_back(db):
    net = Net(search={3: md("md-3", mu_id=3, al=303)}, anilist={303: "Found By Search"})
    net.fill(db, [ref(mu=3, title="Made Up Searched"), ref(mu=4, title="Made Up Unknown")])
    assert net.searches == [(3, "Made Up Searched"), (4, "Made Up Unknown")]
    got = cached(db)
    assert got["mu:3"].english == "Found By Search" and got["mu:4"].status == E.NONE and got["mu:4"].english is None


def test_a_mangadex_record_of_another_mangaupdates_series_is_not_used(db):
    net = Net([md("md-5", mu_id=999, al=505)], anilist={505: "Wrong Series"})
    net.fill(db, [ref(mu=5, mdid="md-5")])
    entry = cached(db)["mu:5"]
    assert entry.english is None and entry.status == E.NONE and net.al_batches == []


def test_no_english_title_on_anilist_is_none(db):
    net = Net([md("md-6", mu_id=6, al=606)], anilist={606: None})
    report = net.fill(db, [ref(mu=6, mdid="md-6")])
    assert report.found == 0 and cached(db)["mu:6"].status == E.NONE


def test_requests_are_batched_and_spaced(db):
    refs = [ref(mu=i, mdid=f"md-{i}") for i in range(1, 131)]
    net = Net([md(f"md-{i}", mu_id=i, al=1000 + i) for i in range(1, 131)],
              anilist={1000 + i: f"Made Up English {i}" for i in range(1, 131)})
    report = net.fill(db, refs)
    assert [len(b) for b in net.md_batches] == [100, 30]          # MangaDex: 100 records a request
    assert [len(b) for b in net.al_batches] == [50, 50, 30]       # AniList: 50 a request
    from mangalist.anilist_client import REQUEST_DELAY

    assert net.sleeps == [E.MANGADEX_DELAY, REQUEST_DELAY, REQUEST_DELAY, REQUEST_DELAY]   # none before the first
    assert report.found == 130 and len(cached(db)) == 130


def test_the_cache_answers_until_the_answer_is_old(db):
    net = Net([md("md-7", mu_id=7, al=707)], anilist={707: "Cached English"})
    net.fill(db, [ref(mu=7, mdid="md-7")])
    again = net.fill(db, [ref(mu=7, mdid="md-7")], now=NOW + dt.timedelta(days=30))
    assert again.looked == 0 and len(net.md_batches) == 1         # nothing asked: cached
    later = net.fill(db, [ref(mu=7, mdid="md-7")], now=NOW + E.REFRESH_FOUND)
    assert later.looked == 1 and later.changed == [] and len(net.md_batches) == 2
    store = E.EnglishTitles(db)
    none = E.EnglishEntry("mu:8", E.NONE, "2026-10-16T12:00:00Z")
    assert not none.due(NOW + E.REFRESH_NONE - dt.timedelta(seconds=1)) and none.due(NOW + E.REFRESH_NONE)
    failed = E.EnglishEntry("mu:9", E.FAILED, "2026-10-16T12:00:00Z")
    assert failed.due(NOW + E.RETRY_FAILED) and E.EnglishEntry("mu:1", E.FOUND, "garbage").due(NOW)
    assert store.due([ref(mu=7), ref(mu=7), ref(mu=10)], NOW) == [ref(mu=10)]


def test_a_network_failure_is_retried_later_and_keeps_the_last_answer(db):
    net = Net([md("md-11", mu_id=11, al=111)], anilist={111: "Kept English"})
    net.fill(db, [ref(mu=11, mdid="md-11"), ref(mu=12, mdid="md-12")])
    net.fail_md = True
    report = net.fill(db, [ref(mu=11, mdid="md-11"), ref(mu=12, mdid="md-12"), ref(mu=13, mdid="md-13")],
                      now=NOW + dt.timedelta(days=100))
    got = cached(db)
    assert report.failed == 3 and "could not be asked" in report.summary()
    assert got["mu:11"].english == "Kept English" and got["mu:11"].status == E.FOUND     # the last answer stays
    assert got["mu:13"].status == E.FAILED and got["mu:13"].english is None
    net.fail_md, net.fail_al = False, True
    net.fill(db, [ref(mu=13, mdid="md-13")], now=NOW + dt.timedelta(days=100, hours=13))
    assert cached(db)["mu:13"].status == E.NONE                    # no AniList link: nothing to fail on


def test_stopping_saves_what_was_found(db):
    net = Net([md("md-14", mu_id=14, al=141)], anilist={141: "Found Before Stop"})
    asked = []
    report = net.fill(db, [ref(mu=14, mdid="md-14")], should_stop=lambda: asked.append(1) or len(asked) > 1)
    assert report.stopped and net.al_batches == [] and "Stopped" in report.summary()
    assert "mu:14" not in cached(db)                               # MangaDex was read, AniList not: asked next time
    assert E.FillReport().summary() == "Official English titles: nothing to look up."


def test_record_keeps_what_add_series_found(db):
    E.record(db, ref(mu=15, mdid="md-15", al=151), "Added English", mangadex_english="Added English", now=NOW)
    E.record(db, ref(mu=16), None, now=NOW)
    got = cached(db)
    assert (got["mu:15"].english, got["mu:15"].status, got["mu:15"].anilist_id) == ("Added English", E.FOUND, 151)
    assert got["mu:16"].status == E.NONE


def test_an_older_database_without_the_table_reads_as_empty(tmp_path):
    class Broken:
        def connect(self):
            raise RuntimeError("no such table")

    assert E.EnglishTitles(Broken()).load() == {}


@pytest.mark.parametrize("ids,expected", [(["ab-1", "AB-1", "cd-2"], 2)])
def test_mangadex_batch_request(monkeypatch, ids, expected):
    from mangalist import add_series as A

    seen = []

    def get(url):
        seen.append(url)
        return {"data": [{"id": "ab-1", "type": "manga", "attributes": {
            "title": {"ja-ro": "Made Up Romaji"}, "altTitles": [{"en": "Made Up English"}],
            "links": {"mu": A.base36_from_mu_id(5), "al": "55"}}}]}

    monkeypatch.setattr(A, "_http_get_json", get)
    got = E.mangadex_records(ids)
    assert got["ab-1"].anilist_id == 55 and got["ab-1"].mu_id == 5
    assert seen[0].count("ids%5B%5D=") == expected and "contentRating%5B%5D=pornographic" in seen[0]
    assert E.mangadex_records([]) == {}

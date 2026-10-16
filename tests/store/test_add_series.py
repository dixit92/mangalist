"""Add a series by link (``mangalist.add_series``): link parsing, the lookups with fakes, the proposed folder names,
the guards on creating the folder (temporary directories only) and what is recorded. Made-up titles and ids; no
network."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

from mangalist import add_series as A
from mangalist import mu_cache
from mangalist.store.lock import RootLock

# --- links ------------------------------------------------------------------------------------------------------

UUID = "12345678-1234-4abc-8def-1234567890ab"


@pytest.mark.parametrize("text, expected", [
    ("https://www.mangaupdates.com/series/pb8uwds/some-series",
     A.ParsedLink(A.KIND_MANGAUPDATES, mu_id=int("pb8uwds", 36))),
    ("https://www.mangaupdates.com/series/pb8uwds", A.ParsedLink(A.KIND_MANGAUPDATES, mu_id=int("pb8uwds", 36))),
    ("  mangaupdates.com/series/PB8UWDS/x?foo=1  ", A.ParsedLink(A.KIND_MANGAUPDATES, mu_id=int("pb8uwds", 36))),
    ("<https://mangaupdates.com/series/0abc/>", A.ParsedLink(A.KIND_MANGAUPDATES, mu_id=int("0abc", 36))),
    (f"https://mangadex.org/title/{UUID}/some-series", A.ParsedLink(A.KIND_MANGADEX, mangadex_id=UUID)),
    (f"https://www.mangadex.org/title/{UUID.upper()}", A.ParsedLink(A.KIND_MANGADEX, mangadex_id=UUID)),
    (f"https://mangadex.org/manga/{UUID}", A.ParsedLink(A.KIND_MANGADEX, mangadex_id=UUID)),
    (UUID.upper(), A.ParsedLink(A.KIND_MANGADEX, mangadex_id=UUID)),
])
def test_links_that_name_a_series(text, expected):
    assert A.parse_link(text) == expected


@pytest.mark.parametrize("text", ["", "   ", "Some Made-Up Title", "mangaupdates", "Volume 3 of mangadex.org fans",
                                  "12345"])
def test_text_that_is_not_a_link_is_a_title_search(text):
    assert A.parse_link(text) is None


@pytest.mark.parametrize("text, hint", [
    ("https://www.mangaupdates.com/series.html?id=1234", "old address form"),
    ("https://www.mangaupdates.com/releases", "not a series page"),
    ("https://www.mangaupdates.com/series/", "not a series page"),
    (f"https://mangadex.org/chapter/{UUID}", "not a series page"),
    ("https://mangadex.org/", "not a series page"),
    ("https://example.com/series/abc", "Only MangaUpdates and MangaDex"),
])
def test_links_that_cannot_be_used_say_why(text, hint):
    with pytest.raises(A.LinkError, match=hint):
        A.parse_link(text)


def test_base36_round_trip():
    assert A.mu_id_from_base36("pb8uwds") == 55099564912       # an id the live check read from the API
    assert A.base36_from_mu_id(55099564912) == "pb8uwds"
    assert A.base36_from_mu_id(0) == "0"
    with pytest.raises(ValueError):
        A.mu_id_from_base36("not an id")


# --- lookups with fakes -----------------------------------------------------------------------------------------

def mu_record(sid=1000, title="Made Up Series", **extra) -> Dict[str, Any]:
    rec = {"series_id": sid, "title": title, "url": f"https://www.mangaupdates.com/series/{A.base36_from_mu_id(sid)}/x",
           "type": "Manga", "year": "2015", "licensed": True, "latest_chapter": 42,
           "associated": [{"title": "Made Up Series (Alt)"}, {"title": "メイドアップ"}]}
    rec.update(extra)
    return rec


def md_body(title="Made Up Series", english="Made Up English", mu="rst", alt=()) -> Dict[str, Any]:
    attrs = {"title": {"ja-ro": title}, "altTitles": [{"en": english}] + [{"ja": a} for a in alt], "year": 2015,
             "status": "ongoing", "originalLanguage": "ja", "links": {"mu": mu} if mu else {}}
    if english is None:
        attrs["altTitles"] = []
    return {"result": "ok", "data": {"id": UUID, "type": "manga", "attributes": attrs}}


class Fakes:
    def __init__(self, records=None, search=None, md=None):
        self.records = records or {}
        self.search_calls: List[str] = []
        self.filters: List[tuple] = []
        self.search_results = search or []
        self.md = md
        self.md_calls: List[str] = []
        self.anilist: Dict[int, Dict[str, Any]] = {}
        self.search_md = None                       # MangaDex's title search answer ({"data": [...]})

    def al_get(self, anilist_id):
        return self.anilist.get(anilist_id)

    def mu_get(self, sid):
        if sid not in self.records:
            import requests

            err = requests.HTTPError("404")
            err.response = type("R", (), {"status_code": 404})()
            raise err
        return self.records[sid]

    def mu_search(self, query, page_size=10, filter_types=None):
        self.search_calls.append(query)
        self.filters.append(tuple(filter_types or ()))
        hidden = {t.casefold() for t in filter_types or ()}
        return [{"record": r, "hit_title": ""} for r in self.search_results
                if str(r.get("type") or "").casefold() not in hidden]

    def md_get(self, url):
        self.md_calls.append(url)
        if "/manga?title=" in url:
            return self.search_md or {"data": []}
        if self.md is None:
            raise A.AddSeriesError("MangaDex has no series with that id.")
        return self.md

    def lookup(self, text, show_all=False):
        return A.lookup(text, mu_get=self.mu_get, mu_search=self.mu_search, mangadex_get=self.md_get,
                        show_all=show_all, anilist_get=self.al_get)


def test_a_mangaupdates_link_reads_the_record():
    f = Fakes({1000: mu_record(1000)})
    out = f.lookup(f"https://www.mangaupdates.com/series/{A.base36_from_mu_id(1000)}/x")
    assert out.info.mu_id == 1000 and out.info.kind == "Manga" and out.info.licensed is True
    assert out.info.latest_chapter == 42.0
    assert out.info.summary() == ["Manga (2015)", "Licensed in English", "Latest chapter 42"]
    assert f.search_calls == [] and all("/manga?title=" in url for url in f.md_calls) and len(f.md_calls) <= 1


def test_a_mangaupdates_id_that_does_not_exist_is_said_plainly():
    with pytest.raises(A.AddSeriesError, match="no series with that id"):
        Fakes({}).lookup(f"https://www.mangaupdates.com/series/{A.base36_from_mu_id(5)}/x")


def test_a_mangadex_link_that_lists_mangaupdates_uses_that_record():
    mu = A.mu_id_from_base36("rst")
    f = Fakes({mu: mu_record(mu)}, md=md_body(mu="rst"))
    out = f.lookup(f"https://mangadex.org/title/{UUID}/slug")
    assert f.md_calls == [f"{A.MANGADEX_API}/manga/{UUID}"]
    assert out.info.mu_id == mu and out.info.mangadex_id == UUID and out.info.english_title == "Made Up English"
    assert out.candidates == [] and f.search_calls == []


def test_a_mangadex_link_without_mangaupdates_offers_a_search_and_can_go_without():
    f = Fakes(md=md_body(mu=None), search=[mu_record(7), mu_record(8)])
    out = f.lookup(UUID)
    assert out.info.mu_id is None and not out.info.matched and out.info.mangadex_id == UUID
    assert [c["series_id"] for c in out.candidates] == [7, 8]
    assert f.search_calls == ["Made Up English"]            # MangaDex's English title is what is searched
    assert any("lists no MangaUpdates page" in n for n in out.notes)
    assert out.info.summary() == ["2015", "No MangaUpdates match"]
    picked = A.with_mu_record(out, out.candidates[0], mu_get=Fakes({7: mu_record(7)}).mu_get)
    assert picked.mu_id == 7 and picked.mangadex_id == UUID and picked.english_title == "Made Up English"


def test_a_mangadex_link_to_a_vanished_mangaupdates_page_falls_back_to_the_search():
    f = Fakes({}, md=md_body(mu="rst"), search=[mu_record(9)])
    out = f.lookup(UUID)
    assert out.info.mu_id is None and len(out.candidates) == 1
    assert any("no series with that id" in n for n in out.notes)


def test_a_title_searches_mangaupdates_only():
    f = Fakes(search=[mu_record(3)])
    out = f.lookup("Made Up")
    assert out.info is None and [c["series_id"] for c in out.candidates] == [3] and f.search_calls == ["Made Up"]
    assert Fakes().lookup("Nothing Here").notes == ["MangaUpdates found nothing for that title."]
    with pytest.raises(A.AddSeriesError):
        Fakes().lookup("  ")


def test_a_title_search_hides_doujinshi_and_artbooks_unless_asked():
    # owner, 2026-10-15 (answer 7): "Yes, with the option to enable them in the search"
    rows = [mu_record(1, "Made Up Doujin", type="Doujinshi"), mu_record(2, "Made Up Series"),
            mu_record(3, "Made Up Artbook", type="Artbook"), mu_record(4, "Made Up Novel", type="Novel")]
    f = Fakes(search=rows)
    out = f.lookup("Made Up")
    assert [c["series_id"] for c in out.candidates] == [2, 4]
    assert f.filters == [("Doujinshi", "Artbook")]                  # asked of MangaUpdates: the page holds real series
    out = f.lookup("Made Up", show_all=True)
    assert [c["series_id"] for c in out.candidates] == [1, 2, 3, 4] and f.filters[-1] == ()


def test_hidden_types_are_dropped_even_when_mangaupdates_returns_them():
    def careless(query, page_size=10, filter_types=None):
        return [{"record": mu_record(1, "Made Up Doujin", type="doujinshi")}, {"record": mu_record(2)}]

    assert [r["series_id"] for r in A.search_mu("Made Up", careless)] == [2]
    assert [r["series_id"] for r in A.search_mu("Made Up", careless, show_all=True)] == [1, 2]


def test_a_mangadex_series_without_mangaupdates_searches_without_doujinshi_too():
    f = Fakes(md=md_body(mu=None), search=[mu_record(7, type="Doujinshi"), mu_record(8)])
    out = f.lookup(UUID)
    assert [c["series_id"] for c in out.candidates] == [8]
    assert [c["series_id"] for c in f.lookup(UUID, show_all=True).candidates] == [7, 8]


def test_mangadex_records_without_a_title_or_with_a_bad_mu_link_are_handled():
    with pytest.raises(A.AddSeriesError):
        A.mangadex_from_record({"data": {"id": UUID, "attributes": {"title": {}}}})
    with pytest.raises(A.AddSeriesError):
        A.mangadex_from_record({"nothing": 1})
    md = A.mangadex_from_record(md_body(mu="not an id!"))
    assert md.mu_id is None
    md = A.mangadex_from_record({"data": {"id": UUID, "attributes": {"title": {"ja-ro": "Only Romaji"}}}})
    assert md.title == "Only Romaji" and md.english_title is None


def test_a_failing_mangadex_request_is_a_readable_error():
    def boom(url):
        raise OSError("secret-host")

    with pytest.raises(A.AddSeriesError) as err:
        A.fetch_mangadex(UUID, get=boom)
    assert "secret-host" not in str(err.value)


# --- the folder name --------------------------------------------------------------------------------------------

@pytest.mark.parametrize("raw, clean", [
    ("Made Up: The Sequel", "Made Up - The Sequel"),
    ("Made:Up", "Made-Up"),
    ('A/B\\C|D "E"', "A-B-C-D 'E'"),
    ("What? Really*", "What Really"),
    ("  Spaced   out  ", "Spaced out"),
    ("Ends with dots...", "Ends with dots"),
    (".Hidden", "Hidden"),
    ("CON", "CON_"),
    ("", ""),
    ("???", ""),
])
def test_clean_folder_name(raw, clean):
    assert A.clean_folder_name(raw) == clean


def test_the_folder_scheme_is_proposed_first():
    # owner, 2026-10-15 (answer 6): "Always use "<romaji> [<English>]", similar to animefetch"
    info = A.info_from_mu(mu_record(title="Romaji Title"), A.mangadex_from_record(md_body(title="Romaji Title",
                                                                                          english="English: Title")))
    names = A.propose_names(info)
    assert names[0] == "Romaji Title [English Title]"               # MangaUpdates' title, MangaDex's English title
    assert names[0] == info.scheme_name()
    assert "Romaji Title" in names and "English - Title" in names
    assert "Made Up Series (Alt)" in names and not any("メ" in n for n in names)
    assert len(names) == len(set(names))
    for n in names:
        assert A.windows_name_problem(n) is None


def test_without_an_english_title_the_romaji_alone_is_first_and_english_looking_titles_are_offered():
    info = A.info_from_mu(mu_record(title="Made Up Romaji no Hon",
                                    associated=[{"title": "The Made Up Book"}, {"title": "Made Up Romaji"},
                                                {"title": "メイドアップ"}]))
    names = A.propose_names(info)
    assert names[0] == "Made Up Romaji no Hon"                     # no English title known: "<romaji>"
    assert names[1] == "Made Up Romaji no Hon [The Made Up Book]"  # an alternative title that reads as English
    assert "Made Up Romaji no Hon [Made Up Romaji]" not in names


def test_a_long_romaji_is_proposed_by_its_official_short_name():
    # owner, 2026-10-16: a romaji over 50 characters uses an official short name (animefetch's rule), else stays whole
    long = "Made Up Romaji wa Totemo Nagai Fukudai ga Tsuzuku Hanashi desu yo"
    info = A.info_from_mu(mu_record(title=long, associated=[{"title": "The Made Up Long Story"},
                                                            {"title": "MadeNaga"}]))
    names = A.propose_names(info)
    assert names[0] == "MadeNaga" == info.scheme_name()
    assert names[1] == "MadeNaga [The Made Up Long Story]"
    plain = A.info_from_mu(mu_record(title=long, associated=[{"title": "The Made Up Long Story"}]))
    assert A.propose_names(plain)[0] == long                       # no official short name: never cut


def test_the_matching_title_is_the_only_proposal_when_nothing_differs():
    info = A.info_from_mu(mu_record(title="Same Title", associated=[]))
    assert A.propose_names(info) == ["Same Title"]
    only_md = A.info_from_mangadex(A.mangadex_from_record(md_body(english=None, mu=None)))
    assert A.propose_names(only_md) == ["Made Up Series"]


def test_mangadex_english_and_romanised_titles():
    def md(title, alts):
        return A.mangadex_from_record({"data": {"id": UUID, "attributes": {"title": title, "altTitles": alts}}})

    # the usual record: the romanised title under "en", the English one among the alternatives
    m = md({"en": "Made Up Romaji"}, [{"ja": "メイドアップ"}, {"en": "Made Up English"}])
    assert (m.romaji_title, m.english_title, m.title) == ("Made Up Romaji", "Made Up English", "Made Up English")
    # the English title as the main one, the romanised title among the alternatives
    m = md({"en": "Made Up English"}, [{"ko-ro": "Made Up Romaja"}])
    assert (m.romaji_title, m.english_title) == ("Made Up Romaja", "Made Up English")
    # only a main title: no English title is known
    m = md({"en": "Made Up Only"}, [])
    assert (m.romaji_title, m.english_title, m.title) == ("Made Up Only", None, "Made Up Only")
    # an "English" alternative that is the romaji again is no English title
    m = md({"ja-ro": "Made Up Romaji"}, [{"en": "made-up romaji"}])
    assert m.english_title is None
    info = A.info_from_mangadex(md({"en": "Made Up English"}, [{"ja-ro": "Made Up Romaji"}]))
    assert A.propose_names(info)[0] == "Made Up Romaji [Made Up English]"


# --- the folder -------------------------------------------------------------------------------------------------

@pytest.fixture
def root(db, library):
    return db.add_root(str(library), "Main")


def test_the_folder_is_made_empty_inside_the_library(root, library):
    folder = A.create_series_folder(root, "Made Up Series")
    assert folder == library.resolve() / "Made Up Series"
    assert folder.is_dir() and list(folder.iterdir()) == []
    assert [p.name for p in library.iterdir() if not p.name.startswith(".")] == ["Made Up Series"]
    assert not (library / RootLock(library).path.name).exists()       # the lock is released
    if hasattr(os, "getuid"):
        assert folder.stat().st_uid == os.getuid()                    # owned by the user MangaList runs as


@pytest.mark.parametrize("name, hint", [
    ("", "needs a name"),
    (" padded", "needs a name"),
    ("../Escape", "starting with a dot"),
    ("sub/../Escape", "not allowed on Windows"),
    ("a/b", "not allowed on Windows"),
    ("a\\b", "not allowed on Windows"),
    ("Bad: name", "not allowed on Windows"),
    ("trailing.", "ends with a dot"),
    ("NUL", "reserved"),
    (".hidden", "starting with a dot"),
    ("x" * 300, "longer than"),
])
def test_unsafe_names_are_refused_and_nothing_is_made(root, library, name, hint):
    with pytest.raises(A.AddSeriesError, match=hint):
        A.create_series_folder(root, name)
    assert [p for p in library.iterdir() if not p.name.startswith(".")] == []
    assert not (library.parent / "Escape").exists()


def test_an_existing_path_is_never_used_whatever_its_case(root, library):
    (library / "Existing Series").mkdir()
    (library / "a file.cbz").write_bytes(b"x")
    for name in ("Existing Series", "existing series", "EXISTING SERIES"):
        with pytest.raises(A.AddSeriesError, match="already exists"):
            A.create_series_folder(root, name)
    with pytest.raises(A.AddSeriesError, match="already exists"):
        A.create_series_folder(root, "a file.cbz")
    assert (library / "Existing Series").is_dir() and (library / "a file.cbz").read_bytes() == b"x"


def test_a_path_that_appears_during_the_check_is_not_replaced(root, library, monkeypatch):
    monkeypatch.setattr(A, "folder_problem", lambda r, n: None)
    (library / "Raced").mkdir()
    (library / "Raced" / "keep.cbz").write_bytes(b"x")
    with pytest.raises(A.AddSeriesError, match="already exists"):
        A.create_series_folder(root, "Raced")
    assert (library / "Raced" / "keep.cbz").exists()


def test_a_name_the_library_excludes_is_refused(db, library):
    root = db.add_root(str(library), "Main", exclusions=["Skipped*"])
    with pytest.raises(A.AddSeriesError, match="excludes"):
        A.create_series_folder(root, "Skipped Series")
    assert A.create_series_folder(root, "Kept Series").is_dir()


def test_a_library_that_is_not_there_is_refused(db, tmp_path):
    gone = tmp_path / "unmounted"
    root = db.add_root(str(gone), "Gone")
    with pytest.raises(A.AddSeriesError, match="not reachable"):
        A.create_series_folder(root, "Made Up Series")
    assert not gone.exists()                                         # never creates the library folder itself


def test_a_library_another_instance_is_writing_is_refused(root, library):
    holder = RootLock(library, host="another-host", pid=1)
    holder.acquire()
    try:
        with pytest.raises(A.AddSeriesError, match="Try again"):
            A.create_series_folder(root, "Made Up Series")
    finally:
        holder.release()
    assert not (library / "Made Up Series").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_a_library_that_cannot_be_written_is_a_readable_error(root, library):
    library.chmod(0o555)
    try:
        if os.access(library, os.W_OK):
            pytest.skip("running with rights that ignore permission bits")
        with pytest.raises(A.AddSeriesError):
            A.create_series_folder(root, "Made Up Series")
    finally:
        library.chmod(0o755)


# --- recording --------------------------------------------------------------------------------------------------

class FakeScans:
    def __init__(self, answer="MangaPixer scans: 1 started", fail=False):
        self.calls, self.answer, self.fail = [], answer, fail

    def __call__(self, db, series_ids):
        self.calls.append(list(series_ids))
        if self.fail:
            raise RuntimeError("boom")
        return self.answer


def test_adding_makes_the_folder_the_row_the_confirmed_match_and_the_source(db, root, library):
    info = A.info_from_mu(mu_record(1000), A.mangadex_from_record(md_body(mu="rst")))
    source = A.SourceChoice("5511", "MangaDex (EN)", 77, "Made Up English")
    scans = FakeScans()
    out = A.add_series(db, root, "Made Up English", info, source=source,
                       progress={"scan_latest_chapter": 42.0, "publisher_chapters": 40.0}, request_scans=scans)
    assert out.folder == library.resolve() / "Made Up English" and out.folder.is_dir()
    assert out.notes == [] and out.mangapixer == "MangaPixer scans: 1 started"

    row = db.series_for_folder(out.folder)
    assert row is not None and row.id == out.series_id and row.status == "present" and row.n_archives == 0
    assert row.mu_id == 1000 and row.mu_confirmed                  # copied onto the row by the links cache
    cached = mu_cache.load_entry(out.folder)
    assert cached["mu_id"] == 1000 and cached["mu_confirmed"] is True and cached["licensed"] is True
    assert cached["mu_score"] == 0.0 and cached["mu_band"] is None and cached["scan_latest_chapter"] == 42.0
    assert cached["mu_associated"][0] == "Made Up Series"
    choice = db.get_meta(f"suwayomi.series.{out.series_id}")
    assert '"how": "mangadex-id"' in choice and '"manga_id": 77' in choice and '"source_id": "5511"' in choice
    assert scans.calls == [[out.series_id]]


def test_the_next_scan_keeps_the_choices(db, root, library):
    from mangalist.scanner import scan_root
    from mangalist.store.series import seen_from_entries

    info = A.info_from_mu(mu_record(1000))
    out = A.add_series(db, root, "Made Up Series", info, source=A.SourceChoice("5511", "MangaDex", 77, "T"))
    entries = scan_root(library, root_id=root.id)
    assert [e.title for e in entries] == ["Made Up Series"] and entries[0].files == []     # an empty folder is a series
    db.record_scan(root.id, library.resolve(), seen_from_entries(library.resolve(), entries))
    row = db.series_for_folder(out.folder)
    assert row.id == out.series_id and row.mu_id == 1000 and row.mu_confirmed
    assert db.get_meta(f"suwayomi.series.{out.series_id}") is not None


def test_a_series_without_a_match_records_no_link(db, root):
    info = A.info_from_mangadex(A.mangadex_from_record(md_body(mu=None)))
    out = A.add_series(db, root, "Made Up Series", info, source=A.SourceChoice("5511", "MangaDex", 77, "T"))
    assert mu_cache.load_entry(out.folder) is None
    assert db.get_meta(f"suwayomi.series.{out.series_id}") is not None


def test_failures_after_the_folder_exists_are_notes_not_errors(db, root, monkeypatch):
    info = A.info_from_mu(mu_record(1000))
    monkeypatch.setattr(A, "record_mu_match", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    out = A.add_series(db, root, "Made Up Series", info, source=A.SourceChoice("1", "S", 2, "T"),
                       request_scans=FakeScans(fail=True))
    assert out.folder.is_dir() and out.series_id is not None
    assert any("Fix MangaUpdates match" in n for n in out.notes)
    assert "not asked" in out.mangapixer or "MangaPixer was not asked" in out.mangapixer


def test_no_folder_means_nothing_is_recorded(db, root, library):
    (library / "Taken").mkdir()
    scans = FakeScans()
    with pytest.raises(A.AddSeriesError):
        A.add_series(db, root, "Taken", A.info_from_mu(mu_record(1000)), request_scans=scans)
    assert mu_cache.load_all() == {} and scans.calls == [] and db.list_series() == []


def test_existing_folders_for_a_series(db, root):
    info = A.info_from_mu(mu_record(1000))
    out = A.add_series(db, root, "Made Up Series", info)
    assert A.existing_folders_for(1000) == [str(out.folder)]
    assert A.existing_folders_for(1001) == [] and A.existing_folders_for(None) == []


# --- Suwayomi ---------------------------------------------------------------------------------------------------

def test_the_source_is_found_by_the_mangadex_id_and_never_written(db):
    from mangalist.downloads.contracts import SuwayomiManga, SuwayomiSource
    from mangalist.downloads.contracts import SuwayomiConnection
    from mangalist.store.downloads import SuwayomiSettings

    searched = []

    class Client:
        closed = False

        def sources(self):
            return [SuwayomiSource(id="5511", name="MangaDex", display_name="MangaDex (EN)", lang="en",
                                  extension="eu.kanade.tachiyomi.extension.all.mangadex"),
                    SuwayomiSource(id="6", name="Other", display_name="Other (EN)", lang="en", extension="x.other")]

        def search(self, source_id, query):
            searched.append((source_id, query))
            return [SuwayomiManga(id=77, title="Made Up English", url=f"/manga/{UUID}", source_id=source_id)]

        def close(self):
            self.closed = True

    assert "not set up" in A.find_mangadex_source(db, UUID, client_factory=lambda c: Client()).note
    SuwayomiSettings(db).save_connection(SuwayomiConnection(base_url="http://suwayomi.invalid:4567"))
    client = Client()
    out = A.find_mangadex_source(db, UUID, client_factory=lambda c: client)
    assert out.choice == A.SourceChoice("5511", out.choice.source_name, 77, "Made Up English")
    assert searched == [("5511", f"id:{UUID}")] and client.closed
    assert db.list_series() == [] and SuwayomiSettings(db).series_choice(1) == {}


def test_suwayomi_that_fails_or_lacks_the_series_is_a_note(db):
    from mangalist.downloads.contracts import SuwayomiSource
    from mangalist.services.suwayomi import SuwayomiError
    from mangalist.downloads.contracts import SuwayomiConnection
    from mangalist.store.downloads import SuwayomiSettings

    SuwayomiSettings(db).save_connection(SuwayomiConnection(base_url="http://suwayomi.invalid:4567"))

    class Down:
        def sources(self):
            raise SuwayomiError("unreachable")

    class Empty:
        def sources(self):
            return [SuwayomiSource(id="5511", name="MangaDex", display_name="MangaDex (EN)", lang="en",
                                  extension="x.mangadex")]

        def search(self, source_id, query):
            return []

    class NoMangaDex:
        def sources(self):
            return [SuwayomiSource(id="6", name="Other", display_name="Other (EN)", lang="en", extension="x.other")]

    assert "could not be asked" in A.find_mangadex_source(db, UUID, client_factory=lambda c: Down()).note
    assert "no such series" in A.find_mangadex_source(db, UUID, client_factory=lambda c: Empty()).note
    assert "No MangaDex source" in A.find_mangadex_source(db, UUID, client_factory=lambda c: NoMangaDex()).note


# --- the official English title (owner, 2026-10-15: a typed title gave no English) -------------------------------

def _md_item(mu_sid, english_alts, al=None, title="Made Up Romaji"):
    links = {"mu": A.base36_from_mu_id(mu_sid)}
    if al is not None:
        links["al"] = str(al)
    return {"id": UUID, "type": "manga", "attributes": {"title": {"ja-ro": title}, "year": 2022, "status": "ongoing",
            "originalLanguage": "ja", "altTitles": [{"en": e} for e in english_alts], "links": links}}


def test_a_picked_mangaupdates_series_takes_anilists_english_title_through_mangadex():
    f = Fakes({1000: mu_record(1000, title="Made Up Romaji")}, search=[mu_record(1000, title="Made Up Romaji")])
    f.search_md = {"data": [_md_item(999, ["Not This One"]), _md_item(1000, ["A Fan Title", "The Licensed Title"],
                                                                        al=55)]}
    f.anilist[55] = {"english_title": "The Licensed Title"}
    look = f.lookup("Made Up Romaji")
    info = A.with_mu_record(look, look.candidates[0], mu_get=f.mu_get, mangadex_get=f.md_get, anilist_get=f.al_get)
    assert info.english_title == "The Licensed Title" and info.mangadex_id == UUID
    assert A.propose_names(info)[0] == "Made Up Romaji [The Licensed Title]"


def test_several_english_titles_without_anilist_are_proposals_not_the_default():
    f = Fakes({1000: mu_record(1000, title="Made Up Romaji")}, search=[mu_record(1000, title="Made Up Romaji")])
    f.search_md = {"data": [_md_item(1000, ["A Fan Title", "The Licensed Title"])]}
    look = f.lookup("Made Up Romaji")
    info = A.with_mu_record(look, look.candidates[0], mu_get=f.mu_get, mangadex_get=f.md_get, anilist_get=f.al_get)
    names = A.propose_names(info)
    assert info.english_title is None and names[0] == "Made Up Romaji"
    assert "Made Up Romaji [The Licensed Title]" in names and "Made Up Romaji [A Fan Title]" in names


def test_one_english_title_on_mangadex_is_still_taken():
    f = Fakes({1000: mu_record(1000, title="Made Up Romaji")})
    f.search_md = {"data": [_md_item(1000, ["The Only English Title"])]}
    out = f.lookup(f"https://www.mangaupdates.com/series/{A.base36_from_mu_id(1000)}/x")
    assert out.info.english_title == "The Only English Title"


def test_no_mangadex_record_for_the_series_keeps_the_romaji_alone():
    f = Fakes({1000: mu_record(1000, title="Made Up Romaji")})
    f.search_md = {"data": [_md_item(999, ["Another Series"])]}                  # links another MangaUpdates page
    out = f.lookup(f"https://www.mangaupdates.com/series/{A.base36_from_mu_id(1000)}/x")
    assert out.info.english_title is None and A.propose_names(out.info)[0] == "Made Up Romaji"


def test_a_mangadex_link_prefers_anilists_english_title():
    body = {"result": "ok", "data": _md_item(1000, ["A Fan Title", "The Licensed Title"], al=55)}
    f = Fakes({1000: mu_record(1000, title="Made Up Romaji")}, md=body)
    f.anilist[55] = {"english_title": "The Licensed Title"}
    out = f.lookup(f"https://mangadex.org/title/{UUID}/slug")
    assert out.info.english_title == "The Licensed Title"


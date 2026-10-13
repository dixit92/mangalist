"""The find pass for a series with Unbundled Chapters on (mangalist.unbundled): its missing chapters after the last volume
are searched like any chapter series' - the saved Suwayomi source, the series' group, Auto sends them - next to its
volumes on nyaa. The real wanted list over a temporary library paired with a synthetic MangaPixer item (volumes 1-3 out,
chapters 1-24.5, latest chapter 41); a fake Suwayomi and nyaa; made-up names."""

from __future__ import annotations

import pytest

from mangalist.downloads.finder import run_find
from mangalist.downloads.finding import TRIGGER_NOW
from mangalist.store.findings import FindingStore
from mangalist.store.mangapixer import MangaPixerCache, Mapping
from mangalist.unbundled import UnbundledChapters

from ..states.helpers import item
from ..wanted.library import LIB_ID, _Lib
from .finder_fakes import wire
from .suwayomi_fakes import MANGA, MANGADEX, chapter


@pytest.fixture
def w(db, series, tmp_path):
    w = wire(db, series, tmp_path, suwayomi=True)
    root = db.list_roots()[0]
    cache = MangaPixerCache(db)
    cache.save_libraries([_Lib(LIB_ID, "Manga")])
    cache.apply_page(LIB_ID, [], [item(nodeId="n1", trail=["Series A"])])
    cache.save_mapping(Mapping(root.id, LIB_ID, [], manual=True))
    w.suwayomi.chapter_list = [chapter(108, "8"), chapter(125, "25"), chapter(126, "26"),
                               chapter(226, "26", group="Beta Scans")]
    w.backend.suwayomi.set_series_choice(w.series_id, source_id=MANGADEX.id, manga_id=MANGA.id,
                                         manga_title=MANGA.title, how="confirmed", group="Alpha Scans")
    FindingStore(db).set_mode([w.series_id], "auto")
    return w


def run(db, w):
    return run_find(db, trigger=TRIGGER_NOW, backend=w.backend)     # wanted=None: mangalist.wanted.wanted_list


def enqueued(w):
    return [ids for name, *rest in w.suwayomi.calls if name == "enqueue" for ids in rest]


def test_off_a_volume_series_is_searched_for_its_volumes_only(db, w):
    run(db, w)
    assert [call[1] for call in w.search.calls] == [("2", "3")]
    assert enqueued(w) == [] and not [c for c in w.suwayomi.calls if c[0] == "chapters"]


def test_on_its_chapters_after_the_last_volume_are_found_and_auto_sends_them(db, w):
    UnbundledChapters(db).set([w.series_id], True)
    report = run(db, w)
    assert [call[1] for call in w.search.calls] == [("2", "3")]     # the volumes as before
    assert enqueued(w) == [[125, 126]]                              # 25-26 from the series' group; 8 is in volume 1
    assert report.sent == 1
    recs = w.backend.chapter_ledger.all_records()
    assert {r.wanted_chapters for r in recs} == {("25",), ("26",)}


def test_on_notify_offers_the_chapters_with_the_missing_range(db, w):
    UnbundledChapters(db).set([w.series_id], True)
    FindingStore(db).set_mode([w.series_id], "notify")
    run(db, w)
    (f,) = [f for f in FindingStore(db).list("waiting") if f.kind == "chapters"]
    assert f.fills == ("25", "26") and enqueued(w) == []


# --- where the chapters go, and how they are named and counted --------------------------------------------------------


def test_a_volume_series_files_its_chapters_in_the_series_folder(db, series):
    from mangalist.downloads.chapters import chapter_placement_for

    sid, folder = series
    placement = chapter_placement_for(db, sid)
    assert placement.target_dir == str(folder) and "series folder" in placement.reason


def test_a_filed_chapter_is_named_by_the_scheme_and_counted_as_a_chapter_next_to_the_volumes():
    from mangalist.inventory import inventory_of_parsed
    from mangalist.naming import chapter_file_name, target_name
    from mangalist.parsing import ParseContext, parse_name

    name = chapter_file_name(25, group="Alpha Scans")                # what chapter arrivals file it as
    ctx = ParseContext(series_title="Series A", kind_hint="volumes")  # a volume series' "volumes" answer
    parsed = parse_name(name, ctx)
    assert target_name(parsed, ext=".cbz", series_title="Series A") == name      # no Rename pending
    inv = inventory_of_parsed([("Series A v01.cbz", parse_name("Series A v01.cbz", ctx)), (name, parsed)])
    assert [str(v) for v in inv.volumes] == ["1"] and [str(c) for c in inv.chapters] == ["25"]

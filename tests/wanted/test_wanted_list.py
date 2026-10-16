"""The wanted list without Qt (:mod:`mangalist.wanted`): what the find pass is given, from the library database and the
MangaPixer cache alone. The parity with the window's List / Download tab is in ``tests/gui/test_wanted_parity.py``."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from mangalist import store, wanted
from mangalist.states import State

from ..states.helpers import TODAY
from .library import build_library, scan


@pytest.fixture
def lib(tmp_path):
    store.reset_stores()
    return build_library(tmp_path / "lib")


def _keyed(lib, items):
    """{("Manga/Example Quest", "volumes"): WantedSeries}"""
    rev = {v: k for k, v in lib.folders.items()}
    return {(rev[w.folder], w.group): w for w in items}


def test_the_list_has_every_series_with_gaps_and_nothing_else(lib):
    got = _keyed(lib, wanted.wanted_list(lib.db, today=TODAY))
    expected = {
        ("Manga/Example Quest", "volumes"): ("2", "3"),
        ("Manga/Franchise/Alpha Tale", "volumes"): ("3",),          # inherits the franchise folder's MangaPixer item
        ("Manga/Own Series", "volumes"): ("3", "4", "5"),            # the own matcher: 5 English volumes out
        ("Manga/Volume Gaps", "volumes"): ("2", "4"),
        ("Manga/Scanlation Chapters", "chapters"): ("16", "17", "18", "19", "20"),
        ("Manga/Upgrade Series", "volumes"): ("3",),
        ("Manga/Upgrade Series", "upgrades"): ("2",),                # chapters 9-16 held
        ("Manga/Answered Kind", "volumes"): ("3",),                  # the "volumes" answer read 01 / 02 as volumes
        ("Manga/Empty Licensed", "volumes"): ("1", "2", "3"),        # an empty folder: Wanted
        ("Manga/Parent Series", "volumes"): ("2", "3"),              # the nested series' files are not its own
        ("Manga/Parent Series/Spin-off Story", "volumes"): ("2", "3"),
        ("Manhwa/Own Manhwa", "chapters"): ("11", "12", "13", "14"),
    }
    for key, missing in expected.items():
        assert got[key].missing == missing, key
    # nothing to compare, a pending "volumes or chapters?", restarting seasons, not a series, nothing missing
    for folder in ("Manga/Bare Numbers", "Manga/Season Webtoon", "Manga/Empty Unknown", "Manga/Not A Series",
                   "Manga/Franchise/Beta Tale", "Manhwa/Unmatched Manhwa"):
        assert not any(k[0] == folder for k in got), folder


def test_entries_carry_the_series_id_titles_and_the_nyaa_rule(lib):
    items = wanted.wanted_list(lib.db, today=TODAY)
    assert items and all(w.series_id == lib.db.series_for_folder(w.folder).id for w in items)
    got = _keyed(lib, items)
    quest = got[("Manga/Example Quest", "volumes")]
    assert quest.findable and quest.reason == "" and quest.held == ("1",) and quest.gaps == "Vol. 2-3"
    assert quest.titles[:2] == ("Example Quest", "Example Quest: Second Name")
    own = got[("Manga/Own Series", "volumes")]
    assert own.findable and own.reason == ""             # a confirmed own match is searched too (owner, 2026-10-15)
    chapters = got[("Manga/Scanlation Chapters", "chapters")]
    assert not chapters.findable and chapters.reason == "needs Suwayomi" and chapters.gaps == "Ch. 16-20"


def test_downloads_off_says_so(lib):
    got = _keyed(lib, wanted.wanted_list(lib.db, today=TODAY, downloads=False))
    quest = got[("Manga/Example Quest", "volumes")]
    assert not quest.findable and quest.reason == "downloads are off"


def test_root_ids_pick_the_libraries(lib):
    manhwa = next(r for r in lib.roots if r.name == "Manhwa")
    got = _keyed(lib, wanted.wanted_list(lib.db, root_ids=[manhwa.id], today=TODAY))
    assert set(got) == {("Manhwa/Own Manhwa", "chapters")}
    assert wanted.wanted_list(lib.db, root_ids=[], today=TODAY) == []


def test_the_order_is_the_lists(lib):
    rev = {v: k for k, v in lib.folders.items()}
    folders = [rev[str(v.entry.folder)] for v in wanted.library_states(lib.db, today=TODAY)]
    manga = [f for f in folders if f.startswith("Manga/")]
    assert folders == manga + [f for f in folders if f.startswith("Manhwa/")]
    assert manga.index("Manga/Answered Kind") == 0 and manga.index("Manga/Parent Series") + 1 == \
        manga.index("Manga/Parent Series/Spin-off Story")


def test_states_of_the_special_cases(lib):
    views = {str(v.entry.folder): v for v in wanted.library_states(lib.db, today=TODAY)}

    def view(key):
        return views[lib.folder(key)]

    assert view("Manga/Bare Numbers").state.needs_attention == ("needs kind",)
    assert view("Manga/Season Webtoon").entry.restarting_units == frozenset({"chapter"})
    assert view("Manga/Behind Done").state.state == State.UP_TO_DATE         # the owner's override
    assert view("Manga/Empty Unknown").state.state == State.WANTED and not view("Manga/Empty Unknown").state.gaps
    assert view("Manga/Not A Series").state.state == State.NOT_A_SERIES
    assert view("Manga/Parent Series").entry.n_files == 2                     # counted with its nested series' file
    assert view("Manga/Parent Series").held == ("1",)
    assert view("Manga/Answered Kind").entry.kind_hint == "volumes"


def test_read_from_the_database_not_the_disk(lib):
    """The find pass runs right after the rescan: the last scan's records are the truth until the next one."""
    before = wanted.wanted_list(lib.db, today=TODAY)
    shutil.rmtree(lib.base / "Manga" / "Volume Gaps")
    assert wanted.wanted_list(lib.db, today=TODAY) == before
    scan(lib.db, lib.db.list_roots())                     # the next rescan marks it missing: it leaves the list
    got = _keyed(lib, wanted.wanted_list(lib.db, today=TODAY))
    assert not any(k[0] == "Manga/Volume Gaps" for k in got) and ("Manga/Example Quest", "volumes") in got


def test_a_broken_mangapixer_cache_falls_back_to_the_own_matcher(lib, monkeypatch, caplog):
    def broken(db):
        raise RuntimeError("cache unreadable")

    monkeypatch.setattr(wanted, "open_resolver", broken)
    got = _keyed(lib, wanted.wanted_list(lib.db, today=TODAY))
    assert set(got) == {("Manga/Own Series", "volumes"), ("Manhwa/Own Manhwa", "chapters")}
    assert "MangaPixer's data unavailable" in caplog.text


def test_a_root_that_cannot_be_read_does_not_hide_the_others(lib, monkeypatch, caplog):
    real = wanted.entries_from_db

    def flaky(db, root):
        if root.name == "Manga":
            raise RuntimeError("boom")
        return real(db, root)

    monkeypatch.setattr(wanted, "entries_from_db", flaky)
    got = _keyed(lib, wanted.wanted_list(lib.db, today=TODAY))
    assert set(got) == {("Manhwa/Own Manhwa", "chapters")}
    assert "could not be read" in caplog.text


def test_the_list_is_logged_without_titles(lib, caplog):
    import logging

    with caplog.at_level(logging.INFO, logger="mangalist.wanted"):
        wanted.wanted_list(lib.db, today=TODAY)
    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("Wanted list:"))
    assert "series with gaps" in line and "Example Quest" not in line



def test_a_series_marked_up_to_date_is_not_in_to_get(lib):
    """Owner, 2026-10-15: Mark Behind as up to date also takes the series out of To get (the List keeps its gaps)."""
    views = {str(v.entry.folder): v for v in wanted.library_states(lib.db, today=TODAY)}
    done = views[lib.folder("Manga/Behind Done")]
    assert done.state.gaps and done.entry.behind_override == "done"           # the List still shows the gaps
    got = _keyed(lib, wanted.wanted_list(lib.db, today=TODAY))
    assert not any(k[0] == "Manga/Behind Done" for k in got)
    done.entry.behind_override = None                                         # cleared: it comes back
    assert wanted.series_wanted(done.entry, done.state, done.knowledge, done.held, done.row.id)


def test_a_declared_editions_volumes_are_offered_by_hand_only(lib, monkeypatch):
    """Owner, 2026-10-15: never searched automatically, but searchable by hand in the Download tab (``by_hand``)."""
    from dataclasses import replace
    from decimal import Decimal

    views = {str(v.entry.folder): v for v in wanted.library_states(lib.db, today=TODAY)}
    v = views[lib.folder("Manga/Example Quest")]
    edition = replace(v.knowledge, edition="master", edition_volumes=Decimal(6))
    assert edition.edition_label == "Master edition"
    items = wanted.series_wanted(v.entry, v.state, edition, v.held, v.row.id)
    vols = next(w for w in items if w.group == "volumes")
    assert not vols.findable and vols.by_hand and vols.reason.endswith("not searched automatically")
    off = wanted.series_wanted(v.entry, v.state, edition, v.held, v.row.id, downloads=False)
    assert not any(w.by_hand for w in off)                                     # downloads off: nothing by hand
    plain = wanted.series_wanted(v.entry, v.state, v.knowledge, v.held, v.row.id)
    assert not any(w.by_hand for w in plain)

"""Unbundled Chapters in the wanted list and its storage (mangalist.unbundled, mangalist.wanted): off everywhere nothing
changes; on, a volume series is listed under its missing volumes AND its missing chapters. The made-up library of
``library.py``."""

from __future__ import annotations

import pytest

from mangalist import store, wanted
from mangalist.gui.shell import GROUP_CHAPTERS, GROUP_VOLUMES
from mangalist.unbundled import META_PREFIX, UnbundledChapters, UnbundledFolders, unbundled_ids

from ..states.helpers import TODAY
from .library import build_library


@pytest.fixture
def lib(tmp_path):
    store.reset_stores()
    return build_library(tmp_path / "lib")


def _keyed(lib, items):
    rev = {v: k for k, v in lib.folders.items()}
    return {(rev[w.folder], w.group): w for w in items}


def _sid(lib, key):
    return lib.db.series_for_folder(lib.folders[key]).id


# --- storage ---------------------------------------------------------------------------------------------------------


def test_off_by_default_and_switched_per_series_in_meta(lib):
    setting = UnbundledChapters(lib.db)
    quest, gaps = _sid(lib, "Manga/Example Quest"), _sid(lib, "Manga/Volume Gaps")
    assert setting.series_ids() == set() and not setting.is_on(quest)
    assert setting.set([quest, gaps], True) == 2
    assert setting.series_ids() == {quest, gaps} and setting.is_on(quest)
    assert setting.set([quest], True) == 0                          # already on
    assert lib.db.get_meta(f"{META_PREFIX}{quest}") is not None     # one meta row per series, no schema change
    assert setting.set([quest], False) == 1
    assert setting.series_ids() == {gaps} and lib.db.get_meta(f"{META_PREFIX}{quest}") is None
    assert not setting.is_on(None)


def test_a_stray_or_cleared_meta_row_is_not_a_series():
    store.reset_stores()
    db = store.get_store()
    db.set_meta(f"{META_PREFIX}abc", "{}")
    db.set_meta(f"{META_PREFIX}7", None)                            # cleared the way other meta values are
    assert unbundled_ids(db) == set()


def test_an_unreadable_setting_reads_as_off(caplog):
    class Broken:
        def connect(self):
            raise RuntimeError("database is locked")

    assert unbundled_ids(Broken()) == set()
    assert "could not be read" in caplog.text


def test_the_lists_view_by_folder(lib):
    view = UnbundledFolders(lib.db)
    quest, gaps = lib.folders["Manga/Example Quest"], lib.folders["Manga/Volume Gaps"]
    assert not view.is_on(quest) and not view.all_on([quest])
    assert view.set([quest, gaps, "/not/a/library/folder"], True) == 2
    assert view.is_on(quest) and view.all_on([quest, gaps]) and not view.all_on([])
    assert view.set([gaps], False) == 1 and not view.all_on([quest, gaps])
    assert view.set(["/not/a/library/folder"], True) == 0
    fresh = UnbundledFolders(lib.db)                                # read back from the database
    assert fresh.is_on(quest) and not fresh.is_on(gaps)


# --- the wanted list -----------------------------------------------------------------------------------------------


def test_off_everywhere_nothing_changes(lib):
    before = wanted.wanted_list(lib.db, today=TODAY)
    UnbundledChapters(lib.db).set([_sid(lib, "Manga/Example Quest")], True)
    UnbundledChapters(lib.db).set([_sid(lib, "Manga/Example Quest")], False)
    assert wanted.wanted_list(lib.db, today=TODAY) == before


def test_on_a_volume_series_is_listed_for_its_volumes_and_its_chapters(lib):
    before = _keyed(lib, wanted.wanted_list(lib.db, today=TODAY))
    UnbundledChapters(lib.db).set([_sid(lib, "Manga/Example Quest")], True)
    views = {v.row.id: v for v in wanted.library_states(lib.db, today=TODAY)}
    assert views[_sid(lib, "Manga/Example Quest")].state.unbundled
    after = _keyed(lib, wanted.wanted_list(lib.db, today=TODAY))
    vols = after[("Manga/Example Quest", GROUP_VOLUMES)]
    chs = after[("Manga/Example Quest", GROUP_CHAPTERS)]
    assert vols == before[("Manga/Example Quest", GROUP_VOLUMES)]  # the volume side is unchanged: 2-3
    assert chs.missing == tuple(str(n) for n in range(25, 42))      # after volume 3 (a missing one, ends 24.5)
    assert chs.gaps == "Ch. 25-41" and chs.series_id == _sid(lib, "Manga/Example Quest")
    # every other series is as before
    assert {k: v for k, v in after.items() if k[0] != "Manga/Example Quest"} == \
        {k: v for k, v in before.items() if k[0] != "Manga/Example Quest"}


def test_on_a_series_whose_volume_list_maps_no_chapters_wants_nothing_more(lib):
    before = _keyed(lib, wanted.wanted_list(lib.db, today=TODAY))
    UnbundledChapters(lib.db).set([_sid(lib, "Manga/Volume Gaps")], True)      # its list has no chapter ranges
    assert _keyed(lib, wanted.wanted_list(lib.db, today=TODAY)) == before


# --- the first chapter to get (owner, 2026-10-15, question 14) -----------------------------------------------------


def test_the_first_chapter_to_get_is_stored_with_the_setting_and_forgotten_with_it(lib):
    from decimal import Decimal

    from mangalist.unbundled import UnbundledChoice, unbundled_choices

    setting = UnbundledChapters(lib.db)
    gaps = _sid(lib, "Manga/Volume Gaps")
    assert setting.set_first_chapter(gaps, 40, 5) is False          # off: nothing stored
    setting.set([gaps], True)
    assert setting.choice(gaps) == UnbundledChoice() and unbundled_choices(lib.db) == {gaps: UnbundledChoice()}
    assert setting.set_first_chapter(gaps, "40", "5") is True
    assert setting.choice(gaps) == UnbundledChoice(Decimal(40), Decimal(5))
    assert "on_since" in lib.db.get_meta(f"{META_PREFIX}{gaps}")      # the switch time is kept
    with pytest.raises(ValueError):
        setting.set_first_chapter(gaps, "zero", 5)
    setting.set([gaps], False)
    setting.set([gaps], True)
    assert setting.choice(gaps) == UnbundledChoice()                 # asked again after off / on
    assert setting.choice(None) is None and setting.choice(10_000) is None


def test_an_unreadable_value_reads_as_on_without_an_answer(caplog):
    from mangalist.unbundled import UnbundledChoice, choice_of

    assert choice_of("{not json") == UnbundledChoice() and "unreadable" in caplog.text
    assert choice_of('{"first_chapter": "-3", "for_volume": "2"}') == UnbundledChoice()
    assert choice_of('["a list"]') == UnbundledChoice()


def test_the_wanted_list_asks_for_the_chapters_from_the_answer(lib):
    sid = _sid(lib, "Manga/Volume Gaps")                             # volumes 1, 3, 5 held; the list maps none
    view = UnbundledFolders(lib.db)
    folder = lib.folders["Manga/Volume Gaps"]
    before = _keyed(lib, wanted.wanted_list(lib.db, today=TODAY))
    view.set([folder], True)
    states = {v.row.id: v.state for v in wanted.library_states(lib.db, today=TODAY)}
    assert states[sid].unbundled_ask.needed and states[sid].unbundled_ask.volume == "5"
    assert view.choice(folder) is not None and not view.choice(folder).first_chapter
    assert view.set_first_chapter(folder, 40, "5") and view.choice(folder).first_chapter == 40
    after = _keyed(lib, wanted.wanted_list(lib.db, today=TODAY))
    assert after[("Manga/Volume Gaps", GROUP_CHAPTERS)].missing == ("40", "41")
    assert {k: v for k, v in after.items() if k[0] != "Manga/Volume Gaps"} == \
        {k: v for k, v in before.items() if k[0] != "Manga/Volume Gaps"}
    assert not view.set_first_chapter("/not/a/library/folder", 3, 1)

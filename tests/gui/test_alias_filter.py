"""The List's and the Download tab's filter boxes find a series by another title of its record and by creator (offscreen
Qt, made-up series and names, no network): the "aka" line under the title, the ``creator:`` field and a creator's other
names, a series without data never hidden, and the work a keystroke does over 700 rows."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402

from mangalist import alias_search as alias  # noqa: E402
from mangalist import creator_names as cn  # noqa: E402
from mangalist.gui import download_rules as dr  # noqa: E402
from mangalist.gui.main_window import _SortProxy  # noqa: E402
from mangalist.gui.shell import GROUP_VOLUMES, WantedSeries  # noqa: E402
from mangalist.gui.table_model import COL_TITLE, SUBTITLE_ROLE, MangaTableModel  # noqa: E402
from mangalist.knowledge import LINK_CONFIRMED, SOURCE_MANGAPIXER, SeriesKnowledge  # noqa: E402
from mangalist.models import MangaEntry  # noqa: E402

from ..perf.fixture_library import N_SERIES, build  # noqa: E402
from .conftest import qapp  # noqa: E402,F401


def entry(title, english=None, mu_id=None, associated=(), mu_title=None):
    e = MangaEntry(folder=Path("/lib") / title, title=title, english_title=english)
    e.mu_id, e.mu_title, e.mu_associated = mu_id, mu_title, list(associated)
    return e


def entries():
    return [
        entry("Dungeon Meshi", mu_id=11, mu_title="Dungeon Meshi", associated=["Delicious in Dungeon", "ダンジョン飯"]),
        entry("Monster", mu_id=12, mu_title="Monster", associated=["Monsutā"]),
        entry("Bare Folder"),                                       # nothing known about it
        entry("Pluto", english="Pluto: Urasawa x Tezuka", mu_id=13, mu_title="Pluto"),
    ]


INDEX = alias.CreatorIndex(
    {"11": {"a": [[1, "Ryoko Kui", "author"]]},
     "12": {"a": [[7, "Naoki Urasawa", "author"], [7, "Naoki Urasawa", "artist"]]},
     "13": {"a": [[7, "Naoki Urasawa", "author"], [9, "Osamu Tezuka", "artist"]]}},
    {"7": {"n": ["Naoki Urasawa", "Urasawa Naoki", "浦沢直樹"]}, "1": {"n": ["Ryoko Kui", "九井諒子"]}})


@pytest.fixture
def made(qapp):
    model = MangaTableModel(entries())
    model.set_creator_index(INDEX)
    proxy = _SortProxy()
    proxy.setSourceModel(model)
    return model, proxy


def titles(proxy):
    return sorted(proxy.data(proxy.index(r, COL_TITLE)) for r in range(proxy.rowCount()))


def aka(proxy, title):
    for r in range(proxy.rowCount()):
        index = proxy.index(r, COL_TITLE)
        if proxy.data(index) == title:
            return proxy.data(index, SUBTITLE_ROLE)
    raise AssertionError(f"{title} is not shown")


# --- alternative titles --------------------------------------------------------------------------------------------


def test_a_series_is_found_by_any_alternative_title_and_says_which(made):
    _model, proxy = made
    proxy.set_text_filter("delicious")
    assert titles(proxy) == ["Dungeon Meshi"]
    assert aka(proxy, "Dungeon Meshi") == "aka Delicious in Dungeon"
    proxy.set_text_filter("ダンジョン")
    assert titles(proxy) == ["Dungeon Meshi"] and aka(proxy, "Dungeon Meshi") == "aka ダンジョン飯"


def test_a_series_found_by_its_own_title_shows_the_matched_series_title_as_before(made):
    _model, proxy = made
    proxy.set_text_filter("meshi")
    assert titles(proxy) == ["Dungeon Meshi"]
    assert aka(proxy, "Dungeon Meshi") == "Dungeon Meshi"           # (the delegate leaves out a repeat of the folder's)
    proxy.set_text_filter("")
    assert aka(proxy, "Pluto") == "Pluto"                            # no filter, no hint


def test_the_english_title_and_the_kind_are_still_found_without_a_hint(made):
    _model, proxy = made
    proxy.set_text_filter("tezuka")
    assert titles(proxy) == ["Pluto"] and not proxy.why_shown(proxy.mapToSource(proxy.index(0, 0)).row())


def test_the_matched_series_title_is_an_alternative_title_too(made):
    model, proxy = made
    model._entries[1].folder = Path("/lib/Monster Folder")
    model._entries[1].title = "Monster Folder"
    model._entries[1].mu_title = "Naoki's Monster"
    model.set_creator_index(INDEX)                                    # (forgets what the model kept per row)
    proxy.set_text_filter("naoki's")
    assert titles(proxy) == ["Monster Folder"]
    assert aka(proxy, "Monster Folder") == "aka Naoki's Monster"


def test_a_series_nothing_is_known_about_is_never_hidden_by_it(made):
    _model, proxy = made
    proxy.set_text_filter("bare")
    assert titles(proxy) == ["Bare Folder"]
    proxy.set_text_filter("delicious")
    assert "Bare Folder" not in titles(proxy)                        # (it simply has no such name)
    proxy.set_text_filter("")
    assert len(titles(proxy)) == 4


def test_fewer_than_three_letters_look_at_the_shown_titles_only(made):
    _model, proxy = made
    proxy.set_text_filter("mo")                                      # "Monsutā" is an alt title: not found by 2 letters
    assert titles(proxy) == ["Monster"]                              # (the title: two letters are enough there)
    assert aka(proxy, "Monster") == "Monster"
    proxy.set_text_filter("ダン")
    assert titles(proxy) == []
    proxy.set_text_filter("monsu")
    assert titles(proxy) == ["Monster"] and aka(proxy, "Monster") == "aka Monsutā"


def test_a_mangapixer_records_titles_are_found_once_the_state_is_known(made):
    model, proxy = made
    record = SeriesKnowledge(source=SOURCE_MANGAPIXER, link_state=LINK_CONFIRMED, mu_id="12", title="Monster",
                             alt_titles=("Perfect Edition Monster",))
    model.set_state_providers(knowledge_for=lambda e: record if e.title == "Monster" else None)
    proxy.set_text_filter("perfect edition")
    assert titles(proxy) == []                                       # not computed here: a keystroke never works out a state
    assert model.evaluations == 0
    model.state_at(1)                                                # the background computes it
    proxy.refresh_scope()
    assert titles(proxy) == ["Monster"] and aka(proxy, "Monster") == "aka Perfect Edition Monster"


def test_nothing_is_computed_or_asked_on_a_keystroke(made):
    model, proxy = made
    for text in ("delicious", "creator:urasawa", "monster", "creator:kui meshi", "zzz"):
        proxy.set_text_filter(text)
    assert model.evaluations == 0


# --- creators ------------------------------------------------------------------------------------------------------


def test_creator_finds_the_series_of_a_writer_or_artist(made):
    _model, proxy = made
    proxy.set_text_filter("creator:Urasawa")
    assert titles(proxy) == ["Monster", "Pluto"]
    assert aka(proxy, "Monster") == "by Naoki Urasawa"
    proxy.set_text_filter('creator:"Naoki Urasawa"')
    assert titles(proxy) == ["Monster", "Pluto"]
    proxy.set_text_filter("creator:tezuka")
    assert titles(proxy) == ["Pluto"] and aka(proxy, "Pluto") == "by Osamu Tezuka"


def test_creator_finds_a_series_by_the_authors_other_name(made):
    _model, proxy = made
    proxy.set_text_filter("creator:浦沢直樹")
    assert titles(proxy) == ["Monster", "Pluto"]
    assert aka(proxy, "Monster") == "by Naoki Urasawa (aka 浦沢直樹)"
    proxy.set_text_filter("creator:kui")
    assert titles(proxy) == ["Dungeon Meshi"]
    proxy.set_text_filter('creator:"URASAWA, Naoki"')
    assert titles(proxy) == ["Monster", "Pluto"]


def test_creator_combines_with_free_text_and_other_creators(made):
    _model, proxy = made
    proxy.set_text_filter("creator:Urasawa pluto")
    assert titles(proxy) == ["Pluto"]
    proxy.set_text_filter("creator:Urasawa creator:Tezuka")
    assert titles(proxy) == ["Pluto"]
    proxy.set_text_filter("creator:Urasawa creator:Kui")
    assert titles(proxy) == []
    proxy.set_text_filter("creator:Urasawa delicious")
    assert titles(proxy) == []


def test_a_series_without_known_creators_is_not_a_creator_match_but_still_a_title_match(made):
    _model, proxy = made
    proxy.set_text_filter("creator:Urasawa")
    assert "Bare Folder" not in titles(proxy)
    proxy.set_text_filter("bare")
    assert titles(proxy) == ["Bare Folder"]


def test_an_unknown_prefix_is_text_and_the_old_filter_text_is_unchanged(made):
    _model, proxy = made
    proxy.set_text_filter("Re:Zero")
    assert titles(proxy) == []
    assert proxy.text_filter() == "re:zero"
    proxy.set_text_filter("  MONSTER ")
    assert titles(proxy) == ["Monster"]


def test_a_later_creator_index_is_used_at_once(made):
    model, proxy = made
    model.set_creator_index(alias.CreatorIndex())
    proxy.set_text_filter("creator:Urasawa")
    assert titles(proxy) == []
    model.set_creator_index(INDEX)
    proxy.refresh_scope()
    assert titles(proxy) == ["Monster", "Pluto"]


def test_the_boxes_say_what_they_search():
    assert "creator" in alias.SEARCH_PLACEHOLDER and "other titles" in alias.SEARCH_PLACEHOLDER
    assert "creator:Urasawa" in alias.SEARCH_TIP and "aka" in alias.SEARCH_TIP


def test_the_lists_filter_box_is_labelled(qapp):
    from mangalist.gui.list_tab import ListTab

    tab = ListTab()
    assert tab.search.placeholderText() == alias.SEARCH_PLACEHOLDER and tab.search.toolTip() == alias.SEARCH_TIP
    tab.deleteLater()


# --- the work a keystroke does -------------------------------------------------------------------------------------


def test_a_keystroke_over_700_series_with_alternative_titles_and_creators_stays_cheap(qapp):
    entries = build().entries
    for i, e in enumerate(entries):
        e.mu_id = 20_000 + i
        e.mu_associated = [f"{e.title} Alias {n}" for n in range(8)] + ["Seven Seas Edition"]
    index = alias.CreatorIndex({str(e.mu_id): {"a": [[i, f"Writer {i}", "author"], [i + 1, f"Artist {i}", "artist"]]}
                                for i, e in enumerate(entries)},
                               {str(i): {"n": [f"Writer {i}", f"Pen Name {i}"]} for i in range(len(entries) + 1)})
    model = MangaTableModel(entries)
    model.set_creator_index(index)
    proxy = _SortProxy()
    proxy.setSourceModel(model)
    assert model.rowCount() == N_SERIES
    proxy.set_text_filter("seven seas")                              # builds each row's names once
    assert proxy.rowCount() == N_SERIES
    started = time.perf_counter()
    for text in ("nothing like it", "alias 7", "creator:pen name 30", "creator:writer 5 alias", "seven"):
        proxy.set_text_filter(text)
    per_keystroke = (time.perf_counter() - started) / 5
    assert proxy.rowCount() == N_SERIES
    assert model.evaluations == 0
    assert per_keystroke < 0.5, f"{per_keystroke:.3f} s per keystroke over {N_SERIES} series"
    # a row's names are built once, not per keystroke
    assert len(model._aliases) == N_SERIES


# --- the Download tab's filter ---------------------------------------------------------------------------------------


def wanted(title, **kw):
    kw.setdefault("titles", (title,))
    return WantedSeries(1, f"/lib/{title}", title, GROUP_VOLUMES, "Vol. 2", missing=("2",), held=("1",), **kw)


def test_the_to_get_filter_finds_a_series_by_any_alternative_title():
    many = tuple(f"Filler Title {n}" for n in range(15)) + ("Deep Alias",)        # past the 10 names a search sends
    series = wanted("Alpha", alt_titles=many, mu_id="12")
    assert dr.matches_filter(series, "deep alias")
    assert dr.filter_why(series, "deep alias") == ("aka Deep Alias",)
    assert dr.filter_why(series, "alpha") == ()
    assert dr.filter_why(series, "") == ()
    assert not dr.matches_filter(series, "unrelated")
    assert not dr.matches_filter(series, "de")                          # (two letters look in the names only)
    assert dr.matches_filter(wanted("Alpha", titles=("Alpha", "Be")), "be")


def test_the_to_get_filter_knows_creators():
    series = wanted("Monster", mu_id="12")
    assert not dr.matches_filter(series, "creator:Urasawa")             # no creators known yet
    assert dr.matches_filter(series, "creator:Urasawa", INDEX)
    assert dr.matches_filter(series, "creator:浦沢直樹 monster", INDEX)
    assert not dr.matches_filter(series, "creator:Tezuka", INDEX)
    assert dr.filter_why(series, "creator:浦沢直樹", INDEX) == ("by Naoki Urasawa (aka 浦沢直樹)",)
    groups = dict(dr.grouped([series, wanted("Other", mu_id="13"), wanted("Bare")], "creator:Urasawa", creators=INDEX))
    assert [s.title for s in groups[GROUP_VOLUMES]] == ["Monster", "Other"]


def test_the_to_get_list_says_why_a_series_is_shown(qapp):
    from mangalist.gui.download_tab import DownloadTab
    from mangalist.gui.download_widgets import ROLE_SUB

    from .conftest import FakeBackend

    tab = DownloadTab(FakeBackend(), confirm=lambda p, t: True, refresh_ms=0, search_delay_ms=0)
    try:
        tab.set_creator_index(INDEX)
        tab.set_wanted([wanted("Monster", mu_id="12", alt_titles=("Monsutā",)), wanted("Bare Folder")])

        def subs():
            return [tab.tree.topLevelItem(i).data(0, ROLE_SUB) for i in range(tab.tree.topLevelItemCount())
                    if tab.tree.topLevelItem(i).data(0, ROLE_SUB)]

        assert subs() == ["Vol. 2", "Vol. 2"]
        tab.filter_edit.setText("monsu")
        assert subs() == ["Vol. 2  ·  aka Monsutā"]
        tab.filter_edit.setText("creator:urasawa")
        assert subs() == ["Vol. 2  ·  by Naoki Urasawa"]
        tab.filter_edit.setText("monster")
        assert subs() == ["Vol. 2"]
        assert tab.filter_edit.placeholderText() == alias.SEARCH_PLACEHOLDER
    finally:
        tab.stop()
        tab.deleteLater()


def test_the_wanted_list_carries_the_names_the_filter_needs():
    from types import SimpleNamespace

    from mangalist.gui.list_text import wanted_series

    record = SeriesKnowledge(source=SOURCE_MANGAPIXER, link_state=LINK_CONFIRMED, mu_id="12", title="Monster",
                             english_title="Monster (EN)", alt_titles=("Monsutā", "Monster"))
    state = SimpleNamespace(gaps=True, missing_volumes=("2",), missing_volumes_text="2", missing_chapters=(),
                            upgrade_volumes=())
    got = wanted_series(folder="/lib/Monster", title="Monster", english_title=None, state=state, knowledge=record,
                        held=("1",), series_id=1, volumes=None)
    assert got and got[0].mu_id is None and got[0].creators == () and got[0].alt_titles == ("Monster", "Monster (EN)", "Monsutā")


# --- MangaPixer's creators and MangaUpdates' --------------------------------------------------------------------------


def exported(*pairs):
    return SeriesKnowledge(source=SOURCE_MANGAPIXER, link_state=LINK_CONFIRMED, mu_id="12", title="Monster",
                           creators=tuple(pairs))


def test_a_series_mangapixer_knows_takes_its_creators_from_the_export(made):
    model, proxy = made
    record = exported(("Exported Writer", ("Pen Name",)))
    model.set_state_providers(knowledge_for=lambda e: record if e.title == "Monster" else None)
    model.state_at(1)
    proxy.set_text_filter('creator:"pen name"')
    assert titles(proxy) == ["Monster"] and aka(proxy, "Monster") == "by Exported Writer (aka Pen Name)"
    proxy.set_text_filter("creator:Urasawa")             # (what MangaUpdates said of series 12 is not used for it)
    assert titles(proxy) == ["Pluto"]


def test_a_series_mangapixer_knows_without_exported_creators_has_none(made):
    model, proxy = made
    model.set_state_providers(knowledge_for=lambda e: exported() if e.title == "Monster" else None)
    model.state_at(1)
    record_old = SeriesKnowledge(source=SOURCE_MANGAPIXER, link_state=LINK_CONFIRMED, mu_id="12", title="Monster")
    model.set_state_providers(knowledge_for=lambda e: record_old if e.title == "Monster" else None)
    model.state_at(1)
    proxy.set_text_filter("creator:Urasawa")
    assert titles(proxy) == ["Pluto"]                    # still found by its titles
    proxy.set_text_filter("monster")
    assert titles(proxy) == ["Monster"]


def test_the_to_get_list_takes_mangapixers_creators_for_its_series():
    from types import SimpleNamespace

    from mangalist.gui.list_text import wanted_series

    state = SimpleNamespace(gaps=True, missing_volumes=("2",), missing_volumes_text="2", missing_chapters=(),
                            upgrade_volumes=())

    def make_wanted(knowledge):
        return wanted_series(folder="/lib/Monster", title="Monster", english_title=None, state=state,
                             knowledge=knowledge, held=("1",), series_id=1, volumes=None)[0]

    known = make_wanted(exported(("Exported Writer", ("Pen Name",))))
    assert known.mu_id is None and [c.name for c in known.creators] == ["Exported Writer"]
    assert dr.matches_filter(known, "creator:pen", INDEX) and not dr.matches_filter(known, "creator:Urasawa", INDEX)
    older = make_wanted(SeriesKnowledge(source=SOURCE_MANGAPIXER, link_state=LINK_CONFIRMED, mu_id="12", title="Monster"))
    assert older.creators == () and not dr.matches_filter(older, "creator:Urasawa", INDEX)
    from mangalist.knowledge import SOURCE_OWN_MATCHER

    own = make_wanted(SeriesKnowledge(source=SOURCE_OWN_MATCHER, link_state=LINK_CONFIRMED, mu_id="12", title="Monster"))
    assert own.creators is None and own.mu_id == "12" and dr.matches_filter(own, "creator:Urasawa", INDEX)

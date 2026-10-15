"""Unbundled Chapters' "first chapter to get" in the main window (owner, 2026-10-15, questions 14 and 17; offscreen Qt,
made-up series in a temporary library): switching the setting on for a series whose volume list does not say where its
newest volume ends asks once, the answer is stored and the states follow, the row menu's "First chapter to get…" changes
it, a selection is not asked (the status line says how many wait), and More filters lists the series with the setting."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from mangalist.gui.list_tab import MORE_FILTERS  # noqa: E402
from mangalist.gui.list_text import UNBUNDLED_FILTER, UNBUNDLED_FIRST_MENU, UNBUNDLED_MENU  # noqa: E402
from mangalist.gui.table_model import COL_TITLE  # noqa: E402
from mangalist.knowledge import from_mangapixer_item  # noqa: E402
from mangalist.models import FileHit, MangaEntry  # noqa: E402
from mangalist.states import ATTENTION_UNBUNDLED_START, GAP_CHAPTER, InventorySnapshot, UnbundledAsk  # noqa: E402
from mangalist.unbundled import UnbundledChapters  # noqa: E402

from ..states.helpers import TODAY, item, vol  # noqa: E402
from .conftest import FakeBackend, qapp  # noqa: E402,F401
from .test_auto_modes_window import _sync  # noqa: E402

TITLES = ("Alpha Saga", "Beta Days", "Gamma Mapped")
# volume 1 maps chapters 1-8, volume 2 is out without a chapter range; latest chapter 41
UNMAPPED = {"source": "merged", "fetchedAt": "2026-10-02T12:00:00.000Z",
            "items": [vol("1", "1", "8", "2025-01-01", "released"), vol("2", None, None, "2025-05-01", "released")]}


@pytest.fixture
def win(qapp, monkeypatch, tmp_path):
    from mangalist.gui import background as bg
    from mangalist.gui import main_window as mw

    base = tmp_path / "lib"
    folders = {t: base / t for t in TITLES}
    for f in folders.values():
        f.mkdir(parents=True)
    monkeypatch.setenv("MANGALIST_DOWNLOADS", "1")
    backend = FakeBackend(series_ids={})
    monkeypatch.setattr(mw.MainWindow, "_make_volumes_backend", lambda self: backend)
    import importlib
    import pkgutil

    import mangalist.gui as gui

    for module in pkgutil.iter_modules(gui.__path__):                  # see test_auto_modes_window's make_window
        importlib.import_module(f"mangalist.gui.{module.name}")
    with monkeypatch.context() as patch:
        patch.setattr(bg, "start_call", _sync)
        w = mw.MainWindow()
    root = w._db.add_root(str(base), "Manga")
    for t in TITLES:
        backend.series_ids[str(folders[t])] = w._db.ensure_series(root.id, t).id
    entries = [MangaEntry(folder=f, title=t, english_title=None,
                          files=[FileHit(f / f"{t} v0{n}.cbz", 1000, 0, has_volume=True) for n in (1, 2)])
               for t, f in folders.items()]
    w._model.set_entries(entries)

    def knowledge(e):
        return from_mangapixer_item(item(trail=[e.title]) if e.title == "Gamma Mapped"
                                    else item(trail=[e.title], volumes=UNMAPPED))

    w._model.set_state_providers(knowledge_for=knowledge,
                                 inventory_for=lambda e: InventorySnapshot(held_volumes=["1", "2"]),
                                 needs_kind_for=lambda e: False, today=TODAY)
    w._refresh_derived()
    yield w
    w.close()
    w.deleteLater()


@pytest.fixture
def answers(monkeypatch):
    """The prompt's answers, in order (None: Ask later); what it was asked is recorded."""
    from mangalist.gui import unbundled_dialog

    given, asked = [], []

    def fake(parent, title, ask):
        asked.append((title, ask))
        return given.pop(0) if given else None

    monkeypatch.setattr(unbundled_dialog, "ask_first_chapter", fake)
    return given, asked


def _row(win, title):
    return next(r for r in range(win._model.rowCount()) if win._model.entry_at(r).title == title)


def _menu(win, *titles, pick=None):
    """Select *titles*, open the row menu and choose the entry *pick* (None: nothing); the menu's entry texts."""
    from PySide6.QtCore import QItemSelectionModel

    sel = win._table.selectionModel()
    sel.clearSelection()
    for title in titles:
        idx = win._proxy.mapFromSource(win._model.index(_row(win, title), COL_TITLE))
        sel.select(idx, QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
    seen = []

    def answer(menu, pos):
        seen.extend(a.text() for a in menu.actions())
        return next((a for a in menu.actions() if a.text() == pick), None) if pick else None

    win._exec_menu = answer
    idx = win._proxy.mapFromSource(win._model.index(_row(win, titles[0]), COL_TITLE))
    win._on_context_menu(win._table.visualRect(idx).center())
    return seen


def _chapter_gaps(win, title):
    return [(g.start, g.end) for g in win._model.state_at(_row(win, title)).gaps if g.kind == GAP_CHAPTER]


def test_switching_it_on_asks_once_and_the_answer_counts(win, answers):
    given, asked = answers
    given.append(17)
    _menu(win, "Alpha Saga", pick=UNBUNDLED_MENU)
    (title, ask), = asked
    assert title == "Alpha Saga" and (ask.volume, ask.mapped_volume, ask.mapped_end, ask.suggestion) == ("2", "1", "8", 17)
    assert _chapter_gaps(win, "Alpha Saga") == [("17", "41")]
    assert win._status_label.text() == "Unbundled chapters from chapter 17 (the first chapter to get after volume 2)"
    sid = win._db.series_for_folder(win._model.entry_at(_row(win, "Alpha Saga")).folder).id
    assert UnbundledChapters(win._db).choice(sid).first_chapter == 17
    # editable later from the row menu; switching it on again does not ask
    given.append(20)
    assert UNBUNDLED_FIRST_MENU in _menu(win, "Alpha Saga", pick=UNBUNDLED_FIRST_MENU)
    assert len(asked) == 2 and asked[1][1].current == "17"
    assert _chapter_gaps(win, "Alpha Saga") == [("20", "41")]


def test_ask_later_compares_nothing_more_and_the_series_needs_attention(win, answers):
    _menu(win, "Alpha Saga", pick=UNBUNDLED_MENU)
    st = win._model.state_at(_row(win, "Alpha Saga"))
    assert st.unbundled and not _chapter_gaps(win, "Alpha Saga")
    assert ATTENTION_UNBUNDLED_START in st.needs_attention
    assert "not compared until you give one" in win._status_label.text()


def test_a_series_whose_list_maps_its_volumes_is_never_asked(win, answers):
    _, asked = answers
    _menu(win, "Gamma Mapped", pick=UNBUNDLED_MENU)
    assert not asked and _chapter_gaps(win, "Gamma Mapped") == [("25", "41")]     # after volume 3 (the gap)
    assert UNBUNDLED_FIRST_MENU not in _menu(win, "Gamma Mapped")


def test_a_selection_is_not_asked_the_status_line_says_how_many_wait(win, answers):
    _, asked = answers
    _menu(win, "Alpha Saga", "Beta Days", "Gamma Mapped", pick=UNBUNDLED_MENU)
    assert not asked
    text = win._status_label.text()
    assert text.startswith("Unbundled chapters on for 3 series") and " - 2 series need the first chapter to get" in text
    assert UNBUNDLED_FIRST_MENU not in _menu(win, "Alpha Saga", "Beta Days")       # one series at a time


def test_more_filters_lists_the_series_with_the_setting(win, answers):
    assert ("unbundled", UNBUNDLED_FILTER) in MORE_FILTERS
    _menu(win, "Alpha Saga", "Gamma Mapped", pick=UNBUNDLED_MENU)
    win._proxy.set_state_filter("unbundled")
    shown = {win._model.entry_at(win._proxy.mapToSource(win._proxy.index(r, 0)).row()).title
             for r in range(win._proxy.rowCount())}
    assert shown == {"Alpha Saga", "Gamma Mapped"}
    win._proxy.set_state_filter(None)


def test_the_question_itself(qapp):
    from mangalist.gui.unbundled_dialog import FirstChapterDialog

    ask = UnbundledAsk(volume="7", mapped_volume="5", mapped_end="44", suggestion=62)
    dialog = FirstChapterDialog("Alpha Saga", ask)
    assert dialog.spin.value() == 62 and "volume 7" in dialog.question.text() and "chapter 44" in dialog.question.text()
    dialog.spin.setValue(61)
    dialog.use_button.click()
    assert dialog.answer == 61
    again = FirstChapterDialog("Alpha Saga", UnbundledAsk(volume="8", mapped_volume="5", mapped_end="44",
                                                          suggestion=70, given_for="7"))
    assert "arrived since" in again.question.text()
    again.later_button.click()
    assert again.answer is None
    dialog.deleteLater()
    again.deleteLater()

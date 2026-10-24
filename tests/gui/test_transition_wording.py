"""The Suwayomi -> built-in extensions transition, wording (A2, A4; 2026-10-24): every screen that names the program that
downloads chapters follows the chapter backend in force - the chapters panel, the Download tab (the group's note, the row
menu's tooltips, why a series cannot be looked up), the Found view and Add a series. Each text is checked under BOTH
backends, and a panel that lives through a switch changes its words with it. Offscreen Qt, fake backends."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtWidgets import QAbstractButton, QLabel, QMenu  # noqa: E402

from dataclasses import replace  # noqa: E402

from mangalist.downloads import chapters as chm  # noqa: E402
from mangalist.downloads.contracts import Placement  # noqa: E402
from mangalist.gui import chapter_words as cw  # noqa: E402
from mangalist.gui import chapters_panel as cp  # noqa: E402
from mangalist.gui import download_tab as dt  # noqa: E402
from mangalist.gui.chapters_panel import ChaptersPanel  # noqa: E402
from mangalist.gui.download_tab import DownloadTab  # noqa: E402
from mangalist.gui.found_view import PART_ACTION, PART_REASON, FoundView  # noqa: E402
from mangalist.gui.shell import GROUP_CHAPTERS, WantedSeries  # noqa: E402

from ..downloads.suwayomi_fakes import MANGA, MANGADEX, WEEB  # noqa: E402
from .chapter_fakes import FOLDER, WEEB_MANGA, SwitchableChapterBackend, lookup_for  # noqa: E402
from .conftest import qapp, wait_until  # noqa: E402,F401
from .finding_fakes import FakeFindingBackend, find_run, finding  # noqa: E402

MISSING = ("41", "42", "43", "44")
BOTH = ("suwayomi", "extensions")
CHAPTER_SERIES = WantedSeries(4, FOLDER, "Example Webcomic", GROUP_CHAPTERS, "Ch. 41-44", missing=MISSING,
                              held=("40",), titles=("Example Webcomic",), findable=False, reason="needs Suwayomi")


def shown_texts(widget) -> str:
    """Everything the widget shows or promises on hover: labels, buttons, tooltips, table headers and cells."""
    out = []
    for w in widget.findChildren(QLabel):
        out += [w.text(), w.toolTip()]
    for w in widget.findChildren(QAbstractButton):
        out += [w.text(), w.toolTip()]
    from PySide6.QtWidgets import QTableWidget

    for t in widget.findChildren(QTableWidget):
        out += [t.horizontalHeaderItem(c).text() for c in range(t.columnCount()) if t.horizontalHeaderItem(c)]
        out += [t.item(r, c).text() for r in range(t.rowCount()) for c in range(t.columnCount()) if t.item(r, c)]
    return "\n".join(out)


def assert_names(text: str, key: str) -> None:
    if key == "suwayomi":
        assert "extension host" not in text
    else:
        assert "Suwayomi" not in text, text


# --- the helper ---------------------------------------------------------------------------------------------------

def test_the_helper_follows_the_backend_and_falls_back_to_suwayomi():
    class Old:
        pass

    class Broken:
        def chapter_backend(self):
            raise RuntimeError("no")

    assert cw.program(Old()) == "Suwayomi" and cw.label_of(None) == "Suwayomi" and cw.program(Broken()) == "Suwayomi"
    ext = SwitchableChapterBackend("extensions")
    assert cw.program(ext) == "the extension host" and cw.label_of(ext) == "Built-in extensions"
    assert cw.reword(ext, "Send 3 to Suwayomi") == "Send 3 to the extension host"
    assert cw.reword(ext, "Suwayomi could not be asked") == "The extension host could not be asked"
    assert cw.reword(SwitchableChapterBackend(), "Send 3 to Suwayomi") == "Send 3 to Suwayomi"
    # the card was "Suwayomi sources": it is "chapter sources" under either, never "the extension host sources"
    for key in BOTH:
        text = cw.reword(SwitchableChapterBackend(key), "your other Suwayomi sources; Settings > Download sources > "
                                                         "Suwayomi sources")
        assert "chapter sources" in text and "host sources" not in text
    assert cw.reword(ext, "no Suwayomi source is saved for the series yet") == \
        "no chapter source is saved for the series yet"
    assert cw.reword(ext, "Suwayomi · MangaDex (EN)") == "Built-in extensions · MangaDex (EN)"
    assert cw.reword(None, None) is None


# --- the chapters panel (A2) --------------------------------------------------------------------------------------

@pytest.fixture
def panels():
    made = []
    yield made
    for p in made:
        p.stop()
        p.deleteLater()


def make_panel(panels, key="suwayomi", **kw):
    backend = SwitchableChapterBackend(key, **kw)
    asked = []
    panel = ChaptersPanel(backend, confirm=lambda parent, text: asked.append(text) or True)
    panel.resize(1000, 700)
    panels.append(panel)
    return panel, backend, asked


def open_panel(qapp, panel):
    panel.open_series(4, "Example Webcomic", MISSING, ("Example Webcomic",))
    wait_until(qapp, lambda: not panel._calls)


@pytest.mark.parametrize("key", BOTH)
def test_the_chapters_panel_names_the_program_in_use(qapp, panels, key):
    panel, backend, asked = make_panel(panels, key)
    program = cw.program(backend)
    open_panel(qapp, panel)
    assert panel.stack.currentIndex() == cp.PAGE_CHAPTERS
    n = len(panel.picks())
    assert n and panel.btn_send.text() == f"Send {n} to {program}"
    assert panel.note_label.text() == cw.reword(backend, cp.FOOTER_NOTE)
    assert f"once {program} has it, then {program} deletes its own copy" in panel.note_label.text()
    assert program in panel.btn_again.toolTip()
    assert "your other chapter sources" in panel.btn_other.toolTip()
    assert panel.send_blocker() is None
    panel.send_selected()
    assert len(asked) == 1 and f"to {program}?" in asked[0] and f"{program} downloads them" in asked[0]
    wait_until(qapp, lambda: not panel._calls)
    assert panel.status_label.text().startswith("Sent")
    assert_names(shown_texts(panel), key)
    assert_names(asked[0], key)


@pytest.mark.parametrize("key", BOTH)
def test_the_chapters_panel_messages_name_the_program(qapp, panels, key):
    panel, backend, _ = make_panel(panels, key)
    program = cw.program(backend)
    # the lookup's own wait and failure
    backend.lookup_error = "the program did not answer"
    panel.open_series(4, "Example Webcomic", MISSING, ("Example Webcomic",))
    assert panel.message_text.text() == f"Looking the series up in {program}..."
    wait_until(qapp, lambda: not panel._calls)
    assert panel.message_text.text().startswith(cw.reword(backend, "Suwayomi could not be asked: "))
    assert_names(panel.message_text.text(), key)
    # nothing found anywhere: the way out names the card, not a program
    panel.show_lookup(lookup_for(4, MISSING))
    text = panel.message_text.text()
    assert "None of your chapter sources has this series" in text and "Settings > Download sources > Chapter sources" in text
    assert ("install their extensions in Suwayomi" if key == "suwayomi" else
            "Built-in extensions > Extensions") in text
    assert_names(text, key)
    # the series is already in the program's queue
    match = chm.MangaMatch(MANGADEX, MANGA, chm.HOW_MANGADEX)
    look = lookup_for(4, MISSING, match=match)
    look.queued_in_suwayomi = ["41", "42"]
    panel.show_lookup(look)
    assert panel.queued_label.text().startswith(f"Already in {program}'s download queue: ch 41")
    assert_names(panel.queued_label.text(), key)
    # the candidates table
    cands = [chm.MangaMatch(WEEB, WEEB_MANGA, chm.HOW_TITLE)]
    panel.show_lookup(lookup_for(4, MISSING, candidates=cands))
    assert panel.cand_table.horizontalHeaderItem(1).text() == f"Series in {program}".upper()
    # a send that fails and one the program refused
    backend.chapter_send_error = "Suwayomi refused the download"
    panel.show_lookup(look)
    panel.send_selected()
    wait_until(qapp, lambda: not panel._calls)
    assert panel.status_label.text() == cw.reword(backend, "Suwayomi refused the download")
    assert_names(panel.status_label.text(), key)


def test_a_panel_that_lives_through_a_switch_changes_its_words(qapp, panels):
    panel, backend, _ = make_panel(panels, "suwayomi")
    open_panel(qapp, panel)
    n = len(panel.picks())
    assert panel.btn_send.text() == f"Send {n} to Suwayomi"
    backend.key = "extensions"                             # the owner switched in Settings
    open_panel(qapp, panel)
    assert panel.btn_send.text() == f"Send {n} to the extension host"
    assert "the extension host" in panel.note_label.text() and "Suwayomi" not in panel.note_label.text()
    backend.key = "suwayomi"
    panel.show_lookup(panel.lookup)
    assert panel.btn_send.text() == f"Send {n} to Suwayomi" and "Suwayomi deletes its own copy" in panel.note_label.text()


def test_the_confirmation_dialog_is_titled_by_the_program(qapp, panels, monkeypatch):
    titles = []

    class Box:
        Yes, No = 1, 2

        @staticmethod
        def question(parent, title, text, *a):
            titles.append(title)
            return 2

    monkeypatch.setattr(cp, "QMessageBox", Box)
    for key, title in (("suwayomi", "Send to Suwayomi"), ("extensions", "Send to the extension host")):
        backend = SwitchableChapterBackend(key)
        panel = ChaptersPanel(backend)
        panels.append(panel)
        assert panel._confirm(panel, "text") is False
    assert titles == ["Send to Suwayomi", "Send to the extension host"]


# --- the Download tab (A4) ------------------------------------------------------------------------------------------

@pytest.fixture
def tabs():
    made = []
    original = DownloadTab.__init__

    def tracking(self, *a, **kw):
        original(self, *a, **kw)
        made.append(self)

    DownloadTab.__init__ = tracking
    yield made
    DownloadTab.__init__ = original
    for tab in made:
        tab.stop()
        tab.deleteLater()


def make_tab(key="suwayomi", connected=True):
    backend = SwitchableChapterBackend(key, connected=connected, series_ids={FOLDER: 4})
    tab = DownloadTab(backend, confirm=lambda p, t: True, refresh_ms=0, search_delay_ms=0)
    tab.resize(1200, 800)
    tab.set_wanted([CHAPTER_SERIES])
    return tab, backend


def header_note(tab):
    tab.show_group(GROUP_CHAPTERS)
    return tab.tree.topLevelItem(0).data(0, dt.ROLE_ASIDE)


def test_the_missing_chapters_group_names_the_program(qapp, tabs):
    for key, note in (("suwayomi", "Suwayomi"), ("extensions", "Built-in extensions")):
        tab, backend = make_tab(key)
        tabs.append(tab)
        assert header_note(tab) == note
    # not set up (Suwayomi only: the host has a default address): amber "needs ..." - never a stale program
    tab, backend = make_tab("suwayomi", connected=False)
    tabs.append(tab)
    assert header_note(tab) == "needs Suwayomi"
    backend.key = "extensions"
    tab._rebuild()
    assert tab.tree.topLevelItem(0).data(0, dt.ROLE_ASIDE) == "needs the extension host"


def test_the_group_note_follows_a_switch_at_the_next_reload(qapp, tabs):
    tab, backend = make_tab("suwayomi")
    tabs.append(tab)
    assert header_note(tab) == "Suwayomi"
    backend.key = "extensions"
    tab._on_records([])                                     # the hourly / Settings-closed reload of the downloads
    assert tab.tree.topLevelItem(0).data(0, dt.ROLE_ASIDE) == "Built-in extensions"


@pytest.mark.parametrize("key", BOTH)
def test_the_row_menus_tooltips_name_the_program(qapp, tabs, key):
    tab, backend = make_tab(key)
    tabs.append(tab)
    tab.show_group(GROUP_CHAPTERS)
    seen = []

    def grab(menu, pos):
        seen.append([(a.text(), a.toolTip()) for a in menu.actions()])
        return None

    tab._exec_menu = grab
    row = tab._items[FOLDER]
    tab.tree.setCurrentItem(row)
    pos = tab.tree.visualItemRect(row).center()
    tab._on_tree_menu(pos)
    assert seen, "the menu was built"
    tips = "\n".join(t for _a, t in seen[0])
    assert "Check for new chapters" in "\n".join(a for a, _t in seen[0])
    assert_names(tips, key)
    assert ("Suwayomi" in tips) == (key == "suwayomi")


@pytest.mark.parametrize("key", BOTH)
def test_a_series_that_cannot_be_looked_up_says_so_in_the_programs_words(qapp, tabs, key):
    tab, backend = make_tab(key, connected=False)
    tabs.append(tab)
    backend.view = type(backend.view)()                      # no address (Suwayomi); the host fake still says "ready"
    backend.suwayomi_ready = lambda: False
    tab.show_group(GROUP_CHAPTERS)
    tab.select_series(FOLDER, GROUP_CHAPTERS)
    text = tab.releases.message_text.text()
    assert text.startswith("Missing chapters come from ")
    assert_names(text, key)
    tab.check_new_chapters(4, FOLDER, "Example Webcomic", ("Example Webcomic",))
    assert_names(tab.releases.message_text.text(), key)


# --- the Found view (A4) --------------------------------------------------------------------------------------------

class SwitchableFindingBackend(FakeFindingBackend):
    def __init__(self, key="suwayomi", **kw):
        super().__init__(**kw)
        self.key = key

    def chapter_backend(self):
        return self.key


def chapter_finding(**kw):
    base = dict(id=1, series_id=1, kind="chapters", title="Ch. 41-44", fills=MISSING, size_bytes=None,
                reason="no Suwayomi source is saved for the series yet")
    base.update(kw)
    out = finding(**base)
    return replace(out, payload={"type": "suwayomi", "source_name": "MangaDex (EN)"})


@pytest.mark.parametrize("key", BOTH)
def test_found_rows_and_tooltips_name_the_program(qapp, key):
    backend = SwitchableFindingBackend(key, found=[chapter_finding(), chapter_finding(id=2, series_id=2, reason="")],
                                       last_run=find_run())
    v = FoundView(backend, confirm=lambda t, x: True)
    v.set_series_menu_enabled(True)
    v.resize(1100, 600)
    v.set_findings(backend.findings(), {1: "Example", 2: "Other"}, backend.last_run)
    v.show()
    qapp.processEvents()
    try:
        lines = [v.model.line_at(r) for r in range(v.model.rowCount())]
        findings_ = [ln for ln in lines if not ln.is_series]
        assert findings_ and all(ln.builtin == (key == "extensions") for ln in lines)
        text = "\n".join(f"{ln.release}|{ln.reason}" if not ln.is_series else ln.summary for ln in lines)
        assert ("Built-in extensions · MangaDex (EN)" if key == "extensions" else "Suwayomi · MangaDex (EN)") in text
        assert_names(text, key)
        # the tooltips of the buttons and of the reason
        first = findings_[0]
        row = v.model.row_of(first.key)
        rect = v.list.visualRect(v.model.index(row))
        tips = []
        for part in (PART_ACTION, PART_REASON):
            parts = v.delegate.parts(rect, first)
            if part in parts:
                tips.append(v.delegate.tooltip(rect, first, parts[part].center()))
        assert tips
        assert_names("\n".join(tips), key)
        # a switch while Found is open: the rows are made again in the other words
        backend.key = "extensions" if key == "suwayomi" else "suwayomi"
        v.set_findings(backend.findings(), {1: "Example", 2: "Other"}, backend.last_run)
        again = [v.model.line_at(r) for r in range(v.model.rowCount())]
        assert all(ln.builtin == (backend.key == "extensions") for ln in again)
    finally:
        v.stop()


@pytest.mark.parametrize("key", BOTH)
def test_send_all_asks_in_the_programs_words(qapp, key):
    backend = SwitchableFindingBackend(key, found=[chapter_finding()], last_run=find_run())
    asked = []
    v = FoundView(backend, confirm=lambda title, text: asked.append(text) or False)
    v.resize(1100, 600)
    v.set_findings(backend.findings(), {1: "Example"}, backend.last_run)
    try:
        v.send_all()
        assert asked and "set of chapters" in asked[0]
        assert_names(asked[0], key)
    finally:
        v.stop()


# --- Add a series (A4) --------------------------------------------------------------------------------------------

@pytest.mark.parametrize("key", BOTH)
def test_add_series_names_the_program(qapp, key):
    from mangalist import add_series as core
    from mangalist.gui.add_series_dialog import AddSeriesDialog

    from .test_add_series_dialog import FakeBackend as AddBackend
    from .test_add_series_dialog import mu_record

    class Backend(AddBackend):
        def chapter_builtin(self):
            return key == "extensions"

    backend = Backend(None)
    dlg = AddSeriesDialog(backend, [])
    try:
        dlg._show_series(core.info_from_mu(mu_record(1000), None))          # no MangaDex id: the source comes later
        assert dlg.series_source.text() == cw.reword(SwitchableChapterBackend(key),
                                                     "The chapters' source is found when you first open them (Suwayomi).")
        assert_names(dlg.series_source.text(), key)
        from dataclasses import replace as _replace

        dlg._show_series(_replace(core.info_from_mu(mu_record(1000), None), mangadex_id="abc"))   # a MangaDex id
        assert dlg.series_source.text().startswith("Asking ")                   # ... the program is asked
        assert_names(dlg.series_source.text(), key)
    finally:
        dlg.close()
        dlg.deleteLater()


def test_the_live_backend_knows_the_chosen_program(qapp, tmp_path):
    from mangalist import store
    from mangalist.gui.add_series_dialog import LiveBackend
    from mangalist.store.downloads import SuwayomiSettings

    store.reset_stores()
    db = store.get_store()
    live = LiveBackend(db)
    assert live.chapter_builtin() is False
    SuwayomiSettings(db).set_backend("extensions")
    assert live.chapter_builtin() is True

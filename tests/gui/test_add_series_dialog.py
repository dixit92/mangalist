"""The Add a series dialog and its top-bar entry (offscreen Qt, fake lookups, real folders in temporary directories).
No MangaUpdates, MangaDex or Suwayomi call is made."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QDialog, QMessageBox  # noqa: E402

from mangalist import add_series as core  # noqa: E402
from mangalist import mu_cache  # noqa: E402
from mangalist.gui import add_series_dialog as ui  # noqa: E402
from mangalist.gui.add_series_dialog import AddSeriesDialog  # noqa: E402

from .conftest import qapp, wait_until  # noqa: E402,F401

UUID = "12345678-1234-4abc-8def-1234567890ab"
MU_URL = "https://www.mangaupdates.com/series/" + core.base36_from_mu_id(1000) + "/made-up"


def mu_record(sid=1000, title="Made Up Series") -> Dict[str, Any]:
    url = f"https://www.mangaupdates.com/series/{core.base36_from_mu_id(sid)}/x"
    return {"series_id": sid, "title": title, "url": url,
            "type": "Manga", "year": "2015", "licensed": True, "latest_chapter": 42, "associated": []}


class FakeBackend:
    """The dialog's backend with canned answers; ``add`` really makes the folder (in a temporary library)."""

    def __init__(self, db=None, *, existing=(), source=None, look=None, fail_add=None):
        self.db = db
        self.existing_folders = list(existing)
        self.source = source or core.SourceLookup(None, "Suwayomi is not set up.")
        self.looks: Dict[str, core.Lookup] = look or {}
        self.lookups: List[str] = []
        self.show_all: List[bool] = []          # "Show doujinshi and artbooks" as each lookup was asked
        self.sources_asked: List[str] = []
        self.added: List[tuple] = []
        self.fail_add = fail_add

    def lookup(self, text, show_all=False):
        self.lookups.append(text)
        self.show_all.append(show_all)
        if text not in self.looks:
            raise ui.BackendError("MangaUpdates found nothing.")
        return self.looks[text]

    def with_mu_record(self, look, record):
        return core.info_from_mu(mu_record(record["series_id"], record["title"]), look.mangadex)

    def find_source(self, mangadex_id):
        self.sources_asked.append(mangadex_id)
        return self.source

    def existing(self, mu_id):
        return self.existing_folders

    def problem(self, root, name):
        return core.folder_problem(root, name)

    def add(self, root, name, info, source):
        if self.fail_add:
            raise ui.BackendError(self.fail_add)
        self.added.append((root.name, name, info.mu_id, source))
        return core.add_series(self.db, root, name, info, source=source)


@pytest.fixture
def libs(tmp_path):
    from mangalist import store

    store.reset_stores()
    db = store.get_store()
    made = []
    for name in ("Manga", "Comics"):
        folder = tmp_path / "libs" / name
        folder.mkdir(parents=True)
        made.append(db.add_root(str(folder), name))
    return db, made, tmp_path / "libs"


@pytest.fixture
def dialogs():
    made = []
    yield made
    for dlg in made:
        dlg.close()
        dlg.deleteLater()


def open_dialog(dialogs, qapp, libs, backend=None, **kw):
    db, roots, _ = libs
    backend = backend or FakeBackend(db)
    dlg = AddSeriesDialog(backend, roots, current_root_id=kw.pop("current_root_id", None), **kw)
    dialogs.append(dlg)
    dlg.show()
    qapp.processEvents()
    return dlg, backend


def settle(qapp, dlg):
    wait_until(qapp, lambda: not dlg._busy)


def look_up(qapp, dlg, text):
    dlg.input.setText(text)
    dlg.look_up()
    settle(qapp, dlg)


def mu_look(**kw) -> core.Lookup:
    return core.Lookup(info=core.info_from_mu(mu_record(1000), kw.pop("mangadex", None)), **kw)


# --- the happy paths ------------------------------------------------------------------------------------------

def test_a_mangaupdates_link_shows_the_series_and_adds_it(qapp, dialogs, libs):
    db, roots, base = libs
    backend = FakeBackend(db, look={MU_URL: mu_look()})
    dlg, _ = open_dialog(dialogs, qapp, libs, backend)
    assert not dlg.btn_add.isEnabled() and dlg.series_box.isHidden() and dlg.form_box.isHidden()

    look_up(qapp, dlg, MU_URL)
    assert not dlg.series_box.isHidden() and not dlg.form_box.isHidden()
    assert dlg.series_name.text() == "Made Up Series"
    assert dlg.series_facts.text() == "Manga (2015)  ·  Licensed in English  ·  Latest chapter 42"
    assert dlg.folder_name() == "Made Up Series" and dlg.btn_add.isEnabled()
    assert "found when you first open" in dlg.series_source.text()          # a MangaUpdates link names no MangaDex id
    assert backend.sources_asked == []

    dlg.add()
    wait_until(qapp, lambda: dlg.outcome is not None)
    assert not dlg.done_box.isHidden() and dlg.isVisible()                  # the finish step, not closed yet
    dlg.finish(None)
    assert dlg.result() == QDialog.DialogCode.Accepted and dlg.root.name == "Manga" and dlg.next_step is None
    assert backend.added == [("Manga", "Made Up Series", 1000, None)]
    assert (base / "Manga" / "Made Up Series").is_dir() and dlg.outcome.series_id is not None
    assert mu_cache.load_entry(dlg.outcome.folder)["mu_confirmed"] is True


def test_the_library_and_the_name_are_the_owners_to_pick(qapp, dialogs, libs):
    db, roots, base = libs
    backend = FakeBackend(db, look={MU_URL: mu_look()})
    dlg, _ = open_dialog(dialogs, qapp, libs, backend, current_root_id=roots[1].id)
    assert dlg.library.currentText() == "Comics"                             # the library picked in the window
    look_up(qapp, dlg, MU_URL)
    dlg.folder.setEditText("Another Name")
    dlg.add()
    wait_until(qapp, lambda: dlg.outcome is not None)
    assert backend.added[0][:2] == ("Comics", "Another Name")
    assert (base / "Comics" / "Another Name").is_dir() and not (base / "Manga" / "Another Name").exists()


def test_a_mangadex_link_asks_suwayomi_and_passes_the_source_on(qapp, dialogs, libs):
    db, roots, base = libs
    md = core.mangadex_from_record({"data": {"id": UUID, "attributes": {
        "title": {"ja-ro": "Made Up Series"}, "altTitles": [{"en": "Made Up English"}], "links": {"mu": "rs"}}}})
    choice = core.SourceChoice("5511", "MangaDex (EN)", 77, "Made Up English")
    backend = FakeBackend(db, source=core.SourceLookup(choice), look={UUID: mu_look(mangadex=md)})
    dlg, _ = open_dialog(dialogs, qapp, libs, backend)
    look_up(qapp, dlg, UUID)
    assert backend.sources_asked == [UUID]
    assert "MangaDex (EN)" in dlg.series_source.text() and "Made Up English" in dlg.series_source.text()
    assert dlg.folder_name() == "Made Up Series [Made Up English]"          # the folder scheme "<romaji> [<English>]"
    names = [dlg.folder.itemText(i) for i in range(dlg.folder.count())]
    assert "Made Up Series" in names and "Made Up English" in names
    dlg.add()
    wait_until(qapp, lambda: dlg.outcome is not None)
    assert backend.added[0][3] == choice
    series = dlg.outcome.series_id
    assert '"manga_id": 77' in db.get_meta(f"suwayomi.series.{series}")


def test_a_mangadex_series_without_a_mangaupdates_page_can_be_picked_or_added_bare(qapp, dialogs, libs):
    db, roots, base = libs
    md = core.mangadex_from_record({"data": {"id": UUID, "attributes": {"title": {"en": "Made Up English"}}}})
    look = core.Lookup(info=core.info_from_mangadex(md), mangadex=md,
                       candidates=[mu_record(7, "Seven"), mu_record(8, "Eight")],
                       notes=["MangaDex lists no MangaUpdates page for this series."])
    backend = FakeBackend(db, look={UUID: look})
    dlg, _ = open_dialog(dialogs, qapp, libs, backend)
    look_up(qapp, dlg, UUID)
    assert not dlg.pick_box.isHidden() and dlg.candidates.count() == 2
    assert dlg.series_facts.text().endswith("No MangaUpdates match") and dlg.btn_add.isEnabled()
    assert "lists no MangaUpdates page" in dlg.status.text()

    dlg._picked(dlg.candidates.item(1))                                      # pick "Eight": its record is read
    settle(qapp, dlg)
    assert dlg.info.mu_id == 8 and dlg.info.mangadex_id == UUID and dlg.series_name.text() == "Eight"
    assert dlg.folder_name() == "Eight"                                      # MangaUpdates' title is the romaji
    assert "Made Up English" in [dlg.folder.itemText(i) for i in range(dlg.folder.count())]
    dlg.add()
    wait_until(qapp, lambda: dlg.outcome is not None)
    assert backend.added[0][2] == 8


def test_a_series_added_without_a_match_has_no_link(qapp, dialogs, libs):
    db, roots, base = libs
    md = core.mangadex_from_record({"data": {"id": UUID, "attributes": {"title": {"en": "Bare Series"}}}})
    backend = FakeBackend(db, look={UUID: core.Lookup(info=core.info_from_mangadex(md), mangadex=md)})
    dlg, _ = open_dialog(dialogs, qapp, libs, backend)
    look_up(qapp, dlg, UUID)
    dlg.add()
    wait_until(qapp, lambda: dlg.outcome is not None)
    assert backend.added[0][2] is None and mu_cache.load_entry(dlg.outcome.folder) is None


def test_a_title_lists_the_candidates_and_nothing_else_until_one_is_picked(qapp, dialogs, libs):
    backend = FakeBackend(libs[0], look={"made up": core.Lookup(candidates=[mu_record(3, "Three")])})
    dlg, _ = open_dialog(dialogs, qapp, libs, backend)
    look_up(qapp, dlg, "made up")
    assert not dlg.pick_box.isHidden() and dlg.series_box.isHidden() and not dlg.btn_add.isEnabled()
    dlg._picked(dlg.candidates.item(0))
    settle(qapp, dlg)
    assert dlg.series_name.text() == "Three" and dlg.btn_add.isEnabled()


# --- what is refused --------------------------------------------------------------------------------------------

def test_a_link_that_cannot_be_used_is_said_without_a_request(qapp, dialogs, libs):
    dlg, backend = open_dialog(dialogs, qapp, libs)
    look_up(qapp, dlg, "https://example.com/series/abc")
    assert "Only MangaUpdates and MangaDex" in dlg.status.text() and backend.lookups == []
    assert dlg.series_box.isHidden() and not dlg.btn_add.isEnabled()


def test_a_failed_lookup_is_shown_and_clears_the_last_series(qapp, dialogs, libs):
    backend = FakeBackend(libs[0], look={MU_URL: mu_look()})
    dlg, _ = open_dialog(dialogs, qapp, libs, backend)
    look_up(qapp, dlg, MU_URL)
    assert dlg.info is not None
    look_up(qapp, dlg, "https://www.mangaupdates.com/series/zzzz/other")
    assert dlg.status.text() == "MangaUpdates found nothing." and dlg.info is None and not dlg.btn_add.isEnabled()


def test_a_name_that_exists_or_breaks_a_rule_stops_the_add_button(qapp, dialogs, libs):
    db, roots, base = libs
    (base / "Manga" / "Taken").mkdir()
    backend = FakeBackend(db, look={MU_URL: mu_look()})
    dlg, _ = open_dialog(dialogs, qapp, libs, backend)
    look_up(qapp, dlg, MU_URL)
    for name, hint in (("taken", "already exists"), ("Bad: name", "not allowed on Windows"), ("   ", "")):
        dlg.folder.setEditText(name)
        dlg._check_name()
        assert not dlg.btn_add.isEnabled(), name
        assert hint in dlg.folder_problem.text()
    dlg.folder.setEditText("Free Name")
    dlg._check_name()
    assert dlg.btn_add.isEnabled() and dlg.folder_problem.isHidden()
    dlg.library.setCurrentIndex(1)                                           # the same name in another library
    (base / "Comics" / "Free Name").mkdir()
    dlg._check_name()
    assert not dlg.btn_add.isEnabled() and "already exists in Comics" in dlg.folder_problem.text()


def test_the_name_is_checked_again_when_adding(qapp, dialogs, libs):
    db, roots, base = libs
    backend = FakeBackend(db, look={MU_URL: mu_look()})
    dlg, _ = open_dialog(dialogs, qapp, libs, backend)
    look_up(qapp, dlg, MU_URL)
    (base / "Manga" / "Made Up Series").mkdir()                              # appeared since the lookup
    dlg.add()
    assert backend.added == [] and "already exists" in dlg.folder_problem.text() and dlg.outcome is None


def test_a_series_already_in_the_library_is_warned_about(qapp, dialogs, libs):
    backend = FakeBackend(libs[0], existing=["/data/Manga/Made Up Series"], look={MU_URL: mu_look()})
    dlg, _ = open_dialog(dialogs, qapp, libs, backend)
    look_up(qapp, dlg, MU_URL)
    assert not dlg.series_warning.isHidden() and "Made Up Series" in dlg.series_warning.text()
    assert dlg.btn_add.isEnabled()                                           # a warning, not a block


def test_a_failure_while_adding_is_shown_and_the_dialog_stays_open(qapp, dialogs, libs):
    backend = FakeBackend(libs[0], look={MU_URL: mu_look()}, fail_add="The folder could not be made.")
    dlg, _ = open_dialog(dialogs, qapp, libs, backend)
    look_up(qapp, dlg, MU_URL)
    dlg.add()
    settle(qapp, dlg)
    assert dlg.status.text() == "The folder could not be made." and dlg.outcome is None
    assert dlg.isVisible() and dlg.btn_add.isEnabled()
    dlg.reject()                                                             # cancelling works again
    assert not dlg.isVisible()


def test_notes_after_the_folder_exists_are_shown(qapp, dialogs, libs, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda parent, title, text: shown.append(text))
    backend = FakeBackend(libs[0], look={MU_URL: mu_look()})
    real_add = backend.add

    def add_with_note(*a):
        res = real_add(*a)
        res.notes.append("The Suwayomi source was not saved.")
        return res

    backend.add = add_with_note
    dlg, _ = open_dialog(dialogs, qapp, libs, backend)
    look_up(qapp, dlg, MU_URL)
    dlg.add()
    wait_until(qapp, lambda: dlg.outcome is not None)
    assert shown == [] and not dlg.done_notes.isHidden()                     # on the finish step, no extra box
    assert "Suwayomi source was not saved" in dlg.done_notes.text()


def test_without_next_steps_notes_are_a_message_and_the_dialog_closes(qapp, dialogs, libs, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda parent, title, text: shown.append(text))
    backend = FakeBackend(libs[0], look={MU_URL: mu_look()})
    real_add = backend.add

    def add_with_note(*a):
        res = real_add(*a)
        res.notes.append("The Suwayomi source was not saved.")
        return res

    backend.add = add_with_note
    dlg, _ = open_dialog(dialogs, qapp, libs, backend, next_steps=())
    look_up(qapp, dlg, MU_URL)
    dlg.add()
    wait_until(qapp, lambda: dlg.outcome is not None)
    assert len(shown) == 1 and "Suwayomi source was not saved" in shown[0]
    assert dlg.result() == QDialog.DialogCode.Accepted and dlg.next_step is None


# --- the finish step (owner, 2026-10-15, answer 5: offer the first download right away) ---------------------------

def test_the_finish_step_offers_finding_or_opening_the_series(qapp, dialogs, libs):
    for choice in (ui.NEXT_FIND, ui.NEXT_OPEN, None):
        db, roots, base = libs
        backend = FakeBackend(db, look={MU_URL: mu_look()})
        dlg, _ = open_dialog(dialogs, qapp, libs, backend)
        look_up(qapp, dlg, MU_URL)
        dlg.folder.setEditText(f"Made Up Series {choice}")
        dlg.add()
        wait_until(qapp, lambda: dlg.outcome is not None)
        assert dlg.series_box.isHidden() and dlg.form_box.isHidden() and dlg.btn_add.isHidden()
        assert f"Added “Made Up Series {choice}” to Manga" == dlg.done_title.text()
        assert not dlg.btn_find.isHidden() and not dlg.btn_open.isHidden() and not dlg.btn_done.isHidden()
        {ui.NEXT_FIND: dlg.btn_find, ui.NEXT_OPEN: dlg.btn_open, None: dlg.btn_done}[choice].click()
        assert dlg.result() == QDialog.DialogCode.Accepted and dlg.next_step == choice


def test_closing_the_finish_step_is_done_and_keeps_the_outcome(qapp, dialogs, libs):
    backend = FakeBackend(libs[0], look={MU_URL: mu_look()})
    dlg, _ = open_dialog(dialogs, qapp, libs, backend, next_steps=(ui.NEXT_OPEN,))
    look_up(qapp, dlg, MU_URL)
    dlg.add()
    wait_until(qapp, lambda: dlg.outcome is not None)
    assert dlg.btn_find.isHidden() and not dlg.btn_open.isHidden()          # only what the window can do
    dlg.reject()                                                             # Escape
    assert dlg.result() == QDialog.DialogCode.Accepted and dlg.next_step is None and dlg.outcome is not None


# --- doujinshi and artbooks (owner, 2026-10-15, answer 7) ---------------------------------------------------------

def test_show_doujinshi_and_artbooks_repeats_the_search_with_them(qapp, dialogs, libs):
    backend = FakeBackend(libs[0], look={"made up": core.Lookup(candidates=[mu_record(3, "Three")])})
    dlg, _ = open_dialog(dialogs, qapp, libs, backend)
    look_up(qapp, dlg, "made up")
    assert backend.show_all == [False] and not dlg.show_all.isChecked() and not dlg.show_all.isHidden()
    dlg.show_all.setChecked(True)
    settle(qapp, dlg)
    assert backend.lookups == ["made up", "made up"] and backend.show_all == [False, True]
    assert dlg.candidates.count() == 1 and not dlg.pick_box.isHidden()


def test_without_a_library_folder_nothing_can_be_added(qapp, dialogs):
    dlg = AddSeriesDialog(FakeBackend(), [])
    dialogs.append(dlg)
    assert not dlg.input.isEnabled() and not dlg.btn_add.isEnabled() and "library folder first" in dlg.status.text()


# --- the live backend with fakes behind it ----------------------------------------------------------------------

def test_the_live_backend_adds_through_the_core(libs, monkeypatch):
    db, roots, base = libs
    info = core.info_from_mu(mu_record(1000))
    asked = []
    monkeypatch.setattr("mangalist.renamer.request_mangapixer_scans", lambda db, ids: asked.append(list(ids)) or "")
    out = ui.LiveBackend(db).add(roots[0], "Made Up Series", info, None)
    assert (base / "Manga" / "Made Up Series").is_dir() and asked == [[out.series_id]]
    assert mu_cache.load_entry(out.folder)["scan_latest_chapter"] == 42.0       # the record's progress came along
    with pytest.raises(ui.BackendError, match="already exists"):
        ui.LiveBackend(db).add(roots[0], "made up series", info, None)


def test_the_live_backend_turns_core_errors_into_readable_ones(libs):
    backend = ui.LiveBackend(libs[0])
    with pytest.raises(ui.BackendError, match="Only MangaUpdates and MangaDex"):
        backend.lookup("https://example.com/x")
    assert backend.problem(libs[1][0], "Fine Name") is None
    assert backend.existing(None) == []


# --- the window's entry ------------------------------------------------------------------------------------------

@pytest.fixture
def window(qapp, libs, monkeypatch):
    from mangalist.gui import lanes
    from mangalist.gui import main_window as mw
    from .conftest import FakeBackend as DownloadsFake
    from .test_shell_window import FakeDownloadTab, FakeDuplicatesView

    monkeypatch.setattr(mw.MainWindow, "_make_volumes_backend", lambda self: DownloadsFake())
    monkeypatch.setattr(lanes, "download_tab_class", lambda: FakeDownloadTab)
    monkeypatch.setattr(lanes, "duplicates_view_class", lambda: FakeDuplicatesView)
    monkeypatch.setattr(lanes, "open_settings_function", lambda: (lambda parent, db, backend, section=None: None))
    monkeypatch.setattr(lanes, "find_duplicate_files_function", lambda: (lambda db: []))
    win = mw.MainWindow()
    yield win
    win.close()
    win.deleteLater()


def test_the_top_bar_button_opens_the_dialog_and_rescans_the_library_it_was_added_to(qapp, window, libs, monkeypatch):
    db, roots, base = libs
    opened = []
    backend = FakeBackend(db, look={MU_URL: mu_look()})

    def fake_exec(self):
        opened.append((self.library.currentText(), [r.name for r in self.roots]))
        look_up(qapp, self, MU_URL)
        self.add()
        wait_until(qapp, lambda: self.outcome is not None)
        self.finish(None)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(ui, "LiveBackend", lambda database: backend)
    monkeypatch.setattr(AddSeriesDialog, "exec", fake_exec)
    scans = []
    window._start_scan = lambda roots=None, scopes=None: scans.append(
        ([r.name for r in roots] if roots is not None else None, scopes))
    window._library = roots[1].id                                            # the library picked in the window
    window._top.btn_add_series.click()
    assert opened == [("Comics", ["Manga", "Comics"])]
    (names, scopes), = scans
    assert names == ["Comics"] and list(scopes) == [roots[1].id]              # only the library that got the folder
    assert len(scopes[roots[1].id]) == 1                                     # ... and in it only the new folder


def test_find_releases_now_opens_the_new_series_panel_after_the_rescan(qapp, window, libs, monkeypatch):
    """Owner, 2026-10-29: "When you add a new series, it should send you to this page instead of the top of the find
    page" - the series' own panel, which searches by itself; no bulk find."""
    db, roots, base = libs
    backend = FakeBackend(db, look={MU_URL: mu_look()})

    def fake_exec(self):
        assert self.next_steps == (ui.NEXT_FIND,)              # one choice: Open in Download is the same now
        look_up(qapp, self, MU_URL)
        self.add()
        wait_until(qapp, lambda: self.outcome is not None)
        self.finish(ui.NEXT_FIND)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(ui, "LiveBackend", lambda database: backend)
    monkeypatch.setattr(AddSeriesDialog, "exec", fake_exec)
    window._start_scan = lambda roots=None, scopes=None: None
    found, opened = [], []
    window._download_tab.find_for = lambda ids: found.append(list(ids)) or True
    window.get_missing = lambda folder: opened.append(folder) or True
    window._top.btn_add_series.click()
    assert opened == [] and window._after_add[0] == ui.NEXT_FIND            # nothing before the rescan
    folder = window._after_add[1]
    window._run_after_add()                                                  # what the rescan's end calls
    assert opened == [folder] and found == [] and window._after_add is None
    window._run_after_add()                                                  # once only
    assert opened == [folder] and found == []


def test_open_and_find_both_select_the_series_and_done_does_nothing(qapp, window, libs):
    opened = []
    window.get_missing = lambda folder: opened.append(folder) or True
    window._after_add = (ui.NEXT_OPEN, "/lib/Made Up", 5)
    window._run_after_add()
    window._download_tab.find_for = lambda ids: False                        # no automation set up
    window._after_add = (ui.NEXT_FIND, "/lib/Made Up Two", 6)
    window._run_after_add()
    window._after_add = (None, "/lib/Done", 7)                               # Done: nothing
    window._run_after_add()
    assert opened == ["/lib/Made Up", "/lib/Made Up Two"]


def test_cancelling_the_dialog_rescans_nothing(qapp, window, libs, monkeypatch):
    monkeypatch.setattr(ui, "LiveBackend", lambda database: FakeBackend(libs[0]))
    monkeypatch.setattr(AddSeriesDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    scans = []
    window._start_scan = lambda roots=None, scopes=None: scans.append(roots)
    window._top.btn_add_series.click()
    assert scans == []


def test_the_new_empty_folder_shows_up_as_a_series_with_its_match(qapp, window, libs):
    """The whole path with the real scan: add, rescan the library, the folder is a series (no files) carrying the
    confirmed MangaUpdates match."""
    db, roots, base = libs
    info = core.info_from_mu(mu_record(1000))
    out = core.add_series(db, roots[0], "Made Up Series", info)
    window._start_signatures = lambda: None
    window._start_scan([roots[0]])
    wait_until(qapp, lambda: window._thread is None, timeout=30)
    rows = [e for e in window._model.entries() if e.title == "Made Up Series"]
    assert len(rows) == 1 and rows[0].files == [] and rows[0].mu_id == 1000 and rows[0].mu_confirmed
    series = db.series_for_folder(out.folder)
    assert series.id == out.series_id and series.status == "present"


def test_a_kind_answer_in_the_renamer_rescans_only_that_series_folder(qapp, window, libs):
    # owner, 2026-10-17: answering "chapters" for one series read its whole library (304 series, 11 s)
    db, roots, base = libs
    (base / "Manga" / "Bare Series").mkdir()
    series = db.ensure_series(roots[0].id, "Bare Series")
    scans = []
    window._start_scan = lambda roots=None, scopes=None: scans.append(([r.name for r in roots], scopes))
    window._on_series_reread([roots[0].id], [series.id])
    assert scans == [(["Manga"], {roots[0].id: {"Bare Series"}})]
    # a series the database no longer knows: its library is read whole (the window's wait for it still ends)
    window._on_series_reread([roots[1].id], [99999])
    assert scans[-1] == (["Comics"], {roots[1].id: None})

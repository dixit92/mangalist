"""Other releases in the releases card (owner, 2026-10-24: "optionally show downloads even if you don't have missing
chapters in a collapsible search result list hidden by default ... similarly to upgrade"): the section collapsed by
default with its count, the message when nothing fills a gap, Choose volumes, the replacement confirmation and what the
send carries; "Search releases..." of a series with no gaps. Offscreen Qt, fake backend, made-up names."""

from __future__ import annotations

import dataclasses
import threading

import pytest

pytest.importorskip("PySide6")

from mangalist.downloads.partial import choose_files  # noqa: E402
from mangalist.gui import releases_panel  # noqa: E402
from mangalist.gui.releases_panel import ReleasesPanel, other_line, split_other  # noqa: E402

from .conftest import FakeBackend, candidate, qapp, target, wait_until  # noqa: E402,F401

HELD = tuple(str(n) for n in range(1, 15))
COLLECTION = candidate("Made Up Title v01-14 + 110-203 (Digital) (Group)", info_hash="c" * 40, vol_from="1",
                       vol_to="14", covers_missing=(), covers_held=HELD, seeders=9,
                       reasons=("includes 14 volumes you already have (1 ... 14)", "Digital", "9 seeders"))
COLLECTION = dataclasses.replace(COLLECTION, other=True, published="2026-10-20T00:00:00+00:00")
SCANS = dataclasses.replace(candidate("Made Up Title v03 (Scans)", info_hash="d" * 40, vol_from="3", vol_to="3",
                                      covers_missing=(), covers_held=("3",), digital=False), other=True)
USABLE = candidate("Made Up Title v15 (Digital)", info_hash="e" * 40, vol_from="15", vol_to="15",
                   covers_missing=("15",))


class OtherBackend(FakeBackend):
    """search_all with other releases; send keeps its keyword arguments; replacement_preview lists the files."""

    def __init__(self, *, packs=False, **kw):
        super().__init__(**kw)
        self.all_searched = []
        self.send_kwargs = []
        self.previews = []
        self.inspected = []
        if packs:
            self.inspect_pack = self._inspect

    def search_all(self, titles, missing, held):
        self.all_searched.append((tuple(titles), tuple(missing), tuple(held)))
        return list(self.results)

    def send(self, series_id, cand, wanted_volumes, target_dir, **kw):
        self.send_kwargs.append(kw)
        return super().send(series_id, cand, wanted_volumes, target_dir)

    def replacement_preview(self, series_id, volumes):
        self.previews.append((series_id, tuple(volumes)))
        moved = [f"Made Up Title v{int(v):02d} (Old Group).cbz" for v in volumes if v != "2"]
        return moved, (["Made Up Title v02 + extras.cbz"] if "2" in volumes else [])

    def _inspect(self, cand, wanted_volumes):
        self.threads.append(threading.get_ident())
        self.inspected.append((cand.info_hash, tuple(wanted_volumes)))
        names = [f"Made Up Title v{n:02d} (Digital).cbz" for n in range(1, 15)]
        return choose_files([(f"Made Up Title/{n}", 1000) for n in names], wanted_volumes, "volumes")


@pytest.fixture(autouse=True)
def _fast_and_closed(monkeypatch):
    monkeypatch.setattr(releases_panel, "PACK_DELAY_MS", 0)
    made = []
    original = ReleasesPanel.__init__

    def tracking(self, *a, **kw):
        original(self, *a, **kw)
        made.append(self)

    monkeypatch.setattr(ReleasesPanel, "__init__", tracking)
    yield
    for panel in made:
        panel.stop()
        panel.deleteLater()


def open_panel(qapp, backend, confirm=None, **kw):
    panel = ReleasesPanel(backend, confirm=confirm or (lambda p, t: True))
    panel.resize(1000, 700)
    panel.show()
    kw.setdefault("missing", ("15",))
    kw.setdefault("held", HELD)
    panel.open_target(target(**kw))
    wait_until(qapp, lambda: panel._search_state == "done" and panel._placement is not None)
    return panel


def select_other(qapp, panel, row=0):
    panel.set_others_open(True)
    panel.other_table.selectRow(row)
    wait_until(qapp, lambda: panel.pack_state() not in ("waiting", "reading"))


# --- the words ---------------------------------------------------------------------------------------------------

def test_what_an_other_release_replaces_and_adds():
    assert split_other(COLLECTION, HELD, ("15",)) == (HELD, ())
    assert other_line(COLLECTION, HELD, ("15",)) == "replaces v01-v14 you have, adds nothing"
    newer = dataclasses.replace(COLLECTION, vol_to="16")
    assert other_line(newer, HELD, ("15",)) == "replaces v01-v14 you have, adds v16"


# --- the section --------------------------------------------------------------------------------------------------

def test_other_releases_are_a_collapsed_section_under_the_results(qapp):
    backend = OtherBackend(results=[USABLE, COLLECTION, SCANS])
    panel = open_panel(qapp, backend)
    assert backend.all_searched and not backend.searched              # the card asks for the other releases too
    assert panel.table.rowCount() == 1 and panel.table.item(0, 1).text() == USABLE.title
    assert panel.other_toggle.isVisibleTo(panel) and "Other releases (2)" in panel.other_toggle.text()
    assert not panel.others_open() and not panel.other_card.isVisibleTo(panel)      # hidden by default
    assert panel.search_label.text() == "nyaa · 1 release · 2 other"
    assert panel.selected_candidate() == USABLE
    panel.other_toggle.click()
    assert panel.others_open() and panel.other_card.isVisibleTo(panel)
    assert [panel.other_table.item(r, 1).text() for r in range(2)] == [COLLECTION.title, SCANS.title]
    assert [panel.other_table.horizontalHeaderItem(c).text() for c in (2, 3)] == ["REPLACES", "ADDS"]
    assert panel.other_table.item(0, 2).text() == "v01-v14" and panel.other_table.item(0, 3).text() == "-"
    sub = panel.other_table.item(0, 1).data(releases_panel.ROLE_SUB)
    assert sub.startswith("replaces v01-v14 you have, adds nothing") and "you already have" not in sub
    panel.other_toggle.click()
    assert not panel.others_open() and not panel.other_card.isVisibleTo(panel)


def test_without_other_releases_there_is_no_section(qapp):
    panel = open_panel(qapp, OtherBackend(results=[USABLE]))
    assert not panel.other_toggle.isVisibleTo(panel) and panel.search_label.text() == "nyaa · 1 release"


def test_with_only_other_releases_the_card_says_so_instead_of_no_releases_found(qapp):
    panel = open_panel(qapp, OtherBackend(results=[COLLECTION, SCANS]))
    assert panel.stack.currentIndex() == releases_panel.PAGE_RESULTS
    assert not panel.results_card.isVisibleTo(panel) and panel.results_note.isVisibleTo(panel)
    note = panel.results_note.text()
    assert "No release on nyaa holds a missing volume" in note and "2 other releases" in note
    assert "Open Other releases below" in note and "No releases found" not in note
    assert panel.other_toggle.isVisibleTo(panel) and not panel.others_open()      # the offer, still collapsed
    assert panel.selected_candidate() is None and panel.send_blocker() == "Select a release."


def test_nothing_at_all_is_still_no_releases_found(qapp):
    panel = open_panel(qapp, OtherBackend(results=[]))
    assert panel.stack.currentIndex() == releases_panel.PAGE_MESSAGE
    assert "No releases found on nyaa" in panel.message_text.text()


def test_one_selection_across_the_two_tables(qapp):
    panel = open_panel(qapp, OtherBackend(results=[USABLE, COLLECTION]))
    select_other(qapp, panel)
    assert panel.selected_candidate() == COLLECTION and not panel.table.selectionModel().hasSelection()
    assert panel.other_choice.isVisibleTo(panel)
    assert panel.other_line.text().startswith("Replaces v01-v14 - your copies go to the holding folder")
    panel.table.selectRow(0)
    assert panel.selected_candidate() == USABLE and not panel.other_table.selectionModel().hasSelection()
    assert not panel.other_choice.isVisibleTo(panel)
    select_other(qapp, panel)
    panel.set_others_open(False)                     # collapsing drops the hidden selection
    assert panel.selected_candidate() == USABLE


# --- choosing and sending -----------------------------------------------------------------------------------------

def test_choose_volumes_narrows_what_is_replaced_and_read_from_the_file_list(qapp):
    backend = OtherBackend(results=[COLLECTION], packs=True)
    panel = open_panel(qapp, backend)
    select_other(qapp, panel)
    assert panel.chosen_volumes(COLLECTION) == HELD
    assert backend.inspected[-1] == (COLLECTION.info_hash, HELD)
    asked = []
    panel.choose_volumes = lambda parent, title, items, chosen: asked.append((title, list(items), chosen)) or \
        ["1", "2", "3"]
    panel.btn_choose.click()
    title, items, chosen = asked[0]
    assert title == COLLECTION.title and items[0] == ("1", "v01 - replaces yours") and chosen == HELD
    wait_until(qapp, lambda: panel.pack_state() not in ("waiting", "reading"))
    assert panel.chosen_volumes(COLLECTION) == ("1", "2", "3")
    assert backend.inspected[-1] == (COLLECTION.info_hash, ("1", "2", "3"))   # the file list for the choice
    assert panel.partial_check.text().startswith("Only the chosen volumes (3 of 14 files")
    assert panel.other_table.item(0, 2).text() == "v01-v03"
    panel.choose_volumes = lambda *a: []
    panel.btn_choose.click()
    assert panel.send_blocker() == "Choose at least one volume of this release to download."
    panel.choose_volumes = lambda *a: None                                     # Cancel: as it was
    panel.btn_choose.click()
    assert panel.chosen_volumes(COLLECTION) == ()


def test_the_replacement_confirmation_names_the_files_the_holding_folder_what_is_added_and_the_size(qapp):
    asked = []
    backend = OtherBackend(results=[dataclasses.replace(COLLECTION, vol_to="16")])
    panel = open_panel(qapp, backend, confirm=lambda p, t: asked.append(t) or True)
    select_other(qapp, panel)
    panel.set_chosen(panel.selected_candidate(), ["1", "2", "3", "16"])
    assert panel.send_selected()
    wait_until(qapp, lambda: backend.sent)
    text = asked[0]
    assert text.startswith("Replace your copies with this release?")
    assert "Replaces: v01-v03 - your copies go to the holding folder (restorable from the Download tab):" in text
    assert "  Made Up Title v01 (Old Group).cbz" in text and "  Made Up Title v03 (Old Group).cbz" in text
    assert "Kept (they hold more than the chosen volumes" in text and "Made Up Title v02 + extras.cbz" in text
    assert "Adds: v16" in text and "Size: 700.0 MB" in text and "Target folder: /lib/Made Up Title" not in text
    assert "Target folder: /lib/Example Series" in text
    assert backend.previews == [(7, ("1", "2", "3"))]
    assert backend.sent[0][2] == ("1", "2", "3", "16")
    assert backend.send_kwargs[0]["replace_volumes"] == ("1", "2", "3")
    assert "replaces your copies of v01-v03" in panel.status_label.text()


def test_an_other_release_that_only_adds_sends_no_replacement(qapp):
    backend = OtherBackend(results=[dataclasses.replace(COLLECTION, vol_from="16", vol_to="17")])
    panel = open_panel(qapp, backend)
    select_other(qapp, panel)
    assert "Replaces none of your copies" in panel.other_line.text()
    assert panel.send_selected()
    wait_until(qapp, lambda: backend.sent)
    assert backend.sent[0][2] == ("16", "17") and "replace_volumes" not in backend.send_kwargs[0]


def test_a_usable_release_sends_as_before(qapp):
    backend = OtherBackend(results=[USABLE, COLLECTION])
    panel = open_panel(qapp, backend)
    assert panel.send_selected()
    wait_until(qapp, lambda: backend.sent)
    assert backend.sent[0][2] == ("15",) and "replace_volumes" not in backend.send_kwargs[0]


def test_the_budget_line_is_in_the_replacement_confirmation(qapp):
    from mangalist.downloads.budget import BudgetState

    asked = []
    backend = OtherBackend(results=[COLLECTION])
    backend.budget_status = lambda: BudgetState(cap_bytes=50 * 1024 ** 3, used_bytes=1024 ** 3)
    panel = open_panel(qapp, backend, confirm=lambda p, t: asked.append(t) or True)
    select_other(qapp, panel)
    assert panel.send_selected()
    wait_until(qapp, lambda: backend.sent)
    assert "Download budget:" in asked[0] and "this adds" in asked[0]


# --- Search releases... of a series with nothing missing ----------------------------------------------------------

def test_a_series_with_no_gaps_opens_with_its_other_releases_shown(qapp):
    backend = OtherBackend(results=[COLLECTION])
    panel = ReleasesPanel(backend, confirm=lambda p, t: True)
    panel.open_others_next(7)
    panel.open_target(target(missing=(), held=HELD, browse=True))
    wait_until(qapp, lambda: panel._search_state == "done" and panel._placement is not None)
    assert panel.others_open() and backend.all_searched[-1][1] == ()
    assert "Nothing missing - every release on nyaa is compared with the volumes you have" in \
        panel.subtitle_label.text()
    assert "No release on nyaa holds a volume you do not have" in panel.results_note.text()
    panel.open_target(target(missing=(), held=HELD))                    # the next series: collapsed again
    wait_until(qapp, lambda: panel._search_state == "done")
    assert not panel.others_open()


# --- the replaced-files words in the Download tab -----------------------------------------------------------------

def test_a_replacements_old_copies_are_worded_as_replaced_files():
    from mangalist.gui.download_rules import replaced_bar_text
    from mangalist.gui.replaced_chapters import file_line
    from mangalist.store.replacements import Batch, ReplacedFile

    old = ReplacedFile(path="/lib/Made Up Title/Made Up Title v01.cbz", rel="Made Up Title v01.cbz", size=1000,
                       modified="2026-10-01T00:00:00+00:00", chapters="", volume="1")
    chapter = dataclasses.replace(old, rel="Made Up Title c004.cbz", chapters="4", volume="2")
    assert file_line(old) == "Made Up Title v01.cbz    v01, replaced by a newer release    1000.0 B"
    assert file_line(chapter).startswith("Made Up Title c004.cbz    ch. 4 in v02")

    def batch(files):
        return Batch(id=1, download_id=-1, series_id=7, root_path="/lib", series_dir="/lib/Made Up Title",
                     volumes=("1",), volume_files=(), files=tuple(files), kept=(), mode="holding", status="held",
                     plan_id=1, holding_dir="/holding", purge_after=None, error=None, created_at="", updated_at="")

    assert replaced_bar_text([batch([old])])[0] == "1000 B of replaced files in the holding folder (1 file, 1 series)"
    assert "of replaced chapters" in replaced_bar_text([batch([chapter])])[0]

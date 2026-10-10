"""The chapters panel (offscreen Qt, a fake backend with the Suwayomi extras): the lookup runs off the UI thread; a series
found by its MangaDex id lists its missing chapters with their groups (the default group's copies ticked, a choice per
row where more than one group has it); changing the group re-picks the rows and is remembered for the series; a title
match waits for the owner's confirmation; nothing is sent without the confirmation; chapters already sent are not
offered again."""

from __future__ import annotations

import threading

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402

from mangalist.downloads import chapters as chm  # noqa: E402
from mangalist.downloads.contracts import Placement  # noqa: E402
from mangalist.gui import chapters_panel as cp  # noqa: E402
from mangalist.gui.chapters_panel import ChaptersPanel  # noqa: E402
from mangalist.gui.shell import SECTION_SOURCES  # noqa: E402

from ..downloads.suwayomi_fakes import MANGA, MANGADEX  # noqa: E402
from .chapter_fakes import FOLDER, WEEB_MANGA, FakeChapterBackend, chapter_record, lookup_for  # noqa: E402
from .conftest import qapp, wait_until  # noqa: E402,F401

MISSING = ("41", "42", "43", "44")


@pytest.fixture(autouse=True)
def _close_panels():
    made = []
    original = ChaptersPanel.__init__

    def tracking(self, *a, **kw):
        original(self, *a, **kw)
        made.append(self)

    ChaptersPanel.__init__ = tracking
    yield
    ChaptersPanel.__init__ = original
    for panel in made:
        panel.stop()
        panel.deleteLater()


def make(qapp, backend=None, confirm=None):
    backend = backend or FakeChapterBackend()
    asked = []

    def _confirm(parent, text):
        asked.append(text)
        return True if confirm is None else confirm

    panel = ChaptersPanel(backend, confirm=_confirm)
    panel.resize(1000, 700)
    return panel, backend, asked


def settle(qapp, panel):
    wait_until(qapp, lambda: not panel._calls)


def open_series(qapp, panel, sid=4, missing=MISSING):
    panel.open_series(sid, "Example Webcomic", missing, ("Example Webcomic",))
    settle(qapp, panel)


def table(panel):
    out = []
    for r in range(panel.table.rowCount()):
        combo = panel.table.cellWidget(r, cp.COL_GROUP)
        group = combo.currentText() if combo is not None else panel.table.item(r, cp.COL_GROUP).text()
        out.append((panel.table.item(r, cp.COL_PICK).checkState() == Qt.CheckState.Checked,
                    panel.table.item(r, cp.COL_CHAPTER).text(), group, panel.table.item(r, cp.COL_STATUS).text()))
    return out


def test_a_mangadex_match_lists_the_missing_chapters_with_their_groups(qapp):
    panel, backend, _ = make(qapp)
    looked = []
    panel.looked_up.connect(lambda sid, lookup: looked.append((sid, lookup)))
    open_series(qapp, panel)
    assert backend.lookups == [(4, MISSING, ("Example Webcomic",))]
    assert backend.threads and threading.get_ident() not in backend.threads          # off the UI thread
    assert looked and looked[0][0] == 4 and looked[0][1].match is not None
    assert panel.stack.currentIndex() == cp.PAGE_CHAPTERS
    assert "MangaDex (EN)" in panel.match_label.text() and "MangaDex id" in panel.match_label.text()
    assert table(panel) == [(True, "41", "Alpha Scans", ""), (True, "42", "Alpha Scans", ""),
                            (True, "43", "Beta Group", ""), (False, "44", "-", "not on MangaDex (EN)")]
    assert panel.group_combo.currentData() == "Alpha Scans" and panel.group_combo.isEnabled()
    assert "the most of these chapters" in panel.group_reason.text()
    assert "not on MangaDex (EN): ch 44" in panel.subtitle_label.text()
    assert panel.btn_send.isEnabled() and panel.btn_send.text() == "Send 3 to Suwayomi"
    assert [c.id for c in panel.picks()] == [41, 42, 43]


def test_changing_the_group_repicks_the_rows_and_is_remembered(qapp):
    panel, backend, _ = make(qapp)
    open_series(qapp, panel)
    i = next(i for i in range(panel.group_combo.count()) if panel.group_combo.itemData(i) == "Beta Group")
    panel.group_combo.setCurrentIndex(i)
    settle(qapp, panel)
    assert backend.groups_set == [(4, "Beta Group")]
    assert [c.id for c in panel.picks()] == [41, 142, 43]          # 42 now from Beta Group, 41 has only Alpha
    assert panel.group_reason.text() == "your choice for this series"
    # One row's own choice; unticking a row leaves it out.
    combo = panel.table.cellWidget(1, cp.COL_GROUP)
    combo.setCurrentIndex(next(i for i in range(combo.count()) if combo.itemText(i) == "Alpha Scans"))
    panel.table.item(0, cp.COL_PICK).setCheckState(Qt.CheckState.Unchecked)
    assert [c.id for c in panel.picks()] == [42, 43] and panel.btn_send.text() == "Send 2 to Suwayomi"


def test_send_asks_first_then_sends_off_the_ui_thread_and_looks_up_again(qapp):
    panel, backend, asked = make(qapp)
    sent = []
    panel.sent.connect(sent.append)
    open_series(qapp, panel)
    assert panel.send_selected()
    settle(qapp, panel)
    assert asked and "Send 3 chapters of \"Example Webcomic\"" in asked[0] and "Chapters: 41-43" in asked[0]
    assert "From: MangaDex (EN) (Alpha Scans, Beta Group)" in asked[0] and f"Filed into: {FOLDER}" in asked[0]
    ((sid, match, picks, target, title, source),) = backend.chapter_sends
    assert sid == 4 and match.how == chm.HOW_MANGADEX and target == FOLDER and source == "MangaDex (EN)"
    assert [p[0] for p in picks] == [41, 42, 43]
    assert sent and len(sent[0]) == 3
    assert "Sent ch 41-43 to Suwayomi" in panel.status_label.text()
    # Looked up again: the sent chapters show how they stand and cannot be sent again.
    assert len(backend.lookups) == 2
    assert [row[0] for row in table(panel)] == [False, False, False, False]
    assert table(panel)[0][3] == "Downloading ch 41" and not panel.btn_send.isEnabled()


def test_nothing_is_sent_when_the_owner_says_no_and_a_refusal_is_shown(qapp):
    panel, backend, _ = make(qapp, confirm=False)
    open_series(qapp, panel)
    assert not panel.send_selected() and backend.chapter_sends == []
    panel._confirm = lambda parent, text: True
    backend.chapter_send_error = "Suwayomi could not be reached at http://192.0.2.10:4567 (ConnectionError)"
    assert panel.send_selected()
    settle(qapp, panel)
    assert "could not be reached" in panel.status_label.text()


def test_a_title_match_is_confirmed_before_any_chapter_is_listed(qapp):
    backend = FakeChapterBackend()
    backend.title_only.add(4)
    panel, _, _ = make(qapp, backend)
    open_series(qapp, panel)
    assert panel.stack.currentIndex() == cp.PAGE_CANDIDATES
    assert panel.cand_table.rowCount() == 2 and panel.cand_table.item(0, 1).text() == "Example Webcomic"
    assert "MangaPixer links no MangaDex id" in panel.match_label.text()
    assert not panel.btn_confirm.isEnabled() and not panel.btn_send.isEnabled()
    assert panel.send_blocker() == "no series found in Suwayomi yet"
    panel.cand_table.selectRow(0)
    assert panel.btn_confirm.isEnabled() and panel.confirm_selected()
    settle(qapp, panel)
    ((sid, match),) = backend.confirmed
    assert sid == 4 and match.manga == WEEB_MANGA and match.how == chm.HOW_TITLE
    assert panel.stack.currentIndex() == cp.PAGE_CHAPTERS and "confirmed by you" in panel.match_label.text()
    # "Not this series?" forgets it and looks again.
    assert panel.forget_match()
    settle(qapp, panel)
    assert backend.forgotten == [4] and panel.stack.currentIndex() == cp.PAGE_CANDIDATES


def test_no_source_has_it_or_none_allowed_points_to_the_sources(qapp):
    panel, backend, _ = make(qapp)
    asked = []
    panel.settings_requested.connect(asked.append)
    panel.show_lookup(lookup_for(4, MISSING))                    # no match, no candidates
    panel._series_id = 4
    assert panel.stack.currentIndex() == cp.PAGE_MESSAGE
    assert "None of your Suwayomi sources has this series" in panel.message_text.text()
    panel.message_action.click()
    panel.show_lookup(lookup_for(4, MISSING, error="no Suwayomi source is allowed"))
    assert "no Suwayomi source is allowed" in panel.message_text.text()
    panel.message_action.click()
    assert asked == [SECTION_SOURCES, SECTION_SOURCES]


def test_a_lookup_failure_is_shown_and_can_be_retried(qapp):
    backend = FakeChapterBackend()
    backend.lookup_error = "Suwayomi could not be reached at http://192.0.2.10:4567 (ConnectionError)"
    panel, _, _ = make(qapp, backend)
    looked = []
    panel.looked_up.connect(lambda sid, lookup: looked.append(lookup))
    open_series(qapp, panel)
    assert looked == [None] and "could not be reached" in panel.message_text.text()
    backend.lookup_error = None
    panel.look_up_again()
    settle(qapp, panel)
    assert panel.stack.currentIndex() == cp.PAGE_CHAPTERS


def test_an_ambiguous_folder_must_be_chosen_and_a_cached_lookup_is_shown_at_once(qapp):
    panel, backend, _ = make(qapp)
    placement = Placement(FOLDER, None, "chapters are in two folders", options=(FOLDER, FOLDER + "/Side"))
    match = chm.MangaMatch(MANGADEX, MANGA, chm.HOW_MANGADEX)
    cached = lookup_for(4, MISSING, match=match, placement=placement)
    panel.open_series(4, "Example Webcomic", MISSING, ("Example Webcomic",), cached=cached)
    assert backend.lookups == []                                     # no new lookup
    assert panel.folder_row.isVisibleTo(panel) and panel.send_blocker() == "choose the folder first"
    panel.folder_combo.setCurrentIndex(2)
    assert panel.chosen_target_dir() == FOLDER + "/Side" and panel.send_blocker() is None


def test_chapters_already_in_hand_are_not_offered(qapp):
    backend = FakeChapterBackend()
    backend.record_list.append(chapter_record(1, 4, "41"))
    panel, _, _ = make(qapp, backend)
    open_series(qapp, panel)
    assert table(panel)[0] == (False, "41", "Alpha Scans", "Downloading ch 41")
    assert [c.id for c in panel.picks()] == [42, 43]

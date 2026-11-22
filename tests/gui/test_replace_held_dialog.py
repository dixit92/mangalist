"""Replace held chapters… (owner, 2026-11-22): the dialog finds the series on the chosen source, lists the held
chapters it has with the files each replaces, and sends the ticked ones with those files - after a confirmation."""

from __future__ import annotations

from decimal import Decimal as D

import pytest

pytest.importorskip("PySide6")

from mangalist.downloads import replace_held as rh  # noqa: E402
from mangalist.downloads.chapters import HOW_TITLE, ChapterSendOutcome, MangaMatch  # noqa: E402
from mangalist.gui.replace_held_dialog import ReplaceHeldDialog, available  # noqa: E402

from ..downloads.suwayomi_fakes import MANGA, MANGADEX, WEEB, chapter  # noqa: E402
from .conftest import qapp, wait_until  # noqa: E402,F401


class FakeBackend:
    def __init__(self):
        self.calls = []
        self.source = [chapter(33, "33", "New Group"), chapter(34, "34", "New Group"), chapter(134, "34", "Other"),
                       chapter(41, "40.1", "New Group")]
        self.held = [rh.HeldFile(f"/lib/S/Ch. {n}.cbz", f"Ch. {n}.cbz", D(n)) for n in ("33.1", "33.2", "34", "40")]

    def suwayomi_sources(self):
        return [(MANGADEX, True), (WEEB, False)]

    def replace_candidates(self, source_id, titles):
        self.calls.append(("candidates", source_id, tuple(titles)))
        return [MangaMatch(WEEB, MANGA, HOW_TITLE)]

    def replace_lookup(self, series_id, match):
        self.calls.append(("lookup", series_id, match.source.id))
        return rh.ReplaceLookup(series_id, match, MANGA.title, WEEB.display_name, rh.rows_for(self.source, self.held))

    def send_replacements(self, series_id, match, picks, manga_title="", source_name=""):
        self.calls.append(("send", series_id, [(ch.number, ch.scanlator, list(paths)) for ch, paths in picks]))
        return ChapterSendOutcome(records=[], backend="extensions")


def test_find_confirm_tick_and_replace(qapp):
    backend = FakeBackend()
    asked = []
    dlg = ReplaceHeldDialog(backend, 7, "Made Up Series", ["Made Up Series", "Made Up"],
                            confirm=lambda parent, text: asked.append(text) or True)
    assert available(backend)
    wait_until(qapp, lambda: dlg.source_combo.count() == 2)
    assert "not allowed" in dlg.source_combo.itemText(1)             # every installed source, the allowed ones first
    dlg.source_combo.setCurrentIndex(1)
    assert dlg.find()
    wait_until(qapp, lambda: dlg.selected_candidate() is not None)
    assert backend.calls[0] == ("candidates", WEEB.id, ("Made Up Series", "Made Up"))
    assert dlg.use_selected()
    wait_until(qapp, lambda: dlg.lookup is not None)
    numbers = [r.number for r in dlg.lookup.rows]
    assert numbers == ["33", "34", "40.1"] and not dlg.btn_replace.isEnabled()          # nothing ticked
    assert "1 listed but not replaceable" in dlg.rows_note.text()
    dlg.set_checked(["33", "34", "40.1"])                             # 40.1 cannot be ticked (a part for a whole)
    assert dlg.btn_replace.text() == "Replace 2 chapters"
    dlg.table.cellWidget(1, 3).setCurrentIndex(1)                    # chapter 34: the other group's copy
    assert dlg.replace_selected()
    wait_until(qapp, lambda: any(c[0] == "send" for c in backend.calls))
    (send,) = [c for c in backend.calls if c[0] == "send"]
    assert send == ("send", 7, [("33", "New Group", ["/lib/S/Ch. 33.1.cbz", "/lib/S/Ch. 33.2.cbz"]),
                                ("34", "Other", ["/lib/S/Ch. 34.cbz"])])
    assert "Ch. 33.1.cbz + Ch. 33.2.cbz" in asked[0] and "holding folder" in asked[0]
    wait_until(qapp, lambda: not dlg._calls)
    assert dlg.picks() == []                                         # sent: unticked again
    dlg.reject()


def test_nothing_is_sent_without_the_owners_yes(qapp):
    backend = FakeBackend()
    dlg = ReplaceHeldDialog(backend, 7, "Made Up Series", ["Made Up Series"], confirm=lambda parent, text: False)
    wait_until(qapp, lambda: dlg.source_combo.count() == 2)
    dlg.find()
    wait_until(qapp, lambda: dlg.selected_candidate() is not None)
    dlg.use_selected()
    wait_until(qapp, lambda: dlg.lookup is not None)
    dlg.set_checked(["33"])
    assert not dlg.replace_selected() and not [c for c in backend.calls if c[0] == "send"]
    dlg.reject()


def test_a_backend_without_it_offers_nothing():
    assert not available(object())

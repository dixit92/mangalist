"""The "volumes or chapters?" dialog (offscreen Qt)."""

from __future__ import annotations

from decimal import Decimal

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402

from mangalist.gui import kind_dialog  # noqa: E402
from mangalist.gui.kind_dialog import MAX_SAMPLES, KindDialog, ask_series_kind, sample_names  # noqa: E402
from mangalist.scanner import record_library_scan, scan_library  # noqa: E402

from .conftest import make_archive  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def bare(db, library):
    for i in range(1, 13):
        make_archive(library / "Series N" / f"{i:02d}.cbz")
    root = db.add_root(str(library))
    result = scan_library(db.list_roots())
    record_library_scan(db, result)
    return root, result.entries[0]


def test_dialog_shows_a_few_file_names(qapp, bare):
    _, entry = bare
    dlg = KindDialog(entry)
    items = [dlg.sample_list.item(i).text() for i in range(dlg.sample_list.count())]
    assert items[:MAX_SAMPLES] == [f"{i:02d}.cbz" for i in range(1, MAX_SAMPLES + 1)]
    assert items[-1] == f"... and {12 - MAX_SAMPLES} more"
    assert "12 file(s)" in dlg.question.text() and "Series N" in dlg.question.text()
    assert sample_names(entry, 3) == (["01.cbz", "02.cbz", "03.cbz"], 12)


@pytest.mark.parametrize("button, expected", [("volumes_button", "volumes"), ("chapters_button", "chapters"),
                                              ("later_button", None)])
def test_buttons_answer(qapp, bare, button, expected):
    _, entry = bare
    dlg = KindDialog(entry)
    getattr(dlg, button).click()
    assert dlg.answer == expected
    assert dlg.result() == (QDialog.DialogCode.Accepted if expected else QDialog.DialogCode.Rejected)


def test_ask_series_kind_records_the_answer(qapp, db, bare, monkeypatch):
    root, entry = bare

    def fake_exec(self):
        self.chapters_button.click()
        return 1

    monkeypatch.setattr(kind_dialog.KindDialog, "exec", fake_exec)
    assert ask_series_kind(None, entry, db=db) == "chapters"
    assert db.series_kind(root.id, "Series N") == "chapters"
    assert not entry.needs_kind and entry.highest_chapter == Decimal("12")
    # Asked again later (to change the answer): shows the files the answer decided.
    dlg = KindDialog(entry)
    assert "now: <b>chapters</b>" in dlg.question.text()
    assert dlg.sample_list.item(0).text() == "01.cbz"


def test_ask_later_stores_nothing(qapp, db, bare, monkeypatch):
    root, entry = bare
    monkeypatch.setattr(kind_dialog.KindDialog, "exec", lambda self: self.later_button.click() or 0)
    assert ask_series_kind(None, entry, db=db) is None
    assert db.series_kind(root.id, "Series N") is None and entry.needs_kind

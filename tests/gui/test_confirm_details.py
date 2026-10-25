"""The merge confirmation fits any screen: a short summary, the files in a bounded scroll box, buttons in view
(owner, 2026-10-24: 27 files made the dialog taller than the screen). Made-up names only."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, QEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from mangalist import series_merge  # noqa: E402
from mangalist.gui.confirm_details import DetailsConfirm, SCREEN_SHARE, available_height  # noqa: E402
from mangalist.series_merge import Identical, MergeCheck  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _check(plain: int, renamed: int, identical: int, left: int = 0) -> MergeCheck:
    check = MergeCheck("/lib/Sample Series", "/lib2/Sample Series (EN)", merged_where="Lib / Sample Series",
                       kept_where="Lib2 / Sample Series (EN)", merged_root_id=1, kept_root_id=2)
    for i in range(plain):
        check.moves.append((f"/lib/Sample Series/Sample Series v{i:02d}.cbz", f"/lib2/Sample Series (EN)/Sample Series v{i:02d}.cbz"))
    for i in range(renamed):
        old, new = f"Sample Series ch{i:03d}.cbz", f"Sample Series ch{i:03d} (from Lib).cbz"
        check.suffixed.append((old, new))
        check.moves.append((f"/lib/Sample Series/{old}", f"/lib2/Sample Series (EN)/{new}"))
    for i in range(identical):
        check.identical.append(Identical(f"/lib/Sample Series/Sample Series x{i:02d}.cbz", 4000, "t",
                                         f"/lib2/Sample Series (EN)/Sample Series x{i:02d}.cbz", 4000, "t"))
    for i in range(left):
        check.left.append((f"notes{i}.txt", "not a volume"))
    return check


def test_the_summary_keeps_its_length_however_many_files_there_are():
    few, many = series_merge.confirm_parts(_check(1, 1, 1)), series_merge.confirm_parts(_check(40, 30, 25, 20))
    assert abs(len(few.summary.splitlines()) - len(many.summary.splitlines())) <= 1
    assert len(many.summary) < len(few.summary) + 120
    assert "25 identical copies will be DELETED" in many.summary and "Undo cannot bring them back" in many.summary
    assert "40 items move in under their own names" in many.summary and "30 items move in under a new name" in many.summary
    assert "20 items stay where they are" in many.summary
    assert many.label == "Merge 70 items and delete 25"


def test_the_details_list_every_file_and_confirm_text_keeps_them_all():
    check = _check(20, 16, 10, 3)
    parts = series_merge.confirm_parts(check)
    for i in range(20):
        assert f"Sample Series v{i:02d}.cbz" in parts.details
    for i in range(16):
        assert f"Sample Series ch{i:03d}.cbz  ->  Sample Series ch{i:03d} (from Lib).cbz" in parts.details
    for i in range(10):
        assert f"Sample Series x{i:02d}.cbz  (3.9 KB)" in parts.details or f"Sample Series x{i:02d}.cbz" in parts.details
    assert "Identical copies that will be DELETED, for good (10):" in parts.details
    assert "notes2.txt - not a volume" in parts.details
    _t, text, _b = series_merge.confirm_text(check)
    assert parts.summary in text and parts.details in text and "more" not in text.replace("Nothing is copied", "")


def test_no_details_box_when_nothing_is_listed():
    parts = series_merge.confirm_parts(_check(0, 0, 0))
    assert parts.details == ""


@pytest.mark.parametrize("files", [(1, 1, 1), (40, 30, 25)])
def test_the_dialog_is_shorter_than_the_screen_and_its_buttons_are_in_view(qapp, files):
    parts = series_merge.confirm_parts(_check(*files, 5))
    dialog = DetailsConfirm(parts.title, parts.summary, parts.details, parts.label)
    try:
        dialog.show()
        qapp.processEvents()
        screen = available_height(dialog)
        assert screen > 0
        assert dialog.height() <= screen * SCREEN_SHARE + 1
        assert dialog.details_box.height() <= 360
        # both buttons lie fully inside the dialog
        for button in (dialog.btn_accept, dialog.btn_cancel):
            bottom = button.mapTo(dialog, button.rect().bottomLeft()).y()
            assert 0 <= bottom <= dialog.height()
            assert button.isVisibleTo(dialog)
        assert dialog.btn_accept.text() == parts.label and dialog.summary_label.text() == parts.summary
        if files[0] > 20:
            bar = dialog.details_box.verticalScrollBar()
            assert bar.maximum() > 0                  # the long list scrolls instead of growing the dialog
    finally:
        dialog.close()
        dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def test_the_dialog_stays_within_a_small_screen(qapp, monkeypatch):
    """An iPad-sized (768 px) screen: the cap follows the screen; the list scrolls."""
    from mangalist.gui import confirm_details

    monkeypatch.setattr(confirm_details, "available_height", lambda widget=None: 700)
    parts = series_merge.confirm_parts(_check(40, 30, 25, 5))
    dialog = DetailsConfirm(parts.title, parts.summary, parts.details, parts.label)
    try:
        dialog.show()
        qapp.processEvents()
        assert dialog.maximumHeight() == int(700 * SCREEN_SHARE)
        assert dialog.height() <= 700 * SCREEN_SHARE + 1
    finally:
        dialog.close()
        dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

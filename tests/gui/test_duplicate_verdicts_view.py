"""The Duplicates view's content verdicts (owner, 2026-10-24: "Content verdicts"): the pages of the whole chapters
with parts are read in the background, each card says what they showed with a line of evidence and the matching
Discard preselected, different chapters and plain splits are hidden (and counted apart), Dismiss persists until a
file changes, and without Pillow the section works from the names as before. Made-up series, synthetic pages."""

from __future__ import annotations

import os
import zipfile

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, QEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QPushButton  # noqa: E402

from mangalist import page_hash, store  # noqa: E402
from mangalist.gui.duplicates_view import DuplicatesView  # noqa: E402
from tests.duplicates.conftest import Library, db, lib  # noqa: E402,F401
from tests.gui.conftest import wait_until  # noqa: E402

T0 = 1_700_000_000_000_000_000


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def views(qapp, db):
    made = []

    def make():
        view = DuplicatesView(db, confirm=lambda files: True)
        made.append(view)
        view.refresh()
        wait_until(qapp, lambda: not view._scanning and not view.checking_pages)
        return view

    yield make
    for view in made:
        view.stop()
        view.close()
        view.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    qapp.processEvents()


def texts(view):
    return [label.text() for label in view.findChildren(QLabel) if label.isVisibleTo(view)]


def button(view, text):
    found = [b for b in view.findChildren(QPushButton) if b.text() == text and b.isVisibleTo(view)]
    assert found, text
    return found


def fake_zip(path):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("001.jpg", f"not a picture {path.name}")


def zip_series(lib, series, numbers):
    for i, n in enumerate(numbers):
        p = lib.add(series, f"{series} c{n}.cbz")
        fake_zip(p)
        os.utime(p, ns=(T0 + i, T0 + i))
    lib.scan()


@pytest.fixture
def pil():
    pytest.importorskip("PIL")
    from tests.duplicates import pages

    return pages


def test_the_pages_decide_what_is_listed_and_what_is_preselected(views, lib, pil):
    def add(series, n, seeds, i, **kw):
        return pil.add_cbz(lib, series, f"{series} c{n}.cbz", seeds, mtime_ns=T0 + i * 1_000_000_000, **kw)

    alpha = [add("Alpha", n, s, i, **kw) for i, (n, s, kw) in enumerate((
        ("009", range(900, 912), {}), ("010", range(1000, 1016), {}), ("010.1", range(1000, 1008), {"quality": 60}),
        ("010.2", range(1008, 1016), {"scale": 0.75}), ("011", range(1100, 1112), {})))]
    bravo = [add("Bravo", n, s, i) for i, (n, s) in enumerate((
        ("009", range(2900, 2920)), ("009.1", range(2900, 2920)), ("009.2", range(2950, 2962))))]
    for i, (n, s) in enumerate((("081", range(8100, 8116)), ("081.1", range(8200, 8226)), ("081.2", range(8300, 8329)))):
        add("Delta", n, s, i)
    lib.scan()
    view = views()
    assert [g.title for g in view.whole_and_parts] == ["Alpha", "Bravo"]
    assert [g.title for g in view.hidden_whole_and_parts] == ["Delta"]
    shown = " ".join(texts(view))
    assert "WHOLE CHAPTERS WITH PARTS  ·  2 chapters in 2 series" in shown          # the count is what is shown
    assert "Pages: 16 of 16 pages of ch 10 are in 10.1 + 10.2." in shown
    assert "Same pages: the whole chapter 10 is its parts 10.1, 10.2 - keep one form" in shown
    assert "Copy: part 9.1 is the whole chapter 9 again" in shown and "(all byte-identical)" in shown
    assert "Hidden: 1 checked as different chapters." in shown and "Chapter 81" not in shown
    assert sorted(view.discarded_paths()) == sorted([str(alpha[2]), str(alpha[3]), str(bravo[1])])
    # the owner's own choice wins over the preselection, across a refresh
    assert view.set_discard(str(bravo[1]), False)
    view.refresh()
    wait_until(QApplication.instance(), lambda: not view._scanning and not view.checking_pages)
    assert str(bravo[1]) not in view.discarded_paths() and str(alpha[2]) in view.discarded_paths()
    # the hidden ones can be shown, with their evidence
    button(view, "Show hidden (1)")[0].click()
    assert "Pages: none of ch 81's 16 pages are in 81.1 / 81.2 - separate chapters." in " ".join(texts(view))
    assert "WHOLE CHAPTERS WITH PARTS  ·  2 chapters in 2 series" in " ".join(texts(view))


def test_dismiss_hides_an_item_until_a_file_changes_and_restore_brings_it_back(views, lib, db):
    zip_series(lib, "Example Series", ("037", "038", "038.1", "038.2"))
    view = views()
    (group,) = view.whole_and_parts
    assert view.dismiss(group)
    assert view.whole_and_parts == [] and [g.number for g in view.hidden_whole_and_parts] == ["38"]
    shown = " ".join(texts(view))
    assert "Hidden: 1 dismissed by you." in shown and "dismissed: it is shown again when one of its files changes" in shown
    assert view.discarded_paths() == []
    store.reset_stores()
    again = views()                                         # a new view (another start): still dismissed
    assert again.whole_and_parts == [] and len(again.hidden_whole_and_parts) == 1
    button(again, "Show hidden (1)")[0].click()
    button(again, "Restore")[0].click()
    assert [g.number for g in again.whole_and_parts] == ["38"]
    assert views().whole_and_parts != []


def test_without_pillow_the_section_lists_by_the_names_and_says_why(views, lib, monkeypatch):
    monkeypatch.setattr(page_hash, "pillow_available", lambda: False)
    zip_series(lib, "Example Series", ("037", "038", "038.1", "038.2", "040", "040.2"))
    view = views()
    assert [g.number for g in view.whole_and_parts] == ["38"] and [g.number for g in view.hidden_whole_and_parts] == ["40"]
    shown = " ".join(texts(view))
    assert "The pages cannot be read here (Pillow is not installed)" in shown
    assert "part 38.1 is held too, so the whole chapter 38 may be redundant" in shown
    assert "Pages: pages cannot be read here: Pillow is not installed." in shown
    assert "Hidden: 1 plain split (the whole is part 1)." in shown and view.discarded_paths() == []

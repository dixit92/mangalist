"""The Found view as one painted list (UI performance stage 1, owner 2026-10-14): the mouse on the painted Send / Skip /
"⋯" and the series headers (fold, the mode chip), hover and press, a double click that does not act twice, the
tooltips, the filter box, the rows diffed by finding id (an unchanged reload touches nothing, no widget is ever made per
finding), nothing done while the view is hidden, and a Light / Dark switch that only repaints. Offscreen Qt, the fake
finding backend, made-up series."""

from __future__ import annotations

import threading
from dataclasses import replace

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from mangalist.gui.found_view import (  # noqa: E402
    PART_ACTION,
    PART_MODE,
    PART_MORE,
    PART_SKIP,
    PART_TOGGLE,
    FoundView,
)

from .conftest import qapp, wait_until  # noqa: E402,F401
from .finding_fakes import FakeFindingBackend, find_run, finding  # noqa: E402
from .test_dark_mode import themed  # noqa: E402,F401

NAMES = {1: "Alpha Saga", 2: "Beta Days", 3: "Gamma Webcomic"}
FOUND = [finding(1, series_id=1, fills=("2",), title="Alpha Saga v02 (Digital)"),
         finding(2, series_id=1, fills=("3",), title="Alpha Saga v03 (Digital)", size_bytes=None),
         finding(3, series_id=2, fills=("5",), title="Beta Days v05", reason="a pack whose files cannot be narrowed"),
         finding(4, series_id=3, kind="chapters", fills=("41", "42"), size_bytes=None, title="Ch. 41-42")]


@pytest.fixture
def view(qapp):
    made = []

    def make(findings=FOUND, backend=None, shown=True, **kw):
        backend = backend if backend is not None else FakeFindingBackend(found=findings, last_run=find_run(), **kw)
        v = FoundView(backend, confirm=lambda t, x: True)
        v.set_series_menu_enabled(True)
        v.resize(1100, 600)
        v.set_findings(backend.findings(), NAMES, backend.last_run)
        if shown:
            v.show()
            QApplication.processEvents()
        made.append(v)
        return v, backend

    yield make
    for v in made:
        v.stop()
        v.close()
        v.deleteLater()


def part_point(v, key, part) -> QPoint:
    """The middle of a painted part, in the list's viewport."""
    row = v.model.row_of(key)
    rect = v.list.visualRect(v.model.index(row))
    return v.delegate.parts(rect, v.model.line_at(row))[part].center()


def click(v, key, part) -> None:
    QTest.mouseClick(v.list.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     part_point(v, key, part))


def settle(qapp, v):
    wait_until(qapp, lambda: not v.busy())


def test_the_painted_send_and_skip_act_on_a_real_click(qapp, view):
    v, backend = view()
    assert list(v.headers()) == [1, 2, 3] and list(v.rows) == [1, 2, 3, 4]
    changed = []
    v.changed.connect(lambda: changed.append(True))
    click(v, ("finding", 3), PART_SKIP)
    assert backend.skipped_findings == [3] and changed == [True]
    click(v, ("finding", 1), PART_ACTION)
    settle(qapp, v)
    assert backend.sent_findings == [1]
    assert v.status_label.text() == "Alpha Saga v02 - Sent to qBittorrent: Alpha Saga v02 (Digital)."


def test_a_click_beside_the_buttons_does_nothing_and_a_double_click_acts_once(qapp, view):
    v, backend = view()
    row = v.model.row_of(("finding", 3))
    rect = v.list.visualRect(v.model.index(row))
    QTest.mouseClick(v.list.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     QPoint(rect.left() + 60, rect.center().y()))                # on the release line: it selects
    assert backend.skipped_findings == [] and v.list.currentIndex().row() == row
    skip = part_point(v, ("finding", 3), PART_SKIP)
    QTest.mouseClick(v.list.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, skip)
    QTest.mouseDClick(v.list.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, skip)
    assert backend.skipped_findings == [3]                                   # a double click acts once


def test_hover_and_press_follow_the_mouse_and_leave_with_it(qapp, view):
    v, _backend = view()
    vp = v.list.viewport()
    QTest.mouseMove(vp, part_point(v, ("finding", 1), PART_ACTION))
    assert v.delegate.hover == (("finding", 1), PART_ACTION)
    assert vp.cursor().shape() == Qt.CursorShape.PointingHandCursor
    QTest.mouseMove(vp, part_point(v, ("series", 2), PART_TOGGLE))
    assert v.delegate.hover == (("series", 2), PART_TOGGLE)
    QTest.mousePress(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     part_point(v, ("finding", 1), PART_SKIP))
    assert v.delegate.pressed == (("finding", 1), PART_SKIP)
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                       part_point(v, ("finding", 3), PART_SKIP))             # released elsewhere: nothing happens
    assert v.delegate.pressed is None and v.waiting_count() == 4
    QApplication.sendEvent(vp, QEvent(QEvent.Type.Leave))                   # the mouse left the list
    assert v.delegate.hover is None


def test_a_series_header_folds_its_findings_and_shows_what_they_would_get(qapp, view):
    v, _backend = view()
    click(v, ("series", 1), PART_TOGGLE)
    assert list(v.rows) == [3, 4] and v.headers()[1].folded and v.headers()[1].summary == "v02 · v03"
    assert v.model.removed == 2 and v.model.resets == 0
    click(v, ("series", 1), PART_TOGGLE)
    assert list(v.rows) == [1, 2, 3, 4] and not v.headers()[1].folded
    assert len(v.shown()) == 4                                               # folded or not, Send all counts them


def test_the_mode_chip_opens_the_automatic_downloads_menu(qapp, view):
    v, backend = view(modes={2: "auto"})
    heads = v.headers()
    assert heads[1].mode == "Default (Notify)" and heads[2].mode == "Auto"
    assert "this series' own" in heads[2].mode_tip and "Settings > Automation" in heads[1].mode_tip
    asked = []
    v._exec_menu = lambda menu, pos: asked.append([a.text() for a in menu.actions() if a.text()]) or next(
        a for a in menu.actions() if a.text() == "Off")
    changed = []
    v.mode_changed.connect(lambda: changed.append(True))
    click(v, ("series", 1), PART_MODE)
    assert asked == [["Off", "Notify", "Auto", "Use the default (Notify)"]]
    assert backend.mode_calls == [((1,), "off")] and changed == [True]
    assert v.headers()[1].mode == "Off" and v.status_label.text() == "Automatic downloads for Alpha Saga: Off."
    assert v.rows[1].action == "Send"                                         # its findings stay


def test_the_more_button_asks_the_host_for_the_series_menu_below_it(qapp, view):
    v, _backend = view()
    asked = []
    v.series_menu_requested.connect(lambda sid, pos: asked.append((sid, pos)))
    click(v, ("finding", 4), PART_MORE)
    point = v.list.viewport().mapToGlobal(part_point(v, ("finding", 4), PART_MORE))
    assert asked and asked[0][0] == 3 and asked[0][1].y() > point.y()        # the menu opens under the button
    v.set_series_menu_enabled(False)
    assert not v.rows[4].menu and PART_MORE not in v.delegate.parts(
        v.list.visualRect(v.model.index(v.model.row_of(("finding", 4)))), v.rows[4])


def test_a_queued_or_sending_row_does_not_take_the_click(qapp, view):
    backend = FakeFindingBackend(found=FOUND, last_run=find_run())
    backend.send_gate = threading.Event()
    v, _b = view(backend=backend)
    click(v, ("finding", 1), PART_ACTION)
    click(v, ("finding", 3), PART_ACTION)
    assert v.queued() == [3] and v.rows[3].status == ("Queued · 1st", "run")
    assert PART_ACTION not in v.delegate.parts(v.list.visualRect(v.model.index(v.model.row_of(("finding", 3)))),
                                               v.rows[3])
    assert v.delegate.part_at(v.list.visualRect(v.model.index(v.model.row_of(("finding", 1)))), v.rows[1],
                              part_point(v, ("finding", 1), "_action")) is None      # Sending…: not clickable
    backend.send_gate.set()
    settle(qapp, v)
    assert backend.sent_findings == [1, 3]


def test_the_tooltips_name_what_each_part_does(qapp, view):
    v, _backend = view(findings=FOUND + [replace(finding(5, series_id=3, kind="chapters", fills=("43",),
                                                         size_bytes=None, reason="not on its source any more"),
                                                 payload={"type": "dropped"})])
    rect_of = lambda fid: v.list.visualRect(v.model.index(v.model.row_of(("finding", fid))))  # noqa: E731
    tip = lambda fid, part: v.delegate.tooltip(rect_of(fid), v.rows[fid], part_point(v, ("finding", fid), part))  # noqa
    assert tip(1, PART_ACTION).startswith("Send it now") and tip(1, PART_SKIP).startswith("Do not get it")
    assert tip(1, PART_MORE).startswith("This series:") and tip(5, PART_ACTION).startswith("Open the series in Missing")
    assert tip(3, "reason") == "Waits because a pack whose files cannot be narrowed"
    assert tip(1, "release") == "Alpha Saga v02 (Digital)"
    head = v.model.row_of(("series", 1))
    assert "Click to fold" in v.delegate.tooltip(v.list.visualRect(v.model.index(head)), v.headers()[1],
                                                 part_point(v, ("series", 1), PART_TOGGLE))


def test_the_filter_box_narrows_the_list_and_send_all(qapp, view):
    v, _backend = view()
    v.filter_edit.setText("beta")
    assert list(v.rows) == [3] and list(v.headers()) == [2] and v.count_label.text() == "1 waiting · 700 MB"
    v.filter_edit.setText("ch. 41")                                          # the release too
    assert list(v.rows) == [4]
    v.filter_edit.setText("nothing like it")
    assert v.rows == {} and not v.empty.isHidden() and v.empty_title.text() == "No finding matches"
    v.filter_edit.clear()
    assert list(v.rows) == [1, 2, 3, 4] and v.btn_send_all.text() == "Send all 4…"


def test_an_unchanged_update_touches_no_row_and_makes_no_widget(qapp, view):
    v, backend = view()
    widgets = len(v.findChildren(QWidget))
    before = (v.model.inserted, v.model.removed, v.model.changed, v.model.resets)
    for _ in range(3):
        v.set_findings(backend.findings(), dict(NAMES), backend.last_run)
    assert (v.model.inserted, v.model.removed, v.model.changed, v.model.resets) == before
    backend.skip_finding(2)                                                  # one finding gone: one row removed
    v.set_findings(backend.findings(), NAMES, backend.last_run)
    assert v.model.removed == before[1] + 1 and v.model.inserted == before[0] and v.model.resets == 0
    assert v.headers()[1].count == "1 waiting · 700 MB" and v.model.changed == before[2] + 1     # its header
    backend.found.append(finding(9, series_id=2, fills=("6",)))              # a new one: one row inserted
    v.set_findings(backend.findings(), NAMES, backend.last_run)
    assert v.model.inserted == before[0] + 1 and list(v.rows) == [1, 3, 9, 4]
    assert len(v.findChildren(QWidget)) == widgets                           # no widget per finding, ever


def test_many_findings_are_still_the_same_widgets(qapp, view):
    small, _b = view(findings=FOUND[:1])
    many = [finding(100 + n, series_id=n % 40 + 1, fills=(str(n),)) for n in range(150)]
    big, _b = view(findings=many)
    assert len(big.rows) == 150 and len(big.findChildren(QWidget)) == len(small.findChildren(QWidget))


def test_a_renamed_series_moves_its_rows(qapp, view):
    v, backend = view()
    v.set_findings(backend.findings(), {**NAMES, 1: "Zeta Saga"}, backend.last_run)
    assert list(v.headers()) == [2, 3, 1] and list(v.rows) == [3, 4, 1, 2] and v.rows[1].name == "Zeta Saga"


def test_nothing_is_done_while_hidden(qapp, view):
    v, backend = view(shown=False)
    assert v.model.rowCount() == 0                                           # not built until shown
    for _ in range(3):
        v.set_findings(backend.findings(), NAMES, backend.last_run)
    assert v.model.inserted == 0
    v.show()
    QApplication.processEvents()
    assert v.model.rowCount() == 7 and v.model.inserted == 7 and v.model.resets == 0


def test_a_theme_switch_repaints_and_rebuilds_nothing(themed, view):
    from mangalist.gui import theme
    from mangalist.gui.colors import qcolor
    from mangalist.gui.palette import current

    v, _backend = view()
    before = (v.model.inserted, v.model.removed, v.model.changed, v.model.resets, len(v.list.findChildren(QWidget)))
    theme.apply_theme(themed, "dark")
    assert current().dark and qcolor("surface").name() == current().surface
    assert (v.model.inserted, v.model.removed, v.model.changed, v.model.resets,
            len(v.list.findChildren(QWidget))) == before
    v.list.viewport().grab()                                                 # paints in Dark without an error
    theme.apply_theme(themed, "light")
    assert qcolor("surface").name() == current().surface

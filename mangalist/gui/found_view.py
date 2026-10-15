"""The Download tab's **Found** view (automatic downloads, owner decisions 2026-10-10): what find passes found that waits
for the owner - grouped by series, one row per finding with what it would get, the release and where it comes from, why
it waits, its status and its size, **Send** / **Skip** per row and **Send all** (asks first, with the count and the total
size). Empty, it says "Nothing found waiting for you" and when the last pass ran.

**One painted list** (UI performance stage 1, owner 2026-10-14: "The redesign looks quite similar to Animefetch,
approved. Do it, especially for the speed aspect"): the findings are rows of one :class:`QListView` drawn by
:class:`FoundDelegate` - as the To get list beside it - not a card of ten widgets each (155 findings were about 1,550
widgets: 0.8 s to rebuild, 0.8 s of start-up, and most of a Light / Dark switch). Each series has a header row - its name,
how many wait and their size, its **Automatic downloads** mode as a chip (a click opens the Off / Notify / Auto / Use the
default menu) - that folds and unfolds its findings with a click. Send, Skip and "⋯" are painted in the row, with hover
and press like buttons (:meth:`FoundDelegate.editorEvent`); the tooltips name what each part does. A new list of
findings is diffed by finding id (:meth:`FoundModel.set_lines`): an unchanged reload - every 60 s, after every send -
changes no row, and one sent finding removes one row. While the view is hidden (another group shown) the list is not
touched at all; it catches up when it is shown. The header (Send all, the filter box, the status line) and the menus are
real widgets.

A chapter finding with nothing to send - the series' source is gone or lacks the chapters, or it has none yet - shows
**Change source…** instead of Send: it opens the series in Missing chapters, where its source is picked (owner,
2026-10-12: "there's no option here to change the source"); Send all leaves such findings out. Showing one series, the
header has its **Automatic downloads** mode (Off / Notify / Auto / the default; owner, 2026-10-12: "I should also be able
to switch a series from notify to auto here"); its "Use the default" names whose default it is - the series' library's
or the one in Settings > Automation. Each row's **⋯** opens the series' menu (the Download tab builds it with
:mod:`.series_actions`: Show in To get, Show in List, Open folder, Automatic downloads, Skip all of this series, and
Change source… for a series with nothing to send).

**Keyboard** (owner, 2026-10-15: Send and Skip are painted, so Tab no longer reaches them): on the selected finding
**Enter** is Send (Change source… for one with nothing to send) and **Delete** is Skip; on a series header Enter folds or
unfolds it. The arrow keys move between rows. After a skip the selection moves to the finding that took its place (else
the one above), so Delete can be pressed again; the list taking the focus selects its first finding.

Sends run one at a time (a pack may take a while: its file list is read to narrow it). **Send** while one runs queues the
finding - its button says "Queued", its status "Queued · 1st" - and the queue is worked through in order (owner,
2026-10-12: "Am I not able to queue sends?"); Skip and Change source stay usable meanwhile, and skipping a queued finding
takes it off the queue.

The rows are the backend's findings (:class:`~mangalist.store.findings.Finding`, ``findings()``); sending runs off the
UI thread (``send_finding`` may add a torrent to qBittorrent or queue it under the download budget, or enqueue chapters
in Suwayomi) and says what happened, as the backend words it; skipping is quick and the pick is never offered again.
A pick a pass sent by itself is not here: it is an ordinary download in the In progress list.

The Download tab feeds it (:meth:`FoundView.set_findings`) and reloads it when :attr:`FoundView.changed` fires.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from PySide6.QtCore import QAbstractListModel, QEvent, QModelIndex, QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QLineEdit,
    QListView,
    QMenu,
    QMessageBox,
    QProgressBar,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .automation_rules import (
    EMPTY_TITLE,
    MODE_AUTO,
    MODE_HELP,
    MODE_LABELS,
    MODES,
    by_series,
    default_entry_label,
    default_entry_tip,
    default_of,
    defaults_of,
    gets_text,
    kind_text,
    last_run_text,
    mode_chip,
    needs_source,
    reason_text,
    release_text,
    send_all_question,
    send_all_summary,
    sendable,
    series_line,
    size_text,
    status_text,
    total_text,
)
from .background import BackgroundCall, describe_error, start_call
from .colors import hex_color, qcolor
from .download_style import set_tone
from .download_widgets import button, card, chip_colors, hbox, label
from .series_actions import MENU_BUTTON_TEXT, add_mode_menu

_log = logging.getLogger(__name__)

ConfirmFn = Callable[[str, str], bool]

ROLE_LINE = Qt.ItemDataRole.UserRole + 40       # the row's FoundLine

# The painted parts of a row (what a click, a hover and a tooltip refer to).
PART_TOGGLE = "toggle"          # a series header (but its mode chip): fold / unfold its findings
PART_MODE = "mode"              # a series header's mode chip: the Automatic downloads menu
PART_ACTION = "action"          # Send (or Change source…)
PART_SKIP = "skip"
PART_MORE = "more"              # "⋯": the series' menu
PART_RELEASE = "release"        # tooltip only: the release line
PART_REASON = "reason"          # tooltip only: why it waits
PART_STATUS = "status"          # tooltip only: the status chip

SEND_TIP = ("Send it now: a torrent goes to qBittorrent (or waits in the queue past the download budget), chapters to "
            "Suwayomi (Enter on the selected row)")
SKIP_TIP = "Do not get it: it leaves the list and is not offered again (Delete on the selected row)"
SOURCE_TIP = ("Open the series in Missing chapters to pick where its chapters come from: MangaDex or your other Suwayomi "
              "sources. Remembered for the series")
MORE_TIP = ("This series: Show in To get (its other releases or chapters), Show in List, Open folder, Automatic "
            "downloads, Skip all of its findings")
CHANGE_SOURCE_TEXT = "Change source…"
STATUS_TIPS = {
    "Waiting": "Found by a find pass; waits for you to send or skip it",
    "Sending…": "Being sent now",
    "Change source?": "Nothing to send until the series has a (another) chapter source: Change source…",
}

# The geometry of the rows (pixels; the approved mockup's, 2026-10-14).
HEADER_H, ROW_H = 40, 52
PAD_L, PAD_R = 12, 16           # a header's padding
ROW_PAD_L = 46                  # a finding row's content starts under the series name
COL_GAP = 16
STATUS_W, SIZE_W = 132, 112
BTN_H, BTN_GAP, BTN_PAD, MORE_W = 28, 6, 12, 32
CHIP_H = 22
CHEVRON = 24


@dataclass(frozen=True)
class FoundLine:
    """One row of the list: a series header (``finding`` None) or one finding. Frozen and compared by value: the model
    replaces a row only when what it shows changed."""

    key: Tuple[str, int]                # ("series", series id) / ("finding", finding id)
    series_id: int
    name: str                           # the series' name
    finding: object = None
    # a series header
    count: str = ""                     # "2 waiting · 2.3 GB"
    summary: str = ""                   # what its findings would get (shown while it is folded)
    folded: bool = False
    mode: str = ""                      # the mode chip ('' without automatic downloads)
    mode_tip: str = ""
    # a finding
    state: str = ""                     # "sending" / "queued" / ""
    queue_pos: int = 0                  # its place in the queue (1 = next)
    passing: bool = False               # a find pass runs: every action waits for it
    menu: bool = False                  # "⋯" is offered (the host opens the series' menu)

    @property
    def is_series(self) -> bool:
        return self.finding is None

    # --- what a finding row shows (the tests read these too)

    @property
    def gets(self) -> str:
        return gets_text(self.finding)

    @property
    def kind(self) -> str:
        return kind_text(self.finding)

    @property
    def release(self) -> str:
        return release_text(self.finding)

    @property
    def reason(self) -> str:
        return reason_text(self.finding)

    @property
    def needs_source(self) -> bool:
        return needs_source(self.finding)

    @property
    def size(self) -> str:
        """Its size; '' when there is nothing to download yet (no source)."""
        return "" if self.needs_source else size_text(self.finding.size_bytes)

    @property
    def status(self) -> Tuple[str, str]:
        return status_text(self.finding, self.state, self.queue_pos)

    @property
    def action(self) -> str:
        """Send / Sending… / Queued, or Change source… for a finding with nothing to send."""
        if self.needs_source:
            return CHANGE_SOURCE_TEXT
        return {"sending": "Sending…", "queued": "Queued"}.get(self.state, "Send")

    @property
    def action_enabled(self) -> bool:
        if self.passing:
            return False
        return self.needs_source or not self.state

    @property
    def skip_enabled(self) -> bool:
        return not self.passing and self.state != "sending"


class FoundModel(QAbstractListModel):
    """The rows of the Found list. :meth:`set_lines` applies a new list as the difference to the shown one - rows
    removed, rows inserted, rows whose content changed - so a reload that changed nothing touches nothing, and the
    scroll position and the hover stay. The counters are read by the work-counter tests."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lines: List[FoundLine] = []
        self._rows: Dict[Tuple[str, int], int] = {}
        self.inserted = self.removed = self.changed = self.resets = 0

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self._lines)

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._lines):
            return None
        line = self._lines[index.row()]
        if role == ROLE_LINE:
            return line
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.AccessibleTextRole):
            if line.is_series:
                return f"{line.name}, {line.count}"
            return f"{line.gets} {line.kind}, {line.release}, {line.status[0]}, {line.size}".rstrip(", ")
        return None

    def flags(self, index: QModelIndex):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        if self._lines[index.row()].is_series:
            return Qt.ItemFlag.ItemIsEnabled
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def lines(self) -> List[FoundLine]:
        return list(self._lines)

    def line_at(self, row: int) -> Optional[FoundLine]:
        return self._lines[row] if 0 <= row < len(self._lines) else None

    def row_of(self, key: Tuple[str, int]) -> Optional[int]:
        return self._rows.get(key)

    def set_lines(self, lines: Sequence[FoundLine]) -> None:
        lines = list(lines)
        new_keys = [line.key for line in lines]
        if [line.key for line in self._lines] != new_keys:
            wanted = set(new_keys)
            r = len(self._lines) - 1                # removals, bottom up, a run of rows at a time
            while r >= 0:
                if self._lines[r].key in wanted:
                    r -= 1
                    continue
                end = r
                while r >= 0 and self._lines[r].key not in wanted:
                    r -= 1
                self.beginRemoveRows(QModelIndex(), r + 1, end)
                del self._lines[r + 1:end + 1]
                self.endRemoveRows()
                self.removed += end - r
            kept = {line.key for line in self._lines}
            if [line.key for line in self._lines] != [k for k in new_keys if k in kept]:
                # the order changed (a series was renamed): start over (still no widget is made)
                self.beginResetModel()
                self._lines = lines
                self._rows = {k: i for i, k in enumerate(new_keys)}
                self.endResetModel()
                self.resets += 1
                return
            i = 0                                   # insertions, top down: the rows kept are in the new order
            while i < len(lines):
                if i < len(self._lines) and self._lines[i].key == lines[i].key:
                    i += 1
                    continue
                j = i
                while j < len(lines) and lines[j].key not in kept:
                    j += 1
                self.beginInsertRows(QModelIndex(), i, j - 1)
                self._lines[i:i] = lines[i:j]
                self.endInsertRows()
                self.inserted += j - i
                i = j
        self._rows = {k: i for i, k in enumerate(new_keys)}
        for i, line in enumerate(lines):            # what a kept row shows
            if self._lines[i] != line:
                self._lines[i] = line
                index = self.index(i)
                self.dataChanged.emit(index, index)
                self.changed += 1


class _Fonts:
    """The fonts of the painted rows, made once (a delegate used to make them on every paint)."""

    def __init__(self):
        self.name = theme.font(14, 600)
        self.count = theme.font(13)
        self.gets = theme.font(14, 500)
        self.kind = theme.font(12)
        self.release = theme.font(12, mono=True)
        self.reason = theme.font(13)
        self.size = theme.font(13, mono=True)
        self.chip = theme.font(12, 600)
        self.button = theme.font(13)
        self.primary = theme.font(13, 600)
        self.more = theme.font(16, 700)
        self.metrics: Dict[int, QFontMetrics] = {}

    def fm(self, font: QFont) -> QFontMetrics:
        key = id(font)
        if key not in self.metrics:
            self.metrics[key] = QFontMetrics(font)
        return self.metrics[key]


class FoundDelegate(QStyledItemDelegate):
    """Paints the Found rows - a series header, or a finding with its parts in columns (what it fills and the release,
    why it waits, the status chip, the size, Send / Skip / "⋯") - and turns clicks on the painted buttons into
    :attr:`part_clicked`. The parts' rectangles come from one place (:meth:`parts`) for painting, hovering, clicking and
    the tooltips. Colours are read from the palette in force as it paints: a Light / Dark switch is a repaint."""

    part_clicked = Signal(object, str, QPoint)     # (the row's key, the part, where its menu goes - global)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._fonts: Optional[_Fonts] = None
        self.hover: Optional[Tuple[Tuple[str, int], str]] = None       # (row key, part) under the mouse
        self.pressed: Optional[Tuple[Tuple[str, int], str]] = None     # (row key, part) pressed

    @property
    def fonts(self) -> _Fonts:
        if self._fonts is None:
            self._fonts = _Fonts()
        return self._fonts

    # --- geometry -----------------------------------------------------------------------------------------

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        line = index.data(ROLE_LINE)
        view = option.widget
        width = view.viewport().width() if isinstance(view, QAbstractItemView) else option.rect.width()
        return QSize(max(width, 200), HEADER_H if line is not None and line.is_series else ROW_H)

    def _button_width(self, text: str, primary: bool) -> int:
        f = self.fonts
        return f.fm(f.primary if primary else f.button).horizontalAdvance(text) + 2 * BTN_PAD + 2

    def parts(self, rect: QRect, line: FoundLine) -> Dict[str, QRect]:
        """The rectangles of a row's parts inside *rect* (the row's): the clickable ones (only when they can be clicked
        now) and the ones with a tooltip. A narrow list drops the reason column first, then the status chip."""
        out: Dict[str, QRect] = {}
        if line.is_series:
            if line.mode:
                f = self.fonts
                width = f.fm(f.chip).horizontalAdvance(line.mode) + 36
                out[PART_MODE] = QRect(rect.right() - PAD_R - width + 1, rect.center().y() - 12 + 1, width, 24)
            out[PART_TOGGLE] = QRect(rect.left(), rect.top(), (out[PART_MODE].left() - 8 if PART_MODE in out
                                                               else rect.right()) - rect.left() + 1, rect.height())
            return out
        y = rect.top() + (rect.height() - BTN_H) // 2
        x = rect.right() - PAD_R + 1
        more = QRect(x - MORE_W, y, MORE_W, BTN_H) if line.menu else None
        if more is not None:
            x = more.left() - BTN_GAP
        skip_w = self._button_width("Skip", False)
        skip = QRect(x - skip_w, y, skip_w, BTN_H)
        x = skip.left() - BTN_GAP
        action_w = max(self._button_width(line.action, True), self._button_width("Send", True) + 14)
        action = QRect(x - action_w, y, action_w, BTN_H)
        if line.action_enabled:
            out[PART_ACTION] = action
        if line.skip_enabled:
            out[PART_SKIP] = skip
        if more is not None:
            out[PART_MORE] = more
        out["_action"], out["_skip"] = action, skip
        if more is not None:
            out["_more"] = more
        left = rect.left() + ROW_PAD_L
        # The buttons sit in a column as wide as the widest of them (Change source…), so the columns line up.
        right = x - self._button_width(CHANGE_SOURCE_TEXT, True) - COL_GAP
        room = right - left
        size = status = reason = None
        if room >= 300:
            size = QRect(right - SIZE_W, rect.top(), SIZE_W, rect.height())
            right = size.left() - COL_GAP
            room = right - left
        if room >= 260:
            status = QRect(right - STATUS_W, rect.top(), STATUS_W, rect.height())
            right = status.left() - COL_GAP
            room = right - left
        if room >= 320:                            # the two text columns share the rest 1.7 : 1.2 (the mockup's)
            what_w = int((room - COL_GAP) * 1.7 / 2.9)
            reason = QRect(left + what_w + COL_GAP, rect.top(), right - left - what_w - COL_GAP, rect.height())
            right = left + what_w
        out["_what"] = QRect(left, rect.top() + 7, max(right - left, 0), rect.height() - 14)
        out[PART_RELEASE] = QRect(left, rect.center().y(), max(right - left, 0), rect.height() // 2 - 6)
        if reason is not None:
            out[PART_REASON] = reason
        if status is not None:
            out[PART_STATUS] = status
        if size is not None:
            out["_size"] = size
        return out

    def part_at(self, rect: QRect, line: FoundLine, pos: QPoint) -> Optional[str]:
        """The clickable part of the row at *pos* (None: none there, or it cannot be clicked now)."""
        for name, part in self.parts(rect, line).items():
            if name in (PART_MODE, PART_ACTION, PART_SKIP, PART_MORE) and part.contains(pos):
                return name
        if line.is_series and rect.contains(pos):
            return PART_TOGGLE
        return None

    # --- painting -----------------------------------------------------------------------------------------

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        line = index.data(ROLE_LINE)
        if line is None:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = option.rect
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if line.is_series:
            self._paint_header(painter, rect, line, hovered, index.row() == 0)
        else:
            selected = bool(option.state & QStyle.StateFlag.State_Selected)
            painter.fillRect(rect, qcolor("selected") if selected else qcolor("hover") if hovered
                             else qcolor("surface"))
            self._paint_finding(painter, rect, line)
        painter.restore()

    def _rule(self, painter: QPainter, rect: QRect) -> None:
        painter.setPen(QPen(qcolor("row_rule"), 1))
        painter.drawLine(rect.left(), rect.top(), rect.right(), rect.top())

    def _paint_header(self, painter: QPainter, rect: QRect, line: FoundLine, hovered: bool, first: bool) -> None:
        f = self.fonts
        painter.fillRect(rect, qcolor("hover") if hovered and self.hover == (line.key, PART_TOGGLE)
                         else qcolor("surface_subtle"))
        if not first:
            self._rule(painter, rect)
        parts = self.parts(rect, line)
        cy = rect.center().y()
        # the chevron: down while unfolded, right while folded
        painter.setPen(QPen(qcolor("muted"), 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                            Qt.PenJoinStyle.RoundJoin))
        cx = rect.left() + PAD_L + CHEVRON // 2
        path = QPainterPath()
        if line.folded:
            path.moveTo(cx - 2, cy - 4)
            path.lineTo(cx + 2, cy)
            path.lineTo(cx - 2, cy + 4)
        else:
            path.moveTo(cx - 4, cy - 2)
            path.lineTo(cx, cy + 2)
            path.lineTo(cx + 4, cy - 2)
        painter.drawPath(path)
        x = rect.left() + PAD_L + CHEVRON + 10
        end = (parts[PART_MODE].left() - 12) if PART_MODE in parts else rect.right() - PAD_R
        for text, font, token in ((line.name, f.name, "text"), (line.count, f.count, "muted"),
                                  (line.summary if line.folded else "", f.release, "muted_2")):
            if not text or x >= end:
                continue
            fm = f.fm(font)
            shown = fm.elidedText(text, Qt.TextElideMode.ElideRight, end - x)
            painter.setFont(font)
            painter.setPen(qcolor(token))
            painter.drawText(QRect(x, rect.top(), end - x, rect.height()),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, shown)
            x += fm.horizontalAdvance(shown) + 10
        if PART_MODE in parts:
            chip = parts[PART_MODE]
            hot = self.hover == (line.key, PART_MODE)
            painter.setPen(QPen(qcolor("control"), 1))
            painter.setBrush(qcolor("pressed") if self.pressed == (line.key, PART_MODE) else
                             qcolor("hover") if hot else qcolor("surface"))
            box = QRectF(chip).adjusted(0.5, 0.5, -0.5, -0.5)
            painter.drawRoundedRect(box, box.height() / 2, box.height() / 2)
            painter.setFont(f.chip)
            painter.setPen(qcolor("text_2"))
            painter.drawText(chip.adjusted(11, 0, -22, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                             line.mode)
            ax, ay = chip.right() - 14, chip.center().y()
            painter.setPen(QPen(qcolor("text_2"), 1.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                                Qt.PenJoinStyle.RoundJoin))
            arrow = QPainterPath()
            arrow.moveTo(ax - 3, ay - 1.5)
            arrow.lineTo(ax, ay + 1.5)
            arrow.lineTo(ax + 3, ay - 1.5)
            painter.drawPath(arrow)
            painter.setBrush(Qt.BrushStyle.NoBrush)

    def _paint_finding(self, painter: QPainter, rect: QRect, line: FoundLine) -> None:
        f = self.fonts
        self._rule(painter, rect)
        parts = self.parts(rect, line)
        what = parts["_what"]
        if what.width() > 20:
            gets_fm = f.fm(f.gets)
            gets = gets_fm.elidedText(line.gets, Qt.TextElideMode.ElideRight, what.width())
            top = QRect(what.left(), what.top(), what.width(), what.height() // 2)
            painter.setFont(f.gets)
            painter.setPen(qcolor("text"))
            painter.drawText(top, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, gets)
            kind_x = what.left() + gets_fm.horizontalAdvance(gets) + 8
            if kind_x < what.right() - 20:
                painter.setFont(f.kind)
                painter.setPen(qcolor("muted"))
                painter.drawText(QRect(kind_x, top.top(), what.right() - kind_x, top.height()),
                                 Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                                 f.fm(f.kind).elidedText(line.kind, Qt.TextElideMode.ElideRight,
                                                         what.right() - kind_x))
            painter.setFont(f.release)
            painter.setPen(qcolor("muted_2"))
            painter.drawText(QRect(what.left(), top.bottom() + 1, what.width(), what.height() - top.height()),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                             f.fm(f.release).elidedText(line.release, Qt.TextElideMode.ElideRight, what.width()))
        if PART_REASON in parts:
            self._paint_reason(painter, parts[PART_REASON], line.reason)
        if PART_STATUS in parts:
            self._paint_status(painter, parts[PART_STATUS], *line.status)
        if "_size" in parts and line.size:
            painter.setFont(f.size)
            painter.setPen(qcolor("muted"))
            painter.drawText(parts["_size"], Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                             f.fm(f.size).elidedText(line.size, Qt.TextElideMode.ElideRight, parts["_size"].width()))
        self._paint_button(painter, parts["_action"], line.action, line, PART_ACTION, primary=True,
                           enabled=line.action_enabled)
        self._paint_button(painter, parts["_skip"], "Skip", line, PART_SKIP, primary=False, enabled=line.skip_enabled)
        if "_more" in parts:
            self._paint_button(painter, parts["_more"], MENU_BUTTON_TEXT, line, PART_MORE, primary=False, enabled=True,
                               more=True)

    def _paint_reason(self, painter: QPainter, rect: QRect, text: str) -> None:
        """Up to two lines, the second elided (the whole text is the tooltip)."""
        f = self.fonts
        fm = f.fm(f.reason)
        width = rect.width()
        words, lines, current_line = text.split(), [], ""
        for word in words:
            trial = f"{current_line} {word}".strip()
            if fm.horizontalAdvance(trial) <= width or not current_line:
                current_line = trial
            else:
                lines.append(current_line)
                current_line = word
                if len(lines) == 2:
                    break
        if len(lines) < 2 and current_line:
            lines.append(current_line)
        if len(lines) == 2:
            rest = text[len(lines[0]):].strip()
            lines[1] = fm.elidedText(rest, Qt.TextElideMode.ElideRight, width)
        lines = [fm.elidedText(ln, Qt.TextElideMode.ElideRight, width) for ln in lines[:2]]
        height = fm.height() * len(lines)
        y = rect.top() + (rect.height() - height) // 2
        painter.setFont(f.reason)
        painter.setPen(qcolor("muted"))
        for ln in lines:
            painter.drawText(QRect(rect.left(), y, width, fm.height()),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, ln)
            y += fm.height()

    def _paint_status(self, painter: QPainter, rect: QRect, text: str, kind: str) -> None:
        f = self.fonts
        bg, fg, border = chip_colors().get(kind, chip_colors()["muted"])
        spinner = kind == "run" and text.startswith("Sending")
        width = min(rect.width(), f.fm(f.chip).horizontalAdvance(text) + 20 + (18 if spinner else 0))
        box = QRectF(rect.left() + 0.5, rect.center().y() - CHIP_H / 2 + 0.5, width - 1, CHIP_H - 1)
        painter.setPen(QPen(hex_color(border), 1))
        painter.setBrush(hex_color(bg))
        painter.drawRoundedRect(box, CHIP_H / 2, CHIP_H / 2)
        x = rect.left() + 9
        if spinner:                                 # a quarter arc over a faint ring, as the mockup's
            ring = QRectF(x, box.center().y() - 5.5, 11, 11)
            color = hex_color(fg)
            faint = QColor(color)
            faint.setAlphaF(0.35)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(faint, 1.8))
            painter.drawEllipse(ring)
            painter.setPen(QPen(color, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawArc(ring, 0, 90 * 16)
            x += 18
        painter.setFont(f.chip)
        painter.setPen(hex_color(fg))
        painter.drawText(QRect(x, int(box.top()), int(box.right()) - x - 8, CHIP_H),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         f.fm(f.chip).elidedText(text, Qt.TextElideMode.ElideRight, int(box.right()) - x - 8))

    def _paint_button(self, painter: QPainter, rect: QRect, text: str, line: FoundLine, part: str, *, primary: bool,
                      enabled: bool, more: bool = False) -> None:
        """A button as the Download tab's stylesheet draws one (primary: filled with the accent), with its hover and
        pressed fills."""
        f = self.fonts
        hot = enabled and self.hover == (line.key, part)
        down = enabled and self.pressed == (line.key, part)
        if primary:
            if not enabled:
                fill, edge, ink = qcolor("accent_fill_disabled"), qcolor("accent_fill_disabled"), \
                    qcolor("accent_text_disabled")
            else:
                fill = qcolor("accent_fill_hover") if down else qcolor("accent_fill_hover_2") if hot \
                    else qcolor("accent_fill")
                edge, ink = fill, qcolor("accent_text")
        elif not enabled:
            fill, edge, ink = qcolor("background"), qcolor("border"), qcolor("disabled")
        else:
            fill = qcolor("pressed") if down else qcolor("hover") if hot else qcolor("surface")
            edge, ink = qcolor("control"), qcolor("text_2") if more else qcolor("text")
        box = QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(QPen(edge, 1))
        painter.setBrush(fill)
        painter.drawRoundedRect(box, 6, 6)
        painter.setFont(f.more if more else f.primary if primary else f.button)
        painter.setPen(ink)
        painter.drawText(rect.adjusted(0, -2 if more else 0, 0, 0), Qt.AlignmentFlag.AlignCenter, text)

    # --- the mouse ----------------------------------------------------------------------------------------

    def _repaint(self, view, *keys) -> None:
        model = view.model() if view is not None else None
        if model is None:
            return
        for key in keys:
            row = model.row_of(key) if key is not None else None
            if row is not None:
                view.viewport().update(view.visualRect(model.index(row)))

    def set_hover(self, view, hover) -> None:
        if hover != self.hover:
            old, self.hover = self.hover, hover
            self._repaint(view, old[0] if old else None, hover[0] if hover else None)
            if view is not None:
                view.viewport().setCursor(Qt.CursorShape.PointingHandCursor if hover is not None and
                                          hover[1] != PART_TOGGLE else Qt.CursorShape.ArrowCursor)

    def editorEvent(self, event, model, option, index) -> bool:
        line = index.data(ROLE_LINE)
        view = option.widget
        if line is None:
            return False
        kind = event.type()
        if kind not in (QEvent.Type.MouseMove, QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease,
                        QEvent.Type.MouseButtonDblClick):
            return False
        pos = event.position().toPoint()
        part = self.part_at(option.rect, line, pos)
        if kind == QEvent.Type.MouseMove:
            self.set_hover(view, (line.key, part) if part is not None else None)
            return False
        if event.button() != Qt.MouseButton.LeftButton:
            return False
        if kind == QEvent.Type.MouseButtonDblClick:
            return part is not None                 # the second click of a double click does nothing more
        if kind == QEvent.Type.MouseButtonPress:
            if part is None:
                return False                        # a press elsewhere selects the row
            self.pressed = (line.key, part)
            self._repaint(view, line.key)
            return True
        pressed, self.pressed = self.pressed, None   # the release
        if pressed is not None:
            self._repaint(view, pressed[0])
        if part is not None and pressed == (line.key, part):
            rect = self.parts(option.rect, line).get(part, option.rect)
            where = view.viewport().mapToGlobal(rect.bottomLeft()) if view is not None else QPoint()
            self.part_clicked.emit(line.key, part, where)
            return True
        return part is not None

    def helpEvent(self, event, view, option, index) -> bool:
        line = index.data(ROLE_LINE) if index.isValid() else None
        if line is None or event.type() != QEvent.Type.ToolTip:
            return super().helpEvent(event, view, option, index)
        tip = self.tooltip(option.rect, line, event.pos())
        if tip:
            QToolTip.showText(event.globalPos(), tip, view)
        else:
            QToolTip.hideText()
            event.ignore()
        return True

    def tooltip(self, rect: QRect, line: FoundLine, pos: QPoint) -> str:
        """What the part of the row at *pos* says on hover."""
        parts = self.parts(rect, line)
        if line.is_series:
            if PART_MODE in parts and parts[PART_MODE].contains(pos):
                return line.mode_tip
            return f"{line.name}: {line.count}\n" + ("Click to show its findings" if line.folded else
                                                      "Click to fold its findings")
        for name, tip in ((PART_ACTION if line.action_enabled else "_action",
                           SOURCE_TIP if line.needs_source else SEND_TIP),
                          (PART_SKIP if line.skip_enabled else "_skip", SKIP_TIP),
                          ("_more", MORE_TIP),
                          (PART_REASON, f"Waits because {line.finding.reason}" if line.finding.reason else
                           "Waits for you"),
                          (PART_STATUS, STATUS_TIPS.get(line.status[0], "Sent one after another, in this order")),
                          (PART_RELEASE, line.release)):
            if name in parts and parts[name].contains(pos):
                return tip
        return f"{line.name}: {line.gets} ({line.kind})"


class FoundList(QListView):
    """The list the delegate paints in: the mouse is tracked for the hover, and the hover / press are cleared when the
    mouse leaves. Enter and Delete on the selected row are :attr:`key_part` (Send / Skip); taking the focus without a
    finding selected is :attr:`focused`."""

    key_part = Signal(str)          # PART_ACTION (Enter) / PART_SKIP (Delete) on the current row
    focused = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("foundList")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setUniformItemSizes(False)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def mouseMoveEvent(self, event) -> None:
        super().mouseMoveEvent(event)
        self.track(event.position().toPoint())

    def track(self, pos: QPoint) -> None:
        """The painted part under the mouse at *pos* (viewport coordinates) gets the hover."""
        delegate = self.itemDelegate()
        if not isinstance(delegate, FoundDelegate):
            return
        index = self.indexAt(pos)
        line = index.data(ROLE_LINE) if index.isValid() else None
        part = delegate.part_at(self.visualRect(index), line, pos) if line is not None else None
        delegate.set_hover(self, (line.key, part) if part is not None else None)

    def viewportEvent(self, event) -> bool:
        if event.type() == QEvent.Type.Leave:
            delegate = self.itemDelegate()
            if isinstance(delegate, FoundDelegate):
                delegate.set_hover(self, None)
        return super().viewportEvent(event)

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier:
            self.key_part.emit(PART_ACTION)
            event.accept()
            return
        if key == Qt.Key.Key_Delete and not event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier:
            self.key_part.emit(PART_SKIP)
            event.accept()
            return
        super().keyPressEvent(event)

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self.focused.emit()

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        delegate = self.itemDelegate()
        if isinstance(delegate, FoundDelegate) and delegate.pressed is not None:   # released off any row
            pressed, delegate.pressed = delegate.pressed, None
            delegate._repaint(self, pressed[0])


class FoundView(QWidget):
    changed = Signal()              # findings were sent or skipped: the host reloads them (and the downloads)
    change_source = Signal(int)     # a series id: open it in Missing chapters to pick its source
    mode_changed = Signal()         # a series' automatic-download mode was set here: the List's chips follow
    series_menu_requested = Signal(int, QPoint)     # a row's "⋯": (series id, where) - the host opens the menu

    def __init__(self, backend, parent: Optional[QWidget] = None, confirm: Optional[ConfirmFn] = None):
        super().__init__(parent)
        self.setObjectName("foundView")
        self._backend = backend
        self._confirm = confirm or self._ask
        self._findings: List = []
        self._names: Mapping[int, str] = {}
        self._run = None
        self._series: Optional[int] = None          # show only this series' findings (the list on the left)
        self._call: Optional[BackgroundCall] = None
        self._sending: Optional[int] = None         # the finding being sent now
        self._queue: List[int] = []                 # findings waiting to be sent, in order
        self._sending_text = ""
        self._last_outcome = ("", "")
        self._busy_text = ""
        self._series_menu = False                   # the rows' "⋯" (the host handles series_menu_requested)
        self._folded: set = set()                   # series ids whose findings are folded away (this session)
        self._needle = ""                           # the filter box's text, case-folded
        self._modes: Dict[int, Tuple[str, str]] = {}    # series id -> its header's mode chip (text, tooltip), read once
        self._dirty = True                          # the list has not caught up with the findings (hidden meanwhile)
        self._refocus: Optional[int] = None         # a key skipped the finding at this row: select the one in its place

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)
        self.title_label = label("Found", "h2")
        self.count_label = label("", "muted")
        self.btn_show_all = button("", link=True, tip="Show what was found for every series")
        self.btn_show_all.clicked.connect(lambda: self.set_series_filter(None))
        self.filter_edit = QLineEdit()
        self.filter_edit.setObjectName("foundFilter")
        self.filter_edit.setPlaceholderText("Filter series or release")
        self.filter_edit.setAccessibleName("Filter findings")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.setMaximumWidth(260)
        self.filter_edit.textChanged.connect(self.set_text_filter)
        self.btn_send_all = button("Send all…", tip="Send every finding listed here (asks first)")
        self.btn_send_all.clicked.connect(self.send_all)
        head = hbox(self.title_label, self.count_label, None, self.btn_show_all, self.filter_edit, self.btn_send_all,
                    spacing=12)
        head.setStretchFactor(self.filter_edit, 1)
        outer.addLayout(head)
        # One series shown: its automatic-download mode, changed in place.
        self.mode_combo = QComboBox()
        self.mode_combo.setObjectName("foundMode")
        self.mode_combo.setToolTip("This series' automatic downloads; the default is its library's (Settings > Library) "
                                   "or else the one in Settings > Automation")
        self.mode_combo.activated.connect(self._mode_chosen)
        self.mode_help = label("", "muted", wrap=True)
        self.mode_row = QWidget()
        mode_line = hbox(label("Automatic downloads", "muted"), self.mode_combo, self.mode_help, spacing=8)
        mode_line.setStretchFactor(self.mode_help, 1)
        self.mode_row.setLayout(mode_line)
        self.mode_row.setVisible(False)
        outer.addWidget(self.mode_row)
        self.run_label = label("", "muted", wrap=True)
        outer.addWidget(self.run_label)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setFixedWidth(90)
        self.progress.setTextVisible(False)
        self.busy_label = label("", "muted", wrap=True)
        self.busy_row = QWidget()
        busy = hbox(self.progress, self.busy_label)
        busy.setStretchFactor(self.busy_label, 1)
        self.busy_row.setLayout(busy)
        self.busy_row.setVisible(False)
        outer.addWidget(self.busy_row)
        self.status_label = label("", wrap=True, selectable=True)
        self.status_label.setVisible(False)
        outer.addWidget(self.status_label)

        self.empty = card("dashed")
        ev = QVBoxLayout(self.empty)
        ev.setContentsMargins(24, 28, 24, 28)
        ev.setSpacing(6)
        self.empty_title = label(EMPTY_TITLE, "h3")
        self.empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_text = label("", "muted", wrap=True)
        self.empty_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ev.addWidget(self.empty_title)
        ev.addWidget(self.empty_text)
        self.empty.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)   # its own height, at the top
        outer.addWidget(self.empty)

        # The findings: one list, one delegate - no widget per finding.
        self.model = FoundModel(self)
        self.delegate = FoundDelegate(self)
        self.delegate.part_clicked.connect(self._on_part_clicked)
        self.list = FoundList()
        self.list.setModel(self.model)
        self.list.setItemDelegate(self.delegate)
        self.list.key_part.connect(self.key_action)
        self.list.focused.connect(self._select_first_if_none)
        self.box = QFrame()
        self.box.setObjectName("foundBox")
        self.box.setProperty("card", "true")        # the bordered, rounded box of the mockup
        bv = QVBoxLayout(self.box)
        bv.setContentsMargins(1, 1, 1, 1)
        bv.addWidget(self.list)
        outer.addWidget(self.box, 1)
        outer.addStretch(0)                     # takes the room while the list is hidden (the empty state)
        self._show()

    # --- data ----------------------------------------------------------------------------------------

    def set_findings(self, findings: Sequence, names: Mapping[int, str], last_run=None) -> None:
        """The waiting findings, the series names (series id -> name) and the last find pass. Unchanged, nothing in
        the list is touched."""
        self._findings = [f for f in findings if f.status == "waiting"]
        self._names = dict(names)
        self._run = last_run
        if self._series is not None and not any(f.series_id == self._series for f in self._findings):
            self._series = None
        self._show()

    def set_series_filter(self, series_id: Optional[int]) -> None:
        """Show one series' findings (None: every series)."""
        self._series = series_id
        self._show()

    def series_filter(self) -> Optional[int]:
        return self._series

    def set_text_filter(self, text: str) -> None:
        """Show only the findings whose series, release or numbers hold *text* (any case; '' shows all)."""
        needle = (text or "").strip().casefold()
        if needle == self._needle:
            return
        self._needle = needle
        if self.filter_edit.text().strip().casefold() != needle:
            self.filter_edit.setText(text)
        self._show()

    def _matches(self, finding) -> bool:
        if not self._needle:
            return True
        hay = f"{self.name_of(finding.series_id)}\n{finding.title}\n{gets_text(finding)}".casefold()
        return self._needle in hay

    def shown(self) -> List:
        """The findings listed now (the series picked, the filter box applied), top to bottom - folded ones too."""
        rows = [f for f in self._findings if (self._series is None or f.series_id == self._series) and self._matches(f)]
        return sorted(rows, key=lambda f: (self.name_of(f.series_id).casefold(), f.series_id, f.id))

    def name_of(self, series_id: int) -> str:
        return self._names.get(series_id) or f"Series #{series_id}"

    def waiting_count(self) -> int:
        return len(self._findings)

    def set_busy(self, text: str) -> None:
        """A find pass runs for this view ('' when done): a progress line, and Send / Skip wait for it."""
        self._busy_text = text
        self.busy_label.setText(text)
        self.busy_row.setVisible(bool(text))
        self._enable()

    def busy(self) -> bool:
        return bool(self._busy_text) or self._call is not None or bool(self._queue)

    def queued(self) -> List[int]:
        """The findings waiting to be sent, in order (not the one being sent)."""
        return list(self._queue)

    def say(self, text: str, tone: str = "") -> None:
        set_tone(self.status_label, tone if text else "")
        self.status_label.setText(text)
        self.status_label.setVisible(bool(text))

    # --- the rows ------------------------------------------------------------------------------------

    @property
    def rows(self) -> Dict[int, FoundLine]:
        """finding id -> its row as painted now (unfolded series only), top to bottom."""
        self._sync()
        return {line.finding.id: line for line in self.model.lines() if not line.is_series}

    def headers(self) -> Dict[int, FoundLine]:
        """series id -> its header row as painted now."""
        self._sync()
        return {line.series_id: line for line in self.model.lines() if line.is_series}

    def fold(self, series_id: int, folded: Optional[bool] = None) -> None:
        """Fold (or unfold) a series' findings under its header (None: the other way round)."""
        folded = series_id not in self._folded if folded is None else folded
        (self._folded.add if folded else self._folded.discard)(series_id)
        self._refresh()

    def click(self, finding_or_series_id: int, part: str) -> None:
        """Click a painted part as the mouse would (``action`` / ``skip`` / ``more`` of a finding, ``toggle`` /
        ``mode`` of a series header) - when it can be clicked now. For tests and keyboard helpers; the mouse comes in
        through :meth:`FoundDelegate.editorEvent`."""
        self._sync()
        key = ("series" if part in (PART_TOGGLE, PART_MODE) else "finding", finding_or_series_id)
        row = self.model.row_of(key)
        if row is None:
            return
        line = self.model.line_at(row)
        rect = self.list.visualRect(self.model.index(row))
        if rect.isEmpty():
            rect = QRect(0, 0, max(self.list.viewport().width(), 900), HEADER_H if line.is_series else ROW_H)
        part_rect = self.delegate.parts(rect, line).get(part)
        if part_rect is None:
            return                              # not clickable now (a disabled button, no menu)
        self._on_part_clicked(key, part, self.list.viewport().mapToGlobal(part_rect.bottomLeft()))

    def _on_part_clicked(self, key, part: str, where: QPoint) -> None:
        kind, ident = key
        if kind == "series":
            if part == PART_MODE:
                self._open_mode_menu(ident, where)
            else:
                self.fold(ident)
            return
        finding = self._finding(ident)
        if finding is None:
            return
        if part == PART_ACTION:
            if needs_source(finding):
                self.change_source.emit(finding.series_id)
            else:
                self.send(ident)
        elif part == PART_SKIP:
            self.skip(ident)
        elif part == PART_MORE and self._series_menu:
            self.series_menu_requested.emit(finding.series_id, where)

    # --- showing -------------------------------------------------------------------------------------

    def _show(self) -> None:
        shown = self.shown()
        filtered = self._series is not None
        self.title_label.setText(f"Found for {self.name_of(self._series)}" if filtered else "Found")
        self.title_label.setWordWrap(filtered)
        self.count_label.setText(total_text(shown))
        self.count_label.setVisible(bool(shown))
        self.btn_show_all.setText(f"Show all ({len(self._findings)})")
        self.btn_show_all.setVisible(filtered)
        self.filter_edit.setVisible(bool(self._findings))
        n_send = len(sendable(shown))
        self.btn_send_all.setText(f"Send all {n_send}…" if n_send > 1 else "Send all…")
        run = last_run_text(self._run)
        self.run_label.setText(run)
        self.run_label.setVisible(bool(shown))
        self.empty_text.setText(run if self._findings or not self._needle else "Nothing matches the filter.")
        self.empty_title.setText(EMPTY_TITLE if not self._findings or not self._needle else "No finding matches")
        self.empty.setVisible(not shown)
        self.box.setVisible(bool(shown))
        self._show_mode()
        self._enable()

    def _refresh(self) -> None:
        """Bring the list up to date with the findings - now while it is shown, else when it is shown next."""
        self._dirty = True
        if self.isVisible():
            self._sync()

    def _sync(self) -> None:
        if not self._dirty:
            return
        self._dirty = False
        self.model.set_lines(self._lines())
        if self._refocus is not None and self._finding(self._refocus_id) is None:
            row, self._refocus = self._refocus, None
            self._select_near(row)

    # --- the keyboard --------------------------------------------------------------------------------

    _refocus_id: Optional[int] = None
    _last_skipped: Optional[int] = None

    def current_line(self) -> Optional[FoundLine]:
        """The selected row (a finding or a series header), None when none is."""
        index = self.list.currentIndex()
        return self.model.line_at(index.row()) if index.isValid() else None

    def select(self, finding_id: int) -> bool:
        """Select a finding's row (as the arrow keys would); False when it is not shown."""
        self._sync()
        row = self.model.row_of(("finding", finding_id))
        if row is None:
            return False
        index = self.model.index(row)
        self.list.setCurrentIndex(index)
        self.list.scrollTo(index)
        return True

    def _select_near(self, row: int) -> None:
        """Select the finding at *row* or the next one below it, else the nearest above (after a skip)."""
        lines = self.model.lines()
        below = [i for i in range(max(row, 0), len(lines)) if not lines[i].is_series]
        above = [i for i in range(min(row, len(lines)) - 1, -1, -1) if not lines[i].is_series]
        pick = below[0] if below else above[0] if above else None
        if pick is not None:
            self.list.setCurrentIndex(self.model.index(pick))
            self.list.scrollTo(self.model.index(pick))

    def _select_first_if_none(self) -> None:
        line = self.current_line()
        if line is None or line.is_series:
            self._sync()
            current = self.list.currentIndex()
            self._select_near(current.row() if current.isValid() else 0)

    def key_action(self, part: str) -> None:
        """Enter (``action``) / Delete (``skip``) on the selected row: Send / Skip a finding as its painted buttons do
        (only when they can be clicked now); Enter on a series header folds or unfolds it."""
        line = self.current_line()
        if line is None:
            return
        if line.is_series:
            if part == PART_ACTION:
                self.fold(line.series_id)
            return
        fid = line.finding.id
        if part == PART_SKIP:
            self._refocus, self._refocus_id = self.model.row_of(line.key), fid
            self._last_skipped = None
        self.click(fid, part)
        if part == PART_SKIP and self._last_skipped != fid:
            self._refocus = None                    # not skipped (a pass runs, it is being sent, refused): it stays

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._sync()

    def _lines(self) -> List[FoundLine]:
        shown = self.shown()
        groups = by_series(shown)
        modes = self._mode_chips(list(groups))
        passing = bool(self._busy_text)
        out: List[FoundLine] = []
        for sid, items in groups.items():
            name = self.name_of(sid)
            folded = sid in self._folded
            mode, mode_tip = modes.get(sid, ("", ""))
            out.append(FoundLine(("series", sid), sid, name, count=total_text(items), summary=series_line(items),
                                 folded=folded, mode=mode, mode_tip=mode_tip))
            if folded:
                continue
            for f in items:
                state = "sending" if f.id == self._sending else "queued" if f.id in self._queue else ""
                pos = self._queue.index(f.id) + 1 if state == "queued" else 0
                out.append(FoundLine(("finding", f.id), sid, name, f, state=state, queue_pos=pos, passing=passing,
                                     menu=self._series_menu))
        return out

    def _mode_chips(self, series_ids: Sequence[int]) -> Dict[int, Tuple[str, str]]:
        """series id -> its header's mode chip, read for the series not read yet: ONE read of their own modes and one
        of their defaults for all of them (owner, 2026-10-15: the chip shows a series' own mode exactly, also when it
        equals the default); {} without automatic downloads. A backend without ``own_series_modes`` gives the mode in
        force, which tells an own mode from the default only where they differ."""
        own_fn = getattr(self._backend, "own_series_modes", None)
        if not callable(own_fn) and not callable(getattr(self._backend, "series_modes", None)):
            return {}
        missing = [sid for sid in series_ids if sid not in self._modes]
        if missing:
            try:
                defaults = defaults_of(self._backend, missing)
                if callable(own_fn):
                    own = dict(own_fn(missing))
                else:
                    in_force = dict(self._backend.series_modes(missing))
                    own = {sid: m for sid, m in in_force.items() if m != defaults.get(sid, ("", False))[0]}
            except Exception as exc:  # noqa: BLE001 - the chips stay off; nothing else depends on them
                _log.warning("Found: the modes could not be read: %s", describe_error(exc))
                return dict(self._modes)
            for sid in missing:
                default, library = defaults.get(sid, ("", False))
                self._modes[sid] = mode_chip(own.get(sid), default, library) if default else ("", "")
        return dict(self._modes)

    def set_series_menu_enabled(self, on: bool) -> None:
        """Offer each row's "⋯" (the host opens the series' menu on :attr:`series_menu_requested`)."""
        self._series_menu = bool(on)
        self._refresh()

    # --- the series' mode ----------------------------------------------------------------------------

    def refresh_mode(self) -> None:
        """Read the modes again (they were changed on another screen)."""
        self._modes.clear()
        self._show_mode()
        self._refresh()

    def _show_mode(self) -> None:
        """One series shown: its own mode ticked (or "Use the default (…)"), and what the mode in force does."""
        sid = self._series
        modes = callable(getattr(self._backend, "own_series_mode", None))
        self.mode_row.setVisible(sid is not None and modes)
        if sid is None or not modes:
            return
        try:
            own = self._backend.own_series_mode(sid)
            default, library = default_of(self._backend, sid)
        except Exception as exc:  # noqa: BLE001 - the line says why; nothing else depends on it
            self.mode_row.setVisible(False)
            _log.warning("Found: the mode of series %s could not be read: %s", sid, describe_error(exc))
            return
        self.mode_combo.blockSignals(True)
        self.mode_combo.clear()
        for mode in MODES:
            self.mode_combo.addItem(MODE_LABELS[mode], mode)
        self.mode_combo.addItem(default_entry_label(default, library), None)
        self.mode_combo.setItemData(len(MODES), default_entry_tip(library), Qt.ItemDataRole.ToolTipRole)
        self.mode_combo.setCurrentIndex(MODES.index(own) if own in MODES else len(MODES))
        self.mode_combo.blockSignals(False)
        in_force = own if own in MODES else default
        help_text = MODE_HELP.get(in_force, "")
        if in_force == MODE_AUTO:
            help_text += " What waits here goes at the next scheduled find pass or Find now (a bulk find sends nothing)."
        self.mode_help.setText(help_text)

    def _mode_chosen(self, index: int) -> None:
        if self._series is not None:
            self.set_mode(self._series, self.mode_combo.itemData(index))

    def _exec_menu(self, menu: QMenu, pos: QPoint):
        """Open *menu* at *pos* and return the chosen action (one place, so tests can answer for the owner)."""
        return menu.exec(pos)

    def _open_mode_menu(self, series_id: int, where: QPoint) -> None:
        """A series header's mode chip: Off / Notify / Auto / Use the default (…), the series' own ticked."""
        menu = QMenu(self)
        modes = add_mode_menu(menu, self._backend, [series_id])
        sub = menu.actions()[0].menu() if menu.actions() else None
        if not modes or sub is None:
            return
        chosen = self._exec_menu(sub, where)
        if chosen in modes:
            self.set_mode(series_id, modes[chosen])

    def set_mode(self, series_id: int, mode: Optional[str]) -> bool:
        """Set a series' own automatic-download mode (None: back to its default); the status line says so."""
        try:
            self._backend.set_series_mode([series_id], mode)
        except Exception as exc:  # noqa: BLE001 - shown; the picker shows the stored mode again
            self.say(f"Could not change the automatic downloads: {describe_error(exc)}", "bad")
            self._show_mode()
            return False
        _log.info("Found: automatic downloads %s for series %s", mode or "the default", series_id)
        name = self.name_of(series_id)
        whose = "the default"
        if not mode:
            try:
                whose = "the library's default" if default_of(self._backend, series_id)[1] else "the default"
            except Exception:  # noqa: BLE001 - the wording only
                pass
        self.say(f"Automatic downloads for {name}: {MODE_LABELS[mode]}." if mode else
                 f"Automatic downloads for {name}: {whose} again.", "ok")
        self._modes.pop(series_id, None)
        self._show_mode()
        self._refresh()
        self.mode_changed.emit()
        return True

    def _enable(self) -> None:
        free = not self.busy()
        several = len(sendable(self.shown())) > 1
        self.btn_send_all.setVisible(several)
        self.btn_send_all.setEnabled(free and several)
        self._refresh()                             # the rows' states (sending, queued, a pass running)

    # --- actions -------------------------------------------------------------------------------------

    def _finding(self, finding_id: int):
        return next((f for f in self._findings if f.id == finding_id), None)

    def send(self, finding_id: int) -> bool:
        """Send one finding (off the UI thread); the line under the header says what happened. While another send runs
        it is queued and sent after the ones before it."""
        finding = self._finding(finding_id)
        if finding is None or self._busy_text or finding_id == self._sending or finding_id in self._queue:
            return False
        if self._call is not None:
            self._queue.append(finding_id)
            _log.info("Found: finding %s queued (%d waiting)", finding_id, len(self._queue))
            self._say_sending()
            self._enable()
            return True
        self._start_send(finding)
        return True

    def _start_send(self, finding) -> None:
        finding_id, name = finding.id, self.name_of(finding.series_id)
        _log.info("Found: sending finding %s (%s, %s)", finding.id, finding.kind, gets_text(finding))
        self._sending = finding_id
        self._sending_text = f"Sending {gets_text(finding)} of {name}..."
        self._say_sending()
        backend = self._backend
        self._call = start_call(lambda: backend.send_finding(finding_id),
                                lambda outcome: self._sent(f"{name} {gets_text(finding)} - {outcome}", "ok"),
                                lambda message: self._sent(f"Could not send {gets_text(finding)} of {name}: {message}",
                                                           "bad"),
                                self._send_finished)
        self._enable()

    def _say_sending(self) -> None:
        more = len(self._queue)
        self.say(self._sending_text + (f" ({more} more queued)" if more else ""))

    def _sent(self, text: str, tone: str) -> None:
        self._last_outcome = (text, tone)
        self.say(text, tone)

    def _send_finished(self) -> None:
        """One send is done: the next queued finding (still waiting) goes; the host reloads either way (the queue is
        kept here, so the reload does not lose it)."""
        self._call, self._sending = None, None
        while self._queue:
            finding = self._finding(self._queue.pop(0))
            if finding is not None and finding.status == "waiting":
                text, tone = self._last_outcome
                self._start_send(finding)
                if text:                            # the last outcome stays readable beside the next "Sending"
                    self.say(f"{text}  ·  {self.status_label.text()}", tone)
                break
        self._enable()
        self.changed.emit()

    def skip(self, finding_id: int) -> bool:
        """Skip one finding: it leaves the list and is never offered again (quick)."""
        finding = self._finding(finding_id)
        if finding is None or self._busy_text or finding_id == self._sending:
            return False
        name = self.name_of(finding.series_id)
        if finding_id in self._queue:
            self._queue.remove(finding_id)
        try:
            self._backend.skip_finding(finding_id)
        except Exception as exc:  # noqa: BLE001 - shown to the owner
            self.say(f"Could not skip it: {describe_error(exc)}", "bad")
            return False
        _log.info("Found: skipped finding %s (%s, %s)", finding.id, finding.kind, gets_text(finding))
        self._last_skipped = finding_id
        self.say(f"Skipped {gets_text(finding)} of {name}: it is not offered again.")
        self.changed.emit()
        return True

    def skip_series(self, series_id: int) -> int:
        """Skip every finding of *series_id* waiting here (the row menu's "Skip all"), but the one being sent; how many
        were skipped. Never offered again, like Skip."""
        if self._busy_text:
            return 0
        ids = [f.id for f in self._findings if f.series_id == series_id and f.id != self._sending]
        done, error = 0, ""
        for fid in ids:
            if fid in self._queue:
                self._queue.remove(fid)
            try:
                self._backend.skip_finding(fid)
            except Exception as exc:  # noqa: BLE001 - the others still go; the first error is shown
                error = error or describe_error(exc)
                continue
            done += 1
        _log.info("Found: skipped %d finding(s) of series %s", done, series_id)
        name = self.name_of(series_id)
        if error:
            self.say(f"Skipped {done} of {len(ids)} for {name}; could not skip the rest: {error}", "bad")
        else:
            self.say(f"Skipped {done} finding{'s' if done != 1 else ''} of {name}: not offered again.")
        if done:
            self.changed.emit()
        return done

    def _ask(self, title: str, text: str) -> bool:
        box = QMessageBox(QMessageBox.Icon.Question, title, text, parent=self)
        yes = box.addButton("Send all", QMessageBox.ButtonRole.AcceptRole)
        no = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(no)
        box.exec()
        return box.clickedButton() is yes

    def send_all(self) -> bool:
        """Send every finding listed that has something to send (after the owner's yes), one after another off the UI
        thread."""
        shown = sendable(self.shown())
        if not shown or self.busy():
            return False
        title, text = send_all_question(shown)
        if not self._confirm(title, text):
            return False
        backend = self._backend
        items = [(f.id, f"{self.name_of(f.series_id)} {gets_text(f)}") for f in shown]
        _log.info("Found: sending all %d findings", len(items))

        def run():
            outcomes = []
            for fid, what in items:
                try:
                    backend.send_finding(fid)
                    outcomes.append((what, None))
                except Exception as exc:  # noqa: BLE001 - one failure does not stop the others
                    outcomes.append((what, describe_error(exc)))
            return outcomes

        self.say(f"Sending {len(items)}...")
        self._call = start_call(run, lambda outcomes: self.say(*send_all_summary(outcomes)),
                                lambda message: self.say(f"Could not send them: {message}", "bad"),
                                self._call_finished)
        self._enable()
        return True

    def _call_finished(self) -> None:
        self._call = None
        self._enable()
        self.changed.emit()

    def stop(self) -> None:
        self._queue.clear()
        if self._call is not None:
            self._call.abandon()
            self._call = None
        self._sending = None

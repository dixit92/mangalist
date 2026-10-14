"""The Download tab's **Found** view (automatic downloads, owner decisions 2026-10-10): what find passes found that waits
for the owner - one row per finding with its series, what it would get, its size and why it waits, **Send** / **Skip**
per row and **Send all** (asks first, with the count and the total size). Empty, it says "Nothing found waiting for you"
and when the last pass ran.

A chapter finding with nothing to send - the series' source is gone or lacks the chapters, or it has none yet - shows
**Change source…** instead of Send: it opens the series in Missing chapters, where its source is picked (owner,
2026-10-12: "there's no option here to change the source"); Send all leaves such findings out. Showing one series, the
header has its **Automatic downloads** mode (Off / Notify / Auto / the default; owner, 2026-10-12: "I should also be able
to switch a series from notify to auto here"); its "Use the default" names whose default it is - the series' library's
or the one in Settings > Automation. Each card's **⋯** opens the series' menu (the Download tab builds it with
:mod:`.series_actions`: Show in To get, Show in List, Open folder, Automatic downloads, Skip all of this series).

Sends run one at a time (a pack may take a while: its file list is read to narrow it). **Send** while one runs queues the
finding - its button says "Queued" - and the queue is worked through in order (owner, 2026-10-12: "Am I not able to
queue sends?"); Skip and Change source stay usable meanwhile, and skipping a queued finding takes it off the queue.

The rows are the backend's findings (:class:`~mangalist.store.findings.Finding`, ``findings()``); sending runs off the
UI thread (``send_finding`` may add a torrent to qBittorrent or queue it under the download budget, or enqueue chapters
in Suwayomi) and says what happened, as the backend words it; skipping is quick and the pick is never offered again.
A pick a pass sent by itself is not here: it is an ordinary download in the In progress list.

The Download tab feeds it (:meth:`FoundView.set_findings`) and reloads it when :attr:`FoundView.changed` fires.
"""

from __future__ import annotations

import logging
from typing import Callable, Dict, List, Mapping, Optional, Sequence

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QMessageBox,
    QProgressBar,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .automation_rules import (
    EMPTY_TITLE,
    MODE_AUTO,
    MODE_HELP,
    MODE_LABELS,
    MODES,
    default_entry_label,
    default_entry_tip,
    default_of,
    gets_text,
    kind_text,
    last_run_text,
    needs_source,
    sendable,
    send_all_question,
    send_all_summary,
    size_text,
    total_text,
)
from .background import BackgroundCall, describe_error, start_call
from .download_style import set_tone
from .download_widgets import button, card, hbox, label, pill
from .series_actions import below, menu_button

_log = logging.getLogger(__name__)

ConfirmFn = Callable[[str, str], bool]


class FoundRow(QFrame):
    """One finding: the series and the kind on top, what it would get and the release under them, then why it waits;
    its size, Send and Skip at the right."""

    def __init__(self, finding, series_name: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.finding = finding
        self.setObjectName("foundRow")
        self.setProperty("card", "true")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(4)
        self.name_label = label(series_name, "name", wrap=True)
        self.kind_pill = pill(kind_text(finding), "muted")
        self.size_label = label(size_text(finding.size_bytes), "mono")
        self.btn_send = button("Send", primary=True, tip="Send it now: a torrent goes to qBittorrent (or waits in the "
                                                         "queue past the download budget), chapters to Suwayomi")
        self.btn_skip = button("Skip", tip="Do not get it: it leaves the list and is not offered again")
        # Nothing to send until the series has a (another) source: picking one is the way on.
        self.btn_source = button("Change source…", primary=True,
                                 tip="Open the series in Missing chapters to pick where its chapters come from: "
                                     "MangaDex or your other Suwayomi sources. Remembered for the series")
        self.needs_source = needs_source(finding)
        first = self.btn_source if self.needs_source else self.btn_send
        (self.btn_send if self.needs_source else self.btn_source).setVisible(False)
        self.size_label.setVisible(not self.needs_source)          # nothing to download yet: no size to tell
        # The series' name alone on the first line with the actions; the kind and the numbers under it (owner: title
        # and counts clearly apart).
        self.btn_more = menu_button("This series: Show in To get (its other releases or chapters), Show in List, "
                                    "Open folder, Automatic downloads, Skip all of its findings")
        self.btn_more.setVisible(False)                             # shown when the host builds the menu
        top = hbox(self.name_label, self.size_label, first, self.btn_skip, self.btn_more, spacing=10)
        top.setStretchFactor(self.name_label, 1)
        lay.addLayout(top)
        self.gets_label = label(f"Gets {gets_text(finding)}", "lead")
        lay.addLayout(hbox(self.kind_pill, self.gets_label, None, spacing=8))
        self.release_label = label(finding.title, "mono", wrap=True, selectable=True)
        self.reason_label = label(f"Waits because {finding.reason}" if finding.reason else "Waits for you", "muted",
                                  wrap=True)
        for widget in (self.release_label, self.reason_label):
            lay.addWidget(widget)

    def set_enabled_actions(self, enabled: bool, state: str = "") -> None:
        """*state*: ``sending`` / ``queued`` (Send says so and waits; a queued one can still be skipped) or ''."""
        self.btn_send.setText({"sending": "Sending…", "queued": "Queued"}.get(state, "Send"))
        self.btn_send.setEnabled(enabled and not state)
        self.btn_source.setEnabled(enabled)
        self.btn_skip.setEnabled(enabled and state != "sending")


class FoundView(QWidget):
    changed = Signal()              # findings were sent or skipped: the host reloads them (and the downloads)
    change_source = Signal(int)     # a series id: open it in Missing chapters to pick its source
    mode_changed = Signal()         # a series' automatic-download mode was set here: the List's chips follow
    series_menu_requested = Signal(int, QPoint)     # a card's "⋯": (series id, where) - the host opens the menu

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
        self._series_menu = False                   # the cards' "⋯" (the host handles series_menu_requested)
        self.rows: Dict[int, FoundRow] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)
        self.title_label = label("Found", "h2")
        self.count_label = label("", "muted")
        self.btn_show_all = button("", link=True, tip="Show what was found for every series")
        self.btn_show_all.clicked.connect(lambda: self.set_series_filter(None))
        self.btn_send_all = button("Send all…", tip="Send every finding listed here (asks first)")
        self.btn_send_all.clicked.connect(self.send_all)
        outer.addLayout(hbox(self.title_label, self.count_label, None, self.btn_show_all, self.btn_send_all, spacing=12))
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

        self.scroll = QScrollArea()
        self.scroll.setObjectName("foundScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("foundBody")
        self._list = QVBoxLayout(body)
        self._list.setContentsMargins(0, 0, 4, 0)
        self._list.setSpacing(8)
        self._list.addStretch(1)
        self.scroll.setWidget(body)
        outer.addWidget(self.scroll, 1)
        outer.addStretch(0)                     # takes the room while the list is hidden (the empty state)
        self._show()

    # --- data ----------------------------------------------------------------------------------------

    def set_findings(self, findings: Sequence, names: Mapping[int, str], last_run=None) -> None:
        """The waiting findings, the series names (series id -> name) and the last find pass."""
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

    def shown(self) -> List:
        """The findings listed now, top to bottom."""
        rows = [f for f in self._findings if self._series is None or f.series_id == self._series]
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

    # --- showing -------------------------------------------------------------------------------------

    def _show(self) -> None:
        shown = self.shown()
        for row in self.rows.values():
            self._list.removeWidget(row)
            row.hide()                          # gone now, not when the event loop deletes it
            row.deleteLater()
        self.rows.clear()
        for finding in shown:
            row = FoundRow(finding, self.name_of(finding.series_id))
            row.btn_send.clicked.connect(lambda _=False, fid=finding.id: self.send(fid))
            row.btn_source.clicked.connect(lambda _=False, sid=finding.series_id: self.change_source.emit(sid))
            row.btn_skip.clicked.connect(lambda _=False, fid=finding.id: self.skip(fid))
            row.btn_more.setVisible(self._series_menu)
            row.btn_more.clicked.connect(lambda _=False, sid=finding.series_id, b=row.btn_more:
                                         self.series_menu_requested.emit(sid, below(b)))
            self._list.insertWidget(self._list.count() - 1, row)
            row.show()                          # shown with the list (a reparented widget starts hidden)
            self.rows[finding.id] = row
        filtered = self._series is not None
        self.title_label.setText(f"Found for {self.name_of(self._series)}" if filtered else "Found")
        self.title_label.setWordWrap(filtered)
        self.count_label.setText(total_text(shown))
        self.count_label.setVisible(bool(shown))
        self.btn_show_all.setText(f"Show all ({len(self._findings)})")
        self.btn_show_all.setVisible(filtered)
        n_send = len(sendable(shown))
        self.btn_send_all.setText(f"Send all {n_send}…" if n_send > 1 else "Send all…")
        run = last_run_text(self._run)
        self.run_label.setText(run)
        self.run_label.setVisible(bool(shown))
        self.empty_text.setText(run)
        self.empty.setVisible(not shown)
        self.scroll.setVisible(bool(shown))
        self._show_mode()
        self._enable()

    def set_series_menu_enabled(self, on: bool) -> None:
        """Show each card's "⋯" (the host opens the series' menu on :attr:`series_menu_requested`)."""
        self._series_menu = bool(on)
        for row in self.rows.values():
            row.btn_more.setVisible(self._series_menu)

    # --- the series' mode ----------------------------------------------------------------------------

    def refresh_mode(self) -> None:
        """Read the shown series' mode again (it was changed on another screen)."""
        self._show_mode()

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
        sid = self._series
        if sid is None:
            return
        mode = self.mode_combo.itemData(index)
        try:
            self._backend.set_series_mode([sid], mode)
        except Exception as exc:  # noqa: BLE001 - shown; the combo shows the stored mode again
            self.say(f"Could not change the automatic downloads: {describe_error(exc)}", "bad")
            self._show_mode()
            return
        _log.info("Found: automatic downloads %s for series %s", mode or "the default", sid)
        name = self.name_of(sid)
        whose = "the default"
        if not mode:
            try:
                whose = "the library's default" if default_of(self._backend, sid)[1] else "the default"
            except Exception:  # noqa: BLE001 - the wording only
                pass
        self.say(f"Automatic downloads for {name}: {MODE_LABELS[mode]}." if mode else
                 f"Automatic downloads for {name}: {whose} again.", "ok")
        self._show_mode()
        self.mode_changed.emit()

    def _enable(self) -> None:
        free = not self.busy()
        several = len(sendable(self.shown())) > 1
        self.btn_send_all.setVisible(several)
        self.btn_send_all.setEnabled(free and several)
        passing = bool(self._busy_text)             # a find pass runs: every action waits for it
        for fid, row in self.rows.items():
            state = "sending" if fid == self._sending else "queued" if fid in self._queue else ""
            row.set_enabled_actions(not passing, state)

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
        self.say(f"Skipped {gets_text(finding)} of {name}: it is not offered again.")
        self.changed.emit()
        return True

    def skip_series(self, series_id: int) -> int:
        """Skip every finding of *series_id* waiting here (the card menu's "Skip all"), but the one being sent; how many
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

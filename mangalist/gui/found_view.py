"""The Download tab's **Found** view (automatic downloads, owner decisions 2026-10-10): what find passes found that waits
for the owner - one row per finding with its series, what it would get, its size and why it waits, **Send** / **Skip**
per row and **Send all** (asks first, with the count and the total size). Empty, it says "Nothing found waiting for you"
and when the last pass ran.

The rows are the backend's findings (:class:`~mangalist.store.findings.Finding`, ``findings()``); sending runs off the
UI thread (``send_finding`` may add a torrent to qBittorrent or queue it under the download budget, or enqueue chapters
in Suwayomi) and says what happened, as the backend words it; skipping is quick and the pick is never offered again.
A pick a pass sent by itself is not here: it is an ordinary download in the In progress list.

The Download tab feeds it (:meth:`FoundView.set_findings`) and reloads it when :attr:`FoundView.changed` fires.
"""

from __future__ import annotations

import logging
from typing import Callable, Dict, List, Mapping, Optional, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QMessageBox, QProgressBar, QScrollArea, QVBoxLayout, QWidget

from .automation_rules import (
    EMPTY_TITLE,
    gets_text,
    kind_text,
    last_run_text,
    send_all_question,
    send_all_summary,
    size_text,
    total_text,
)
from .background import BackgroundCall, describe_error, start_call
from .download_style import set_tone
from .download_widgets import button, card, hbox, label, pill

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
        top = hbox(self.name_label, self.kind_pill, None, self.size_label, self.btn_send, self.btn_skip, spacing=10)
        top.setStretchFactor(self.name_label, 1)
        lay.addLayout(top)
        self.gets_label = label(f"Gets {gets_text(finding)}", "lead", wrap=True)
        self.release_label = label(finding.title, "mono", wrap=True, selectable=True)
        self.reason_label = label(f"Waits because {finding.reason}" if finding.reason else "Waits for you", "muted",
                                  wrap=True)
        for widget in (self.gets_label, self.release_label, self.reason_label):
            lay.addWidget(widget)

    def set_enabled_actions(self, enabled: bool) -> None:
        self.btn_send.setEnabled(enabled)
        self.btn_skip.setEnabled(enabled)


class FoundView(QWidget):
    changed = Signal()              # findings were sent or skipped: the host reloads them (and the downloads)

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
        self._busy_text = ""
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
        return bool(self._busy_text) or self._call is not None

    def say(self, text: str, tone: str = "") -> None:
        set_tone(self.status_label, tone if text else "")
        self.status_label.setText(text)
        self.status_label.setVisible(bool(text))

    # --- showing -------------------------------------------------------------------------------------

    def _show(self) -> None:
        shown = self.shown()
        for row in self.rows.values():
            self._list.removeWidget(row)
            row.deleteLater()
        self.rows.clear()
        for finding in shown:
            row = FoundRow(finding, self.name_of(finding.series_id))
            row.btn_send.clicked.connect(lambda _=False, fid=finding.id: self.send(fid))
            row.btn_skip.clicked.connect(lambda _=False, fid=finding.id: self.skip(fid))
            self._list.insertWidget(self._list.count() - 1, row)
            self.rows[finding.id] = row
        filtered = self._series is not None
        self.title_label.setText(f"Found for {self.name_of(self._series)}" if filtered else "Found")
        self.title_label.setWordWrap(filtered)
        self.count_label.setText(total_text(shown))
        self.btn_show_all.setText(f"Show all ({len(self._findings)})")
        self.btn_show_all.setVisible(filtered)
        self.btn_send_all.setText(f"Send all {len(shown)}…" if len(shown) > 1 else "Send all…")
        run = last_run_text(self._run)
        self.run_label.setText(run)
        self.run_label.setVisible(bool(shown))
        self.empty_text.setText(run)
        self.empty.setVisible(not shown)
        self.scroll.setVisible(bool(shown))
        self._enable()

    def _enable(self) -> None:
        free = not self.busy()
        self.btn_send_all.setEnabled(free and len(self.shown()) > 1)
        for row in self.rows.values():
            row.set_enabled_actions(free)

    # --- actions -------------------------------------------------------------------------------------

    def _finding(self, finding_id: int):
        return next((f for f in self._findings if f.id == finding_id), None)

    def send(self, finding_id: int) -> bool:
        """Send one finding (off the UI thread); the line under the header says what happened."""
        finding = self._finding(finding_id)
        if finding is None or self.busy():
            return False
        name = self.name_of(finding.series_id)
        _log.info("Found: sending finding %s (%s, %s)", finding.id, finding.kind, gets_text(finding))
        self.say(f"Sending {gets_text(finding)} of {name}...")
        backend = self._backend
        self._call = start_call(lambda: backend.send_finding(finding_id),
                                lambda outcome: self.say(f"{name}, {gets_text(finding)}: {outcome}", "ok"),
                                lambda message: self.say(f"Could not send {gets_text(finding)} of {name}: {message}",
                                                         "bad"),
                                self._call_finished)
        self._enable()
        return True

    def skip(self, finding_id: int) -> bool:
        """Skip one finding: it leaves the list and is never offered again (quick)."""
        finding = self._finding(finding_id)
        if finding is None or self.busy():
            return False
        name = self.name_of(finding.series_id)
        try:
            self._backend.skip_finding(finding_id)
        except Exception as exc:  # noqa: BLE001 - shown to the owner
            self.say(f"Could not skip it: {describe_error(exc)}", "bad")
            return False
        _log.info("Found: skipped finding %s (%s, %s)", finding.id, finding.kind, gets_text(finding))
        self.say(f"Skipped {gets_text(finding)} of {name}: it is not offered again.")
        self.changed.emit()
        return True

    def _ask(self, title: str, text: str) -> bool:
        box = QMessageBox(QMessageBox.Icon.Question, title, text, parent=self)
        yes = box.addButton("Send all", QMessageBox.ButtonRole.AcceptRole)
        no = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(no)
        box.exec()
        return box.clickedButton() is yes

    def send_all(self) -> bool:
        """Send every finding listed (after the owner's yes), one after another off the UI thread."""
        shown = self.shown()
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
        if self._call is not None:
            self._call.abandon()
            self._call = None

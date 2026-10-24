"""Settings > Connected services > Built-in extensions > "Carry over from Suwayomi" (2026-10-23): the owner's per-series
Suwayomi sources carried over to MangaList's own extension host, once (:mod:`mangalist.services.extensions.carry_over`).

The panel first asks both programs what it would do (a dry run, off the UI thread: nothing is written) and shows it -
the extensions it will install in the host, the repositories it will add, how many series it will carry and which ones
it cannot (and why). **Carry over** does it, with a progress bar, and shows the report. Suwayomi is only read; its own
choices stay as they are, so switching back to Suwayomi keeps working. Everything is a button (the owner uses an iPad);
a second run carries only what is still missing.
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QPlainTextEdit, QProgressBar, QVBoxLayout, QWidget

from .background import BackgroundCall, start_call
from .download_widgets import button, hbox, label
from .downloads_backend import BackendError

INTRO = ("Carries the source each series uses in Suwayomi over to the built-in extensions: the extensions they need are "
         "installed in the extension host (from its repositories, signature-checked), Suwayomi's repositories are "
         "added, and each series gets the same manga on the host - so nothing has to be picked again. Suwayomi is only "
         "read and its own choices stay, so switching back keeps working. A series already set up on the host is "
         "never changed.")
CHECKING = "Checking what to carry over (asking Suwayomi and the extension host)..."

#: (dry_run, progress(done, total, text)) -> CarryReport. Blocking; called off the UI thread.
Runner = Callable[[bool, Callable[[int, int, str], None]], object]


class _Relay(QObject):
    """Progress from the worker thread, delivered on the UI thread (a queued signal)."""

    progressed = Signal(int, int, str)


def default_runner(db) -> Runner:
    from ..services.extensions.carry_over import CarryOverError, run_from_store

    def run(dry_run: bool, progress) -> object:
        try:
            return run_from_store(db, dry_run=dry_run, progress=progress)
        except CarryOverError as exc:
            raise BackendError(str(exc)) from None

    return run


def plan_text(report) -> str:
    """What a dry run's report says the carry-over will do (the confirmation)."""
    from ..services.extensions import carry_over as co

    counts = report.counts()
    todo = []
    installs = report.to_install
    if installs:
        todo.append(f"install {len(installs)} extension(s) in the extension host: "
                    + ", ".join(f"{e.name or e.pkg} ({e.series} series)" for e in installs))
    if report.repos_added:
        todo.append("add Suwayomi's repositories the host lacks: " + ", ".join(report.repos_added))
    if counts[co.CARRIED]:
        todo.append(f"carry over {counts[co.CARRIED]} series")
    lines = ["This will:"] + [f"  - {t}" for t in todo] if todo else ["Nothing to carry over."]
    if counts[co.ALREADY]:
        lines.append(f"{counts[co.ALREADY]} series are already set up on the host (left as they are).")
    skipped = [s for s in co.STATUSES[2:] if counts[s]]
    if skipped:
        lines.append("Cannot be carried: " + ", ".join(f"{counts[s]} {s}" for s in skipped) + " (details below).")
    for e in report.extension_problems:
        lines.append(f"Extension {e.name or e.pkg}: {e.state}" + (f" - {e.message}" if e.message else ""))
    return "\n".join(lines)


def needs_doing(report) -> bool:
    from ..services.extensions import carry_over as co

    return bool(report.counts()[co.CARRIED] or report.to_install or report.repos_added)


class CarryOverPanel(QWidget):
    """Check -> confirm -> carry over -> report. ``carried`` is emitted after a real run (the caller refreshes)."""

    carried = Signal(object)

    def __init__(self, db, parent: Optional[QWidget] = None, runner: Optional[Runner] = None):
        super().__init__(parent)
        self.setObjectName("carryOverPanel")
        self._runner = runner or default_runner(db)
        self._call: Optional[BackgroundCall] = None
        self._relay = _Relay()
        self._relay.progressed.connect(self._progress)
        self.plan = None                      # the dry run's report
        self.report = None                    # the real run's report
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        lay.addWidget(label(INTRO, "muted", wrap=True))
        self.status = label("", wrap=True, selectable=True)
        self.status.setObjectName("carryStatus")
        lay.addWidget(self.status)
        self.bar = QProgressBar()
        self.bar.setVisible(False)
        lay.addWidget(self.bar)
        self.btn_carry = button("Carry over", primary=True,
                                tip="Install the extensions, add the repositories and carry the series over now")
        self.btn_carry.clicked.connect(self.carry)
        self.btn_check = button("Check again", tip="Ask Suwayomi and the extension host again (nothing is written)")
        self.btn_check.clicked.connect(self.check)
        lay.addLayout(hbox(self.btn_check, None, self.btn_carry))
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setObjectName("carryDetails")
        lay.addWidget(self.details, 1)
        self.check()

    # --- state ------------------------------------------------------------------------------------------------

    def busy(self) -> bool:
        return self._call is not None

    def _start(self, dry_run: bool) -> None:
        relay = self._relay

        def progress(done: int, total: int, text: str) -> None:
            relay.progressed.emit(int(done), int(total), str(text))

        self.btn_carry.setEnabled(False)
        self.btn_check.setEnabled(False)
        self.bar.setVisible(not dry_run)
        self.bar.setRange(0, 0)
        runner = self._runner
        self._call = start_call(lambda: runner(dry_run, progress),
                                self._planned if dry_run else self._done, self._failed)

    def _progress(self, done: int, total: int, text: str) -> None:
        if self._call is None:
            return
        if total > 0:
            self.bar.setRange(0, total)
            self.bar.setValue(min(done, total))
        if self.plan is None or self.bar.isVisible():
            self.status.setText(text)

    def check(self) -> bool:
        """The dry run: what would be done (shown before anything is written)."""
        if self.busy():
            return False
        self.status.setText(CHECKING)
        self.details.setPlainText("")
        self._start(True)
        return True

    def _planned(self, report) -> None:
        self._call = None
        self.plan = report
        self.btn_check.setEnabled(True)
        self.bar.setVisible(False)
        self.status.setText(plan_text(report))
        self.details.setPlainText("\n".join(report.lines()))
        doing = needs_doing(report)
        self.btn_carry.setEnabled(doing)
        from ..services.extensions import carry_over as co

        n = report.counts()[co.CARRIED]
        self.btn_carry.setText(f"Carry over {n} series" if n else "Carry over")

    def carry(self) -> bool:
        """The confirmed run (only after a check that found something to do)."""
        if self.busy() or self.plan is None or not needs_doing(self.plan):
            return False
        self.status.setText("Carrying over...")
        self._start(False)
        return True

    def _done(self, report) -> None:
        self._call = None
        self.report = report
        self.bar.setVisible(False)
        self.btn_check.setEnabled(True)
        self.btn_carry.setEnabled(False)
        self.btn_carry.setText("Carried over")
        self.status.setText("Done. " + report.summary() + "." + (
            " The series that were not carried are listed below; they can be picked by hand in their chapters panel."
            if len(report.series) != report.counts()["carried"] + report.counts()["already there"] else ""))
        self.details.setPlainText("\n".join(report.lines()))
        self.plan = None
        self.carried.emit(report)

    def _failed(self, message: str) -> None:
        self._call = None
        self.bar.setVisible(False)
        self.btn_check.setEnabled(True)
        self.btn_carry.setEnabled(False)
        self.status.setText(f"Not carried over: {message}")

    def stop(self) -> None:
        if self._call is not None:
            self._call.abandon()
            self._call = None

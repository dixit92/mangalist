"""Settings > Connected services > Built-in extensions > "Carry over from Suwayomi" (2026-10-23): the owner's per-series
Suwayomi sources carried over to MangaList's own extension host, once (:mod:`mangalist.services.extensions.carry_over`).

The panel first asks both programs what it would do (a dry run, off the UI thread: nothing is written) and shows it -
the extensions it will install in the host, the repositories it will add, how many series it will carry and which ones
it cannot (and why). **Carry over** does it, with a progress bar, and shows the report. Suwayomi is only read; its own
choices stay as they are, so switching back to Suwayomi keeps working. Everything is a button (the owner uses an iPad);
a second run carries only what is still missing.

**Carry back to Suwayomi…** (transition audit B8, 2026-10-24) turns the panel around: what was decided on the host is
written to Suwayomi's choices (:func:`mangalist.services.extensions.carry_over.carry_back`), with the same check,
confirmation and report.
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
BACK_INTRO = ("Carries back to Suwayomi what was decided on the built-in extensions - title matches you confirmed, "
              "Change source, series added, scanlation groups picked - so switching back to Suwayomi keeps them: each "
              "series gets the same manga in Suwayomi, found by its address on the same source. The extension host is "
              "only read; a series whose Suwayomi source already names that manga is left as it is.")
BACK_CHECKING = "Checking what to carry back (asking Suwayomi and the extension host)..."

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

    if isinstance(report, co.CarryBackReport):
        return report.to_do() > 0
    return bool(report.counts()[co.CARRIED] or report.to_install or report.repos_added)


def default_back_runner(db) -> Runner:
    from ..services.extensions.carry_over import CarryOverError, run_back_from_store

    def run(dry_run: bool, progress) -> object:
        try:
            return run_back_from_store(db, dry_run=dry_run, progress=progress)
        except CarryOverError as exc:
            raise BackendError(str(exc)) from None

    return run


def back_plan_text(report) -> str:
    """What a carry-back dry run says it will do (the confirmation)."""
    from ..services.extensions import carry_over as co

    counts = report.counts()
    todo = []
    if counts[co.BACK_CARRIED]:
        todo.append(f"give {counts[co.BACK_CARRIED]} series the manga chosen on the host, in Suwayomi")
    if counts[co.BACK_GROUP]:
        todo.append(f"carry the scanlation group of {counts[co.BACK_GROUP]} more series back")
    lines = ["This will:"] + [f"  - {t}" for t in todo] if todo else ["Nothing to carry back."]
    if counts[co.BACK_SAME]:
        lines.append(f"{counts[co.BACK_SAME]} series already have the same source in Suwayomi (left as they are).")
    skipped = [s for s in co.BACK_STATUSES[3:] if counts[s]]
    if skipped:
        lines.append("Cannot be carried back: " + ", ".join(f"{counts[s]} {s}" for s in skipped) + " (details below).")
    return "\n".join(lines)


class CarryOverPanel(QWidget):
    """Check -> confirm -> carry over -> report. ``carried`` is emitted after a real run (the caller refreshes)."""

    carried = Signal(object)

    def __init__(self, db, parent: Optional[QWidget] = None, runner: Optional[Runner] = None,
                 back_runner: Optional[Runner] = None):
        super().__init__(parent)
        self.setObjectName("carryOverPanel")
        self._runner = runner or default_runner(db)
        self._back_runner = back_runner or default_back_runner(db)
        self.mode = "over"                    # or "back": the carry-back to Suwayomi (transition audit B8)
        self._call: Optional[BackgroundCall] = None
        self._relay = _Relay()
        self._relay.progressed.connect(self._progress)
        self.plan = None                      # the dry run's report
        self.report = None                    # the real run's report
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self.intro = label(INTRO, "muted", wrap=True)
        lay.addWidget(self.intro)
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
        self.btn_mode = button("Carry back to Suwayomi…", tip="Switching back to Suwayomi? Carry what was decided on "
                                                             "the built-in extensions back to Suwayomi's choices")
        self.btn_mode.clicked.connect(self.toggle_mode)
        lay.addLayout(hbox(None, self.btn_mode))
        self.check()

    def toggle_mode(self) -> bool:
        """Carry over (Suwayomi -> host) <-> carry back (host -> Suwayomi); checks the new direction at once."""
        if self.busy():
            return False
        self.mode = "back" if self.mode == "over" else "over"
        back = self.mode == "back"
        self.intro.setText(BACK_INTRO if back else INTRO)
        self.btn_mode.setText("Carry over from Suwayomi…" if back else "Carry back to Suwayomi…")
        self.btn_carry.setText("Carry back" if back else "Carry over")
        self.plan = self.report = None
        return self.check()

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
        self.btn_mode.setEnabled(False)
        runner = self._back_runner if self.mode == "back" else self._runner
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
        self.status.setText(BACK_CHECKING if self.mode == "back" else CHECKING)
        self.details.setPlainText("")
        self._start(True)
        return True

    def _planned(self, report) -> None:
        self._call = None
        self.plan = report
        self.btn_check.setEnabled(True)
        self.btn_mode.setEnabled(True)
        self.bar.setVisible(False)
        back = self.mode == "back"
        self.status.setText(back_plan_text(report) if back else plan_text(report))
        self.details.setPlainText("\n".join(report.lines()))
        doing = needs_doing(report)
        self.btn_carry.setEnabled(doing)
        from ..services.extensions import carry_over as co

        if back:
            n = report.to_do()
            self.btn_carry.setText(f"Carry back {n} series" if n else "Carry back")
            return
        n = report.counts()[co.CARRIED]
        self.btn_carry.setText(f"Carry over {n} series" if n else "Carry over")

    def carry(self) -> bool:
        """The confirmed run (only after a check that found something to do)."""
        if self.busy() or self.plan is None or not needs_doing(self.plan):
            return False
        self.status.setText("Carrying back..." if self.mode == "back" else "Carrying over...")
        self._start(False)
        return True

    def _done(self, report) -> None:
        self._call = None
        self.report = report
        self.bar.setVisible(False)
        self.btn_check.setEnabled(True)
        self.btn_mode.setEnabled(True)
        self.btn_carry.setEnabled(False)
        if self.mode == "back":
            from ..services.extensions import carry_over as co

            counts = report.counts()
            self.btn_carry.setText("Carried back")
            left = len(report.series) - counts[co.BACK_CARRIED] - counts[co.BACK_GROUP] - counts[co.BACK_SAME]
            self.status.setText("Done. " + report.summary() + "." + (
                " The series that were not carried back are listed below; after switching, pick them in their "
                "chapters panel." if left else ""))
            self.details.setPlainText("\n".join(report.lines()))
            self.plan = None
            self.carried.emit(report)
            return
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
        self.btn_mode.setEnabled(True)
        self.btn_carry.setEnabled(False)
        self.status.setText(f"Not carried {'back' if self.mode == 'back' else 'over'}: {message}")

    def stop(self) -> None:
        if self._call is not None:
            self._call.abandon()
            self._call = None

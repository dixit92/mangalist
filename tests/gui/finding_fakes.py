"""A FAKE backend with the automatic-downloads methods (:mod:`mangalist.downloads.finding`) for the GUI tests: findings,
modes and find passes kept in memory, every call recorded with its thread. Built on the Suwayomi fake so the Download tab
has every extra. No network, no library, made-up names."""

from __future__ import annotations

import threading
from dataclasses import replace
from typing import Dict, List, Optional

from mangalist.downloads.finding import TRIGGER_BULK, TRIGGER_NOW, FindReport
from mangalist.gui.downloads_backend import BackendError
from mangalist.store.findings import FindRun, Finding

from .chapter_fakes import FakeChapterBackend

WHEN = "2026-10-11T04:30:00+00:00"


def finding(id=1, series_id=1, kind="volumes", title="Example Series v03-05 (Digital) (Group)", fills=("3", "4", "5"),
            size_bytes=734_003_200, reason="the series is on Notify", status="waiting") -> Finding:
    return Finding(id=id, series_id=series_id, kind=kind, pick_key=f"nyaa:{id:040x}", title=title, fills=tuple(fills),
                   size_bytes=size_bytes, reason=reason, payload={}, status=status, run_id=1, record_id=None,
                   found_at=WHEN, updated_at=WHEN)


def find_run(id=1, trigger="schedule", summary="12 series searched, 1 sent, 2 waiting for you", finished=True) -> FindRun:
    return FindRun(id=id, trigger=trigger, started_at=WHEN, finished_at=WHEN if finished else None, series_searched=12,
                   found=2, sent=1, errors=0, summary=summary)


class FakeFindingBackend(FakeChapterBackend):
    def __init__(self, *, found=(), modes: Optional[Dict[int, Optional[str]]] = None, default="notify", last_run=None,
                 **kw):
        super().__init__(**kw)
        self.found: List[Finding] = list(found)
        self.own_modes: Dict[int, Optional[str]] = dict(modes or {})
        self.default = default
        self.last_run = last_run
        self.find_calls: List[Optional[tuple]] = []
        self.sent_findings: List[int] = []
        self.skipped_findings: List[int] = []
        self.mode_calls: List[tuple] = []
        self.find_gate: Optional[threading.Event] = None    # set to hold a find pass until released
        self.find_error: Optional[str] = None
        self.send_finding_error: Dict[int, str] = {}
        self.new_on_find: List[Finding] = []                 # what the next pass "finds"

    # --- the find pass
    def find_now(self, series_ids=None):
        self.threads.append(threading.get_ident())
        self.find_calls.append(tuple(series_ids) if series_ids is not None else None)
        if self.find_gate is not None:
            self.find_gate.wait(10)
        if self.find_error:
            raise BackendError(self.find_error)
        self.found = list(self.new_on_find) + self.found
        trigger = TRIGGER_BULK if series_ids is not None else TRIGGER_NOW
        report = FindReport(run_id=len(self.find_calls), trigger=trigger,
                            series_searched=len(series_ids) if series_ids is not None else 12,
                            found=len(self.new_on_find), sent=0)
        self.last_run = replace(find_run(id=len(self.find_calls), trigger=trigger), summary=report.summary)
        self.new_on_find = []
        return report

    def last_find_run(self):
        return self.last_run

    # --- findings
    def findings(self, status="waiting"):
        return [f for f in self.found if status is None or f.status == status]

    def send_finding(self, finding_id):
        self.threads.append(threading.get_ident())
        if finding_id in self.send_finding_error:
            raise BackendError(self.send_finding_error[finding_id])
        self.sent_findings.append(finding_id)
        self._move(finding_id, "sent")
        return "sent to qBittorrent"

    def skip_finding(self, finding_id):
        self.skipped_findings.append(finding_id)
        self._move(finding_id, "skipped")

    def _move(self, finding_id, status):
        self.found = [replace(f, status=status) if f.id == finding_id else f for f in self.found]

    # --- modes
    def series_modes(self, series_ids):
        return {int(i): self.own_modes.get(int(i)) or self.default for i in series_ids}

    def own_series_mode(self, series_id):
        return self.own_modes.get(series_id)

    def set_series_mode(self, series_ids, mode):
        self.mode_calls.append((tuple(series_ids), mode))
        for sid in series_ids:
            self.own_modes[int(sid)] = mode

    def default_mode(self):
        return self.default

    def set_default_mode(self, mode):
        self.mode_calls.append(("default", mode))
        self.default = mode

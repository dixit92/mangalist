"""Automatic downloads: the contract between the lanes of the automatic-downloads cycle (owner decisions 2026-10-10,
vault note "Manga-list - Automatic Downloads"). No Qt here.

**The wanted list without Qt** (lane A, :mod:`mangalist.wanted`)::

    wanted_list(db, *, root_ids=None, today=None) -> List[WantedSeries]

The Download tab's "To get" entries (:class:`mangalist.gui.shell.WantedSeries`, Qt-free) for every present series of
the given roots (all roots when None) computed from the library database and the MangaPixer cache alone - the same
states and gaps the List shows (``states.compute_state`` with MangaPixer's knowledge or the own matcher's, the series'
inventory, its "volumes or chapters?" answer, its Behind override, the numbering-restart rule) and the same
``findable`` / ``reason`` (``gui.volumes_target.find_volumes_availability`` for volumes). ``series_id`` is always set.

**The find pass** (lane B, :mod:`mangalist.downloads.finder`)::

    run_find(db, *, trigger, series_ids=None, wanted=None, ...) -> FindReport

For each wanted series whose mode is not Off (``FindingStore.modes``) and with no download in flight: volumes (nyaa +
the D4 ranking + a greedy cover; pack narrowing like a manual send) and chapters (the series' saved Suwayomi source
only). Picks of a series on Auto that may go out by themselves are sent through the existing send paths (the budget
and the queue apply to torrents); everything else is offered as a finding (``FindingStore.offer``) with the reason.
*series_ids*: a bulk find (trigger ``bulk``) - only those series, and nothing is sent automatically. *wanted*:
injected for tests (default: ``mangalist.wanted.wanted_list``, imported lazily so lane B tests with fakes before lane A
is merged).

**The backend** (lane B adds to :class:`mangalist.downloads.adapter.Backend`; lane C calls it off the UI thread,
except the quick ones; see :mod:`mangalist.gui.downloads_backend` for the table)::

    find_now(series_ids=None) -> FindReport            # Find now (None) or a bulk find (ids)
    findings(status="waiting") -> List[Finding]         # quick
    send_finding(finding_id) -> str                    # what happened (sent / queued / refused, readable)
    skip_finding(finding_id) -> None                   # quick
    series_modes(series_ids) -> Dict[int, str]         # quick: the mode in force
    own_series_mode(series_id) -> Optional[str]         # quick: None = follows the default
    own_series_modes(series_ids) -> {id: mode or None}  # quick, optional: the own modes in one read (Found's chips)
    set_series_mode(series_ids, mode_or_None) -> None   # quick
    default_mode() -> str / set_default_mode(mode)      # quick
    default_for(series_id) -> (mode, from_library)      # quick, optional (schema 8: a library's default mode)
    series_defaults(series_ids) -> {id: (mode, lib)}   # quick, optional
    last_find_run() -> Optional[FindRun]               # quick

**The schedule**: the headless runner's existing ``dispatch-batch`` job (``schedule_dispatch``, default
``daily@04:30``) runs the find pass (trigger ``schedule``); Settings > Automation shows it as "Find and send missing
volumes and chapters". Only with downloads on, as today.

**The log viewer** (lane D) reads the log files in ``mangalist.paths.log_dir()`` (``mangalist.log``, ``headless.log``
and their rotated copies) read-only; it needs nothing from the other lanes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from ..store.findings import FindRun, Finding  # noqa: F401 - re-exported for the GUI and the finder

TRIGGER_SCHEDULE, TRIGGER_NOW, TRIGGER_BULK = "schedule", "now", "bulk"

#: Why a pick waits instead of going out by itself (``Finding.reason``), shown to the owner as is.
REASON_NOTIFY = "the series is on Notify"
REASON_BULK = "found by a bulk find"
REASON_PACK_WHOLE = "a pack whose files cannot be narrowed to the missing volumes"
REASON_UNCERTAIN = "the release may not hold a missing volume (its numbers cannot be compared)"
REASON_NO_SOURCE = "no Suwayomi source is saved for the series yet"
#: The saved source cannot give the missing chapters - one reason per case (owner, 2026-10-12: "make the 'not on ...'
#: message say which case it is"). Each ends in "Change source?": the way on is another source.
REASON_SOURCE_GONE = "{source} cannot open the series any more - Change source?"
REASON_SOURCE_TAKEN_DOWN = "taken down on {source}: its chapters there are marked unavailable - Change source?"
REASON_SOURCE_NEVER = "{source} has never had chapters of the series - Change source?"
REASON_SOURCE_EMPTY = "{source} lists no chapters of the series - Change source?"
REASON_SOURCE_LACKS = "{source} lacks {missing} (it has {has}) - Change source?"
CHANGE_SOURCE = " - Change source?"


@dataclass
class FindReport:
    """What one find pass did (also written to ``find_runs``)."""

    run_id: Optional[int]
    trigger: str
    series_searched: int = 0
    found: int = 0                      # findings now waiting for the owner
    sent: int = 0                       # picks sent automatically
    errors: int = 0
    lines: List[str] = field(default_factory=list)      # one per series with something to say, for the log

    @property
    def summary(self) -> str:
        parts = [f"{self.series_searched} series searched", f"{self.sent} sent", f"{self.found} waiting for you"]
        if self.errors:
            parts.append(f"{self.errors} failed")
        return ", ".join(parts)

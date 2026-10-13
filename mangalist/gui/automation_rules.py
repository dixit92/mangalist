"""Qt-free wording and rules of the automatic-downloads screens (owner decisions 2026-10-10, vault note "Manga-list -
Automatic Downloads"): the Off / Notify / Auto modes as the owner reads them, what a finding would get, its size, the
"Send all" question, and when the last find pass ran.

Kept apart from the widgets so the wording is tested without a display. The backend methods behind it are listed in
:mod:`mangalist.downloads.finding`; a backend without them (an older adapter, a test fake) simply shows none of these
screens - :func:`has_automation` decides.
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

MODE_OFF, MODE_NOTIFY, MODE_AUTO = "off", "notify", "auto"
MODES: Tuple[str, ...] = (MODE_OFF, MODE_NOTIFY, MODE_AUTO)
MODE_LABELS: Mapping[str, str] = {MODE_OFF: "Off", MODE_NOTIFY: "Notify", MODE_AUTO: "Auto"}
#: One sentence per mode (Settings > Automation, the row menu's tooltips).
MODE_HELP: Mapping[str, str] = {
    MODE_OFF: "Never searched: MangaList looks for nothing and sends nothing for the series.",
    MODE_NOTIFY: "Searched on the schedule; what is found waits under Found in the Download tab for you to send or "
                 "skip.",
    MODE_AUTO: "Searched on the schedule; what is found is sent by itself (torrents within the download budget). A pick "
               "that needs a look - a pack that cannot be narrowed, numbers that cannot be compared - still waits "
               "under Found.",
}
#: The "follows the default" entry of the row menu (its label carries the default: "Use the default (Notify)").
DEFAULT_KEY = "default"

#: The backend methods the screens need (:mod:`mangalist.downloads.finding`).
AUTOMATION_METHODS = ("find_now", "findings", "send_finding", "skip_finding", "series_modes", "own_series_mode",
                      "set_series_mode", "default_mode", "set_default_mode", "last_find_run")

KIND_TEXT: Mapping[str, str] = {"volumes": "Missing volumes", "upgrades": "Upgrade", "chapters": "Missing chapters"}
TRIGGER_TEXT: Mapping[str, str] = {"schedule": "scheduled", "now": "Find now", "bulk": "bulk find"}

EMPTY_TITLE = "Nothing found waiting for you"
NEVER_RAN = "No find pass has run yet."


def has_automation(backend) -> bool:
    """The backend offers automatic downloads (every method of the contract)."""
    return backend is not None and all(callable(getattr(backend, name, None)) for name in AUTOMATION_METHODS)


def mode_label(mode: Optional[str]) -> str:
    return MODE_LABELS.get(mode or "", str(mode or ""))


def default_entry_label(default: str) -> str:
    """The row menu's "follow the default" entry: ``Use the default (Notify)``."""
    return f"Use the default ({mode_label(default)})"


def mode_text(own: Optional[str], default: str) -> str:
    """What the details panel says: ``Auto``, or ``Notify (the default)`` for a series that follows the default."""
    if own in MODES:
        return mode_label(own)
    return f"{mode_label(default)} (the default)"


def common_choice(owns: Iterable[Optional[str]]) -> Optional[str]:
    """The row menu entry to tick for a selection: the one mode every series has as its own (``DEFAULT_KEY`` when all
    follow the default), or None when they differ (nothing ticked)."""
    keys = {own if own in MODES else DEFAULT_KEY for own in owns}
    return keys.pop() if len(keys) == 1 else None


def set_mode_text(mode: Optional[str], n: int, default: str) -> str:
    """The status line after the row menu changed the mode of *n* series."""
    series = f"{n} series"
    if mode is None:
        return f"Automatic downloads: {series} follow the default again ({mode_label(default)})."
    return f"Automatic downloads: {mode_label(mode)} for {series}."


def bulk_find_label(n: int) -> str:
    """The bulk find's button / menu entry: ``Find releases for 3 series``."""
    return f"Find releases for {n} series"


# --- findings -----------------------------------------------------------------------------------------------

def size_text(size_bytes: Optional[int]) -> str:
    """``734 MB`` / ``1.7 GB``; ``size not known`` when the pick has none (a chapter set, a magnet)."""
    if not size_bytes:
        return "size not known"
    from ..downloads.partial import size_text as _size

    return _size(int(size_bytes))


def gets_text(finding) -> str:
    """What a finding would get: ``v03-v05`` (volumes or an upgrade) or ``ch 101-104`` (chapters); exact numbers."""
    from .volumes_target import numbers_text

    if finding.kind == "chapters":
        numbers = numbers_text(finding.fills)
        return f"ch {numbers}" if numbers else "chapters"
    return numbers_text(finding.fills, pad=True) or "volumes"


#: Chapter findings with nothing to send (``Finding.payload["type"]``, mangalist.downloads.finder): the series' saved
#: source is gone or lacks the chapters, or it has none yet - the way on is picking a source in its chapters.
NO_PICK_PAYLOADS = ("dropped", "no-source")


def needs_source(finding) -> bool:
    """The finding has nothing to send until the series has a (another) chapter source."""
    payload = finding.payload if isinstance(finding.payload, Mapping) else {}
    return payload.get("type") in NO_PICK_PAYLOADS


def sendable(findings: Sequence) -> List:
    """The findings Send all sends: all but those waiting for a source."""
    return [f for f in findings if not needs_source(f)]


def kind_text(finding) -> str:
    return KIND_TEXT.get(finding.kind, finding.kind)


def total_size(findings: Sequence) -> Tuple[int, int]:
    """(the bytes of the findings that say their size, how many do not)."""
    known = sum(int(f.size_bytes) for f in findings if f.size_bytes)
    return known, sum(1 for f in findings if not f.size_bytes)


def total_text(findings: Sequence) -> str:
    """``3 waiting · 2.1 GB`` (``+ 1 of size not known``)."""
    n = len(findings)
    if not n:
        return "0 waiting"
    known, unknown = total_size(findings)
    parts = [f"{n} waiting"]
    if known:
        parts.append(size_text(known) + (f" + {unknown} of size not known" if unknown else ""))
    return " · ".join(parts)


def series_line(findings: Sequence) -> str:
    """The second line of a series in the Found list: what its findings would get (``v03-v05 · ch 101-104``)."""
    return " · ".join(dict.fromkeys(gets_text(f) for f in findings))


def by_series(findings: Iterable) -> Dict[int, List]:
    """series id -> its findings, in the order given (newest first from the backend)."""
    out: Dict[int, List] = {}
    for finding in findings:
        out.setdefault(finding.series_id, []).append(finding)
    return out


def send_all_question(findings: Sequence) -> Tuple[str, str]:
    """(title, text) of the question before Send all: how many, of which kind, the total size."""
    n = len(findings)
    torrents = [f for f in findings if f.kind != "chapters"]
    chapters = n - len(torrents)
    known, unknown = total_size(torrents)
    parts = []
    if torrents:
        size = size_text(known) if known else ""
        if unknown:
            size = (size + " plus " if size else "") + f"{unknown} of a size not known yet"
        parts.append(f"{len(torrents)} torrent{'s' if len(torrents) != 1 else ''} ({size})")
    if chapters:
        parts.append(f"{chapters} set{'s' if chapters != 1 else ''} of chapters (Suwayomi)")
    text = (f"Send all {n} found release{'s' if n != 1 else ''}? " + " and ".join(parts) + ".")
    if torrents:
        text += ("\n\nTorrents count against the download budget; any that would go past it wait in the queue, as "
                 "when you send one yourself.")
    return f"Send all {n}?", text


def send_all_summary(outcomes: Sequence[Tuple[str, Optional[str]]]) -> Tuple[str, str]:
    """(text, tone) after Send all: *outcomes* are (the finding's title, the error or None)."""
    failed = [(t, e) for t, e in outcomes if e]
    done = len(outcomes) - len(failed)
    text = f"Sent {done} of {len(outcomes)}."
    if failed:
        title, error = failed[0]
        text += f" {len(failed)} could not be sent - {title}: {error}" + (" ..." if len(failed) > 1 else "")
    return text, "bad" if failed else "ok"


# --- the find pass ------------------------------------------------------------------------------------------

def last_run_text(run, now: Optional[datetime] = None) -> str:
    """When the last find pass ran and what it did: ``Last find pass: 04:30 (scheduled) - 12 series searched, 1 sent,
    2 waiting for you``. A pass not finished yet says it is running; none at all says so."""
    from .download_rules import when_text

    if run is None:
        return NEVER_RAN
    how = TRIGGER_TEXT.get(run.trigger, run.trigger)
    if not run.finished_at:
        started = when_text(run.started_at, now)
        return f"A find pass is running ({how}, started {started})." if started else f"A find pass is running ({how})."
    when = when_text(run.finished_at, now)
    head = f"Last find pass: {when} ({how})" if when else f"Last find pass ({how})"
    return f"{head} - {run.summary}." if run.summary else f"{head}."


def report_text(report, bulk: bool = False) -> str:
    """The line after Find now / a bulk find: ``Find now: 12 series searched, 1 sent, 2 waiting for you.``"""
    head = "Bulk find" if bulk else "Find now"
    return f"{head}: {report.summary}."

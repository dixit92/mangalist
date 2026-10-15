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


def whose_default(library: bool) -> str:
    """``the default`` (Settings > Automation) or ``the library's default`` (Settings > Library > Edit, schema 8)."""
    return "the library's default" if library else "the default"


def default_entry_label(default: str, library: bool = False) -> str:
    """The row menu's "follow the default" entry: ``Use the default (Notify)``, or ``Use the library's default (Off)``
    when the series' library sets one."""
    return f"Use {whose_default(library)} ({mode_label(default)})"


def default_entry_tip(library: Optional[bool]) -> str:
    """The tooltip of that entry: where the default it follows is set (None: a selection whose defaults differ)."""
    if library is None:
        return ("Follow the default again: each series its library's (Settings > Library > Edit) or else the one in "
                "Settings > Automation")
    if library:
        return "Follow the default set for this series' library in Settings > Library (Edit)"
    return "Follow the default set in Settings > Automation"


def mode_text(own: Optional[str], default: str, library: bool = False) -> str:
    """What the details panel says: ``Auto``, or ``Notify (the default)`` / ``Off (the library's default)`` for a series
    that follows a default."""
    if own in MODES:
        return mode_label(own)
    return f"{mode_label(default)} ({whose_default(library)})"


def default_of(backend, series_id: int) -> Tuple[str, bool]:
    """(the default a series follows, True when it is its library's): the backend's ``default_for`` when it has one,
    else the default setting."""
    fn = getattr(backend, "default_for", None)
    if callable(fn):
        mode, library = fn(series_id)
        return mode, bool(library)
    return backend.default_mode(), False


def defaults_of(backend, series_ids: Iterable[int]) -> Dict[int, Tuple[str, bool]]:
    """series id -> :func:`default_of`, in one read when the backend has ``series_defaults`` (else one per series, or
    the default setting for all on a backend without library defaults)."""
    ids = sorted({int(i) for i in series_ids})
    if not ids:
        return {}
    fn = getattr(backend, "series_defaults", None)
    if callable(fn):
        got = {int(k): (v[0], bool(v[1])) for k, v in dict(fn(ids)).items()}
        if len(got) < len(ids):                         # a series the store does not know: the default setting
            default = backend.default_mode()
            got.update({i: (default, False) for i in ids if i not in got})
        return got
    if callable(getattr(backend, "default_for", None)):
        return {i: default_of(backend, i) for i in ids}
    default = backend.default_mode()
    return {i: (default, False) for i in ids}


def selection_default(pairs: Iterable[Tuple[str, bool]]) -> Tuple[str, Optional[bool]]:
    """(the "follow the default" entry's label, whose default it is - None when they differ) for a selection's
    (default, from its library) pairs: the default named when every series follows the same one, the mode alone when
    they agree on it from different places, a plain ``Use the default`` across libraries whose defaults differ."""
    pairs = set(pairs)
    if len(pairs) == 1:
        mode, library = pairs.pop()
        return default_entry_label(mode, library), library
    modes = {mode for mode, _library in pairs}
    if len(modes) == 1:
        return default_entry_label(modes.pop()), None
    return "Use the default", None


def common_choice(owns: Iterable[Optional[str]]) -> Optional[str]:
    """The row menu entry to tick for a selection: the one mode every series has as its own (``DEFAULT_KEY`` when all
    follow the default), or None when they differ (nothing ticked)."""
    keys = {own if own in MODES else DEFAULT_KEY for own in owns}
    return keys.pop() if len(keys) == 1 else None


def set_mode_text(mode: Optional[str], n: int, default: Optional[str], library: bool = False) -> str:
    """The status line after the row menu changed the mode of *n* series (*default*: the one they follow now, None
    when it differs between them)."""
    series = f"{n} series"
    if mode is None:
        if default is None:
            return f"Automatic downloads: {series} follow their defaults again."
        return f"Automatic downloads: {series} follow {whose_default(library)} again ({mode_label(default)})."
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


def source_text(finding) -> str:
    """Where a finding would come from: ``nyaa``, ``Suwayomi · MangaDex (EN)``; '' when it has nothing to send yet (or
    the pass did not say)."""
    payload = finding.payload if isinstance(finding.payload, Mapping) else {}
    kind = payload.get("type")
    if kind == "nyaa":
        return "nyaa"
    if kind == "suwayomi":
        name = payload.get("source_name")
        return f"Suwayomi · {name}" if name else "Suwayomi"
    return ""


def release_text(finding) -> str:
    """The release line of a Found row: its title, then where it comes from (``... (Digital) · nyaa``)."""
    source = source_text(finding)
    return f"{finding.title} · {source}" if source and finding.title else finding.title or source


def reason_text(finding) -> str:
    """Why a finding waits for the owner, as the Found row says it (the backend's words)."""
    return finding.reason or "Waits for you"


def ordinal(n: int) -> str:
    """``1st`` / ``2nd`` / ``3rd`` / ``11th`` / ``22nd``."""
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def status_text(finding, state: str = "", queue_pos: int = 0) -> Tuple[str, str]:
    """(the Found row's status chip, its chip kind): ``Change source?`` (warn) for a finding with nothing to send,
    ``Sending…`` / ``Queued · 2nd`` (run) while it is on its way, else ``Waiting`` (ready)."""
    if needs_source(finding):
        return "Change source?", "warn"
    if state == "sending":
        return "Sending…", "run"
    if state == "queued":
        return (f"Queued · {ordinal(queue_pos)}" if queue_pos > 0 else "Queued"), "run"
    return "Waiting", "ready"


def mode_chip(own: Optional[str], default: str, library: bool = False) -> Tuple[str, str]:
    """(a Found series header's mode chip, its tooltip): the series' OWN mode exactly - also when it equals the default
    (owner, 2026-10-15: "Show it exactly") - else ``Default (Notify)`` for a series that follows the default."""
    if own in MODES:
        same = " - the same as the default" if own == default else ""
        return mode_label(own), f"Automatic downloads: {mode_label(own)} (this series' own{same}). " \
                                f"{MODE_HELP[own]} Click to change it."
    where = "Settings > Library" if library else "Settings > Automation"
    return f"Default ({mode_label(default)})", f"Automatic downloads: {mode_label(default)}, {whose_default(library)} " \
                                                f"({where}). Click to change it."


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

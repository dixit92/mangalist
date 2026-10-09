"""Qt-free rules of the Download tab: the "To get" groups and their filter, what a row says about its download, the
badge colour of a download's status, the wording of times and schedules, and the "why" line of a release.

Kept apart from the widgets so the wording and the filtering are tested without a display.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from ..downloads.contracts import DownloadRecord, DownloadStatus, NyaaCandidate
from .shell import GROUP_CHAPTERS, GROUP_UPGRADES, GROUP_VOLUMES, GROUPS, WantedSeries
from .volumes_target import status_text

GROUP_TITLES: Mapping[str, str] = {GROUP_VOLUMES: "Missing volumes", GROUP_CHAPTERS: "Missing chapters",
                                   GROUP_UPGRADES: "Upgrades"}
#: (note, tone): the small text at the right of a group's header; ``warn`` is the amber of "needs Suwayomi".
GROUP_NOTES: Mapping[str, Tuple[str, str]] = {GROUP_VOLUMES: ("nyaa", "muted"),
                                              GROUP_CHAPTERS: ("needs Suwayomi", "warn"),
                                              GROUP_UPGRADES: ("nyaa", "muted")}
NOT_NYAA_REASONS: Mapping[str, str] = {
    GROUP_CHAPTERS: "Missing chapters come from Suwayomi, which is not set up yet (Settings > Connected services).",
}
#: The groups whose volumes come from nyaa (an upgrade is a volume held only as chapters).
NYAA_GROUPS = (GROUP_VOLUMES, GROUP_UPGRADES)
#: What a shell that does not decide upgrades yet says for them (gui.list_text before the volumes cycle).
LEGACY_UPGRADES_REASON = "coming later"
UPGRADE_NOT_FINDABLE = "This series cannot be upgraded from nyaa yet (only series matched in MangaPixer and licensed " \
                       "in English can be searched)."

# The search state of a series (this session): shown in its row until a download's own status replaces it.
SEARCH_QUEUED = "queued"
SEARCH_RUNNING = "searching"
SEARCH_READY = "ready"          # releases found
SEARCH_NONE = "none"            # searched, nothing usable
SEARCH_FAILED = "failed"

SEARCH_TEXT: Mapping[str, str] = {SEARCH_QUEUED: "Queued", SEARCH_RUNNING: "Searching...", SEARCH_READY: "Releases ready",
                                  SEARCH_NONE: "No releases", SEARCH_FAILED: "Search failed"}

#: Download statuses that are still going on: they outrank "releases ready" in a row.
_LIVE = (DownloadStatus.SENT, DownloadStatus.DOWNLOADED, DownloadStatus.FAILED)


def matches_filter(series: WantedSeries, text: str) -> bool:
    needle = text.strip().casefold()
    return not needle or needle in series.title.casefold() or any(needle in t.casefold() for t in series.titles)


def grouped(series: Iterable[WantedSeries], filter_text: str = "") -> List[Tuple[str, List[WantedSeries]]]:
    """(group, its series) for every group, in the fixed order, series sorted by title (case-insensitive); the
    filter keeps those matching it. A group with nothing stays in the list (the tab shows its header, count 0)."""
    buckets: Dict[str, List[WantedSeries]] = {g: [] for g in GROUPS}
    for item in series:
        if item.group in buckets and matches_filter(item, filter_text):
            buckets[item.group].append(item)
    for items in buckets.values():
        items.sort(key=lambda s: (s.title.casefold(), s.folder))
    return [(g, buckets[g]) for g in GROUPS]


def count_text(shown: int, total: int) -> str:
    if shown == total:
        return f"{total} series"
    return f"{shown} of {total} series"


def not_findable_reason(series: WantedSeries) -> str:
    """Why the nyaa search cannot run for *series* ('' when it can): the shell's own reason, or the group's."""
    if series.group not in NYAA_GROUPS:
        return NOT_NYAA_REASONS.get(series.group, series.reason or "Not available yet.")
    if series.findable:
        return ""
    if series.group == GROUP_UPGRADES and series.reason in ("", LEGACY_UPGRADES_REASON):
        return UPGRADE_NOT_FINDABLE
    return series.reason or "This series cannot be searched on nyaa yet."


def merge_entries(entries: Sequence[WantedSeries]) -> WantedSeries:
    """One folder's "To get" entries as the one series the releases panel searches: a series with missing volumes AND
    upgrades is one search for both (``missing`` = every wanted volume, exact, ascending). Without a searchable entry,
    the first in group order (its reason is the one shown)."""
    from dataclasses import replace

    from ..knowledge import fmt_num, to_decimal

    order = {g: i for i, g in enumerate(GROUPS)}
    entries = sorted(entries, key=lambda e: order.get(e.group, len(order)))
    searchable = [e for e in entries if e.group in NYAA_GROUPS and e.findable]
    if not searchable:
        return entries[0]
    if len(searchable) == 1:
        return searchable[0]
    numbers = {d for e in searchable for d in (to_decimal(v) for v in e.missing) if d is not None}
    return replace(searchable[0], missing=tuple(fmt_num(d) for d in sorted(numbers)),
                   gaps="  ·  ".join(e.gaps for e in searchable if e.gaps))


def upgrade_volumes_of(entries: Sequence[WantedSeries]) -> Tuple[str, ...]:
    """The upgrade volumes among one folder's searchable entries (the Download tab's note says what happens after)."""
    return tuple(v for e in entries if e.group == GROUP_UPGRADES and e.findable for v in e.missing)


# --- replaced chapters (upgrades) ---------------------------------------------------------------------------

def upgrade_note(volumes: Sequence[str], mode: str, days: int) -> str:
    """The line above the releases of a series with upgrade volumes: what happens to the chapters they replace."""
    from .volumes_target import numbers_text

    vols = numbers_text(volumes, pad=True)
    if mode == "delete":
        after = ("the chapter files they replace are listed for you to confirm; nothing is deleted before you do "
                 "(Settings > Automation)")
    else:
        after = (f"the chapter files they replace move to the holding folder, restorable for {days} days "
                 "(Settings > Automation)")
    return f"Upgrade {vols}: volumes for chapters you hold. Once filed, {after}."


def _files(n: int) -> str:
    return f"{n} chapter file{'s' if n != 1 else ''}"


def replaced_bar_text(batches: Sequence) -> Tuple[str, str]:
    """(text, tone) of the Download tab's replaced-chapters line; ('', '') when there is nothing to show. Pending
    batches ask ("Replace 24 chapter files of 2 series?"); held ones say where they are."""
    pending = [b for b in batches if b.status == "pending"]
    held = [b for b in batches if b.status == "held"]
    failed = [b for b in batches if b.status == "failed"]
    if failed and not pending:
        return (f"Replacing chapters failed for {len({b.series_dir for b in failed})} series; the chapters are still "
                "in the library", "warn")
    if pending:
        n = sum(len(b.files) for b in pending)
        series = len({b.series_dir for b in pending})
        waiting = [b for b in pending if b.mode == "holding" and b.error]
        text = f"Replace {_files(n)} of {series} series with the volumes filed?"
        if waiting:
            text += f" ({len(waiting)} could not be moved yet)"
        return text, "warn"
    if held:
        n = sum(len(b.files) for b in held)
        return f"{_files(n)} replaced by volumes are in the holding folder ({len(held)} series)", "muted"
    return "", ""


def batch_status_text(batch) -> str:
    """One batch's state in words (the Replaced chapters dialog)."""
    if batch.status == "pending":
        if batch.mode == "holding" and batch.error:
            return f"Not moved yet: {batch.error}"
        return "Waiting for your answer"
    if batch.status == "held":
        until = when_text(batch.purge_after)
        return f"In the holding folder until {until}" if until else "In the holding folder"
    return {"restored": "Restored", "purged": "Emptied from the holding folder", "deleted": "Deleted",
            "declined": "Kept", "failed": f"Failed: {batch.error}" if batch.error else "Failed",
            "nothing": "Nothing to replace"}.get(batch.status, batch.status)


def row_status(record: Optional[DownloadRecord], search: Optional[str]) -> str:
    """The text at the right of a "To get" row. A live download (sent, downloaded, failed) beats a search result;
    a search in flight beats an old download."""
    if search in (SEARCH_QUEUED, SEARCH_RUNNING):
        return SEARCH_TEXT[search]
    if record is not None and record.status in _LIVE:
        return status_text(record)
    if search in SEARCH_TEXT:
        return SEARCH_TEXT[search]
    if record is not None and record.status in (DownloadStatus.FILED, DownloadStatus.REMOVED):
        return status_text(record)
    return ""


# --- the In progress list --------------------------------------------------------------------------------

BADGE_RUN, BADGE_OK, BADGE_DONE, BADGE_BAD = "run", "ok", "done", "bad"


def badge_kind(record: DownloadRecord) -> str:
    """run: on its way (blue); ok: filed, still seeding (green); done: finished or cancelled (grey); bad: failed (red)."""
    status = record.status
    if status in (DownloadStatus.SENT, DownloadStatus.DOWNLOADED):
        return BADGE_RUN
    if status == DownloadStatus.FILED:
        return BADGE_OK
    if status == DownloadStatus.FAILED:
        return BADGE_BAD
    return BADGE_DONE


def parse_when(text: Optional[str]) -> Optional[datetime]:
    if not text:
        return None
    try:
        when = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo is not None else when.replace(tzinfo=timezone.utc)


def when_text(text: Optional[str], now: Optional[datetime] = None) -> str:
    """``10:48`` for today, ``Oct 7 19:21`` for another day (local time), '' when unknown."""
    when = parse_when(text)
    if when is None:
        return ""
    local = when.astimezone()
    today: date = (now.astimezone() if now is not None else datetime.now().astimezone()).date()
    if local.date() == today:
        return local.strftime("%H:%M")
    return f"{local.strftime('%b')} {local.day} {local.strftime('%H:%M')}"


def next_check_text(next_run: Optional[str], now: Optional[datetime] = None) -> str:
    """"Next automatic check 11:21", or what is known when the scheduler's time is not."""
    when = when_text(next_run, now)
    return f"Next automatic check {when}" if when else "Checks run automatically every hour"


def release_why(candidate: NyaaCandidate) -> str:
    """The small line under a release's name: the ranking reasons, plus what the title does not say."""
    parts = list(candidate.reasons)
    if candidate.not_comic:
        parts.append("light novel / not a comic release")
    if candidate.remake and not any("remake" in p for p in parts):
        parts.append("marked as a remake on nyaa")
    if candidate.trusted:
        parts.append("trusted uploader")
    return ", ".join(parts) if parts else "no ranking reasons given"


def series_names_for(records: Sequence[DownloadRecord], wanted: Iterable[WantedSeries],
                     known: Optional[Mapping[int, str]] = None) -> Dict[int, str]:
    """series id -> a name for the list: the names the backend gave, else the wanted series' titles."""
    names: Dict[int, str] = {}
    for item in wanted:
        if item.series_id is not None:
            names[item.series_id] = item.title
    names.update(known or {})
    return {r.series_id: names.get(r.series_id, f"Series #{r.series_id}") for r in records}


# --- Settings: schedules ---------------------------------------------------------------------------------

SCHEDULE_ROWS = (("Rescan the library", "MANGALIST_RESCAN_SCHEDULE", "daily@03:30"),
                 ("Sync with MangaPixer", "MANGALIST_MANGAPIXER_SYNC_SCHEDULE", "daily@03:15"),
                 ("File finished downloads", "MANGALIST_DOWNLOADS_SCHEDULE", "every 1h"))


def schedule_text(described: str) -> str:
    """``daily@03:30`` -> ``daily 03:30``; ``every 1h`` -> ``every hour``; ``every 12h`` -> ``every 12 hours``."""
    if described.startswith("daily@"):
        return "daily " + described[len("daily@"):]
    if described.startswith("every ") and described.endswith("h"):
        hours = described[len("every "):-1]
        return "every hour" if hours in ("1", "1.0") else f"every {hours} hours"
    return described


def schedule_rows(env: Optional[Mapping[str, str]] = None) -> List[Tuple[str, str, bool]]:
    """(what, when, from the environment?) for the Automation section. The schedules come from the container's
    environment; a bad value is shown as it is, flagged, rather than hidden."""
    import os

    from ..headless.schedule import parse_schedule

    env = os.environ if env is None else env
    rows = []
    for label, name, default in SCHEDULE_ROWS:
        raw = env.get(name)
        text = default if raw is None else raw
        try:
            parsed = parse_schedule(text)
        except ValueError:
            when = f"{text} (not understood)"
        else:
            when = schedule_text(parsed.describe()) if parsed is not None else "off"
            if name == "MANGALIST_DOWNLOADS_SCHEDULE" and parsed is not None:
                when += " (and Check qBittorrent now)"
        rows.append((label, when, raw is not None))
    return rows

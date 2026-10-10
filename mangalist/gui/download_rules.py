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
#: The Missing chapters group's note once Suwayomi is connected (the Suwayomi MVP).
CHAPTERS_READY_NOTE: Tuple[str, str] = ("Suwayomi", "muted")


def group_note(group: str, suwayomi_ready: bool = False) -> Tuple[str, str]:
    """(note, tone) at the right of a group's header: Missing chapters says "needs Suwayomi" (amber) until Suwayomi is
    connected, then "Suwayomi"."""
    if group == GROUP_CHAPTERS and suwayomi_ready:
        return CHAPTERS_READY_NOTE
    return GROUP_NOTES[group]


def chapters_reason(series: WantedSeries, suwayomi_ready: bool, series_id: Optional[int] = None) -> str:
    """Why the chapters panel cannot look a Missing chapters series up ('' when it can): Suwayomi not set up, the
    folder not scanned yet, or no chapter numbers known."""
    if not suwayomi_ready:
        return NOT_NYAA_REASONS[GROUP_CHAPTERS]
    if (series_id if series_id is not None else series.series_id) is None:
        return "MangaList has not scanned this folder yet: rescan first."
    if not series.missing:
        return "The missing chapter numbers are not known for this series."
    return ""
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
    if held:                            # the space first (owner, 2026-10-10: more useful than the number of files)
        n = sum(len(b.files) for b in held)
        series = len({b.series_dir for b in held})
        return (f"{held_size_text(held)} of replaced chapters in the holding folder ({_files(n)}, {series} series)",
                "muted")
    return "", ""


def held_size_text(batches: Sequence) -> str:
    """The space the held batches take in the holding folder ("1.2 GB", "340 MB")."""
    from ..downloads.partial import size_text

    return size_text(sum(f.size for b in batches for f in b.files))


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


#: A chip at the right of a "To get" row: (text, kind) - kind is a badge colour (run, ok, bad, done, muted) or
#: ``ready`` (releases found: an outlined pill, not a download).
Chip = Tuple[str, str]
#: The search states as chips.
SEARCH_CHIPS: Mapping[str, Chip] = {SEARCH_QUEUED: ("Queued", "muted"), SEARCH_RUNNING: ("Searching...", "muted"),
                                    SEARCH_READY: ("Releases ready", "ready"), SEARCH_NONE: ("No releases", "muted"),
                                    SEARCH_FAILED: ("Search failed", "bad")}
#: The downloads a row always shows: queued under the download budget, the torrent in qBittorrent (on its way, or filed
#: and still there), or failed.
_SHOWN = (DownloadStatus.QUEUED, DownloadStatus.SENT, DownloadStatus.DOWNLOADED, DownloadStatus.FILED,
          DownloadStatus.FAILED)
MAX_DOWNLOAD_CHIPS = 2


def download_chip(record: DownloadRecord) -> Chip:
    """One download as a chip: "Queued v36" (grey: waiting under the download budget), "Downloading v36", "Downloaded
    v36", "Seeding v09-v18", "Filed v09-v18 - stopped", "Failed: ...", "Filed v03 - done" - in the In progress list's
    badge colour. The numbers are the record's wanted units, so a chapter download gets the same chips: "Downloading ch
    101-104", "Filed ch 101-104" (a chapter download does not seed)."""
    from .volumes_target import units_text
    units = units_text(record)
    status = record.status
    if record.is_chapters and status in (DownloadStatus.FILED, DownloadStatus.REMOVED) and not record.error:
        return (f"Filed {units}" if units else "Filed"), badge_kind(record)
    if status == DownloadStatus.QUEUED:
        text = f"Queued {units}" if units else "Queued"
    elif status == DownloadStatus.SENT:
        text = f"Downloading {units}" if units else "Downloading"
    elif status == DownloadStatus.DOWNLOADED:
        text = f"Downloaded {units}" if units else "Downloaded"
    elif status == DownloadStatus.FILED and not record.error:
        text = f"Seeding {units}" if units else "Seeding"
    else:
        text = status_text(record)
    return text, badge_kind(record)


def merge_batches(records: Iterable[DownloadRecord]) -> List[DownloadRecord]:
    """Chapter downloads as the owner sent them: the chapter records of one Send (``batch``) that are in the same status
    (and, failed, for the same reason) become one record - their chapters together, the newest id and update, the source
    and group in its title ("Ch. 101-104 · Group · MangaDex (EN)") - so the chips and the In progress list show
    "Downloading ch 101-104" once, not four times. Torrent records pass unchanged. The order is kept (a merged record
    takes the place of its first member)."""
    from dataclasses import replace

    from .volumes_target import numbers_text

    out: List[DownloadRecord] = []
    groups: Dict[tuple, List[DownloadRecord]] = {}
    for record in records:
        if not record.is_chapters:
            out.append(record)
            continue
        key = (record.batch or f"#{record.id}", record.status, record.error or "")
        if key not in groups:
            groups[key] = []
            out.append(record)                  # its place; replaced below
        groups[key].append(record)
    from ..knowledge import to_decimal

    merged: Dict[int, DownloadRecord] = {}
    for members in groups.values():
        first = members[0]
        chapters = tuple(dict.fromkeys(c for m in members for c in m.wanted_chapters))
        chapters = tuple(sorted(chapters, key=lambda c: (to_decimal(c) is None, to_decimal(c) or 0)))
        parts = [f"Ch. {numbers_text(chapters)}" if chapters else "Chapters"]
        groups_named = sorted({m.group for m in members if m.group})
        if groups_named:
            parts.append(" + ".join(groups_named))
        if first.source:
            parts.append(first.source)
        title = " · ".join(parts) if len(members) > 1 else first.title or " · ".join(parts)
        merged[first.id] = replace(first, id=max(m.id for m in members), wanted_chapters=chapters, title=title,
                                   updated_at=max(m.updated_at for m in members),
                                   filed_files=tuple(f for m in members for f in m.filed_files),
                                   copied=any(m.copied for m in members))
    return [merged.get(r.id, r) if r.is_chapters else r for r in out]


def row_chips(records: Iterable[DownloadRecord], search: Optional[str]) -> List[Chip]:
    """The chips at the right of a "To get" row (owner, 2026-10-09: "user should be aware if there's a torrent
    already under download for a series"): every torrent of the series still in qBittorrent or failed, newest first
    (at most two, then "+N"), then the search state - so a series with a torrent is never shown as plain "Releases
    ready". A queued download (the download budget) shows the same way. A finished download (removed from qBittorrent)
    shows only when there is nothing else to say. Chapter downloads show by Send (:func:`merge_batches`)."""
    records = sorted(merge_batches(records), key=lambda r: r.id, reverse=True)
    live = [r for r in records if r.status in _SHOWN]
    chips = [download_chip(r) for r in live[:MAX_DOWNLOAD_CHIPS]]
    if len(live) > MAX_DOWNLOAD_CHIPS:
        chips.append((f"+{len(live) - MAX_DOWNLOAD_CHIPS}", "muted"))
    if search in SEARCH_CHIPS:
        chips.append(SEARCH_CHIPS[search])
    if not chips:
        done = next((r for r in records if r.status == DownloadStatus.REMOVED), None)
        if done is not None:
            chips.append(download_chip(done))
    return chips


def chips_text(chips: Sequence[Chip]) -> str:
    """The chips as one line (the row's plain status: tooltips, tests, accessibility)."""
    return " · ".join(text for text, _kind in chips)


def in_qbittorrent(records: Iterable[DownloadRecord]) -> List[DownloadRecord]:
    """The series' downloads whose torrent is still in qBittorrent (on its way, or filed and seeding), newest first."""
    keep = (DownloadStatus.SENT, DownloadStatus.DOWNLOADED, DownloadStatus.FILED)
    return sorted((r for r in records if r.status in keep and not r.is_chapters), key=lambda r: r.id, reverse=True)


def in_hand(records: Iterable[DownloadRecord]) -> List[DownloadRecord]:
    """:func:`in_qbittorrent` plus the series' QUEUED downloads (waiting under the download budget), newest first: a
    release in either is not sent again."""
    keep = (DownloadStatus.QUEUED, DownloadStatus.SENT, DownloadStatus.DOWNLOADED, DownloadStatus.FILED)
    return sorted((r for r in records if r.status in keep and not r.is_chapters), key=lambda r: r.id, reverse=True)


# --- the In progress list --------------------------------------------------------------------------------

BADGE_RUN, BADGE_OK, BADGE_DONE, BADGE_BAD, BADGE_QUEUED = "run", "ok", "done", "bad", "muted"


#: Downloads that are over: their torrent left qBittorrent at its seed goal ("Filed v03 - done") or was cancelled. The
#: In progress list hides them unless asked (owner, 2026-10-10: "They are not in progress anymore"); a failed one
#: stays in view - it wants a look.
FINISHED = (DownloadStatus.REMOVED, DownloadStatus.CANCELLED)


def in_progress(records: Iterable[DownloadRecord], show_finished: bool = False) -> List[DownloadRecord]:
    """The records the In progress list shows, in the order given."""
    return [r for r in records if show_finished or r.status not in FINISHED]


def badge_kind(record: DownloadRecord) -> str:
    """queued: waiting under the download budget (muted grey); run: on its way (blue); ok: filed, still seeding
    (green); done: finished or cancelled (grey); bad: failed (red)."""
    status = record.status
    if status == DownloadStatus.QUEUED:
        return BADGE_QUEUED
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

# The schedules the Automation section edits (owner, 2026-10-09). The dispatch batch is left out: it is a stub that
# dispatches nothing yet, so a time for it would only confuse. Stored in the database; the container's variables only
# seed them (see mangalist/headless/settings.py).
SCHEDULE_JOBS = ("rescan", "mangapixer-sync", "downloads")

#: The choices each schedule offers (owner, 2026-10-10: "good options are: Weekly, Daily, Every 12 hours and Every 6
#: hours"), plus Off. Filing finished downloads also keeps Every hour - its default; without it filing could not run
#: more often than every 6 hours. A current value that is none of these (a container variable such as "every 3h") is
#: offered as one more choice, so it is never changed silently.
CHOICE_WEEKLY, CHOICE_DAILY, CHOICE_OFF = "weekly", "daily", "off"
SCHEDULE_CHOICES: Tuple[Tuple[str, str], ...] = ((CHOICE_WEEKLY, "Weekly"), (CHOICE_DAILY, "Daily"),
                                                 ("every 12h", "Every 12 hours"), ("every 6h", "Every 6 hours"),
                                                 (CHOICE_OFF, "Off"))
HOURLY_CHOICE: Tuple[str, str] = ("every 1h", "Every hour")
DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
DEFAULT_AT = (3, 30)                    # the time a schedule switched to Weekly or Daily starts from
DEFAULT_WEEKDAY = 6                     # ... and the day (Sunday) a Weekly one starts from


@dataclass(frozen=True)
class ScheduleChoice:
    """A schedule as the Automation section's controls hold it: the choice (``weekly``, ``daily``, ``off`` or an
    interval such as ``every 12h``), and the day / time Weekly and Daily use."""

    choice: str
    weekday: int = DEFAULT_WEEKDAY
    hour: int = DEFAULT_AT[0]
    minute: int = DEFAULT_AT[1]

    def text(self) -> str:
        """The canonical schedule text to store (``weekly@sun 03:30``, ``daily@03:30``, ``every 12h``, ``off``)."""
        from ..headless.schedule import WEEKDAYS

        if self.choice == CHOICE_WEEKLY:
            return f"weekly@{WEEKDAYS[self.weekday]} {self.hour:02d}:{self.minute:02d}"
        if self.choice == CHOICE_DAILY:
            return f"daily@{self.hour:02d}:{self.minute:02d}"
        return self.choice


def schedule_choices(job: str, current: str) -> List[Tuple[str, str]]:
    """(choice, label) for *job*'s dropdown; *current* (canonical text) is added when it is none of them."""
    out = list(SCHEDULE_CHOICES)
    if job == "downloads":
        out.insert(0, HOURLY_CHOICE)
    choice = choice_of(current).choice
    if choice not in {c for c, _l in out}:
        out.insert(len(out) - 1, (choice, f"{schedule_text(choice)[:1].upper()}{schedule_text(choice)[1:]}"))
    return out


def choice_of(canonical: str) -> ScheduleChoice:
    """The controls' state for a canonical schedule text (what :func:`schedule_entries` gives)."""
    from ..headless.schedule import DailyAt, WeeklyAt, parse_schedule

    try:
        parsed = parse_schedule(canonical)
    except ValueError:
        return ScheduleChoice(canonical)                    # not understood: shown as it is
    if parsed is None:
        return ScheduleChoice(CHOICE_OFF)
    if isinstance(parsed, WeeklyAt):
        return ScheduleChoice(CHOICE_WEEKLY, parsed.weekday, parsed.hour, parsed.minute)
    if isinstance(parsed, DailyAt):
        return ScheduleChoice(CHOICE_DAILY, hour=parsed.hour, minute=parsed.minute)
    return ScheduleChoice(parsed.describe())


@dataclass(frozen=True)
class ScheduleEntry:
    job: str
    label: str
    edit_text: str          # what the field holds: the canonical text, or a bad container value as it is
    when: str               # the plain-words reading ("daily 03:30"), or "<text> (not understood)"
    source: str             # headless.settings.SOURCE_STORED / SOURCE_ENV / SOURCE_DEFAULT
    valid: bool


def schedule_text(described: str) -> str:
    """``weekly@sun 03:30`` -> ``every Sunday 03:30``; ``daily@03:30`` -> ``daily 03:30``; ``every 1h`` -> ``every hour``;
    ``every 12h`` -> ``every 12 hours``."""
    if described.startswith("weekly@"):
        from ..headless.schedule import WEEKDAYS

        day, _sp, at = described[len("weekly@"):].partition(" ")
        name = DAY_NAMES[WEEKDAYS.index(day)] if day in WEEKDAYS else day
        return f"every {name} {at}"
    if described.startswith("daily@"):
        return "daily " + described[len("daily@"):]
    if described.startswith("every ") and described.endswith("h"):
        hours = described[len("every "):-1]
        return "every hour" if hours in ("1", "1.0") else f"every {hours} hours"
    return described


def schedule_entries(db, env: Optional[Mapping[str, str]] = None) -> List[ScheduleEntry]:
    """The schedules for the Automation section: what is in force and where it comes from (set in Settings, the
    container's variable, or the default). A bad value is shown as it is, flagged, rather than hidden."""
    from ..headless.schedule import parse_schedule
    from ..headless.settings import SCHEDULE_BY_JOB, resolve_schedule

    rows = []
    for job in SCHEDULE_JOBS:
        spec = SCHEDULE_BY_JOB[job]
        choice = resolve_schedule(spec, db, env)
        try:
            parsed = parse_schedule(choice.text)
        except ValueError:
            rows.append(ScheduleEntry(job, spec.label, choice.text, f"{choice.text} (not understood)", choice.source,
                                      False))
            continue
        canonical = parsed.describe() if parsed is not None else "off"
        when = schedule_text(canonical) if parsed is not None else "off"
        if job == "downloads" and parsed is not None:
            when += " (and Check qBittorrent now)"
        rows.append(ScheduleEntry(job, spec.label, canonical, when, choice.source, True))
    return rows


def schedule_rows(env: Optional[Mapping[str, str]] = None) -> List[Tuple[str, str, bool]]:
    """(what, when, from the container's environment?) as the container's variables alone give them (no database):
    the schedules before anything is set in Settings."""
    return [(e.label, e.when, e.source == "env") for e in schedule_entries(None, env)]


def save_schedule_text(db, job: str, text: str) -> str:
    """Check and store the schedule typed for *job*; the stored text. ValueError with a plain message for the owner."""
    from ..headless.settings import SCHEDULE_BY_JOB, save_schedule

    return save_schedule(db, SCHEDULE_BY_JOB[job], text)


def reset_schedule_text(db, job: str) -> None:
    """Forget the stored schedule of *job*: the container's value applies again."""
    from ..headless.settings import SCHEDULE_BY_JOB, reset_schedule

    reset_schedule(db, SCHEDULE_BY_JOB[job])

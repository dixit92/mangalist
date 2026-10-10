"""Qt-free rules of the volumes GUI: when "Find volumes on nyaa..." is enabled (and why not), what the nyaa
search is told about a series, and the wording of a download's status.

One function decides the enable rule so the row menu, the Wanted panel and the tests cannot drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from ..downloads.contracts import DownloadRecord, DownloadStatus
from ..knowledge import SOURCE_MANGAPIXER, SeriesKnowledge, fmt_num, to_decimal
from ..states import SeriesState

MAX_SEARCH_TITLES = 10     # the main title first, then alternatives; a series has dozens of aliases at most


@dataclass(frozen=True)
class VolumeTarget:
    """One series, as the nyaa dialog needs it (all volume numbers exact decimal strings)."""

    series_id: int
    folder: str
    title: str                          # shown in the header and the confirmation
    titles: Tuple[str, ...]             # what the search is given: main title first, then alternatives
    missing: Tuple[str, ...]
    held: Tuple[str, ...]
    upgrade: Tuple[str, ...] = ()       # those of *missing* that replace chapters held (an upgrade), for the wording


@dataclass(frozen=True)
class Availability:
    enabled: bool
    reason: str = ""                    # why it is disabled (a tooltip); empty when enabled
    target: Optional[VolumeTarget] = None


def search_titles(knowledge: SeriesKnowledge, *own_titles: Optional[str]) -> Tuple[str, ...]:
    """The series' names for the search: the English title, the folder's own names (in an English library usually
    the English release name), then the knowledge's main title and its alternatives; case-insensitive duplicates
    dropped. nyaa's English releases are named in English, so those come first."""
    seen = set()
    out: List[str] = []
    for title in (knowledge.english_title, *own_titles, knowledge.title, *knowledge.alt_titles):
        text = (title or "").strip()
        if text and text.casefold() not in seen:
            seen.add(text.casefold())
            out.append(text)
    return tuple(out[:MAX_SEARCH_TITLES])


def volume_label(number: str) -> str:
    """``'3'`` -> ``v03``, ``'12'`` -> ``v12``, ``'6.5'`` -> ``v6.5``."""
    return f"v{number.zfill(2)}" if number.isdigit() else f"v{number}"


def numbers_text(numbers: Iterable[str], *, pad: bool = False) -> str:
    """``['1','2','3','5','6.5']`` -> ``1-3, 5, 6.5`` (whole numbers in a row merge); ``pad`` writes the
    volume style ``v01-v03, v05, v6.5``. Numbers are exact: nothing goes through a float."""
    ints: List[int] = []
    out: List[str] = []

    def label(text: str) -> str:
        return volume_label(text) if pad else text

    def flush() -> None:
        if ints:
            first, last = ints[0], ints[-1]
            out.append(label(str(first)) if first == last else f"{label(str(first))}-{label(str(last))}")
            ints.clear()

    for number in sorted((d for d in (to_decimal(n) for n in numbers) if d is not None)):
        if number != number.to_integral_value():
            flush()
            out.append(label(fmt_num(number)))
        elif ints and int(number) == ints[-1] + 1:
            ints.append(int(number))
        elif ints and int(number) == ints[-1]:
            continue
        else:
            flush()
            ints.append(int(number))
    flush()
    return ", ".join(out)


def find_volumes_availability(*, series_id: Optional[int], folder: str, title: str, english_title: Optional[str],
                              knowledge: Optional[SeriesKnowledge], state: Optional[SeriesState],
                              held: Sequence[str]) -> Availability:
    """Whether a series can be searched on nyaa. Enabled for a series matched in MangaPixer (source ``mangapixer``
    and ``matched``), licensed in English, with a scanned folder. The missing volumes are a ranking hint, not a
    condition: when MangaList cannot tell which English volumes are out (``missing`` empty), nyaa's results are
    compared with the volumes held - a release on nyaa is itself proof that a volume is out."""
    if knowledge is None or knowledge.source != SOURCE_MANGAPIXER:
        return Availability(False, "Only series matched in MangaPixer can be searched on nyaa "
                                   "(this one is not linked there).")
    if not knowledge.matched:
        why = knowledge.not_a_series_reason or "MangaPixer has not matched this series yet"
        return Availability(False, f"Only series matched in MangaPixer can be searched on nyaa ({why}).")
    if not knowledge.licensed:
        return Availability(False, "Not licensed in English: there are no English volumes to look for.")
    missing = state.missing_volumes if state is not None else ()
    if series_id is None:
        return Availability(False, "MangaList has not scanned this folder yet: rescan first.")
    return Availability(True, "", VolumeTarget(series_id=series_id, folder=folder, title=title,
                                               titles=search_titles(knowledge, english_title, title),
                                               missing=tuple(missing), held=tuple(held)))


# --- download status wording ----------------------------------------------------------------------


def units_text(record: DownloadRecord) -> str:
    """The units a download is for: ``v03-v05`` (volumes) or ``ch 101-104`` (a chapter download)."""
    if getattr(record, "is_chapters", False):
        chapters = numbers_text(record.wanted_chapters)
        return f"ch {chapters}" if chapters else ""
    return numbers_text(record.wanted_volumes, pad=True)


def chapter_status_text(record: DownloadRecord) -> str:
    """A chapter download (Suwayomi): "Downloading", "Downloading - <Suwayomi's error>", "Downloaded", "Filed ch 4",
    "Filed ch 4 - <why Suwayomi still has its copy>", "Filed ch 4 - done", "Failed: <reason>", "Cancelled"."""
    status, units = record.status, units_text(record)
    filed = f"Filed {units}" if units else "Filed"
    if status == DownloadStatus.SENT:
        return f"Downloading - {record.error}" if record.error else "Downloading"
    if status == DownloadStatus.FILED:
        return f"{filed} - {record.error}" if record.error else filed
    if status == DownloadStatus.REMOVED:
        return f"{filed} - done"
    if status == DownloadStatus.FAILED:
        return f"Failed: {record.error}" if record.error else "Failed"
    return {DownloadStatus.DOWNLOADED: "Downloaded", DownloadStatus.CANCELLED: "Cancelled"}.get(status,
                                                                                               status.capitalize())


def status_text(record: DownloadRecord) -> str:
    """"Queued - 2nd in line", "Downloading", "Downloaded", "Filed v03-v05 - seeding", "Filed v03-v05 - done",
    "Failed: <reason>", "Cancelled". A chapter download: :func:`chapter_status_text`.

    The volumes come first: once filed they are in the library, whatever happens to the torrent afterwards - "done"
    means qBittorrent finished seeding and the torrent with its downloaded copy was removed (never the library's).
    A queued download waits under the download budget; a note on it (e.g. bigger than the cap on its own) replaces its
    place in line."""
    if getattr(record, "is_chapters", False):
        return chapter_status_text(record)
    status = record.status
    if status == DownloadStatus.QUEUED:
        from ..downloads.budget import place_text

        return f"Queued - {record.error}" if record.error else f"Queued - {place_text(record.queue_position)}"
    if status in (DownloadStatus.FILED, DownloadStatus.REMOVED):
        vols = numbers_text(record.wanted_volumes, pad=True)
        filed = f"Filed {vols}" if vols else "Filed"
        if status == DownloadStatus.FILED:
            return f"{filed} - {record.error}" if record.error else f"{filed} - seeding"   # e.g. stopped by hand
        return f"{filed} - done"
    if status == DownloadStatus.FAILED:
        return f"Failed: {record.error}" if record.error else "Failed"
    return {DownloadStatus.SENT: "Downloading", DownloadStatus.DOWNLOADED: "Downloaded",
            DownloadStatus.CANCELLED: "Cancelled"}.get(status, status.capitalize())


def chapter_status_tooltip(record: DownloadRecord) -> str:
    lines = [record.title, f"Chapters: {numbers_text(record.wanted_chapters) or '-'}",
             f"From: {record.source or 'Suwayomi'}" + (f" · {record.group}" if record.group else " · no group named"),
             f"Target folder: {record.target_dir}", f"Updated: {record.updated_at}",
             "Chapter downloads are outside the download budget (it counts torrents only)."]
    if record.status == DownloadStatus.SENT:
        lines.append("In Suwayomi's download queue; MangaList files it once Suwayomi has it (every hour, or Check now).")
    if record.status == DownloadStatus.FILED:
        lines.append(f"The chapter is in the library; Suwayomi still has its downloaded copy"
                     + (f": {record.error}." if record.error else " (deleted at the next check)."))
    if record.status == DownloadStatus.REMOVED:
        lines.append("Done: filed into the library under MangaList's naming scheme; Suwayomi deleted its downloaded copy.")
    if record.copied:
        lines.append("Copied, not hard-linked: Suwayomi's download folder is on another filesystem.")
    if record.status == DownloadStatus.FAILED and record.error:
        lines.append(f"Error: {record.error}")
    return "\n".join(lines)


def status_tooltip(record: DownloadRecord) -> str:
    if getattr(record, "is_chapters", False):
        return chapter_status_tooltip(record)
    from ..downloads.budget import counts, place_text, size_note

    lines = [record.title, f"Volumes: {numbers_text(record.wanted_volumes, pad=True) or '-'}",
             f"Target folder: {record.target_dir}", f"Updated: {record.updated_at}"]
    if record.status == DownloadStatus.QUEUED:
        lines.append(f"Queued, {place_text(record.queue_position)}: MangaList hands it to qBittorrent when there is "
                     "room under the download budget (Settings > Download sources). Right-click to send it now or "
                     "change its place.")
        lines.append(f"Size: {size_note(record.size_bytes, record.size_source)}")
    elif counts(record):
        lines.append(f"Counts against the download budget: {size_note(record.size_bytes, record.size_source)}")
    if record.status == DownloadStatus.FILED:
        if record.error:
            lines.append(f"The volumes are in the library; the torrent is {record.error} - MangaList leaves it. "
                         "Resume it in qBittorrent, or right-click: Remove now.")
        else:
            lines.append("The volumes are in the library; qBittorrent is still seeding the torrent.")
    if record.status == DownloadStatus.REMOVED:
        if record.error:                # e.g. "removed in qBittorrent, not by MangaList"
            lines.append(f"Done: the torrent was {record.error}. The volumes stay in the library.")
        else:
            lines.append("Done: qBittorrent finished seeding, and MangaList removed the torrent and its downloaded "
                         "copy. The volumes stay in the library.")
    if record.copied:
        lines.append("Copied, not hard-linked: the library holds its own copy (double the space).")
    if record.status == DownloadStatus.FAILED and record.error:
        lines.append(f"Error: {record.error}")
    return "\n".join(lines)


def latest_by_series(records: Iterable[DownloadRecord]) -> Dict[int, DownloadRecord]:
    """The newest record (highest id) of each series: the one whose status the panels show."""
    latest: Dict[int, DownloadRecord] = {}
    for record in records:
        current = latest.get(record.series_id)
        if current is None or record.id > current.id:
            latest[record.series_id] = record
    return latest

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


@dataclass(frozen=True)
class Availability:
    enabled: bool
    reason: str = ""                    # why it is disabled (a tooltip); empty when enabled
    target: Optional[VolumeTarget] = None


def search_titles(knowledge: SeriesKnowledge, *own_titles: Optional[str]) -> Tuple[str, ...]:
    """The series' names for the search: the knowledge's main title (English first), its alternatives, then the
    folder's own names; case-insensitive duplicates dropped."""
    seen = set()
    out: List[str] = []
    for title in (knowledge.search_title, knowledge.title, *knowledge.alt_titles, *own_titles):
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
    """Whether a series can be searched on nyaa. Enabled only for a series matched in MangaPixer (source
    ``mangapixer`` and ``matched``) that has missing volumes and a scanned folder."""
    if knowledge is None or knowledge.source != SOURCE_MANGAPIXER:
        return Availability(False, "Only series matched in MangaPixer can be searched on nyaa "
                                   "(this one is not linked there).")
    if not knowledge.matched:
        why = knowledge.not_a_series_reason or "MangaPixer has not matched this series yet"
        return Availability(False, f"Only series matched in MangaPixer can be searched on nyaa ({why}).")
    missing = state.missing_volumes if state is not None else ()
    if not missing:
        return Availability(False, "No missing volumes: there is nothing to look for.")
    if series_id is None:
        return Availability(False, "MangaList has not scanned this folder yet: rescan first.")
    return Availability(True, "", VolumeTarget(series_id=series_id, folder=folder, title=title,
                                               titles=search_titles(knowledge, english_title, title),
                                               missing=tuple(missing), held=tuple(held)))


# --- download status wording ----------------------------------------------------------------------


def status_text(record: DownloadRecord) -> str:
    """"Sent", "Downloaded", "Filed v03-v05", "Failed: <reason>", "Removed", "Cancelled"."""
    status = record.status
    if status == DownloadStatus.FILED:
        vols = numbers_text(record.wanted_volumes, pad=True)
        return f"Filed {vols}" if vols else "Filed"
    if status == DownloadStatus.FAILED:
        return f"Failed: {record.error}" if record.error else "Failed"
    return {DownloadStatus.SENT: "Sent", DownloadStatus.DOWNLOADED: "Downloaded",
            DownloadStatus.REMOVED: "Removed", DownloadStatus.CANCELLED: "Cancelled"}.get(status, status.capitalize())


def status_tooltip(record: DownloadRecord) -> str:
    lines = [record.title, f"Volumes: {numbers_text(record.wanted_volumes, pad=True) or '-'}",
             f"Target folder: {record.target_dir}", f"Updated: {record.updated_at}"]
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

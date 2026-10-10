"""The download budget: how much MangaList has in a download client's hands, against the owner's cap. No Qt, no I/O.

Owner, 2026-10-09: "the user should be able to set how many 'active' things are currently being processed in terms of
size in settings and the rest of the items are queued if that cap is reached. For example: I set MangaList to use a
maximum of 50 GB. This means that at any point, MangaList is managing 50 GB of seeds/downloads including future
suwayomi or other download clients." Decisions of the same day: default cap 50 GB; one release bigger than the cap on
its own -> the owner is told and decides (never silently refused, never silently sent); the owner can override the
queue (send now past the cap, move a queued item to the front).

**What counts** (:func:`counts`): every download MangaList has sent and not yet seen removed - SENT (downloading),
DOWNLOADED (finished, not filed yet) and FILED (seeding; also when the filing fell back to a copy: the torrent's own data
is still there until it is removed). A FAILED download counts while its torrent is still in the client: a failed filing
leaves the torrent alone, so its data still sits on the disk until the owner removes it - and once a pass sees it gone
from the client it stops counting (``in_client``). QUEUED, REMOVED and CANCELLED do not count (a cancelled record's
torrent is the owner's from then on).

**The size counted** (``DownloadRecord.size_bytes`` / ``size_source``): the release's size from nyaa, or for a partial
send the total of the files it keeps, until qBittorrent reports its own figure (the selected files' bytes); each pass
writes that figure back to the record. A size that is not known at all counts as 0 and is said so.

The accounting works on :class:`~mangalist.downloads.contracts.DownloadRecord` alone - whichever client a record belongs
to - so chapter downloads (Suwayomi, later) join the same budget by being records with a size. Sizes are in bytes; the
cap is set in GB of 1024^3 bytes, the unit of every size MangaList shows (the releases table, the partial-download line).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple

from .contracts import DownloadRecord, DownloadStatus

GB = 1024 ** 3

#: Where a record's counted size comes from (``DownloadRecord.size_source``).
SIZE_RELEASE = "release"                    # the size nyaa lists for the release (the whole pack)
SIZE_SELECTED = "selected files"            # a partial send: the total of the files it keeps
SIZE_CLIENT = "qbittorrent"                 # qBittorrent's own figure for the files it downloads

#: The statuses whose download always counts against the cap.
COUNTED = (DownloadStatus.SENT, DownloadStatus.DOWNLOADED, DownloadStatus.FILED)

# What a send does when it would go over the cap (the confirmation's choice; ``Backend.send(..., over_cap=...)``).
OVER_CAP_QUEUE = "queue"            # record it as QUEUED; handed over when there is room (the default)
OVER_CAP_SEND = "send"              # the owner's override: send it now, past the cap

# The verdict of :meth:`BudgetState.verdict` for one more download.
FITS = "fits"                       # within the cap (or no cap), and nothing queued ahead of it
OVER = "over"                       # would go over the cap, or others are waiting in the queue: queue it (or override)
TOO_BIG = "too big"                 # bigger than the cap on its own: never fits; the owner decides


def gb_bytes(gb: float) -> int:
    return int(round(max(0.0, float(gb or 0)) * GB))


def gb_text(size: int) -> str:
    """``12.3 GB`` (always GB, one decimal, ``.0`` dropped: the cap's own unit, so usage and cap read alike)."""
    text = f"{max(0, size) / GB:.1f}"
    return f"{text[:-2] if text.endswith('.0') else text} GB"


def counts(record: DownloadRecord) -> bool:
    """True when *record*'s download counts against the cap (see the module docstring)."""
    if record.status in COUNTED:
        return True
    return record.status == DownloadStatus.FAILED and record.in_client


def used_bytes(records: Iterable[DownloadRecord]) -> int:
    return sum(max(0, r.size_bytes) for r in records if counts(r))


def queue_of(records: Iterable[DownloadRecord]) -> List[DownloadRecord]:
    """The QUEUED records in the order they are handed over (their ``queue_position``, then id)."""
    queued = [r for r in records if r.status == DownloadStatus.QUEUED]
    return sorted(queued, key=lambda r: (r.queue_position or 1 << 30, r.id))


@dataclass(frozen=True)
class BudgetState:
    """The cap and what counts against it right now."""

    cap_bytes: int                      # 0: no limit
    used_bytes: int
    queued: Tuple[DownloadRecord, ...] = ()     # in hand-over order
    unknown_sizes: int = 0              # counted downloads whose size is not known (counted as 0)

    @property
    def limited(self) -> bool:
        return self.cap_bytes > 0

    @property
    def free_bytes(self) -> int:
        """The room left under the cap (0 when at or over it; meaningless without a cap)."""
        return max(0, self.cap_bytes - self.used_bytes) if self.limited else 0

    @property
    def over(self) -> bool:
        """At or past the cap already (e.g. after the owner lowered it, or sent past it)."""
        return self.limited and self.used_bytes > self.cap_bytes

    def fits(self, size: int) -> bool:
        return not self.limited or self.used_bytes + max(0, size) <= self.cap_bytes

    def too_big(self, size: int) -> bool:
        return self.limited and size > self.cap_bytes

    def verdict(self, size: int, *, ignore_queue: bool = False) -> str:
        """What a new download of *size* bytes does: :data:`FITS`, :data:`OVER` or :data:`TOO_BIG`. The queue is
        first come, first served: while anything waits in it a new send queues behind it, even one that would fit."""
        if self.too_big(size):
            return TOO_BIG
        if not self.fits(size) or (self.waiting and not ignore_queue):
            return OVER
        return FITS

    @property
    def waiting(self) -> Tuple[DownloadRecord, ...]:
        """The queued downloads a hand-over will send in turn: not those bigger than the cap on their own (they wait
        for the owner, and never hold up the others)."""
        return tuple(r for r in self.queued if not self.too_big(r.size_bytes))

    def usage_text(self) -> str:
        """``using 12.3 GB of 50 GB`` (``using 12.3 GB, no limit`` without a cap)."""
        if not self.limited:
            return f"using {gb_text(self.used_bytes)}, no limit"
        return f"using {gb_text(self.used_bytes)} of {gb_text(self.cap_bytes)}"

    def summary(self) -> str:
        """``Using 12.3 GB of 50 GB; 2 downloads queued`` - the In progress list's line."""
        text = self.usage_text()
        text = text[:1].upper() + text[1:]
        if self.queued:
            n = len(self.queued)
            text += f"; {n} download{'s' if n != 1 else ''} queued"
        if self.unknown_sizes:
            text += (f"; {self.unknown_sizes} download{'s' if self.unknown_sizes != 1 else ''} of unknown size "
                     "not counted")
        return text


def state_of(records: Sequence[DownloadRecord], cap_gb: float) -> BudgetState:
    """The budget over *records* (every record of every client) under a cap of *cap_gb* GB (0: no limit)."""
    counted = [r for r in records if counts(r)]
    return BudgetState(cap_bytes=gb_bytes(cap_gb), used_bytes=used_bytes(counted), queued=tuple(queue_of(records)),
                       unknown_sizes=sum(1 for r in counted if r.size_bytes <= 0))


def ordinal(n: int) -> str:
    """``1st``, ``2nd``, ``3rd``, ``4th``, ``11th``, ``21st``."""
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def place_text(position: int) -> str:
    """``1st in line`` (or ``in the queue`` when the place is not known)."""
    return f"{ordinal(position)} in line" if position > 0 else "in the queue"


def size_note(size: int, source: str) -> str:
    """How a size was worked out, for the owner: ``1.7 GB (the release's size on nyaa; qBittorrent's own figure
    replaces it once it reports one)``."""
    if size <= 0:
        return "size not known yet"
    if source == SIZE_CLIENT:
        return f"{gb_text(size)} (as qBittorrent reports it)"
    if source == SIZE_SELECTED:
        return f"{gb_text(size)} (the selected files; qBittorrent's own figure replaces it once it reports one)"
    return f"{gb_text(size)} (the release's size on nyaa; qBittorrent's own figure replaces it once it reports one)"


def over_cap_text(state: BudgetState, size: int) -> str:
    """The confirmation's sentence for a send that would go over the cap (or wait behind the queue)."""
    if state.waiting and state.fits(size):
        n = len(state.waiting)
        return (f"{n} download{'s are' if n != 1 else ' is'} already waiting in the queue, so this one would wait "
                f"behind {'them' if n != 1 else 'it'} ({state.usage_text()}; this adds {gb_text(size)}).")
    return (f"This would go over the download budget: MangaList is {state.usage_text()}, and this adds "
            f"{gb_text(size)}.")


def too_big_text(size: int, state: BudgetState) -> str:
    return (f"This release is {gb_text(size)} - bigger than the whole download budget of {gb_text(state.cap_bytes)} "
            "on its own, so it would never fit under the cap.")


def first_waiting(queue: Sequence[DownloadRecord], state: BudgetState) -> Optional[DownloadRecord]:
    """The queued record a hand-over would try next (the first one that is not bigger than the cap on its own)."""
    return next((r for r in queue if not state.too_big(r.size_bytes)), None)

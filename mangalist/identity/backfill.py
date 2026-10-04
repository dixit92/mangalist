"""Background content-signature backfill (port of MangaPixer's ``ContentSignatureBackfill``, 1.31.1).

A move or rename is only recognised when the archive had a signature BEFORE it moved (a row without one never
counts as a move), so signatures are filled in the background after each scan - not lazily when a file has
already gone. :func:`backfill_signatures`:

- takes present archive rows without a signature, in batches by ascending id (resumable: signatures are
  committed every :data:`COMMIT_EVERY` files and when the pass stops, so a stopped pass simply leaves the rest
  for the next one);
- reads the same 128 KiB per file the move detection reads, re-checking the file's size + mtime against the row
  before and after hashing (a changed or still-written file is left for the next scan);
- writes the signature only while the row still has none and still describes those bytes (path + size +
  mtime: a file changed in place loses its signature at the next scan and is signed again here);
- is throttled per file (``per_file_delay``, default :data:`DEFAULT_DELAY`) and skips roots that are not
  reachable;
- afterwards runs the after-the-fact pairing and series carry-over (newly signed rows may now pair) and
  MangaPixer's pending carries.

No Qt here; the GUI runs it in a worker thread, the headless rescan after recording the scan.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from ..store.archives import abs_path
from .signature import bytes_read_for, signature_if_unchanged

_log = logging.getLogger(__name__)

BATCH_SIZE = 200
COMMIT_EVERY = 50              # signatures written per transaction (resumable at this granularity)
DEFAULT_DELAY = 0.01           # seconds per file (MangaPixer: 20 ms); gentle on a share during a first pass

ProgressFn = Callable[[int, int], None]     # (done, total)
StopFn = Callable[[], bool]


@dataclass
class BackfillResult:
    looked: int = 0                 # rows looked at
    signed: int = 0
    skipped: int = 0                # unreadable / changed / root offline: left for the next scan
    bytes_read: int = 0
    seconds: float = 0.0
    stopped: bool = False
    remaining: int = 0              # present rows still without a signature afterwards
    paired: int = 0                 # archives paired after the fact afterwards
    carries: List = field(default_factory=list)

    @property
    def renamed(self):
        return [(Path(c.old_folder), Path(c.new_folder)) for c in self.carries if c.carried]


def backfill_signatures(db, *, batch_size: int = BATCH_SIZE, per_file_delay: float = DEFAULT_DELAY,
                        should_stop: Optional[StopFn] = None, progress: Optional[ProgressFn] = None,
                        limit: Optional[int] = None, signer=None, follow_up: bool = True,
                        sleep: Callable[[float], None] = time.sleep) -> BackfillResult:
    """Sign present archives that have no signature yet (module docstring). *limit* caps the rows looked at
    in this pass; *follow_up* runs the pairing / carry-over afterwards."""
    signer = signer or signature_if_unchanged
    res = BackfillResult()
    started = time.monotonic()
    total = db.unsigned_count()
    if limit is not None:
        total = min(total, int(limit))
    roots: Dict[int, Optional[str]] = {}
    with db.connect() as con:
        for r in con.execute("SELECT id, path FROM roots"):
            roots[r["id"]] = r["path"] if os.path.isdir(r["path"]) else None
    cursor = 0
    pending: List[tuple] = []
    while True:
        if should_stop is not None and should_stop():
            res.stopped = True
            break
        take = batch_size if limit is None else min(batch_size, int(limit) - res.looked)
        if take <= 0:
            break
        with db.connect() as con:
            rows = con.execute("SELECT id, root_id, rel_path, size, mtime_ns FROM archives WHERE status = 'present'"
                               " AND signature IS NULL AND id > ? ORDER BY id LIMIT ?", (cursor, take)).fetchall()
        if not rows:
            break
        for r in rows:
            if should_stop is not None and should_stop():
                res.stopped = True
                break
            cursor = r["id"]
            res.looked += 1
            root = roots.get(r["root_id"])
            sig = None
            if root is not None:
                res.bytes_read += bytes_read_for(r["size"])
                sig = signer(abs_path(root, r["rel_path"]), r["size"], r["mtime_ns"])
            if sig is None:
                res.skipped += 1
            else:
                pending.append((sig, r["id"], r["size"], r["mtime_ns"]))
                if len(pending) >= COMMIT_EVERY:
                    _flush(db, pending, res)
            if progress is not None:
                progress(res.looked, max(total, res.looked))
            if per_file_delay > 0:
                sleep(per_file_delay)
        _flush(db, pending, res)
        if res.stopped or len(rows) < take:
            break
    res.seconds = round(time.monotonic() - started, 3)
    res.remaining = db.unsigned_count()
    if follow_up and not res.stopped:
        from .mangapixer import apply_pending
        from .moves import pair_after_the_fact

        try:
            pairing = pair_after_the_fact(db)
            res.paired = len(pairing.moves)
            res.carries = list(pairing.carries)
            res.carries += apply_pending(db)
        except Exception:  # noqa: BLE001 - the signatures are written; the next scan tries again
            _log.warning("Pairing after the signature backfill failed", exc_info=True)
    if res.signed or res.skipped:
        _log.info("Content signature backfill: %d archives signed, %d left for the next scan, %d KiB read in %.1f s",
                  res.signed, res.skipped, res.bytes_read // 1024, res.seconds)
    return res


def _flush(db, pending: List[tuple], res: BackfillResult) -> None:
    """Write the signatures computed so far in one transaction (a commit per file costs far more than the
    128 KiB read: closing the last connection checkpoints the WAL)."""
    if not pending:
        return
    with db.connect() as con:
        for sig, row_id, size, mtime_ns in pending:
            cur = con.execute("UPDATE archives SET signature = ? WHERE id = ? AND signature IS NULL AND size = ?"
                              " AND mtime_ns = ? AND status = 'present'", (sig, row_id, size, mtime_ns))
            if cur.rowcount == 1:
                res.signed += 1
            else:
                res.skipped += 1
    pending.clear()


__all__ = ["BATCH_SIZE", "COMMIT_EVERY", "DEFAULT_DELAY", "BackfillResult", "backfill_signatures"]

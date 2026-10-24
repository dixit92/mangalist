"""Backups of MangaList's database together with the extension host's (transition audit D4, 2026-10-24). No Qt.

The built-in extension host keeps its own database (``<data>/extension-host/host.db``): the manga and chapter ids
MangaList stores for every series choice and chapter download on the host. The two belong together - restoring one
from another day than the other makes MangaList's ids name other mangas and chapters (the host's database id,
:mod:`mangalist.downloads.host_identity`, then notices and finds them again, but the pair is what a restore wants).

:func:`snapshot` copies both, consistently (SQLite's backup API: committed WAL content included), into
``<data>/backups`` as a pair with one label: ``mangalist-<label>.db`` and ``extension-host-<label>.db`` (the second only
when the host has a database). Made:

- by the container's headless runner when MangaList starts on a new version (:func:`at_version_change`: label
  ``auto-<old>-to-<new>``; the newest :data:`KEEP_AUTO` automatic pairs are kept, older automatic ones deleted - never
  any other file in the folder);
- by hand, e.g. before a release swap: ``python -m mangalist.store.backups pre-2026.10.57`` (in the container:
  ``docker exec -u 99:100 mangalist /opt/mangalist/venv/bin/python -m mangalist.store.backups pre-<version>``).
"""

from __future__ import annotations

import logging
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Sequence

_log = logging.getLogger(__name__)

FOLDER = "backups"
HOST_DB = Path("extension-host") / "host.db"
META_LAST_VERSION = "backups.last_version"
AUTO_PREFIX = "auto-"
KEEP_AUTO = 2
_LABEL = re.compile(r"^[A-Za-z0-9._-]{1,80}$")


def _copy(src: Path, dst: Path) -> None:
    from ..paths import _copy_sqlite

    _copy_sqlite(src, dst)


def snapshot(data_dir: Path, label: str, *, db_file: Optional[Path] = None) -> List[Path]:
    """Copy MangaList's database and the extension host's (when there is one) into ``<data_dir>/backups`` under
    *label*. Returns the files written. Refuses a label with other characters than letters, digits, ``.``, ``_`` and
    ``-``, or one already used (nothing is overwritten)."""
    if not _LABEL.match(label or ""):
        raise ValueError(f"a backup label is letters, digits, '.', '_' and '-' only: {label!r}")
    data_dir = Path(data_dir)
    db_file = Path(db_file) if db_file is not None else data_dir / "mangalist.db"
    folder = data_dir / FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    pairs = [(db_file, folder / f"mangalist-{label}.db"), (data_dir / HOST_DB, folder / f"extension-host-{label}.db")]
    pairs = [(src, dst) for src, dst in pairs if src.is_file()]
    taken = [dst for _src, dst in pairs if dst.exists()]
    if taken:
        raise FileExistsError(f"a backup named {label!r} exists already: {taken[0]}")
    written = []
    for src, dst in pairs:
        _copy(src, dst)
        written.append(dst)
    _log.info("Backup %s: %s", label, ", ".join(str(p) for p in written) or "nothing to copy")
    return written


def _auto_labels(folder: Path) -> List[str]:
    """The automatic backups' labels, oldest first (by the time stamp that ends each label, then the file's time)."""
    found = {}
    for f in folder.glob(f"mangalist-{AUTO_PREFIX}*.db"):
        found[f.name[len("mangalist-"):-len(".db")]] = f.stat().st_mtime
    return sorted(found, key=lambda k: (k[-15:], found[k]))


def prune_auto(data_dir: Path, keep: int = KEEP_AUTO) -> List[Path]:
    """Delete the automatic pairs but the newest *keep* (never a backup made by hand)."""
    folder = Path(data_dir) / FOLDER
    if not folder.is_dir():
        return []
    labels = _auto_labels(folder)
    gone: List[Path] = []
    for label in labels[: max(0, len(labels) - keep)]:
        for name in (f"mangalist-{label}.db", f"extension-host-{label}.db"):
            for suffix in ("", "-wal", "-shm"):
                f = folder / (name + suffix)
                if f.exists():
                    f.unlink()
                    gone.append(f)
    return gone


def at_version_change(db, data_dir: Path, version: str, *, now: Optional[datetime] = None) -> List[Path]:
    """When MangaList runs *version* for the first time on this data folder: an automatic pair (see the module
    docstring). Returns the files written ([] when the version is the one seen last). The first run ever only notes
    the version."""
    last = db.get_meta(META_LAST_VERSION)
    if last == version:
        return []
    db.set_meta(META_LAST_VERSION, version)
    if not last:
        return []
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    label = re.sub(r"[^A-Za-z0-9._-]", "_", f"{AUTO_PREFIX}{last}-to-{version}-{stamp}")[:80]
    try:
        written = snapshot(data_dir, label, db_file=getattr(db, "path", None))
    except (OSError, ValueError) as exc:
        _log.warning("Backup at the update to %s failed: %s", version, exc)
        return []
    prune_auto(data_dir)
    return written


def main(argv: Optional[Sequence[str]] = None) -> int:
    from .. import paths

    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: python -m mangalist.store.backups <label>   (e.g. pre-2026.10.57)", file=sys.stderr)
        return 2
    try:
        written = snapshot(paths.data_dir(), args[0], db_file=paths.db_file())
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for f in written:
        print(f)
    return 0


if __name__ == "__main__":
    sys.exit(main())

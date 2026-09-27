"""Centralised logging configuration for manga-list.

Writes rotating log files to the per-user log folder (``paths.log_dir()``).  Call ``setup()`` once at
application startup (from ``__main__.py``).  All other modules obtain loggers
via the standard ``logging.getLogger(__name__)`` pattern.

Rotation policy
---------------
- Up to 5 files of 2 MB each (10 MB total).
- UTF-8 encoded; delays creation until the first message is written.
- Console handler at WARNING level so the terminal stays quiet in normal use.
- File handler at DEBUG level so full details are always on disk.
"""

from __future__ import annotations

import logging
import logging.handlers

from . import paths


_FMT = "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s"
_DATE_FMT = "%Y-%m-%d %H:%M:%S"

_configured = False


def setup(level: int = logging.DEBUG) -> None:
    """Configure the root logger.  Safe to call multiple times (no-op after first)."""
    global _configured
    if _configured:
        return
    _configured = True

    log_dir = paths.log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(level)

    formatter = logging.Formatter(_FMT, datefmt=_DATE_FMT)

    # --- Rotating file handler (DEBUG and above) ---
    fh = logging.handlers.RotatingFileHandler(
        log_dir / "manga_list.log",
        maxBytes=2 * 1024 * 1024,   # 2 MB per file
        backupCount=5,
        encoding="utf-8",
        delay=True,
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(formatter)
    root.addHandler(fh)

    # --- Console handler (WARNING and above) ---
    ch = logging.StreamHandler()
    ch.setLevel(logging.WARNING)
    ch.setFormatter(formatter)
    root.addHandler(ch)


def get_logger(name: str) -> logging.Logger:
    """Convenience wrapper — identical to ``logging.getLogger(name)``."""
    return logging.getLogger(name)

"""Centralised logging configuration for MangaList.

Writes rotating log files to the per-user log folder (``paths.log_dir()``).  Call ``setup()`` once at
application startup (from ``__main__.py``; the headless runner calls it with its own file name).  All other
modules obtain loggers via the standard ``logging.getLogger(__name__)`` pattern.

Convention (owner, 2026-10-09: "good logging throughout the app including logging levels")
-----------------------------------------------------------------------------------------
- INFO    every user-visible action (a scan, a sync, a send, a filing, a setting that changes behaviour) and the
          outcome of every external call (MangaPixer, nyaa, qBittorrent, MangaUpdates): what was asked, what came
          back, how long it took where that matters.  One line per action, not one per file.
- WARNING a recoverable problem: the work goes on (a root that is not mounted, a service that did not answer and
          will be tried again, a value that was ignored).
- ERROR   a failure of the action itself; use ``_log.exception(...)`` inside an ``except`` so the traceback is kept.
- DEBUG   detail for finding a fault (per-file decisions, request paths, counts): never needed in normal use, and
          never the only record of something the owner would want to know.
- ``logging.getLogger(__name__)`` in every module (the logger name is what the per-area levels below match).
- NEVER log a token, a password, a cookie or an API key - not in a message, not in an exception's text, not at
  DEBUG.  Clients keep secrets in a wrapper that prints ``***``; as a second line of defence every handler here
  masks the usual shapes (``Authorization: Bearer ...``, ``token=...``, ``password=...``, ``cookie: ...``) in the
  finished line, so a mistake does not reach the file.

Levels and controls (Settings > Logging)
----------------------------------------
- The level of the log FILE is one setting for the desktop app and the headless runner (``log_level``): both read the
  same database, and "what do I want to see in the log" is one question; the two files are still separate
  (``mangalist.log``, ``headless.log``).  Default INFO.  ``MANGALIST_LOG_LEVEL`` only seeds it (used while nothing is
  stored), exactly like the schedule variables.
- Optional per-area levels (``log_areas``: area key -> level) override the general level for the loggers of one area
  (scanning, matching, MangaPixer, nyaa, qBittorrent, filing, duplicates, upgrades); an area is just a list of
  logger-name prefixes (``AREAS``), so a new module is covered by naming its logger ``mangalist.<package>...``.
- File size and number of rotated files kept (``log_max_mb`` / ``log_backups``).
- The root logger stays at DEBUG; the level is applied by a filter on the FILE handler, so the console behaves as
  before (the desktop app: WARNING and up; the runner: INFO and up on standard output) whatever the file level is,
  and a change applies at once with no restart (:func:`apply`).  The headless runner calls :func:`apply_stored` on
  every scheduler tick, so a change made in the GUI reaches it within a minute.
- ``urllib3`` stays at WARNING: at DEBUG it logs every request line of every HTTP call.

Rotation policy (defaults)
--------------------------
- Up to 5 rotated files of 2 MB each (the newest plus 5 backups).
- UTF-8 encoded; delays creation until the first message is written.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

from . import paths


_FMT = "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s"
_DATE_FMT = "%Y-%m-%d %H:%M:%S"

# --- the settings ------------------------------------------------------------------------------------------------

KEY_LEVEL = "log_level"
KEY_AREAS = "log_areas"
KEY_MAX_MB = "log_max_mb"
KEY_BACKUPS = "log_backups"
ENV_LEVEL = "MANGALIST_LOG_LEVEL"

#: The four levels the owner can pick, most important first (name -> ``logging`` level).
LEVELS: Dict[str, int] = {"error": logging.ERROR, "warning": logging.WARNING, "info": logging.INFO,
                          "debug": logging.DEBUG}
LEVEL_LABELS: Dict[str, str] = {"error": "Error", "warning": "Warning", "info": "Info", "debug": "Debug"}
DEFAULT_LEVEL = "info"
DEFAULT_MAX_MB = 2
DEFAULT_BACKUPS = 5
MAX_MB_RANGE = (1, 50)
BACKUPS_RANGE = (1, 20)

#: (key, label, logger-name prefixes): the areas that can have a level of their own.
AREAS: Tuple[Tuple[str, str, Tuple[str, ...]], ...] = (
    ("scan", "Scanning the library", ("mangalist.scanner", "mangalist.inventory", "mangalist.identity")),
    ("matching", "Matching (MangaUpdates, AniList)",
     ("mangalist.mu_match", "mangalist.mu_client", "mangalist.mu_cache", "mangalist.matcher",
      "mangalist.anilist_client", "mangalist.gui.mu_worker")),
    ("mangapixer", "MangaPixer", ("mangalist.services.mangapixer", "mangalist.identity.mangapixer")),
    ("nyaa", "nyaa", ("mangalist.services.nyaa",)),
    ("qbittorrent", "qBittorrent", ("mangalist.services.qbittorrent",)),
    ("filing", "Filing downloads", ("mangalist.downloads", "mangalist.headless.downloads_job",
                                    "mangalist.gui.downloads_backend", "mangalist.gui.volumes_controller")),
    ("duplicates", "Duplicates", ("mangalist.duplicates", "mangalist.gui.duplicates_view")),
    ("upgrades", "Upgrades (replaced chapters)", ("mangalist.upgrades", "mangalist.store.replacements")),
)
AREA_KEYS = tuple(key for key, _label, _prefixes in AREAS)


def parse_level(value: Any, default: Optional[str] = None) -> Optional[str]:
    """``'Debug'`` / ``'warn'`` / ``10`` -> the level's key (``'debug'``, ``'warning'``); *default* for anything else."""
    if isinstance(value, int) and not isinstance(value, bool):
        for key, number in LEVELS.items():
            if number == value:
                return key
        return default
    text = str(value or "").strip().lower()
    if text == "warn":
        text = "warning"
    return text if text in LEVELS else default


@dataclass(frozen=True)
class LogSettings:
    level: str = DEFAULT_LEVEL
    areas: Mapping[str, str] = field(default_factory=dict)      # area key -> level key; absent = follow ``level``
    max_mb: int = DEFAULT_MAX_MB
    backups: int = DEFAULT_BACKUPS

    def area_level(self, key: str) -> str:
        return self.areas.get(key, self.level)


def _clamp(value: Any, low_high: Tuple[int, int], default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low_high[0], min(low_high[1], number))


def normalized(settings: LogSettings) -> LogSettings:
    """*settings* with every field in range: an unknown level becomes the default, an unknown area is dropped."""
    areas = {k: lv for k, v in dict(settings.areas or {}).items()
             if k in AREA_KEYS and (lv := parse_level(v)) is not None}
    return LogSettings(parse_level(settings.level, DEFAULT_LEVEL) or DEFAULT_LEVEL, areas,
                       _clamp(settings.max_mb, MAX_MB_RANGE, DEFAULT_MAX_MB),
                       _clamp(settings.backups, BACKUPS_RANGE, DEFAULT_BACKUPS))


def load(db: Any, env: Optional[Mapping[str, str]] = None) -> LogSettings:
    """The stored settings; ``MANGALIST_LOG_LEVEL`` stands in for the level while none is stored (it only seeds, it
    never overrides what the owner chose in Settings); anything unusable falls back to the defaults."""
    env = os.environ if env is None else env
    seed = parse_level(env.get(ENV_LEVEL), DEFAULT_LEVEL) or DEFAULT_LEVEL
    stored_areas = db.get_setting(KEY_AREAS, {})
    return normalized(LogSettings(
        level=parse_level(db.get_setting(KEY_LEVEL), seed) or seed,
        areas=stored_areas if isinstance(stored_areas, dict) else {},
        max_mb=db.get_setting(KEY_MAX_MB, DEFAULT_MAX_MB),
        backups=db.get_setting(KEY_BACKUPS, DEFAULT_BACKUPS)))


def save(db: Any, settings: LogSettings) -> LogSettings:
    """Store *settings* (normalised) in the database; returns what was stored."""
    settings = normalized(settings)
    db.set_settings({KEY_LEVEL: settings.level, KEY_AREAS: dict(settings.areas), KEY_MAX_MB: settings.max_mb,
                     KEY_BACKUPS: settings.backups})
    return settings


# --- the handlers ------------------------------------------------------------------------------------------------

_SECRET_PATTERNS = (
    # Authorization: Bearer abc / Authorization=Basic abc / Authorization: abc
    (re.compile(r"(?i)\b(authorization|proxy-authorization)(\s*[:=]\s*)(?:(?:bearer|basic|token)\s+)?[^\s,;'\"]+"),
     r"\1\2***"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer ***"),
    # token=abc, password: abc, api_key=abc, 'cookie': 'SID=abc'
    (re.compile(r"(?i)\b(token|access[_-]?token|api[_-]?key|apikey|password|passwd|secret|cookie|set-cookie|sid)"
                r"(['\"]?\s*[:=]\s*['\"]?)[^&\s,;'\"}]+"), r"\1\2***"),
)


def redact(text: str) -> str:
    """*text* with the usual shapes of a secret masked (a safety net: the code never puts one in a message)."""
    for pattern, repl in _SECRET_PATTERNS:
        text = pattern.sub(repl, text)
    return text


class RedactingFormatter(logging.Formatter):
    """The log line (and any traceback in it) with secrets masked."""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


class LevelFilter(logging.Filter):
    """Applies the general level and the per-area levels to the records of one handler.

    The root logger stays at DEBUG, so records are created for everything; this filter keeps the ones the owner
    asked for. A logger takes the level of the area with the longest matching name prefix, else the general level.
    Replace the levels with :meth:`configure` (any thread; swapping the dict is atomic).
    """

    def __init__(self) -> None:
        super().__init__()
        self.general = LEVELS[DEFAULT_LEVEL]
        self._areas: List[Tuple[str, int]] = []
        self._cache: Dict[str, int] = {}

    def configure(self, settings: LogSettings) -> None:
        rules: List[Tuple[str, int]] = []
        for key, _label, prefixes in AREAS:
            level = settings.areas.get(key)
            if level is not None:
                rules.extend((prefix, LEVELS[level]) for prefix in prefixes)
        rules.sort(key=lambda r: len(r[0]), reverse=True)
        self.general = LEVELS[settings.level]
        self._areas = rules
        self._cache = {}

    def level_for(self, name: str) -> int:
        cache = self._cache
        level = cache.get(name)
        if level is None:
            level = self.general
            for prefix, area_level in self._areas:
                if name == prefix or name.startswith(prefix + "."):
                    level = area_level
                    break
            cache[name] = level
        return level

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno >= self.level_for(record.name)


_configured = False
_file_handler: Optional[logging.handlers.RotatingFileHandler] = None
_level_filter: Optional[LevelFilter] = None
_added: List[logging.Handler] = []
_applied: Optional[LogSettings] = None


def current() -> Optional[LogSettings]:
    """The settings in force (None before :func:`setup`)."""
    return _applied


def log_file() -> Optional[str]:
    """The path of the log file in use (None before :func:`setup`)."""
    return _file_handler.baseFilename if _file_handler is not None else None


def setup(level: Optional[Any] = None, *, filename: str = "mangalist.log", console_level: int = logging.WARNING,
          console_stream: Any = None) -> None:
    """Configure the root logger.  Safe to call multiple times (no-op after first).

    *level* is the file's general level (a key such as ``'debug'``, or a ``logging`` level); by default INFO, or
    ``MANGALIST_LOG_LEVEL``. The stored settings are applied later by :func:`apply_stored`, once the database is open.
    *filename* and the console parameters are for the headless runner (its own file, INFO on standard output).
    """
    global _configured, _file_handler, _level_filter, _applied
    if _configured:
        return
    _configured = True

    log_dir = paths.log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    formatter = RedactingFormatter(_FMT, datefmt=_DATE_FMT)

    seed = parse_level(level) if level is not None else None
    if seed is None:
        seed = parse_level(os.environ.get(ENV_LEVEL), DEFAULT_LEVEL) or DEFAULT_LEVEL
    settings = LogSettings(level=seed)

    # --- Rotating file handler (the owner's level) ---
    fh = logging.handlers.RotatingFileHandler(
        log_dir / filename,
        maxBytes=settings.max_mb * 1024 * 1024,
        backupCount=settings.backups,
        encoding="utf-8",
        delay=True,
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(formatter)
    _level_filter = LevelFilter()
    _level_filter.configure(settings)
    fh.addFilter(_level_filter)
    root.addHandler(fh)

    # --- Console handler (WARNING and above in the desktop app) ---
    ch = logging.StreamHandler(console_stream)
    ch.setLevel(console_level)
    ch.setFormatter(formatter)
    root.addHandler(ch)

    # A library that logs every HTTP request line stays quiet.
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    _file_handler = fh
    _added[:] = [fh, ch]
    _applied = settings


def apply(settings: LogSettings) -> LogSettings:
    """Put *settings* in force now (the level, the area levels, the rotation). Returns the normalised settings.
    Does nothing before :func:`setup`."""
    global _applied
    settings = normalized(settings)
    if _file_handler is None or _level_filter is None:
        return settings
    previous = _applied
    _level_filter.configure(settings)
    _file_handler.maxBytes = settings.max_mb * 1024 * 1024         # read by the handler at every write
    _file_handler.backupCount = settings.backups
    _applied = settings
    if previous is not None and previous != settings:
        logging.getLogger(__name__).info(
            "Logging changed: level %s%s; files of %d MB, %d kept", settings.level,
            "".join(f", {k} {v}" for k, v in sorted(settings.areas.items())), settings.max_mb, settings.backups)
    return settings


def apply_stored(db: Any = None) -> Optional[LogSettings]:
    """Read the settings from the database (the data folder's, when *db* is None) and :func:`apply` them.

    Never raises: logging must not stop the app, so a database that cannot be read keeps the level in force."""
    try:
        if db is None:
            from . import store

            db = store.get_store()
        return apply(load(db))
    except Exception:  # noqa: BLE001 - keep the current settings
        logging.getLogger(__name__).warning("Could not read the logging settings; keeping the current ones",
                                            exc_info=True)
        return None


def shutdown() -> None:
    """Remove and close the handlers :func:`setup` added, so the next call configures again (tests, mostly)."""
    global _configured, _file_handler, _level_filter, _applied
    root = logging.getLogger()
    for handler in _added:
        root.removeHandler(handler)
        handler.close()
    _added.clear()
    _configured = False
    _file_handler = None
    _level_filter = None
    _applied = None


def get_logger(name: str) -> logging.Logger:
    """Convenience wrapper — identical to ``logging.getLogger(name)``."""
    return logging.getLogger(name)


__all__ = ["AREAS", "AREA_KEYS", "BACKUPS_RANGE", "DEFAULT_BACKUPS", "DEFAULT_LEVEL", "DEFAULT_MAX_MB", "ENV_LEVEL",
           "LEVELS", "LEVEL_LABELS", "LevelFilter", "LogSettings", "MAX_MB_RANGE", "RedactingFormatter", "apply",
           "apply_stored", "current", "get_logger", "load", "log_file", "normalized", "parse_level", "redact", "save",
           "setup", "shutdown"]

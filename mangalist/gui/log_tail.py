"""Reading MangaList's log files for the log viewer (Qt-free, so it is tested without a display).

The files are the ones :mod:`mangalist.log_config` writes into ``paths.log_dir()``: ``mangalist.log`` (the app) and
``headless.log`` (the container's scheduled jobs), each with rotated copies beside it (``.1`` newest ... ``.N``).

- **Read-only, never locking.** Every read opens the file, reads, and closes it again; nothing here writes, truncates
  or renames a log. (Keeping a file open would stop the logging's rotation on Windows.)
- **Big files.** The first read takes only the last :data:`CHUNK_BYTES` of the file; :func:`read_older` reads the
  chunk before that on request, and goes on into the rotated copies once the file's start is reached.
- **Following.** :func:`read_new` reads what was appended since the last read (only whole lines: a line being written
  waits for the next poll). The file is told apart by its identity (device + inode), not by its name, so rotation is
  seen - the file shrinking, or replaced by a new one: the rest of the old file is read from its rotated copy, then
  the new file from its start, with a marker line between them.
- **Records.** A line in the log's format (``2026-10-10 12:00:00  INFO      mangalist.scanner  text``) starts a record;
  the lines after it that are not (a traceback) belong to it, so a filter keeps or hides them together.

Lines are shown as written: ``log_config``'s formatter already masks secrets in every line it writes.
"""

from __future__ import annotations

import functools
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from .. import log_config

_log = logging.getLogger(__name__)

#: (key, label, file name) of the logs the viewer can show.
LOGS: Tuple[Tuple[str, str, str], ...] = (
    ("app", "App", "mangalist.log"),
    ("jobs", "Scheduled jobs", "headless.log"),
)
LOG_FILE = {key: name for key, _label, name in LOGS}

CHUNK_BYTES = 4 * 1024 * 1024           # the first read, and each "load older"
MAX_POLL_BYTES = 8 * 1024 * 1024        # more appended than this since the last poll: only its end is read
MAX_BACKUPS = 50                        # rotated copies looked for (log_config allows 20)

MARKER_ROTATED = "--- the log file was rotated here: the lines below are in a new file ---"
MARKER_TRUNCATED = "--- the log file was emptied here ---"
MARKER_SKIPPED = "--- {size} of log lines skipped here (written faster than shown); Load older lines does not reach them ---"

HEADER = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+(DEBUG|INFO|WARNING|ERROR|CRITICAL)\s+(\S+)(?:\s+(.*))?$")
LEVEL_NUMBERS = {"DEBUG": logging.DEBUG, "INFO": logging.INFO, "WARNING": logging.WARNING, "ERROR": logging.ERROR,
                 "CRITICAL": logging.CRITICAL}

#: (key, label, lowest level shown) of the level filter.
LEVEL_FILTERS: Tuple[Tuple[str, str, int], ...] = (
    ("all", "Everything", 0),
    ("warnings", "Warnings and errors", logging.WARNING),
    ("errors", "Errors", logging.ERROR),
)

# --- areas: which part of the app a logger belongs to ---------------------------------------------------------

#: Short names for the areas of Settings > Logging (``log_config.AREAS``), which this filter extends.
_SHORT = {"scan": "Scanning", "matching": "Matching", "mangapixer": "MangaPixer", "nyaa": "nyaa",
          "qbittorrent": "qBittorrent", "filing": "Downloads", "duplicates": "Duplicates", "upgrades": "Upgrades"}
#: Further areas (logger-name prefix, label); the longest matching prefix wins.
_EXTRA: Tuple[Tuple[str, str], ...] = (
    ("mangalist.services.suwayomi", "Suwayomi"),
    ("mangalist.headless.mangapixer_job", "MangaPixer"),
    ("mangalist.mu_progress", "Matching"),
    ("mangalist.gui.download_tab", "Downloads"),
    ("mangalist.gui.downloads_dialog", "Downloads"),
    ("mangalist.gui.downloads_list", "Downloads"),
    ("mangalist.gui.chapters_panel", "Downloads"),
    ("mangalist.gui.releases_panel", "Downloads"),
    ("mangalist.gui.nyaa_dialog", "nyaa"),
    ("mangalist.gui.qbittorrent_dialog", "qBittorrent"),
    ("mangalist.gui.mangapixer_dialog", "MangaPixer"),
    ("mangalist.gui.replaced_chapters", "Upgrades"),
    ("mangalist.renamer", "Renamer"),
    ("mangalist.gui.renamer_view", "Renamer"),
    ("mangalist.store", "Library database"),
    ("mangalist.headless", "Scheduled jobs"),
    ("mangalist.log_config", "Logging"),
    ("mangalist.gui", "Window"),
)
OTHER_AREA = "Other"


def _area_rules() -> List[Tuple[str, str]]:
    rules = [(prefix, _SHORT.get(key, label)) for key, label, prefixes in log_config.AREAS for prefix in prefixes]
    rules.extend(_EXTRA)
    rules.sort(key=lambda rule: len(rule[0]), reverse=True)
    return rules


_AREA_RULES = _area_rules()
#: The order of the area filter (areas found in a log are listed in this order; any other at the end).
AREA_ORDER: Tuple[str, ...] = tuple(dict.fromkeys(
    [_SHORT.get(key, label) for key, label, _p in log_config.AREAS]
    + ["Suwayomi", "Renamer", "Library database", "Scheduled jobs", "Window", "Logging", OTHER_AREA]))


@functools.lru_cache(maxsize=1024)
def area_of(logger: str) -> str:
    """The area of a logger name (``mangalist.services.suwayomi.client`` -> ``Suwayomi``); ``Other`` when none."""
    for prefix, area in _AREA_RULES:
        if logger == prefix or logger.startswith(prefix + "."):
            return area
    return OTHER_AREA


def area_sort_key(area: str) -> Tuple[int, str]:
    return (AREA_ORDER.index(area), "") if area in AREA_ORDER else (len(AREA_ORDER), area.lower())


# --- records -----------------------------------------------------------------------------------------------------

@dataclass
class Record:
    """One log record: its first line and any lines that follow it (a traceback)."""

    lines: List[str]
    level: int = 0              # 0: not in the log's format (the cut start of a chunk, or a marker)
    logger: str = ""
    marker: bool = False        # a line the viewer adds (rotation, a skipped stretch); never in the file
    _lower: Optional[str] = field(default=None, repr=False, compare=False)

    @property
    def area(self) -> str:
        return area_of(self.logger) if self.logger else ""

    @property
    def headed(self) -> bool:
        return bool(self.logger)

    def lower(self) -> str:
        if self._lower is None or self._lower.count("\n") + 1 != len(self.lines):
            self._lower = "\n".join(self.lines).lower()
        return self._lower


def marker(text: str) -> Record:
    return Record([text], marker=True)


def parse(text: str) -> List[Record]:
    """The records of *text* (whole lines). Lines before the first record's header form a record of their own."""
    records: List[Record] = []
    if text.endswith("\n"):
        text = text[:-1]
    if not text:
        return records
    current: Optional[Record] = None
    for line in text.split("\n"):
        line = line.rstrip("\r")
        m = HEADER.match(line)
        if m is not None:
            current = Record([line], LEVEL_NUMBERS[m.group(2)], m.group(3))
            records.append(current)
        elif current is not None:
            current.lines.append(line)
        else:
            current = Record([line])
            records.append(current)
    return records


def joins(older: Record, newer: Record) -> bool:
    """*newer* continues *older* (a traceback cut by a chunk or a poll boundary)."""
    return not newer.headed and not newer.marker and not older.marker


def append_records(records: List[Record], new: Sequence[Record]) -> int:
    """Add *new* after *records* (in place). Returns how many lines went into the last existing record."""
    new = list(new)
    merged = 0
    if records and new and joins(records[-1], new[0]):
        merged = len(new[0].lines)
        records[-1].lines.extend(new.pop(0).lines)
    records.extend(new)
    return merged


def prepend_records(older: Sequence[Record], records: List[Record]) -> List[Record]:
    """*older* + *records*, the cut start of *records* joined to the record it belongs to."""
    older = list(older)
    if older and records and joins(older[-1], records[0]):
        older[-1].lines.extend(records[0].lines)
        return older + records[1:]
    return older + records


def visible(record: Record, min_level: int = 0, area: str = "", needle: str = "") -> bool:
    """Does *record* pass the filters (*needle* already lower-case)? A marker shows unless a search is typed."""
    if record.marker:
        return not needle
    if min_level and record.level < min_level:
        return False
    if area and record.area != area:
        return False
    return not needle or needle in record.lower()


def line_count(records: Sequence[Record]) -> int:
    return sum(len(r.lines) for r in records)


# --- sizes ------------------------------------------------------------------------------------------------------

def size_text(n: int) -> str:
    """``4194304`` -> ``4.0 MB`` (binary units, one decimal from KB up; integer arithmetic, no float)."""
    n = max(0, int(n))
    if n < 1024:
        return f"{n} B"
    for power, unit in ((1, "KB"), (2, "MB"), (3, "GB")):
        div = 1024 ** power
        tenths = (n * 10 * 2 + div) // (2 * div)            # rounded to a tenth
        if tenths < 10240 or unit == "GB":
            return f"{tenths // 10}.{tenths % 10} {unit}"
    return f"{n} B"     # unreachable


# --- reading ----------------------------------------------------------------------------------------------------

Ident = Tuple[int, int]


def ident_of(st: os.stat_result) -> Ident:
    return (st.st_dev, st.st_ino)


@dataclass
class Chunk:
    """Records read from one file, the byte range they came from and the file as it was read."""

    records: List[Record]
    start: int                  # offset of the first byte used
    end: int                    # offset just after the last whole line used
    size: int                   # the file's size at the read
    ident: Ident
    name: str = ""


def _whole_lines(data: bytes, start: int, *, cut_head: bool, keep_tail: bool) -> Tuple[bytes, int, int]:
    """*data* read from offset *start* trimmed to whole lines: (data, start, end). *cut_head*: the chunk starts
    inside the file, so its first (partial) line is dropped. *keep_tail*: a last line without a newline is kept
    (a rotated file is complete; a live one may be mid-write)."""
    if cut_head:
        nl = data.find(b"\n")
        if nl >= 0:
            data, start = data[nl + 1:], start + nl + 1
    if not keep_tail:
        nl = data.rfind(b"\n")
        data = data[:nl + 1] if nl >= 0 else b""
    return data, start, start + len(data)


def _decode(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


def _read_range(path: Path, start: int, stop: Optional[int]) -> Tuple[bytes, os.stat_result]:
    with open(path, "rb") as fh:                    # closed straight away: never hold a log open
        st = os.fstat(fh.fileno())
        stop = st.st_size if stop is None else min(stop, st.st_size)
        fh.seek(start)
        data = fh.read(max(0, stop - start))
    return data, st


def read_tail(path: Path, max_bytes: int = CHUNK_BYTES) -> Optional[Chunk]:
    """The last *max_bytes* of *path* (whole lines), or None when the file does not exist (yet)."""
    path = Path(path)
    try:
        with open(path, "rb") as fh:                # closed straight away: never hold a log open
            st = os.fstat(fh.fileno())
            start = max(0, st.st_size - max_bytes)
            fh.seek(start)
            data = fh.read(st.st_size - start)
    except FileNotFoundError:
        return None
    data, start, end = _whole_lines(data, start, cut_head=start > 0, keep_tail=False)
    _log.debug("Log viewer read %s: %d bytes from %d of %d", path.name, len(data), start, st.st_size)
    return Chunk(parse(_decode(data)), start, end, st.st_size, ident_of(st), path.name)


def rotated_files(path: Path) -> List[Tuple[Path, os.stat_result]]:
    """*path* and its rotated copies (``.1`` ... ) that exist, newest first; stops at the first missing copy."""
    path = Path(path)
    out: List[Tuple[Path, os.stat_result]] = []
    for i in range(MAX_BACKUPS + 1):
        candidate = path if i == 0 else path.with_name(f"{path.name}.{i}")
        try:
            out.append((candidate, os.stat(candidate)))
        except FileNotFoundError:
            if i > 0:
                break
    return out


def total_size(path: Path) -> int:
    """Bytes in *path* and all its rotated copies."""
    return sum(st.st_size for _p, st in rotated_files(path))


@dataclass
class Older:
    """The result of :func:`read_older`: the chunk read (None when there is nothing older) and whether still more is
    left; *gone* when the file the viewer had reached is not there any more (rotated away)."""

    chunk: Optional[Chunk]
    more: bool = False
    gone: bool = False


def _find(files: Sequence[Tuple[Path, os.stat_result]], ident: Ident) -> int:
    for i, (_p, st) in enumerate(files):
        if ident_of(st) == ident:
            return i
    return -1


def has_older(path: Path, ident: Ident, start: int) -> bool:
    """Is there anything before byte *start* of the file *ident* (in it, or in an older rotated copy)?"""
    if start > 0:
        return True
    files = rotated_files(path)
    i = _find(files, ident)
    return 0 <= i < len(files) - 1


def read_older(path: Path, ident: Ident, start: int, max_bytes: int = CHUNK_BYTES) -> Older:
    """The *max_bytes* before byte *start* of the file *ident* - which may since have been rotated to ``.1``, ``.2``
    ...; at its start, the end of the next older copy (with a marker after it: the newer file begins there)."""
    files = rotated_files(path)
    i = _find(files, ident)
    if i < 0:
        return Older(None, gone=True)
    tail: List[Record] = []
    if start <= 0:
        if i + 1 >= len(files):
            return Older(None)
        i, tail = i + 1, [marker(MARKER_ROTATED)]
        start = files[i][1].st_size
    file_path = files[i][0]
    begin = max(0, start - max_bytes)
    try:
        data, st = _read_range(file_path, begin, start)
    except FileNotFoundError:
        return Older(None, gone=True)
    if ident_of(st) != ident_of(files[i][1]):          # rotated between the listing and the read
        return Older(None, gone=True)
    data, begin, end = _whole_lines(data, begin, cut_head=begin > 0, keep_tail=True)
    chunk = Chunk(parse(_decode(data)) + tail, begin, end, st.st_size, ident_of(st), file_path.name)
    more = begin > 0 or i + 1 < len(files)
    _log.debug("Log viewer read older lines of %s: %d bytes from %d", file_path.name, len(data), begin)
    return Older(chunk, more=more)


@dataclass
class Update:
    """What :func:`read_new` found: the records appended (markers included), where the next poll starts, and the
    file's identity and size now. *missing*: the file is not there (not written yet, or between rotations)."""

    records: List[Record]
    end: int
    size: int
    ident: Optional[Ident]
    missing: bool = False
    rotated: bool = False


def read_new(path: Path, ident: Optional[Ident], offset: int, max_bytes: int = MAX_POLL_BYTES) -> Update:
    """The whole lines appended to *path* after byte *offset* of the file *ident* (None: nothing read yet - the file
    is read from its start). Rotation (another file under the name) and truncation (the same file, shorter) are
    handled as described in the module docstring. At most *max_bytes* of new text are read: past that, the start of
    the new stretch is skipped with a marker."""
    path = Path(path)
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return Update([], offset, 0, ident, missing=True)
    records: List[Record] = []
    rotated = False
    if ident is not None and ident_of(st) != ident:
        rotated = True
        for old_path, old_st in rotated_files(path)[1:]:             # the rest of the file we were following
            if ident_of(old_st) == ident:
                try:
                    data, _st = _read_range(old_path, offset, None)
                except FileNotFoundError:
                    break
                data, _b, _e = _whole_lines(data, offset, cut_head=False, keep_tail=True)
                records.extend(parse(_decode(data)))
                break
        records.append(marker(MARKER_ROTATED))
        offset = 0
    elif ident is not None and st.st_size < offset:
        rotated = True
        records.append(marker(MARKER_TRUNCATED))
        offset = 0
    elif ident is None:
        offset = 0
    begin = offset
    if st.st_size - offset > max_bytes:
        begin = st.st_size - max_bytes
        records.append(marker(MARKER_SKIPPED.format(size=size_text(begin - offset))))
    try:
        data, st = _read_range(path, begin, None)
    except FileNotFoundError:
        return Update(records, offset, 0, ident, missing=True, rotated=rotated)
    data, begin, end = _whole_lines(data, begin, cut_head=begin > offset, keep_tail=False)
    records.extend(parse(_decode(data)))
    if rotated:
        _log.info("Log viewer: %s was rotated or emptied; following the new file", path.name)
    return Update(records, end, st.st_size, ident_of(st), rotated=rotated)


__all__ = ["AREA_ORDER", "CHUNK_BYTES", "Chunk", "LEVEL_FILTERS", "LOGS", "LOG_FILE", "MARKER_ROTATED",
           "MARKER_SKIPPED", "MARKER_TRUNCATED", "MAX_POLL_BYTES", "OTHER_AREA", "Older", "Record", "Update",
           "append_records", "area_of", "area_sort_key", "has_older", "line_count", "marker", "parse",
           "prepend_records", "read_new", "read_older", "read_tail", "rotated_files", "size_text", "total_size",
           "visible"]

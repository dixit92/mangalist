"""The log viewer's reading of the log files (Qt-free): the tail, older chunks and rotated copies, following through
rotation and truncation, records with their tracebacks, the filters. Synthetic logs in temporary folders only."""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path

import pytest

from mangalist import log_config
from mangalist.gui import log_tail as lt


def line(n: int, level: str = "INFO", logger: str = "mangalist.scanner", text: str = "") -> str:
    """One line in the log's format (``log_config._FMT``), numbered so order and loss can be checked."""
    return f"2026-10-10 12:{n // 60 % 60:02d}:{n % 60:02d}  {level:<8}  {logger}  {text or f'line {n:06d}'}"


def write(path: Path, lines, mode: str = "w") -> None:
    with open(path, mode, encoding="utf-8", newline="\n") as fh:
        fh.write("".join(f"{x}\n" for x in lines))


def texts(records):
    return [x for r in records for x in r.lines]


# --- parsing --------------------------------------------------------------------------------------------------

def test_a_record_keeps_its_traceback_and_level_and_logger():
    text = "\n".join([line(1), line(2, "ERROR", "mangalist.services.nyaa.client", "search failed"),
                      "Traceback (most recent call last):", '  File "x.py", line 1', "OSError: boom",
                      line(3, "WARNING", "mangalist.services.suwayomi", "slow")]) + "\n"
    records = lt.parse(text)
    assert [len(r.lines) for r in records] == [1, 4, 1]
    assert [(r.level, r.logger, r.area) for r in records] == [
        (logging.INFO, "mangalist.scanner", "Scanning"), (logging.ERROR, "mangalist.services.nyaa.client", "nyaa"),
        (logging.WARNING, "mangalist.services.suwayomi", "Suwayomi")]
    assert records[1].lines[-1] == "OSError: boom"


def test_lines_before_the_first_header_and_windows_line_ends():
    records = lt.parse("  File \"cut.py\", line 9\r\nValueError: x\r\n" + line(1) + "\r\n")
    assert [r.headed for r in records] == [False, True]
    assert records[0].lines == ['  File "cut.py", line 9', "ValueError: x"] and records[0].level == 0
    assert records[1].lines == [line(1)]
    assert lt.parse("") == [] and lt.parse("\n") == []


def test_a_line_from_the_real_formatter_is_understood(tmp_path):
    handler = logging.FileHandler(tmp_path / "x.log", encoding="utf-8")
    handler.setFormatter(log_config.RedactingFormatter(log_config._FMT, datefmt=log_config._DATE_FMT))
    log = logging.getLogger("mangalist.renamer.test_log_tail")
    log.addHandler(handler)
    log.propagate = False
    log.setLevel(logging.INFO)
    try:
        log.warning("renamed %d files", 3)
        try:
            raise ValueError("bad name")
        except ValueError:
            log.exception("could not rename")
        log.critical("stop")
    finally:
        log.removeHandler(handler)
        handler.close()
    records = lt.parse((tmp_path / "x.log").read_text(encoding="utf-8"))
    assert [(r.level, r.area) for r in records] == [(logging.WARNING, "Renamer"), (logging.ERROR, "Renamer"),
                                                    (logging.CRITICAL, "Renamer")]
    assert records[1].lines[-1] == "ValueError: bad name" and len(records[1].lines) > 2


@pytest.mark.parametrize("logger, area", [
    ("mangalist.scanner", "Scanning"), ("mangalist.identity.mangapixer", "MangaPixer"),
    ("mangalist.identity.folders", "Scanning"), ("mangalist.services.qbittorrent.client", "qBittorrent"),
    ("mangalist.services.suwayomi.graphql", "Suwayomi"), ("mangalist.downloads.finding", "Downloads"),
    ("mangalist.gui.downloads_backend", "Downloads"), ("mangalist.gui.download_tab", "Downloads"),
    ("mangalist.headless.downloads_job", "Downloads"), ("mangalist.headless.mangapixer_job", "MangaPixer"),
    ("mangalist.headless", "Scheduled jobs"), ("mangalist.headless.scheduler", "Scheduled jobs"),
    ("mangalist.renamer", "Renamer"), ("mangalist.store.journal", "Library database"),
    ("mangalist.store.replacements", "Upgrades"), ("mangalist.gui.list_tab", "Window"),
    ("mangalist.mu_client", "Matching"), ("urllib3.connectionpool", "Other"), ("root", "Other"),
    ("mangalist.scannerx", "Other"),
])
def test_the_area_of_a_logger(logger, area):
    assert lt.area_of(logger) == area


def test_every_settings_area_has_a_place_in_the_filter():
    for key, _label, prefixes in log_config.AREAS:
        for prefix in prefixes:
            assert lt.area_of(prefix) in lt.AREA_ORDER
    assert sorted(["Other", "Window", "Scanning", "zzz"], key=lt.area_sort_key) == ["Scanning", "Window", "Other",
                                                                                   "zzz"]


def test_filters_keep_a_record_whole_and_markers_show_unless_searching():
    records = lt.parse("\n".join([line(1, "DEBUG"), line(2, "INFO", "mangalist.renamer"),
                                  line(3, "WARNING", "mangalist.services.nyaa"), "  detail",
                                  line(4, "ERROR", "mangalist.renamer", "Disk FULL"), "Traceback"]))
    records.append(lt.marker(lt.MARKER_ROTATED))
    shown = lambda *a: [r.lines[0] for r in records if lt.visible(r, *a)]  # noqa: E731
    assert len(shown()) == 5
    assert shown(logging.WARNING) == [line(3, "WARNING", "mangalist.services.nyaa"),
                                      line(4, "ERROR", "mangalist.renamer", "Disk FULL"), lt.MARKER_ROTATED]
    assert shown(logging.ERROR, "Renamer") == [line(4, "ERROR", "mangalist.renamer", "Disk FULL"), lt.MARKER_ROTATED]
    assert shown(0, "", "traceback") == [line(4, "ERROR", "mangalist.renamer", "Disk FULL")], "search sees the traceback"
    assert shown(0, "", "disk full") and not shown(0, "Scanning", "disk full")


def test_append_and_prepend_join_a_cut_record():
    records = lt.parse(line(1) + "\n" + line(2, "ERROR") + "\nTraceback\n")
    merged = lt.append_records(records, lt.parse("  more\n  and more\n" + line(3) + "\n"))
    assert merged == 2 and texts(records)[-3:] == ["  more", "  and more", line(3)]
    assert len(records[1].lines) == 4
    records[1].lines.append("x")
    assert "x" in records[1].lower(), "the search text follows appended lines"
    after_marker = [lt.marker("m")]
    assert lt.append_records(after_marker, lt.parse("cut\n")) == 0 and len(after_marker) == 2
    joined = lt.prepend_records(lt.parse(line(0, "ERROR") + "\nTraceback\n"), lt.parse("  cut tail\n" + line(1)))
    assert [len(r.lines) for r in joined] == [3, 1]


def test_size_text_uses_whole_numbers():
    assert [lt.size_text(n) for n in (0, 1023, 1024, 1536, 4 * 1024 ** 2, 50 * 1024 ** 2 + 1, 1024 ** 2 - 1,
                                      3 * 1024 ** 3)] == [
        "0 B", "1023 B", "1.0 KB", "1.5 KB", "4.0 MB", "50.0 MB", "1.0 MB", "3.0 GB"]


# --- reading ----------------------------------------------------------------------------------------------------

def test_the_tail_of_a_missing_small_and_big_file(tmp_path):
    path = tmp_path / "mangalist.log"
    assert lt.read_tail(path) is None
    write(path, [line(i) for i in range(10)])
    chunk = lt.read_tail(path)
    assert texts(chunk.records) == [line(i) for i in range(10)] and chunk.start == 0
    assert chunk.end == chunk.size == path.stat().st_size and chunk.name == "mangalist.log"
    write(path, [line(i) for i in range(10, 2000)], "a")
    chunk = lt.read_tail(path, 5000)
    got = texts(chunk.records)
    assert 0 < chunk.start and chunk.size - chunk.start <= 5000 and got[-1] == line(1999)
    assert all(t.startswith("2026-10-10") for t in got), "the cut first line is dropped"
    assert path.read_bytes()[chunk.start - 1:chunk.start] == b"\n"


def test_a_line_being_written_waits_for_the_next_read(tmp_path):
    path = tmp_path / "mangalist.log"
    write(path, [line(1)])
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line(2)[:20])
    chunk = lt.read_tail(path)
    assert texts(chunk.records) == [line(1)] and chunk.end == len(line(1)) + 1
    assert lt.read_new(path, chunk.ident, chunk.end).records == []
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line(2)[20:] + "\n" + line(3) + "\n")
    update = lt.read_new(path, chunk.ident, chunk.end)
    assert texts(update.records) == [line(2), line(3)] and update.end == path.stat().st_size
    assert not update.rotated and update.ident == chunk.ident


def test_following_a_file_that_does_not_exist_yet(tmp_path):
    path = tmp_path / "headless.log"
    update = lt.read_new(path, None, 0)
    assert update.missing and update.records == []
    write(path, [line(1), line(2)])
    update = lt.read_new(path, None, 0)
    assert texts(update.records) == [line(1), line(2)] and not update.missing and not update.rotated


def test_an_emptied_file_is_read_again_from_its_start(tmp_path):
    path = tmp_path / "mangalist.log"
    write(path, [line(i) for i in range(50)])
    chunk = lt.read_tail(path)
    write(path, [line(100)])                                       # same file, shorter
    update = lt.read_new(path, chunk.ident, chunk.end)
    assert update.rotated and [r.marker for r in update.records] == [True, False]
    assert update.records[0].lines == [lt.MARKER_TRUNCATED] and texts(update.records[1:]) == [line(100)]


def test_rotation_by_the_real_handler_loses_and_doubles_nothing(tmp_path):
    path = tmp_path / "mangalist.log"
    handler = logging.handlers.RotatingFileHandler(path, maxBytes=3000, backupCount=3, encoding="utf-8")
    handler.setFormatter(log_config.RedactingFormatter(log_config._FMT, datefmt=log_config._DATE_FMT))
    log = logging.getLogger("mangalist.scanner.test_rotation")
    log.addHandler(handler)
    log.propagate = False
    log.setLevel(logging.INFO)
    seen = []
    ident, end = None, 0
    try:
        for i in range(400):
            log.info("message %04d", i)
            if i % 7 == 0:                                         # polls between writes, several rotations apart
                update = lt.read_new(path, ident, end)
                seen.extend(t for r in update.records if not r.marker for t in r.lines)
                ident, end = update.ident, update.end
        update = lt.read_new(path, ident, end)
        seen.extend(t for r in update.records if not r.marker for t in r.lines)
    finally:
        log.removeHandler(handler)
        handler.close()
    numbers = [int(t.rsplit(" ", 1)[1]) for t in seen]
    first = numbers[0]
    assert numbers == list(range(first, 400)), "every line once, in order, across the rotations"
    assert len(lt.rotated_files(path)) == 4


def test_a_burst_bigger_than_a_poll_skips_its_start_with_a_marker(tmp_path):
    path = tmp_path / "mangalist.log"
    write(path, [line(0)])
    chunk = lt.read_tail(path)
    write(path, [line(i) for i in range(1, 3000)], "a")
    update = lt.read_new(path, chunk.ident, chunk.end, max_bytes=10_000)
    assert update.records[0].marker and "skipped" in update.records[0].lines[0]
    rest = texts(update.records[1:])
    assert rest[-1] == line(2999) and all(t.startswith("2026") for t in rest) and len(rest) < 200
    assert update.end == path.stat().st_size


def test_loading_older_goes_back_through_the_file_and_into_its_rotated_copies(tmp_path):
    path = tmp_path / "mangalist.log"
    write(path.with_name("mangalist.log.2"), [line(i) for i in range(0, 100)])
    write(path.with_name("mangalist.log.1"), [line(i) for i in range(100, 200)])
    write(path, [line(i) for i in range(200, 300)])
    chunk = lt.read_tail(path, 3000)
    records = chunk.records
    ident, start = chunk.ident, chunk.start
    assert lt.has_older(path, ident, start)
    markers = 0
    while True:
        older = lt.read_older(path, ident, start, 3000)
        if older.chunk is None:
            break
        markers += sum(r.marker for r in older.chunk.records)
        records = lt.prepend_records(older.chunk.records, records)
        ident, start = older.chunk.ident, older.chunk.start
        assert older.more == lt.has_older(path, ident, start)
    assert not older.gone and markers == 2
    assert [t for r in records if not r.marker for t in r.lines] == [line(i) for i in range(300)]
    assert [i for i, r in enumerate(records) if r.marker] == [100, 201]


def test_older_lines_of_a_file_rotated_away_are_gone(tmp_path):
    path = tmp_path / "mangalist.log"
    write(path, [line(i) for i in range(200)])
    chunk = lt.read_tail(path, 2000)
    os.rename(path, path.with_name("mangalist.log.1"))             # rotated while shown: still found by identity
    write(path, [line(500)])
    older = lt.read_older(path, chunk.ident, chunk.start, 2000)
    assert older.chunk is not None and older.chunk.name == "mangalist.log.1" and older.more
    os.remove(path.with_name("mangalist.log.1"))
    assert lt.read_older(path, chunk.ident, chunk.start).gone
    assert not lt.has_older(path, chunk.ident, chunk.start - chunk.start)


def test_reading_never_changes_or_holds_a_log(tmp_path):
    path = tmp_path / "mangalist.log"
    write(path.with_name("mangalist.log.1"), [line(i) for i in range(100)])
    write(path, [line(i) for i in range(100, 300)])
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.iterdir()}
    fds = len(os.listdir("/proc/self/fd")) if sys.platform.startswith("linux") else None
    chunk = lt.read_tail(path, 2000)
    lt.read_new(path, chunk.ident, chunk.end)
    lt.read_older(path, chunk.ident, chunk.start, 2000)
    lt.read_older(path, chunk.ident, 0, 2000)
    assert {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.iterdir()} == before
    if fds is not None:
        assert len(os.listdir("/proc/self/fd")) == fds, "no file left open"


def test_a_broken_byte_does_not_stop_the_reading(tmp_path):
    path = tmp_path / "mangalist.log"
    path.write_bytes(line(1).encode() + b"\n" + line(2, text="caf\xe9 \xff").encode("latin-1") + b"\n")
    chunk = lt.read_tail(path)
    assert len(chunk.records) == 2 and "�" in chunk.records[1].lines[0]

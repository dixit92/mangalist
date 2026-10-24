"""The built-in extensions backend end to end, through MangaList's REAL pieces (shared by the fake-host case that runs in
CI and the opt-in live cases against lane H's host).

One run (:func:`download_one_chapter`): a library root with one series folder (scanned into a fresh database) ->
"Built-in extensions" chosen in the GUI backend (:class:`mangalist.downloads.adapter.Backend`), the host's address saved
and tested (MangaList learns the host's downloads folder) -> the chapters panel's lookup on the allowed source (a title
search whose candidate the owner confirms, as in the panel) -> the panel's Send (``Backend.send_chapters`` ->
:func:`mangalist.downloads.chapters.send_chapters`: a ledger record, the chapter enqueued on the host) -> Check now (the
real downloads job and its chapter arrivals pass) until the record is REMOVED -> the result: the filed CBZ in the series
folder, named by MangaList's scheme, the host's copy gone.
"""

from __future__ import annotations

import os
import time
import zipfile
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from mangalist import naming
from mangalist.downloads.adapter import Backend
from mangalist.downloads.chapter_arrivals import expected_path, read_comicinfo
from mangalist.downloads.chapters import MangaMatch, split_chapter_name
from mangalist.downloads.contracts import CHAPTER_BACKEND_EXTENSIONS, DownloadStatus
from mangalist.scanner import record_library_scan, scan_library
from mangalist.store.downloads import ExtensionHostConfig


@dataclass
class Outcome:
    record_status: str
    filed: Path                         # the CBZ in the series folder
    expected_name: str                  # MangaList's scheme name for it
    host_copy: str                      # where the host had written it
    pages: List[str] = field(default_factory=list)
    comicinfo_number: Optional[str] = None
    checks: List[str] = field(default_factory=list)     # what each Check now said
    seconds: float = 0.0


def library_with_series(db, tmp_path: Path, series: str) -> int:
    """A library root holding one (empty) series folder, scanned; the series' id."""
    root = tmp_path / "library" / "Manga"
    (root / series).mkdir(parents=True)
    (root / series / "cover.jpg").write_bytes(b"\xff\xd8\xff")      # a folder MangaList keeps as a series
    r = db.add_root(str(root), "Manga")
    record_library_scan(db, scan_library(db.list_roots(), db=db))
    row = db.get_series(r.id, series)
    assert row is not None, "the scan did not record the series folder"
    return row.id


def choose_builtin(backend: Backend, host_url: str, download_dir: str = "", flaresolverr: str = "") -> None:
    backend.set_chapter_backend(CHAPTER_BACKEND_EXTENSIONS)
    backend.save_extension_host(ExtensionHostConfig(url=host_url, download_dir=download_dir,
                                                    flaresolverr_url=flaresolverr))


def download_one_chapter(db, backend: Backend, series_id: int, *, source_id: str, titles: Sequence[str],
                         number: str, pick_candidate: Callable[[MangaMatch], bool] = lambda m: True,
                         group: Optional[str] = None, timeout: float = 600.0, pause: float = 0.2) -> Outcome:
    """Find, send, download and file chapter *number* of the series (see the module docstring)."""
    check = backend.test_extension_host()
    assert check.downloads_path, "the host did not say where it downloads to"
    backend.set_suwayomi_sources([source_id])
    lookup = backend.chapter_lookup(series_id, (number,), tuple(titles))
    assert lookup.error is None, lookup.error
    if lookup.match is None:
        picked = [c for c in lookup.candidates if c.source.id == source_id and pick_candidate(c)]
        assert picked, f"no candidate on source {source_id}: {[(c.source.id, c.manga.title) for c in lookup.candidates]}"
        backend.confirm_match(series_id, picked[0])           # the owner confirms the title match
        lookup = backend.chapter_lookup(series_id, (number,), tuple(titles))
        assert lookup.error is None, lookup.error
    assert lookup.match is not None and lookup.match.confirmed
    rows = [r for r in lookup.rows if r.number == number]
    assert rows, f"chapter {number} is not offered: {[r.number for r in lookup.rows]} ({lookup.notes})"
    pick = rows[0].default
    if group is not None:
        pick = next(c for c in rows[0].options if (c.scanlator or "") == group)
    assert pick is not None, f"the source does not have chapter {number}"
    assert lookup.placement is not None and lookup.placement.target_dir
    outcome = backend.send_chapters(series_id, lookup.match, [pick], lookup.placement.target_dir, lookup.manga_title,
                                    lookup.source_name)
    assert outcome.error is None, outcome.error
    (record,) = outcome.records
    assert record.status == DownloadStatus.SENT
    host_copy = expected_path(check.files_from or check.downloads_path, lookup.source_name, lookup.manga_title,
                              pick.name, pick.scanlator)
    started = time.monotonic()
    said: List[str] = []
    while True:
        said.append(backend.check_now())
        rec = backend.chapter_ledger.get(record.id)
        if rec.status in (DownloadStatus.REMOVED, DownloadStatus.FAILED, DownloadStatus.CANCELLED):
            break
        assert time.monotonic() - started < timeout, f"not filed in {timeout:.0f} s: {rec.status} {rec.error}; {said}"
        time.sleep(pause)
    assert rec.status == DownloadStatus.REMOVED, f"{rec.status}: {rec.error}; {said[-3:]}"
    assert rec.filed_files, rec
    filed = Path(lookup.placement.target_dir) / rec.filed_files[0]
    stated_volume, title = split_chapter_name(pick.name)
    expected = naming.chapter_file_name(Decimal(number), volume=Decimal(stated_volume) if stated_volume else None,
                                        title=title, group=pick.scanlator or None, ext=".cbz",
                                        folder=lookup.placement.target_dir)
    with zipfile.ZipFile(filed) as zf:
        pages = sorted(n for n in zf.namelist() if not n.lower().endswith(".xml"))
    info = read_comicinfo(str(filed))
    return Outcome(record_status=rec.status, filed=filed, expected_name=expected, host_copy=host_copy, pages=pages,
                   comicinfo_number=info.number if info else None, checks=said, seconds=time.monotonic() - started)


def assert_filed(outcome: Outcome, number: str) -> None:
    """What every case asserts: filed in the series folder under the scheme's name, pages and ComicInfo inside, the
    host's copy deleted."""
    assert outcome.filed.is_file(), outcome.filed
    assert outcome.filed.name == outcome.expected_name
    assert outcome.pages and all(p[:3].isdigit() for p in outcome.pages), outcome.pages
    assert outcome.comicinfo_number == number
    assert not os.path.exists(outcome.host_copy), f"the host's copy is still there: {outcome.host_copy}"

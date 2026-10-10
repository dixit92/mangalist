"""The chapter arrivals pass: chapters Suwayomi finished -> the series folder, named by MangaList's scheme. No Qt here.

One pass (:func:`run_chapter_arrivals`; the hourly ``downloads`` job and Check now) is idempotent. It asks Suwayomi for its
download queue, then for the chapters of every active chapter record (in that order: a chapter finishing in between is
seen as downloaded, never as lost), and for each record:

- **SENT** + Suwayomi has the chapter downloaded -> DOWNLOADED (and on to filing in the same pass). Still queued or
  downloading -> waits; Suwayomi's ``ERROR`` state -> waits with the reason on the record (Suwayomi retries; the owner can
  retry it there). Neither downloaded nor in the queue (removed in Suwayomi) -> FAILED. A chapter Suwayomi no longer
  knows -> FAILED.
- **DOWNLOADED** -> filing: the CBZ is found in the download folder MangaList reads (Settings > Connected services >
  Suwayomi) by Suwayomi's own path rule - ``<downloads>/mangas/<source>/<manga title>/<scanlator>_<chapter name>.cbz``,
  each part cleaned as Suwayomi cleans it (:func:`suwayomi_name`) - else by a look through that manga's folders for the CBZ
  whose ComicInfo names the chapter's URL. Its ``ComicInfo.xml`` is read: ``Number`` must be the record's chapter (a CBZ
  of another chapter is never filed), ``Title`` gives the chapter title (``Vol.3 Ch.102 - <title>`` -> ``<title>``, and the
  volume when MangaPixer's volume list does not know one), ``Translator`` the group. The new name comes from the injected
  namer (:class:`LazyNaming`: ``mangalist.naming.chapter_file_name`` with the volume from ``volume_lookup``), in the
  record's target folder. The file goes in through ONE journal plan (:meth:`~mangalist.store.journal.Journal.plan_links`:
  write-ahead, a hard link where the folders share a filesystem, else a copy verified by size and MangaPixer's content
  signature; never over an existing name) -> FILED. The plan id is stored on the record before the plan is applied, so a
  crash in between is settled by the next pass. Refused (nothing moved, Suwayomi's copy left alone) and FAILED when: the
  series or target folder is gone or no longer inside a root; no CBZ is found (or Suwayomi saved a folder of images: "Download
  as CBZ" is off); the CBZ is another chapter; a file of that name, or a file of that chapter, is already in the folder.
- **FILED** -> the move is completed: once the library file is verified (present at its filed size) Suwayomi is asked to
  delete its downloaded copy (``deleteDownloadedChapters``: Suwayomi removes the file and its now-empty folders and marks
  the chapter not downloaded) -> REMOVED. The library file survives it (a hard link keeps the data; a copy is its own).
  Never when Suwayomi's download folder overlaps a library root. Suwayomi not answering -> stays FILED with the reason,
  retried next pass.

A file is never lost: the library name is made before Suwayomi's copy goes, and only after it is verified. The filed
records are reported (``report.filed``) so the job records a rescan and asks MangaPixer to scan, as for volumes.
"""

from __future__ import annotations

import importlib
import logging
import os
import posixpath
import zipfile
from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple
from xml.etree import ElementTree

from ..scanner import ARCHIVE_EXTS
from ..store.downloads import DownloadLedger, StatusConflict
from .arrivals import ArrivalsReport
from .chapters import split_chapter_name, to_decimal
from .contracts import ChapterClient, DownloadRecord, DownloadStatus, QueuedChapter, SuwayomiChapter
from .placement import locate_series, same_or_inside

_log = logging.getLogger(__name__)

PLAN_REASON = "chapter arrival"
MAX_COMICINFO_BYTES = 1 << 20
MAX_NAME_BYTES = 240            # Suwayomi (Mihon's DiskUtil.buildValidFilename) cuts a name to 240 bytes
_INVALID = set('"*/:<>?\\|\x7f')


# --- the namer (lane A's mangalist.naming, injected) --------------------------------------------------------------

class LazyNaming:
    """The naming scheme of ``mangalist.naming`` (imported when first used, so this module never needs it to import)."""

    module = "mangalist.naming"

    def _naming(self):
        return importlib.import_module(self.module)

    def chapter_file_name(self, chapter: Decimal, **kwargs) -> str:
        return self._naming().chapter_file_name(chapter, **kwargs)

    def volume_lookup(self, db, series_id: int) -> Callable[[Decimal], Optional[Decimal]]:
        return self._naming().volume_lookup(db, series_id)


# --- Suwayomi's download folder -----------------------------------------------------------------------------------

def suwayomi_name(name: str) -> str:
    """A name as Suwayomi writes it on disk (Mihon's ``DiskUtil.buildValidFilename``): dots and spaces trimmed from
    both ends, control characters and ``" * / : < > ? \\ |`` replaced by ``_``, cut to 240 bytes of UTF-8; ``(invalid)``
    when nothing is left."""
    text = (name or "").strip(". ")
    if not text:
        return "(invalid)"
    clean = "".join("_" if (ord(c) < 0x20 or c in _INVALID) else c for c in text)
    data = clean.encode("utf-8")
    if len(data) > MAX_NAME_BYTES:
        clean = data[:MAX_NAME_BYTES].decode("utf-8", errors="ignore")
    return clean


def chapter_dir_name(chapter_name: str, scanlator: Optional[str]) -> str:
    """Suwayomi's name for a chapter (``<scanlator>_<chapter name>``, or the chapter name alone without a group)."""
    group = (scanlator or "").strip()
    return suwayomi_name(f"{group}_{chapter_name}" if group else chapter_name)


def expected_path(download_dir: str, source_name: str, manga_title: str, chapter_name: str,
                  scanlator: Optional[str]) -> str:
    return os.path.join(download_dir, "mangas", suwayomi_name(source_name), suwayomi_name(manga_title),
                        chapter_dir_name(chapter_name, scanlator) + ".cbz")


@dataclass(frozen=True)
class ComicInfo:
    number: Optional[str] = None        # exact
    title: Optional[str] = None
    series: Optional[str] = None
    translator: Optional[str] = None
    web: Optional[str] = None


def read_comicinfo(path: str) -> Optional[ComicInfo]:
    """The CBZ's ``ComicInfo.xml`` (None when it has none, or it cannot be read)."""
    try:
        with zipfile.ZipFile(path) as zf:
            entry = next((i for i in zf.infolist() if posixpath.basename(i.filename).lower() == "comicinfo.xml"), None)
            if entry is None or entry.file_size > MAX_COMICINFO_BYTES:
                return None
            data = zf.read(entry)
        root = ElementTree.fromstring(data)
    except (OSError, zipfile.BadZipFile, ElementTree.ParseError, RuntimeError, ValueError):
        return None

    def text(tag: str) -> Optional[str]:
        el = root.find(tag)
        value = (el.text or "").strip() if el is not None and el.text else ""
        return value or None

    number = text("Number")
    exact = None
    if number is not None:
        d = to_decimal(number)
        exact = (str(int(d)) if d == d.to_integral_value() else format(d.normalize(), "f")) if d is not None else None
    return ComicInfo(number=exact, title=text("Title"), series=text("Series"), translator=text("Translator"),
                     web=text("Web"))


def find_cbz(download_dir: str, request: Mapping[str, object]) -> Tuple[Optional[str], str]:
    """(the chapter's CBZ, '') or (None, why not). The path rule first, then the chapter's URL in the ComicInfo of the
    CBZs in the manga's folders (Suwayomi's manga title may have changed since the send)."""
    name = str(request.get("chapter_name") or "")
    scanlator = str(request.get("scanlator") or "") or None
    source = str(request.get("source_name") or "")
    title = str(request.get("manga_title") or "")
    path = expected_path(download_dir, source, title, name, scanlator)
    if os.path.isfile(path):
        return path, ""
    if os.path.isdir(path[: -len(".cbz")]):
        return None, ("Suwayomi saved this chapter as a folder of images, not a CBZ: turn on \"Download as CBZ\" in "
                      "Suwayomi's settings, then delete the chapter there and send it again")
    mangas = os.path.join(download_dir, "mangas")
    if not os.path.isdir(mangas):
        return None, (f"Suwayomi's download folder was not found at {download_dir} (no \"mangas\" folder): check the "
                      "download folder in Settings > Connected services > Suwayomi")
    url = str(request.get("chapter_url") or "")
    want = os.path.basename(path)
    folders: List[str] = []
    source_dir = os.path.join(mangas, suwayomi_name(source))
    for base in ([source_dir] if os.path.isdir(source_dir) else []) + [mangas]:
        try:
            names = sorted(os.listdir(base))
        except OSError:
            continue
        for entry in names:
            candidate = os.path.join(base, entry, suwayomi_name(title)) if base == mangas else os.path.join(base, entry)
            if os.path.isdir(candidate) and candidate not in folders:
                folders.append(candidate)
    for folder in folders[:200]:
        try:
            files = sorted(os.listdir(folder))
        except OSError:
            continue
        if want in files:
            return os.path.join(folder, want), ""
        if not url:
            continue
        for f in files:
            if f.lower().endswith(".cbz"):
                info = read_comicinfo(os.path.join(folder, f))
                if info is not None and info.web and info.web.strip() == url.strip():
                    return os.path.join(folder, f), ""
    return None, f"the chapter's CBZ is not in Suwayomi's download folder (looked for {want})"


# --- the pass ------------------------------------------------------------------------------------------------------

def run_chapter_arrivals(client: ChapterClient, ledger: DownloadLedger, *, download_dir: str, journal=None,
                         namer=None, should_stop: Callable[[], bool] = lambda: False) -> ArrivalsReport:
    """One chapter arrivals pass over every active chapter record (see the module docstring)."""
    return _ChapterPass(client, ledger, download_dir, journal, namer, should_stop).run()


class _ChapterPass:
    def __init__(self, client: ChapterClient, ledger: DownloadLedger, download_dir: str, journal, namer, should_stop):
        from ..store.journal import Journal

        self.client = client
        self.ledger = ledger
        self.db = ledger.store
        self.download_dir = os.path.normpath(download_dir) if download_dir else ""
        self.journal = journal if journal is not None else Journal(self.db)
        self.namer = namer if namer is not None else LazyNaming()
        self.should_stop = should_stop
        self.report = ArrivalsReport()
        self._volumes: Dict[int, Callable[[Decimal], Optional[Decimal]]] = {}

    def run(self) -> ArrivalsReport:
        records = list(self.ledger.active())
        if not records:
            return self.report
        ids = [int(r.info_hash) for r in records if r.info_hash.isdigit()]
        try:
            queue = {q.chapter_id: q for q in self.client.queue()}      # first: see the module docstring
            chapters = {c.id: c for c in self.client.chapters_by_id(ids)}
        except Exception as exc:  # noqa: BLE001 - nothing changes while Suwayomi cannot be asked
            self.report.error = f"Suwayomi could not be asked about its downloads: {type(exc).__name__}: {exc}"
            _log.warning("Chapter arrivals: %s; nothing changed", self.report.error)
            return self.report
        for rec in records:
            if self.should_stop():
                break
            self.report.checked += 1
            try:
                ref = int(rec.info_hash) if rec.info_hash.isdigit() else -1
                self._one(rec, chapters.get(ref), queue.get(ref))
            except StatusConflict as exc:
                _log.info("Chapter arrivals: %s", exc)
            except Exception as exc:  # noqa: BLE001 - one record must not stop the others; it stays as it was
                _log.exception("Chapter arrivals: download %d (%s) failed this pass", rec.id, rec.title)
                self.report.errors.append((rec.id, f"{type(exc).__name__}: {exc}"))
        return self.report

    def _one(self, rec: DownloadRecord, ch: Optional[SuwayomiChapter], queued: Optional[QueuedChapter]) -> None:
        if rec.status == DownloadStatus.SENT:
            if ch is None:
                self._fail(rec, "Suwayomi no longer knows this chapter (the source dropped it, or the manga was "
                                "removed from Suwayomi)")
                return
            if not ch.downloaded:
                if queued is None:
                    self._fail(rec, "no longer in Suwayomi's download queue and not downloaded (removed there?)")
                elif queued.state == "ERROR":
                    note = f"Suwayomi could not download it ({queued.tries} tries); retry it in Suwayomi, or cancel"
                    self._wait(rec, note)
                    self._note(rec, note)
                else:
                    self._wait(rec, f"{queued.state.lower() or 'queued'} in Suwayomi ({queued.progress:.0%})")
                    self._note(rec, None)
                return
            rec = self.ledger.set_status(rec.id, DownloadStatus.DOWNLOADED, expect=(DownloadStatus.SENT,), error=None)
            self.report.downloaded.append(rec.id)
            _log.info("Chapter arrivals: download %d (%s) finished in Suwayomi", rec.id, rec.title)
        if rec.status == DownloadStatus.DOWNLOADED:
            rec = self._file(rec)
            if rec is None or rec.status != DownloadStatus.FILED:
                return
        if rec.status == DownloadStatus.FILED:
            self._remove_copy(rec)

    # --- filing ----------------------------------------------------------------------------------------------

    def _file(self, rec: DownloadRecord) -> Optional[DownloadRecord]:
        try:
            series, root, series_dir = locate_series(self.db, rec.series_id)
        except LookupError as exc:
            return self._fail(rec, f"{exc}; nothing filed")
        problem = self._target_problem(rec, series, series_dir)
        plan_id = self.ledger.plan_id(rec.id)
        if plan_id is not None:
            return self._settle(rec, plan_id, series_dir, blocked=problem)
        if problem:
            return self._fail(rec, problem + "; nothing filed, Suwayomi's copy is left alone")
        if not self.download_dir or not os.path.isdir(self.download_dir):
            note = ("Suwayomi's download folder is not set or not there (Settings > Connected services > Suwayomi); "
                    "nothing filed")
            self._wait(rec, note)
            self._note(rec, note)
            return None
        request = self.ledger.request(rec.id)
        src, why = find_cbz(self.download_dir, request)
        if src is None:
            return self._fail(rec, why + "; nothing filed")
        info = read_comicinfo(src)
        number = rec.wanted_chapters[0] if rec.wanted_chapters else None
        if info is not None and info.number is not None and number is not None and info.number != number:
            return self._fail(rec, f"the CBZ found ({os.path.basename(src)}) is chapter {info.number}, not {number}; "
                                   "nothing filed")
        try:
            name = self._name(rec, request, info, number)
        except ImportError as exc:
            note = f"the naming scheme is not available in this build ({exc}); nothing filed"
            self._wait(rec, note)
            self._note(rec, note)
            return None
        if not name or "/" in name or "\\" in name or name in (".", ".."):
            return self._fail(rec, f"the naming scheme gave no usable name ({name!r}); nothing filed")
        dst = os.path.join(rec.target_dir, name)
        if os.path.lexists(dst):
            return self._fail(rec, f"a file named {name} is already in the target folder; nothing filed")
        held = self._held_copy(rec.target_dir, number)
        if held:
            return self._fail(rec, f"chapter {number} is already in the folder ({held}); nothing filed")
        from ..store.journal import StepRefused

        try:
            plan = self.journal.plan_links(PLAN_REASON, [(src, dst)], root_path=root.path,
                                           note=f"chapter download {rec.id}: {rec.title}")
        except StepRefused as exc:
            return self._fail(rec, f"the journal refused the filing: {exc}; nothing filed")
        self.ledger.attach_plan(rec.id, plan.id)
        return self._settle(rec, plan.id, series_dir)

    def _name(self, rec: DownloadRecord, request: Mapping[str, object], info: Optional[ComicInfo],
              number: Optional[str]) -> str:
        chapter = to_decimal(number) if number is not None else None
        if chapter is None:
            raise ValueError(f"download {rec.id} has no chapter number")
        source_name = (info.title if info is not None and info.title else str(request.get("chapter_name") or ""))
        stated_volume, title = split_chapter_name(source_name)
        volume = self._volume_of(rec.series_id, chapter)
        if volume is None and stated_volume is not None:
            volume = to_decimal(stated_volume)
        group = (info.translator if info is not None and info.translator else str(request.get("scanlator") or "")) or None
        kwargs = {"volume": volume, "title": title, "group": group, "ext": ".cbz", "folder": rec.target_dir}
        return self.namer.chapter_file_name(chapter, **kwargs)

    def _volume_of(self, series_id: int, chapter: Decimal) -> Optional[Decimal]:
        lookup = self._volumes.get(series_id)
        if lookup is None:
            try:
                lookup = self.namer.volume_lookup(self.db, series_id)
            except ImportError:
                raise
            except Exception:  # noqa: BLE001 - no volume list: the name's own volume (or none) is used
                _log.info("Chapter arrivals: no volume list for series %s", series_id, exc_info=True)
                lookup = lambda _n: None  # noqa: E731
            self._volumes[series_id] = lookup
        try:
            return lookup(chapter)
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _held_copy(folder: str, number: Optional[str]) -> Optional[str]:
        """The name of an archive in *folder* that holds exactly chapter *number* (a fresh look), else None."""
        from ..parsing import Kind, parse_name

        want = to_decimal(number)
        if want is None:
            return None
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            return None
        for name in names:
            if os.path.splitext(name)[1].lower() not in ARCHIVE_EXTS:
                continue
            parsed = parse_name(name)
            if parsed.kind not in (Kind.CHAPTER, Kind.BOTH) or parsed.chapter is None:
                continue
            start, end = parsed.chapter.start, parsed.chapter.end
            if start == want and (end is None or end == start):
                return name
        return None

    def _target_problem(self, rec: DownloadRecord, series, series_dir: str) -> Optional[str]:
        target = rec.target_dir
        if series.status != "present":
            return "the series folder was missing at the last scan"
        if not os.path.isdir(series_dir):
            return "the series folder is gone"
        if not target or not os.path.isabs(target) or not os.path.isdir(target):
            return f"the target folder {target!r} is gone"
        if not same_or_inside(target, series_dir):
            return "the target folder is no longer inside the series folder"
        root = self.db.root_for_path(target)
        if root is None or not same_or_inside(target, root.path):
            return "the target folder is no longer inside a configured library root"
        return None

    def _settle(self, rec: DownloadRecord, plan_id: int, series_dir: str,
                blocked: Optional[str] = None) -> Optional[DownloadRecord]:
        from ..store.journal import PlanStateError
        from ..store.lock import LockError

        try:
            plan = self.journal.get_plan(plan_id)
            if plan.status in ("applying", "undoing"):
                self.journal.recover(only=[plan_id])
                plan = self.journal.get_plan(plan_id)
            if plan.status in ("applying", "undoing"):
                self._wait(rec, f"its journal plan {plan_id} is busy in another live process")
                return None
            if plan.status in ("planned", "interrupted"):
                if blocked:
                    return self._fail(rec, blocked + "; filing not resumed, Suwayomi's copy is left alone",
                                      filed_files=self._filed(plan, series_dir),
                                      copied=any(s.how == "copy" for s in plan.done_steps))
                plan = self.journal.apply(plan_id)
        except LockError as exc:
            self._wait(rec, f"the library root is locked by another MangaList instance ({exc}); next pass")
            return None
        except PlanStateError as exc:
            return self._fail(rec, f"its journal plan {plan_id}: {exc}")
        filed = self._filed(plan, series_dir)
        copied = any(s.how == "copy" for s in plan.done_steps)
        if plan.status == "applied":
            rec = self.ledger.set_status(rec.id, DownloadStatus.FILED, expect=(DownloadStatus.DOWNLOADED,),
                                         error=None, filed_files=filed, copied=copied)
            self.report.filed.append(rec.id)
            if copied:
                self.report.copied.append(rec.id)
            _log.info("Chapter arrivals: download %d (%s) filed%s: %s", rec.id, rec.title,
                      " by copy (Suwayomi's folder is on another filesystem)" if copied else "", ", ".join(filed))
            return rec
        failed = next((s for s in plan.steps if s.state == "failed"), None)
        why = (f"filing stopped at {posixpath.basename(failed.dst.replace(os.sep, '/'))}: {failed.error}"
               if failed is not None else f"its journal plan {plan_id} is {plan.status}")
        return self._fail(rec, why, filed_files=filed, copied=copied)

    @staticmethod
    def _filed(plan, series_dir: str) -> List[str]:
        return [os.path.relpath(s.dst, series_dir).replace(os.sep, "/") for s in plan.done_steps]

    # --- completing the move: Suwayomi deletes its copy ------------------------------------------------------------

    def _remove_copy(self, rec: DownloadRecord) -> None:
        problem = self._library_problem(rec)
        if problem:
            self._wait(rec, f"Suwayomi's copy not deleted: {problem}")
            self._note(rec, f"Suwayomi's copy kept: {problem}")
            _log.warning("Chapter arrivals: download %d (%s): Suwayomi's copy kept: %s", rec.id, rec.title, problem)
            return
        try:
            self.client.delete_downloaded([int(rec.info_hash)])
        except Exception as exc:  # noqa: BLE001 - retried next pass; the chapter is in the library already
            note = f"Suwayomi did not delete its copy yet ({type(exc).__name__})"
            self._wait(rec, note)
            self._note(rec, note)
            return
        self.ledger.set_status(rec.id, DownloadStatus.REMOVED, expect=(DownloadStatus.FILED,), error=None)
        self.report.removed.append(rec.id)
        _log.info("Chapter arrivals: download %d (%s): Suwayomi deleted its downloaded copy; the library file stays",
                  rec.id, rec.title)

    def _library_problem(self, rec: DownloadRecord) -> Optional[str]:
        plan_id = self.ledger.plan_id(rec.id)
        if plan_id is None or not rec.filed_files:
            return "no filed file recorded"
        try:
            plan = self.journal.get_plan(plan_id)
        except Exception:  # noqa: BLE001 - PlanStateError
            return f"its journal plan {plan_id} is gone"
        done = [s for s in plan.steps if s.state == "done"]
        if not done or len(done) != len(rec.filed_files):
            return "the filed file and the journal disagree"
        for s in done:
            name = posixpath.basename(s.dst.replace(os.sep, "/"))
            try:
                size = os.stat(s.dst).st_size if os.path.isfile(s.dst) else None
            except OSError:
                size = None
            if size is None:
                return f"{name} is no longer in the library"
            if size != s.src_size:
                return f"{name} in the library is not the filed size"
        for root in self.db.list_roots():
            if self.download_dir and (same_or_inside(self.download_dir, root.path)
                                      or same_or_inside(root.path, self.download_dir)):
                return f"Suwayomi's download folder overlaps the library root {root.path}; never deleted there"
        return None

    # --- outcomes ----------------------------------------------------------------------------------------------

    def _fail(self, rec: DownloadRecord, why: str, *, filed_files: Optional[Sequence[str]] = None,
              copied: Optional[bool] = None) -> DownloadRecord:
        out = self.ledger.set_status(rec.id, DownloadStatus.FAILED, expect=(rec.status,), error=why,
                                     filed_files=filed_files, copied=copied)
        self.report.failed.append((rec.id, why))
        _log.warning("Chapter arrivals: download %d (%s) FAILED: %s", rec.id, rec.title, why)
        return out

    def _wait(self, rec: DownloadRecord, why: str) -> None:
        self.report.waiting.append((rec.id, why))

    def _note(self, rec: DownloadRecord, note: Optional[str]) -> None:
        """Keep why a record waits on it (None: nothing to say), for the downloads list."""
        if (rec.error or None) == (note or None):
            return
        try:
            self.ledger.set_status(rec.id, rec.status, expect=(rec.status,), error=note)
        except StatusConflict:
            pass

"""Content verdicts for "Whole chapters with parts" in the Duplicates view (owner, 2026-10-24: "Content verdicts").

Why: the section decided from names and numbers alone, and on the owner's library half of it was noise - research lane
D read the pages of all 54 items (2026-10-24): 22 real overlaps, 32 false positives. Only the pages can tell.

**What is read** - candidates only, never the whole library (owner, 2026-10-24): the files of each whole chapter held
with its parts (:func:`mangalist.duplicates.find_whole_and_parts`, the plain splits ``N`` + ``N.2`` included), plus
the neighbouring chapters ``N-1`` / ``N+1`` of the same folder as controls (the whole file, else its parts). Each
archive's pages are hashed once (:mod:`mangalist.page_hash`, MangaPixer's 64-bit pHash) and cached by its content
signature (:mod:`mangalist.store.page_hashes`; ``archives.signature``, computed on the spot for a file the background
backfill has not signed yet), so an unchanged file is never read again, and a rename or a move keeps its hashes. The
hashing runs off the UI thread (the view hands :func:`hash_files` to the subprocess pool, a batch at a time); the
verdicts themselves are a cheap read of the cache (:func:`judge`).

**The rule** (:func:`decide`; lane D's prototype, the thresholds as measured - exact on the 54 items): two pages are one
page within :data:`~mangalist.page_hash.MATCH_BITS` bits. A page of the item that also matches a page of a neighbouring
chapter is a credit / recruitment page a group repeats in every chapter, and is left out of the counts.

- **wrong chapter**: at least 70 % of the whole file's pages are a neighbouring chapter's (a source's numbering error:
  the file ``14`` holding chapter 13). Discard is preselected on the whole file.
- **copy**: one part is the whole again (at least 85 % both ways; the group re-posted ``N`` as ``N.1``). Discard is
  preselected on the later of the two.
- **whole = parts**: at least 70 % of the whole's pages are in its parts, and every part at least 60 % in the whole
  (two groups' releases of one chapter). Discard is preselected on the form the series does not hold
  (:func:`mangalist.downloads.chapter_forms.held_form`: the whole chapter in a parts series, else the parts).
- **distinct**: at most 15 % either way - separate chapters or side stories numbered ``N.1``, ``N.2``. Hidden.
- **undecided**: anything in between, too few pages, an archive that cannot be read (RAR / 7z, a broken zip, pages
  Pillow cannot decode, no Pillow at all), or not hashed yet. Shown as before, everything kept.

A plain split (``N`` + ``N.2``, the file ``N`` being part 1) is no longer listed (owner, 2026-10-24: "Rule fixes"), unless
its pages say it overlaps after all (a copy, the wrong chapter, the same pages). The owner's **Dismiss** ("this is
fine") hides an item until one of its files changes (:func:`dismiss`). The section counts only what it shows.

Nothing is deleted here or because of a verdict: a verdict only changes what is hidden and what is preselected; Apply
still lists every file and asks. No Qt.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from . import page_hash
from .gui.shell import OVERLAP_STAND_IN, DuplicateGroup
from .identity.signature import is_usable, signature_if_unchanged
from .page_hash import STATUS_OK, ArchivePages, same_page
from .store import page_hashes

_log = logging.getLogger(__name__)

VERDICT_PARTS = "whole = parts"
VERDICT_COPY = "copy"
VERDICT_WRONG = "wrong chapter"
VERDICT_DISTINCT = "distinct"
VERDICT_UNDECIDED = "undecided"

#: The thresholds research lane D measured (2026-10-24): constants, so a change is a reviewed change.
WRONG_SHARE = 0.70          # of the whole file's pages found in one neighbouring chapter
COPY_SHARE = 0.85           # a part inside the whole and the whole inside that part
WHOLE_IN_PARTS = 0.70       # of the whole's pages (credits left out) found in its parts
PART_IN_WHOLE = 0.60        # ... and of every part's pages found in the whole
DISTINCT_SHARE = 0.15       # at most this much either way: separate chapters
MIN_PAGES = 3               # fewer comparable pages in the whole than this: undecided

Key = Tuple[str, str]       # (the folder holding the whole file, the chapter number)
NOT_CHECKED = "the pages are not checked yet"


@dataclass(frozen=True)
class FileRef:
    """One archive of an item or a control: where it is, as seen now, and its content signature (None: unknown)."""

    path: str
    number: str
    size: int
    mtime_ns: int
    signature: Optional[str]


@dataclass(frozen=True)
class Item:
    key: Key
    group: DuplicateGroup
    whole: FileRef
    parts: Tuple[FileRef, ...]
    controls: Tuple[FileRef, ...]               # the neighbouring chapters N-1 / N+1 (credit pages, wrong chapter)

    @property
    def files(self) -> Tuple[FileRef, ...]:
        return (self.whole, *self.parts)


@dataclass(frozen=True)
class Verdict:
    kind: str                                   # VERDICT_*
    headline: str                               # the card's line ("" when undecided: the rule's note stays)
    evidence: str                               # what the pages showed, e.g. "16 of 16 pages of ch 81 are in ..."
    discard: Tuple[str, ...] = ()               # the paths preselected for Discard
    stand_in: bool = False                      # a plain split (N + N.2): listed only when its pages overlap
    dismiss_key: Optional[str] = None           # None: a file's signature is unknown, it cannot be dismissed
    dismissed: bool = False

    @property
    def hidden(self) -> bool:
        """Not listed: different chapters, dismissed by the owner, or a plain split its pages do not contradict."""
        if self.dismissed or self.kind == VERDICT_DISTINCT:
            return True
        return self.stand_in and self.kind not in (VERDICT_PARTS, VERDICT_COPY, VERDICT_WRONG)

    @property
    def hidden_why(self) -> str:
        if self.dismissed:
            return "dismissed"
        if self.kind == VERDICT_DISTINCT:
            return "different chapters"
        return "split chapter" if self.hidden else ""


@dataclass
class Judgement:
    verdicts: Dict[Key, Verdict] = field(default_factory=dict)
    pending: List[FileRef] = field(default_factory=list)      # archives still to hash (none when Pillow is missing)
    pillow: bool = True
    seconds: float = 0.0


@dataclass
class HashReport:
    files: int = 0              # archives looked at
    hashed: int = 0             # ... read and stored
    unreadable: int = 0         # ... of which could not be read (stored, so not tried again)
    skipped: int = 0            # changed while read, unsupported: not stored
    pages: int = 0
    seconds: float = 0.0
    stopped: bool = False


def item_key(group: DuplicateGroup) -> Key:
    return (os.path.normpath(os.path.dirname(group.files[0].path)), group.number)


# --- the decision (pure) ----------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Decision:
    kind: str
    evidence: str
    copy_of: Optional[str] = None               # the number of the part that is the whole again
    wrong_as: Optional[str] = None              # the neighbouring chapter whose pages the whole file holds


def _hits(pages: Sequence[int], pool: Sequence[int]) -> int:
    return sum(1 for a in pages if any(same_page(a, b) for b in pool))


def decide(number: str, whole: ArchivePages, parts: Sequence[Tuple[str, ArchivePages]],
           controls: Sequence[Tuple[str, ArchivePages]] = ()) -> Decision:
    """The verdict on a whole chapter *number* and its *parts* ``(number, pages)`` from their page hashes, with the
    neighbouring chapters *controls* (module docstring). Every input must be readable (``ok``)."""
    hw = [h for h in whole.hashes if h is not None]
    control_pages = [h for _n, c in controls for h in c.hashes if h is not None]
    names = " + ".join(n for n, _p in parts)
    either = " / ".join(n for n, _p in parts)
    # the wrong chapter: the whole file is a neighbour's pages (checked first: they would all read as credit pages)
    best: Optional[Tuple[int, str]] = None
    for n, c in controls:
        hits = _hits(hw, [h for h in c.hashes if h is not None])
        if best is None or hits > best[0]:
            best = (hits, n)
    if hw and best is not None and best[0] >= WRONG_SHARE * len(hw):
        return Decision(VERDICT_WRONG, f"{best[0]} of {len(hw)} pages of the file ch {number} are chapter {best[1]}'s "
                                       "pages", wrong_as=best[1])
    whole_own = [h for h in hw if not any(same_page(h, c) for c in control_pages)]
    credits = len(hw) - len(whole_own)
    credit_note = (f" ({credits} credit page{'s' if credits != 1 else ''} also in the next chapters left out)"
                   if credits else "")
    if len(whole_own) < MIN_PAGES:
        return Decision(VERDICT_UNDECIDED, f"ch {number} has only {len(whole_own)} page"
                                           f"{'s' if len(whole_own) != 1 else ''} to compare{credit_note}")
    all_parts = [h for _n, p in parts for h in p.hashes if h is not None]
    covered = _hits(whole_own, all_parts)
    share = covered / len(whole_own)
    per_part = []                   # (number, own pages, of them in the whole, whole pages in it, exact, pages)
    crc_whole = set(whole.crcs)
    for n, p in parts:
        hp = [h for h in p.hashes if h is not None]
        own = [h for h in hp if not any(same_page(h, c) for c in control_pages)]
        per_part.append((n, len(own), _hits(own, hw), _hits(whole_own, hp), sum(1 for c in p.crcs if c in crc_whole),
                         len(p.crcs)))
    for n, own, inside, back, exact, total in per_part:
        if own and inside >= COPY_SHARE * own and back >= COPY_SHARE * len(whole_own):
            same = ("all byte-identical" if exact and exact == total else f"{exact} byte-identical" if exact
                    else "re-encoded")
            return Decision(VERDICT_COPY, f"{back} of {len(whole_own)} pages of ch {number} are in {n}, and {inside} of "
                                          f"{own} of {n}'s are in ch {number} ({same}){credit_note}", copy_of=n)
    measured = [inside / own for _n, own, inside, *_rest in per_part if own]
    if not measured:
        return Decision(VERDICT_UNDECIDED, f"the parts {names} have no pages to compare{credit_note}")
    base = f"{covered} of {len(whole_own)} pages of ch {number} are in {names}"
    if share >= WHOLE_IN_PARTS and all(m >= PART_IN_WHOLE for m in measured):
        return Decision(VERDICT_PARTS, base + credit_note)
    if share <= DISTINCT_SHARE and all(m <= DISTINCT_SHARE for m in measured):
        text = (f"none of ch {number}'s {len(whole_own)} pages are in {either}" if not covered
                else f"only {covered} of {len(whole_own)} pages of ch {number} are in {either}")
        return Decision(VERDICT_DISTINCT, f"{text} - separate chapters{credit_note}")
    return Decision(VERDICT_UNDECIDED, f"{base} - partly the same pages{credit_note}")


# --- reading the candidates --------------------------------------------------------------------------------------

def _stat(path: str) -> Optional[Tuple[int, int]]:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return int(st.st_size), int(st.st_mtime_ns)


# Signatures computed here for files the backfill has not signed yet, by (path, size, mtime): the verdicts are re-read
# after every batch of page hashes, and a file must not be read again for that.
_SIGNED: Dict[Tuple[str, int, int], str] = {}
_SIGNED_MAX = 5000


class _Signer:
    """Content signatures of files: the stored one when the archive row still describes the file, else computed now
    (128 KiB read; the background backfill writes the row, not this)."""

    def __init__(self, db):
        self.db = db
        self.roots = {r.id: r.path for r in db.list_roots()}
        self.rows: Dict[str, object] = {}
        self.seen_series: set = set()

    def load_series(self, series_id: Optional[int]) -> None:
        if series_id is None or series_id in self.seen_series:
            return
        self.seen_series.add(series_id)
        for a in self.db.list_archives(series_id=series_id, status="present"):
            root = self.roots.get(a.root_id)
            if root is not None:
                self.rows[os.path.normpath(str(Path(root).joinpath(*a.rel_path.split("/"))))] = a

    def ref(self, path: str, number: str) -> Optional[FileRef]:
        stamp = _stat(path)
        if stamp is None:
            return None
        row = self.rows.get(os.path.normpath(path))
        sig = None
        if row is not None and (row.size, row.mtime_ns) == stamp and is_usable(row.signature, row.size):
            sig = row.signature
        if sig is None:
            seen = (os.path.normpath(path), *stamp)
            sig = _SIGNED.get(seen)
            if sig is None:
                sig = signature_if_unchanged(path, *stamp)
                if sig is not None:
                    if len(_SIGNED) >= _SIGNED_MAX:
                        _SIGNED.clear()
                    _SIGNED[seen] = sig
        return FileRef(str(path), number, stamp[0], stamp[1], sig)


def _numbers_by_file(db, series_id: Optional[int], series_folder: str) -> Dict[str, Tuple[str, Decimal]]:
    """abs path -> (container rel to the series folder, the ONE chapter number it states) for a series' archives."""
    from .duplicates import KIND_CHAPTER, stated_unit

    if series_id is None:
        return {}
    per: Dict[str, list] = {}
    with db.connect() as con:
        for row in con.execute("SELECT rel_path, kind, vol_from, vol_to, ch_from, ch_to, group_name FROM units"
                               " WHERE series_id = ? ORDER BY rel_path, seq", (series_id,)):
            per.setdefault(row["rel_path"], []).append(SimpleNamespace(**dict(row)))
    out = {}
    for rel, rows in per.items():
        stated = stated_unit(rows)
        if stated is None or stated.kind != KIND_CHAPTER:
            continue
        parts = [p for p in str(rel).replace("\\", "/").split("/") if p and p != "."]
        path = os.path.normpath(os.path.join(series_folder, *parts))
        out[path] = ("/".join(parts[:-1]), stated.number)
    return out


def _canonical(n: Decimal) -> str:
    from .duplicates import canonical_number

    return canonical_number(n)


def build_items(db, groups: Sequence[DuplicateGroup], signer: Optional[_Signer] = None) -> List[Item]:
    """The candidates of *groups* (overlaps of :func:`~mangalist.duplicates.find_whole_and_parts`) with their files'
    numbers and signatures and their neighbouring chapters as controls. A group whose files cannot be read now is
    left out (it is listed as it is)."""
    from .split_chapters import part_numbers

    signer = signer or _Signer(db)
    numbers_cache: Dict[Optional[int], Dict[str, Tuple[str, Decimal]]] = {}
    items = []
    for g in groups:
        if not getattr(g, "overlap", "") or not g.files:
            continue
        sid = g.series_id
        if sid not in numbers_cache:
            numbers_cache[sid] = _numbers_by_file(db, sid, g.folder)
            signer.load_series(sid)
        numbers = numbers_cache[sid]
        whole_path = os.path.normpath(g.files[0].path)
        container = numbers.get(whole_path, (None, None))[0]
        if container is None:
            continue
        refs = []
        for f in g.files:
            seen = numbers.get(os.path.normpath(f.path))
            ref = signer.ref(f.path, _canonical(seen[1]) if seen else "?")
            if ref is None:
                break
            refs.append(ref)
        if len(refs) != len(g.files):
            continue
        whole = Decimal(g.number)
        here: Dict[Decimal, List[str]] = {}
        for path, (cont, n) in numbers.items():
            if cont == container:
                here.setdefault(n, []).append(path)
        controls = []
        splits = part_numbers(here)
        for m in (whole - 1, whole + 1):
            paths = sorted(here.get(m, ()))
            if not paths and m >= 0:
                paths = [p for part in splits.get(int(m), ()) for p in sorted(here.get(part, ()))]
            for p in paths[:3]:
                ref = signer.ref(p, _canonical(m))
                if ref is not None:
                    controls.append(ref)
        items.append(Item(item_key(g), g, refs[0], tuple(refs[1:]), tuple(controls)))
    return items


def judge(db, groups: Sequence[DuplicateGroup], *, pillow: Optional[bool] = None) -> Judgement:
    """The verdict on every overlap of *groups* from the cached page hashes, and the archives still to hash (module
    docstring). Reads the database and stats the files (a file without a signature is signed on the spot: 128 KiB);
    never decodes a page. Off the UI thread."""
    started = time.monotonic()
    pillow = page_hash.pillow_available() if pillow is None else pillow
    items = build_items(db, groups)
    every = {f.signature for it in items for f in (*it.files, *it.controls) if f.signature}
    cached = page_hashes.cached_pages(db, every)
    dismissed = page_hashes.dismissed_keys(db)
    out = Judgement(pillow=pillow)
    pending: Dict[str, FileRef] = {}
    for it in items:
        verdict = _verdict(db, it, cached, pillow, pending)
        key = page_hashes.dismissal_key(f.signature for f in it.files)
        out.verdicts[it.key] = Verdict(verdict.kind, verdict.headline, verdict.evidence, verdict.discard,
                                       it.group.overlap == OVERLAP_STAND_IN, key, key is not None and key in dismissed)
    for g in groups:                            # a candidate that could not be read now: undecided, its note stays
        if getattr(g, "overlap", "") and item_key(g) not in out.verdicts:
            out.verdicts[item_key(g)] = Verdict(VERDICT_UNDECIDED, "", "its files cannot be read now",
                                                stand_in=g.overlap == OVERLAP_STAND_IN)
    out.pending = list(pending.values())
    out.seconds = round(time.monotonic() - started, 3)
    return out


def _readable_kind(path: str) -> bool:
    import zipfile

    try:
        return zipfile.is_zipfile(path)
    except OSError:
        return False


def _verdict(db, it: Item, cached: Dict[str, ArchivePages], pillow: bool, pending: Dict[str, FileRef]) -> Verdict:
    """One item's verdict; adds the archives still to hash to *pending*."""
    missing, bad = [], []
    for f in (*it.files, *it.controls):
        pages = cached.get(f.signature) if f.signature else None
        if pages is not None:
            if not pages.ok and f in it.files:
                bad.append(f"ch {f.number} cannot be read ({pages.reason})")
            continue
        if f.signature is None:
            if f in it.files:
                bad.append(f"ch {f.number} is being changed")
            continue
        if not _readable_kind(f.path):
            if f in it.files:
                kind = os.path.splitext(f.path)[1].lower().lstrip(".")
                bad.append(f"ch {f.number} cannot be read (not a zip archive)" if kind in ("cbz", "zip", "")
                           else f"ch {f.number} is a {kind} archive, which is not read")
            continue
        if not pillow:
            if f in it.files:
                bad.append("pages cannot be read here: Pillow is not installed")
            continue
        missing.append(f)
        pending.setdefault(f.signature, f)
    if bad:
        return Verdict(VERDICT_UNDECIDED, "", "; ".join(dict.fromkeys(bad)))
    if missing:
        return Verdict(VERDICT_UNDECIDED, "", NOT_CHECKED)
    controls = [(c.number, cached[c.signature]) for c in it.controls
                if c.signature in cached and cached[c.signature].ok]
    decision = decide(it.group.number, cached[it.whole.signature],
                      [(p.number, cached[p.signature]) for p in it.parts], controls)
    return _worded(db, it, decision)


def _worded(db, it: Item, d: Decision) -> Verdict:
    """The card's words and the preselected discards for a decision."""
    n = it.group.number
    by_path = {f.path: f for f in it.group.files}
    if d.kind == VERDICT_WRONG:
        return Verdict(d.kind, f"Wrong chapter: the file {n} holds chapter {d.wrong_as}'s pages - it can go (chapter "
                               f"{n} itself is still missing).", d.evidence, (it.whole.path,))
    if d.kind == VERDICT_COPY:
        part = next(p for p in it.parts if p.number == d.copy_of)
        later = max((by_path[it.whole.path], by_path[part.path]), key=lambda f: (f.modified, f.path))
        return Verdict(d.kind, f"Copy: part {d.copy_of} is the whole chapter {n} again - one of them can go (the later "
                               "one is marked Discard).", d.evidence, (later.path,))
    if d.kind == VERDICT_PARTS:
        from .downloads.chapter_forms import held_form

        held = _held_numbers(db, it)
        form = held_form(held)
        if form.parts:
            discard, said = (it.whole.path,), "the whole chapter is marked Discard - the series holds its chapters in parts"
        else:
            discard = tuple(p.path for p in it.parts)
            said = "the parts are marked Discard - the series holds whole chapters"
        return Verdict(d.kind, f"Same pages: the whole chapter {n} is its parts {', '.join(p.number for p in it.parts)} "
                               f"- keep one form ({said}).", d.evidence, discard)
    if d.kind == VERDICT_DISTINCT:
        return Verdict(d.kind, "Different chapters: nothing to do.", d.evidence)
    return Verdict(d.kind, "", d.evidence)


def _held_numbers(db, it: Item) -> List[Decimal]:
    numbers = _numbers_by_file(db, it.group.series_id, it.group.folder)
    container = numbers.get(os.path.normpath(it.whole.path), ("", None))[0]
    return [n for cont, n in numbers.values() if cont == container]


# --- hashing --------------------------------------------------------------------------------------------------

def hash_files(db, files: Iterable[FileRef], *, should_stop: Optional[Callable[[], bool]] = None) -> HashReport:
    """Hash the pages of *files* (read-only) and store them by signature (module docstring); a file that changed
    since it was listed is skipped. The caller runs this off the UI thread."""
    report = HashReport()
    started = time.monotonic()
    for f in files:
        if should_stop is not None and should_stop():
            report.stopped = True
            break
        report.files += 1
        if not f.signature:
            report.skipped += 1
            continue
        pages = page_hash.hash_archive(f.path, expect=(f.size, f.mtime_ns))
        if page_hashes.store_pages(db, f.signature, pages):
            report.hashed += 1
            report.pages += pages.pages
            if pages.status != STATUS_OK:
                report.unreadable += 1
                _log.info("Duplicates: the pages of %s cannot be read: %s", f.path, pages.reason)
        else:
            report.skipped += 1
            _log.debug("Duplicates: pages of %s not hashed now: %s", f.path, pages.reason)
    report.seconds = round(time.monotonic() - started, 3)
    if report.hashed or report.skipped:
        _log.info("Duplicates: page hashes of %d archive%s (%d pages, %d unreadable, %d left for later) in %.1f s",
                  report.hashed, "" if report.hashed == 1 else "s", report.pages, report.unreadable, report.skipped,
                  report.seconds)
    return report


def hash_job(db_path: str, files: Sequence[FileRef]) -> HashReport:
    """:func:`hash_files` for the subprocess pool (:mod:`mangalist.process_pool`): opens its own store."""
    from .store import Store

    return hash_files(Store(Path(db_path), import_legacy=False), files)


# --- shown and hidden; Dismiss -------------------------------------------------------------------------------------

def split_shown(groups: Sequence[DuplicateGroup], verdicts: Dict[Key, Verdict]) -> Tuple[List[DuplicateGroup],
                                                                                        List[DuplicateGroup]]:
    """(listed, hidden) of *groups*: hidden are different chapters, dismissed items and plain splits whose pages do
    not overlap (a stand-in without a verdict is hidden too: the rule fix)."""
    shown, hidden = [], []
    for g in groups:
        v = verdicts.get(item_key(g))
        if v is None:
            hide = g.overlap == OVERLAP_STAND_IN
        else:
            hide = v.hidden
        (hidden if hide else shown).append(g)
    return shown, hidden


def dismiss(db, group: DuplicateGroup, verdict: Verdict) -> bool:
    """Remember that the owner judged *group* fine: hidden until one of its files changes. False when it cannot be
    (a file's signature is unknown)."""
    if not verdict.dismiss_key:
        return False
    page_hashes.dismiss(db, verdict.dismiss_key, series_id=group.series_id, folder=item_key(group)[0],
                        number=group.number, files=[os.path.basename(f.path) for f in group.files])
    _log.info("Duplicates: whole chapter %s with parts dismissed in %s (until a file changes)", group.number,
              item_key(group)[0])
    return True


def restore(db, verdict: Verdict) -> bool:
    """Undo a dismissal."""
    return bool(verdict.dismiss_key) and page_hashes.undismiss(db, verdict.dismiss_key)


__all__ = ["NOT_CHECKED", "VERDICT_PARTS", "VERDICT_COPY", "VERDICT_WRONG", "VERDICT_DISTINCT", "VERDICT_UNDECIDED", "Decision",
           "FileRef", "HashReport", "Item", "Judgement", "Verdict", "build_items", "decide", "dismiss", "hash_files",
           "hash_job", "item_key", "judge", "restore", "split_shown"]

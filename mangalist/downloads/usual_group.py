"""A chapter from another group, replaced by the series' usual group's copy when that is released (owner, 2026-11-06).
No Qt here.

The owner's question and answer (2026-11-06): "should MangaList wait a while for the series' usual group?" - "No.
Download and if the usual group releases, replace the downloaded chapter."

Why: the scheduled chapter check takes a new chapter at once, from "the only group with these chapters" when only one
group has it (:func:`.chapters.default_group`). A series held chapters 1-46 by a joint group "Group A + Group B" (names
made up); the source listed chapter 47 first only by "Group C", a day before the joint group's copy - the check took
C's, and nothing happened when the usual group's came out.

**Provisional** (:func:`extra_for`): a chapter sent automatically - the find pass or the scheduled chapter check on Auto,
or a waiting finding the owner sends - whose group is not the series' usual group (:func:`usual_of`: the owner's own
group choice for the series when there is one, else the folder's usual group by
:func:`mangalist.duplicates.usual_group`'s rule around the first chapter sent - the nearest neighbours, else the folder's
clear favourite). A group is compared as :func:`same_group` reads it: the library's files and the source spell a joint
group differently (``A, B`` and ``A & B``; the naming scheme cuts a long one with ``…``). Its record keeps
``usual_group`` and ``usual_until`` (the end of the watch) in its request. Never
provisional: a chapter from the usual group; a series with no usual group (no chapter file names a group, or no clear
favourite); a chapter the owner sent from the chapters panel (they chose the copy themselves).

**The watch** (:func:`watched`): a provisional chapter is watched for :data:`WATCH_DAYS` days from its send, while it is
filed (its file still in the series folder, as the journal followed it), from the series' saved source (sources number
differently), and not replaced yet. The scheduled chapter check (:mod:`.chapter_check`) looks at the source's list it
fetched anyway - no extra request - and when the usual group's copy of the SAME chapter number is listed and not in the
download queue, it is sent (:func:`usual_copies`, :func:`send_usual_copies`) into the folder the provisional file lies
in. Up to :data:`MAX_TRIES` sends when one fails; a replacement the owner cancelled ends the watch. The check's
MangaDex gate does not hide it: the usual group's upload is MangaDex's new latest upload, and a series is fetched at
least daily anyway (:data:`~.chapter_gate.REFETCH_AFTER`). It is sent on Notify too: the chapter is held already, and
the owner's answer stands for it.

**One form per chapter** (:mod:`.chapter_forms`): only the same number replaces. When the usual group lists the chapter
in another form - parts ``47.1`` / ``47.2`` where the provisional copy is the whole ``47``, or the whole where the
provisional copies are parts - nothing is replaced (two files for one, or one for two, would mix the forms or leave
half a chapter from each group); the provisional copy stays and the check's log says so.

**Filing** (:mod:`.chapter_arrivals`): the provisional file goes to the holding folder FIRST, through the replaced-files
batch of other releases (:func:`mangalist.upgrades.hold_replaced_volumes`: ``hold`` steps, restorable from the Download
tab, emptied after the holding period; the batch id kept on the record so a stopped pass resumes), then the usual
group's copy is filed under the naming scheme (its group in the name). Nothing filed: the old copy is put back.

**What the owner sees** (:func:`note_text`, on the record's In progress line): "from Group C; replaced by Group A +
Group B's copy when it is released (watched until 2026-11-20)", later "from Group C; replaced by Group A + Group B's
copy"; the usual group's own record: "replaces Group C's copy", then "replaced Group C's copy".

**Not watched**: a series on Off (the chapter check does not ask it), a finished series (the check skips it), and the
find pass (it fills gaps; a provisional chapter is held, so it is none).
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .contracts import DownloadStatus, SuwayomiChapter

_log = logging.getLogger(__name__)

#: How long a provisional chapter is watched for the usual group's copy, from its send. Two weeks: the live case's usual
#: group was one day behind, a weekly series' group a few days at most; a group still silent after two weeks has most
#: likely skipped the chapter (or stopped), and a replacement so late would surprise more than help. The watch costs
#: no request (the check reads the list it fetches anyway); the bound keeps old chapters from being swapped long after.
WATCH_DAYS = 14
#: Sends of the usual group's copy for one provisional chapter when they fail (the extension host gave up, the source
#: dropped it): then the provisional copy stays.
MAX_TRIES = 3

KEY_USUAL = "usual_group"               # a provisional chapter's request: the series' usual group
KEY_UNTIL = "usual_until"               # ... the last day of its watch (ISO date, UTC)
KEY_REPLACED_BY = "replaced_by"         # ... the usual group's record that replaced it (once filed)
KEY_REPLACES = "replaces"               # the usual group's record: the provisional record it replaces
KEY_REPLACES_GROUP = "replaces_group"   # ... that record's group

_FILED = (DownloadStatus.FILED, DownloadStatus.REMOVED)
_ON_ITS_WAY = (DownloadStatus.QUEUED, DownloadStatus.SENT, DownloadStatus.DOWNLOADED)


def _key(group: Optional[str]) -> str:
    return (group or "").strip().casefold()


_JOINERS = re.compile(r"\s*[,&+/|]\s*")
_CUT = "\u2026"                          # the naming scheme cuts a long group to 32 characters, ending with it


def _members(group: Optional[str]) -> Tuple[List[str], bool]:
    """(the groups a joint group names, letters and digits only, in order; whether the name was cut)."""
    text = (group or "").strip()
    cut = text.endswith(_CUT)
    parts = [re.sub(r"[\W_]+", "", p.casefold()) for p in _JOINERS.split(text.rstrip(_CUT))]
    return [p for p in parts if p], cut


def same_group(a: Optional[str], b: Optional[str]) -> bool:
    """*a* and *b* name the same scanlation group, as the library's files and the source spell it: a joint group is
    the same whatever joins its members (the files say ``A, B``, the extension host ``A & B``), brackets and case aside
    (the scheme writes ``[SW]`` as ``(SW)``), and a name the naming scheme cut (``A, Long Na…``) matches the full one
    it starts. Live, 2026-11-06: the usual group's files said ``ToruScans, Luminare Translations``."""
    if _key(a) == _key(b):
        return bool(_key(a))
    ma, cut_a = _members(a)
    mb, cut_b = _members(b)
    if not ma or not mb:
        return False
    if not cut_a and not cut_b:
        return sorted(ma) == sorted(mb)
    if cut_b and not cut_a:
        ma, mb, cut_a, cut_b = mb, ma, cut_b, cut_a
    # *a* is cut: its members but the last are the other's first ones, its last one starts the other's next one
    if len(ma) > len(mb) or ma[:-1] != mb[:len(ma) - 1]:
        return False
    return mb[len(ma) - 1].startswith(ma[-1]) or (cut_b and ma[-1].startswith(mb[len(ma) - 1]))


def _today(now: Optional[datetime] = None) -> date:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).date()


# --- which group is usual --------------------------------------------------------------------------------------

def usual_of(held: Mapping[Decimal, Sequence[str]], missing: Sequence[Decimal],
             stored: Optional[str] = None) -> Optional[str]:
    """The series' usual group for chapters *missing* (see the module docstring): the owner's choice for the series
    (*stored*), else the group the folder's chapter files use around the first of them; None when there is none."""
    if stored and stored.strip():
        return stored.strip()
    if not held or not missing:
        return None
    from ..duplicates import usual_group

    groups = sorted({g for gs in held.values() for g in gs if g}, key=_key)
    if not groups:
        return None
    # usual_group names the group of the folder's own files among the candidates given: every held group and a
    # nameless one, so "some have it, some do not" holds
    return usual_group({n: list(gs) for n, gs in held.items()}, min(missing), [*groups, None])


def provisional(scanlator: Optional[str], usual: Optional[str]) -> bool:
    """A chapter from *scanlator* is provisional: the series has a usual group and it is another one
    (:func:`same_group`)."""
    return bool(usual and _key(usual)) and not same_group(scanlator, usual)


def extra_for(picks: Sequence[SuwayomiChapter], usual: Optional[str],
              replacing: Optional[Mapping[str, Mapping[str, Any]]] = None,
              now: Optional[datetime] = None) -> Tuple[Dict[int, Dict[str, Any]], List[str]]:
    """(chapter id -> the request keys a send records for it, the chapter numbers sent although held): a provisional
    pick gets ``usual_group`` / ``usual_until``; a pick that replaces a provisional copy (*replacing*: number -> its
    keys) gets those."""
    from ..store.units import exact_number

    until = (_today(now) + timedelta(days=WATCH_DAYS)).isoformat()
    replacing = {exact_number(n): dict(v) for n, v in (replacing or {}).items() if exact_number(n)}
    out: Dict[int, Dict[str, Any]] = {}
    waived: List[str] = []
    for ch in picks:
        number = exact_number(ch.number) if ch.number else None
        if number is not None and number in replacing:
            out[int(ch.id)] = replacing[number]
            waived.append(number)
        elif provisional(ch.scanlator, usual):
            out[int(ch.id)] = {KEY_USUAL: str(usual).strip(), KEY_UNTIL: until}
    return out, waived


# --- the watch -------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Watched:
    """A provisional chapter still watched for its usual group's copy."""

    record_id: int
    number: str                         # exact
    group: str                          # the provisional copy's group ('' : none)
    usual: str
    source_id: str
    path: str                           # where its file is now (absolute)
    until: str                          # the last day of the watch


def watched(db, ledger, series_id: int, now: Optional[datetime] = None) -> List[Watched]:
    """The series' provisional chapters still watched (see the module docstring), by chapter number."""
    from ..store.journal import Journal, MoveFollower
    from .placement import locate_series

    requests = ledger.chapter_requests(series_id)
    if not any(r.get(KEY_USUAL) for r in requests.values()):
        return []
    try:
        _series, root, series_dir = locate_series(db, series_id)
    except LookupError:
        return []
    today = _today(now).isoformat()
    records = {r.id: r for r in ledger.for_series(series_id)}
    tries: Dict[int, List[str]] = {}
    for rid, req in requests.items():
        old = req.get(KEY_REPLACES)
        if isinstance(old, int) and rid in records:
            tries.setdefault(old, []).append(records[rid].status)
    journal = Journal(db)
    follower = MoveFollower(journal)
    out: List[Watched] = []
    for rid, req in requests.items():
        rec = records.get(rid)
        usual = str(req.get(KEY_USUAL) or "").strip()
        until = str(req.get(KEY_UNTIL) or "")
        if rec is None or not usual or rec.status not in _FILED or req.get(KEY_REPLACED_BY) or not until:
            continue
        if until < today or len(rec.wanted_chapters) != 1:
            continue
        before = tries.get(rid, [])
        if any(s in (*_ON_ITS_WAY, *_FILED, DownloadStatus.CANCELLED) for s in before):
            continue                    # on its way, filed, or the owner cancelled it: the watch is over
        if sum(1 for s in before if s == DownloadStatus.FAILED) >= MAX_TRIES:
            continue
        path = _filed_path(ledger, journal, follower, rid, root.path)
        if path is None or not (os.path.normcase(path).startswith(os.path.normcase(series_dir.rstrip(os.sep)) + os.sep)):
            continue                    # its file is gone (deleted, held, moved out): nothing to replace
        out.append(Watched(rid, rec.wanted_chapters[0], rec.group, usual, str(req.get("source_id") or ""), path,
                           until))
    return out


def _filed_path(ledger, journal, follower, record_id: int, root: str) -> Optional[str]:
    """Where the file a chapter record filed is now (followed through the journal's moves), when it is still a plain
    file in the library; else None."""
    from .. import upgrades

    plan_id = ledger.plan_id(record_id)
    if plan_id is None:
        return None
    try:
        plan = journal.get_plan(plan_id)
    except Exception:  # noqa: BLE001 - PlanStateError: its plan is gone
        return None
    for step in plan.done_steps:
        if follower.held(step.dst, plan_id):
            continue
        path = follower.current_path(step.dst, plan_id)
        if upgrades._plain_below(path, root) is not None:
            return path
    return None


def old_copy(ledger, journal, record_id: int, root: str) -> Optional[str]:
    """The provisional record's file as it is now (for the filing of its replacement); None when it is gone."""
    from ..store.journal import MoveFollower

    return _filed_path(ledger, journal, MoveFollower(journal), record_id, root)


def usual_copies(chapters: Sequence[SuwayomiChapter], watch: Sequence[Watched], *, source_id: str,
                 queued_ids: Sequence[int] = ()) -> Tuple[List[Tuple[Watched, SuwayomiChapter]], List[str]]:
    """(the usual group's copies the source lists for the watched chapters, what is said of the others): the same
    chapter number from the usual group (its latest upload) on the watched chapters' own source, not in the download
    queue. A usual group's copy in another form (parts for a whole chapter, a whole for parts) is not taken: see the
    module docstring."""
    from .chapters import _per_group, to_decimal

    per = _per_group(chapters)
    queued = {int(q) for q in queued_ids}
    pairs: List[Tuple[Watched, SuwayomiChapter]] = []
    notes: List[str] = []
    for w in watch:
        if w.source_id and str(w.source_id) != str(source_id):
            continue
        number = to_decimal(w.number)
        if number is None:
            continue
        copy = next((ch for ch in per.get(number, {}).values() if same_group(ch.scanlator, w.usual)), None)
        if copy is not None:
            if int(copy.id) not in queued:
                pairs.append((w, copy))
            continue
        whole = number.to_integral_value() == number
        def usual_has(n: Decimal) -> bool:
            return any(same_group(ch.scanlator, w.usual) for ch in per[n].values())

        other = ([n for n in per if n != number and n.to_integral_value() != n and int(n) == number and usual_has(n)]
                 if whole else [n for n in per if n == int(number) and usual_has(n)])
        if other:
            notes.append(f"ch {w.number}: {w.usual} released it as ch "
                         f"{', '.join(_plain(n) for n in sorted(other))} - another form, so "
                         f"{w.group or 'the first'}'s copy is kept (one form per chapter)")
    return pairs, notes


def _plain(n: Decimal) -> str:
    return str(int(n)) if n == n.to_integral_value() else format(n.normalize(), "f")


def send_usual_copies(client, ledger, series_id: int, match, pairs: Sequence[Tuple[Watched, SuwayomiChapter]], *,
                      manga_title: str = "", source_name: str = ""):
    """Send the usual group's copies (one send per folder the provisional files lie in) -> the send outcomes."""
    from .chapters import send_chapters

    by_folder: Dict[str, List[Tuple[Watched, SuwayomiChapter]]] = {}
    for w, ch in pairs:
        by_folder.setdefault(os.path.dirname(w.path), []).append((w, ch))
    outcomes = []
    for folder, group in by_folder.items():
        replacing = {w.number: {KEY_REPLACES: w.record_id, KEY_REPLACES_GROUP: w.group} for w, _ch in group}
        outcomes.append(send_chapters(client, ledger, series_id, match, [ch for _w, ch in group], folder,
                                      manga_title=manga_title, source_name=source_name, replacing=replacing))
    return outcomes


# --- what the owner is told ------------------------------------------------------------------------------------

def _possessive(group: str) -> str:
    return f"{group}'s" if group else "the first"


def note_text(request: Mapping[str, Any], status: str) -> str:
    """What a chapter record says about the usual group (see the module docstring); '' when nothing."""
    group = str(request.get("scanlator") or "").strip()
    usual = str(request.get(KEY_USUAL) or "").strip()
    if usual:
        if request.get(KEY_REPLACED_BY):
            return f"from {group or 'no group'}; replaced by {_possessive(usual)} copy"
        if status in (DownloadStatus.FAILED, DownloadStatus.CANCELLED):
            return ""
        until = str(request.get(KEY_UNTIL) or "")
        return (f"from {group or 'no group'}; replaced by {_possessive(usual)} copy when it is released"
                + (f" (watched until {until})" if until else ""))
    if isinstance(request.get(KEY_REPLACES), int):
        old = _possessive(str(request.get(KEY_REPLACES_GROUP) or "").strip())
        if status in _FILED:
            return f"replaced {old} copy (it is in the holding folder)"
        if status in (DownloadStatus.FAILED, DownloadStatus.CANCELLED):
            return f"{old} copy is kept"
        return f"replaces {old} copy"
    return ""


__all__ = ["KEY_REPLACED_BY", "KEY_REPLACES", "KEY_REPLACES_GROUP", "KEY_UNTIL", "KEY_USUAL", "MAX_TRIES", "WATCH_DAYS",
           "Watched", "extra_for", "note_text", "old_copy", "provisional", "same_group", "send_usual_copies",
           "usual_copies", "usual_of", "watched"]

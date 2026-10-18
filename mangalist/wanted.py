"""The Download tab's "To get" list without Qt: what is missing, for the headless find pass and the window alike.

:func:`wanted_list` is the contract of :mod:`mangalist.downloads.finding` (lane A of the automatic-downloads cycle): the
:class:`~mangalist.gui.shell.WantedSeries` of every present series of the given roots, from the library database and the
MangaPixer cache alone - no Qt, no network, no disk walk.

**Where the files come from: the database, not a fresh walk.** The find pass runs right after the scheduled rescan, which
has just recorded every series folder (``series``) and every archive with its size (``archives``). Each series' archives are
parsed again from their names with exactly the scanner's context (the folder title, the root's naming scheme, the series'
"volumes or chapters?" answer - :func:`mangalist.scanner._parse_context` + :func:`mangalist.classifier.annotate_file`), so
the rebuilt :class:`~mangalist.models.MangaEntry` reads the same as the scanned one: the same inventory, the same
"volumes or chapters?" question (a guessed chapter is not in the ``units`` rows, so those are not used), the same
numbering-restart rule (season subfolders). Why not walk the disk again: the rescan just did, a second walk wakes the
array's disks and takes as long as the rescan itself, and the List shows what the last scan recorded too. A folder that
changed since the last scan is read at the next one (the same as the List). Cost: about 5 s for 700 series with 22,000
archives on the dev box, half of it ``pathlib`` in the inventory and the restart rule (shared with the window).

**One implementation.** The window's state providers (``MainWindow._mp_item_for`` / ``_knowledge_for`` /
``_inventory_for``), the table model's ``_compute`` and the window's To get entries call the functions below; the window
keeps its own caching. Since UI performance stage 2 the window computes them off the UI thread (the states' worker:
:func:`evaluate`, :func:`inventory_of`, :func:`series_wanted` per series, as :func:`library_states` composes them), and
its Rename-pending count runs in a subprocess over :func:`entries_from_db` - the series as the last scan recorded them. What the window has that the database does not: the rename counts (they only set the
Rename pending flag, never a gap) and the table's unsaved edits (none touch the state: overrides, links and kind answers
are saved at once).
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .gui.list_text import wanted_series
from .gui.shell import WantedSeries
from .gui.volumes_target import Availability, find_volumes_availability
from .knowledge import SeriesKnowledge, declared_edition_of, fmt_num, from_mangapixer_item, from_own_matcher, to_decimal
from .models import FileHit, MangaEntry
from .states import SeriesState, compute_state, fallback_inventory_from_entry
from .unbundled import unbundled_choices

_log = logging.getLogger(__name__)

__all__ = [
    "wanted_list", "library_states", "SeriesView", "entries_from_db", "apply_links_cache", "load_links_cache", "open_resolver", "mangapixer_item",
    "mangapixer_resolution", "item_of",
    "knowledge_of_item", "inventory_of", "evaluate", "held_volumes", "series_wanted",
]


# --- the pieces the window shares --------------------------------------------------------------------------------


def apply_links_cache(entry: MangaEntry, cached: Mapping[str, Any]) -> None:
    """Copy one ``links_cache`` row (``mu_cache`` dict shape) onto *entry*: the own matcher's MangaUpdates link and
    numbers, and the owner's Behind override - what the own matcher's knowledge and the state read."""
    entry.mu_id = cached.get("mu_id")
    entry.mu_title = cached.get("mu_title")
    entry.mu_url = cached.get("mu_url")
    entry.licensed = cached.get("licensed")
    entry.mu_confirmed = cached.get("mu_confirmed", False)
    entry.mu_associated = cached.get("mu_associated") or []
    entry.mu_score = float(cached.get("mu_score") or 0.0)
    entry.mu_score_version = int(cached.get("mu_score_version") or 1)
    entry.mu_band = cached.get("mu_band")
    entry.mu_reasons = list(cached.get("mu_reasons") or [])
    # Older caches lack the newer keys.
    entry.scan_latest_chapter = _opt_float(cached.get("scan_latest_chapter"))
    entry.publisher_name = cached.get("publisher_name")
    entry.publisher_chapters = _opt_float(cached.get("publisher_chapters"))
    entry.publisher_volumes = _opt_float(cached.get("publisher_volumes"))
    entry.publisher_status = cached.get("publisher_status")
    entry.scan_latest_volume = _opt_float(cached.get("scan_latest_volume"))
    entry.anilist_id = cached.get("anilist_id")
    entry.anilist_chapters = _opt_float(cached.get("anilist_chapters"))
    entry.anilist_volumes = _opt_float(cached.get("anilist_volumes"))
    co = cached.get("completed_in_origin")
    entry.completed_in_origin = None if co is None else bool(co)
    entry.behind_override = cached.get("behind_override") or None


def _opt_float(v) -> Optional[float]:
    # The links cache stores MangaUpdates' counts as REAL already (the entry's fields are floats); knowledge turns them
    # into exact decimals.
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load_links_cache(db) -> Dict[str, Dict[str, Any]]:
    """Every ``links_cache`` row of *db* as ``{absolute folder: dict}`` (``mu_cache.load_all``'s shape, but from the
    given database rather than the data folder's)."""
    from .mu_cache import _row_to_dict

    with db.connect() as con:
        rows = con.execute("SELECT * FROM links_cache").fetchall()
    return {row["folder"]: _row_to_dict(row) for row in rows}


def open_resolver(db):
    """A MangaPixer :class:`~mangalist.services.mangapixer.resolve.Resolver` over *db*'s cache (mappings and items
    read once)."""
    from .services.mangapixer import open_cache
    from .services.mangapixer.resolve import Resolver

    return Resolver(open_cache(db))


def mangapixer_resolution(resolver, root_id: Optional[int], root_path: Optional[str], folder, *,
                          resolved_root: Optional[Path] = None):
    """MangaPixer's :class:`~mangalist.services.mangapixer.resolve.Resolution` of *folder* (its own item or an
    ancestor's - a subfolder inherits), or None. *root_path* is the root's stored path; the folder is made relative to
    it resolved, as the scanner keys it (*resolved_root*: the caller resolved it already - :func:`resolved_roots` - so a
    table of 700 series does not resolve the same library path 700 times on a network share)."""
    if root_id is None or root_path is None or resolver is None:
        return None
    base = resolved_root if resolved_root is not None else Path(root_path).resolve()
    rel = Path(folder).resolve().relative_to(base).as_posix()
    return resolver.resolve(root_id, rel)


def item_of(res, own: Any = None) -> Optional[dict]:
    """The item of a resolution as it applies to the series *own* (its entry with the links applied, or its series row):
    None when there is none, or when it is inherited and the folder's own confirmed MangaUpdates link names another
    record (:func:`~mangalist.services.mangapixer.resolve.own_link_wins`; owner, 2026-10-18: a spin-off nested in its
    parent's folder read the parent's volumes and names)."""
    from .services.mangapixer.resolve import own_link_wins

    if res is None or own_link_wins(res, own):
        return None
    return res.item


def mangapixer_item(resolver, root_id: Optional[int], root_path: Optional[str], folder, *,
                    resolved_root: Optional[Path] = None, own: Any = None) -> Optional[dict]:
    """The MangaPixer export item that applies to *folder* (its own or an ancestor's - a subfolder inherits), or
    None (:func:`mangapixer_resolution`). *own*: the series' entry (or row) - an inherited item does not apply to a
    folder whose own confirmed MangaUpdates link names another record (:func:`item_of`)."""
    return item_of(mangapixer_resolution(resolver, root_id, root_path, folder, resolved_root=resolved_root), own)


def resolved_roots(db) -> Dict[int, Tuple[str, Path]]:
    """root id -> (its stored path, that path resolved): one roots read for a whole table."""
    out: Dict[int, Tuple[str, Path]] = {}
    for root in db.list_roots():
        try:
            out[root.id] = (root.path, Path(root.path).resolve())
        except OSError:                 # an unreachable path: resolved by the item lookup itself (and failing there)
            out[root.id] = (root.path, Path(root.path))
    return out


def mangapixer_known(db, folders: Iterable[Tuple[Optional[int], str]], own: Optional[Mapping[str, Any]] = None) -> set:
    """The *folders* (``(root id, folder)``) MangaPixer's export knows - each folder that has an item of its own or an
    ancestor's (*own*: folder -> its entry; an inherited item a folder's own confirmed link beats does not count,
    :func:`item_of`). One resolver and one roots read for all; runs off the UI thread (the MangaUpdates lookup asks it
    for the folders it would otherwise fetch)."""
    resolver = open_resolver(db)
    roots = resolved_roots(db)
    known = set()
    for root_id, folder in folders:
        root = roots.get(root_id) if root_id is not None else None
        if root is None:
            continue
        try:
            if mangapixer_item(resolver, root_id, root[0], folder, resolved_root=root[1],
                               own=(own or {}).get(str(folder))) is not None:
                known.add(str(folder))
        except Exception:  # noqa: BLE001 - a folder outside its root or a broken cache: not known
            _log.debug("MangaPixer item for %s unavailable", folder, exc_info=True)
    return known


def knowledge_of_item(item: Optional[Mapping[str, Any]]) -> Optional[SeriesKnowledge]:
    """MangaPixer's knowledge from its item, or None (the own matcher's applies)."""
    return from_mangapixer_item(item) if item is not None else None


def inventory_of(entry: MangaEntry, item: Optional[Mapping[str, Any]]):
    """The series' held units in the state's shape, merged with MangaPixer's volume list (which chapters each volume
    collects) when MangaPixer knows the series - not for a folder of a declared edition (an omnibus / master edition):
    the list is the regular edition's, and its chapter ranges are not what the edition's volumes hold."""
    from .inventory import as_state_inventory

    item = item or {}
    if declared_edition_of(item)[1] is not None:
        return as_state_inventory(entry.inventory(None))
    volumes = item.get("volumes") if isinstance(item.get("volumes"), dict) else None
    volume_list = volumes.get("items") if volumes else None
    return as_state_inventory(entry.inventory(volume_list or None))


def evaluate(entry: MangaEntry, *, knowledge: Optional[SeriesKnowledge], inventory: Any, needs_kind: bool,
             rename_count: int = 0, today: Optional[_dt.date] = None,
             unbundled: Any = False) -> Tuple[SeriesState, SeriesKnowledge]:
    """The series' state as the List shows it, and the knowledge behind it (*knowledge* None: the own matcher's;
    *inventory* None: read from the entry's file names; *unbundled*: the series' Unbundled Chapters setting,
    :mod:`mangalist.unbundled`)."""
    knowledge = knowledge or from_own_matcher(entry)
    if inventory is None:
        inventory = fallback_inventory_from_entry(entry)
    st = compute_state(inventory, knowledge, folder_empty=entry.n_files == 0, needs_kind=needs_kind,
                       rename_count=rename_count, behind_override=entry.behind_override, today=today,
                       restarts=entry.restarting_units, unbundled=unbundled)
    return st, knowledge


def held_volumes(inventory: Any) -> Tuple[str, ...]:
    """The volume numbers held as volume archives (exact decimal strings, ascending, no repeats)."""
    nums = [d for d in (to_decimal(v) for v in inventory.held_volumes) if d is not None]
    return tuple(fmt_num(d) for d in sorted(set(nums)))


def series_wanted(entry: MangaEntry, state: Optional[SeriesState], knowledge: Optional[SeriesKnowledge],
                  held: Sequence[str], series_id: Optional[int], *, downloads: bool = True) -> List[WantedSeries]:
    """What one series adds to "To get" (nothing without gaps). With *downloads* on, volumes and upgrades are
    findable by the nyaa rule (:func:`~mangalist.gui.volumes_target.find_volumes_availability`); off: "downloads are
    off".

    A series the owner marked up to date (the List's Behind override "done") adds nothing either (owner, 2026-10-15:
    "Mark Behind as up to date" also takes the series out of To get) - the List still shows its gaps, and clearing the
    override in the List brings it back."""
    if entry is None or state is None or not state.gaps:
        return []
    if getattr(entry, "behind_override", None) == "done":
        return []
    volumes = None
    by_hand = False
    if knowledge is not None and knowledge.edition_label and state.missing_volumes:
        # A declared edition's volumes are numbered by that edition, nyaa's releases mostly by the regular one: a
        # search would take regular volume 3 for the edition's volume 3. Shown in To get, never searched automatically
        # (nor by the find pass or a bulk search) - but the owner may search by hand in the Download tab and judge the
        # releases himself (owner, 2026-10-15: "Allow a search by hand in the Download tab?" - "Yes").
        volumes = Availability(False, f"{knowledge.edition_label}: nyaa releases are matched by volume number, and "
                                      "this edition numbers its volumes its own way - not searched automatically")
        by_hand = downloads
    elif downloads and (state.missing_volumes or state.upgrade_volumes):
        volumes = find_volumes_availability(series_id=series_id, folder=str(entry.folder), title=entry.title,
                                            english_title=entry.english_title, knowledge=knowledge, state=state,
                                            held=held)
    items = wanted_series(folder=str(entry.folder), title=entry.title, english_title=entry.english_title, state=state,
                          knowledge=knowledge, held=held, series_id=series_id, volumes=volumes)
    if by_hand:
        from dataclasses import replace

        from .gui.shell import GROUP_UPGRADES, GROUP_VOLUMES

        items = [replace(w, by_hand=True) if w.group in (GROUP_VOLUMES, GROUP_UPGRADES) and not w.findable else w
                 for w in items]
    return items


# --- the series from the database --------------------------------------------------------------------------------


def _resolved(path: str) -> Path:
    try:
        return Path(path).resolve()
    except OSError:
        return Path(path)


def _under(rel: str, folder_rel: str) -> bool:
    return rel.startswith(folder_rel + "/")


def _walk_order(rel: str):
    # The scanner's os.walk order: a folder's files before its subfolders' (each level sorted case-insensitively).
    parts = rel.split("/")
    return tuple((0, p.casefold(), p) for p in parts[:-1]) + ((-1, parts[-1].casefold(), parts[-1]),)


def entries_from_db(db, root) -> List[Tuple[Any, MangaEntry]]:
    """``(series row, MangaEntry)`` for every present series of *root* (a ``store.Root``), rebuilt from the last scan's
    records: the folder under the resolved root (the scanner's key), the title from the folder name, the series' own
    present archives parsed with the scanner's context - and, as the scanner does, a franchise parent's nested series'
    archives in its file count but not in its inventory (``nested_series``). No disk access beyond resolving the root."""
    from .classifier import annotate_file, parse_folder_name
    from .scanner import MAX_DEPTH, _parse_context, _root_schemes

    root_dir = _resolved(root.path)
    rows = [s for s in db.list_series(root.id) if s.status == "present"]
    archives: Dict[int, List[Any]] = defaultdict(list)
    for a in db.list_archives(root_id=root.id, status="present"):
        if a.series_id is not None:
            archives[a.series_id].append(a)
    by_top: Dict[str, List[Any]] = defaultdict(list)       # a nested series lies in the same top-level folder
    for s in rows:
        by_top[s.rel_path.split("/", 1)[0]].append(s)
    schemes = _root_schemes(root)
    out: List[Tuple[Any, MangaEntry]] = []
    for row in rows:
        rel = row.rel_path
        folder = root_dir.joinpath(*rel.split("/"))
        title, eng = parse_folder_name(folder.name)
        entry = MangaEntry(folder=folder, title=title, english_title=eng, root_id=root.id)
        entry.kind_hint = row.kind_hint
        ctx = _parse_context(title, row.kind_hint, schemes)
        nested = [s for s in by_top[rel.split("/", 1)[0]] if s.id != row.id and _under(s.rel_path, rel)]
        own: List[Tuple[str, Any, bool]] = []
        for a in archives.get(row.id, ()):
            if _under(a.rel_path, rel):
                own.append((a.rel_path[len(rel) + 1:], a, True))
        for s in nested:                # counted, never parsed or held (inventory_files leaves them out)
            for a in archives.get(s.id, ()):
                inner = a.rel_path[len(rel) + 1:]
                if inner.count("/") <= MAX_DEPTH:
                    own.append((inner, a, False))
        files: List[FileHit] = []
        for inner, a, parse in sorted(own, key=lambda t: _walk_order(t[0])):
            hit = FileHit(path=folder.joinpath(*inner.split("/")), size=int(a.size or 0), depth=inner.count("/"),
                          mtime_ns=a.mtime_ns)
            if parse:
                annotate_file(hit, ctx)
            files.append(hit)
        entry.files = files
        entry.nested_series = [root_dir.joinpath(*s.rel_path.split("/")) for s in nested]
        out.append((row, entry))
    return out


# --- the list ----------------------------------------------------------------------------------------------------


@dataclass
class SeriesView:
    """One present series as the List evaluates it (what :func:`wanted_list` is built from)."""

    row: Any                            # the ``series`` row (``store.Series``)
    entry: MangaEntry
    state: SeriesState
    knowledge: SeriesKnowledge
    held: Tuple[str, ...]               # volumes held, exact decimal strings


def library_states(db, *, root_ids: Optional[Iterable[int]] = None,
                   today: Optional[_dt.date] = None) -> List[SeriesView]:
    """Every present series of *root_ids* (all roots when None) with its state, in the List's order (root by root,
    series folders by name). A broken MangaPixer cache leaves every series on the own matcher's knowledge, as in the
    window; a root whose records cannot be read is left out (both logged). The series with Unbundled Chapters on
    (:mod:`mangalist.unbundled`, read once) also want the chapters after their last volume."""
    wanted_ids = None if root_ids is None else set(root_ids)
    roots = [r for r in db.list_roots() if wanted_ids is None or r.id in wanted_ids]
    try:
        links = load_links_cache(db)
    except Exception:  # noqa: BLE001 - without the links every own-matcher series is "not matched": no gaps invented
        _log.warning("Wanted list: the MangaUpdates links could not be read", exc_info=True)
        links = {}
    try:
        resolver = open_resolver(db)
    except Exception:  # noqa: BLE001 - a broken MangaPixer cache must not stop the list
        _log.warning("Wanted list: MangaPixer's data unavailable; the own matcher's knowledge is used", exc_info=True)
        resolver = None
    unbundled = unbundled_choices(db)
    out: List[SeriesView] = []
    for root in roots:
        try:
            pairs = entries_from_db(db, root)
        except Exception:  # noqa: BLE001 - one unreadable root must not hide the others' gaps
            _log.warning("Wanted list: the series of %s could not be read", root.name, exc_info=True)
            continue
        pairs.sort(key=lambda p: _list_order(p[0].rel_path))
        for row, entry in pairs:
            cached = links.get(str(entry.folder))
            if cached:
                apply_links_cache(entry, cached)
            item = _safe_item(resolver, root, entry)
            inventory = inventory_of(entry, item)
            state, knowledge = evaluate(entry, knowledge=knowledge_of_item(item), inventory=inventory,
                                        needs_kind=entry.needs_kind, today=today,
                                        unbundled=unbundled.get(row.id) or False)
            out.append(SeriesView(row, entry, state, knowledge, held_volumes(inventory)))
    return out


def wanted_list(db, *, root_ids: Optional[Iterable[int]] = None, today: Optional[_dt.date] = None,
                downloads: bool = True) -> List[WantedSeries]:
    """The "To get" entries of every present series of *root_ids* (all roots when None), in the List's order: the
    same states and gaps the List shows and the same ``findable`` / ``reason`` the Download tab shows with downloads on
    (*downloads* False: as with downloads off). ``series_id`` is always set."""
    started = time.monotonic()
    views = library_states(db, root_ids=root_ids, today=today)
    out: List[WantedSeries] = []
    for v in views:
        out += series_wanted(v.entry, v.state, v.knowledge, v.held, v.row.id, downloads=downloads)
    _log.info("Wanted list: %d entr%s for %d series with gaps, of %d series (%.2f s)", len(out),
              "y" if len(out) == 1 else "ies", len({w.series_id for w in out}), len(views), time.monotonic() - started)
    return out


def _list_order(rel: str):
    # scan_root: the root's children by name (lower case); a franchise parent's entry before its subseries.
    top, _, rest = rel.partition("/")
    return (top.lower(), rest.casefold(), rest)


def _safe_item(resolver, root, entry: MangaEntry) -> Optional[dict]:
    try:
        return mangapixer_item(resolver, root.id, root.path, entry.folder, own=entry)
    except Exception:  # noqa: BLE001 - as the window: a broken MangaPixer cache must not break the list
        _log.warning("MangaPixer data for %s unavailable", os.path.basename(str(entry.folder)), exc_info=True)
        return None


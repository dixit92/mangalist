"""A series nested in another series' folder is not searched by that series' names (the find pass). No Qt here.

Owner, 2026-10-18: the spin-off ``Toaru Kagaku no Railgun [A Certain Scientific Railgun]/Toaru Kagaku no Railgun Gaiden
- Astral Buddy`` was searched on nyaa by its folder name, then by ``Toaru Kagaku no Railgun`` and ``A Certain Scientific
Railgun`` - the parent's names, which it had from the parent folder's inherited MangaPixer item. nyaa's series match is
exact (:func:`mangalist.services.nyaa.ranking.matches_series`), so ``A Certain Scientific Railgun v01-20`` WAS the series
for those names, and the find pass sent the parent's pack into the spin-off's folder. The inheritance itself is fixed
where the knowledge is read (:func:`mangalist.services.mangapixer.resolve.own_link_wins`); this is the guard behind it,
for any way a nested series may come to carry its parent's names (an inherited item it has no own link against, a
MangaUpdates record listing the parent's name among its associated names).

**The rule** (:func:`search_titles`): a name of the series that equals - after nyaa's own comparison (the forms of
:func:`~mangalist.services.nyaa.ranking.series_forms`: case, punctuation, spacing, a leading "The") - a name of a
folder its folder is in (:func:`enclosing_names`) is not searched, and so no release named so is taken for it; the
series' own folder names (its title and its ``[English]`` part) are always kept (``Series/Series`` stays searchable). A
series not nested in another keeps every name: an English title that is a prefix of the longer romaji one
(``Re:Example`` / ``Re:Example kara ...``) is the same series and nothing here touches it.

**The names of an enclosing folder**: its folder name (title and ``[English]`` part), its MangaUpdates link in the links
cache (title and associated names) and MangaPixer's record for it (its own item or the one it inherits: title and
alternatives). Read from the database only - no network, no disk walk.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, List, Optional, Sequence, Tuple

_log = logging.getLogger(__name__)


def _resolved(path: str) -> Path:
    try:
        return Path(path).resolve()
    except OSError:
        return Path(path)


def _series_row(db, series_id: int):
    from ..store.series import _row

    with db.connect() as con:
        r = con.execute("SELECT * FROM series WHERE id = ?", (int(series_id),)).fetchone()
    return _row(r) if r is not None else None


def _own_names(rel_path: str) -> Tuple[str, ...]:
    from ..classifier import parse_folder_name

    title, english = parse_folder_name(rel_path.rsplit("/", 1)[-1])
    return tuple(n for n in (title, english) if n)


def _links_names(db, folder: Path) -> List[str]:
    with db.connect() as con:
        r = con.execute("SELECT mu_title, mu_associated FROM links_cache WHERE folder = ?", (str(folder),)).fetchone()
    if r is None:
        return []
    out = [r["mu_title"]] if r["mu_title"] else []
    try:
        associated = json.loads(r["mu_associated"] or "[]")
    except (TypeError, ValueError):
        associated = []
    return out + [a for a in associated if isinstance(a, str)]


def _mangapixer_names(resolver, root_id: int, rel_path: str) -> List[str]:
    if resolver is None:
        return []
    res = resolver.resolve(root_id, rel_path)
    record = res.record if res is not None else None
    if not isinstance(record, dict):
        return []
    names = [record.get("title")] + list(record.get("altTitles") or ())
    return [n for n in names if isinstance(n, str)]


def enclosing_names(db, series_id: int) -> Tuple[str, ...]:
    """The names of every folder (inside its library folder) that holds series *series_id*'s folder - see the module
    docstring; ``()`` for a series at the top of its library folder or one the database does not know."""
    row = _series_row(db, series_id)
    if row is None or "/" not in row.rel_path:
        return ()
    root = db.get_root(row.root_id)
    if root is None:
        return ()
    try:
        from ..wanted import open_resolver

        resolver = open_resolver(db)
    except Exception:  # noqa: BLE001 - no MangaPixer data: the folder names and the links still count
        _log.debug("Spin-offs: MangaPixer's data unavailable", exc_info=True)
        resolver = None
    base = _resolved(root.path)
    parts = row.rel_path.split("/")
    out: List[str] = []
    for n in range(1, len(parts)):
        rel = "/".join(parts[:n])
        out += _own_names(rel)
        out += _links_names(db, base.joinpath(*parts[:n]))
        try:
            out += _mangapixer_names(resolver, root.id, rel)
        except Exception:  # noqa: BLE001 - a broken cache: the other names still count
            _log.debug("Spin-offs: no MangaPixer item for %s", rel, exc_info=True)
    seen, unique = set(), []
    for name in (n.strip() for n in out):
        if name and name.casefold() not in seen:
            seen.add(name.casefold())
            unique.append(name)
    return tuple(unique)


def search_titles(db, series_id: Optional[int], titles: Sequence[str], *, label: Any = None) -> Tuple[str, ...]:
    """*titles* (the series' search names, in order) without the names of the folders its folder is in, its own folder
    names kept (see the module docstring). Each name left out is logged (*label*: how the log names the series)."""
    titles = tuple(titles)
    if series_id is None:
        return titles
    try:
        outer = enclosing_names(db, int(series_id))
        row = _series_row(db, int(series_id)) if outer else None
    except Exception:  # noqa: BLE001 - the database could not be read: the titles stay as given (as before)
        _log.warning("Spin-offs: the folders holding series %s could not be read", series_id, exc_info=True)
        return titles
    if not outer or row is None:
        return titles
    from ..services.nyaa.ranking import series_forms

    taken = series_forms(outer) - series_forms(_own_names(row.rel_path))
    kept = tuple(t for t in titles if not (series_forms([t]) & taken))
    dropped = [t for t in titles if t not in kept]
    if dropped:
        _log.info("%s: not searched by %d name%s of the series its folder is in (%s)", label or f"series {series_id}",
                  len(dropped), "" if len(dropped) == 1 else "s", ", ".join(repr(t) for t in dropped))
    return kept


__all__ = ["enclosing_names", "search_titles"]

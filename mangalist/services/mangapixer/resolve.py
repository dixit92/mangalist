"""The effective MangaPixer item of a MangaList series folder - the interface the states lane consumes.

Interface
---------

``resolve(cache, root_id, rel_path) -> Optional[Resolution]`` (one folder), or for many folders
``Resolver(cache).resolve(root_id, rel_path)`` (loads each library's items once).

- *cache*: :class:`mangalist.store.mangapixer.MangaPixerCache` (``MangaPixerCache(store.get_store())``).
- *root_id*: the MangaList root's id; *rel_path*: the series folder relative to the root, ``/``
  separators (the ``series.rel_path`` column).

Returns ``None`` when the root has no usable mapping (none, the owner's "do not use MangaPixer", a
library MangaPixer no longer lists, or a kind skipped by default without the override) or when neither
the folder nor any ancestor has an item. Otherwise a :class:`Resolution`:

=================  ===========================================================================================
``item``           the MangaPixer export item, the JSON exactly as MangaPixer 1.33.0 sent it (``schemaVersion`` 1,
                   ``docs/metadata-export.md``: ``nodeId, nodeKind, carriedFrom, trail, updatedAt, link{state,
                   method, score, updatedAt}, record, companions, officialLinks, volumes, completion, refresh``).
                   Optional blocks may be ``null``; unknown fields may appear - ignore them.
``record``         ``item["record"]`` when its provider is ``mangaupdates``, else ``None`` (other providers, e.g.
                   ``gcd``, are ignored). Always ``None`` for NeedsReview / DontMatch. Use this, not
                   ``item["record"]``.
``link_state``     ``Confirmed`` | ``Auto`` | ``NeedsReview`` | ``DontMatch`` (the effective state of the folder).
``inherited``      ``False``: the folder's own item. ``True``: the nearest ancestor's item (franchise subfolders).
``source_trail``   the trail of the folder that carries the item (inside the library).
``library_id``     the MangaPixer library; ``node_id``: the item's node.
``match``          ``exact`` or ``casefold`` (the case-insensitive fallback found it).
=================  ===========================================================================================

Inheritance follows MangaPixer's resolver: the folder's own item wins; otherwise the NEAREST ancestor
that has an item (inside the root, then up through the mapping's trail prefix). A DontMatch row stops
inheritance: a folder below a DontMatch folder resolves to that DontMatch item (``inherited=True``,
``link_state="DontMatch"``), never to a link further up. Meaning for MangaList (owner decisions):
Confirmed / Auto -> use the record and fetch nothing itself; NeedsReview -> "Needs review in
MangaPixer", no own matching; DontMatch -> not matched, never auto-matched by MangaList; ``None`` ->
MangaList's own matcher.

Archive items are never in the cache (series only), so they never take part. No Qt here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ...store.mangapixer import MangaPixerCache, fold_key, nfc, trail_key
from .client import usable_record

LINK_STATES = ("Confirmed", "Auto", "NeedsReview", "DontMatch")


@dataclass(frozen=True)
class Resolution:
    item: Dict[str, Any]
    link_state: Optional[str]
    inherited: bool
    library_id: str
    node_id: str
    source_trail: Tuple[str, ...]
    match: str = "exact"

    @property
    def record(self) -> Optional[Dict[str, Any]]:
        return usable_record(self.item)

    @property
    def dont_match(self) -> bool:
        return self.link_state == "DontMatch"


class LibraryIndex:
    """One library's folder items by trail (exact NFC key and casefolded key)."""

    def __init__(self, library_id: str, rows):
        self.library_id = library_id
        self.exact: Dict[str, Any] = {}
        self.folded: Dict[str, Any] = {}
        for row in rows:
            self.exact[trail_key(row.trail)] = row
            self.folded.setdefault(fold_key(row.trail), row)

    @classmethod
    def load(cls, cache: MangaPixerCache, library_id: str) -> "LibraryIndex":
        return cls(library_id, cache.items(library_id))

    def _find(self, parts: Sequence[str]):
        row = self.exact.get(trail_key(parts))
        if row is not None:
            return row, "exact"
        row = self.folded.get(fold_key(parts))
        if row is not None:
            return row, "casefold"
        return None, ""

    def lookup(self, prefix: Sequence[str], rel_path: str) -> Optional[Resolution]:
        rel = [nfc(p) for p in str(rel_path).replace("\\", "/").split("/") if p and p != "."]
        full = [nfc(p) for p in prefix] + rel
        if not rel:
            return None
        for n in range(len(full), 0, -1):          # the folder itself, then the nearest ancestor up
            row, how = self._find(full[:n])
            if row is not None:
                link = row.item.get("link") if isinstance(row.item.get("link"), dict) else {}
                return Resolution(item=row.item, link_state=link.get("state") or row.link_state,
                                  inherited=n < len(full), library_id=self.library_id, node_id=row.node_id,
                                  source_trail=tuple(row.trail), match=how)
        return None


class Resolver:
    """Resolves many folders; reads the mappings once and each library's items once."""

    def __init__(self, cache: MangaPixerCache):
        self.cache = cache
        self._mappings = cache.mappings()
        self._libraries = {lib.id: lib for lib in cache.libraries()}
        self._indexes: Dict[str, LibraryIndex] = {}

    def mapping_for(self, root_id: int):
        """The root's usable mapping (library present, kind allowed), else None."""
        m = self._mappings.get(root_id)
        if m is None or not m.library_id:
            return None
        lib = self._libraries.get(m.library_id)
        if lib is None or not lib.present:
            return None
        if not lib.default_kind and not m.any_kind:
            return None
        return m

    def resolve(self, root_id: int, rel_path: str) -> Optional[Resolution]:
        m = self.mapping_for(root_id)
        if m is None:
            return None
        index = self._indexes.get(m.library_id)
        if index is None:
            index = self._indexes[m.library_id] = LibraryIndex.load(self.cache, m.library_id)
        return index.lookup(m.prefix, rel_path)

    def resolve_many(self, root_id: int, rel_paths: Sequence[str]) -> Dict[str, Optional[Resolution]]:
        return {rel: self.resolve(root_id, rel) for rel in rel_paths}


def resolve(cache: MangaPixerCache, root_id: int, rel_path: str) -> Optional[Resolution]:
    """The effective MangaPixer item of one MangaList series folder (see the module docstring)."""
    return Resolver(cache).resolve(root_id, rel_path)


__all__: List[str] = ["Resolution", "Resolver", "LibraryIndex", "resolve", "LINK_STATES"]

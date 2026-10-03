"""The MangaPixer source's tables in the library database (schema migration 2).

- ``mangapixer_connection``: the server URL, the token, the TLS choice. The token is kept here and not
  in ``settings``, because ``config.load()`` / ``config.save()`` copy every settings key around; it is
  never logged and never returned by :meth:`MangaPixerCache.connection` (only :meth:`MangaPixerCache.token`
  hands it to the client).
- ``mangapixer_libraries``: what ``/api/v1/export/libraries`` lists.
- ``mangapixer_items``: the export's FOLDER items keyed by (library, nodeId), with the trail and the
  item JSON exactly as MangaPixer sent it.
- ``mangapixer_sync``: per library, the first page's ``serverTime`` of the last complete sync.
- ``mangapixer_mappings``: MangaList root -> MangaPixer library + trail prefix (automatic or manual).

:class:`MangaPixerCache` wraps a :class:`~mangalist.store.Store` (it only uses ``store.connect()``),
so the store class itself is unchanged. No Qt here.
"""

from __future__ import annotations

import json
import sqlite3
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .db import utcnow

# Library kinds MangaList maps by default (``None``: the library declares no kind). Every other kind
# (comic, graphic-novel, novel, anything new) is skipped unless a mapping overrides it.
DEFAULT_KINDS = frozenset({"manga", "manhwa", "manhua", "webtoon", None})


def kind_is_default(kind: Optional[str]) -> bool:
    return (kind or None) in DEFAULT_KINDS


def nfc(name: str) -> str:
    return unicodedata.normalize("NFC", str(name))


def trail_key(parts: Sequence[str]) -> str:
    """The lookup key of a trail: NFC names joined with ``/`` (no on-disk name holds a ``/``)."""
    return "/".join(nfc(p) for p in parts)


def fold_key(parts: Sequence[str]) -> str:
    """The case-insensitive fallback key (NFC, then casefold, then NFC again)."""
    return nfc(trail_key(parts).casefold())


@dataclass
class Connection:
    """The connection settings WITHOUT the token (``has_token`` says whether one is stored)."""

    base_url: str = ""
    has_token: bool = False
    verify_tls: bool = True
    ca_file: Optional[str] = None
    token_rejected_at: Optional[str] = None

    @property
    def verify(self):
        """The ``requests`` verify value: a CA file, else True / False."""
        if self.ca_file:
            return self.ca_file
        return bool(self.verify_tls)

    def __repr__(self) -> str:  # no token here anyway; keep it short
        return (f"Connection(base_url={self.base_url!r}, has_token={self.has_token}, "
                f"verify_tls={self.verify_tls}, ca_file={self.ca_file!r})")


@dataclass
class LibraryRow:
    id: str
    display_name: str
    kind: Optional[str] = None
    folder_count: Optional[int] = None
    item_count: Optional[int] = None
    last_scan_at: Optional[str] = None
    present: bool = True
    position: int = 0

    @property
    def default_kind(self) -> bool:
        return kind_is_default(self.kind)


@dataclass
class SyncState:
    library_id: str
    server_time: Optional[str] = None
    last_full_at: Optional[str] = None
    last_sync_at: Optional[str] = None
    last_status: Optional[str] = None
    last_error: Optional[str] = None
    last_mode: Optional[str] = None


@dataclass
class Mapping:
    root_id: int
    library_id: Optional[str]
    prefix: List[str] = field(default_factory=list)
    manual: bool = False
    any_kind: bool = False
    matched: Optional[int] = None
    unmatched: Optional[int] = None
    updated_at: Optional[str] = None


@dataclass
class ItemRow:
    library_id: str
    node_id: str
    trail: List[str]
    link_state: Optional[str]
    item: Dict[str, Any]


@dataclass
class PageApplied:
    upserted: int = 0
    removed: int = 0
    rekeyed: List[Tuple[str, str]] = field(default_factory=list)  # (old nodeId, new nodeId)


def _lib(r: sqlite3.Row) -> LibraryRow:
    return LibraryRow(id=r["id"], display_name=r["display_name"], kind=r["kind"], folder_count=r["folder_count"],
                      item_count=r["item_count"], last_scan_at=r["last_scan_at"], present=bool(r["present"]),
                      position=r["position"])


def _mapping(r: sqlite3.Row) -> Mapping:
    try:
        prefix = [str(p) for p in json.loads(r["prefix"] or "[]")]
    except (TypeError, ValueError):
        prefix = []
    return Mapping(root_id=r["root_id"], library_id=r["library_id"], prefix=prefix, manual=bool(r["manual"]),
                   any_kind=bool(r["any_kind"]), matched=r["matched"], unmatched=r["unmatched"],
                   updated_at=r["updated_at"])


def _int_or_none(value: Any) -> Optional[int]:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


class MangaPixerCache:
    """The MangaPixer tables of *store* (a :class:`~mangalist.store.Store`)."""

    def __init__(self, store):
        self.store = store

    def connect(self):
        return self.store.connect()

    # --- connection ----------------------------------------------------------------------------------

    def connection(self) -> Connection:
        with self.connect() as con:
            r = con.execute("SELECT * FROM mangapixer_connection WHERE id = 1").fetchone()
        if r is None:
            return Connection()
        return Connection(base_url=r["base_url"] or "", has_token=bool(r["token"]),
                          verify_tls=bool(r["verify_tls"]), ca_file=r["ca_file"] or None,
                          token_rejected_at=r["token_rejected_at"])

    def token(self) -> Optional[str]:
        """The stored token, for the client only. Never log or display it."""
        with self.connect() as con:
            r = con.execute("SELECT token FROM mangapixer_connection WHERE id = 1").fetchone()
        return (r["token"] or None) if r is not None else None

    _KEEP = object()

    def set_connection(self, base_url: Optional[str] = None, token: Any = _KEEP,
                       verify_tls: Optional[bool] = None, ca_file: Any = _KEEP) -> Connection:
        """Change the connection. *token*: a new token, ``None`` to forget it, or left out to keep it.
        A new token (or a changed URL) clears the "token rejected" stop."""
        now = utcnow()
        with self.connect() as con:
            r = con.execute("SELECT * FROM mangapixer_connection WHERE id = 1").fetchone()
            cur = dict(r) if r is not None else {"base_url": "", "token": None, "verify_tls": 1, "ca_file": None,
                                                  "token_rejected_at": None}
            rejected = cur["token_rejected_at"]
            if base_url is not None and base_url != cur["base_url"]:
                cur["base_url"] = base_url
                rejected = None
            if token is not self._KEEP:
                cur["token"] = (str(token).strip() or None) if token is not None else None
                rejected = None
            if verify_tls is not None:
                cur["verify_tls"] = 1 if verify_tls else 0
            if ca_file is not self._KEEP:
                cur["ca_file"] = (str(ca_file).strip() or None) if ca_file else None
            con.execute(
                "INSERT INTO mangapixer_connection (id, base_url, token, verify_tls, ca_file, token_rejected_at,"
                " updated_at) VALUES (1,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET base_url=excluded.base_url,"
                " token=excluded.token, verify_tls=excluded.verify_tls, ca_file=excluded.ca_file,"
                " token_rejected_at=excluded.token_rejected_at, updated_at=excluded.updated_at",
                (cur["base_url"], cur["token"], cur["verify_tls"], cur["ca_file"], rejected, now))
        return self.connection()

    def mark_token_rejected(self, rejected: bool = True) -> None:
        """HTTP 401: remember it, so no scheduled sync tries the same token again."""
        with self.connect() as con:
            con.execute("UPDATE mangapixer_connection SET token_rejected_at = ?, updated_at = ? WHERE id = 1",
                        (utcnow() if rejected else None, utcnow()))

    # --- libraries -----------------------------------------------------------------------------------

    def save_libraries(self, libraries: Iterable[Any]) -> List[LibraryRow]:
        """Record the libraries MangaPixer lists now (objects with ``id``, ``display_name``, ``kind``,
        ``folder_count``, ``item_count``, ``last_scan_at``). Libraries no longer listed are kept, marked
        not present (their cache stays until the owner re-maps)."""
        now = utcnow()
        libs = list(libraries)
        with self.connect() as con:
            con.execute("UPDATE mangapixer_libraries SET present = 0")
            for pos, lib in enumerate(libs):
                con.execute(
                    "INSERT INTO mangapixer_libraries (id, display_name, kind, folder_count, item_count, last_scan_at,"
                    " present, position, seen_at) VALUES (?,?,?,?,?,?,1,?,?) ON CONFLICT(id) DO UPDATE SET"
                    " display_name=excluded.display_name, kind=excluded.kind, folder_count=excluded.folder_count,"
                    " item_count=excluded.item_count, last_scan_at=excluded.last_scan_at, present=1,"
                    " position=excluded.position, seen_at=excluded.seen_at",
                    (str(lib.id), str(lib.display_name or lib.id), lib.kind or None, _int_or_none(lib.folder_count),
                     _int_or_none(lib.item_count), lib.last_scan_at, pos, now))
        return self.libraries()

    def libraries(self, present_only: bool = False) -> List[LibraryRow]:
        sql = "SELECT * FROM mangapixer_libraries"
        if present_only:
            sql += " WHERE present = 1"
        with self.connect() as con:
            return [_lib(r) for r in con.execute(sql + " ORDER BY present DESC, position, id")]

    def library(self, library_id: str) -> Optional[LibraryRow]:
        with self.connect() as con:
            r = con.execute("SELECT * FROM mangapixer_libraries WHERE id = ?", (library_id,)).fetchone()
        return _lib(r) if r else None

    def mark_library_gone(self, library_id: str) -> None:
        with self.connect() as con:
            con.execute("UPDATE mangapixer_libraries SET present = 0 WHERE id = ?", (library_id,))

    # --- items ---------------------------------------------------------------------------------------

    def apply_page(self, library_id: str, removed: Iterable[str], items: Iterable[Dict[str, Any]]) -> PageApplied:
        """One export page in one transaction: the removals first, then the items as upserts. An item
        with ``carriedFrom`` takes over the old node's row (re-key), it is not a remove + add. *items*
        must already be the folder items to keep (the sync filters archives out)."""
        out = PageApplied()
        now = utcnow()
        with self.connect() as con:
            for node_id in removed:
                cur = con.execute("DELETE FROM mangapixer_items WHERE library_id = ? AND node_id = ?",
                                  (library_id, str(node_id)))
                out.removed += cur.rowcount
            for item in items:
                node_id = str(item["nodeId"])
                old = item.get("carriedFrom")
                if old and str(old) != node_id:
                    cur = con.execute("DELETE FROM mangapixer_items WHERE library_id = ? AND node_id = ?",
                                      (library_id, str(old)))
                    if cur.rowcount:
                        out.rekeyed.append((str(old), node_id))
                trail = [str(p) for p in (item.get("trail") or [])]
                link = item.get("link") if isinstance(item.get("link"), dict) else {}
                con.execute(
                    "INSERT INTO mangapixer_items (library_id, node_id, trail, trail_key, trail_fold, link_state,"
                    " updated_at, item, synced_at) VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(library_id, node_id)"
                    " DO UPDATE SET trail=excluded.trail, trail_key=excluded.trail_key,"
                    " trail_fold=excluded.trail_fold, link_state=excluded.link_state,"
                    " updated_at=excluded.updated_at, item=excluded.item, synced_at=excluded.synced_at",
                    (library_id, node_id, json.dumps(trail, ensure_ascii=False), trail_key(trail), fold_key(trail),
                     link.get("state"), item.get("updatedAt"), json.dumps(item, ensure_ascii=False), now))
                out.upserted += 1
        return out

    def keep_only(self, library_id: str, node_ids: Iterable[str]) -> int:
        """After a full sync: drop the library's rows whose node the export no longer has."""
        keep = sorted({str(n) for n in node_ids})
        with self.connect() as con:
            con.execute("CREATE TEMP TABLE IF NOT EXISTS mp_keep (node_id TEXT PRIMARY KEY)")
            con.execute("DELETE FROM mp_keep")
            con.executemany("INSERT OR IGNORE INTO mp_keep (node_id) VALUES (?)", [(n,) for n in keep])
            cur = con.execute("DELETE FROM mangapixer_items WHERE library_id = ? AND node_id NOT IN"
                              " (SELECT node_id FROM mp_keep)", (library_id,))
            con.execute("DELETE FROM mp_keep")
            return cur.rowcount

    def items(self, library_id: str) -> List[ItemRow]:
        with self.connect() as con:
            rows = con.execute("SELECT node_id, trail, link_state, item FROM mangapixer_items WHERE library_id = ?"
                               " ORDER BY trail_key", (library_id,)).fetchall()
        out = []
        for r in rows:
            out.append(ItemRow(library_id=library_id, node_id=r["node_id"], trail=json.loads(r["trail"]),
                               link_state=r["link_state"], item=json.loads(r["item"])))
        return out

    def item(self, library_id: str, node_id: str) -> Optional[Dict[str, Any]]:
        with self.connect() as con:
            r = con.execute("SELECT item FROM mangapixer_items WHERE library_id = ? AND node_id = ?",
                            (library_id, node_id)).fetchone()
        return json.loads(r["item"]) if r else None

    def item_count(self, library_id: Optional[str] = None) -> int:
        with self.connect() as con:
            if library_id is None:
                return int(con.execute("SELECT COUNT(*) FROM mangapixer_items").fetchone()[0])
            return int(con.execute("SELECT COUNT(*) FROM mangapixer_items WHERE library_id = ?",
                                   (library_id,)).fetchone()[0])

    # --- sync state ----------------------------------------------------------------------------------

    def sync_state(self, library_id: str) -> SyncState:
        with self.connect() as con:
            r = con.execute("SELECT * FROM mangapixer_sync WHERE library_id = ?", (library_id,)).fetchone()
        if r is None:
            return SyncState(library_id)
        return SyncState(library_id=library_id, server_time=r["server_time"], last_full_at=r["last_full_at"],
                         last_sync_at=r["last_sync_at"], last_status=r["last_status"], last_error=r["last_error"],
                         last_mode=r["last_mode"])

    def save_sync_state(self, state: SyncState) -> None:
        with self.connect() as con:
            con.execute(
                "INSERT INTO mangapixer_sync (library_id, server_time, last_full_at, last_sync_at, last_status,"
                " last_error, last_mode) VALUES (?,?,?,?,?,?,?) ON CONFLICT(library_id) DO UPDATE SET"
                " server_time=excluded.server_time, last_full_at=excluded.last_full_at,"
                " last_sync_at=excluded.last_sync_at, last_status=excluded.last_status,"
                " last_error=excluded.last_error, last_mode=excluded.last_mode",
                (state.library_id, state.server_time, state.last_full_at, state.last_sync_at, state.last_status,
                 state.last_error, state.last_mode))

    def last_sync_at(self) -> Optional[str]:
        """The most recent successful library sync (MangaList's clock), or None."""
        with self.connect() as con:
            r = con.execute("SELECT MAX(last_sync_at) FROM mangapixer_sync WHERE last_status = 'ok'").fetchone()
        return r[0] if r else None

    # --- mappings ------------------------------------------------------------------------------------

    def mappings(self) -> Dict[int, Mapping]:
        with self.connect() as con:
            return {r["root_id"]: _mapping(r) for r in con.execute("SELECT * FROM mangapixer_mappings")}

    def mapping(self, root_id: int) -> Optional[Mapping]:
        with self.connect() as con:
            r = con.execute("SELECT * FROM mangapixer_mappings WHERE root_id = ?", (root_id,)).fetchone()
        return _mapping(r) if r else None

    def save_mapping(self, m: Mapping) -> Mapping:
        m.updated_at = utcnow()
        with self.connect() as con:
            con.execute(
                "INSERT INTO mangapixer_mappings (root_id, library_id, prefix, manual, any_kind, matched, unmatched,"
                " updated_at) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(root_id) DO UPDATE SET"
                " library_id=excluded.library_id, prefix=excluded.prefix, manual=excluded.manual,"
                " any_kind=excluded.any_kind, matched=excluded.matched, unmatched=excluded.unmatched,"
                " updated_at=excluded.updated_at",
                (int(m.root_id), m.library_id, json.dumps([str(p) for p in m.prefix], ensure_ascii=False),
                 1 if m.manual else 0, 1 if m.any_kind else 0, m.matched, m.unmatched, m.updated_at))
        return m

    def clear_mapping(self, root_id: int) -> None:
        """Forget the root's mapping (the next automatic mapping decides again)."""
        with self.connect() as con:
            con.execute("DELETE FROM mangapixer_mappings WHERE root_id = ?", (root_id,))

    def libraries_to_sync(self) -> List[LibraryRow]:
        """Present libraries of a default kind, plus those a mapping uses with the kind override."""
        overrides = {m.library_id for m in self.mappings().values() if m.library_id and m.any_kind}
        return [lib for lib in self.libraries(present_only=True) if lib.default_kind or lib.id in overrides]

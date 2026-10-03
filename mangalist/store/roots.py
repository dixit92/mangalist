"""Roots (folders of series folders) and their exclusions."""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence

from .db import utcnow
from .exclusions import ExclusionSet, normalize_pattern
from .schema import ENFORCE_NAMING, ENFORCE_NAMING_DEFAULT, ORIGIN_HINTS


class RootError(ValueError):
    pass


@dataclass
class Root:
    id: Optional[int]
    name: str
    path: str
    origin_hint: Optional[str] = None
    enforce_naming: str = ENFORCE_NAMING_DEFAULT
    staging_folder: Optional[str] = None
    naming_scheme: Optional[str] = None
    position: int = 0
    exclusions: List[str] = field(default_factory=list)

    @property
    def folder(self) -> Path:
        return Path(self.path)

    def exclusion_set(self) -> ExclusionSet:
        return ExclusionSet(self.exclusions)


def normalize_root_path(path: str) -> str:
    """Absolute, without a trailing separator; symlinks are NOT resolved (a Docker mount path stays
    the path this instance knows)."""
    p = str(path or "").strip()
    if not p:
        raise RootError("a root needs a folder")
    return os.path.normpath(os.path.abspath(os.path.expanduser(p)))


def _same_or_inside(a: str, b: str) -> bool:
    """True when folder *a* is *b* or lies inside it (case-insensitive on Windows)."""
    na, nb = os.path.normcase(a), os.path.normcase(b)
    if na == nb:
        return True
    return na.startswith(nb.rstrip(os.sep) + os.sep)


def validate_root(root: Root, others: Sequence[Root]) -> Root:
    """Normalise *root* and check it against the other roots; raises :class:`RootError`."""
    root.path = normalize_root_path(root.path)
    root.name = (root.name or "").strip() or Path(root.path).name or root.path
    if root.origin_hint in ("", "none"):
        root.origin_hint = None
    if root.origin_hint is not None and root.origin_hint not in ORIGIN_HINTS:
        raise RootError(f"unknown origin hint {root.origin_hint!r}")
    if root.enforce_naming not in ENFORCE_NAMING:
        raise RootError(f"enforce naming must be one of {', '.join(ENFORCE_NAMING)}")
    if root.staging_folder is not None:
        root.staging_folder = root.staging_folder.strip() or None
        if root.staging_folder is not None:
            root.staging_folder = normalize_root_path(root.staging_folder)
    for other in others:
        if other is root or (other.id is not None and other.id == root.id):
            continue
        if _same_or_inside(root.path, other.path) or _same_or_inside(other.path, root.path):
            raise RootError(f"{root.path} overlaps the root {other.name!r} ({other.path})")
    patterns: List[str] = []
    for pat in root.exclusions:
        try:
            norm = normalize_pattern(pat)
        except ValueError as exc:
            raise RootError(f"exclusion {pat!r}: {exc}") from None
        if norm not in patterns:
            patterns.append(norm)
    root.exclusions = patterns
    return root


class RootsMixin:
    """Roots and exclusions on the :class:`~mangalist.store.Store`."""

    def list_roots(self) -> List[Root]:
        with self.connect() as con:
            return _list_roots(con)

    def get_root(self, root_id: int) -> Optional[Root]:
        return next((r for r in self.list_roots() if r.id == root_id), None)

    def root_for_path(self, path) -> Optional[Root]:
        """The root that holds *path* (or is it)."""
        p = os.path.abspath(os.fspath(path))
        for root in self.list_roots():
            if _same_or_inside(p, root.path):
                return root
        return None

    def add_root(self, path: str, name: str = "", **settings) -> Root:
        root = Root(id=None, name=name, path=path, **settings)
        with self.connect() as con:
            existing = _list_roots(con)
            validate_root(root, existing)
            root.position = max((r.position for r in existing), default=-1) + 1
            now = utcnow()
            cur = con.execute(
                "INSERT INTO roots (name, path, origin_hint, enforce_naming, naming_scheme, staging_folder,"
                " position, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (root.name, root.path, root.origin_hint, root.enforce_naming, root.naming_scheme,
                 root.staging_folder, root.position, now, now))
            root.id = int(cur.lastrowid)
            _write_exclusions(con, root.id, root.exclusions)
        return root

    def update_root(self, root: Root) -> Root:
        if root.id is None:
            raise RootError("update_root needs a saved root")
        with self.connect() as con:
            existing = _list_roots(con)
            if not any(r.id == root.id for r in existing):
                raise RootError(f"no root with id {root.id}")
            validate_root(root, existing)
            con.execute(
                "UPDATE roots SET name=?, path=?, origin_hint=?, enforce_naming=?, naming_scheme=?,"
                " staging_folder=?, position=?, updated_at=? WHERE id=?",
                (root.name, root.path, root.origin_hint, root.enforce_naming, root.naming_scheme,
                 root.staging_folder, root.position, utcnow(), root.id))
            _write_exclusions(con, root.id, root.exclusions)
        return root

    def remove_root(self, root_id: int) -> None:
        """Forget a root (its series and units rows go with it). Nothing on disk is touched, and the
        MangaUpdates links cache keeps its rows (keyed by folder), so adding the root again restores them."""
        with self.connect() as con:
            con.execute("DELETE FROM roots WHERE id = ?", (root_id,))

    def set_exclusions(self, root_id: int, patterns: Sequence[str]) -> List[str]:
        root = self.get_root(root_id)
        if root is None:
            raise RootError(f"no root with id {root_id}")
        root.exclusions = list(patterns)
        return self.update_root(root).exclusions


def _list_roots(con: sqlite3.Connection) -> List[Root]:
    rows = con.execute("SELECT * FROM roots ORDER BY position, id").fetchall()
    excl = {}
    for e in con.execute("SELECT root_id, pattern FROM exclusions ORDER BY root_id, position, id"):
        excl.setdefault(e["root_id"], []).append(e["pattern"])
    return [Root(id=r["id"], name=r["name"], path=r["path"], origin_hint=r["origin_hint"],
                 enforce_naming=r["enforce_naming"] or ENFORCE_NAMING_DEFAULT,
                 staging_folder=r["staging_folder"], naming_scheme=r["naming_scheme"],
                 position=r["position"], exclusions=excl.get(r["id"], []))
            for r in rows]


def _write_exclusions(con: sqlite3.Connection, root_id: int, patterns: Sequence[str]) -> None:
    con.execute("DELETE FROM exclusions WHERE root_id = ?", (root_id,))
    con.executemany("INSERT INTO exclusions (root_id, pattern, position) VALUES (?,?,?)",
                    [(root_id, p, i) for i, p in enumerate(patterns)])

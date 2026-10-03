"""Root-relative exclusion patterns: what a root's scan never looks at.

Pattern rules (close to ``.gitignore``, case-insensitive because the library is shared with Windows):

- Paths are relative to the root, with ``/`` separators (a ``\\`` in a pattern is read as ``/``); a
  leading ``/`` or ``./`` is ignored.
- A pattern WITHOUT a ``/`` matches a name at any depth: ``*.txt`` hides every ``.txt`` file,
  ``@Oneshots`` hides a folder (or file) of that name wherever it is.
- A pattern WITH a ``/`` is anchored at the root: ``@Oneshots/**`` hides everything inside the root's
  ``@Oneshots`` folder (and the folder itself, so it is not reported as an empty series),
  ``Series/Extras`` hides that one subfolder.
- A trailing ``/`` matches folders only (``Scans/``).
- ``*`` matches within one name, ``?`` one character, ``[abc]`` / ``[!abc]`` a set, ``**`` as a whole
  segment any number of folders (``**/Raw/*.zip``).

An excluded folder is not descended into, so nothing below it is scanned.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Pattern, Tuple


class InvalidPattern(ValueError):
    pass


def normalize_pattern(pattern: str) -> str:
    """The stored form of *pattern*; raises :class:`InvalidPattern` when it is empty or unusable."""
    p = (pattern or "").strip().replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    p = p.lstrip("/")
    if not p or p in ("/",):
        raise InvalidPattern("empty pattern")
    if "//" in p.rstrip("/"):
        raise InvalidPattern(f"empty path segment in {pattern!r}")
    if any(seg in (".", "..") for seg in p.rstrip("/").split("/")):
        raise InvalidPattern(f"'.' and '..' are not allowed in a pattern: {pattern!r}")
    if p.count("[") != p.count("]"):
        raise InvalidPattern(f"unbalanced [ ] in {pattern!r}")
    return p


def _translate_segment(seg: str) -> str:
    out: List[str] = []
    i, n = 0, len(seg)
    while i < n:
        c = seg[i]
        if c == "*":
            while i + 1 < n and seg[i + 1] == "*":
                i += 1  # '**' inside a name behaves like '*'
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "[":
            j = seg.find("]", i + 1)
            if j < 0:
                out.append(re.escape(c))
            else:
                body = seg[i + 1:j]
                neg = body.startswith("!")
                if neg:
                    body = body[1:]
                body = body.replace("\\", "\\\\").replace("^", "\\^")
                out.append(f"[{'^' if neg else ''}{body}]" if body else re.escape(seg[i:j + 1]))
                i = j
        else:
            out.append(re.escape(c))
        i += 1
    return "".join(out)


def _compile_segments(segs: List[str]) -> Pattern[str]:
    out = ""
    last = len(segs) - 1
    for i, seg in enumerate(segs):
        if seg == "**":
            out += ".*" if i == last else "(?:[^/]+/)*"
        else:
            out += _translate_segment(seg)
            if i != last:
                out += "/"
    return re.compile("^" + out + "$", re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True)
class _Compiled:
    pattern: str
    regex: Pattern[str]
    dir_only: bool


def compile_pattern(pattern: str) -> List[_Compiled]:
    """One or two matchers for a pattern (``X/**`` also matches the folder ``X`` itself)."""
    p = normalize_pattern(pattern)
    dir_only = p.endswith("/")
    body = p.rstrip("/")
    segs = body.split("/")
    if len(segs) == 1 and segs[0] != "**":
        segs = ["**", segs[0]]  # no slash: a name at any depth
    compiled = [_Compiled(p, _compile_segments(segs), dir_only)]
    if len(segs) > 1 and segs[-1] == "**" and segs[:-1] != ["**"]:
        compiled.append(_Compiled(p, _compile_segments(segs[:-1]), True))
    return compiled


class ExclusionSet:
    """A root's compiled exclusions. Invalid patterns are kept out (see :attr:`invalid`)."""

    def __init__(self, patterns: Iterable[str] = ()):
        self.patterns: List[str] = []
        self.invalid: List[Tuple[str, str]] = []
        self._compiled: List[_Compiled] = []
        for pat in patterns:
            try:
                comp = compile_pattern(pat)
            except InvalidPattern as exc:
                self.invalid.append((pat, str(exc)))
                continue
            self.patterns.append(comp[0].pattern)
            self._compiled.extend(comp)

    def __bool__(self) -> bool:
        return bool(self._compiled)

    def match(self, rel_path: str, is_dir: bool) -> Optional[str]:
        """The pattern that excludes *rel_path* (root-relative, ``/`` separators), or None."""
        rel = rel_path.replace("\\", "/").strip("/")
        if not rel:
            return None  # the root itself is never excluded
        for c in self._compiled:
            if c.dir_only and not is_dir:
                continue
            if c.regex.match(rel):
                return c.pattern
        return None

    def excludes(self, rel_path: str, is_dir: bool) -> bool:
        return self.match(rel_path, is_dir) is not None


def rel_posix(path: Path, root: Path) -> str:
    """*path* relative to *root* with ``/`` separators ('' for the root itself)."""
    rel = os.path.relpath(os.fspath(path), os.fspath(root))
    if rel == ".":
        return ""
    return rel.replace(os.sep, "/")


# --- Live preview (Roots manager) ---------------------------------------------------------------------


@dataclass
class TreeListing:
    """A bounded listing of a root (what a scan could see), for previewing patterns without rescanning."""

    root: Path
    entries: List[Tuple[str, bool]] = field(default_factory=list)  # (rel path, is_dir), parents first
    truncated: bool = False
    error: Optional[str] = None


def list_tree(root: Path, max_depth: int = 4, max_entries: int = 20000) -> TreeListing:
    """List *root* down to *max_depth* levels (1 = its direct children), at most *max_entries* items."""
    listing = TreeListing(root=Path(root))

    def walk(folder: Path, prefix: str, depth: int) -> bool:
        try:
            with os.scandir(folder) as it:
                items = sorted(it, key=lambda e: e.name.lower())
        except OSError as exc:
            if depth == 1:
                listing.error = str(exc)
            return True
        for item in items:
            if depth == 1 and item.name.startswith(".mangalist.lock"):
                continue  # MangaList's own root lock
            if len(listing.entries) >= max_entries:
                listing.truncated = True
                return False
            try:
                is_dir = item.is_dir()
            except OSError:
                is_dir = False
            rel = f"{prefix}{item.name}"
            listing.entries.append((rel, is_dir))
            if is_dir and depth < max_depth:
                if not walk(Path(item.path), rel + "/", depth + 1):
                    return False
        return True

    walk(listing.root, "", 1)
    return listing


@dataclass
class PreviewItem:
    rel_path: str
    is_dir: bool
    pattern: str
    hidden_below: int = 0  # listed items inside an excluded folder


def preview(listing: TreeListing, patterns: Iterable[str]) -> List[PreviewItem]:
    """What *patterns* hide in *listing*: the top-most excluded items, each with the number of listed
    items below it."""
    ex = ExclusionSet(patterns)
    if not ex:
        return []
    out: List[PreviewItem] = []
    open_dirs: List[PreviewItem] = []  # excluded folders whose subtree we are inside
    for rel, is_dir in listing.entries:
        while open_dirs and not rel.startswith(open_dirs[-1].rel_path + "/"):
            open_dirs.pop()
        if open_dirs:
            open_dirs[-1].hidden_below += 1
            continue
        pat = ex.match(rel, is_dir)
        if pat is None:
            continue
        item = PreviewItem(rel, is_dir, pat)
        out.append(item)
        if is_dir:
            open_dirs.append(item)
    return out

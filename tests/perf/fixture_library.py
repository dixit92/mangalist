"""A synthetic library the size of the owner's for the work-counter tests (UI performance research, 2026-10-14,
"Recommended tests and budgets"): 700 series across 7 libraries, about 22.5k archives - volume folders, chapter
folders, folders with both, some with a season subfolder - and 150 findings waiting under Found. Made-up names only;
nothing is on disk (the folders do not exist), so the tests count work without touching a filesystem.

Qt-free. :func:`build` makes fresh entries every call (tests change them); the plan behind them is made once per
session."""

from __future__ import annotations

import functools
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple

from mangalist.models import FileHit, MangaEntry, Verdict
from mangalist.parsing import ParseContext, parse_name
from mangalist.store.findings import Finding

N_ROOTS = 7
N_SERIES = 700
N_FINDINGS = 150
WHEN = "2026-10-14T04:30:00+00:00"
SIZE = 50_000_000                   # every archive's size (bytes)

_FIRST = ("Amber", "Hollow", "Silver", "Quiet", "Paper", "Salt", "Umbral", "Ninth", "Kitsune", "Velvet", "Iron",
          "Lantern", "Willow", "Copper", "Glass", "Morning", "Northern", "Crimson", "Winter", "Distant")
_SECOND = ("Lantern", "Tide", "Archivist", "Ferry", "Garden", "Post Office", "Harbor", "Orchard", "Signal", "Atlas",
           "Circuit", "Meadow", "Compass", "Lighthouse", "Workshop", "Station", "Valley", "Bridge", "Archive",
           "Market", "Courier", "Library", "Observatory", "Canal", "Theater")


@dataclass(frozen=True)
class SeriesPlan:
    root: int                   # 1..N_ROOTS
    title: str
    english: str
    files: Tuple[Tuple[str, bool, bool], ...]     # (path relative to the series folder, has volume, has chapter)
    parsed: Tuple[object, ...]                    # each file's ParsedName, as the scanner sets it (made once)


@dataclass
class Library:
    roots: Dict[int, Path]                          # root id -> its (not existing) folder
    entries: List[MangaEntry]
    findings: List[Finding] = field(default_factory=list)
    series_ids: Dict[str, int] = field(default_factory=dict)     # folder -> series id (the findings' ids)

    def names(self) -> Dict[int, str]:
        """series id -> title, for the Found view."""
        by_folder = {str(e.folder): e.title for e in self.entries}
        return {sid: by_folder[f] for f, sid in self.series_ids.items()}


@functools.lru_cache(maxsize=None)
def _plan(n_series: int, n_roots: int) -> Tuple[SeriesPlan, ...]:
    out = []
    for i in range(n_series):
        title = f"{_FIRST[i % len(_FIRST)]} {_SECOND[(i // len(_FIRST)) % len(_SECOND)]} {i:03d}"
        shape = i % 5
        files: List[Tuple[str, bool, bool]] = []
        if shape in (0, 1):                         # a volume folder
            files += [(f"{title} v{n:02d} (Digital).cbz", True, False) for n in range(1, 13 + i % 20)]
        elif shape == 2:                            # a chapter folder
            files += [(f"{title} Ch.{n:03d}.cbz", False, True) for n in range(1, 30 + i % 40)]
        elif shape == 3:                            # volumes, then the chapters after them
            files += [(f"{title} v{n:02d}.cbz", True, False) for n in range(1, 8)]
            files += [(f"{title} Ch.{n:03d}.cbz", False, True) for n in range(60, 80 + i % 25)]
        else:                                       # two seasons in subfolders
            files += [(f"Season 1/{title} Ch.{n:03d}.cbz", False, True) for n in range(1, 25)]
            files += [(f"Season 2/{title} Ch.{n:03d}.cbz", False, True) for n in range(1, 10 + i % 15)]
        parsed = tuple(parse_name(Path(rel).name, ParseContext(), file_size=SIZE) for rel, _v, _c in files)
        out.append(SeriesPlan(root=i % n_roots + 1, title=title, english=f"The {title}" if i % 3 == 0 else "",
                              files=tuple(files), parsed=parsed))
    return tuple(out)


def build(n_series: int = N_SERIES, n_roots: int = N_ROOTS, n_findings: int = N_FINDINGS,
          base: Path = Path("/synthetic")) -> Library:
    """Fresh entries (and findings) of the synthetic library; *base* holds the libraries' folders."""
    roots = {r: base / f"Library {r}" for r in range(1, n_roots + 1)}
    entries, series_ids = [], {}
    for i, plan in enumerate(_plan(n_series, n_roots)):
        folder = roots[plan.root] / plan.title
        e = MangaEntry(folder=folder, title=plan.title, english_title=plan.english or None, root_id=plan.root)
        e.files = [FileHit(path=folder / rel, size=SIZE, depth=rel.count("/"), has_volume=v, has_chapter=c, parsed=p)
                   for (rel, v, c), p in zip(plan.files, plan.parsed)]
        kinds = {(v, c) for _r, v, c in plan.files}
        e.verdict = Verdict.BOTH if len(kinds) > 1 else Verdict.VOLUMES if (True, False) in kinds else Verdict.CHAPTERS
        if i % 2 == 0:                              # every other series matched on MangaUpdates, a few volumes or
            volumes = sum(1 for _r, v, _c in plan.files if v)       # chapters behind: something to get
            e.mu_id, e.mu_title, e.mu_band, e.mu_confirmed = 10_000 + i, plan.title, "auto", True
            if volumes:
                e.licensed, e.publisher_name, e.publisher_volumes = True, "Example Press", float(volumes + 2)
            else:
                e.licensed, e.scan_latest_chapter = False, float(len(plan.files) + 3)
        entries.append(e)
        series_ids[str(folder)] = i + 1
    findings = []
    for n in range(n_findings):
        i = (n * 7) % n_series                      # spread over the libraries, a few series with several
        plan = _plan(n_series, n_roots)[i]
        chapters = i % 5 in (2, 4)
        number = str(40 + n % 30) if chapters else str(20 + n % 10)
        findings.append(Finding(
            id=n + 1, series_id=i + 1, kind="chapters" if chapters else "volumes", pick_key=f"nyaa:{n:040x}",
            title=f"{plan.title} {'Ch.' if chapters else 'v'}{number} (Digital)", fills=(number,),
            size_bytes=None if chapters else 400_000_000 + n, reason="the series is on Notify",
            payload={"type": "suwayomi", "source_name": "Example Source"} if chapters else {"type": "nyaa"},
            status="waiting", run_id=1, record_id=None, found_at=WHEN, updated_at=WHEN))
    return Library(roots=roots, entries=entries, findings=findings, series_ids=series_ids)

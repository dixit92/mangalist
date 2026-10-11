"""A made-up library on disk for the wanted-list tests: two roots, one paired with a synthetic MangaPixer library, series
folders covering every kind of gap, the MangaUpdates links cache - scanned and recorded exactly as the window's scan does
(``scanner.scan_and_record_library``). Titles, ids and paths are invented."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from mangalist import mu_cache, store
from mangalist.scanner import LibraryScan, scan_and_record_library
from mangalist.store.mangapixer import Mapping, MangaPixerCache

from ..states.helpers import item, vol

LIB_ID = "lib0manga"

# series folder (root-relative) -> archive names (root-relative to the series folder; "" = an empty folder)
MANGA: Dict[str, List[str]] = {
    "Example Quest": ["Example Quest v01.cbz"],                                         # MangaPixer: vol. 2-3 missing
    "Franchise/Alpha Tale": ["Alpha Tale v01.cbz", "Alpha Tale v02.cbz"],              # inherits "Franchise"
    "Franchise/Beta Tale": ["Beta Tale v01.cbz", "Beta Tale v02.cbz", "Beta Tale v03.cbz"],
    "Own Series": ["Own Series v01.cbz", "Own Series v02.cbz"],                        # own matcher: 5 out
    "Volume Gaps": ["Volume Gaps v01.cbz", "Volume Gaps v03.cbz", "Volume Gaps v05.cbz"],
    "Scanlation Chapters": [f"Scanlation Chapters c{n:03d}.cbz" for n in range(1, 16)],  # ch. 16-20 missing
    "Upgrade Series": ["Upgrade Series v01.cbz"] + [f"Upgrade Series c{n:03d}.cbz" for n in range(9, 17)],
    "Season Webtoon": [f"Season {s}/Season Webtoon c{n:03d}.cbz" for s in (1, 2) for n in range(1, 6)],
    "Bare Numbers": ["01.cbz", "02.cbz"],                                              # "volumes or chapters?"
    "Answered Kind": ["01.cbz", "02.cbz"],                                             # answered: volumes
    "Behind Done": ["Behind Done v01.cbz"],                                            # marked up to date
    "Empty Licensed": [],                                                              # empty: Wanted (official)
    "Empty Unknown": [],                                                               # empty, nothing known
    "Parent Series": ["Parent Series v01.cbz", "Spin-off Story/Spin-off Story v01.cbz"],
    "Not A Series": ["Not A Series v01.cbz"],                                          # MangaPixer: Don't match
}
MANHWA: Dict[str, List[str]] = {
    "Own Manhwa": [f"Own Manhwa c{n:03d}.cbz" for n in range(1, 11)],                  # own matcher, chapters
    "Unmatched Manhwa": ["Unmatched Manhwa c001.cbz"],
}


def _items() -> List[dict]:
    five = {"items": [vol(str(n), date="2025-01-01", kind="released") for n in range(1, 6)], "source": "merged",
            "fetchedAt": "2026-10-02T12:00:00.000Z"}
    scan = dict(record__licensedEn=False, record__englishPublishers=[], record__latestChapter="20", volumes=None,
                completion=None)
    out = [
        item(nodeId="n01", trail=["Example Quest"]),
        item(nodeId="n02", trail=["Franchise"]),
        item(nodeId="n03", trail=["Volume Gaps"], volumes=five),
        item(nodeId="n04", trail=["Scanlation Chapters"], **scan),
        item(nodeId="n05", trail=["Upgrade Series"]),
        item(nodeId="n06", trail=["Season Webtoon"], **scan),
        item(nodeId="n07", trail=["Bare Numbers"]),
        item(nodeId="n08", trail=["Answered Kind"]),
        item(nodeId="n09", trail=["Behind Done"]),
        item(nodeId="n10", trail=["Empty Licensed"]),
        item(nodeId="n11", trail=["Parent Series"]),
        item(nodeId="n12", trail=["Not A Series"], link={"state": "DontMatch", "method": None, "score": None,
                                                          "updatedAt": "2026-09-28T12:00:00.000Z"},
             record=None, volumes=None, completion=None),
    ]
    return out


class _Lib:
    def __init__(self, id, name, kind="manga"):
        self.id, self.display_name, self.kind = id, name, kind
        self.folder_count = self.item_count = self.last_scan_at = None


@dataclass
class Library:
    db: store.Store
    roots: list
    scan: LibraryScan
    base: Path
    folders: Dict[str, str] = field(default_factory=dict)    # "Manga/Example Quest" -> the scanned folder (str)

    def folder(self, key: str) -> str:
        return self.folders[key]


def _write(folder: Path, names: List[str]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for n, name in enumerate(names):
        p = folder / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * (1000 + n))


def scan(db, roots) -> LibraryScan:
    return scan_and_record_library(roots, db)


def build_library(base: Path, db: Optional[store.Store] = None) -> Library:
    """Write the folders, pair the Manga root with the synthetic MangaPixer library, cache the own matcher's links,
    store one "volumes or chapters?" answer, and scan + record both roots (twice: the answer is stored on the series
    row the first scan created, as the owner gives it)."""
    db = db or store.get_store()
    for name, series in (("Manga", MANGA), ("Manhwa", MANHWA)):
        for rel, names in series.items():
            _write(base / name / rel, names)
    manga = db.add_root(str(base / "Manga"), "Manga")
    manhwa = db.add_root(str(base / "Manhwa"), "Manhwa")
    roots = [manga, manhwa]

    cache = MangaPixerCache(db)
    cache.save_libraries([_Lib(LIB_ID, "Manga")])
    cache.apply_page(LIB_ID, [], _items())
    cache.save_mapping(Mapping(manga.id, LIB_ID, [], manual=True))

    first = scan(db, db.list_roots())
    folders = {f"{r.root_name}/{Path(e.folder).relative_to(r.folder).as_posix()}": str(e.folder)
               for r in first.roots for e in r.entries}
    db.set_series_kind(manga.id, "Answered Kind", "volumes")

    mu_cache.save_entry(Path(folders["Manga/Own Series"]), 9001, "Own Series", "https://mu.example/s/9001",
                        licensed=True, mu_confirmed=True, publisher_name="Example Books", publisher_volumes=5.0,
                        publisher_status="Ongoing")
    mu_cache.save_entry(Path(folders["Manga/Behind Done"]), 9002, "Behind Done", "https://mu.example/s/9002",
                        licensed=True, mu_confirmed=True, behind_override="done")
    mu_cache.save_entry(Path(folders["Manhwa/Own Manhwa"]), 9003, "Own Manhwa", "https://mu.example/s/9003",
                        licensed=False, mu_confirmed=True, scan_latest_chapter=14.0)
    result = scan(db, db.list_roots())
    return Library(db=db, roots=db.list_roots(), scan=result, base=base, folders=folders)

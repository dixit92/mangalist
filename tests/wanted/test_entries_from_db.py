"""``wanted.entries_from_db`` reads every archive as the scan that recorded it did (the folder's series head included):
the headless passes and the List never disagree about a file. Made-up names."""

from __future__ import annotations

from pathlib import Path

from mangalist import store, wanted

from .library import _write, scan

FOLDERS = {
    # a number in the title the files carry (not the folder's): volumes 1-3, not three copies of chapter 9
    "Kisekae Zero Zero Nine": [f"Dress Up 009 v{n:02d} (2020) (Digital).cbz" for n in (1, 2, 3)],
    # the files carry another title than the folder: chapters 1-3
    "Romaji no Title": ["English Title 001 The Start.cbz", "English Title 002 The Middle.cbz",
                        "English Title 003 The End.cbz"],
    "Plain Series": ["Plain Series v01.cbz", "Season 2/Plain Series c001.cbz", "Season 2/Plain Series c002.cbz"],
}


def _read(entry):
    def units(r):
        return None if r is None else str(r)

    return {Path(h.path).relative_to(entry.folder).as_posix(): (
        h.kind, getattr(h.parsed.kind, "value", h.parsed.kind), units(h.parsed.volume), units(h.parsed.chapter),
        units(h.parsed.number), h.parsed.guessed) for h in entry.files}


def test_every_archive_reads_as_the_scan_read_it(tmp_path):
    store.reset_stores()
    db = store.get_store()
    for rel, names in FOLDERS.items():
        _write(tmp_path / "lib" / "Manga" / rel, names)
    root = db.add_root(str(tmp_path / "lib" / "Manga"), "Manga")
    scanned = {Path(e.folder).name: _read(e) for r in scan(db, db.list_roots()).roots for e in r.entries}
    rebuilt = {Path(e.folder).name: _read(e) for _row, e in wanted.entries_from_db(db, root)}
    assert set(scanned) == set(FOLDERS)
    for name in FOLDERS:
        assert rebuilt[name] == scanned[name], name
    dress = scanned["Kisekae Zero Zero Nine"]
    assert [v[0] for v in dress.values()] == ["volume"] * 3 and len({v[2] for v in dress.values()}) == 3
    title = scanned["Romaji no Title"]
    assert [v[0] for v in title.values()] == ["chapter"] * 3 and len({v[3] for v in title.values()}) == 3

"""Content verdicts for "Whole chapters with parts" (owner, 2026-10-24: "Content verdicts";
:mod:`mangalist.duplicate_verdicts`): the rule on page hashes (whole = parts, copy, wrong chapter, distinct, undecided;
credit pages left out), the candidates and their neighbouring chapters, the cache by content signature, Dismiss, and
the graceful "undecided" when an archive or Pillow is missing. Made-up series and synthetic pages; the end-to-end
tests decode real (synthetic) JPEG pages and need Pillow, the rest do not."""

from __future__ import annotations

import os
import random
import sqlite3
import zipfile

import pytest

from mangalist import duplicate_verdicts as dv, page_hash, store
from mangalist.duplicate_verdicts import (VERDICT_COPY, VERDICT_DISTINCT, VERDICT_PARTS, VERDICT_UNDECIDED,
                                          VERDICT_WRONG, decide, dismiss, hash_files, judge, restore, split_shown)
from mangalist.duplicates import find_whole_and_parts
from mangalist.page_hash import STATUS_OK, ArchivePages
from mangalist.store import page_hashes, schema

T0 = 1_700_000_000_000_000_000


# --- the rule, on made-up hashes (no Pillow) --------------------------------------------------------------------

def H(seed: int) -> int:
    return random.Random(seed).getrandbits(64)


def near(h: int, seed: int, bits: int = 4) -> int:
    """*h* with *bits* bits flipped: the same page, re-encoded by another group."""
    for b in random.Random(seed).sample(range(64), bits):
        h ^= 1 << b
    return h


def pages(hashes, crcs=None) -> ArchivePages:
    hashes = tuple(hashes)
    return ArchivePages(STATUS_OK, hashes, tuple(crcs) if crcs is not None else tuple(((h or 0) & 0xFFFF, 7) for h in hashes))


def reencoded(seeds, salt: int) -> ArchivePages:
    return pages([near(H(s), salt * 1000 + s) for s in seeds], crcs=[(salt, s) for s in seeds])


def test_a_whole_chapter_that_is_its_parts():
    whole = pages(H(s) for s in range(1, 17))
    d = decide("81", whole, [("81.1", reencoded(range(1, 9), 1)), ("81.2", reencoded(range(9, 17), 2))])
    assert (d.kind, d.evidence) == (VERDICT_PARTS, "16 of 16 pages of ch 81 are in 81.1 + 81.2")


def test_separate_chapters_numbered_as_parts():
    whole = pages(H(s) for s in range(1, 17))
    d = decide("81", whole, [("81.1", pages(H(s) for s in range(100, 126))), ("81.2", pages(H(s) for s in range(200, 229)))])
    assert (d.kind, d.evidence) == (VERDICT_DISTINCT, "none of ch 81's 16 pages are in 81.1 / 81.2 - separate chapters")


def test_a_part_that_is_the_whole_again_is_a_copy():
    whole = pages(H(s) for s in range(1, 27))
    d = decide("9", whole, [("9.1", pages(H(s) for s in range(1, 27))), ("9.2", pages(H(s) for s in range(50, 65)))])
    assert d.kind == VERDICT_COPY and d.copy_of == "9.1"
    assert d.evidence == "26 of 26 pages of ch 9 are in 9.1, and 26 of 26 of 9.1's are in ch 9 (all byte-identical)"
    d = decide("9", whole, [("9.1", reencoded(range(1, 27), 3)), ("9.2", pages(H(s) for s in range(50, 65)))])
    assert d.kind == VERDICT_COPY and d.evidence.endswith("(re-encoded)")


def test_a_whole_file_holding_the_previous_chapters_pages_is_the_wrong_chapter():
    thirteen = pages(H(s) for s in range(300, 328))
    d = decide("14", reencoded(range(300, 328), 4), [("14.2", pages(H(s) for s in range(400, 445)))],
               controls=[("13", thirteen), ("15", pages(H(s) for s in range(500, 520)))])
    assert (d.kind, d.wrong_as) == (VERDICT_WRONG, "13")
    assert d.evidence == "28 of 28 pages of the file ch 14 are chapter 13's pages"


def test_credit_pages_a_group_repeats_in_every_chapter_are_left_out():
    credits = [H(s) for s in (900, 901, 902)]
    whole = pages([H(s) for s in range(1, 6)] + credits)
    parts = [("20.1", pages([H(s) for s in range(10, 13)] + credits)), ("20.2", pages([H(s) for s in range(20, 23)] + credits))]
    controls = [("19", pages([H(s) for s in range(30, 40)] + credits)), ("21", pages([H(s) for s in range(40, 50)] + credits))]
    d = decide("20", whole, parts, controls)
    assert d.kind == VERDICT_DISTINCT
    assert d.evidence == ("none of ch 20's 5 pages are in 20.1 / 20.2 - separate chapters (3 credit pages also in the "
                          "next chapters left out)")
    # without the neighbouring chapters the credit pages read as shared content: half of every part - undecided
    assert decide("20", whole, parts).kind == VERDICT_UNDECIDED


def test_partly_the_same_pages_or_too_few_pages_is_undecided():
    whole = pages(H(s) for s in range(1, 11))
    d = decide("5", whole, [("5.1", pages(H(s) for s in range(1, 5))), ("5.2", pages(H(s) for s in range(60, 70)))])
    assert d.kind == VERDICT_UNDECIDED and d.evidence == "4 of 10 pages of ch 5 are in 5.1 + 5.2 - partly the same pages"
    d = decide("6", pages([H(1), H(2)]), [("6.1", pages([H(1)])), ("6.2", pages([H(2)]))])
    assert d.kind == VERDICT_UNDECIDED and d.evidence == "ch 6 has only 2 pages to compare"


def test_undecodable_pages_are_ignored_in_the_counts():
    whole = pages([H(s) for s in range(1, 11)] + [None])
    d = decide("7", whole, [("7.1", reencoded(range(1, 6), 5)), ("7.2", reencoded(range(6, 11), 6))])
    assert (d.kind, d.evidence) == (VERDICT_PARTS, "10 of 10 pages of ch 7 are in 7.1 + 7.2")


# --- the cache and Dismiss in the database (no Pillow) ----------------------------------------------------------

def test_page_hashes_are_stored_by_signature_with_unreadable_pages(db):
    got = ArchivePages(STATUS_OK, (1, None, (1 << 64) - 1), ((10, 20), (30, 40), (50, 60)), seconds=0.5)
    assert page_hashes.store_pages(db, "v1:3:abc", got)
    assert page_hashes.cached_pages(db, ["v1:3:abc", "v1:9:other"]) == {
        "v1:3:abc": ArchivePages(STATUS_OK, (1, None, (1 << 64) - 1), ((10, 20), (30, 40), (50, 60)), "", 0.5)}
    bad = ArchivePages(page_hash.STATUS_UNREADABLE, reason="the archive holds no image")
    assert page_hashes.store_pages(db, "v1:4:bad", bad)
    assert page_hashes.cached_pages(db, ["v1:4:bad"])["v1:4:bad"].reason == "the archive holds no image"
    # results that depend on more than the file's bytes are never stored: tried again later
    for status in (page_hash.STATUS_UNSUPPORTED,):
        assert not page_hashes.store_pages(db, "v1:5:x", ArchivePages(status, reason="Pillow is not installed"))
    assert not page_hashes.store_pages(db, "", got)
    assert page_hashes.hashed_count(db) == 2


def fake_zip(path, entries=("001.jpg",)):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        for i, name in enumerate(entries):
            z.writestr(name, f"not really a picture {path.name} {i}")
    return path


def zip_series(lib, series, numbers, mtime=T0):
    out = {}
    for i, n in enumerate(numbers):
        p = lib.add(series, f"Series c{n}.cbz")
        fake_zip(p)
        os.utime(p, ns=(mtime + i, mtime + i))
        out[n] = p
    lib.scan()
    return out


def test_dismiss_persists_until_a_file_changes(db, lib):
    files = zip_series(lib, "Example Series", ("037", "038", "038.1", "038.2"))
    (g,) = find_whole_and_parts(db)
    v = judge(db, [g], pillow=False).verdicts[dv.item_key(g)]
    assert v.kind == VERDICT_UNDECIDED and not v.hidden and v.dismiss_key
    assert dismiss(db, g, v)
    again = judge(db, [g], pillow=False).verdicts[dv.item_key(g)]
    assert again.dismissed and again.hidden and split_shown([g], {dv.item_key(g): again}) == ([], [g])
    store.reset_stores()                                    # the app restarted: still dismissed
    reopened = store.get_store()
    assert judge(reopened, [g], pillow=False).verdicts[dv.item_key(g)].dismissed
    fake_zip(files["038.2"], entries=("001.jpg", "002.jpg"))  # a file changed: the item is back
    lib.scan()
    (g2,) = find_whole_and_parts(reopened)
    back = judge(reopened, [g2], pillow=False).verdicts[dv.item_key(g2)]
    assert not back.dismissed and not back.hidden
    assert restore(reopened, again) and not judge(reopened, [g], pillow=False).verdicts[dv.item_key(g)].dismissed


def test_without_pillow_or_with_unreadable_archives_the_verdict_is_undecided(db, lib):
    zip_series(lib, "Example Series", ("038", "038.1", "038.2"))
    rar = lib.add("Other Series", "Other c010.cbr")
    rar.write_bytes(b"Rar!\x1a\x07\x00" + b"\0" * 64)
    lib.add("Other Series", "Other c010.1.cbz")                          # no zip at all
    fake_zip(lib.add("Other Series", "Other c010.2.cbz"))
    lib.scan()
    groups = find_whole_and_parts(db)
    j = judge(db, groups, pillow=False)
    assert j.pending == [] and not j.pillow
    by_number = {k[1]: v for k, v in j.verdicts.items()}
    assert by_number["38"].kind == VERDICT_UNDECIDED and not by_number["38"].hidden
    assert by_number["38"].evidence == "pages cannot be read here: Pillow is not installed"
    assert by_number["10"].evidence == ("ch 10 is a cbr archive, which is not read; ch 10.1 cannot be read (not a zip "
                                        "archive); pages cannot be read here: Pillow is not installed")
    with_pillow = judge(db, groups, pillow=True)
    assert {os.path.basename(f.path) for f in with_pillow.pending} == {
        "Series c038.cbz", "Series c038.1.cbz", "Series c038.2.cbz", "Other c010.2.cbz"}
    assert {k[1]: v.evidence for k, v in with_pillow.verdicts.items()}["38"] == dv.NOT_CHECKED


def test_a_plain_split_without_a_verdict_stays_hidden(db, lib):
    zip_series(lib, "Example Series", ("040", "040.2"))
    (g,) = find_whole_and_parts(db, stand_ins=True)
    j = judge(db, [g], pillow=False)
    v = j.verdicts[dv.item_key(g)]
    assert v.stand_in and v.kind == VERDICT_UNDECIDED and v.hidden and v.hidden_why == "split chapter"
    assert split_shown([g], {}) == ([], [g])


def test_schema_11_adds_the_cache_and_the_dismissals_to_a_schema_10_database(tmp_path):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    for version, script in schema.MIGRATIONS:
        if version > 10:
            break
        con.executescript(f"BEGIN;\n{script}\nPRAGMA user_version={version};\nCOMMIT;")
    con.execute("INSERT INTO roots (id, name, path, created_at, updated_at) VALUES (1, 'M', '/x', 't', 't')")
    con.commit()
    con.close()
    st = store.Store(path, import_legacy=False)
    assert st.schema_version() == 11 == schema.SCHEMA_VERSION
    assert [r.name for r in st.list_roots()] == ["M"]
    with st.connect() as c:
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"page_hashes", "overlap_dismissals"} <= tables
    assert page_hashes.store_pages(st, "v1:1:a", ArchivePages(STATUS_OK, (5,), ((1, 2),)))
    page_hashes.dismiss(st, "k", series_id=None, folder="/x/S", number="3", files=["a.cbz"])
    assert page_hashes.dismissed_keys(st) == {"k"}


# --- end to end on synthetic pages (Pillow) -------------------------------------------------------------------------

@pytest.fixture
def pil():
    pytest.importorskip("PIL")
    from tests.duplicates import pages as synthetic

    return synthetic


def build(lib, pil, series, chapters):
    """*chapters*: number -> (seeds, encode options, mtime offset)."""
    out = {}
    for n, (seeds, kw) in chapters.items():
        out[n] = pil.add_cbz(lib, series, f"{series} c{n}.cbz", seeds, mtime_ns=T0 + len(out) * 1_000_000_000, **kw)
    return out


@pytest.fixture
def library(db, lib, pil):
    """Five made-up series, one per verdict."""
    files = {
        # two groups' releases of chapter 10: the whole by one, 10.1 / 10.2 re-encoded and resized by another
        "Alpha": build(lib, pil, "Alpha", {"009": (range(900, 912), {}), "010": (range(1000, 1016), {}),
                                           "010.1": (range(1000, 1008), {"quality": 60}),
                                           "010.2": (range(1008, 1016), {"scale": 0.75}),
                                           "011": (range(1100, 1112), {})}),
        # the group re-posted 9 as 9.1: byte-identical; 9.2 continues
        "Bravo": build(lib, pil, "Bravo", {"008": (range(2800, 2810), {}), "009": (range(2900, 2920), {}),
                                           "009.1": (range(2900, 2920), {}), "009.2": (range(2950, 2962), {}),
                                           "010": (range(3000, 3010), {})}),
        # the file 14 is chapter 13's pages (a source's numbering error); 14.2 is its own: a plain split by the numbers
        "Charlie": build(lib, pil, "Charlie", {"013": (range(1300, 1318), {}),
                                               "014": (range(1300, 1318), {"quality": 70}),
                                               "014.2": (range(1420, 1440), {})}),
        # 81.1 / 81.2 are a two-part side story numbered after 81
        "Delta": build(lib, pil, "Delta", {"080": (range(8000, 8010), {}), "081": (range(8100, 8116), {}),
                                           "081.1": (range(8200, 8226), {}), "081.2": (range(8300, 8329), {}),
                                           "082": (range(8400, 8410), {})}),
        # a plain split: the file 24 is part 1, 24.2 part 2
        "Echo": build(lib, pil, "Echo", {"024": (range(2400, 2418), {}), "024.2": (range(2450, 2474), {})}),
    }
    lib.scan()
    return files


def verdicts(db):
    groups = find_whole_and_parts(db, stand_ins=True)
    j = judge(db, groups)
    return groups, j, {g.title: j.verdicts[dv.item_key(g)] for g in groups}


def test_each_verdict_on_real_pages(db, library):
    groups, j, _ = verdicts(db)
    # 20 archives, 19 to read: 9.1 has the same bytes as 9, so one read (one signature) serves both
    assert len(j.pending) == 19 and all(v.kind == VERDICT_UNDECIDED for v in j.verdicts.values())
    report = hash_files(db, j.pending)
    assert (report.hashed, report.unreadable, report.skipped) == (19, 0, 0) and report.pages == 297
    groups, j, by = verdicts(db)
    assert j.pending == []
    alpha, bravo, charlie, delta, echo = (by[t] for t in ("Alpha", "Bravo", "Charlie", "Delta", "Echo"))
    assert alpha.kind == VERDICT_PARTS and alpha.evidence == "16 of 16 pages of ch 10 are in 10.1 + 10.2"
    assert alpha.headline == ("Same pages: the whole chapter 10 is its parts 10.1, 10.2 - keep one form (the parts are "
                              "marked Discard - the series holds whole chapters).")
    assert alpha.discard == (str(library["Alpha"]["010.1"]), str(library["Alpha"]["010.2"]))
    assert bravo.kind == VERDICT_COPY and bravo.evidence == ("20 of 20 pages of ch 9 are in 9.1, and 20 of 20 of 9.1's "
                                                             "are in ch 9 (all byte-identical)")
    assert bravo.discard == (str(library["Bravo"]["009.1"]),)               # the later of the two
    assert charlie.kind == VERDICT_WRONG and charlie.stand_in and not charlie.hidden
    assert charlie.evidence == "18 of 18 pages of the file ch 14 are chapter 13's pages"
    assert charlie.discard == (str(library["Charlie"]["014"]),)
    assert delta.kind == VERDICT_DISTINCT and delta.hidden and delta.discard == ()
    assert delta.evidence == "none of ch 81's 16 pages are in 81.1 / 81.2 - separate chapters"
    assert echo.kind == VERDICT_DISTINCT and echo.hidden
    shown, hidden = split_shown(groups, j.verdicts)
    assert [g.title for g in shown] == ["Alpha", "Bravo", "Charlie"] and [g.title for g in hidden] == ["Delta", "Echo"]


def test_a_parts_series_keeps_its_parts(db, lib, pil):
    build(lib, pil, "Foxtrot", {"008.1": (range(10, 15), {}), "008.2": (range(15, 20), {}), "009.1": (range(20, 25), {}),
                                "009.2": (range(25, 30), {}), "010": (range(30, 46), {}),
                                "010.1": (range(30, 38), {"quality": 65}), "010.2": (range(38, 46), {"quality": 65})})
    lib.scan()
    groups = find_whole_and_parts(db)
    hash_files(db, judge(db, groups).pending)
    (v,) = judge(db, groups).verdicts.values()
    assert v.kind == VERDICT_PARTS and v.discard == (str(lib.dir / "Foxtrot" / "Foxtrot c010.cbz"),)
    assert "the whole chapter is marked Discard - the series holds its chapters in parts" in v.headline


def test_unchanged_files_are_never_hashed_again_and_the_cache_follows_the_content(db, lib, pil, monkeypatch):
    files = build(lib, pil, "Golf", {"005": (range(50, 60), {}), "005.1": (range(50, 55), {}),
                                     "005.2": (range(55, 60), {})})
    lib.scan()
    calls = []
    real = page_hash.hash_archive
    monkeypatch.setattr(page_hash, "hash_archive", lambda path, expect=None: calls.append(path) or real(path, expect))
    groups = find_whole_and_parts(db)
    hash_files(db, judge(db, groups).pending)
    assert len(calls) == 3 and judge(db, groups).pending == []
    # a copy of a file under another name (a rename or a move keeps the signature): no new read
    os.rename(files["005.2"], files["005.2"].with_name("Golf c005.2 [renamed].cbz"))
    lib.files["Golf"] = [r for r in lib.files["Golf"] if "005.2" not in r] + ["Golf c005.2 [renamed].cbz"]
    lib.scan()
    groups = find_whole_and_parts(db)
    assert judge(db, groups).pending == [] and len(calls) == 3
    # a file changed in place: a new signature, read once more
    pil.write_cbz(files["005.1"], range(70, 75))
    lib.scan()
    groups = find_whole_and_parts(db)
    pending = judge(db, groups).pending
    assert [os.path.basename(f.path) for f in pending] == ["Golf c005.1.cbz"]
    hash_files(db, pending)
    assert len(calls) == 4 and judge(db, find_whole_and_parts(db)).pending == []


def test_a_file_that_changes_before_it_is_hashed_is_left_for_later(db, lib, pil):
    files = build(lib, pil, "Hotel", {"005": (range(50, 60), {}), "005.1": (range(50, 55), {}),
                                      "005.2": (range(55, 60), {})})
    lib.scan()
    pending = judge(db, find_whole_and_parts(db)).pending
    pil.write_cbz(files["005"], range(80, 95))
    report = hash_files(db, pending)
    assert (report.hashed, report.skipped) == (2, 1)


def test_hashing_time_on_synthetic_archives(db, lib, pil):
    """A budget, not a benchmark: lane D's pure-Python run took about 18 ms a page (88 s for 4,846 pages); the reduced
    decode plus the DCT here must stay well under 50 ms a page on a CI machine."""
    paths = [pil.write_cbz(lib.dir / "Timing" / f"Timing c{n:03d}.cbz", range(n * 100, n * 100 + 20)) for n in range(1, 4)]
    import time

    started = time.perf_counter()
    read = [page_hash.hash_archive(p) for p in paths]
    per_page = (time.perf_counter() - started) / sum(r.pages for r in read)
    assert all(r.ok for r in read) and per_page < 0.05, per_page

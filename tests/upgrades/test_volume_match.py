"""Content-based volume-to-chapter matching (:mod:`mangalist.volume_match`) and its integration with auto-upgrades
(:func:`mangalist.upgrades.after_filing`), manual set-aside (:func:`mangalist.upgrades.estimated_duplicates`) and
recorded volume contents (:mod:`mangalist.volume_contents`). Synthetic pages and invented names only."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path

import pytest

from mangalist import page_hash, upgrades
from mangalist.downloads.arrivals import run_arrivals
from mangalist.knowledge import VolumeInfo, from_mangapixer_item
from mangalist.states import GAP_CHAPTER, InventorySnapshot, State, compute_state
from mangalist.store import page_hashes
from mangalist.store.replacements import ReplacementStore
from mangalist.volume_contents import SOURCE_PROVEN, held_volume_contents, load_claims
from mangalist.volume_match import (HashedChapter, HashedVolume, best_run, match_volumes, proves)

from ..downloads.fakes import HASH, FakeQbt, candidate, scan
from ..states.helpers import TODAY, item, vol
from .conftest import SERIES, make_series, names

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)


def _hashes(start: int, count: int):
    """Distinct synthetic 64-bit hashes well separated in Hamming distance (splitmix64)."""
    out = []
    for n in range(start, start + count):
        x = (n + 1) * 0x9E3779B97F4A7C15 & 0xFFFF_FFFF_FFFF_FFFF
        x = ((x ^ (x >> 30)) * 0xBF58476D1CE4E5B9) & 0xFFFF_FFFF_FFFF_FFFF
        x = ((x ^ (x >> 27)) * 0x94D049BB133111EB) & 0xFFFF_FFFF_FFFF_FFFF
        out.append((x ^ (x >> 31)) & 0xFFFF_FFFF_FFFF_FFFF)
    return tuple(out)


# --- pure matcher tests (no Pillow needed) ----------------------------------------------------------------------


def test_best_run_ignores_toc_dividers_afterword_and_scanlator_credits():
    toc = _hashes(9000, 4)
    ch1_pages = _hashes(100, 12)
    divider = _hashes(9100, 2)
    ch2_pages = _hashes(200, 10)
    afterword = _hashes(9200, 8)
    # Volume has cover/ToC + ch1 (with 1 internal bonus page) + divider + ch2 + afterword
    vol_pages = toc + ch1_pages[:6] + _hashes(9300, 1) + ch1_pages[6:] + divider + ch2_pages + afterword
    # Chapter 1 has 1 scanlator credit at front and 2 at back (3 unmatched of 15 = 20% <= 25%)
    ch1_with_credits = _hashes(8000, 1) + ch1_pages + _hashes(8010, 2)
    run = best_run(ch1_with_credits, vol_pages)
    assert len(run) == 12 and proves(len(run), len(ch1_with_credits))
    assert run[0][1] == 4 and run[-1][1] == 16                  # skips the 4-page ToC and bridges the 1-page gap

    unrelated = _hashes(5000, 16)
    assert best_run(unrelated, vol_pages) == []


def test_proves_rejects_short_runs_and_partial_range_files():
    vol1 = HashedVolume("v1", D(1), (D(1),), _hashes(100, 30))
    vol2 = HashedVolume("v2", D(2), (D(2),), _hashes(130, 30))
    vol3 = HashedVolume("v3", D(3), (D(3),), _hashes(160, 30))

    # Only 4 matching pages -> below MIN_RUN_PAGES (5)
    short = HashedChapter("short.cbz", "1", _hashes(100, 4))
    assert match_volumes([vol1], [short]) == {}

    # Range file half in vol 1 (15 pages) and half in vol 2 (15 pages): NOT replaced when only vol 1 is filed
    spanning = HashedChapter("c1-2.cbz", "1-2", _hashes(115, 30))
    assert match_volumes([vol1], [spanning]) == {}
    # Replaced only when BOTH adjacent volumes 1 and 2 are filed
    both = match_volumes([vol1, vol2], [spanning])
    assert "c1-2.cbz" in both and both["c1-2.cbz"].volumes == (D(1), D(2)) and both["c1-2.cbz"].split

    # Never combined across non-adjacent volumes (1 and 3)
    gap_span = HashedChapter("gap.cbz", "1-3", _hashes(115, 15) + _hashes(160, 15))
    assert match_volumes([vol1, vol3], [gap_span]) == {}


# --- end-to-end tests with real synthetic CBZ archives (Pillow) -------------------------------------------------


@pytest.fixture
def pil():
    pytest.importorskip("PIL")
    from tests.duplicates import pages

    return pages


def _cbz_bytes(pil, tmp_path: Path, name: str, seeds, **kw) -> bytes:
    return pil.write_cbz(tmp_path / "_build" / name, seeds, **kw).read_bytes()


def _file_real_volumes(ledger, tmp_path, sid, sdir: Path, vol_payloads: dict, info_hash: str = HASH):
    qbt = FakeQbt(tmp_path / f"torrents-{info_hash[:4]}")
    qbt.save_root.mkdir(parents=True, exist_ok=True)
    vols = [str(n) for n in sorted(vol_payloads)]
    rec = ledger.create(sid, candidate(info_hash, title=f"{SERIES} v{'-'.join(vols)}"), vols, str(sdir))
    qbt.put(info_hash, f"{SERIES} pack", {f"{SERIES} v{str(v).zfill(2)} (Digital).cbz": payload
                                          for v, payload in vol_payloads.items()})
    report = run_arrivals(qbt, ledger, remove_completed=False)
    assert rec.id in report.filed, report
    return ledger.get(rec.id)


def test_partial_volume_list_still_replaces_all_proven_chapters_and_avoids_false_gaps(
        pil, db, ledger, library, holding, known, tmp_path):
    """The Ashiya-kun regression: MangaPixer's volume list only maps Vol. 1 to 4.1-5, while Vol. 1 holds 1, 2.1, 2.2,
    3.1, 3.2, 4.1, 4.2, 5. Page-hash proof moves all 8 chapters, leaves Ch. 6 alone, caches hashes, and widens Vol. 1's
    recorded contents to 1-5 so Ch. 1-3 are not flagged as missing."""
    known[:] = [VolumeInfo("1", "4.1", "5"), VolumeInfo("3", "12", "14")]

    # Distinct synthetic page seeds for each chapter, plus volume-only ToC/dividers/afterword & chapter scanlator pages
    ch_seeds = {
        "Ch. 0001.00 Vol. 001.cbz": list(range(100, 110)),
        "Ch. 0002.10 Vol. 001.cbz": list(range(200, 208)),
        "Ch. 0002.20 Vol. 001.cbz": list(range(220, 228)),
        "Ch. 0003.10 Vol. 001.cbz": list(range(300, 308)),
        "Ch. 0003.20 Vol. 001.cbz": list(range(320, 328)),
        "Ch. 0004.10 Vol. 001.cbz": list(range(400, 408)),
        "Ch. 0004.20 Vol. 001.cbz": list(range(420, 428)),
        "Ch. 0005.00 Vol. 001.cbz": list(range(500, 508)),
        "Ch. 0006.00.cbz": list(range(600, 610)),
    }
    vol1_seeds = (
        [9001, 9002]                            # cover + ToC
        + ch_seeds["Ch. 0001.00 Vol. 001.cbz"]
        + [9003]                                # divider
        + ch_seeds["Ch. 0002.10 Vol. 001.cbz"]
        + ch_seeds["Ch. 0002.20 Vol. 001.cbz"]
        + [9004]
        + ch_seeds["Ch. 0003.10 Vol. 001.cbz"]
        + ch_seeds["Ch. 0003.20 Vol. 001.cbz"]
        + [9005]
        + ch_seeds["Ch. 0004.10 Vol. 001.cbz"]
        + ch_seeds["Ch. 0004.20 Vol. 001.cbz"]
        + ch_seeds["Ch. 0005.00 Vol. 001.cbz"]
        + [9006, 9007, 9008]                    # afterword / bonus
    )
    # Add scanlator credit pages to chapters 1 and 5 (and re-encode Vol. 1 at lower JPEG quality)
    chapter_blobs = {}
    for rel, seeds in ch_seeds.items():
        if "0001.00" in rel or "0005.00" in rel:
            seeds = [8001] + seeds + [8002]
        chapter_blobs[rel] = _cbz_bytes(pil, tmp_path, rel, seeds)

    sid, sdir = make_series(db, library, chapter_blobs)
    vol1_blob = _cbz_bytes(pil, tmp_path, "v01.cbz", vol1_seeds, quality=70)
    _file_real_volumes(ledger, tmp_path, sid, sdir, {1: vol1_blob})

    report = upgrades.after_filing(db, ledger, now=NOW)
    assert report.files_moved == 8
    (batch,) = ReplacementStore(db).all()
    assert batch.status == "held" and len(batch.files) == 8
    assert all(f.proven for f in batch.files)
    assert names(sdir) == ["Ch. 0006.00.cbz", f"{SERIES} v01 (Digital).cbz"]
    assert page_hashes.hashed_count(db) >= 10                   # volume + all 9 chapters cached

    # After rescanning, recorded claims from the batch widen Vol. 1 from 4.1-5 to 1-5 so Ch. 1-3 are not missing!
    scan(db)
    claims = load_claims(db, [sid])[sid]
    assert any(c.source == SOURCE_PROVEN and c.volume == D(1) and c.lo == D(1) for c in claims)
    widened = held_volume_contents(claims, [D(1)], {D(1): (D("4.1"), D(5)), D(3): (D(12), D(14))})
    assert widened == {D(1): (D(1), D(5))}
    k = from_mangapixer_item(item(
        record__latestChapter="6",
        record__originVolumes=3,
        record__englishPublishers=[{"name": "Example Press", "volumes": 1, "chapters": None,
                                    "status": "Ongoing", "omnibus": False}],
        volumes={"source": "mangadex", "fetchedAt": "2026-11-23T00:00:00.000Z",
                 "items": [vol("1", "4.1", "5"), vol("3", "12", "14")]},
        completion=None, companions={},
    ))
    inv = InventorySnapshot(held_volumes=["1"], held_chapters=["6"], recorded_volume_contents=claims)
    st = compute_state(inv, k, folder_empty=False, today=TODAY)
    assert st.state == State.UP_TO_DATE and not [g for g in st.gaps if g.kind == GAP_CHAPTER]
    # Sanity check: without those proven claims, Ch. 1-3 WOULD be flagged as missing!
    st_without = compute_state(InventorySnapshot(held_volumes=["1"], held_chapters=["6"]), k,
                               folder_empty=False, today=TODAY)
    assert [(g.start, g.end) for g in st_without.gaps if g.kind == GAP_CHAPTER] == [("1", "3")]


def test_no_volume_list_still_replaces_chapters_proven_by_pages(pil, db, ledger, library, holding, known, tmp_path):
    known[:] = []                                               # MangaPixer has no volume list at all
    ch1 = list(range(10, 20))
    ch2 = list(range(30, 40))
    ch3 = list(range(50, 60))
    sid, sdir = make_series(db, library, {
        f"{SERIES} c001.cbz": _cbz_bytes(pil, tmp_path, "c1.cbz", ch1),
        f"{SERIES} c002.cbz": _cbz_bytes(pil, tmp_path, "c2.cbz", ch2),
        f"{SERIES} c003.cbz": _cbz_bytes(pil, tmp_path, "c3.cbz", ch3),
    })
    vol1_blob = _cbz_bytes(pil, tmp_path, "v1.cbz", [999] + ch1 + ch2 + [998])
    _file_real_volumes(ledger, tmp_path, sid, sdir, {1: vol1_blob})

    report = upgrades.after_filing(db, ledger, now=NOW)
    assert report.files_moved == 2
    (batch,) = ReplacementStore(db).all()
    assert batch.status == "held" and batch.error is None
    assert sorted(f.rel for f in batch.files) == [f"{SERIES} c001.cbz", f"{SERIES} c002.cbz"]
    assert names(sdir) == [f"{SERIES} c003.cbz", f"{SERIES} v01 (Digital).cbz"]


def test_set_aside_finds_leftover_chapters_by_page_proof_and_records_proven_claims(
        pil, db, library, holding, monkeypatch, tmp_path):
    """Already-filed volume with partial volume list (4.1-5) and leftover Ch. 1-3 on disk: manual Set aside finds them
    by page hashes, moves them to holding, and records proven=True so gap logic knows Vol. 1 holds 1-5."""
    monkeypatch.setattr(upgrades, "_knowledge_volumes", lambda db, series: ([VolumeInfo("1", "4.1", "5")], ""))
    monkeypatch.setattr(upgrades, "_knowledge_estimates", lambda db, series: ((), "no estimates"))
    ch1, ch2, ch6 = list(range(110, 120)), list(range(130, 140)), list(range(150, 160))
    sid, sdir = make_series(db, library, {
        f"{SERIES} v01.cbz": _cbz_bytes(pil, tmp_path, "v01.cbz", [900] + ch1 + ch2 + [901]),
        f"{SERIES} c001.cbz": _cbz_bytes(pil, tmp_path, "c001.cbz", ch1),
        f"{SERIES} c002.cbz": _cbz_bytes(pil, tmp_path, "c002.cbz", ch2),
        f"{SERIES} c006.cbz": _cbz_bytes(pil, tmp_path, "c006.cbz", ch6),
    })

    found = upgrades.estimated_duplicates(db, sid)
    assert found.status == "pending" and found.error == "by matching pages inside the volume"
    assert sorted(f.rel for f in found.files) == [f"{SERIES} c001.cbz", f"{SERIES} c002.cbz"]
    assert all(f.proven for f in found.files)

    batch = upgrades.set_aside_estimated(db, sid, [f.rel for f in found.files], now=NOW)
    assert batch.status == "held" and batch.download_id < 0 and all(f.proven for f in batch.files)
    assert names(sdir) == [f"{SERIES} c006.cbz", f"{SERIES} v01.cbz"]

    # Proven set-aside batch feeds load_claims even though download_id < 0 (estimates without proven still do not)
    claims = load_claims(db, [sid])[sid]
    assert held_volume_contents(claims, [D(1)], {D(1): (D("4.1"), D(5))}) == {D(1): (D(1), D(5))}


def test_max_pages_override_allows_volume_archives_above_chapter_limit(pil, tmp_path, monkeypatch):
    monkeypatch.setattr(page_hash, "MAX_PAGES", 3)
    path = pil.write_cbz(tmp_path / "omni.cbz", [1, 2, 3, 4, 5])
    assert not page_hash.hash_archive(path).ok
    assert page_hash.hash_archive(path, max_pages=10).ok

"""``MuWorker`` end to end on recorded MangaUpdates responses: the matcher's tier reaches the entry
and the cache, full records are not fetched twice, and a stale unconfirmed match is cleared. Offline
(the client and AniList are patched); needs PySide6's QtCore only, no display."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from manga_list import mu_cache, mu_client  # noqa: E402
from manga_list.gui import mu_worker  # noqa: E402
from manga_list.models import FileHit, MangaEntry  # noqa: E402

from .fixture_mu import FakeMangaUpdates  # noqa: E402

ROOT = Path("/library/Manga")


@pytest.fixture
def fake(monkeypatch) -> FakeMangaUpdates:
    # The cache lives in the per-test data folder (conftest.py sets MANGA_LIST_DATA_DIR).
    f = FakeMangaUpdates()
    monkeypatch.setattr(mu_client, "search_series_page", f.search_series_page)
    monkeypatch.setattr(mu_client, "get_series", f.get_series)
    monkeypatch.setattr(mu_client, "get_latest_releases", lambda *a, **k: [])
    monkeypatch.setattr(mu_client, "REQUEST_DELAY", 0)
    monkeypatch.setattr(mu_worker, "_fetch_anilist", lambda *a, **k: None)
    return f


def _entry(name: str, files) -> MangaEntry:
    folder = ROOT / name
    return MangaEntry(folder=folder, title=name, english_title=None,
                      files=[FileHit(path=folder / f, size=1, depth=0) for f in files])


def _run(entry: MangaEntry) -> list:
    updated = []
    worker = mu_worker.MuWorker([(0, entry)])
    worker.entry_updated.connect(lambda e, row: updated.append(row))
    worker.run()
    return updated


def test_auto_match_is_stored_with_band_and_version(fake):
    entry = _entry("Chainsaw Man", [f"Chainsaw Man v{i:02d}.cbz" for i in range(1, 21)])
    assert _run(entry) == [0]

    assert entry.mu_id == 75336092483
    assert entry.mu_band == "auto" and entry.mu_score == 1.0 and not entry.mu_confirmed
    assert entry.mu_work_class == "SERIES"
    # The record fetched by the retrieval loop is reused: one GET per id.
    assert len(fake.gets) == len(set(fake.gets))
    row = mu_cache.load_entry(entry.folder)
    assert row["mu_band"] == "auto" and row["mu_score_version"] == mu_cache.MU_SCORE_VERSION


def test_review_match_keeps_its_reasons(fake):
    entry = _entry("Look Back", [f"Look Back v{i:02d}.cbz" for i in range(1, 11)])
    _run(entry)
    assert entry.mu_band == "review"
    assert "CountConflict" in entry.mu_reasons
    assert mu_cache.load_entry(entry.folder)["mu_reasons"] == entry.mu_reasons


def test_unmatched_clears_a_stale_unconfirmed_match(fake):
    entry = _entry("Zzqx Nonexistent Synthetic Title", ["Zzqx Nonexistent Synthetic Title v01.cbz",
                                                        "Zzqx Nonexistent Synthetic Title v02.cbz"])
    # A legacy (version 1) unconfirmed match from the old matcher.
    mu_cache.save_entry(entry.folder, 999, "Something Else", "", None, mu_confirmed=False,
                        mu_score=1.0, mu_score_version=1, scan_latest_chapter=12.0,
                        completed_in_origin=True)
    _run(entry)
    assert entry.mu_band == "unmatched"
    assert entry.mu_id is None and entry.mu_title is None
    # Progress read from the old record goes with it (Behind / Completed must not show it).
    assert entry.scan_latest_chapter is None and entry.completed_in_origin is None
    assert mu_cache.load_entry(entry.folder) is None


def test_confirmed_match_is_never_rescored(fake):
    entry = _entry("Berserk", [f"Berserk v{i:02d}.cbz" for i in range(1, 6)])
    mu_cache.save_entry(entry.folder, 51239621230, "Berserk", "", None, mu_confirmed=True,
                        mu_score=0.4, mu_score_version=1)
    _run(entry)
    assert fake.searches == []  # refresh only, no search
    row = mu_cache.load_entry(entry.folder)
    assert row["mu_confirmed"] is True
    assert row["mu_score_version"] == 1 and row["mu_score"] == 0.4


def test_collection_folder_is_not_searched(fake):
    entry = _entry("Shelf", ["Look Back.cbz", "Sayonara Eri.cbz", "Hunter x Hunter v01.cbz",
                             "Akira v01.cbz", "Oyasumi Punpun v01.cbz"])
    _run(entry)
    assert fake.searches == []
    assert entry.mu_band == "not_a_work" and entry.mu_id is None

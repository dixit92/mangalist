"""Chapters a held volume holds by MangaPixer's ESTIMATED range (1.42.0 ``chaptersEstimated``): listed for the owner,
set aside only when confirmed, held (restorable) like an upgrade's batch; estimates never reach the volume list itself.
Made-up names only."""

from __future__ import annotations

import pytest

from mangalist import upgrades
from mangalist.knowledge import VolumeInfo, from_mangapixer_item
from mangalist.store.replacements import ReplacementConflict

from ..states.helpers import item
from .conftest import SERIES, data, make_series, names

ESTIMATES = (VolumeInfo("7", "51", "62"), VolumeInfo("8", "63", "74"), VolumeInfo("10", "87", "98"))


@pytest.fixture
def estimates(monkeypatch):
    found = list(ESTIMATES)
    monkeypatch.setattr(upgrades, "_knowledge_estimates", lambda db, series: (tuple(found), "") if found
                        else ((), "MangaPixer estimates no volume of this series"))
    return found


def _series(db, library):
    files = {f"{SERIES} c{n}.cbz": data(f"chapter {n}") for n in range(51, 76)}      # 51-75 as chapters
    files.update({f"{SERIES} v07.cbz": data("volume 7", 9000), f"{SERIES} v08.cbz": data("volume 8", 9000)})
    return make_series(db, library, files)


def test_estimated_ranges_stay_out_of_the_volume_list():
    it = item(volumes={"items": [
        {"volume": "6", "chapters": {"from": "42", "to": "50"}, "sources": ["mangadex"]},
        {"volume": "7", "chapters": {"from": "51", "to": "62"}, "chaptersEstimated": True, "sources": ["estimate"]}]})
    k = from_mangapixer_item(it)
    assert [v.volume for v in k.volumes] == ["6"]
    assert [(v.volume, v.chapters_from, v.chapters_to) for v in k.estimated_volumes] == [("7", "51", "62")]


def test_the_chapters_inside_held_volumes_are_listed_and_set_aside_only_when_confirmed(db, library, holding,
                                                                                      estimates):
    sid, sdir = _series(db, library)
    found = upgrades.estimated_duplicates(db, sid)
    assert found.status == "pending" and found.volumes == ["7", "8"]                 # 10 is not held
    assert sorted(f.rel for f in found.files) == sorted(f"{SERIES} c{n}.cbz" for n in range(51, 75))
    assert {f.volume for f in found.files} == {"7", "8"} and len(found.volume_files) == 2
    assert names(sdir).count(f"{SERIES} c51.cbz") == 1                               # nothing moved by listing
    confirmed = [f"{SERIES} c{n}.cbz" for n in range(51, 63)]                        # volume 7 only
    batch = upgrades.set_aside_estimated(db, sid, confirmed)
    assert batch.status == "held" and batch.download_id < 0 and len(batch.files) == 12
    left = names(sdir)
    assert f"{SERIES} c51.cbz" not in left and f"{SERIES} c63.cbz" in left and f"{SERIES} c75.cbz" in left
    assert f"{SERIES} v07.cbz" in left and f"{SERIES} v08.cbz" in left            # the volumes are never touched
    upgrades.restore_batch(db, batch.id)
    assert f"{SERIES} c51.cbz" in names(sdir)                                       # restorable like any batch
    second = upgrades.set_aside_estimated(db, sid, [f"{SERIES} c63.cbz"])
    assert second.download_id < batch.download_id                                   # each by-hand batch its own id


def test_nothing_is_offered_without_estimates_or_a_held_volume(db, library, holding, estimates):
    sid, _ = _series(db, library)
    estimates[:] = [VolumeInfo("10", "87", "98")]
    assert upgrades.estimated_duplicates(db, sid).status == "nothing"
    estimates[:] = []
    found = upgrades.estimated_duplicates(db, sid)
    assert found.status == "nothing" and "estimates no volume" in found.error
    with pytest.raises(ReplacementConflict):
        upgrades.set_aside_estimated(db, sid, [f"{SERIES} c51.cbz"])

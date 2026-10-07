"""Placement: new volumes go where the series' existing layout puts them; unclear layouts ask the owner."""

from __future__ import annotations

import os

import pytest

from mangalist.downloads.placement import infer_placement, placement_for, same_or_inside
from mangalist.store import Unit


def vol(rel: str) -> Unit:
    return Unit(rel_path=rel, kind="volume", vol_from="1")


def chap(rel: str) -> Unit:
    return Unit(rel_path=rel, kind="chapter", ch_from="1")


def touch(path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")


@pytest.fixture
def sdir(tmp_path):
    d = tmp_path / "Manga" / "Series A"
    d.mkdir(parents=True)
    return d


def test_volumes_in_the_series_folder(sdir):
    touch(sdir / "Series A v01.cbz")
    touch(sdir / "Chapters" / "Series A c010.cbz")
    p = infer_placement(str(sdir), [vol("Series A v01.cbz"), chap("Chapters/Series A c010.cbz")])
    assert p.target_dir == str(sdir) and not p.ambiguous and p.reason == "volumes live in the series folder"


def test_volumes_in_one_subfolder(sdir):
    touch(sdir / "Volumes" / "Series A v01.cbz")
    touch(sdir / "Volumes" / "Series A v02.cbz")
    touch(sdir / "Series A c010.cbz")
    p = infer_placement(str(sdir), [vol("Volumes/Series A v01.cbz"), vol("Volumes/Series A v02.cbz"),
                                    chap("Series A c010.cbz")])
    assert p.target_dir == str(sdir / "Volumes") and p.reason == 'volumes live in "Volumes"'


def test_volumes_spread_over_folders_are_ambiguous(sdir):
    for rel in ("Vols/a v01.cbz", "Vols/a v02.cbz", "Old/a v03.cbz"):
        touch(sdir / rel)
    p = infer_placement(str(sdir), [vol("Vols/a v01.cbz"), vol("Vols/a v02.cbz"), vol("Old/a v03.cbz")])
    assert p.ambiguous and "spread over 2 folders" in p.reason
    assert p.options == (str(sdir / "Vols"), str(sdir / "Old"))        # most volumes first


def test_no_volumes_and_loose_files_or_an_empty_folder_mean_the_series_folder(sdir):
    assert infer_placement(str(sdir), []).target_dir == str(sdir)      # empty
    touch(sdir / "Series A c001.cbz")
    (sdir / "Extras").mkdir()
    p = infer_placement(str(sdir), [chap("Series A c001.cbz")])
    assert p.target_dir == str(sdir) and "no volumes" in p.reason


def test_no_volumes_and_only_subfolders_is_ambiguous(sdir):
    touch(sdir / "Chapters" / "c001.cbz")
    touch(sdir / "Side Story" / "s001.cbz")
    (sdir / ".hidden").mkdir()
    (sdir / "@eaDir").mkdir()
    p = infer_placement(str(sdir), [chap("Chapters/c001.cbz")])
    assert p.ambiguous and "only subfolders" in p.reason
    assert p.options == (str(sdir), str(sdir / "Chapters"), str(sdir / "Side Story"))


def test_volumes_as_folders_of_images_are_ambiguous(sdir):
    touch(sdir / "Vol. 01" / "001.jpg")
    touch(sdir / "Vol. 02" / "001.png")
    p = infer_placement(str(sdir), [])
    assert p.ambiguous and "folders of images" in p.reason and str(sdir / "Vol. 01") in p.options
    # An image folder that is not named like a volume (an artbook) does not make the layout unclear.
    touch(sdir / "Series A v03.cbz")
    os.rename(sdir / "Vol. 01", sdir / "Artwork")
    os.rename(sdir / "Vol. 02", sdir / "Covers")
    assert infer_placement(str(sdir), [vol("Series A v03.cbz")]).target_dir == str(sdir)


def test_a_missing_series_folder_or_volumes_folder(sdir, tmp_path):
    p = infer_placement(str(tmp_path / "Manga" / "Gone"), [vol("x v01.cbz")])
    assert p.ambiguous and "not there" in p.reason and p.options == ()
    p = infer_placement(str(sdir), [vol("Volumes/x v01.cbz")])         # stale units: the folder is gone
    assert p.ambiguous and "gone" in p.reason


def test_never_a_folder_outside_the_series_folder(sdir, tmp_path):
    p = infer_placement(str(sdir), [vol("../Other/x v01.cbz")])
    assert p.ambiguous and "not inside" in p.reason
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    touch(outside / "x v01.cbz")
    os.symlink(outside, sdir / "Linked")
    p = infer_placement(str(sdir), [vol("Linked/x v01.cbz")])
    assert p.ambiguous and p.target_dir is None
    assert not same_or_inside(str(sdir / "Linked"), str(sdir))
    assert same_or_inside(str(sdir), str(sdir)) and same_or_inside(str(sdir / "a" / "b"), str(sdir))


def test_placement_from_the_library_database(tmp_path):
    from mangalist import store

    from .conftest import scan

    store.reset_stores()
    db = store.get_store()
    root = tmp_path / "library" / "Manga"
    touch(root / "Series B" / "Volumes" / "Series B v01.cbz")
    touch(root / "Series B" / "Series B c020.cbz")
    r = db.add_root(str(root), "Manga")
    scan(db)
    # The scanner records "Series B/Volumes" as a nested series row; the layout still counts.
    assert {s.rel_path for s in db.list_series()} == {"Series B", "Series B/Volumes"}
    sid = db.get_series(r.id, "Series B").id
    p = placement_for(db, sid)
    assert p.target_dir == str(root / "Series B" / "Volumes") and p.series_dir == str(root / "Series B")
    nested = db.get_series(r.id, "Series B/Volumes").id
    assert placement_for(db, nested).target_dir == str(root / "Series B" / "Volumes")
    with pytest.raises(LookupError):
        placement_for(db, 999)

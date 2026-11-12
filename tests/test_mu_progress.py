"""The Behind text's estimate through AniList (:func:`mangalist.mu_progress.format_behind`): when progress is known in
one unit only, AniList's chapters-per-volume ratio converts it to the unit held on disk - in both directions."""

from __future__ import annotations

from mangalist.mu_progress import format_behind


def _behind(**kw) -> str:
    base = dict(licensed=True, publisher_chapters=None, publisher_volumes=None, publisher_status=None,
                scan_latest_chapter=None, disk_max_chapter=None, disk_max_volume=None,
                anilist_chapters=100, anilist_volumes=10)
    base.update(kw)
    return format_behind(**base)


def test_volumes_known_chapters_held_estimates_chapters():
    # 10 official volumes at 10 chapters a volume are about 100 chapters: chapter 50 is about 50 behind
    assert _behind(publisher_volumes=10, disk_max_chapter=50) == "~+50 ch official (~)"
    assert _behind(publisher_volumes=10, disk_max_chapter=100) == "Up to date"
    assert _behind(publisher_volumes=10, disk_max_chapter=120) == "~Disk ahead"


def test_chapters_known_volumes_held_estimates_volumes():
    assert _behind(publisher_chapters=100, disk_max_volume=4) == "~+6 vol official (~)"


def test_unlicensed_scan_volumes_known_chapters_held():
    assert _behind(licensed=False, scan_latest_volume=10, disk_max_chapter=50) == "~+50 ch (scan ~)"

"""The renamer with the real naming scheme (lane A's :mod:`mangalist.naming`), not the fake namer: the three things the
renamer relies on (integrator check, 2026-10-10) - a file already in the scheme is unchanged, an extra or unreadable name is
left alone, and the folder / extension it passes are what ``target_name`` expects. Synthetic names only."""

from __future__ import annotations

from decimal import Decimal

from mangalist.parsing import parse_name
from mangalist.renamer import LEFT_ALONE, RENAME, UNCHANGED, DefaultNamer, naming_available, plan_files


def _plans(folder, names, **kw):
    files = []
    for n in names:
        p = folder / n
        p.write_bytes(b"x" * 10)
        files.append((str(p), parse_name(n)))
    namer = DefaultNamer()
    return {p.name: p for p in plan_files(files, namer=namer, limits=namer.limits(None), **kw)}


def test_the_real_scheme_is_in_this_build():
    assert naming_available()


def test_fmd2_names_become_the_scheme_and_scheme_names_stay(tmp_path):
    fmd2 = "0126 [Ch. 0102 - A Quiet Morning [Some Group]].cbz"
    with_volume = "0012 [Vol. 3 Ch. 12.5 - Side Road [Other Team]].cbz"
    in_scheme = "Ch. 0050.00 (Already Done) [Some Group].cbz"
    plans = _plans(tmp_path, [fmd2, with_volume, in_scheme], series_title="Series X")
    assert plans[fmd2].status == RENAME and plans[fmd2].target == "Ch. 0102.00 (A Quiet Morning) [Some Group].cbz"
    assert plans[with_volume].target == "Ch. 0012.50 Vol. 003 (Side Road) [Other Team].cbz"
    assert plans[in_scheme].status == UNCHANGED                       # its own name: not a rename


def test_a_volume_from_the_volume_list_is_added_and_then_stable(tmp_path):
    name = "0126 [Ch. 0102 - A Quiet Morning [Some Group]].cbz"
    volume_of = {Decimal(102): Decimal(12)}.get
    plans = _plans(tmp_path, [name], series_title="Series X", volume_of=volume_of)
    target = plans[name].target
    assert target == "Ch. 0102.00 Vol. 012 (A Quiet Morning) [Some Group].cbz"
    again = _plans(tmp_path / "..", [], series_title="Series X")    # (no files: just the namer)
    assert again == {}
    (tmp_path / "b").mkdir()
    plans = _plans(tmp_path / "b", [target], series_title="Series X", volume_of=volume_of)
    assert plans[target].status == UNCHANGED                         # the renamed name reads back to itself


def test_volume_releases_get_the_series_title(tmp_path):
    name = "Series X v05 (2022) (Digital) (grp).cbz"
    plans = _plans(tmp_path, [name], series_title="Series X")
    assert plans[name].target == "Series X - Vol. 005 [grp].cbz"


def test_extras_and_unreadable_names_are_left_alone(tmp_path):
    names = ["readme.cbz", "cover art.cbz"]
    plans = _plans(tmp_path, names, series_title="Series X")
    assert all(plans[n].status == LEFT_ALONE and plans[n].target is None for n in names)

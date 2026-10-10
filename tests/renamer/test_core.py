"""The renamer's pure parts: what the scheme makes of each file, collisions, the order of the renames, batches."""

from __future__ import annotations

import os
from decimal import Decimal

import pytest

from mangalist.parsing import parse_name
from mangalist.renamer import (
    COLLISION,
    LEFT_ALONE,
    RENAME,
    UNCHANGED,
    SeriesPreview,
    make_batches,
    normalize_server,
    order_moves,
    plan_files,
)

from .conftest import fmd2
from .fakes import FakeLimits, FakeNamer


def _files(folder, names):
    out = []
    for n in names:
        p = folder / n
        p.write_bytes(b"x" * 10)
        out.append((str(p), parse_name(n)))
    return out


def _by_name(plans):
    return {p.name: p for p in plans}


def test_each_file_gets_its_verdict(tmp_path):
    names = [fmd2(126, "0102", "A Day Out", "Group A"), "Ch. 0001.00 (Start) [Group A].cbz", "omake.cbz",
             "Series v03 (2020) (Digital) (Rel).cbz", fmd2(7, "0007", "Bad")]
    namer = FakeNamer(overrides={fmd2(7, "0007", "Bad"): "Ch. 0007.00 (A:B).cbz"})
    plans = _by_name(plan_files(_files(tmp_path, names), namer=namer, series_title="Series X"))
    assert plans[names[0]].status == RENAME and plans[names[0]].target == "Ch. 0102.00 (A Day Out) [Group A].cbz"
    assert plans[names[0]].layer == "fmd2"
    assert plans[names[1]].status == UNCHANGED
    assert plans[names[2]].status == LEFT_ALONE and "no volume or chapter" in plans[names[2]].reason
    assert plans[names[3]].target == "Series X - Vol. 003 [Rel].cbz"
    assert plans[names[4]].status == LEFT_ALONE and "not allowed on Windows" in plans[names[4]].reason
    # the namer is told the folder (the length rule's Windows path) and the series title
    assert all(c[2] == str(tmp_path) and c[1] == "Series X" for c in namer.calls)


def test_volume_from_the_lookup_and_the_namer_saying_leave_it(tmp_path):
    names = [fmd2(1, "0010", "Ten"), fmd2(2, "0011")]
    namer = FakeNamer(overrides={fmd2(2, "0011"): None})
    plans = _by_name(plan_files(_files(tmp_path, names), namer=namer, series_title="S",
                                volume_of=lambda ch: Decimal(2) if ch == 10 else None))
    assert plans[names[0]].target == "Ch. 0010.00 Vol. 002 (Ten).cbz"
    assert plans[names[1]].status == LEFT_ALONE and "could not be read" in plans[names[1]].reason


def test_gone_files_and_naming_failures_are_left_alone(tmp_path):
    files = _files(tmp_path, [fmd2(1, "0001"), fmd2(2, "0002")])
    files.append((str(tmp_path / fmd2(3, "0003")), parse_name(fmd2(3, "0003"))))     # never written: gone
    plans = _by_name(plan_files(files, namer=FakeNamer(fail_on=(fmd2(2, "0002"),)), series_title="S"))
    assert plans[fmd2(1, "0001")].status == RENAME
    assert plans[fmd2(2, "0002")].status == LEFT_ALONE and "naming failed" in plans[fmd2(2, "0002")].reason
    assert plans[fmd2(3, "0003")].status == LEFT_ALONE and "gone" in plans[fmd2(3, "0003")].reason


def test_two_groups_copies_differ_by_group(tmp_path):
    names = [fmd2(5, "0005", "Same", "Group A"), fmd2(6, "0005", "Same", "Group B")]
    plans = plan_files(_files(tmp_path, names), namer=FakeNamer(), series_title="S")
    assert [p.status for p in plans] == [RENAME, RENAME]
    assert len({p.target for p in plans}) == 2


def test_two_files_to_one_name_both_stay(tmp_path):
    names = [fmd2(5, "0005", "Same", "Group A"), fmd2(9, "0005", "Same", "Group A")]
    plans = plan_files(_files(tmp_path, names), namer=FakeNamer(), series_title="S")
    assert [p.status for p in plans] == [COLLISION, COLLISION]
    assert "duplicates review" in plans[0].reason


def test_an_unchanged_file_keeps_its_name_against_a_rename(tmp_path):
    names = ["Ch. 0005.00 [Group A].cbz", fmd2(5, "0005", "", "Group A")]
    plans = _by_name(plan_files(_files(tmp_path, names), namer=FakeNamer(), series_title="S"))
    assert plans[names[1]].status == COLLISION
    assert plans[names[0]].status == COLLISION          # listed too (both halves), and of course not renamed


def test_a_name_taken_by_another_file_in_the_folder(tmp_path):
    (tmp_path / "ch. 0001.00.CBZ").write_bytes(b"other")      # not a series file (case differs: SMB compares folded)
    plans = plan_files(_files(tmp_path, [fmd2(1, "0001")]), namer=FakeNamer(), series_title="S")
    assert plans[0].status == COLLISION and "already in the folder" in plans[0].reason


def test_a_chain_is_not_a_collision(tmp_path):
    # b's new name frees a's: a -> b's old name, b -> c
    names = ["first.cbz", "second.cbz"]
    namer = FakeNamer(overrides={"first.cbz": "second.cbz", "second.cbz": "third.cbz"})
    plans = plan_files(_files(tmp_path, names), namer=namer, series_title="S")
    assert [p.status for p in plans] == [RENAME, RENAME]


def test_a_collision_that_blocks_a_chain_falls_back(tmp_path):
    names = ["a.cbz", "b.cbz", "c.cbz"]
    namer = FakeNamer(overrides={"a.cbz": "b.cbz", "b.cbz": "x.cbz", "c.cbz": "x.cbz"})
    plans = _by_name(plan_files(_files(tmp_path, names), namer=namer, series_title="S"))
    # b and c both want x: both stay; then a's wish (b.cbz) is taken by b staying
    assert plans["b.cbz"].status == COLLISION and plans["c.cbz"].status == COLLISION
    assert plans["a.cbz"].status == COLLISION


def test_flags_cut_titles_and_unit_words(tmp_path):
    long_title = "A very long chapter title " * 6
    names = [fmd2(1, "0001", long_title.strip(), "G"), fmd2(2, "0002", "The Vol 2 Begins", "G"),
             fmd2(3, "0003", "Plain", "G")]
    namer = FakeNamer()
    limits = FakeLimits(max_name_bytes=120)
    plans = _by_name(plan_files(_files(tmp_path, names), namer=namer, series_title="S", limits=limits))
    assert plans[names[0]].title_cut and not plans[names[0]].group_cut
    assert len(plans[names[0]].target.encode()) <= 120
    assert plans[names[1]].unit_words and not plans[names[1]].title_cut
    assert not plans[names[2]].unit_words and not plans[names[2]].title_cut


def test_order_moves_chain_and_cycle(tmp_path):
    a, b, c = (str(tmp_path / n) for n in ("a.cbz", "b.cbz", "c.cbz"))
    assert order_moves([(a, b), (b, c)]) == [(b, c), (a, b)]
    swapped = order_moves([(a, b), (b, a)])
    assert len(swapped) == 3
    temp = swapped[0][1]
    assert os.path.basename(temp).startswith("~mangalist-rename-") and swapped[-1] == (temp, b)
    # simulate: every step's destination is free when it runs
    present = {a, b}
    for src, dst in swapped:
        assert src in present and dst not in present
        present.remove(src)
        present.add(dst)
    assert present == {a, b}


def test_order_moves_case_only_rename_is_one_step(tmp_path):
    a = str(tmp_path / "ch. 0001.00.cbz")
    b = str(tmp_path / "Ch. 0001.00.cbz")
    assert order_moves([(a, b)]) == [(a, b)]


def _preview(root_id, n, title="S"):
    from mangalist.renamer import FilePlan

    files = [FilePlan(f"/lib/{title}/f{i}.cbz", f"g{i}.cbz", RENAME) for i in range(n)]
    return SeriesPreview(series_id=n, root_id=root_id, root_path=f"/lib{root_id}", folder=f"/lib/{title}", title=title,
                         files=files)


def test_batches_keep_series_whole_and_roots_apart():
    previews = [_preview(1, 3, "A"), _preview(1, 3, "B"), _preview(1, 5, "C"), _preview(2, 1, "D"), _preview(1, 0, "E")]
    batches = make_batches(previews, size=6)
    assert [[s.title for s in b.series] for b in batches] == [["A", "B"], ["C"], ["D"]]
    assert [b.n_files for b in batches] == [6, 5, 1]


@pytest.mark.parametrize("text,expected", [("SMIT-SERVER", "SMIT-SERVER"), ("\\\\SMIT-SERVER\\", "SMIT-SERVER"),
                                           ("  ", None), (None, None), ("nas.local", "nas.local")])
def test_server_names(text, expected):
    assert normalize_server(text) == expected


@pytest.mark.parametrize("bad", ["SMIT SERVER", "\\\\srv\\share", "a/b", "-x"])
def test_bad_server_names(bad):
    with pytest.raises(ValueError):
        normalize_server(bad)

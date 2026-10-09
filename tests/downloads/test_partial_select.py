"""File -> volume mapping for a pack: ranges, extras, unknown names, the fallbacks, and the numbers the panel shows."""

from __future__ import annotations

from mangalist.downloads.partial import (
    choose_files, choose_from_live, describe, describe_whole, file_lines, hint_for, priority_changes, size_text,
)
from mangalist.downloads.contracts import TorrentFile

from .fakes import candidate
from .torrents import MB

ROOT = "Series A v01-08 (Digital) (Group)"


def pack(*names, size=10 * MB):
    return [(f"{ROOT}/{n}", size) for n in names]


def keeps(sel):
    return [f.name.split("/")[-1] for f in sel.files if f.keep]


def test_only_the_files_of_wanted_volumes_are_kept():
    files = pack(*(f"Series A v{n:02d} (Digital).cbz" for n in range(1, 9)))
    sel = choose_files(files, ["3", "4", "08"], "volumes")
    assert keeps(sel) == ["Series A v03 (Digital).cbz", "Series A v04 (Digital).cbz", "Series A v08 (Digital).cbz"]
    assert (sel.total_files, sel.kept_files, sel.skipped_files) == (8, 3, 5) and sel.narrows
    assert sel.wanted == ("3", "4", "8") and sel.not_found == ()
    assert describe(sel) == "3 of 8 files, 30 MB of 80 MB"


def test_a_range_file_is_kept_when_it_holds_a_wanted_volume():
    sel = choose_files(pack("Series A v01-03.cbz", "Series A v04-06.cbz", "Series A v07.cbz"), ["5", "6"], "volumes")
    assert keeps(sel) == ["Series A v04-06.cbz"] and sel.not_found == ()


def test_a_range_that_also_holds_a_volume_the_owner_has_is_kept_and_said_so():
    sel = choose_files(pack("Series A v01-03.cbz", "Series A v04.cbz"), ["3"], "volumes")
    assert keeps(sel) == ["Series A v01-03.cbz"]
    reason = sel.files[0].reason
    assert "v3 (wanted)" in reason and "v1, v2" in reason and "nothing wanted is lost" in reason


def test_volume_numbers_are_exact_decimals_never_floats():
    sel = choose_files(pack("Series A v12.5.cbz", "Series A v12.cbz", "Series A v13.cbz"), ["12.5"], "volumes")
    assert keeps(sel) == ["Series A v12.5.cbz"] and sel.wanted == ("12.5",)
    sel = choose_files(pack("Series A v03.10.cbz", "Series A v03.1.cbz"), ["3.10"], "volumes")
    assert sel.files[0].volumes == ("3.10",) and sel.files[0].keep


def test_files_that_are_not_volume_archives_are_skipped():
    sel = choose_files(pack("Series A v01.cbz", "cover.jpg", "release.nfo", "Series A v02.zip"), ["1", "2"], "volumes")
    assert keeps(sel) == ["Series A v01.cbz", "Series A v02.zip"]
    assert {f.reason for f in sel.files if not f.keep} == {"not a volume archive"}


def test_extras_and_chapter_files_the_parser_recognises_are_skipped_not_kept_as_unknown():
    sel = choose_files(pack("Series A v01.cbz", "0012 [Extra [Group]].cbz", "Series A c045 (Digital).cbz"), ["1"],
                       "volumes")
    assert keeps(sel) == ["Series A v01.cbz"] and sel.unknown_kept == 0
    reasons = {f.name.split("/")[-1]: f.reason for f in sel.files}
    assert "extra" in reasons["0012 [Extra [Group]].cbz"] and "chapter" in reasons["Series A c045 (Digital).cbz"]


def test_an_omake_the_parser_cannot_place_is_kept_as_unknown():
    sel = choose_files(pack("Series A v01.cbz", "Series A Omake.cbz"), ["1"], "volumes")
    assert keeps(sel) == ["Series A v01.cbz", "Series A Omake.cbz"] and sel.unknown_kept == 1


def test_a_file_whose_name_says_no_volume_is_kept_never_silently_dropped():
    sel = choose_files(pack("Series A v01.cbz", "Series A v02.cbz", "The Great Book.cbz"), ["2"], "volumes")
    assert keeps(sel) == ["Series A v02.cbz", "The Great Book.cbz"]
    assert sel.unknown_kept == 1 and sel.files[2].unknown and "does not say which volume" in sel.files[2].reason
    assert sel.narrows and sel.skipped_files == 1


def test_bare_numbers_follow_the_kind_hint_like_arrivals_does():
    files = pack("01.cbz", "02.cbz", "03.cbz")
    with_hint = choose_files(files, ["2"], "volumes")
    assert keeps(with_hint) == ["02.cbz"]                       # a volumes release: 01.cbz is volume 1
    without = choose_files(files, ["2"], None)                  # a numberless title: the names say nothing
    assert keeps(without) == ["01.cbz", "02.cbz", "03.cbz"] and without.unknown_kept == 3


def test_when_no_file_is_a_wanted_volume_the_whole_pack_is_kept_with_the_reason():
    sel = choose_files(pack("Series A v01.cbz", "Series A v02.cbz"), ["7"], "volumes")
    assert not sel.narrows and sel.kept_files == 2 and "none of the files names a missing volume" in sel.whole_reason
    assert sel.not_found == ("7",)
    unknown = choose_files(pack("Alpha.cbz", "Beta.cbz"), ["7"], "volumes")
    assert not unknown.narrows and unknown.unknown_kept == 2
    empty_wanted = choose_files(pack("Series A v01.cbz", "Series A v02.cbz"), [], "volumes")
    assert not empty_wanted.narrows and "not known" in empty_wanted.whole_reason


def test_a_pack_that_is_all_wanted_does_not_narrow():
    sel = choose_files(pack("Series A v01.cbz", "Series A v02.cbz"), ["1", "2"], "volumes")
    assert not sel.narrows and sel.whole_reason is None and sel.skipped_files == 0


def test_a_missing_wanted_volume_is_reported():
    sel = choose_files(pack("Series A v01.cbz", "Series A v02.cbz", "Series A v03.cbz"), ["2", "9"], "volumes")
    assert sel.not_found == ("9",) and keeps(sel) == ["Series A v02.cbz"]


def test_a_single_file_torrent_is_one_choice():
    sel = choose_files([("Series A v02.cbz", 5 * MB)], ["2"], "volumes")
    assert sel.total_files == 1 and not sel.narrows and sel.kept_files == 1


def test_an_empty_file_list_is_a_problem_not_a_selection():
    sel = choose_files([], ["1"], None)
    assert not sel.readable and sel.problem and not sel.narrows


def test_the_hint_is_arrivals():
    assert hint_for(candidate(vol_from="2")) == "volumes" and hint_for(candidate(vol_from=None, vol_to=None)) is None


def test_texts_for_the_panel():
    sel = choose_files(pack("Series A v01.cbz", "Series A v02.cbz", "Series A v03.cbz", "cover.jpg"), ["2"], "volumes")
    assert describe(sel) == "1 of 4 files, 10 MB of 40 MB" and describe_whole(sel) == "4 files, 40 MB"
    big = choose_files([("Pack/Series A v01.cbz", 1_400_000_000), ("Pack/Series A v02.cbz", 410 * MB)], ["2"], "volumes")
    assert describe(big) == "1 of 2 files, 410 MB of 1.7 GB" and describe_whole(big) == "2 files, 1.7 GB"
    assert size_text(1) == "1 B" and size_text(1024 ** 3) == "1 GB"
    one = choose_files([("Series A v02.cbz", 5 * MB)], ["2"], "volumes")
    assert describe_whole(one) == "1 file, 5 MB"


def test_file_lines_list_kept_then_skipped_and_cap_each():
    sel = choose_files(pack(*(f"Series A v{n:02d}.cbz" for n in range(1, 31))), ["1"], "volumes")
    lines = file_lines(sel, limit=3).split("\n")
    assert lines[0] == "Downloaded:" and lines[1] == "  Series A v01.cbz"
    assert "Not downloaded:" in lines and "  ... and 26 more" in lines


def test_live_selection_and_priority_changes():
    live = [TorrentFile(f"{ROOT}/Series A v{n:02d}.cbz", 10, 0.0, index=n * 2, priority=1) for n in range(1, 5)]
    sel = choose_from_live(live, ["2"], "volumes")
    skip, enable = priority_changes(live, sel)
    assert skip == (2, 6, 8) and enable == ()
    live[1] = TorrentFile(live[1].name, 10, 0.0, index=4, priority=0)        # the wanted file is switched off
    live[0] = TorrentFile(live[0].name, 10, 0.0, index=2, priority=0)        # an unwanted one already is
    skip, enable = priority_changes(live, sel)
    assert skip == (6, 8) and enable == (4,)


def test_a_client_that_reports_no_file_ids_uses_the_list_position():
    live = [TorrentFile(f"{ROOT}/Series A v{n:02d}.cbz", 10, 0.0) for n in range(1, 4)]       # index -1
    skip, enable = priority_changes(live, choose_from_live(live, ["2"], "volumes"))
    assert skip == (0, 2) and enable == ()

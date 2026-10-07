from __future__ import annotations

import pytest

from mangalist.services.nyaa import parse_title

# (title, series, vol_from, vol_to, digital, pack, group)
VOLUME_CASES = [
    ("Berserk v42 (2025) (Digital) (LuCaZ)", "Berserk", "42", "42", True, False, "LuCaZ"),
    ("Berserk v01-v41 (2003-2023) (Digital) (Dark Horse)", "Berserk", "1", "41", True, True, "Dark Horse"),
    ("Berserk v01-12", "Berserk", "1", "12", False, True, None),
    ("Berserk Vol. 1-12 (Digital)", "Berserk", "1", "12", True, True, None),
    ("Berserk vol 3 (Digital) (Group)", "Berserk", "3", "3", True, False, "Group"),
    ("Berserk - Volume 03", "Berserk", "3", "3", False, False, None),
    ("Berserk v40 (2019) (Digital) (anon).cbz", "Berserk", "40", "40", True, False, "anon"),
    ("Berserk v05.5 (2020) (Digital) (Group)", "Berserk", "5.5", "5.5", True, False, "Group"),
    ("Berserk 01-12 (Digital)", "Berserk", "1", "12", True, True, None),
    ("Vinland Saga Vol. 01-10 (Danke-Empire)", "Vinland Saga", "1", "10", False, True, "Danke-Empire"),
    ("Vinland Saga Manga Vol(1-10)  ENG [Mangahere]", "Vinland Saga", "1", "10", False, True, "Mangahere"),
    ("Vinland Saga Volume 5 (translated)", "Vinland Saga", "5", "5", False, False, None),
    ("Overlord (Manga) Vol.16 - Vol.17", "Overlord", "16", "17", False, True, None),
    ("Overlord Volumes 1-9 + Extras", "Overlord", "1", "9", False, True, None),
    ("Overlord, Vol. 7 (Digital) (danke-Empire)", "Overlord", "7", "7", True, False, "danke-Empire"),
    ("Overlord volume 10 [Light Novel] draft", "Overlord", "10", "10", False, False, None),
    ("[Nazurdin]_Overlord_Volume_12", "Overlord", "12", "12", False, False, "Nazurdin"),
    ("[Grunbeld] Overlord v01-16 (Manga)", "Overlord", "1", "16", False, True, "Grunbeld"),
    ("[Kitzoku] Overlord  - Light Novel - Volume 01 - 07 - PDF (YenPress)", "Overlord", "1", "7", False, True,
     "YenPress"),
    ("Vinland Saga v01-10+ c01-76 [English]", "Vinland Saga", "1", "10", False, True, None),
    ("Berserk v05 (+ c041-045) (2020) (Digital) (Group)", "Berserk", "5", "5", True, False, "Group"),
    ("Overlord v01-16 [Yen Press] [LuCaZ]", "Overlord", "1", "16", False, True, "LuCaZ"),
    # a group name that merely starts with 'Digital' or ends with 'repack' is still a group
    ("Berserk of Gluttony v01-13 (2021-2025) (Digital) (DigitalMangaFan)", "Berserk of Gluttony", "1", "13", True,
     True, "DigitalMangaFan"),
    ("Berserk v01-38 (2003-2017) (Digital) (danke-repack)", "Berserk", "1", "38", True, True, "danke-repack"),
    ("Berserk v01-40 (2003-2019) (Digital) (Cyborgzx-repack) (Fixed White Lines)", "Berserk", "1", "40", True, True,
     "Cyborgzx-repack"),
    # the leading [Group] is the group when no trailing group is given
    ("[0v3r] Vinland Saga v25-26 (= Vinland Saga Omnibus 13)", "Vinland Saga", "25", "26", False, True, "0v3r"),
]


@pytest.mark.parametrize("title,series,vol_from,vol_to,digital,pack,group", VOLUME_CASES)
def test_volume_titles(title, series, vol_from, vol_to, digital, pack, group):
    p = parse_title(title)
    assert (p.series, p.vol_from, p.vol_to, p.digital, p.is_pack, p.group) == (
        series, vol_from, vol_to, digital, pack, group)
    assert not p.chapters_only


def test_a_reversed_range_is_put_right():
    p = parse_title("Series v12-v01 (Digital)")
    assert (p.vol_from, p.vol_to) == ("1", "12")


def test_volume_numbers_are_exact_strings():
    p = parse_title("Series v003-v012.5 (Digital)")
    assert (p.vol_from, p.vol_to) == ("3", "12.5")


@pytest.mark.parametrize("title,series", [
    ("Vinland Saga (2013-2025) (Digital + Scanlation) (lfp-DCP, Rillant, Project Vinland)", "Vinland Saga"),
    ("Berserk (2020-2023) Complete", "Berserk"),
    ("[Unpaid Ferryman] Overlord (Manga) (2016-2024) (Digital) (danke-Empire, Ushi, Kaos)", "Overlord"),
    ("Some Series Complete Collection", "Some Series"),
    ("Some Series (Complete)", "Some Series"),
])
def test_packs_without_numbers(title, series):
    p = parse_title(title)
    assert p.is_pack and p.vol_from is None and p.vol_to is None and not p.chapters_only
    assert p.series == series


def test_pack_group_and_digital():
    p = parse_title("Vinland Saga (2013-2025) (Digital + Scanlation) (lfp-DCP, Rillant, Project Vinland)")
    assert p.digital and p.group == "lfp-DCP, Rillant, Project Vinland"
    p = parse_title("[Unpaid Ferryman] Overlord (Manga) (2016-2024) (Digital) (danke-Empire, Ushi, Kaos)")
    assert p.group == "danke-Empire, Ushi, Kaos"          # the trailing group wins over the leading one


@pytest.mark.parametrize("title", [
    "Overlord LN Vols 01-12 [Yen Press] [EPUB] [Tagged]",
    "Overlord LN epub vol.1-10 (google Play Books compatible)",
    "Overlord, Vol. 1-3 (Light Novel)",
    "Overlord vol 1: The Undead King [Light Novel]",
    "Overlord - Vol 16 - The Half-Elf Demigod Part Il (Audiobook) [Troglodyte]",
    "Some Series (Novel) v01-05",
    "Some Series v03 [EPUB]",
])
def test_not_comic(title):
    p = parse_title(title)
    assert p.not_comic
    assert p.series in ("Overlord", "Some Series")


@pytest.mark.parametrize("title", [
    "Berserk v42 (2025) (Digital) (LuCaZ)", "Overlord (Manga) Vol.18", "Lin Vol. 3", "Colin v02 (Digital)",
    "Overlord v01-16 [Yen Press] [LuCaZ]",
])
def test_comics_are_not_flagged(title):
    assert not parse_title(title).not_comic


@pytest.mark.parametrize("title,label", [
    ("Vinland Saga v14 (2025) (Omnibus Edition) (Digital) (Rillant)", "Omnibus"),
    ("Vinland Saga Omnibus Vol. 01-10 (digital) (danke-empire)", "Omnibus"),
    ("Berserk Deluxe Edition Vol. 3", "Deluxe"),
    ("Some Series Master Edition v02 (Digital)", "Master Edition"),
    ("Some Series 3-in-1 v04", "3-In-1"),
])
def test_editions_that_renumber(title, label):
    assert parse_title(title).renumbered == label


def test_plain_releases_do_not_renumber():
    assert parse_title("Berserk v01-40 (2003-2019) (Digital) (danke-Empire)").renumbered is None


@pytest.mark.parametrize("title", [
    "Vinland Saga - Chapter 128 [MangaStream]",
    "Vinland Saga Chapter 120 [4chan]",
    "Vinland Saga 119 [4chan]",
    "Vinland Saga 101-108 [MiB,Mangabandits,4chan]",
    "Vinland Saga 71 [MangaStream & Binktopia]",
    "Some Series c045 (Digital) (Group)",
    "Some Series Ch. 12-14 (2021) (Digital) (Group)",
])
def test_chapter_releases(title):
    p = parse_title(title)
    assert p.chapters_only and p.vol_from is None
    assert p.series in ("Vinland Saga", "Some Series")


def test_a_number_in_the_series_name_is_not_a_volume():
    p = parse_title("Mob Psycho 100 v03 (Digital) (Group)")
    assert (p.series, p.vol_from) == ("Mob Psycho 100", "3")


def test_a_title_without_any_number_or_pack_word_has_no_units():
    p = parse_title("Berserk Official Guidebook (Digital) (danke-Empire)")
    assert p.vol_from is None and not p.is_pack and not p.chapters_only
    assert p.series == "Berserk Official Guidebook"


@pytest.mark.parametrize("title", ["", "   ", "[]", "[Group]", "(2020)", "v01", "....", "Vol. 1", "　"])
def test_never_raises(title):
    parse_title(title)

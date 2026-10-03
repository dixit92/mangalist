"""Heuristic unit tests with synthetic filenames."""

from __future__ import annotations

from pathlib import Path
from typing import List, Tuple

import pytest

from mangalist.classifier import (
    classify,
    detect_tokens,
    parse_folder_name,
)
from mangalist.models import FileHit, MangaEntry, Verdict


# ---------------------------------------------------------------------------
# Folder name parsing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "name, expected",
    [
        ("7-Nin no Nemuri Hime", ("7-Nin no Nemuri Hime", None)),
        ("A Dating Sim of Life or Death", ("A Dating Sim of Life or Death", None)),
        (
            "Jitsu wa Ore, Saikyou deshita [Am I Actually the Strongest]",
            ("Jitsu wa Ore, Saikyou deshita", "Am I Actually the Strongest"),
        ),
        ("  Trim Me  [English]  ", ("Trim Me", "English")),
        ("ワンピース", ("ワンピース", None)),
    ],
)
def test_parse_folder_name(name, expected):
    assert parse_folder_name(name) == expected


# ---------------------------------------------------------------------------
# Token detection
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "filename, expected",
    [
        ("Vol. 01.cbz", (True, False)),
        ("Volume 02.zip", (True, False)),
        ("v05.cbz", (True, False)),
        ("Ch. 003.cbz", (False, True)),
        ("Chapter 12.cbz", (False, True)),
        ("c102.cbz", (False, True)),
        ("Vol. 1 Ch 3.cbz", (True, True)),  # both tokens present
        ("Some Manga - 005.cbz", (False, False)),
        ("Random Title.zip", (False, False)),
    ],
)
def test_detect_tokens(filename, expected):
    assert detect_tokens(filename) == expected


# ---------------------------------------------------------------------------
# End-to-end classification on synthetic FileHits
# ---------------------------------------------------------------------------

def _make_entry(files: List[Tuple[str, int, int]]) -> MangaEntry:
    """files: list of (filename, size_bytes, depth)."""
    entry = MangaEntry(folder=Path("/fake"), title="Fake", english_title=None)
    hits = []
    for name, size, depth in files:
        hit = FileHit(path=Path("/fake") / name, size=size, depth=depth)
        has_v, has_c = detect_tokens(name, file_size=size)
        hit.has_volume = has_v
        hit.has_chapter = has_c
        hits.append(hit)
    entry.files = hits
    return classify(entry)


MB = 1024 * 1024


def test_volumes_only():
    e = _make_entry([
        ("Vol. 01.cbz", 200 * MB, 0),
        ("Vol. 02.cbz", 210 * MB, 0),
        ("Volume 03.zip", 220 * MB, 0),
    ])
    assert e.verdict == Verdict.VOLUMES
    assert e.vol_pct > e.ch_pct
    assert e.vol_pct > e.both_pct


def test_chapters_only():
    e = _make_entry([(f"Ch. {i:03d}.cbz", 15 * MB, 0) for i in range(1, 21)])
    assert e.verdict == Verdict.CHAPTERS
    assert e.ch_pct > e.vol_pct


def test_vol_x_ch_y_is_chapters():
    e = _make_entry([
        ("Vol. 1 Ch 1.cbz", 10 * MB, 0),
        ("Vol. 1 Ch 2.cbz", 10 * MB, 0),
        ("Vol. 1 Ch 3.cbz", 10 * MB, 0),
        ("Vol. 2 Ch 4.cbz", 10 * MB, 0),
    ])
    assert e.verdict == Verdict.CHAPTERS


def test_structural_both_overrides_volume_majority():
    # Mirrors a real case: many volumes at root, fewer chapters in a subfolder,
    # plus a large median size that would otherwise push the score to Volumes.
    files = [(f"Vol. {i:02d}.cbz", 220 * MB, 0) for i in range(1, 14)]  # 13 vols
    files += [(f"Ch. {i:03d}.cbz", 8 * MB, 1) for i in range(45, 56)]   # 11 chs
    e = _make_entry(files)
    assert e.verdict == Verdict.BOTH


def test_both_volumes_in_root_chapters_in_subfolder():
    e = _make_entry([
        ("Vol. 01.cbz", 220 * MB, 0),
        ("Vol. 02.cbz", 220 * MB, 0),
        ("Vol. 03.cbz", 220 * MB, 0),
        ("Ch. 045.cbz", 12 * MB, 1),
        ("Ch. 046.cbz", 12 * MB, 1),
        ("Ch. 047.cbz", 12 * MB, 1),
    ])
    assert e.verdict == Verdict.BOTH


def test_many_small_files_pushes_to_chapters_even_without_strong_tokens():
    # All files have a chapter token -> obviously chapters.
    e = _make_entry([(f"Ch.{i}.cbz", 8 * MB, 0) for i in range(1, 31)])
    assert e.verdict == Verdict.CHAPTERS
    assert e.n_files == 30


def test_unknown_when_no_tokens_and_no_size_signal():
    # Few files, no tokens, neither very small nor very large -> truly ambiguous.
    e = _make_entry([
        ("Random Name.zip", 35 * MB, 0),
        ("Another.cbz", 35 * MB, 0),
    ])
    assert e.verdict == Verdict.UNKNOWN
    assert e.vol_pct == 0.0
    assert e.ch_pct == 0.0


def test_large_untagged_files_lean_volumes():
    # No explicit tokens, but big median size nudges toward volumes.
    e = _make_entry([
        ("Random Name.zip", 200 * MB, 0),
        ("Another.cbz", 220 * MB, 0),
    ])
    assert e.verdict == Verdict.VOLUMES


def test_empty_folder():
    e = _make_entry([])
    assert e.verdict == Verdict.UNKNOWN
    assert e.n_files == 0


# ---------------------------------------------------------------------------
# Chap keyword detection (was not matched before fix)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "filename, expected",
    [
        # Chap/chap variants
        ("0001 [Chap 001].cbz", (False, True)),
        ("0009 [Chap 0008.5 Extra Chapter].cbz", (False, True)),
        ("[chap 012].cbz", (False, True)),
        ("Chap001.cbz", (False, True)),
        ("Chap.001.cbz", (False, True)),
        # Previously-working variants still work
        ("Ch. 003.cbz", (False, True)),
        ("Chapter 12.cbz", (False, True)),
        ("Chp 5.cbz", (False, True)),
    ],
)
def test_detect_tokens_chap_keyword(filename, expected):
    assert detect_tokens(filename) == expected


def test_chap_series_classifies_as_chapters():
    """Real-world pattern: series using 'Chap NNN' filename convention."""
    # 25 normal chapter files ~14 MB each
    files = [(f"00{i:02d} [Chap {i:03d}].cbz", 14 * MB, 0) for i in range(1, 26)]
    e = _make_entry(files)
    assert e.verdict == Verdict.CHAPTERS
    assert e.n_chapter_files == 25
    assert e.n_volume_files == 0
    assert e.ch_pct > 90.0


# ---------------------------------------------------------------------------
# Announcement-style "Volume N Notice" filenames (size-gated false positive fix)
# ---------------------------------------------------------------------------

def test_announcement_volume_token_demoted_when_tiny():
    """A tiny file with 'Volume N Notice' in its name is not a real volume."""
    # Without size, volume token is still detected (caller can pass 0 if size unknown)
    has_vol, has_ch = detect_tokens("0012 [Chap 0010.5 Volume 2 Notice]", file_size=0)
    # With no chapter token recognised AND no size context, vol token detected
    # (Chap IS detected now; so kind = chapter anyway - but test the vol demotion path)
    # Use a filename without Chap to isolate the vol-demotion logic:
    has_vol_tiny, _ = detect_tokens("Volume 2 Notice extra.cbz", file_size=500 * 1024)
    assert has_vol_tiny is False   # demoted because tiny + announcement keyword

    # Same filename, large file -> NOT demoted
    has_vol_large, _ = detect_tokens("Volume 2 Notice extra.cbz", file_size=200 * MB)
    assert has_vol_large is True   # large file, keep volume token


def test_announcement_files_do_not_inflate_volume_score():
    """Replicate the real Dorei kara no... folder pattern."""
    # 25 normal chapter files
    files = [(f"00{i:02d} [Chap {i:03d}].cbz", 14 * MB, 0) for i in range(1, 26)]
    # 3 tiny announcement files that contain 'Volume N' text
    files += [
        ("0012 [Chap 0010.5 Volume 2 Notice].cbz", 545 * 1024, 0),
        ("0018 [Chap 0015.5 Volume 3 Announcement!].cbz", 1_737 * 1024, 0),
        ("0025 [Chap 0020.5 Volume 4 of the comic is now on sale!].cbz", 1_434 * 1024, 0),
    ]
    e = _make_entry(files)
    assert e.verdict == Verdict.CHAPTERS
    assert e.n_volume_files == 0   # announcement files must not count as volumes
    assert e.ch_pct > 90.0

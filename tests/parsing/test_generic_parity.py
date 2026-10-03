"""Layer 4 - the generic fallback equals today's classifier on every name it handles."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from manga_list.classifier import TINY_FILE_BYTES, detect_tokens
from manga_list.models import FileHit, _max_chapter, _max_volume
from manga_list.parsing import Layer, parse_generic, parse_name

MB = 1024 * 1024

# Names today's classifier reads (synthetic), with optional file sizes.
GENERIC_NAMES = [
    ("Vol. 01.cbz", 0), ("Volume 02.zip", 0), ("v05.cbz", 0), ("Ch. 003.cbz", 0), ("Chapter 12.cbz", 0),
    ("c102.cbz", 0), ("Vol. 1 Ch 3.cbz", 0), ("Ch.12.5.cbz", 0), ("Ch.10-15.cbz", 0), ("Ch. 15-10.cbz", 0),
    ("Title Vol. 2 Ch. 7.5.cbz", 0), ("Title - Chapter 291.999.cbz", 0), ("Title Ch. 12.5", 0),
    ("Title Vol 3 Vol 4.cbz", 0), ("Title_ch_007.zip", 0), ("Title c001 + c002.cbz", 0),
    ("Title Volume 2 Notice.cbz", 50 * MB), ("Title Ch. 1 Ch. 2 Ch. 3.cbz", 0), ("Title chap 4.cbz", 0),
    ("Title Chp. 0009.cbz", 0), ("Title v1 extra.cbz", 0), ("Some Series c012 + 013 (Grp).cbz", 0),
]


@pytest.mark.parametrize("name, size", GENERIC_NAMES)
def test_generic_equals_todays_classifier(name, size):
    r = parse_generic(name, file_size=size)
    assert r is not None and r.layer is Layer.GENERIC
    has_vol, has_ch = detect_tokens(name, file_size=size)
    hit = FileHit(path=Path("/fake") / name, size=size, depth=0, has_volume=has_vol, has_chapter=has_ch)
    assert r.legacy_kind == hit.kind
    assert (r.chapter is not None) == has_ch
    if has_vol:
        assert r.volume.end == Decimal(str(_max_volume([hit])))
    if has_ch:
        assert r.chapter.end == Decimal(str(_max_chapter([hit])))


@pytest.mark.parametrize("name, size", GENERIC_NAMES)
def test_parse_name_falls_back_to_the_same_answer(name, size):
    r = parse_name(name, file_size=size)
    if r.layer is Layer.GENERIC:
        assert r == parse_generic(name, file_size=size)
        has_vol, has_ch = detect_tokens(name, file_size=size)
        hit = FileHit(path=Path(name), size=size, depth=0, has_volume=has_vol, has_chapter=has_ch)
        assert r.legacy_kind == hit.kind


def test_most_generic_names_reach_the_generic_layer():
    layers = [parse_name(n, file_size=s).layer for n, s in GENERIC_NAMES]
    assert layers.count(Layer.GENERIC) >= len(GENERIC_NAMES) - 3


def test_announcement_demotion_is_kept():
    name = "Title Volume 2 Notice.cbz"
    assert parse_generic(name, file_size=TINY_FILE_BYTES - 1) is None      # today: no volume token
    assert parse_generic(name, file_size=50 * MB).volume.end == 2


def test_generic_keeps_todays_extension_split():
    # Today's parser drops everything after the last dot, so a name without an extension loses decimals.
    assert str(parse_generic("Title Ch. 12.5").chapter) == "12"
    assert str(parse_generic("Title Ch. 12.5.cbz").chapter) == "12.5"


@pytest.mark.parametrize("name", ["Some Manga - 005.cbz", "Random Title.zip", "01.cbz"])
def test_generic_declines_names_without_tokens(name):
    assert detect_tokens(name) == (False, False)
    assert parse_generic(name) is None

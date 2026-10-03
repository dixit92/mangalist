"""Every visible change of wiring the layered parser into the scan, pinned: today's reading (token
heuristics) next to the new one. Names are synthetic."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from mangalist.classifier import detect_tokens
from mangalist.models import FileHit, _max_chapter, _max_volume
from mangalist.parsing import parse_name

D = Decimal


def _old(name):
    hv, hc = detect_tokens(name)
    hit = FileHit(path=Path(name), size=0, depth=0, has_volume=hv, has_chapter=hc)
    return hit.kind, _max_volume([hit]), _max_chapter([hit])


def _new(name):
    p = parse_name(name)
    hit = FileHit(path=Path(name), size=0, depth=0, parsed=p)
    return (hit.kind, None if p.volume is None else p.volume.end, None if p.chapter is None else p.chapter.end,
            p.is_extra)


# (name, today's kind / highest volume / highest chapter, new kind / volume / chapter / extra)
CHANGES = [
    # FMD2: the chapter title is never read for numbers.
    ("0005 [Ch. 5 - Back to Vol. 2].cbz", ("chapter", 2.0, 5.0), ("chapter", None, D("5"), False)),
    ("0012 [Chap 0010.5 Volume 2 Notice].cbz", ("chapter", 2.0, 10.5), ("chapter", None, D("10.5"), False)),
    # FMD2: a spaced " - " after the number starts the title, it is not a range.
    ("0012 [Ch. 10 - 12].cbz", ("chapter", None, 12.0), ("chapter", None, D("10"), False)),
    # FMD2: a unit-less body (3+ digit index) is a chapter without a number: an extra.
    ("0123 [Omake].cbz", ("ambiguous", None, None), ("chapter", None, None, True)),
    ("0123 [Side Story 2].cbz", ("ambiguous", None, None), ("chapter", None, None, True)),
    # Release names: a volume with loose chapters is a volume file; its chapters count.
    ("Title v05 (+ c041-045).cbz", ("chapter", 5.0, 45.0), ("volume", D("5"), D("45"), False)),
    ("Title v10 + 085-086 (2021) (Digital) (G).cbz", ("volume", 10.0, None), ("volume", D("10"), D("86"), False)),
    # Release names: the tags (group, edition) are never read for numbers.
    ("Title v01 (2019) (Digital) (Group c2).cbz", ("chapter", 1.0, 2.0), ("volume", D("1"), None, False)),
]


@pytest.mark.parametrize("name, old, new", CHANGES)
def test_visible_change(name, old, new):
    assert _old(name) == old
    assert _new(name) == new


UNCHANGED = [
    "0001 [Chap 001].cbz", "0009 [Chap 0008.5 Extra Chapter].cbz", "0003 [Chp. 3].cbz", "0003 [Ch.3].cbz",
    "0003 [Chapter 3].cbz", "0003 [Vol.1 Ch.3].cbz", "0003 [Vol. 01 Ch. 003 - Title [Grp]].cbz",
    "Title - 0012 [Ch. 12].cbz", "0005 [Ch. 5 - Episode 30].cbz", "0012 [Vol. 3].cbz", "0012 [Ch. 10-12].cbz",
    "Title v01 (2019) (Digital) (G).cbz", "Title c012 (v03) (G).cbz", "Title Vol. 3 Ch. 12.cbz",
    "Title_ch_007.zip", "[Group] Title - c001 [v2].cbz", "Title Volume 1 Chapter 2.cbz", "Ch.12.5.cbz",
    "01.cbz", "Title 012 (2019) (Digital) (G).cbz",
]


@pytest.mark.parametrize("name", UNCHANGED)
def test_same_reading_as_before(name):
    kind, vol, ch = _old(name)
    nkind, nvol, nch, _ = _new(name)
    assert (nkind, nvol, nch) == (kind, None if vol is None else D(repr(vol)), None if ch is None else D(repr(ch)))


@pytest.mark.parametrize("name, chapter", [("0001 [Chap 001].cbz", "1"), ("0003 [Chp. 3].cbz", "3"),
                                           ("0009 [Chap 0008.5 Extra Chapter].cbz", "8.5"),
                                           ("0004 [Vol. 1 Chap 4 - Title [G]].cbz", "4")])
def test_fmd2_heads_with_chap_keep_their_number(name, chapter):
    # Parser fix in this lane: "Chap" / "Chp" heads were read as numberless extras.
    p = parse_name(name)
    assert p.chapter is not None and p.chapter.end == D(chapter) and not p.is_extra

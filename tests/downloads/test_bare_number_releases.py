"""Releases whose numbers carry no volume mark (owner, 2026-10-13: a chapter pack "<Series> 001-051 (2024) (Digital)"
was read as volumes 1-51, its file "<Series> 003 (2024) ...cbz" as volume 3 - chapter 3 was downloaded and filed as
the missing volume). Bare numbers are never certain, the files are not read as volumes because of them, and a pack
whose files are all chapters is never sent for volumes. Made-up names, a fake qBittorrent."""

from __future__ import annotations

import pytest

from mangalist.downloads.contracts import Placement
from mangalist.downloads.finder import best_uncertain, certain
from mangalist.downloads.partial import CHAPTERS_ONLY, choose_files, hint_for
from mangalist.downloads.service import PackSetupError, PackWait, send_pick
from mangalist.services.nyaa.ranking import build_candidate
from mangalist.services.nyaa.rss import RssItem
from mangalist.services.nyaa.titles import parse_title

from .fakes import HASH, candidate
from .torrents import MB, PartialQbt

CHAPTER_PACK = "Series A 001-051 (2024) (Digital) (Group)"
CHAPTER_FILES = [f"Series A 0{n:02d} (2024) (Digital) (Group).cbz" for n in range(1, 52)]
URL = "https://nyaa.example/download/1.torrent"


def test_a_bare_number_range_is_read_but_marked_numbers_only():
    t = parse_title(CHAPTER_PACK)
    assert (t.vol_from, t.vol_to, t.numbers_only) == ("1", "51", True)
    assert not parse_title("Series A v01-03 (2024) (Digital) (Group)").numbers_only
    assert not parse_title("Series A Vol. 3 (Digital)").numbers_only


def item(title: str, seeders: int) -> RssItem:
    return RssItem(title=title, torrent_url=URL, view_url="https://nyaa.example/view/1", info_hash=title[-6:].lower(),
                   published="2026-10-01T00:00:00Z", seeders=seeders, category="3_1")


def test_numbers_only_ranks_below_a_release_that_states_its_volumes_and_says_why():
    bare = build_candidate(item(CHAPTER_PACK, seeders=50), parse_title(CHAPTER_PACK), ["3"], ["1", "2"])
    marked_title = "Series A v01-03 (2024) (Digital) (Other)"
    marked = build_candidate(item(marked_title, seeders=1), parse_title(marked_title), ["3"], ["1", "2"])
    assert bare.numbers_only and bare.covers_missing == ("3",) and marked.rank > bare.rank
    assert any("may be chapters" in r for r in bare.reasons)


def test_numbers_only_is_never_certain_but_may_be_offered_as_uncertain():
    bare = candidate(title=CHAPTER_PACK, vol_from="1", vol_to="51", covers_missing=("3",), numbers_only=True)
    assert not certain(bare) and best_uncertain([bare]) is bare
    assert certain(candidate(covers_missing=("3",)))


def test_the_files_are_not_read_as_volumes_because_of_bare_numbers():
    bare = candidate(title=CHAPTER_PACK, vol_from="1", vol_to="51", numbers_only=True)
    assert hint_for(bare) is None and hint_for(candidate()) == "volumes"
    sel = choose_files([(n, 10 * MB) for n in CHAPTER_FILES], ["3"], hint_for(bare))
    assert sel.chapters_only and sel.problem == CHAPTERS_ONLY and not sel.readable
    # with the old "volumes" hint the third file passed for volume 3
    assert choose_files([(n, 10 * MB) for n in CHAPTER_FILES], ["3"], "volumes").kept_files == 1


class _Tick:
    def __init__(self):
        self.t = 0.0

    def clock(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


def test_a_chapter_pack_is_never_sent_for_volumes(tmp_path, ledger, series):
    save = tmp_path / "torrents" / "mangalist"
    save.mkdir(parents=True)
    q = PartialQbt(save)
    q.offer(URL, HASH, CHAPTER_PACK, [(n, 10 * MB) for n in CHAPTER_FILES])
    tick = _Tick()
    _, sdir = series
    bare = candidate(title=CHAPTER_PACK, vol_from="1", vol_to="51", numbers_only=True, torrent_url=URL)
    with pytest.raises(PackSetupError, match="chapters, not volumes"):
        send_pick(q, ledger, series[0], bare, ["3"], Placement(str(sdir), str(sdir), "volumes live in the series folder"),
                  str(save), only_missing=True, wait=PackWait(timeout=5.0, interval=0.5, sleep=tick.sleep,
                                                              clock=tick.clock))
    assert "start" not in [c[0] for c in q.calls] and ledger.active() == []

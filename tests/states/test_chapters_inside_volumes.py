"""A hint for chapter files a held volume already holds, by the volume list's mapped range, however the volume arrived
(owner, 2026-10-14: volumes that came in outside MangaList's downloads kept 30 chapter files beside them). Display only:
"Needs attention". Made-up series."""

from __future__ import annotations

from mangalist.knowledge import from_mangapixer_item
from mangalist.states import ATTENTION_IN_VOLUMES, InventorySnapshot, compute_state

from .helpers import TODAY, item, vol

VOLUMES = [vol("12", "79", "85"), vol("13", "86", "92"), vol("14", "93", "100")]


def state(held_volumes, held_chapters, volumes=VOLUMES):
    k = from_mangapixer_item(item(volumes={"items": volumes}, record__latestChapter="140"))
    inv = InventorySnapshot(held_volumes=held_volumes, held_chapters=held_chapters)
    return compute_state(inv, k, today=TODAY, folder_empty=False)


def test_chapter_files_inside_a_held_volume_need_attention():
    st = state(["12", "13"], ["86", "87", "92", "113.2", "121"])
    assert ATTENTION_IN_VOLUMES in st.needs_attention


def test_a_part_counts_by_its_whole_chapter():
    assert ATTENTION_IN_VOLUMES in state(["13"], ["90.1", "121"]).needs_attention


def test_chapters_after_the_held_volumes_or_inside_an_unheld_one_do_not():
    assert ATTENTION_IN_VOLUMES not in state(["12", "13"], ["93", "94", "121"]).needs_attention   # volume 14 not held
    assert ATTENTION_IN_VOLUMES not in state(["12"], []).needs_attention


def test_a_volume_without_a_mapped_range_says_nothing():
    st = state(["13"], ["86", "87"], volumes=[vol("13", None, None)])
    assert ATTENTION_IN_VOLUMES not in st.needs_attention


def test_an_extra_after_a_volume_is_not_inside_it():
    # "N.5" is an extra (MangaPixer's split rule: a part only after N.4): 92.5 after volume 13 (86-92) is not inside it
    assert ATTENTION_IN_VOLUMES not in state(["13"], ["92.5", "121"]).needs_attention

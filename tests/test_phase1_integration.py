"""Phase 1 joined up: parsed archive names -> inventory (with MangaPixer's volume list) -> rescan state,
with the knowledge taken from a MangaPixer export item. Synthetic names and data only."""

from __future__ import annotations

from decimal import Decimal

from mangalist.inventory import as_state_inventory, inventory_of_parsed
from mangalist.knowledge import from_mangapixer_item
from mangalist.parsing import parse_name
from mangalist.states import GAP_VOLUME, MISSING_STATES, InventorySnapshot, compute_state

from .states.helpers import TODAY, item


def _inventory(names, volume_list=None):
    return inventory_of_parsed([(n, parse_name(n)) for n in names], volume_list)


def test_adapter_gives_the_states_shape():
    inv = _inventory(["Example Quest v01 (2025) (Digital) (Group).cbz",
                      "0012 [Vol. 2 Ch. 12.5 - Side Story [Group]].cbz"],
                     [{"volume": "1", "chapters": {"from": "1", "to": "8"}}])
    snap = as_state_inventory(inv)
    assert isinstance(snap, InventorySnapshot)
    assert Decimal("1") in snap.held_volumes
    assert Decimal("12.5") in snap.held_chapters
    assert (Decimal("1"), Decimal("8")) in snap.chapters_covered_by_volumes


def test_volumes_with_mangapixer_knowledge_show_the_missing_english_volume():
    export_item = item()  # English volumes 1-3 released, 4 announced; chapters per volume known
    names = ["Example Quest v01 (2025) (Digital) (Group).cbz",
             "Example Quest v02 (2025) (Digital) (Group).cbz"]
    inv = as_state_inventory(_inventory(names, export_item["volumes"]["items"]))
    st = compute_state(inv, from_mangapixer_item(export_item), folder_empty=False, today=TODAY)
    assert st.state in MISSING_STATES
    assert any(g.kind == GAP_VOLUME and g.start == "3" for g in st.gaps), st.gaps
    assert st.source == "mangapixer"

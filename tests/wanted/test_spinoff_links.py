"""A spin-off nested in its parent's folder with its OWN confirmed MangaUpdates link does not read the parent's MangaPixer
item (owner, 2026-10-18: a gaiden inside its parent's folder wanted the parent's volumes 5-20 and the find pass sent
the parent's pack into it). Made-up names; the shared library of :mod:`.library` (``Main Saga`` and its nested
``Main Saga Gaiden - Side Buddy``)."""

from __future__ import annotations

import pytest

from mangalist import store, wanted
from mangalist.knowledge import SOURCE_MANGAPIXER, SOURCE_OWN_MATCHER
from mangalist.services.mangapixer.resolve import Resolution, own_link_wins

from ..states.helpers import TODAY, item
from .library import build_library

GAIDEN = "Manga/Main Saga/Main Saga Gaiden - Side Buddy"


@pytest.fixture
def lib(tmp_path):
    store.reset_stores()
    return build_library(tmp_path / "lib")


def _views(lib):
    rev = {v: k for k, v in lib.folders.items()}
    return {rev[str(v.entry.folder)]: v for v in wanted.library_states(lib.db, today=TODAY)}


def test_a_spin_off_with_its_own_confirmed_link_reads_its_own_knowledge(lib):
    views = _views(lib)
    gaiden, parent = views[GAIDEN], views["Manga/Main Saga"]
    assert parent.knowledge.source == SOURCE_MANGAPIXER and parent.knowledge.title == "Main Saga"
    assert gaiden.knowledge.source == SOURCE_OWN_MATCHER
    assert (gaiden.knowledge.mu_id, gaiden.knowledge.title) == ("9004", "Main Saga Gaiden: Side Body")
    assert gaiden.held == ("1",) and not gaiden.state.missing_volumes      # its 1 English volume is held


def test_the_spin_off_wants_nothing_of_its_parents_volumes(lib):
    rev = {v: k for k, v in lib.folders.items()}
    got = {(rev[w.folder], w.group): w for w in wanted.wanted_list(lib.db, today=TODAY)}
    assert not any(k[0] == GAIDEN for k in got)
    # a nested series WITHOUT a link of its own still inherits its parent's item (franchise parts, by design)
    assert got[("Manga/Parent Series/Spin-off Story", "volumes")].missing == ("2", "3")


def test_same_series_does_not_take_the_spin_off_for_a_part_of_its_parent(lib):
    from mangalist.same_series import matches_of

    gaiden = lib.db.series_for_folder(lib.folder(GAIDEN))
    parent = lib.db.series_for_folder(lib.folder("Manga/Main Saga"))
    keys = {sid: key for sid, (key, _copy) in matches_of(lib.db, [gaiden.id, parent.id]).items()}
    assert keys == {parent.id: "mu:90000000013", gaiden.id: "mu:9004"}


def test_upgrades_do_not_map_the_spin_offs_volumes_by_its_parents_list(lib):
    from mangalist.upgrades import _knowledge_estimates, _knowledge_volumes

    gaiden = lib.db.series_for_folder(lib.folder(GAIDEN))
    assert gaiden.mu_id == 9004 and gaiden.mu_confirmed
    volumes, why = _knowledge_volumes(lib.db, gaiden)
    assert volumes is None and "own MangaUpdates link" in why
    estimates, why = _knowledge_estimates(lib.db, gaiden)
    assert estimates == () and "own MangaUpdates link" in why
    parent = lib.db.series_for_folder(lib.folder("Manga/Main Saga"))
    assert _knowledge_volumes(lib.db, parent)[0]                          # the parent's own item: unchanged


# --- the rule ------------------------------------------------------------------------------------------------------

class _Own:
    def __init__(self, mu_id, confirmed):
        self.mu_id, self.mu_confirmed = mu_id, confirmed


def _res(inherited: bool, external_id="90000000013", state="Confirmed") -> Resolution:
    return Resolution(item=item(record__externalId=external_id), link_state=state, inherited=inherited,
                      library_id="lib", node_id="n", source_trail=("Main Saga",))


@pytest.mark.parametrize("res, own, wins", [
    (_res(True), _Own(9004, True), True),                    # inherited, own confirmed link to another record
    (_res(True), _Own(9004, False), False),                  # an automatic own match never beats MangaPixer
    (_res(True), _Own(None, True), False),                   # no own link
    (_res(True), _Own(90000000013, True), False),            # the same record: the item (and its volume list) stays
    (_res(False), _Own(9004, True), False),                  # the folder's own item always wins
    (_res(True, state="DontMatch"), _Own(9004, True), True),  # the owner's confirmation beats an ancestor's Don't match
    (None, _Own(9004, True), False),
    (_res(True), None, False),
])
def test_own_link_wins(res, own, wins):
    assert own_link_wins(res, own) is wins

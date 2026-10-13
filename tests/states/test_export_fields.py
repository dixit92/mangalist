"""MangaPixer 1.42 export fields in knowledge and states: MangaDex's released chapters, MangaDex series records, the
declared edition and "Track completion: off" (synthetic items, made-up titles and ids)."""

from __future__ import annotations

from decimal import Decimal

from mangalist import knowledge as kn
from mangalist.knowledge import from_mangapixer_item
from mangalist.services.mangapixer import usable_record
from mangalist.states import GAP_CHAPTER, GAP_UPGRADE, GAP_VOLUME, STATE_ORDER, InventorySnapshot, State, compute_state

from .helpers import TODAY, item, vol

UUID = "00000000-0000-4000-8000-0000000000bb"


def inv(**kw) -> InventorySnapshot:
    return InventorySnapshot(**kw)


def state(inventory, knowledge, **kw):
    kw.setdefault("folder_empty", False)
    return compute_state(inventory, knowledge, today=TODAY, **kw)


def released(latest, chapters=None, language="en"):
    return {"language": language, "latestChapter": latest,
            "chapters": chapters if chapters is not None else int(Decimal(latest)) if latest else 0,
            "fetchedAt": "2026-10-12T12:00:00.000Z"}


def with_released(it, rel):
    it["companions"]["mangadexReleased"] = rel
    return it


def scanlation(latest=None, rel=None, **changes):
    """An unlicensed series MangaPixer links (no English edition, no volume list, no Completion)."""
    it = item(record__licensedEn=False, record__englishPublishers=[], record__latestChapter=latest, volumes=None,
              completion=None, **changes)
    return with_released(it, rel) if rel is not None else it


def mangadex_record(rel=None, **record):
    """A MangaDex series record as MangaPixer 1.42.0 exports it: no MangaUpdates fields."""
    rec = {"provider": "mangadex", "externalId": UUID, "siteUrl": f"https://mangadex.org/title/{UUID}",
           "title": "Example Drift", "altTitles": ["Example Drift Alt"], "type": None, "originStatus": "Ongoing",
           "originVolumes": None, "latestChapter": rel["latestChapter"] if rel else None, "totalChapters": None,
           "statusText": None, "licensedEn": None, "translationComplete": None, "completedInOrigin": False,
           "englishPublishers": [], "fetchedAt": "2026-10-12T12:00:00.000Z"}
    rec.update(record)
    it = item(record=rec, volumes=None, completion=None)
    it["companions"] = {"mangadex": UUID, "anilist": None}
    return with_released(it, rel) if rel is not None else it


def chapters(lo, hi):
    return [str(n) for n in range(lo, hi + 1)]


# --- knowledge -------------------------------------------------------------------------------------------------------


def test_mangadex_released_is_read_exactly_and_its_whole_latest_chapter_is_the_floor():
    k = from_mangapixer_item(scanlation(rel=released("22.3", 22)))
    r = k.mangadex_released
    assert (r.language, r.latest_chapter, r.chapters, r.fetched_at) == ("en", "22.3", 22, "2026-10-12T12:00:00.000Z")
    assert r.latest_whole == Decimal(22) and k.mangadex_latest == Decimal(22)
    assert k.mangadex_id == "00000000-0000-4000-8000-00000000000a"           # the companion record


def test_a_missing_or_malformed_released_block_is_none_and_another_language_is_not_counted():
    assert from_mangapixer_item(scanlation()).mangadex_released is None
    for bad in ({"latestChapter": "5", "chapters": 5}, released("5", -1), {**released("5"), "chapters": True},
                {**released("5"), "language": "  "}, "five"):
        assert kn.mangadex_released_of(with_released(scanlation(), bad)) is None, bad
    assert from_mangapixer_item(scanlation(rel=released(None, 0))).mangadex_latest is None     # none listed
    assert from_mangapixer_item(scanlation(rel=released("0.5", 1))).mangadex_latest is None    # only a part
    k = from_mangapixer_item(scanlation(rel=released("40", 40, language="FR")))
    assert k.mangadex_released.language == "fr" and k.mangadex_latest is None


def test_a_mangadex_record_is_accepted_without_mangaupdates_fields():
    k = from_mangapixer_item(mangadex_record(released("18", 18)))
    assert k.matched and k.mangadex_record and k.record_provider == kn.PROVIDER_MANGADEX
    assert k.mu_id is None and k.mu_url is None and k.mangadex_id == UUID
    assert k.title == "Example Drift" and k.alt_titles == ("Example Drift Alt",) and k.series_type is None
    assert k.latest_chapter is None and k.mangadex_latest == Decimal(18)     # MangaDex's count, not MangaUpdates'
    assert not k.licensed and k.licensed_en is None and k.english_publishers == ()
    assert usable_record(mangadex_record()) is not None and usable_record(item(record__provider="gcd")) is None


def test_a_mangadex_record_of_a_folder_that_is_not_a_series_keeps_only_its_label():
    it = mangadex_record()
    it["link"] = {"state": "CollectionAbout"}
    k = from_mangapixer_item(it)
    assert k.not_a_series and k.mu_id is None and k.mangadex_id == UUID and k.title == "Example Drift"


def test_switch_to_mangaupdates_reads_the_same_node_as_a_mangaupdates_record():
    before = from_mangapixer_item(mangadex_record(released("18", 18)))
    after = from_mangapixer_item(with_released(scanlation(latest="18", nodeId="n1"), released("18", 18)))
    assert before.mangadex_record and not after.mangadex_record
    assert after.mu_id == "90000000001" and after.latest_chapter == "18"


def test_the_declared_edition_replaces_the_regular_volume_list():
    it = item(completion={"answer": "MissingSome", "reason": "None", "upgradeAvailable": False, "upgradeVolumes": [],
                          "volumeTotalOverride": 6, "edition": "master"})
    k = from_mangapixer_item(it)
    assert k.edition == "master" and k.edition_volumes == Decimal(6) and k.edition_label == "Master edition"
    assert k.volumes == () and k.estimated_volumes == ()
    assert kn.declared_edition_of(it) == ("master", Decimal(6))


def test_an_edition_label_without_a_count_changes_nothing():
    for completion in ({"answer": "UpToDate", "reason": "None", "edition": "regular"},
                       {"answer": "UpToDate", "reason": "None", "volumeTotalOverride": 0, "edition": "omnibus"}):
        k = from_mangapixer_item(item(completion=completion))
        assert k.edition_label is None and k.edition_volumes is None and len(k.volumes) == 4


def test_tracking_off_is_read():
    assert from_mangapixer_item(item(tracking="off", completion=None)).tracking_off
    assert not from_mangapixer_item(item()).tracking_off


# --- MangaDex's latest chapter: only where nothing else tells ---------------------------------------------------


def test_cant_tell_without_a_latest_chapter_is_settled_by_mangadex():
    held = inv(held_chapters=chapters(1, 5) + ["5.5"])
    assert state(held, from_mangapixer_item(scanlation())).state == State.CANT_TELL
    s = state(held, from_mangapixer_item(scanlation(rel=released("5.5", 5))))
    assert s.state == State.UP_TO_DATE and not s.gaps                         # 5.5 is a part: the target is 5
    assert any("MangaDex lists English chapters up to 5" in r for r in s.reasons)
    s = state(inv(held_chapters=chapters(1, 3)), from_mangapixer_item(scanlation(rel=released("7", 7))))
    assert s.state == State.MISSING_CHAPTERS and s.missing_chapters == (("4", "7"),)


def test_a_low_mangaupdates_count_that_leaves_cant_tell_is_settled_by_mangadex():
    # Licensed volume folder; MangaUpdates' latest chapter 1 matches no volume of the (original) list: Can't tell.
    lst = {"items": [vol("1", "1", "6"), vol("2", "7", "12"), vol("3", "13", "18")]}
    it = item(record__latestChapter="1", record__englishPublishers=[{"name": "Example Press", "volumes": None,
                                                                    "chapters": None, "status": "Ongoing"}],
              volumes=lst, completion=None)
    held = inv(held_volumes=["1", "2"])
    assert state(held, from_mangapixer_item(it)).state == State.CANT_TELL
    s = state(held, from_mangapixer_item(with_released(it, released("18", 18))))
    assert s.state == State.MISSING_VOLUMES and s.missing_volumes == ("3",)
    assert any("MangaUpdates says 1" in r for r in s.reasons)


def test_a_series_mangalist_can_tell_keeps_its_answer_although_mangadex_lists_more():
    # A finished series held whole by MangaUpdates' count; MangaDex lists (taken-down) chapters far beyond it.
    it = scanlation(latest="20", record__completedInOrigin=True, record__translationComplete=True,
                    record__originStatus="Complete", rel=released("179", 179))
    s = state(inv(held_chapters=chapters(1, 20)), from_mangapixer_item(it))
    assert s.state == State.COMPLETE and not s.gaps and not any("MangaDex" in r for r in s.reasons)


def test_the_highest_wins_never_the_lowest():
    # A licensed series where MangaDex lists fewer chapters than MangaUpdates: MangaUpdates' 41 stays the target.
    it = with_released(item(volumes=None, completion=None), released("30", 30))
    s = state(inv(held_chapters=chapters(1, 35)), from_mangapixer_item(it))
    assert s.state == State.MISSING_CHAPTERS and s.missing_chapters == (("36", "41"),)


def test_an_empty_unlicensed_folder_with_no_other_count_takes_mangadexs_chapters():
    s = state(inv(), from_mangapixer_item(scanlation(rel=released("12", 12))), folder_empty=True)
    assert s.state == State.WANTED_SCANLATION and s.missing_chapters == (("1", "12"),)
    s = state(inv(), from_mangapixer_item(scanlation()), folder_empty=True)
    assert s.state == State.WANTED_SCANLATION and not s.gaps


def test_a_mangadex_record_is_compared_with_mangadexs_released_chapters():
    held = inv(held_chapters=chapters(1, 10))
    s = state(held, from_mangapixer_item(mangadex_record(released("12.5", 12))))
    assert s.state == State.MISSING_CHAPTERS and s.missing_chapters == (("11", "12"),)
    assert s.source == kn.SOURCE_MANGAPIXER and not s.needs_attention
    # A finished MangaDex record carries its final chapter as the total: held whole is Complete.
    done = mangadex_record(released("10", 10), originStatus="Completed", completedInOrigin=True, totalChapters=10)
    assert state(held, from_mangapixer_item(done)).state == State.COMPLETE
    # Read in another language, nothing tells: Can't tell (never a guess).
    assert state(held, from_mangapixer_item(mangadex_record(released("12", 12, language="es")))).state == \
        State.CANT_TELL


def test_mangadex_never_fills_in_for_a_series_that_is_not_matched():
    it = with_released(scanlation(link={"state": "NeedsReview"}, record=None), released("9", 9))
    s = state(inv(held_chapters=chapters(1, 3)), from_mangapixer_item(it))
    assert s.state == State.CANT_TELL and not s.gaps


# --- the declared edition ---------------------------------------------------------------------------------------------


def edition_item(total=6, edition="master", **changes):
    completion = {"answer": "MissingSome", "reason": "None", "upgradeAvailable": True, "upgradeVolumes": [3],
                  "computedAt": "2026-10-12T12:00:00.000Z", "volumeTotalOverride": total, "edition": edition}
    return item(completion=completion, **changes)


def test_an_edition_folder_counts_its_own_volumes_and_offers_no_upgrade():
    s = state(inv(held_volumes=["1", "2", "4"], held_chapters=chapters(13, 18)), from_mangapixer_item(edition_item()))
    assert s.state == State.MISSING_VOLUMES and s.missing_volumes == ("3", "5", "6")
    assert not s.gaps_of(GAP_UPGRADE) and not s.gaps_of(GAP_CHAPTER)       # MangaPixer's upgrade vol. 3 too
    assert all(g.note == "Master edition" for g in s.gaps_of(GAP_VOLUME))
    assert s.edition == "Master edition" and s.flags[0] == "Master edition"
    assert s.reasons[0] == "Master edition: 6 volumes (declared in MangaPixer)"
    assert "Chapter files are not compared with an edition's volumes" in s.reasons
    assert not s.upcoming                                                   # the regular edition's vol. 4 is not this


def test_an_edition_held_whole_is_complete_once_the_series_ended_else_up_to_date():
    held = inv(held_volumes=chapters(1, 6) + ["7"])
    ongoing = state(held, from_mangapixer_item(edition_item()))
    assert ongoing.state == State.UP_TO_DATE and not ongoing.gaps
    ended = edition_item(record__completedInOrigin=True, record__originStatus="Complete")
    s = state(held, from_mangapixer_item(ended))
    assert s.state == State.COMPLETE and "Finished, and held whole" in s.reasons
    assert state(inv(held_volumes=["1"]), from_mangapixer_item(ended), behind_override="done").state == \
        State.UP_TO_DATE


def test_an_empty_edition_folder_wants_the_editions_volumes_and_one_without_volume_files_cannot_tell():
    s = state(inv(), from_mangapixer_item(edition_item(total=3, edition="omnibus")), folder_empty=True)
    assert s.state == State.WANTED_OFFICIAL and s.missing_volumes == ("1", "2", "3") and s.edition == "Omnibus edition"
    s = state(inv(held_chapters=chapters(1, 9)), from_mangapixer_item(edition_item()))
    assert s.state == State.CANT_TELL and "no volume file to count" in s.reasons[0]


# --- tracking off -----------------------------------------------------------------------------------------------------


def test_tracking_off_gives_no_answer_at_all():
    off = from_mangapixer_item(with_released(item(tracking="off", completion=None), released("80", 80)))
    for held, empty in ((inv(held_volumes=["1"]), False), (inv(held_chapters=chapters(1, 4)), False), (inv(), True)):
        s = state(held, off, folder_empty=empty, rename_count=2)
        assert s.state == State.NOT_TRACKED and not s.gaps and not s.is_wanted and not s.upcoming
        assert s.reasons == ("Track completion is off for this folder in MangaPixer",)
        assert s.rename_pending and s.rename_count == 2 and s.mangapixer_answer is None
    assert state(inv(), off, folder_empty=False, needs_kind=True).needs_attention == ("needs kind",)
    assert STATE_ORDER.index(State.NOT_TRACKED) == STATE_ORDER.index(State.CANT_TELL) + 1

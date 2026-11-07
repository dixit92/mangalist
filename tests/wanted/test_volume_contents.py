"""The chapters a held volume contains stay off the wanted list when the volume list lost its map (mangalist.volume_contents
through mangalist.wanted; owner, 2026-11-06: "fix the gap logic so it knows what volumes 7-9 contain."): the series'
own records - chapter files named with their volume, held now or gone since (their ``archives`` rows), and the
replacement batches of filed downloads. A made-up library on disk, scanned and recorded as the window's scan does;
invented titles and numbers."""

from __future__ import annotations

from pathlib import Path

import pytest

from mangalist import store, wanted
from mangalist.gui.shell import GROUP_CHAPTERS
from mangalist.scanner import scan_and_record_library
from mangalist.store.mangapixer import MangaPixerCache, Mapping
from mangalist.store.replacements import NewBatch, ReplacedFile, ReplacementStore
from mangalist.volume_contents import ClaimsByFolder, SOURCE_FILED, SOURCE_NAME, claim, load_claims

from ..states.helpers import TODAY, item, vol

LIB_ID = "lib0mixed"
TITLE = "Mixed Saga"
ENDS = {"1": ("1", "5"), "2": ("6", "14"), "3": ("15", "23"), "4": ("24", "32"), "5": ("33", "41"), "6": ("42", "50")}
VOLUME_OF = {**{n: "7" for n in range(51, 59)}, **{n: "8" for n in range(59, 67)}, **{n: "9" for n in range(67, 76)}}


class _Lib:
    def __init__(self, id, name, kind="manga"):
        self.id, self.display_name, self.kind = id, name, kind
        self.folder_count = self.item_count = self.last_scan_at = None


def _item(estimates=False):
    """Volumes 1-6 mapped; MangaDex's map of 7-9 gone (listed without chapters - or with MangaPixer's estimate from
    the folder's files: 51-58, 59-66, 67-86); 9 English volumes out."""
    items = [vol(n, a, b) for n, (a, b) in ENDS.items()]
    if estimates:
        items += [dict(vol(str(n), str(a), str(b)), chaptersEstimated=True, sources=["estimate"])
                  for n, a, b in ((7, 51, 58), (8, 59, 66), (9, 67, 86))]
    else:
        items += [vol(str(n)) for n in (7, 8, 9)]
    return item(nodeId="n1", trail=[TITLE], record__title=TITLE, record__latestChapter="90", record__originVolumes=12,
                record__englishPublishers=[{"name": "Example Press", "volumes": 9, "chapters": None,
                                            "status": "Ongoing", "omnibus": False}],
                volumes={"source": "mangadex", "fetchedAt": "2026-11-04T00:00:00.000Z", "items": items},
                completion=None, companions={})


def chapter_name(n: int, tagged: bool) -> str:
    return f"chapters/Ch. {n:04d}.00 Vol. {int(VOLUME_OF[n]):03d}.cbz" if tagged and n in VOLUME_OF \
        else f"chapters/Ch. {n:04d}.00.cbz"


class Mixed:
    def __init__(self, base: Path, *, tagged: bool, estimates: bool = False):
        store.reset_stores()
        self.db = store.get_store()
        self.folder = base / "Manga" / TITLE
        names = [f"{TITLE} v{n:02d}.cbz" for n in range(1, 10)] + [chapter_name(n, tagged) for n in range(51, 91)]
        for i, name in enumerate(names):
            p = self.folder / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"x" * (1000 + i))
        self.root = self.db.add_root(str(base / "Manga"), "Manga")
        cache = MangaPixerCache(self.db)
        cache.save_libraries([_Lib(LIB_ID, "Manga")])
        cache.apply_page(LIB_ID, [], [_item(estimates)])
        cache.save_mapping(Mapping(self.root.id, LIB_ID, [], manual=True))
        self.scan()
        self.sid = self.db.series_for_folder(str(self.folder.resolve())).id

    def scan(self):
        scan_and_record_library(self.db.list_roots(), self.db)

    def remove(self, numbers, tagged: bool):
        for n in numbers:
            (self.folder / chapter_name(n, tagged)).unlink()
        self.scan()

    def batch(self, download_id: int, numbers, volume_of, status="purged"):
        files = [ReplacedFile(path=str(self.folder / chapter_name(n, False)), rel=chapter_name(n, False), size=1,
                              modified="2026-10-12T00:00:00+00:00", chapters=str(n), volume=volume_of(n))
                 for n in numbers]
        ReplacementStore(self.db).create(NewBatch(download_id=download_id, series_id=self.sid, status=status,
                                                  files=files, mode="holding"))

    def missing_chapters(self):
        got = [w for w in wanted.wanted_list(self.db, today=TODAY) if w.group == GROUP_CHAPTERS]
        assert len(got) <= 1
        return got[0].missing if got else ()


def numbers(lo, hi):
    return tuple(str(n) for n in range(lo, hi + 1))


@pytest.fixture
def tagged(tmp_path):
    return Mixed(tmp_path / "lib", tagged=True)


@pytest.fixture
def untagged(tmp_path):
    return Mixed(tmp_path / "lib", tagged=False)


def test_chapter_files_named_with_their_volume_are_claims_held_now_or_gone(tagged):
    now = load_claims(tagged.db)[tagged.sid]
    assert claim(7, 51, 51, SOURCE_NAME) in now and claim(9, 75, 75, SOURCE_NAME) in now and len(now) == 25
    tagged.remove(range(51, 76), tagged=True)
    gone = load_claims(tagged.db)[tagged.sid]                         # from the archives rows that stay ("missing")
    assert sorted(gone, key=lambda c: c.lo) == sorted(now, key=lambda c: c.lo)
    assert load_claims(tagged.db, [tagged.sid + 1]) == {}


def test_the_volumes_chapters_are_not_wanted_once_the_chapter_files_are_gone(tagged):
    assert tagged.missing_chapters() == ()
    tagged.remove(range(51, 76), tagged=True)
    assert tagged.missing_chapters() == ()                           # not 51-75: volumes 7-9 hold them


def test_a_chapter_no_volume_holds_is_still_wanted(tagged):
    tagged.remove([*range(51, 76), *range(80, 83)], tagged=True)
    assert tagged.missing_chapters() == ("80", "81", "82")


def test_an_estimated_range_hides_no_hole(tmp_path):
    lib = Mixed(tmp_path / "lib", tagged=True, estimates=True)
    lib.remove([*range(51, 76), *range(80, 83)], tagged=True)
    assert lib.missing_chapters() == ("80", "81", "82")             # not hidden by the estimate 67-86 of volume 9


def test_without_records_the_volumes_chapters_are_wanted(untagged):
    untagged.remove(range(51, 76), tagged=False)
    assert untagged.missing_chapters() == numbers(51, 75)           # nothing says what volumes 7-9 hold


def test_a_filed_downloads_batch_is_a_record_and_a_batch_set_aside_by_hand_is_not(untagged):
    untagged.remove(range(51, 87), tagged=False)
    # set aside by hand by MangaPixer's estimate: 12 chapters a volume, 76-86 put in volume 9 - not evidence
    untagged.batch(-1, range(51, 87), lambda n: str(7 + (n - 51) // 12))
    assert untagged.missing_chapters() == numbers(51, 86)
    # a filed download's batch, by the mapped list of the time; one the owner restored does not count
    untagged.batch(41, range(51, 67), lambda n: VOLUME_OF[n], status="restored")
    assert untagged.missing_chapters() == numbers(51, 86)
    untagged.batch(42, range(51, 76), lambda n: VOLUME_OF[n])
    claims = load_claims(untagged.db)[untagged.sid]
    assert {c.source for c in claims} == {SOURCE_FILED} and len(claims) == 25
    assert untagged.missing_chapters() == numbers(76, 86)


def test_the_window_reads_the_records_by_folder_once_per_scan(tagged):
    by_folder = ClaimsByFolder(tagged.db)
    assert len(by_folder.of(str(tagged.folder.resolve()))) == 25
    assert by_folder.of("/nowhere/else") == ()
    tagged.batch(43, range(76, 78), lambda n: "10")
    assert len(by_folder.of(str(tagged.folder.resolve()))) == 25       # cached until reset
    by_folder.reset()
    assert len(by_folder.of(str(tagged.folder.resolve()))) == 27


def test_unreadable_records_change_nothing(untagged, monkeypatch, caplog):
    import mangalist.volume_contents as vc

    def broken(db, series_ids=None):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(vc, "load_claims", broken)
    untagged.remove(range(51, 76), tagged=False)
    assert untagged.missing_chapters() == numbers(51, 75)
    assert "Volume contents" in caplog.text

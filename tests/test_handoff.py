"""Download hand-off, stage A: the wanted model, the MangaUpdates -> MangaDex lookup (recorded-shape answers, no
network), the gallery-dl input file and the FMD2 import file (read back the way FMD2's import reads it)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from manga_list import mu_cache
from manga_list.handoff import mangadex
from manga_list.handoff.exports import fmd2_bookmarks, gallery_dl_filter, gallery_dl_input, write_fmd2_import
from manga_list.handoff.resolve import resolve_all
from manga_list.handoff.wanted import CHAPTERS, UPGRADE, VOLUMES, WantedRange, wanted_for, wanted_list
from manga_list.models import FileHit, MangaEntry

ROOT = Path("/library/Manga")
BERSERK_UUID = "801513ba-a712-498c-8f57-cae55b38cc92"


def entry(name: str, files, **mu) -> MangaEntry:
    folder = ROOT / name
    e = MangaEntry(folder=folder, title=name, english_title=None,
                   files=[FileHit(path=folder / f, size=1, depth=0) for f in files])
    for k, v in mu.items():
        setattr(e, k, v)
    return e


def chapters(title: str, last: int):
    return [f"{title} - Chapter {i:03d}.cbz" for i in range(1, last + 1)]


# --- wanted -----------------------------------------------------------------------------------

def test_missing_chapters_continue_after_the_highest_chapter_on_disk():
    w = wanted_for(entry("Alpha", chapters("Alpha", 40), mu_id=7, mu_title="Alpha", scan_latest_chapter=52.0))
    assert w.ranges == (WantedRange(CHAPTERS, 41, 52),)
    assert w.ranges[0].describe() == "chapters 41-52"


def test_missing_volumes_of_a_licensed_series():
    files = [f"Beta v{i:02d}.cbz" for i in range(1, 9)]
    w = wanted_for(entry("Beta", files, mu_id=8, mu_title="Beta", licensed=True, publisher_volumes=12.0))
    assert w.of_kind(VOLUMES) == (WantedRange(VOLUMES, 9, 12),)


def test_a_chapter_folder_of_a_licensed_series_is_an_upgrade_candidate():
    w = wanted_for(entry("Gamma", chapters("Gamma", 30), mu_id=9, mu_title="Gamma", licensed=True,
                         publisher_volumes=5.0, scan_latest_chapter=30.0))
    assert w.ranges == (WantedRange(UPGRADE, 1, 5),)
    assert w.ranges[0].describe() == "volumes 1-5 (upgrade from chapters)"


def test_nothing_wanted_when_up_to_date_unmatched_marked_done_or_a_finished_omnibus():
    assert wanted_for(entry("Alpha", chapters("Alpha", 52), mu_id=7, scan_latest_chapter=52.0)) is None
    assert wanted_for(entry("Alpha", chapters("Alpha", 40), scan_latest_chapter=52.0)) is None  # not matched
    assert wanted_for(entry("Alpha", chapters("Alpha", 40), mu_id=7, scan_latest_chapter=52.0, behind_override="done")) is None
    omnibus = entry("Delta", ["Delta Omnibus v01.cbz"], mu_id=10, licensed=True, publisher_volumes=9.0,
                    publisher_status="Completed")
    assert wanted_for(omnibus) is None


def test_a_volume_folder_gets_no_chapter_range():
    # Volumes on disk carry no chapter numbers to continue from.
    files = [f"Beta v{i:02d}.cbz" for i in range(1, 9)]
    assert wanted_for(entry("Beta", files, mu_id=8, scan_latest_chapter=80.0)) is None


def test_wanted_titles_start_with_the_mangaupdates_title_and_keep_the_alternatives():
    w = wanted_for(entry("Solo Leveling", chapters("Solo Leveling", 100), mu_id=15180124327, mu_title="Solo Leveling",
                         mu_associated=["Na Honjaman Level-Up", "Solo Leveling", "  "], scan_latest_chapter=200.0))
    assert w.titles == ("Solo Leveling", "Na Honjaman Level-Up")
    assert [x.mu_id for x in wanted_list([entry("x", []), entry("A", chapters("A", 1), mu_id=1, scan_latest_chapter=3.0)])] == [1]


# --- MangaDex lookup --------------------------------------------------------------------------

def test_mu_slug_is_the_base36_id_mangadex_stores():
    assert mangadex.mu_slug(51239621230) == "njeqwry"
    assert mangadex.mu_slug(15180124327) == "6z1uqw7"


def record(uuid: str, mu: object) -> dict:
    return {"id": uuid, "type": "manga", "attributes": {"title": {"en": "x"}, "links": {"mu": mu} if mu else None}}


class FakeMangaDex:
    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def __call__(self, path, params):
        self.calls.append((path, params["title"], tuple(params["contentRating[]"])))
        return {"result": "ok", "data": self.answers.get(params["title"], [])}


def test_resolve_accepts_only_the_record_linking_to_the_series():
    fake = FakeMangaDex({"Berserk": [record("aaaa", "3txb7zc"), record(BERSERK_UUID, "njeqwry"), record("bbbb", None)]})
    assert mangadex.resolve(51239621230, ["Berserk"], fake) == BERSERK_UUID
    assert fake.calls[0][2] == mangadex.CONTENT_RATINGS


def test_resolve_tries_the_alternative_titles_and_never_takes_a_title_match_alone():
    # MangaDex lists Solo Leveling under its romaji title; an English search finds only look-alikes.
    fake = FakeMangaDex({"Solo Leveling": [record("arise", None)],
                         "Na Honjaman Level-Up": [record("32d76d19", "6z1uqw7")]})
    assert mangadex.resolve(15180124327, ["Solo Leveling", "Na Honjaman Level-Up"], fake) == "32d76d19"

    nothing = FakeMangaDex({"Solo Leveling": [record("arise", None)]})
    assert mangadex.resolve(15180124327, ["Solo Leveling"], nothing) is None


def test_resolve_stops_after_max_searches():
    fake = FakeMangaDex({})
    assert mangadex.resolve(1, ["a", "b", "", "c", "d", "e"], fake) is None
    assert [t for _, t, _ in fake.calls] == ["a", "b", "c"]


def test_the_mangadex_link_cache_remembers_hits_and_retries_old_misses():
    mu_cache.save_mangadex_id(1, "uuid-1", now=1000.0)
    mu_cache.save_mangadex_id(2, None, now=1000.0)
    assert mu_cache.load_mangadex_id(1, now=1e12) == "uuid-1"
    assert mu_cache.load_mangadex_id(2, now=1000.0 + 86400) is None
    assert mu_cache.load_mangadex_id(2, now=1000.0 + 31 * 86400) is mu_cache.UNKNOWN
    assert mu_cache.load_mangadex_id(3) is mu_cache.UNKNOWN


def test_resolve_all_uses_the_cache_and_never_caches_a_failure():
    a = wanted_for(entry("Berserk", chapters("Berserk", 10), mu_id=51239621230, mu_title="Berserk", scan_latest_chapter=20.0))
    b = wanted_for(entry("Other", chapters("Other", 10), mu_id=99, mu_title="Other", scan_latest_chapter=20.0))
    fake = FakeMangaDex({"Berserk": [record(BERSERK_UUID, "njeqwry")]})

    def flaky(path, params):
        if params["title"] == "Other":
            raise ConnectionError("offline")
        return fake(path, params)

    assert resolve_all([a, b], flaky) == [(a, BERSERK_UUID), (b, None)]
    assert mu_cache.load_mangadex_id(51239621230) == BERSERK_UUID
    assert mu_cache.load_mangadex_id(99) is mu_cache.UNKNOWN  # the failure is tried again next time

    calls = len(fake.calls)
    assert resolve_all([a], fake) == [(a, BERSERK_UUID)]
    assert len(fake.calls) == calls  # served from the cache


def test_resolve_all_stops_when_the_progress_callback_says_so():
    items = [wanted_for(entry(f"S{i}", chapters("S", 1), mu_id=100 + i, mu_title=f"S{i}", scan_latest_chapter=5.0))
             for i in range(3)]
    fake = FakeMangaDex({})
    seen = []

    def progress(done, total, title):
        seen.append((done, total))
        return done < 1

    result = resolve_all(items, fake, progress)
    assert [uuid for _, uuid in result] == [None, None, None]
    assert len(fake.calls) == 1 and seen == [(0, 3), (1, 3)]


# --- exports ----------------------------------------------------------------------------------

def test_gallery_dl_input_selects_chapters_by_number_per_url():
    berserk = wanted_for(entry("Berserk", chapters("Berserk", 364), mu_id=51239621230, mu_title="Berserk",
                               scan_latest_chapter=380.0))
    lost = wanted_for(entry("Lost", chapters("Lost", 2), mu_id=5, mu_title="Lost Series", scan_latest_chapter=4.0))
    text = gallery_dl_input([(berserk, BERSERK_UUID), (lost, None)])

    lines = [l for l in text.splitlines() if l and not l.startswith("#")]
    assert lines == ['-child-filter = "lang == \'en\' and 365 <= chapter <= 380"', f"https://mangadex.org/title/{BERSERK_UUID}"]
    # gallery-dl reads the value as JSON (a quoted string).
    key, _, value = lines[0][1:].partition("=")
    assert key.strip() == "child-filter" and json.loads(value.strip()) == gallery_dl_filter(365, 380)
    assert "# not found on MangaDex: Lost Series (MangaUpdates 5): chapters 3-4" in text


def fmd2_read(text: str):
    """FMD2's Domdomsoft import (frmImportFavorites.DMDHandle): every line holding <MangaLink> adds a URL, every line
    holding <MangaName> a name; they are paired by position; the module is found by the URL's host."""
    urls, names = [], []
    for line in text.splitlines():
        if "<MangaLink>" in line:
            urls.append(line.split("<MangaLink>", 1)[1].split("</MangaLink>", 1)[0])
        if "<MangaName>" in line:
            names.append(line.split("<MangaName>", 1)[1].split("</MangaName>", 1)[0])
    return list(zip(names, urls))


def test_fmd2_bookmarks_read_back_as_name_link_pairs_on_the_mangadex_host(tmp_path):
    a = wanted_for(entry("Berserk", chapters("Berserk", 10), mu_id=51239621230, mu_title="Berserk <Deluxe>",
                         scan_latest_chapter=20.0))
    b = wanted_for(entry("Lost", chapters("Lost", 2), mu_id=5, mu_title="Lost", scan_latest_chapter=4.0))
    path = write_fmd2_import(tmp_path / "fmd", [(a, BERSERK_UUID), (b, None)])

    assert path == tmp_path / "fmd" / "Config" / "Bookmarks"
    pairs = fmd2_read(path.read_text(encoding="utf-8"))
    assert pairs == [("Berserk (Deluxe)", f"https://mangadex.org/title/{BERSERK_UUID}")]
    assert re.match(r"https?://([^/]+)", pairs[0][1]).group(1) == "mangadex.org"
    assert fmd2_bookmarks([(b, None)]) == ""


# --- the GUI action ---------------------------------------------------------------------------

def test_the_context_action_writes_a_gallery_dl_file(tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

    from manga_list import mangadex_client
    from manga_list.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        out = tmp_path / "missing.txt"
        monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(out), "")))
        shown = []
        monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: shown.append(a[2])))
        monkeypatch.setattr(mangadex_client, "get_json", FakeMangaDex({"Berserk": [record(BERSERK_UUID, "njeqwry")]}))

        e = entry("Berserk", chapters("Berserk", 10), mu_id=51239621230, mu_title="Berserk", scan_latest_chapter=12.0)
        window._hand_off_chapters([e], "gallery-dl")

        assert "11 <= chapter <= 12" in out.read_text(encoding="utf-8")
        assert shown and "1 of 1 series found on MangaDex" in shown[0]
    finally:
        window.close()
        app.processEvents()

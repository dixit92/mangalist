"""A FAKE backend with the Suwayomi extras (the Suwayomi MVP) for the GUI tests: canned lookups built by the real chapter
rules (:mod:`mangalist.downloads.chapters`) from the fake Suwayomi's chapters, canned records, every call recorded. No
network, made-up names."""

from __future__ import annotations

import threading
from dataclasses import replace
from typing import List, Optional

from mangalist.downloads import chapters as chm
from mangalist.downloads.contracts import TOOL_SUWAYOMI, DownloadRecord, DownloadStatus, Placement
from mangalist.gui.downloads_backend import BackendError, SuwayomiCheck, SuwayomiSettingsView

from ..downloads.suwayomi_fakes import MANGA, MANGADEX, WEEB, chapter
from .conftest import FakeBackend

PASSWORD = "Suwa-Gui-Secret-DoNotShow"
FOLDER = "/lib/Example Webcomic"
#: Chapters 41-43 by two groups (42 by both), 44 nowhere.
CHAPTERS = (chapter(41, "41", "Alpha Scans"), chapter(42, "42", "Alpha Scans"), chapter(142, "42", "Beta Group"),
            chapter(43, "43", "Beta Group"))
WEEB_MANGA = replace(MANGA, id=9, source_id=WEEB.id, title="Example Webcomic")


def lookup_for(series_id: int, missing, *, match: Optional[chm.MangaMatch] = None, candidates=(), group=None,
               in_hand=None, error=None, placement: Optional[Placement] = None, notes=()) -> chm.ChapterLookup:
    out = chm.ChapterLookup(series_id=series_id, missing=tuple(missing))
    out.placement = placement or Placement(FOLDER, FOLDER, "chapters live in the series folder")
    out.error = error
    out.candidates = list(candidates)
    out.notes = list(notes)
    if match is None or error:
        return out
    out.match = match
    out.manga_title, out.source_name = match.manga.title or "Example Manga", match.source.display_name
    counts = chm.group_counts(CHAPTERS, [chm.to_decimal(m) for m in missing])
    out.groups = counts
    out.group = (chm.GroupChoice(group, "your choice for this series") if group is not None
                 else chm.default_group(counts, {}, [chm.to_decimal(m) for m in missing]))
    out.rows = chm.chapter_rows(CHAPTERS, missing, out.group.group, in_hand or {})
    return out


class FakeChapterBackend(FakeBackend):
    def __init__(self, *, connected: bool = True, **kw):
        super().__init__(**kw)
        self.view = (SuwayomiSettingsView(base_url="http://192.0.2.10:4567", username="owner", has_password=True,
                                          download_dir="/data/appdata/suwayomi/downloads") if connected
                     else SuwayomiSettingsView())
        self.suwayomi_saved: List[tuple] = []
        self.suwayomi_tested: List[tuple] = []
        self.suwayomi_test_error: Optional[str] = None
        self.check = SuwayomiCheck(version="v2.4.2366", flaresolverr=True, flaresolverr_url="http://192.0.2.10:8191",
                                   folder_found=True)
        self.installed = [MANGADEX, WEEB]
        self.allowed: Optional[List[str]] = None
        self.sources_error: Optional[str] = None
        self.lookups: List[tuple] = []
        self.confirmed: List[tuple] = []
        self.forgotten: List[int] = []
        self.groups_set: List[tuple] = []
        self.chapter_sends: List[tuple] = []
        self.matches: dict = {}                     # series id -> MangaMatch (confirmed or by MangaDex id)
        self.title_only: set = set()                # series ids found by title only (candidates first)
        self.lookup_error: Optional[str] = None
        self.series_group: dict = {}
        self.chapter_send_error: Optional[str] = None
        self.gate: Optional[threading.Event] = None

    # --- Settings ---
    def suwayomi_ready(self):
        return bool(self.view.base_url)

    def load_suwayomi(self):
        return self.view

    def save_suwayomi(self, view, password):
        self.suwayomi_saved.append((view, password))
        self.view = replace(view, has_password=view.has_password or bool(password))

    def test_suwayomi(self, view, password):
        self.threads.append(threading.get_ident())
        self.suwayomi_tested.append((view, password))
        if self.suwayomi_test_error:
            raise BackendError(self.suwayomi_test_error)
        return self.check

    def suwayomi_sources(self):
        self.threads.append(threading.get_ident())
        if self.sources_error:
            raise BackendError(self.sources_error)
        allowed = chm.allowed_sources(self.installed, self.allowed)
        ids = {s.id for s in allowed}
        return [(s, True) for s in allowed] + [(s, False) for s in self.installed if s.id not in ids]

    def set_suwayomi_sources(self, ids):
        self.allowed = list(ids)

    # --- the chapters panel ---
    def chapter_lookup(self, series_id, missing, titles):
        self.threads.append(threading.get_ident())
        self.lookups.append((series_id, tuple(missing), tuple(titles)))
        if self.gate is not None:
            self.gate.wait(10)
        if self.lookup_error:
            raise BackendError(self.lookup_error)
        match = self.matches.get(series_id)
        if match is None and series_id in self.title_only:
            cands = [chm.MangaMatch(WEEB, WEEB_MANGA, chm.HOW_TITLE),
                     chm.MangaMatch(WEEB, replace(WEEB_MANGA, id=10, title="Another Webcomic"), chm.HOW_TITLE)]
            return lookup_for(series_id, missing, candidates=cands, notes=["MangaPixer links no MangaDex id"])
        if match is None:
            match = chm.MangaMatch(MANGADEX, MANGA, chm.HOW_MANGADEX)
        in_hand = {n: r for r in self.record_list if r.is_chapters and r.status != DownloadStatus.FAILED
                   for n in r.wanted_chapters}
        return lookup_for(series_id, missing, match=match, group=self.series_group.get(series_id), in_hand=in_hand)

    def confirm_match(self, series_id, match):
        self.confirmed.append((series_id, match))
        self.matches[series_id] = replace(match, how=chm.HOW_CONFIRMED)

    def forget_match(self, series_id):
        self.forgotten.append(series_id)
        self.matches.pop(series_id, None)
        self.title_only.add(series_id)

    def set_series_group(self, series_id, group):
        self.groups_set.append((series_id, group))
        self.series_group[series_id] = group

    def send_chapters(self, series_id, match, picks, target_dir, manga_title="", source_name=""):
        self.threads.append(threading.get_ident())
        if self.chapter_send_error:
            raise BackendError(self.chapter_send_error)
        self.chapter_sends.append((series_id, match, [(c.id, c.number, c.scanlator) for c in picks], target_dir,
                                   manga_title, source_name))
        out = chm.ChapterSendOutcome()
        for ch in picks:
            rec = chapter_record(len(self.record_list) + 100, series_id, ch.number, group=ch.scanlator or "",
                                 batch=f"b{len(self.chapter_sends)}", source=source_name)
            self.record_list.append(rec)
            out.records.append(rec)
        return out


def chapter_record(id, series_id, number, *, status=DownloadStatus.SENT, group="Alpha Scans", batch="b1",
                   source="MangaDex (EN)", error=None) -> DownloadRecord:
    return DownloadRecord(id=id, series_id=series_id, info_hash=str(id), title=f"Vol.1 Ch.{number} - Example",
                          wanted_volumes=(), target_dir=FOLDER, status=status,
                          created_at="2026-10-10T10:00:00+00:00", updated_at="2026-10-10T10:05:00+00:00", error=error,
                          tool=TOOL_SUWAYOMI, wanted_chapters=(number,), batch=batch, source=source, group=group)

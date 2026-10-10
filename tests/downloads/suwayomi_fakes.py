"""A FAKE Suwayomi (a ChapterClient in memory that writes its CBZs the way Suwayomi-Server v2.4.2366 does - path rule and
ComicInfo as recorded in tests/fixtures/suwayomi) and a FAKE namer standing in for lane A's ``mangalist.naming``. No
network, made-up names."""

from __future__ import annotations

import zipfile
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from mangalist.downloads.chapter_arrivals import expected_path
from mangalist.downloads.contracts import (
    MangaChapters,
    QueuedChapter,
    SuwayomiChapter,
    SuwayomiManga,
    SuwayomiSource,
)

MANGADEX = SuwayomiSource(id="2499283573021220255", name="MangaDex", display_name="MangaDex (EN)", lang="en",
                          extension="eu.kanade.tachiyomi.extension.all.mangadex")
WEEB = SuwayomiSource(id="1000000000000000001", name="Weeb Example", display_name="Weeb Example", lang="en",
                      extension="eu.kanade.tachiyomi.extension.en.weebexample")
MD_ID = "00000000-0000-4000-8000-0000000c0000"
MANGA = SuwayomiManga(id=1, title="Example Manga", url=f"/manga/{MD_ID}", source_id=MANGADEX.id)

COMICINFO = """<?xml version='1.0' encoding='UTF-8' ?>
<ComicInfo xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <Title>{title}</Title>
  <Series>{series}</Series>
  <Number>{number}</Number>
  <Translator>{group}</Translator>
  <Web>{web}</Web>
  <Day>1</Day>
  <Month>2</Month>
  <Year>2018</Year>
</ComicInfo>"""


def chapter(cid: int, number: str, group: Optional[str] = "Alpha Scans", name: Optional[str] = None,
            upload: str = "1517511029000") -> SuwayomiChapter:
    return SuwayomiChapter(id=cid, manga_id=MANGA.id, name=name or f"Vol.1 Ch.{number} - Example Title {cid}",
                           number=number, scanlator=group, url=f"/chapter/c{cid}",
                           real_url=f"https://mangadex.example/chapter/c{cid}", upload_date=upload, source_order=cid)


class FakeSuwayomi:
    def __init__(self, download_dir: Optional[Path] = None, chapters: Sequence[SuwayomiChapter] = ()):
        self.download_dir = download_dir
        self.sources_list: List[SuwayomiSource] = [MANGADEX, WEEB]
        self.mangas: Dict[str, List[SuwayomiManga]] = {}            # query -> results
        self.chapter_list: List[SuwayomiChapter] = list(chapters)
        self.queued: Dict[int, QueuedChapter] = {}
        self.downloaded: set = set()
        self.calls: List[tuple] = []
        self.unreachable = False
        self.enqueue_error: Optional[Exception] = None
        self.delete_error: Optional[Exception] = None
        self.manga_title = MANGA.title

    def _check(self, what: str) -> None:
        self.calls.append((what,))
        if self.unreachable:
            raise ConnectionError("Suwayomi is down")

    # --- test helpers ---
    def by_id(self, cid: int) -> SuwayomiChapter:
        return next(c for c in self.chapter_list if c.id == cid)

    def finish(self, cid: int, *, data: bytes = b"page", number: Optional[str] = None, as_folder: bool = False,
               title: Optional[str] = None) -> Path:
        """Suwayomi finished downloading *cid*: its CBZ (or folder of images) is written, it leaves the queue."""
        ch = self.by_id(cid)
        path = Path(expected_path(str(self.download_dir), MANGADEX.display_name, title or self.manga_title, ch.name,
                                  ch.scanlator))
        path.parent.mkdir(parents=True, exist_ok=True)
        if as_folder:
            folder = path.with_suffix("")
            folder.mkdir()
            (folder / "001.png").write_bytes(data)
        else:
            with zipfile.ZipFile(path, "w") as zf:
                zf.writestr("001.png", data)
                zf.writestr("ComicInfo.xml", COMICINFO.format(
                    title=ch.name, series=self.manga_title, number=number or ch.number, group=ch.scanlator or "",
                    web=ch.real_url))
        self.queued.pop(cid, None)
        self.downloaded.add(cid)
        return path

    # --- ChapterClient ---
    def version(self) -> str:
        self._check("version")
        return "v2.4.2366"

    def sources(self):
        self._check("sources")
        return list(self.sources_list)

    def search(self, source_id: str, query: str):
        self.calls.append(("search", source_id, query))
        if self.unreachable:
            raise ConnectionError("down")
        return list(self.mangas.get(f"{source_id}:{query}", []))

    def chapters(self, manga_id: int) -> MangaChapters:
        self._check("chapters")
        return MangaChapters(manga=SuwayomiManga(id=manga_id, title=self.manga_title, url=MANGA.url,
                                                 source_id=MANGADEX.id),
                             source_name=MANGADEX.display_name,
                             chapters=tuple(c for c in self.chapter_list if c.manga_id == manga_id))

    def add_to_library(self, manga_id: int) -> None:
        self.calls.append(("add_to_library", manga_id))

    def enqueue(self, chapter_ids):
        self.calls.append(("enqueue", list(chapter_ids)))
        if self.enqueue_error is not None:
            raise self.enqueue_error
        for cid in chapter_ids:
            self.queued[cid] = QueuedChapter(cid, "QUEUED")
        return list(self.queued.values())

    def queue(self):
        self._check("queue")
        return list(self.queued.values())

    def chapters_by_id(self, chapter_ids):
        self._check("chapters_by_id")
        from dataclasses import replace

        return [replace(c, downloaded=c.id in self.downloaded) for c in self.chapter_list if c.id in set(chapter_ids)]

    def delete_downloaded(self, chapter_ids) -> None:
        self.calls.append(("delete_downloaded", list(chapter_ids)))
        if self.delete_error is not None:
            raise self.delete_error
        for cid in chapter_ids:
            self.downloaded.discard(cid)
            ch = self.by_id(cid)
            path = Path(expected_path(str(self.download_dir), MANGADEX.display_name, self.manga_title, ch.name,
                                      ch.scanlator))
            if path.exists():
                path.unlink()


class FakeNamer:
    """Lane A's contract, simplified: ``Ch. 0102.00 Vol. 012 (<title>) [<group>].cbz``; volumes from a dict."""

    def __init__(self, volumes: Optional[Dict[Decimal, Decimal]] = None):
        self.volumes = volumes or {}
        self.calls: List[dict] = []

    def chapter_file_name(self, chapter: Decimal, *, chapter_end=None, volume=None, title=None, group=None,
                          ext=".cbz", folder=None, limits=None) -> str:
        self.calls.append({"chapter": chapter, "volume": volume, "title": title, "group": group, "folder": folder})
        whole, _, frac = f"{chapter:.2f}".partition(".")
        name = f"Ch. {int(whole):04d}.{frac}"
        if volume is not None:
            name += f" Vol. {int(volume):03d}"
        if title:
            name += f" ({title})"
        if group:
            name += f" [{group}]"
        return name + ext

    def volume_lookup(self, db, series_id):
        return lambda number: self.volumes.get(number)

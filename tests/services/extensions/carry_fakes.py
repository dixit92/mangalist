"""A FAKE Suwayomi for the carry-over (in memory: the read-only calls :mod:`mangalist.services.extensions.carry_over`
makes) and helpers to set up the per-series choices. Made-up names; the source ids / packages are the fake host's."""

from __future__ import annotations

import json
from typing import Dict, List, Optional, Sequence

from mangalist.downloads.contracts import SuwayomiManga, SuwayomiSource
from mangalist.services.suwayomi.client import SuwayomiExtension, SuwayomiRepo, Unreachable
from mangalist.store.downloads import META_EXTENSIONS_SERIES, META_SUWAYOMI_SERIES

from .fake_host import KEI_URL, MANGADEX_EN, MANGADEX_PKG, SECOND_URL, WEEB_ID, WEEB_PKG

MANGADEX = SuwayomiSource(id=MANGADEX_EN, name="MangaDex", display_name="MangaDex (EN)", lang="en",
                          extension=MANGADEX_PKG)
WEEB = SuwayomiSource(id=WEEB_ID, name="Weeb Example", display_name="Weeb Example (EN)", lang="en", extension=WEEB_PKG)
#: A source whose extension no repository of the host lists.
LOST_ID = "3000000000000000003"
LOST = SuwayomiSource(id=LOST_ID, name="Lost Source", display_name="Lost Source (EN)", lang="en",
                      extension="eu.kanade.tachiyomi.extension.en.lostsource")


class FakeSuwayomiCarry:
    """Suwayomi as the carry-over reads it. Records every call; never changes anything (it has nothing to change)."""

    def __init__(self) -> None:
        self.sources_list: List[SuwayomiSource] = [MANGADEX, WEEB, LOST]
        self.extensions_list: List[SuwayomiExtension] = [
            SuwayomiExtension(MANGADEX_PKG, "MangaDex", "1.4.200", "all", repo=KEI_URL),
            SuwayomiExtension(WEEB_PKG, "Weeb Example", "1.4.3", "en", repo=KEI_URL),
            SuwayomiExtension(LOST.extension, "Lost Source", "1.0.0", "en", repo=KEI_URL)]
        self.repos: List[SuwayomiRepo] = [SuwayomiRepo(KEI_URL, "Keiyoushi")]
        self.mangas: Dict[int, SuwayomiManga] = {}
        self.calls: List[tuple] = []
        self.unreachable = False
        self.searchable: Dict[tuple, str] = {}         # (source id, url) -> title: found by a search on the source

    def add(self, manga_id: int, source: SuwayomiSource, url: str, title: str) -> SuwayomiManga:
        self.mangas[manga_id] = SuwayomiManga(id=manga_id, title=title, url=url, source_id=source.id, in_library=True)
        return self.mangas[manga_id]

    def _call(self, *what) -> None:
        self.calls.append(what)
        if self.unreachable:
            raise Unreachable("Suwayomi could not be reached at http://suwayomi.example:4567 (ConnectionError)")

    def sources(self):
        self._call("sources")
        return list(self.sources_list)

    def installed_extensions(self):
        self._call("installed_extensions")
        return list(self.extensions_list)

    def extension_repos(self):
        self._call("extension_repos")
        return list(self.repos)

    def mangas_by_id(self, ids: Sequence[int], *, pause: float = 0.0):
        self._call("mangas_by_id", tuple(ids))
        return {i: self.mangas[i] for i in ids if i in self.mangas}

    def manga_by_url(self, source_id: str, url: str, title: str = ""):
        """The carry-back's lookup: a manga Suwayomi knows by (source, url); else one its source's search finds (it
        gets a new id then, as a search does in Suwayomi) when :attr:`searchable` has it."""
        self._call("manga_by_url", source_id, url)
        hit = next((m for m in self.mangas.values() if m.source_id == source_id and m.url == url), None)
        if hit is None and (source_id, url) in self.searchable:
            hit = self.add(max(self.mangas, default=100) + 1, next(s for s in self.sources_list if s.id == source_id),
                           url, self.searchable[(source_id, url)])
        return hit

    def close(self) -> None:
        pass


def set_suwayomi_choice(db, series_id: int, manga_id: Optional[int], source_id: str, title: str,
                        how: str = "mangadex-id", **extra) -> None:
    data = {"source_id": source_id, "manga_id": manga_id, "manga_title": title, "how": how, **extra}
    db.set_meta(f"{META_SUWAYOMI_SERIES}{series_id}", json.dumps({k: v for k, v in data.items() if v is not None}))


def raw_choice(db, series_id: int, prefix: str = META_EXTENSIONS_SERIES):
    text = db.get_meta(f"{prefix}{series_id}")
    return None if text is None else json.loads(text)


__all__ = ["FakeSuwayomiCarry", "LOST", "LOST_ID", "MANGADEX", "SECOND_URL", "WEEB", "raw_choice", "set_suwayomi_choice"]

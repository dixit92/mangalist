"""MangaList's chapter client over the extension host's ``/compat`` API (:mod:`mangalist.services.extensions.compat`):
the calls it makes, its answers read into the SAME dataclasses as the Suwayomi client's (exact chapter numbers, display
names, no chapters), and errors mapped to the Suwayomi client's types. A fake session stands in for the host (no JVM, no
network); the last tests talk to the in-process fake host over real HTTP."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any, Dict, List

import pytest

from mangalist.downloads import chapter_backend
from mangalist.downloads.contracts import (
    CHAPTER_BACKEND_EXTENSIONS, ChapterClient, MangaChapters, QueuedChapter, SuwayomiChapter, SuwayomiConnection,
    SuwayomiManga, SuwayomiSource,
)
from mangalist.services.extensions import compat
from mangalist.services.extensions.compat import (
    ExtensionsChapterClient, HostAnswer, HostRefused, HostUnreachable, chapter_number, normalize_host_url,
)
from mangalist.services.suwayomi import GraphQLError, SuwayomiClient, SuwayomiError, UnexpectedResponse, Unreachable
from mangalist.services.suwayomi.client import chapter_of as suwayomi_chapter_of

from .fake_host import MANGADEX_EN, MANGADEX_PKG, FakeChapter, FakeHost, FakeManga

BASE = "http://127.0.0.1:4570"


class Resp:
    def __init__(self, status: int, body: Any = None, *, raw: bytes = b""):
        self.status_code = status
        self.content = raw if raw else json.dumps(body).encode("utf-8")


class FakeSession:
    """Answers by (method, path under /compat); records every request."""

    def __init__(self, routes: Dict[tuple, Any]):
        self.routes = routes
        self.requests: List[Dict[str, Any]] = []
        self.closed = False

    def request(self, method, url, data=None, headers=None, timeout=None):
        assert url.startswith(BASE + "/compat/")
        path = url[len(BASE) + len("/compat"):]
        self.requests.append({"method": method, "path": path, "body": json.loads(data) if data else None,
                              "headers": headers, "timeout": timeout})
        answer = self.routes.get((method, path))
        if isinstance(answer, BaseException):
            raise answer
        if answer is None:
            return Resp(404, {"error": f"no such endpoint: {method} {path}", "type": "HttpError"})
        return answer if isinstance(answer, Resp) else Resp(200, answer)

    def close(self):
        self.closed = True


CHAPTERS = {"manga": {"id": 7, "title": "Example Manga", "url": "/manga/abc", "sourceId": MANGADEX_EN,
                      "inLibrary": False},
            "sourceName": "MangaDex (EN)",
            "chapters": [
                {"id": 11, "mangaId": 7, "name": "Vol.1 Ch.1 - Start", "chapterNumber": 1.0, "scanlator": "Alpha Scans",
                 "url": "/chapter/1", "realUrl": "https://mangadex.org/chapter/1", "uploadDate": "1517511029000",
                 "downloaded": False, "sourceOrder": 1},
                {"id": 12, "mangaId": 7, "name": "Ch.10.5", "chapterNumber": 10.5, "scanlator": "  ",
                 "url": "/chapter/2", "realUrl": "", "uploadDate": "0", "downloaded": True, "sourceOrder": 2},
                {"id": 13, "mangaId": 7, "name": "Ch.1.1", "chapterNumber": 1.100000023841858, "scanlator": None,
                 "url": "/chapter/3", "realUrl": "", "uploadDate": "0", "downloaded": False, "sourceOrder": 3},
                {"id": 14, "mangaId": 7, "name": "Oneshot", "chapterNumber": -1.0, "scanlator": None,
                 "url": "/chapter/4", "realUrl": "", "uploadDate": "0", "downloaded": False, "sourceOrder": 4}]}
QUEUE = {"state": "STARTED", "queue": [{"chapterId": 11, "state": "DOWNLOADING", "progress": 0.5, "tries": 0},
                                       {"chapterId": 13, "state": "ERROR", "progress": 0, "tries": 3}]}
ROUTES = {
    ("GET", "/about"): {"name": "MangaList extension host", "version": "0.2.0"},
    ("GET", "/settings"): {"version": "0.2.0", "downloadAsCbz": True, "downloadsPath": "/config/extension-host/downloads",
                           "flareSolverr": True, "flareSolverrUrl": "http://192.168.2.50:8191"},
    ("GET", "/sources"): [{"id": MANGADEX_EN, "name": "MangaDex", "displayName": "MangaDex (EN)", "lang": "en",
                           "extension": MANGADEX_PKG},
                          {"id": "0", "name": "Local source", "displayName": "Local source", "lang": "localsourcelang",
                           "extension": ""}],
    ("POST", "/search"): {"mangas": [{"id": 7, "title": "Example Manga", "url": "/manga/abc", "sourceId": MANGADEX_EN,
                                      "inLibrary": False}], "hasNextPage": False},
    ("POST", "/manga/7/chapters"): CHAPTERS,
    ("POST", "/manga/7/library"): {"id": 7, "inLibrary": True},
    ("POST", "/downloads/enqueue"): QUEUE,
    ("POST", "/downloads/dequeue"): {"state": "STOPPED", "queue": []},
    ("GET", "/downloads"): QUEUE,
    ("POST", "/chapters"): [CHAPTERS["chapters"][1]],
    ("POST", "/chapters/delete-downloaded"): {"ids": [12]},
}


@pytest.fixture
def session() -> FakeSession:
    return FakeSession(dict(ROUTES))


def make(session) -> ExtensionsChapterClient:
    return ExtensionsChapterClient(SuwayomiConnection(base_url=BASE + "/", backend=CHAPTER_BACKEND_EXTENSIONS),
                                   session=session)


def test_implements_the_contract_and_the_settings(session):
    c: ChapterClient = make(session)
    assert c.version() == "0.2.0"
    assert make(session).server_settings() == {
        "version": "0.2.0", "download_as_cbz": True, "downloads_path": "/config/extension-host/downloads",
        "auth_mode": "NONE", "flaresolverr": True, "flaresolverr_url": "http://192.168.2.50:8191"}
    req = session.requests[0]
    assert req["headers"]["User-Agent"].startswith("MangaList/") and req["body"] is None


def test_the_one_factory_builds_the_chosen_programs_client(session):
    built = chapter_backend.client_from_connection(
        SuwayomiConnection(base_url=BASE, backend=CHAPTER_BACKEND_EXTENSIONS), session=session)
    assert isinstance(built, ExtensionsChapterClient) and built.base_url == BASE
    other = chapter_backend.client_from_connection(SuwayomiConnection(base_url="http://192.0.2.10:4567"),
                                                   session=object())
    assert isinstance(other, SuwayomiClient)
    assert chapter_backend.name_of(SuwayomiConnection(base_url=BASE, backend="extensions")) == "the extension host"


@pytest.mark.parametrize("given, expected", [
    ("127.0.0.1:4570", "http://127.0.0.1:4570"), ("http://host:4570/compat/", "http://host:4570"),
    ("", "http://127.0.0.1:4570"), ("https://box/exthost/", "https://box/exthost")])
def test_normalize_host_url(given, expected):
    assert normalize_host_url(given) == expected


@pytest.mark.parametrize("bad", ["ftp://host", "http://user:pw@host", "http://host/?a=1"])
def test_normalize_host_url_refuses(bad):
    with pytest.raises(ValueError):
        normalize_host_url(bad)


def test_sources_leave_out_a_local_source_and_keep_the_display_name(session):
    (md,) = make(session).sources()
    assert md == SuwayomiSource(id=MANGADEX_EN, name="MangaDex", display_name="MangaDex (EN)", lang="en",
                                extension=MANGADEX_PKG)
    assert md.is_mangadex


def test_search_sends_the_source_query_and_first_page(session):
    found = make(session).search(MANGADEX_EN, "id:abc")
    assert found == [SuwayomiManga(id=7, title="Example Manga", url="/manga/abc", source_id=MANGADEX_EN)]
    req = session.requests[-1]
    assert req["body"] == {"sourceId": MANGADEX_EN, "query": "id:abc", "page": 1}
    assert req["timeout"] == compat.FETCH_TIMEOUT


def test_chapters_are_the_same_dataclasses_with_exact_numbers(session):
    got = make(session).chapters(7)
    assert isinstance(got, MangaChapters) and got.source_name == "MangaDex (EN)"
    assert got.manga == SuwayomiManga(id=7, title="Example Manga", url="/manga/abc", source_id=MANGADEX_EN)
    assert [c.number for c in got.chapters] == ["1", "10.5", "1.1", "-1"]
    first = got.chapters[0]
    assert first == SuwayomiChapter(id=11, manga_id=7, name="Vol.1 Ch.1 - Start", number="1", scanlator="Alpha Scans",
                                    url="/chapter/1", real_url="https://mangadex.org/chapter/1",
                                    upload_date="1517511029000", downloaded=False, source_order=1)
    assert got.chapters[1].scanlator is None and got.chapters[1].downloaded         # blank group: none
    assert session.requests[-1]["path"] == "/manga/7/chapters" and session.requests[-1]["body"] == {}


def test_a_chapter_reads_exactly_as_the_suwayomi_client_reads_it():
    # the same node, as Suwayomi's GraphQL and as the host's /compat give it
    node = {"id": 3, "mangaId": 1, "name": "Vol.2 Ch.17.5 - Extra", "chapterNumber": Decimal("17.5"),
            "scanlator": "Alpha Scans", "url": "/c/3", "realUrl": "https://x/c/3", "uploadDate": "1517511232000",
            "sourceOrder": 3}
    assert compat.chapter_of({**node, "downloaded": True}) == suwayomi_chapter_of({**node, "isDownloaded": True})


@pytest.mark.parametrize("value, expected", [
    (Decimal("1.100000023841858"), "1.1"), (Decimal("187.5"), "187.5"), (Decimal("2.0"), "2"), (-1, "-1"),
    (Decimal("10.333333015441895"), "10.333333"), (Decimal("0.1234567890123"), "0.1234567890123"), (None, "-1")])
def test_chapter_number(value, expected):
    assert chapter_number(value) == expected


def test_no_chapters_is_an_empty_list_not_an_error(session):
    session.routes[("POST", "/manga/7/chapters")] = Resp(502, {"error": "No chapters found", "type": "Exception"})
    got = make(session).chapters(7)
    assert got.chapters == () and got.manga.id == 7
    session.routes[("POST", "/manga/7/chapters")] = {"manga": CHAPTERS["manga"], "sourceName": "MangaDex (EN)",
                                                     "chapters": []}
    assert make(session).chapters(7).chapters == ()


def test_the_queue_library_and_delete(session):
    client = make(session)
    client.add_to_library(7)
    assert session.requests[-1]["path"] == "/manga/7/library"
    queued = client.enqueue([11, 13])
    assert queued == [QueuedChapter(11, "DOWNLOADING", 0.5, 0), QueuedChapter(13, "ERROR", 0.0, 3)]
    assert session.requests[-1]["body"] == {"ids": [11, 13]}
    assert client.queue() == queued and client.dequeue([13]) == []
    assert client.enqueue([]) == [] and client.dequeue([]) == [] and client.chapters_by_id([]) == []
    (ch,) = client.chapters_by_id([12])
    assert ch.id == 12 and ch.downloaded and ch.number == "10.5"
    client.delete_downloaded([12])
    assert session.requests[-1] == {**session.requests[-1], "path": "/chapters/delete-downloaded", "body": {"ids": [12]}}
    n = len(session.requests)
    client.delete_downloaded([])
    assert len(session.requests) == n
    client.close()
    assert session.closed


def test_errors_are_the_suwayomi_clients_types(session):
    session.routes[("GET", "/about")] = ConnectionError("refused")
    with pytest.raises(Unreachable) as err:
        make(session).version()
    assert isinstance(err.value, HostUnreachable) and "extension host could not be reached" in str(err.value)
    session.routes[("POST", "/manga/9/chapters")] = Resp(502, {"error": "HTTP error 503 (Cloudflare)", "type": "IOException"})
    with pytest.raises(GraphQLError) as refused:
        make(session).chapters(9)
    assert isinstance(refused.value, HostRefused) and refused.value.status == 502
    assert str(refused.value) == "Extension host: HTTP error 503 (Cloudflare)" and "Suwayomi" not in str(refused.value)
    with pytest.raises(HostRefused) as missing:          # an unknown id: 404 (Suwayomi: a GraphQL error)
        make(session).add_to_library(99)
    assert missing.value.status == 404
    session.routes[("POST", "/search")] = Resp(400, {"error": "sourceId is required", "type": "BadRequest"})
    with pytest.raises(UnexpectedResponse) as bad:
        make(session).search("", "x")
    assert isinstance(bad.value, HostAnswer) and bad.value.status == 400 and "sourceId is required" in str(bad.value)
    session.routes[("GET", "/downloads")] = Resp(200, raw=b"<html>not the host</html>")
    with pytest.raises(SuwayomiError, match="not JSON"):
        make(session).queue()


# --- against the in-process fake host, over real HTTP -----------------------------------------------------------

@pytest.fixture
def fake_host(tmp_path):
    host = FakeHost(tmp_path / "host", auto_download=False).start()
    host.installed[MANGADEX_PKG] = "1.4.200"
    host.add_manga(MANGADEX_EN, FakeManga("/manga/00000000-0000-4000-8000-0000000c0000", "Example Manga", [
        FakeChapter("/chapter/c1", "Vol.1 Ch.1 - Example Title 1", 1.0, "Alpha Scans"),
        FakeChapter("/chapter/c2", "Vol.1 Ch.2.5 - Extra", 2.5, None)]))
    yield host
    host.stop()


def test_over_http_ids_are_stable_and_a_download_is_one_cbz(fake_host):
    client = ExtensionsChapterClient(base_url=fake_host.url)
    try:
        (manga,) = client.search(MANGADEX_EN, "id:00000000-0000-4000-8000-0000000c0000")
        assert (manga.id, manga.source_id) == (1, MANGADEX_EN)
        assert client.search(MANGADEX_EN, "Example")[0].id == 1                  # the same manga, the same id
        got = client.chapters(manga.id)
        assert [(c.id, c.number, c.scanlator) for c in got.chapters] == [(1, "1", "Alpha Scans"), (2, "2.5", None)]
        assert client.chapters(manga.id).chapters[0].id == 1
        client.enqueue([1])
        assert [q.state for q in client.queue()] == ["QUEUED"]
        fake_host.run_downloads()
        assert client.queue() == [] and client.chapters_by_id([1])[0].downloaded
        cbz = fake_host.downloads_path / "mangas" / "MangaDex (EN)" / "Example Manga" / \
            "Alpha Scans_Vol.1 Ch.1 - Example Title 1.cbz"
        assert cbz.is_file()
        client.delete_downloaded([1])
        assert not cbz.exists() and not (fake_host.downloads_path / "mangas" / "MangaDex (EN)").exists()
        assert not client.chapters_by_id([1])[0].downloaded
        with pytest.raises(HostRefused):
            client.chapters(42)
    finally:
        client.close()


def test_over_http_a_failing_chapter_ends_in_error_and_enqueue_retries_it(fake_host):
    client = ExtensionsChapterClient(base_url=fake_host.url)
    (manga,) = client.search(MANGADEX_EN, "Example")
    client.chapters(manga.id)
    fake_host.fail_urls.add("/chapter/c2")
    client.enqueue([2])
    for _ in range(3):
        fake_host.run_downloads()
    assert client.queue() == [QueuedChapter(2, "ERROR", 0.0, 3)]
    fake_host.fail_urls.clear()
    assert [q.state for q in client.enqueue([2])] == ["QUEUED"]
    fake_host.run_downloads()
    assert client.queue() == [] and client.chapters_by_id([2])[0].downloaded
    client.close()


@pytest.mark.parametrize("text, expected", [
    ("Suwayomi could not be asked about its downloads: X", "The extension host could not be asked about its downloads: X"),
    ("no longer in Suwayomi's download queue", "no longer in the extension host's download queue"),
    ("not set (Settings > Connected services > Suwayomi); nothing filed",
     "not set (Settings > Connected services > Built-in extensions); nothing filed"),
    ("filed. Suwayomi's copy kept", "filed. The extension host's copy kept"), ("", ""), (None, None)])
def test_messages_name_the_program_that_answers(text, expected):
    assert chapter_backend.wording(text, True) == expected
    assert chapter_backend.wording(text, False) == text
    assert chapter_backend.is_builtin(ExtensionsChapterClient(base_url=BASE, session=FakeSession({})))
    assert not chapter_backend.is_builtin(object())

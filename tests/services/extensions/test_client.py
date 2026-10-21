"""The extension host client (prototype): calls, answers read into the dataclasses, errors by status, the demo. A fake
session stands in for the host - no JVM, no network."""

from __future__ import annotations

import json
from typing import Any, Dict, List

import pytest

from mangalist.services.extensions import (
    ExtensionHostClient, ExtensionHostError, HostUnreachable, NotFound, Page, SourceFailed,
)
from mangalist.services.extensions.__main__ import demo, find_source

SID = "2499283573021220255"           # 64-bit: travels as a string


class Resp:
    def __init__(self, status: int, body: Any = None, *, raw: bytes = b"", headers=None):
        self.status_code = status
        self.content = raw if raw else json.dumps(body).encode("utf-8")
        self.headers = headers or {"Content-Type": "application/json"}


class FakeSession:
    """Answers by (method, path) from *routes*; records every request."""

    def __init__(self, routes: Dict[tuple, Any]):
        self.routes = routes
        self.requests: List[Dict[str, Any]] = []

    def request(self, method, url, data=None, headers=None, timeout=None):
        path = url.split("4570", 1)[1]
        self.requests.append({"method": method, "path": path, "body": json.loads(data) if data else None,
                              "headers": headers, "timeout": timeout})
        answer = self.routes.get((method, path.split("?")[0]))
        if isinstance(answer, Exception):
            raise answer
        if answer is None:
            return Resp(404, {"error": f"no such endpoint: {method} {path}", "type": "HttpError"})
        return answer if isinstance(answer, Resp) else Resp(200, answer)


SOURCES = [{"id": SID, "name": "MangaDex", "lang": "en", "baseUrl": "https://mangadex.org", "supportsLatest": True,
            "extension": "eu.kanade.tachiyomi.extension.all.mangadex"},
           {"id": "5", "name": "MangaDex", "lang": "fr", "baseUrl": "https://mangadex.org", "supportsLatest": True,
            "extension": "eu.kanade.tachiyomi.extension.all.mangadex"}]
MANGA = {"url": "/manga/abc", "title": "Example Series", "thumbnailUrl": None, "author": None, "artist": None,
         "description": None, "genre": None, "status": 1, "webUrl": "https://mangadex.org/title/abc"}
CHAPTERS = [{"url": "/chapter/2", "name": "Vol.1 Ch.2.5 - Extra", "chapterNumber": 2.5, "scanlator": "Alpha Scans",
             "dateUpload": 1792615646320, "webUrl": None},
            {"url": "/chapter/1", "name": "Ch.1", "chapterNumber": 1.0, "scanlator": None, "dateUpload": 0}]
PAGES = [{"index": 0, "url": "https://host,https://api/at-home/x,1", "imageUrl": "/data/a/1.jpg"},
         {"index": 1, "url": "https://host,https://api/at-home/x,1", "imageUrl": None}]


def client(routes) -> ExtensionHostClient:
    return ExtensionHostClient("http://127.0.0.1:4570/", session=FakeSession(routes))


def test_sources_search_chapters_pages_and_image():
    c = client({("GET", f"/sources"): SOURCES,
                ("GET", f"/sources/{SID}/search"): {"mangas": [MANGA], "hasNextPage": True},
                ("POST", f"/sources/{SID}/chapters"): CHAPTERS,
                ("POST", f"/sources/{SID}/pages"): PAGES,
                ("POST", f"/sources/{SID}/image"): Resp(200, raw=b"\xff\xd8jpeg", headers={"Content-Type": "image/jpeg"})})
    src = find_source(c.sources(), "MangaDex")
    assert src.id == SID and src.lang == "en"                                   # English preferred
    found = c.search(src.id, "Kaoru Hana / 2")
    assert found.has_next_page and found.mangas[0].title == "Example Series" and found.mangas[0].status == 1
    assert c._session.requests[-1]["path"] == f"/sources/{SID}/search?q=Kaoru%20Hana%20%2F%202&page=1"
    chapters = c.chapters(src.id, "/manga/abc", "Example Series")
    assert c._session.requests[-1]["body"] == {"url": "/manga/abc", "title": "Example Series"}
    assert [(ch.number, ch.scanlator) for ch in chapters] == [(2.5, "Alpha Scans"), (1.0, None)]
    pages = c.pages(src.id, "/chapter/2", resolve=True)
    assert c._session.requests[-1]["path"] == f"/sources/{SID}/pages?resolve=true"
    assert pages[1] == Page(1, "https://host,https://api/at-home/x,1", None)
    data, kind = c.image(src.id, pages[0])
    assert (data, kind) == (b"\xff\xd8jpeg", "image/jpeg")
    assert c._session.requests[-1]["body"] == {"index": 0, "url": PAGES[0]["url"], "imageUrl": "/data/a/1.jpg"}


def test_install_and_the_repository():
    c = client({("POST", "/extensions/install"): {"pkg": "p", "name": "Weeb Central", "version": "1.6.25",
                                                  "libVersion": "1.6", "nsfw": 0, "sources": [SOURCES[0]]},
                ("GET", "/repo"): [{"name": "MangaDex", "pkg": "p", "apkUrl": "https://x/a.apk", "jarUrl": "https://x/a.jar",
                                    "lang": "all", "versionCode": 106000, "version": "1.6.0", "libVersion": "1.6",
                                    "nsfw": True, "sources": [{"id": "1", "name": "MangaDex", "lang": "en",
                                                               "baseUrl": "https://mangadex.org"}]}]})
    ext = c.install("p")
    assert c._session.requests[-1]["body"] == {"pkg": "p", "format": "jar"}
    assert ext.name == "Weeb Central" and ext.sources[0].id == SID
    (entry,) = c.repo("https://example.org/index.json")
    assert c._session.requests[-1]["path"] == "/repo?url=https%3A%2F%2Fexample.org%2Findex.json"
    assert entry.jar_url == "https://x/a.jar" and entry.nsfw and entry.sources[0].lang == "en"


@pytest.mark.parametrize("status, cls", [(404, NotFound), (502, SourceFailed), (400, ExtensionHostError)])
def test_errors_carry_the_hosts_message(status, cls):
    c = client({("POST", f"/sources/{SID}/chapters"): Resp(status, {"error": "No factory registered",
                                                                     "type": "IllegalStateException"})})
    with pytest.raises(cls) as err:
        c.chapters(SID, "/manga/abc")
    assert str(err.value) == "No factory registered" and err.value.status == status
    assert err.value.kind == "IllegalStateException"


def test_a_host_that_is_not_running_is_unreachable():
    c = client({("GET", "/health"): ConnectionError("refused")})
    with pytest.raises(HostUnreachable, match="could not be reached at http://127.0.0.1:4570"):
        c.health()


def test_the_demo_reports_each_source_and_survives_a_failing_one():
    c = client({("GET", "/sources"): SOURCES + [{"id": "9", "name": "Comix", "lang": "en"}],
                ("GET", f"/sources/{SID}/search"): {"mangas": [MANGA], "hasNextPage": False},
                ("POST", f"/sources/{SID}/chapters"): CHAPTERS,
                ("POST", f"/sources/{SID}/pages"): PAGES,
                ("POST", f"/sources/{SID}/image"): Resp(200, raw=b"x" * 10, headers={"Content-Type": "image/png"}),
                ("GET", "/sources/9/search"): Resp(502, {"error": "No factory registered", "type": "x"})})
    lines = dict(demo(c, rows=(("MangaDex", "en", "q"), ("Comix", "en", "q"), ("Weeb Central", "en", "q"))))
    assert lines["MangaDex (en)"].startswith("'Example Series': 2 chapters, newest 'Vol.1 Ch.2.5 - Extra' (no. 2.5, "
                                             "Alpha Scans): 2 pages, page 1 image/png 10 bytes")
    assert lines["Comix (en)"] == "FAILED: No factory registered"
    assert lines["Weeb Central (en)"] == "FAILED: no installed source 'Weeb Central/en'"

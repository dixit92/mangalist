from __future__ import annotations

import json
import logging

import pytest

from mangalist.downloads.contracts import ChapterClient, SuwayomiConnection
from mangalist.services.suwayomi import (
    AuthFailed, GraphQLError, SuwayomiClient, UnexpectedResponse, Unreachable, normalize_base_url,
)
from mangalist.services.suwayomi import client as sc

from .conftest import PASSWORD, FakeSession, recorded

MANGADEX_EN = "2499283573021220255"


def make(session, **conn) -> SuwayomiClient:
    values = {"base_url": "http://suwayomi.example:4567", **conn}
    return SuwayomiClient(SuwayomiConnection(**values), session=session)


def test_implements_the_contract(session):
    c: ChapterClient = make(session)
    assert c.version() == "v2.4.2366"


def test_posts_json_to_api_graphql_with_the_user_agent(session):
    make(session).version()
    req = session.requests[0]
    assert req["url"] == "http://suwayomi.example:4567/api/graphql"
    assert req["headers"]["Content-Type"] == "application/json"
    assert req["headers"]["User-Agent"].startswith("MangaList/")
    assert req["auth"] is None and req["query"] == sc.Q_ABOUT


def test_basic_auth_travels_in_the_header_only_and_is_never_shown(session, caplog):
    caplog.set_level(logging.DEBUG)
    client = make(session, username="owner", password=PASSWORD)
    client.version()
    assert session.requests[0]["auth"] == ("owner", PASSWORD)
    assert PASSWORD not in repr(client) and PASSWORD not in caplog.text
    assert PASSWORD not in repr(SuwayomiConnection("http://x", "owner", PASSWORD))
    session.status = 401
    with pytest.raises(AuthFailed) as err:
        client.version()
    assert PASSWORD not in str(err.value)


@pytest.mark.parametrize("given, expected", [
    ("192.168.1.10:4567", "http://192.168.1.10:4567"),
    ("http://host:4567/", "http://host:4567"),
    ("https://host/suwayomi/api/graphql", "https://host/suwayomi"),
    ("http://host:4567/api", "http://host:4567"),
])
def test_normalize_base_url(given, expected):
    assert normalize_base_url(given) == expected


@pytest.mark.parametrize("bad", ["", "ftp://host", "http://user:pw@host", "http://host/?a=1"])
def test_normalize_base_url_refuses(bad):
    with pytest.raises(ValueError):
        normalize_base_url(bad)


def test_unreachable_and_http_errors(session):
    session.raise_exc = ConnectionError("refused")
    with pytest.raises(Unreachable):
        make(session).version()
    session.raise_exc = None
    session.status = 502
    with pytest.raises(UnexpectedResponse) as err:
        make(session).version()
    assert err.value.status == 502


def test_not_json_is_unexpected():
    class Html(FakeSession):
        def post(self, *a, **k):
            from .conftest import Response
            return Response(200, "<html>WebUI</html>")

    with pytest.raises(UnexpectedResponse):
        make(Html()).version()


def test_graphql_error_carries_suwayomis_message(session):
    session.answers["MangaListChaptersById"] = json.loads(recorded("error_unknown.json"))
    with pytest.raises(GraphQLError) as err:
        make(session).chapters_by_id([99999])
    assert "non null" in str(err.value)


def test_server_settings(session):
    s = make(session).server_settings()
    assert s == {"version": "v2.4.2366", "download_as_cbz": True, "downloads_path": "", "auth_mode": "NONE",
                 "flaresolverr": False, "flaresolverr_url": "http://localhost:8191"}


def test_sources_leave_out_the_local_source(session):
    sources = make(session).sources()
    assert [s.id for s in sources] == ["1411768577036936240", MANGADEX_EN]
    en = sources[1]
    assert (en.name, en.display_name, en.lang) == ("MangaDex", "MangaDex (EN)", "en")
    assert en.extension == "eu.kanade.tachiyomi.extension.all.mangadex" and en.is_mangadex


def test_search_by_mangadex_id(session):
    found = make(session).search(MANGADEX_EN, "id:00000000-0000-4000-8000-0000000c0000")
    assert len(found) == 1
    assert found[0].url == "/manga/00000000-0000-4000-8000-0000000c0000" and found[0].source_id == MANGADEX_EN
    req = session.requests[0]
    assert req["variables"] == {"source": MANGADEX_EN, "query": "id:00000000-0000-4000-8000-0000000c0000"}
    assert req["timeout"] == sc.FETCH_TIMEOUT


def test_chapters_are_exact_numbers(session):
    fetched = make(session).chapters(1)
    assert fetched.manga.title == "Example Manga" and fetched.source_name == "MangaDex (EN)"
    numbers = [c.number for c in fetched.chapters]
    assert numbers[:4] == ["1", "2", "3", "4"] and "953.5" in numbers and "953.6" in numbers
    first = fetched.chapters[0]
    assert first.name == "Vol.1 Ch.1 - Example Title 1" and first.scanlator == "Alpha Scans"
    assert first.real_url.endswith("0000000c0001") and first.manga_id == 1 and not first.downloaded


def test_enqueue_queue_downloaded_delete(session):
    c = make(session)
    queued = c.enqueue([1, 2])
    assert [(q.chapter_id, q.state) for q in queued] == [(1, "QUEUED"), (2, "QUEUED")]
    assert session.requests[-1]["variables"] == {"ids": [1, 2]}
    running = c.queue()
    assert [(q.chapter_id, q.state) for q in running] == [(1, "DOWNLOADING"), (2, "QUEUED")]
    done = c.chapters_by_id([1, 2])
    assert all(ch.downloaded for ch in done)
    c.delete_downloaded([1])
    assert session.names()[-1] == "MangaListDeleteDownloaded"
    assert c.enqueue([]) == [] and c.chapters_by_id([]) == []


def test_every_operation_is_valid_against_the_recorded_schema_names():
    # the operations name only fields Suwayomi v2.4.2366's schema has (introspected at pinning time)
    for key, query in sc.OPERATIONS.items():
        assert key in query
        assert query.split()[0] in ("query", "mutation")

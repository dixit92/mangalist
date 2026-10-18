"""MangaDex's bulk "latest uploaded chapter" request (mangalist.services.mangadex; owner-approved 2026-10-18, "Shared
Chapter Signal"): 100 ids per request, every content rating in the query, MangaList's User-Agent, polite pacing, and a
failure that leaves the ids unknown instead of raising. A fake ``get`` with answers shaped like MangaDex's documented
``GET /manga`` list (made-up ids; no network)."""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from mangalist.services.mangadex import CONTENT_RATINGS, MangaDexClient
from mangalist.services.mangadex.client import read_latest


def uid(n: int, kind: str = "a") -> str:
    return f"00000000-0000-4000-8000-{kind * 4}{n:08d}"


class Resp:
    def __init__(self, status: int = 200, body: Any = None, headers: Dict[str, str] = None):
        self.status_code, self._body, self.headers = status, body, headers or {}

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def answer_for(params, latest=lambda i: uid(int(i[-8:]), "c")) -> Resp:
    ids = [v for k, v in params if k == "ids[]"]
    return Resp(200, {"result": "ok", "response": "collection", "limit": 100, "offset": 0, "total": len(ids),
                      "data": [{"id": i, "type": "manga", "attributes": {"latestUploadedChapter": latest(i),
                                                                          "title": {"en": "Example"}}}
                               for i in ids]})


class FakeGet:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls: List[tuple] = []

    def __call__(self, url, params, timeout):
        self.calls.append((url, list(params), timeout))
        a = self.answers.pop(0) if self.answers else answer_for
        if isinstance(a, Exception):
            raise a
        return a(params) if callable(a) else a


def client(get, **kw) -> MangaDexClient:
    kw.setdefault("sleep", lambda s: None)
    return MangaDexClient(get=get, **kw)


def test_one_request_per_100_ids_with_every_content_rating():
    get = FakeGet()
    ids = [uid(n) for n in range(250)]
    got = client(get).latest_uploads(ids)
    assert got.requests == 3 and got.failed == 0 and len(got.values) == 250
    assert [len([v for k, v in params if k == "ids[]"]) for _url, params, _t in get.calls] == [100, 100, 50]
    url, params, timeout = get.calls[0]
    assert url == "https://api.mangadex.org/manga" and timeout > 0
    assert [v for k, v in params if k == "contentRating[]"] == list(CONTENT_RATINGS)
    assert set(CONTENT_RATINGS) == {"safe", "suggestive", "erotica", "pornographic"}
    assert ("limit", "100") in params
    assert got.values[uid(7)] == uid(7, "c")


def test_null_means_no_chapter_and_a_missing_id_stays_unknown():
    body = {"result": "ok", "data": [
        {"id": uid(1), "attributes": {"latestUploadedChapter": None}},
        {"id": uid(2), "attributes": {"latestUploadedChapter": "not-a-uuid"}},
        {"id": uid(9), "attributes": {"latestUploadedChapter": uid(9, "c")}},          # not asked for
        "junk",
    ]}
    got = client(FakeGet(Resp(200, body))).latest_uploads([uid(1), uid(2), uid(3)])
    assert got.values == {uid(1): None}                     # 2 malformed, 3 not answered, 9 not asked: unknown


def test_ids_that_are_not_uuids_are_never_sent_and_duplicates_once():
    get = FakeGet()
    got = client(get).latest_uploads([uid(1), uid(1).upper(), "abc", "", uid(2)])
    assert [v for k, v in get.calls[0][1] if k == "ids[]"] == [uid(1), uid(2)] and got.requests == 1
    assert client(FakeGet()).latest_uploads(["nope"]).requests == 0


def test_a_failed_request_stops_the_asking_and_leaves_its_ids_unknown():
    get = FakeGet(answer_for, ConnectionError("down"), ConnectionError("down"))
    got = client(get).latest_uploads([uid(n) for n in range(250)])
    assert got.requests == 2 and got.failed == 1 and len(got.values) == 100 and "could not be reached" in got.error
    assert len(get.calls) == 3                              # the second request and its one retry; no third request


@pytest.mark.parametrize("bad", [Resp(403, {}), Resp(200, ValueError("html")), Resp(200, {"result": "error"}),
                                 Resp(200, {"result": "ok", "data": "x"})])
def test_an_unexpected_answer_is_unknown_not_an_error(bad):
    got = client(FakeGet(bad)).latest_uploads([uid(1)])
    assert got.values == {} and got.failed == 1


def test_rate_limited_waits_once_for_mangadexs_retry_time():
    slept = []
    get = FakeGet(Resp(429, None, {"X-RateLimit-Retry-After": "1005"}), answer_for)
    got = client(get, sleep=slept.append, wall=lambda: 1000.0).latest_uploads([uid(1)])
    assert got.values and slept == [5.0]
    long_wait = FakeGet(Resp(429, None, {"Retry-After": "3600"}))
    assert client(long_wait).latest_uploads([uid(1)]).failed == 1 and len(long_wait.calls) == 1


def test_requests_are_paced_and_carry_mangalists_user_agent():
    slept, now = [], [0.0]
    c = client(FakeGet(), sleep=slept.append, clock=lambda: now[0], min_interval=1.0)
    c.latest_uploads([uid(n) for n in range(150)])
    assert slept == [1.0]
    real = MangaDexClient()
    try:
        assert real._session.headers["User-Agent"].startswith("MangaList/") and real._session.trust_env is False
    finally:
        real.close()


def test_read_latest_refuses_a_page_that_is_not_a_list():
    from mangalist.services.mangadex import UnexpectedResponse

    with pytest.raises(UnexpectedResponse):
        read_latest({"data": None}, [uid(1)])

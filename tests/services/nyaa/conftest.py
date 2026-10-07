"""Hand-written nyaa RSS, a fake ``get`` and a fake client. No network, no sleeping."""

from __future__ import annotations

import hashlib
from html import escape
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import pytest

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "nyaa"


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def info_hash(seed: str) -> str:
    return hashlib.sha1(seed.encode()).hexdigest()


def item(title: str, seeders: int = 5, *, hash: Optional[str] = None, trusted: bool = False, remake: bool = False,
         size: str = "120.5 MiB", date: str = "Mon, 03 Aug 2026 19:48:36 -0000", category: str = "3_1",
         leechers: int = 1, downloads: int = 10) -> str:
    h = hash or info_hash(title)
    return f"""<item>
<title>{escape(title)}</title>
<link>https://nyaa.si/download/{h[:6]}.torrent</link>
<guid isPermaLink="true">https://nyaa.si/view/{h[:6]}</guid>
<pubDate>{date}</pubDate>
<nyaa:seeders>{seeders}</nyaa:seeders>
<nyaa:leechers>{leechers}</nyaa:leechers>
<nyaa:downloads>{downloads}</nyaa:downloads>
<nyaa:infoHash>{h.upper()}</nyaa:infoHash>
<nyaa:categoryId>{category}</nyaa:categoryId>
<nyaa:category>Literature - English-translated</nyaa:category>
<nyaa:size>{size}</nyaa:size>
<nyaa:trusted>{'Yes' if trusted else 'No'}</nyaa:trusted>
<nyaa:remake>{'Yes' if remake else 'No'}</nyaa:remake>
</item>"""


def feed(*items: str) -> bytes:
    return ("""<rss xmlns:atom="http://www.w3.org/2005/Atom" xmlns:nyaa="https://nyaa.si/xmlns/nyaa" version="2.0">
<channel><title>Nyaa - test</title>
""" + "\n".join(items) + "\n</channel></rss>").encode("utf-8")


class FakeResponse:
    def __init__(self, status: int = 200, content: bytes = b"", headers: Optional[Dict[str, str]] = None):
        self.status_code = status
        self.content = content
        self.headers = headers or {}


class FakeGet:
    """``get(url, params, timeout)`` that answers from a queue (the last answer repeats) and records calls."""

    def __init__(self, *answers: Any):
        self.answers: List[Any] = list(answers)
        self.calls: List[Dict[str, Any]] = []

    def __call__(self, url: str, params: Dict[str, Any], timeout: float) -> FakeResponse:
        self.calls.append({"url": url, "params": dict(params), "timeout": timeout})
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


class Clock:
    """A clock that only moves when the code sleeps."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: List[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


class FakeClient:
    """Stands in for NyaaClient in search tests: ``pages`` maps a query (lower case) to feed bytes."""

    def __init__(self, pages: Dict[str, bytes]):
        self.pages = {k.lower(): v for k, v in pages.items()}
        self.calls: List[tuple] = []

    def rss(self, query: str, category: str = "3_1", filter_: int = 0):
        from mangalist.services.nyaa import parse_feed

        self.calls.append((query, category))
        return parse_feed(self.pages.get(query.lower(), feed()))


@pytest.fixture
def clock() -> Clock:
    return Clock()

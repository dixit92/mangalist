"""Suwayomi tests: a fake HTTP session that answers with Suwayomi-Server v2.4.2366's RECORDED GraphQL answers
(``tests/fixtures/suwayomi``: recorded 2026-10-10 from a throwaway container with the MangaDex extension, one public series
and two one-page chapters; titles, groups and ids replaced by neutral ones). No network."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "suwayomi"
PASSWORD = "Suwa-Secret-Pa55-DoNotLog"


def recorded(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


#: operation name (as MangaList's client names it) -> the recorded answer
ANSWERS = {"MangaListAbout": "about.json", "MangaListSettings": "settings.json", "MangaListSources": "sources_all.json",
           "MangaListSearch": "search_id.json", "MangaListChapters": "fetch_manga_and_chapters.json",
           "MangaListAddToLibrary": None, "MangaListEnqueue": "enqueue.json", "MangaListQueue": "status_running.json",
           "MangaListChaptersById": "downloaded.json", "MangaListDeleteDownloaded": "delete.json"}


class Response:
    def __init__(self, status: int, text: str):
        self.status_code = status
        self.text = text


class FakeSession:
    """Answers each POST by the operation's name; records every request."""

    def __init__(self) -> None:
        self.requests: List[Dict[str, Any]] = []
        self.answers: Dict[str, Any] = dict(ANSWERS)
        self.status: Optional[int] = None
        self.raise_exc: Optional[BaseException] = None
        self.closed = False

    def post(self, url, data=None, headers=None, auth=None, timeout=None):
        body = json.loads(data.decode("utf-8"))
        name = body["query"].split("(")[0].split("{")[0].split()[-1]
        self.requests.append({"url": url, "name": name, "query": body["query"], "variables": body["variables"],
                              "headers": dict(headers or {}), "auth": auth, "timeout": timeout})
        if self.raise_exc is not None:
            raise self.raise_exc
        if self.status is not None:
            return Response(self.status, "Unauthorized" if self.status == 401 else "boom")
        answer = self.answers.get(name)
        if answer is None:
            return Response(200, json.dumps({"data": {"updateManga": {"manga": {"id": 1, "inLibrary": True}}}}))
        if isinstance(answer, str):
            return Response(200, recorded(answer))
        return Response(200, json.dumps(answer))

    def names(self) -> List[str]:
        return [r["name"] for r in self.requests]

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def session() -> FakeSession:
    return FakeSession()

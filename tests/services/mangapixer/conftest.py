"""A local fake MangaPixer (no live instance, no internet) and a fresh library database per test."""

from __future__ import annotations

import copy
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlsplit

import pytest

from mangalist import store
from mangalist.store.mangapixer import MangaPixerCache

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "mangapixer" / "metadata-export-v1.json"
TOKEN = "mpx_TestTokenDoNotLog_0123456789abcdef"


def load_fixture() -> Dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def folder(node_id: str, trail: List[str], state: str = "Confirmed", updated: str = "2026-10-01T00:00:00.000Z",
           carried_from: Optional[str] = None, kind: str = "folder", provider: str = "mangaupdates") -> Dict[str, Any]:
    """A made-up export item in the documented shape."""
    has_record = state in ("Confirmed", "Auto")
    return {
        "nodeId": node_id, "nodeKind": kind, "carriedFrom": carried_from, "trail": list(trail), "updatedAt": updated,
        "link": {"state": state, "method": "search" if has_record else None, "score": 0.9 if has_record else None,
                 "updatedAt": updated},
        "record": ({"provider": provider, "externalId": "1" + node_id[-4:].rjust(10, "0"), "title": trail[-1],
                    "latestChapter": "12.5"} if has_record else None),
        "companions": {"mangadex": None, "anilist": None}, "officialLinks": [],
        "volumes": {"source": "mangadex", "fetchedAt": updated, "items": []} if has_record else None,
        "completion": None, "refresh": None,
    }


class FakeMangaPixer:
    """Answers /api/v1/export/{ping,libraries,metadata} like MangaPixer 1.33.0 (from in-memory data)."""

    def __init__(self):
        self.token = TOKEN
        self.server_time = "2026-10-04T12:00:00.000Z"
        self.libraries: List[Dict[str, Any]] = [
            {"id": "lib0manga", "displayName": "Manga", "kind": "manga", "folderCount": 10, "itemCount": 4,
             "lastScanAt": "2026-10-04T03:00:00.000Z"}]
        self.items: Dict[str, List[Dict[str, Any]]] = {"lib0manga": []}
        self.removed: Dict[str, List[Dict[str, Any]]] = {"lib0manga": []}
        self.window_start: Optional[str] = None      # updatedSince before this -> 409
        self.schema_version = 1
        self.extra_fields = False
        self.queue: List[Tuple[str, int, Any, Dict[str, str]]] = []   # (endpoint, status, body, headers)
        self.requests: List[Dict[str, Any]] = []
        self._lock = threading.Lock()

    # --- request handling ----------------------------------------------------------------------------

    def handle(self, path: str, query: Dict[str, str], headers: Dict[str, str]) -> Tuple[int, Any, Dict[str, str]]:
        endpoint = path.rsplit("/", 1)[-1] if path.startswith("/api/v1/export/") else path
        with self._lock:
            self.requests.append({"endpoint": endpoint, "path": path, "query": dict(query), "headers": dict(headers)})
            for n, (ep, status, body, hdrs) in enumerate(self.queue):
                if ep in (endpoint, "*"):
                    del self.queue[n]
                    return status, body, hdrs
        if not path.startswith("/api/v1/export/"):
            return 404, {"error": "notFound"}, {}
        auth = headers.get("authorization", "")
        if auth != f"Bearer {self.token}":
            return 401, None, {"WWW-Authenticate": 'Bearer error="invalid_token"'}
        if endpoint == "ping":
            return 200, {"ok": True, "serverTime": self.server_time, "auth": "token"}, {}
        if endpoint == "libraries":
            return 200, {"schemaVersion": self.schema_version, "serverTime": self.server_time,
                         "libraries": copy.deepcopy(self.libraries)}, {}
        if endpoint == "metadata":
            return self._metadata(query)
        return 404, {"error": "notFound"}, {}

    def _metadata(self, q: Dict[str, str]):
        lib_id = q.get("library")
        if not lib_id:
            return 400, {"error": "libraryRequired"}, {}
        lib = next((lib for lib in self.libraries if lib["id"] == lib_id), None)
        if lib is None:
            return 404, {"error": "libraryNotFound"}, {}
        since = q.get("updatedSince")
        if since and self.window_start and since < self.window_start:
            return 409, {"error": "fullSyncRequired"}, {}
        include = None
        if "include" in q:
            include = {b for b in q["include"].split(",") if b}
            if include - {"volumes", "completion", "refresh"}:
                return 400, {"error": "invalidInclude"}, {}
        items = [i for i in self.items.get(lib_id, []) if not since or i["updatedAt"] >= since]
        removed = [r for r in self.removed.get(lib_id, []) if since and r["at"] >= since]
        limit = int(q.get("limit") or 200)
        start = int(q.get("cursor") or 0)
        page = copy.deepcopy(items[start:start + limit])
        if include is not None:
            for item in page:
                for block in ("volumes", "completion", "refresh"):
                    if block not in include:
                        item[block] = None
        if self.extra_fields:
            for item in page:
                item["somethingNew"] = {"x": 1}
        nxt = str(start + limit) if start + limit < len(items) else None
        body = {"schemaVersion": self.schema_version, "serverTime": self.server_time,
                "library": {"id": lib_id, "displayName": lib["displayName"], "kind": lib["kind"]},
                "items": page, "removed": removed if start == 0 else [], "nextCursor": nxt}
        if self.extra_fields:
            body["futureTopLevel"] = True
        return 200, body, {}

    def count(self, endpoint: str) -> int:
        return sum(1 for r in self.requests if r["endpoint"] == endpoint)


def _handler(fake: FakeMangaPixer):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            parts = urlsplit(self.path)
            query = {k: v[-1] for k, v in parse_qs(parts.query, keep_blank_values=True).items()}
            headers = {k.lower(): v for k, v in self.headers.items()}
            status, body, hdrs = fake.handle(parts.path, query, headers)
            data = b"" if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            for k, v in hdrs.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):  # keep test output quiet
            pass

    return Handler


@pytest.fixture
def fake():
    mp = FakeMangaPixer()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(mp))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    mp.url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield mp
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def db() -> store.Store:
    store.reset_stores()
    return store.get_store()


@pytest.fixture
def cache(db) -> MangaPixerCache:
    return MangaPixerCache(db)


@pytest.fixture
def connected(cache, fake) -> MangaPixerCache:
    cache.set_connection(base_url=fake.url, token=TOKEN)
    return cache


@pytest.fixture
def sleeps():
    return []


@pytest.fixture
def client_factory(sleeps):
    from mangalist.services.mangapixer.client import MangaPixerClient

    def make(base_url, token=TOKEN, **kw):
        kw.setdefault("sleep", sleeps.append)
        kw.setdefault("timeout", 5)
        return MangaPixerClient(base_url, token, **kw)

    return make


def add_root_with_series(db, tmp_path, name: str, rels: List[str]):
    """A root in the database with series rows (no folders on disk needed for mapping)."""
    root = db.add_root(str(tmp_path / "lib" / name), name)
    now = store.utcnow()
    with db.connect() as con:
        for rel in rels:
            con.execute("INSERT INTO series (root_id, rel_path, status, first_seen_at, last_seen_at)"
                        " VALUES (?,?, 'present', ?, ?)", (root.id, rel, now, now))
    return root

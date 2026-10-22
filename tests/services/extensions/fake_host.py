"""A FAKE extension host: an in-process HTTP server (127.0.0.1, a free port) that answers the host's ``/compat`` API
(``extension-host/COMPAT.md``) and its own extension-management API the way the contract says - integer manga / chapter
ids given on first sight, a download queue that writes ONE CBZ per chapter in Suwayomi's folder layout with a
``ComicInfo.xml`` (written to a temporary name, then renamed), ``ERROR`` after 3 failed tries, delete-downloaded. No JVM,
no network beyond the loopback, made-up names.

Lets the whole chapter flow (MangaList's real client, backend, send path and arrivals pass) run end to end in CI until
lane H's real host can be used (``tests/e2e/test_extensions_e2e.py``)."""

from __future__ import annotations

import json
import os
import threading
import time
import zipfile
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlsplit
from xml.sax.saxutils import escape

from mangalist.downloads.chapter_arrivals import suwayomi_name

MANGADEX_PKG = "eu.kanade.tachiyomi.extension.all.mangadex"
MANGADEX_EN = "2499283573021220255"
WEEB_PKG = "eu.kanade.tachiyomi.extension.en.weebexample"
WEEB_ID = "1000000000000000001"
PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
       b"\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x35\x81\x84\x00\x00\x00\x00IEND\xaeB`\x82")


@dataclass
class FakeChapter:
    url: str
    name: str
    number: float                      # as the source gives it (-1: none)
    scanlator: Optional[str] = None
    upload: int = 1517511029000
    pages: int = 3


@dataclass
class FakeManga:
    url: str
    title: str
    chapters: List[FakeChapter] = field(default_factory=list)


@dataclass
class RepoEntry:
    pkg: str
    name: str
    version: str
    lang: str
    sources: List[Tuple[str, str, str]]     # (id, name, lang)
    nsfw: bool = False


def display_name(name: str, lang: str) -> str:
    """Suwayomi's display name of a source: ``MangaDex (EN)``."""
    return f"{name} ({lang.upper()})"


def comicinfo(chapter: FakeChapter, title: str, number: str) -> str:
    return ("<?xml version='1.0' encoding='UTF-8' ?>\n<ComicInfo>\n"
            f"  <Title>{escape(chapter.name)}</Title>\n  <Series>{escape(title)}</Series>\n"
            f"  <Number>{number}</Number>\n  <Translator>{escape(chapter.scanlator or '')}</Translator>\n"
            f"  <Web>{escape('https://example.invalid' + chapter.url)}</Web>\n</ComicInfo>")


def number_text(value: float) -> str:
    return str(int(value)) if value == int(value) else repr(value)


class FakeHost:
    """The fake host's state and its server. ``auto_download``: queued chapters are downloaded by a worker thread
    (else by :meth:`run_downloads`); ``fail_urls``: chapter URLs whose download fails (each try counts)."""

    def __init__(self, data_dir: Path, *, auto_download: bool = True, version: str = "0.2.0-fake"):
        self.data_dir = Path(data_dir)
        self.downloads_path = self.data_dir / "downloads"
        self.version = version
        self.auto_download = auto_download
        self.lock = threading.RLock()
        self.repo: List[RepoEntry] = [
            RepoEntry(MANGADEX_PKG, "MangaDex", "1.4.200", "all", [(MANGADEX_EN, "MangaDex", "en"), ("5", "MangaDex", "fr")]),
            RepoEntry(WEEB_PKG, "Weeb Example", "1.4.3", "en", [(WEEB_ID, "Weeb Example", "en")]),
        ]
        self.installed: Dict[str, str] = {}                 # pkg -> version
        self.catalog: Dict[str, List[FakeManga]] = {}       # source id -> its mangas
        self.manga_ids: Dict[Tuple[str, str], int] = {}     # (source id, url) -> id
        self.mangas: Dict[int, Dict[str, Any]] = {}         # id -> {sourceId, url, title, inLibrary}
        self.chapter_ids: Dict[Tuple[int, str], int] = {}   # (manga id, url) -> id
        self.chapters: Dict[int, Dict[str, Any]] = {}       # id -> {mangaId, chapter: FakeChapter, order}
        self.downloaded: Dict[int, Path] = {}
        self.queue: List[Dict[str, Any]] = []               # {chapterId, state, progress, tries}
        self.fail_urls: set = set()
        self.preferences: Dict[str, List[Dict[str, Any]]] = {MANGADEX_EN: [
            {"position": 0, "key": "coverQuality", "type": "ListPreference", "title": "Cover quality",
             "summary": "%s", "entries": ["Original", "Medium", "Low"], "entryValues": ["", ".512.jpg", ".256.jpg"],
             "defaultValue": "", "currentValue": None},
            {"position": 1, "key": "dataSaver", "type": "SwitchPreferenceCompat", "title": "Data saver",
             "defaultValue": False, "currentValue": None},
            {"position": 2, "key": "contentRating", "type": "MultiSelectListPreference", "title": "Content rating",
             "entries": ["Safe", "Suggestive", "Erotica"], "entryValues": ["safe", "suggestive", "erotica"],
             "defaultValue": ["safe", "suggestive"], "currentValue": None},
            {"position": 3, "key": "blockedGroups", "type": "EditTextPreference", "title": "Blocked groups",
             "defaultValue": "", "currentValue": None},
            {"position": 4, "key": "altTitles", "type": "CheckBoxPreference", "title": "Alternative titles",
             "defaultValue": True, "currentValue": None},
        ]}
        self.requests: List[Tuple[str, str]] = []
        self._server: Optional[ThreadingHTTPServer] = None
        self._worker: Optional[threading.Thread] = None
        self._stop = threading.Event()

    # --- set-up helpers --------------------------------------------------------------------------------------------

    def add_manga(self, source_id: str, manga: FakeManga) -> FakeManga:
        self.catalog.setdefault(source_id, []).append(manga)
        return manga

    @property
    def url(self) -> str:
        assert self._server is not None
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def start(self) -> "FakeHost":
        host = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):          # quiet
                pass

            def _answer(self, status: int, body: Any) -> None:
                data = json.dumps(body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _handle(self, method: str) -> None:
                parts = urlsplit(self.path)
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
                host.requests.append((method, parts.path))
                try:
                    status, answer = host.route(method, parts.path.rstrip("/"), parse_qs(parts.query), body)
                except HttpError as exc:
                    status, answer = exc.status, {"error": exc.message, "type": exc.kind}
                except Exception as exc:  # noqa: BLE001
                    status, answer = 500, {"error": str(exc), "type": type(exc).__name__}
                self._answer(status, answer)

            def do_GET(self):
                self._handle("GET")

            def do_POST(self):
                self._handle("POST")

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        if self.auto_download:
            self._worker = threading.Thread(target=self._work, daemon=True)
            self._worker.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()

    # --- the queue ------------------------------------------------------------------------------------------------

    def _work(self) -> None:
        while not self._stop.is_set():
            self.run_downloads()
            self._stop.wait(0.05)

    def run_downloads(self) -> None:
        """Download every QUEUED chapter now (one CBZ each); a failing one is retried up to 3 tries, then ERROR."""
        with self.lock:
            todo = [q for q in self.queue if q["state"] in ("QUEUED", "DOWNLOADING")]
        for item in todo:
            cid = item["chapterId"]
            with self.lock:
                item["state"] = "DOWNLOADING"
                info = self.chapters[cid]
                manga = self.mangas[info["mangaId"]]
                ch: FakeChapter = info["chapter"]
            if ch.url in self.fail_urls:
                with self.lock:
                    item["tries"] += 1
                    item["state"] = "ERROR" if item["tries"] >= 3 else "QUEUED"
                continue
            path = self.cbz_path(manga, ch)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            with zipfile.ZipFile(tmp, "w") as zf:
                for page in range(1, ch.pages + 1):
                    zf.writestr(f"{page:03d}.png", PNG)
                zf.writestr("ComicInfo.xml", comicinfo(ch, manga["title"], number_text(ch.number)))
            os.replace(tmp, path)
            with self.lock:
                self.downloaded[cid] = path
                self.queue = [q for q in self.queue if q["chapterId"] != cid]

    def cbz_path(self, manga: Dict[str, Any], ch: FakeChapter) -> Path:
        source = self._source(manga["sourceId"])
        name = f"{ch.scanlator}_{ch.name}" if ch.scanlator else ch.name
        return (self.downloads_path / "mangas" / suwayomi_name(source["displayName"]) / suwayomi_name(manga["title"])
                / (suwayomi_name(name) + ".cbz"))

    # --- the API ----------------------------------------------------------------------------------------------------

    def _sources(self) -> List[Dict[str, Any]]:
        out = []
        for entry in self.repo:
            if entry.pkg in self.installed:
                for sid, name, lang in entry.sources:
                    out.append({"id": sid, "name": name, "lang": lang, "displayName": display_name(name, lang),
                                "extension": entry.pkg, "baseUrl": "https://example.invalid", "supportsLatest": True})
        return out

    def _source(self, source_id: str) -> Dict[str, Any]:
        found = next((s for s in self._sources() if s["id"] == str(source_id)), None)
        if found is None:
            raise HttpError(404, f"no source {source_id}")
        return found

    def _manga_json(self, mid: int) -> Dict[str, Any]:
        m = self.mangas[mid]
        return {"id": mid, "title": m["title"], "url": m["url"], "sourceId": m["sourceId"], "inLibrary": m["inLibrary"]}

    def _chapter_json(self, cid: int) -> Dict[str, Any]:
        info = self.chapters[cid]
        ch: FakeChapter = info["chapter"]
        return {"id": cid, "mangaId": info["mangaId"], "name": ch.name, "chapterNumber": ch.number,
                "scanlator": ch.scanlator, "url": ch.url, "realUrl": "https://example.invalid" + ch.url,
                "uploadDate": str(ch.upload), "downloaded": cid in self.downloaded, "sourceOrder": info["order"]}

    def _queue_json(self) -> Dict[str, Any]:
        return {"state": "STARTED" if self.queue else "STOPPED", "queue": [dict(q) for q in self.queue]}

    def _extension_json(self, pkg: str) -> Dict[str, Any]:
        entry = next(e for e in self.repo if e.pkg == pkg)
        return {"pkg": pkg, "name": entry.name, "version": self.installed[pkg], "libVersion": "1.4", "nsfw": 0,
                "sources": [s for s in self._sources() if s["extension"] == pkg]}

    def route(self, method: str, path: str, query: Dict[str, List[str]], body: Dict[str, Any]) -> Tuple[int, Any]:
        with self.lock:
            return self._route(method, path, query, body)

    def _route(self, method, path, query, body):  # noqa: C901 - one table, as the host's ApiServer
        parts = [p for p in path.split("/") if p]
        if method == "GET" and path == "/health":
            return 200, {"ok": True, "version": self.version, "sources": len(self._sources()), "flareSolverr": "off"}
        if method == "GET" and path == "/repo":
            return 200, [{"name": e.name, "pkg": e.pkg, "version": e.version, "libVersion": "1.4", "lang": e.lang,
                          "nsfw": e.nsfw, "apkUrl": f"https://example.invalid/{e.pkg}.apk", "jarUrl": None,
                          "sources": [{"id": i, "name": n, "lang": lang, "baseUrl": ""} for i, n, lang in e.sources]}
                         for e in self.repo]
        if method == "GET" and path == "/extensions":
            return 200, [self._extension_json(p) for p in self.installed]
        if method == "POST" and path in ("/extensions/install", "/extensions/update"):
            entry = next((e for e in self.repo if e.pkg == body.get("pkg")), None)
            if entry is None:
                raise HttpError(404, f"no extension {body.get('pkg')} in the repository")
            if path.endswith("update") and entry.pkg not in self.installed:
                raise HttpError(400, f"{entry.pkg} is not installed")
            self.installed[entry.pkg] = entry.version
            return 200, self._extension_json(entry.pkg)
        if method == "POST" and path == "/extensions/uninstall":
            if self.installed.pop(str(body.get("pkg")), None) is None:
                raise HttpError(404, f"{body.get('pkg')} is not installed")
            return 200, {"pkg": body.get("pkg")}
        if method == "GET" and path == "/sources":
            return 200, self._sources()
        if len(parts) == 3 and parts[0] == "sources" and parts[2] == "preferences":
            self._source(parts[1])
            prefs = self.preferences.setdefault(parts[1], [])
            if method == "POST":
                pref = next((p for p in prefs if p["key"] == body.get("key")), None)
                if pref is None:
                    raise HttpError(400, f"no preference {body.get('key')}")
                pref["currentValue"] = body.get("value")
            return 200, prefs
        if parts[:1] != ["compat"]:
            raise HttpError(404, f"no such endpoint: {method} {path}")
        return self._compat(method, "/" + "/".join(parts[1:]), parts[1:], body)

    def _compat(self, method, path, parts, body):
        if method == "GET" and path == "/about":
            return 200, {"name": "MangaList extension host", "version": self.version}
        if method == "GET" and path == "/settings":
            return 200, {"version": self.version, "downloadAsCbz": True, "downloadsPath": str(self.downloads_path),
                         "flareSolverr": False, "flareSolverrUrl": ""}
        if method == "GET" and path == "/sources":
            return 200, [{k: s[k] for k in ("id", "name", "displayName", "lang", "extension")} for s in self._sources()]
        if method == "POST" and path == "/search":
            source = self._source(body.get("sourceId"))
            query = str(body.get("query") or "")
            found = []
            for m in self.catalog.get(source["id"], []):
                if (query.startswith("id:") and m.url.endswith(query[3:])) or (
                        not query.startswith("id:") and query.casefold() in m.title.casefold()):
                    key = (source["id"], m.url)
                    if key not in self.manga_ids:
                        self.manga_ids[key] = len(self.manga_ids) + 1
                        self.mangas[self.manga_ids[key]] = {"sourceId": source["id"], "url": m.url,
                                                            "title": m.title, "inLibrary": False, "fake": m}
                    found.append(self._manga_json(self.manga_ids[key]))
            return 200, {"mangas": found, "hasNextPage": False}
        if len(parts) == 3 and parts[0] == "manga" and parts[2] in ("chapters", "library"):
            mid = int(parts[1])
            if mid not in self.mangas:
                raise HttpError(404, f"no manga {mid}")
            if parts[2] == "library":
                self.mangas[mid]["inLibrary"] = True
                return 200, {"id": mid, "inLibrary": True}
            fake: FakeManga = self.mangas[mid]["fake"]
            ids = []
            for order, ch in enumerate(fake.chapters, start=1):
                key = (mid, ch.url)
                if key not in self.chapter_ids:
                    self.chapter_ids[key] = len(self.chapter_ids) + 1
                cid = self.chapter_ids[key]
                self.chapters[cid] = {"mangaId": mid, "chapter": ch, "order": order}
                ids.append(cid)
            source = self._source(self.mangas[mid]["sourceId"])
            return 200, {"manga": self._manga_json(mid), "sourceName": source["displayName"],
                         "chapters": [self._chapter_json(c) for c in ids]}
        if method == "POST" and path in ("/downloads/enqueue", "/downloads/dequeue"):
            ids = [int(i) for i in body.get("ids") or ()]
            for cid in ids:
                if cid not in self.chapters:
                    raise HttpError(404, f"no chapter {cid}")
            if path.endswith("enqueue"):
                for cid in ids:
                    item = next((q for q in self.queue if q["chapterId"] == cid), None)
                    if item is None and cid not in self.downloaded:
                        self.queue.append({"chapterId": cid, "state": "QUEUED", "progress": 0.0, "tries": 0})
                    elif item is not None and item["state"] == "ERROR":
                        item["state"] = "QUEUED"
            else:
                self.queue = [q for q in self.queue if q["chapterId"] not in ids]
            return 200, self._queue_json()
        if method == "GET" and path == "/downloads":
            return 200, self._queue_json()
        if method == "POST" and path == "/chapters":
            return 200, [self._chapter_json(int(i)) for i in body.get("ids") or () if int(i) in self.chapters]
        if method == "POST" and path == "/chapters/delete-downloaded":
            ids = [int(i) for i in body.get("ids") or ()]
            for cid in ids:
                file = self.downloaded.pop(cid, None)
                if file is not None and file.exists():
                    file.unlink()
                    folder = file.parent
                    while folder != self.downloads_path and folder.is_dir() and not any(folder.iterdir()):
                        folder.rmdir()
                        folder = folder.parent
            return 200, {"ids": ids}
        raise HttpError(404, f"no such endpoint: {method} /compat{path}")


class HttpError(Exception):
    def __init__(self, status: int, message: str, kind: str = "HttpError"):
        super().__init__(message)
        self.status, self.message, self.kind = status, message, kind


def wait_for(predicate, timeout: float = 10.0, step: float = 0.05) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(step)
    return bool(predicate())

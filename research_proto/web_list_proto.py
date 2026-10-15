"""THROWAWAY research prototype (lane F native, never merged): a read-only List page served by FastAPI from the
Qt-free core (mangalist.wanted.library_states) over a COPY of the library database. Measures start-up, memory and
payload size; it is not a design. Run: python web_list_proto.py   (MANGALIST_DATA_DIR = folder holding the DB copy)."""
from __future__ import annotations

import json
import re
import sys
import time

T0 = time.perf_counter()


def rss_mb() -> float:
    with open("/proc/self/status") as f:
        return int(re.search(r"VmRSS:\s+(\d+)", f.read()).group(1)) / 1024


from fastapi import FastAPI                                 # noqa: E402
from fastapi.responses import HTMLResponse, JSONResponse    # noqa: E402

from mangalist.store import get_store                       # noqa: E402
from mangalist.wanted import library_states                 # noqa: E402

T_IMPORT = time.perf_counter() - T0
app = FastAPI()
DB = get_store()

PAGE = """<!doctype html><meta charset=utf-8><title>List</title><table id=t border=1></table>
<script>fetch('/api/list').then(r=>r.json()).then(rows=>{const t=document.getElementById('t');
for(const r of rows){const tr=t.insertRow();for(const k of ['title','kind','state','gaps','files'])tr.insertCell().textContent=r[k]}})</script>"""


@app.get("/", response_class=HTMLResponse)
def index():
    return PAGE


@app.get("/api/list")
def api_list():
    t = time.perf_counter()
    views = library_states(DB)
    rows = []
    for v in views:
        e = v.entry
        rows.append({"id": v.row.id, "title": e.title, "english": e.english_title, "kind": str(getattr(e, "kind_hint", "") or ""),
                     "state": str(getattr(v.state, "verdict", "") or getattr(v.state, "kind", "") or v.state.__class__.__name__),
                     "gaps": ",".join(v.held[:3]), "files": len(e.files)})
    app.state.last = (len(rows), time.perf_counter() - t)
    return JSONResponse(rows)


if __name__ == "__main__":
    import threading
    import urllib.request

    import uvicorn

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8765, log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    while not server.started:
        time.sleep(0.01)
    t_listen = time.perf_counter() - T0
    rss_listen = rss_mb()
    r = []
    for i in range(3):
        t = time.perf_counter()
        body = urllib.request.urlopen("http://127.0.0.1:8765/api/list").read()
        r.append(time.perf_counter() - t)
    t = time.perf_counter()
    page = urllib.request.urlopen("http://127.0.0.1:8765/").read()
    t_page = time.perf_counter() - t
    print(json.dumps({"import_s": round(T_IMPORT, 2), "listening_s": round(t_listen, 2), "rss_at_listen_mb": round(rss_listen),
                      "api_list_s": [round(x, 2) for x in r], "rows": app.state.last[0], "core_compute_s": round(app.state.last[1], 2),
                      "payload_kb": round(len(body) / 1024), "page_bytes": len(page), "rss_after_mb": round(rss_mb())}))
    server.should_exit = True

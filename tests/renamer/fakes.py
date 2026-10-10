"""A FAKE namer standing in for the naming lane's ``mangalist.naming`` (made-up names only; no real titles or paths).

It writes the settled format closely enough for the renamer's tests - ``Ch. 0102.00 Vol. 012 (<title>) [<group>].cbz`` and
``<Series title> - Vol. 001 [<group>].cbz`` - and leaves a name it already wrote as it is (the real module round-trips
through the parser's scheme layer; today's parser does not read the title back, so the fake recognises its own names)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Dict, List, Optional, Tuple

_OWN = re.compile(r"^(Ch\. \d{4}\.\d{2}( Vol\. \d{3})?( \(.*\))?( \[.*\])?|.+ - Vol\. \d{3}( \[.*\])?)\.[A-Za-z0-9]+$")


@dataclass(frozen=True)
class FakeLimits:
    max_name_bytes: int = 255
    windows_server: Optional[str] = None


def _num(d: Decimal) -> str:
    whole = int(d)
    frac = (d - whole).quantize(Decimal("0.01"))
    return f"{whole:04d}.{str(frac)[2:4]}"


class FakeNamer:
    def __init__(self, volumes: Optional[Dict[Decimal, Decimal]] = None, overrides: Optional[Dict[str, Optional[str]]] = None,
                 fail_on: Tuple[str, ...] = ()):
        self.volumes = dict(volumes or {})
        self.overrides = dict(overrides or {})      # current name -> target (None: leave alone)
        self.fail_on = fail_on
        self.calls: List[Tuple[str, Optional[str], Optional[str]]] = []      # (name, series title, folder)
        self.limit_servers: List[Optional[str]] = []

    def limits(self, windows_server):
        self.limit_servers.append(windows_server)
        return FakeLimits(windows_server=windows_server)

    def volume_lookup(self, db, series_id) -> Callable[[Decimal], Optional[Decimal]]:
        return lambda ch: self.volumes.get(Decimal(ch))

    def target_name(self, parsed, *, ext, series_title, volume_of=None, folder=None, limits=None):
        name = parsed.name
        self.calls.append((name, series_title, folder))
        if name in self.fail_on:
            raise RuntimeError("naming failed on purpose")
        if name in self.overrides:
            return self.overrides[name]
        if _OWN.match(name):
            return name
        limits = limits or FakeLimits()
        kind = getattr(parsed.kind, "value", parsed.kind)
        group = f" [{parsed.group.replace('[', '(').replace(']', ')')}]" if parsed.group else ""
        if kind == "chapter" and parsed.chapter is not None:
            vol = parsed.volume.start if parsed.volume is not None else (volume_of(parsed.chapter.start) if volume_of
                                                                         else None)
            head = f"Ch. {_num(parsed.chapter.start)}" + (f" Vol. {int(vol):03d}" if vol is not None else "")
            title = parsed.title.replace("(", "[").replace(")", "]") if parsed.title else ""
            out = head + (f" ({title})" if title else "") + group + ext
            while len(out.encode("utf-8")) > limits.max_name_bytes and title:
                title = title[:-2].rstrip() + "…" if len(title) > 2 else ""
                out = head + (f" ({title})" if title else "") + group + ext
            return out
        if kind == "volume" and parsed.volume is not None and series_title:
            return f"{series_title} - Vol. {int(parsed.volume.start):03d}{group}{ext}"
        return None


class Recorder:
    """The renamer's after-batch hooks, recorded (no scan, no MangaPixer)."""

    def __init__(self):
        self.rescans: List[List[int]] = []
        self.scan_requests: List[List[int]] = []

    def rescan(self, db, root_ids):
        self.rescans.append(list(root_ids))
        from mangalist.scanner import record_library_scan, scan_library

        record_library_scan(db, scan_library([r for r in db.list_roots() if r.id in set(root_ids)], db=db))
        return f"rescan of {len(root_ids)} root(s) recorded"

    def request_scans(self, db, series_ids):
        self.scan_requests.append(list(series_ids))
        return "MangaPixer scans: 1 started"

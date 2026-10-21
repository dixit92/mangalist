"""Try the extension host from the command line (prototype; no Qt).

    python -m mangalist.services.extensions [--url http://127.0.0.1:4570] sources
    python -m mangalist.services.extensions install eu.kanade.tachiyomi.extension.en.weebcentral
    python -m mangalist.services.extensions search "<source name>[/<lang>]" "<query>"
    python -m mangalist.services.extensions chapters "<source>" "<manga url>"
    python -m mangalist.services.extensions demo          # search -> chapters -> pages -> one image, per source
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import List, Optional, Sequence, Tuple

from .client import DEFAULT_URL, ExtensionHostClient, ExtensionHostError, Source

#: (source name, language, a series of the owner's library on that source) - the sources MangaList's series use most
#: (2026-10-21: MangaDex 184 series, MangaFire 13, Weeb Central 13, Comix 3, ManhwaRead 1).
DEMO = (
    ("MangaDex", "en", "Kaoru Hana wa Rin to Saku"),
    ("Weeb Central", "en", "Absolute Regression"),
    ("MangaFire", "en", "Dungeon Gakuen"),
    ("Comix", "en", "Shuden Sugitara Nani Shite Asobu"),
    ("ManhwaRead", "en", "Moby Dick"),          # behind Cloudflare: needs the host's --flaresolverr
)


def find_source(sources: Sequence[Source], spec: str) -> Source:
    """A source by id, or by "name" / "name/lang" (English preferred)."""
    for s in sources:
        if s.id == spec:
            return s
    name, _, lang = spec.partition("/")
    matches = [s for s in sources if s.name.lower() == name.lower() and (not lang or s.lang == lang)]
    if not matches:
        raise SystemExit(f"no installed source {spec!r}")
    return next((s for s in matches if s.lang == "en"), matches[0])


def demo(client: ExtensionHostClient, rows=DEMO) -> List[Tuple[str, str]]:
    """Search -> chapters -> pages -> the first page's image, per source. Returns (source, result) lines."""
    sources = client.sources()
    out = []
    for name, lang, query in rows:
        label = f"{name} ({lang})"
        try:
            src = find_source(sources, f"{name}/{lang}")
            t0 = time.monotonic()
            found = client.search(src.id, query)
            if not found.mangas:
                out.append((label, f"search {query!r}: nothing found"))
                continue
            manga = found.mangas[0]
            chapters = client.chapters(src.id, manga.url, manga.title)
            if not chapters:
                out.append((label, f"{manga.title!r}: no chapters"))
                continue
            newest = chapters[0]
            pages = client.pages(src.id, newest.url)
            data, kind = client.image(src.id, pages[0]) if pages else (b"", "")
            out.append((label, f"{manga.title!r}: {len(chapters)} chapters, newest {newest.name!r} (no. {newest.number:g}"
                               f", {newest.scanlator or 'no group'}): {len(pages)} pages, page 1 {kind or '?'} "
                               f"{len(data):,} bytes ({time.monotonic() - t0:.1f} s)"))
        except (ExtensionHostError, SystemExit) as exc:
            out.append((label, f"FAILED: {exc}"))
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m mangalist.services.extensions")
    ap.add_argument("--url", default=DEFAULT_URL, help=f"the extension host (default {DEFAULT_URL})")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sources")
    p = sub.add_parser("install")
    p.add_argument("pkg")
    p = sub.add_parser("search")
    p.add_argument("source")
    p.add_argument("query")
    p = sub.add_parser("chapters")
    p.add_argument("source")
    p.add_argument("manga_url")
    sub.add_parser("demo")
    args = ap.parse_args(argv)
    client = ExtensionHostClient(args.url)
    try:
        if args.cmd == "sources":
            for s in client.sources():
                print(f"{s.id:>20}  {s.name} ({s.lang})  {s.base_url or ''}")
        elif args.cmd == "install":
            ext = client.install(args.pkg)
            print(f"installed {ext.name} {ext.version}: {len(ext.sources)} source(s)")
        elif args.cmd == "search":
            src = find_source(client.sources(), args.source)
            for m in client.search(src.id, args.query).mangas:
                print(f"{m.title}  {m.url}")
        elif args.cmd == "chapters":
            src = find_source(client.sources(), args.source)
            for c in client.chapters(src.id, args.manga_url):
                print(f"{c.number:>8g}  {c.name}  [{c.scanlator or '-'}]  {c.url}")
        elif args.cmd == "demo":
            for label, line in demo(client):
                print(f"{label:<20} {line}")
    except ExtensionHostError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

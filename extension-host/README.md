# MangaList extension host (prototype)

Runs Mihon / Tachiyomi extensions without Suwayomi, for MangaList. Branch `prototype/mihon-extensions` only; `main`
keeps the Suwayomi integration (owner, 2026-10-21: "develop a mangalist version that can work without Suwayomi and can
use and get the Mihon extensions ... Make this a prototype").

A small JVM program: it reads an extension repository (keiyoushi's `index.json` by default - the one MangaList's
Suwayomi uses), installs extensions (keiyoushi's ready JVM jar, or an APK converted with dex2jar), loads their sources
and answers a local JSON API. Underneath it uses Suwayomi's Android compatibility layer and Tachiyomi source API,
vendored under `vendor/suwayomi` (MPL-2.0; provenance and changes in `vendor/suwayomi/README.md`) - but no Suwayomi
server, database, GraphQL or web UI. MangaList's client is `mangalist/services/extensions`.

## Build and run

```sh
./gradlew :host:installDist          # JDK 21; output: host/build/install/mangalist-extension-host (~128 MB)
host/build/install/mangalist-extension-host/bin/mangalist-extension-host --data ./.data --port 4570 \
    --flaresolverr http://192.168.2.50:8191   # optional: the owner's FlareSolverr container (MANGALIST_FLARESOLVERR_URL)
./gradlew :host:test                 # the repository reader (both index formats)
```

On the Unraid box (no JDK there) the build and the host ran in `eclipse-temurin:21-jdk` containers.

From MangaList's side (its Python environment):

```sh
python -m mangalist.services.extensions --url http://127.0.0.1:4570 install eu.kanade.tachiyomi.extension.en.weebcentral
python -m mangalist.services.extensions --url http://127.0.0.1:4570 demo
```

## Cloudflare (FlareSolverr)

The host does not solve Cloudflare challenges itself: it uses the owner's FlareSolverr container (owner, 2026-10-21: "we
already have a Flaresolverr docker, so it doesn't need to be a part of MangaList"). With `--flaresolverr URL` (or
`MANGALIST_FLARESOLVERR_URL`; options `--flaresolverr-timeout 60`, `--flaresolverr-session mangalist`,
`--flaresolverr-session-ttl 15`) Suwayomi's own `CloudflareInterceptor` (vendored unchanged) sees a challenge (403 / 503
from a Cloudflare server), asks FlareSolverr to open the page, keeps the clearance cookies it returns and retries with
FlareSolverr's User-Agent. The sources are then made again so their headers carry that User-Agent (the cookie is bound
to it), so later requests pass without FlareSolverr until the cookie expires. The host uses its own FlareSolverr session
(`mangalist`), not Suwayomi's (`suwayomi`). Without `--flaresolverr`, a challenged request fails with "Cloudflare bypass
currently disabled". `GET /health` says which (`flareSolverr`: the URL or `off`).

## API

Binds `127.0.0.1` by default and has **no login** - keep it on localhost (in MangaList's container). Source ids are
64-bit and travel as strings.

| Call | Answer |
| --- | --- |
| `GET /health` | `{ok, version, sources, flareSolverr}` |
| `GET /repo?url=<index url>` | the repository's extensions (current `index.json` and legacy `index.min.json` formats) |
| `GET /extensions`, `POST /extensions/install {pkg, repo?, format: jar\|apk}` | installed extensions and their sources |
| `GET /sources` | every loaded source |
| `GET /sources/{id}/search?q=&page=`, `/popular?page=`, `/latest?page=` | `{mangas: [{url, title, thumbnailUrl, ...}], hasNextPage}` |
| `POST /sources/{id}/manga {url, title}` | details |
| `POST /sources/{id}/chapters {url, title}` | `[{url, name, chapterNumber, scanlator, dateUpload, webUrl}]` (an unknown number is read from the name, as Suwayomi does) |
| `POST /sources/{id}/pages {url}` (`?resolve=true`) | `[{index, url, imageUrl}]` |
| `POST /sources/{id}/image {index, url, imageUrl}` | the image's bytes, fetched with the source's headers |

Errors: `{error, type}` with 400 (a bad call), 404 (unknown source, package or endpoint), 502 (the extension or its site failed).

## What the prototype showed (2026-10-21, keiyoushi's repository, the owner's own series)

| Source | Install | Search -> chapters -> pages -> image |
| --- | --- | --- |
| MangaDex 1.6.0 (61 sources) | jar, < 1 s | Fragrant Flower: 8 English chapters, newest no. 187.5, 6 pages, JPEG 287,684 bytes |
| Weeb Central 1.6.25 | jar | Absolute Regression: 124 chapters, 22 pages, PNG 277,786 bytes |
| MangaFire 1.6.34 (7 sources) | jar | Dungeon Gakuen: 50 chapters (newest 39.1), 14 pages, JPEG 207,681 bytes |
| Comix 1.6.54 | jar | **fails**: it opens a WebView ("No factory registered") - needs a browser engine |
| ManhwaRead 1.6.0 (behind Cloudflare) | jar | Moby Dick: without FlareSolverr "Cloudflare bypass currently disabled"; with it, the challenge solved in 13 s, then 102 chapters, 45 pages, WebP 576,360 bytes with no further FlareSolverr call |

Found on the way:
- keiyoushi's `index.min.json` now only tells old apps to update; the real index is `index.json`, with ready JVM jars.
- Converting keiyoushi's minified APKs with dex2jar fails JVM verification ("Bad type on operand stack" in MangaDex 1.6.0);
  their jars load as they are. Suwayomi also installs the jar when there is one.
- keiyoushi's shared code requires an interceptor named `CloudflareInterceptor` in the default client.
- Extensions use `Dispatchers.Main`: the host runs an Android main looper, as Suwayomi does.

## Not in the prototype

- WebView (Comix and other JavaScript-heavy sites; Suwayomi uses KCEF, a downloaded Chromium).
- Extension preferences (MangaDex's language / quality settings stay at their defaults), updates, uninstall, signature checks.
- Anything in MangaList itself: no downloads, no settings, the find pass and the checks still use Suwayomi.
- Packaging into MangaList's image (a JRE plus ~128 MB).

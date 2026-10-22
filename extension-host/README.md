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
./gradlew :host:installDist          # JDK 21; output: host/build/install/mangalist-extension-host (~152 MB)
host/build/install/mangalist-extension-host/bin/mangalist-extension-host --data ./.data --port 4570 \
    --flaresolverr http://192.168.2.50:8191   # optional: the owner's FlareSolverr container (MANGALIST_FLARESOLVERR_URL)
./gradlew :host:test                 # unit, HTTP and process tests (below)
```

Options: `--data DIR` (`MANGALIST_EXTENSIONS_DATA`), `--bind 127.0.0.1`, `--port 4570`, `--downloads DIR` (default
`<data>/downloads`), `--repo URL` (default keiyoushi's `index.json`), `--webview on|off` (default on),
`--download-parallel 2` (1-4), `--flaresolverr URL` and its `--flaresolverr-timeout 60`, `--flaresolverr-session
mangalist`, `--flaresolverr-session-ttl 15`.

The data folder: `host.db` (SQLite: manga / chapter ids and the download queue), `extensions/` (the jars and
`installed.json`), `android/` (AndroidCompat's files, among them the sources' preferences in `settings/`),
`cache/downloads/<chapter id>/` (pages of a download in progress), and with the WebView `bin/kcef/` (Chromium, ~530 MB,
downloaded the first time an extension opens a WebView) and `cache/kcef/` (its profile).

On the Unraid box (no JDK there) the build and the host ran in `eclipse-temurin:21-jdk` containers; with the WebView, in
an image from `eclipse-temurin:21-jdk` plus Xvfb and Chromium's libraries (`xvfb libnss3 libatk-bridge2.0-0t64
libcups2t64 libxkbcommon0 libxcomposite1 libxdamage1 libxrandr2 libgbm1 libpango-1.0-0 libasound2t64 libgtk-3-0t64
...`), started as `Xvfb :99 & DISPLAY=:99 mangalist-extension-host ...`.

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
| `GET /health` | `{ok, version, sources, flareSolverr, webView}` |
| `GET /repo?url=<index url>` | the repository's extensions (current `index.json` and legacy `index.min.json` formats) |
| `GET /extensions` | installed extensions: `{pkg, name, version, versionCode, libVersion, nsfw, signatureHash, repoVersion, repoVersionCode, updateAvailable, sources}` |
| `POST /extensions/install {pkg, repo?, format: jar\|apk}` | install (signature checked first, below) |
| `POST /extensions/update {pkg}` | install the repository's newer version (no newer one: answers the installed one) |
| `POST /extensions/uninstall {pkg}` | remove it (its sources' preferences stay) |
| `GET /sources` | every loaded source |
| `GET /sources/{id}/preferences` | a configurable source's preferences: `[{key, type: list\|multiselect\|edittext\|switch\|checkbox, title, summary, entries, entryValues, dialogTitle, dialogMessage, currentValue, defaultValue, enabled, visible}]` |
| `POST /sources/{id}/preferences {key, value}` | set one (a string; `true`/`false`; a list of strings for multiselect); the extension's sources are made again |
| `GET /sources/{id}/search?q=&page=`, `/popular?page=`, `/latest?page=` | `{mangas: [{url, title, thumbnailUrl, ...}], hasNextPage}` |
| `POST /sources/{id}/manga {url, title}` | details |
| `POST /sources/{id}/chapters {url, title}` | `[{url, name, chapterNumber, scanlator, dateUpload, webUrl}]` |
| `POST /sources/{id}/pages {url}` (`?resolve=true`) | `[{index, url, imageUrl}]` |
| `POST /sources/{id}/image {index, url, imageUrl}` | the image's bytes, fetched with the source's headers |
| `GET /webview`, `POST /webview/start` | the browser engine's state (`off`, `not started`, `starting`, `ready`, `failed: ...`); start it now |
| `POST /webview/debug-eval {url, script, waitMs}` | diagnostics: open a page, run a script, answer what it left in `window.__mlDebug` |
| `/compat/...` | what MangaList asked Suwayomi, with Suwayomi's semantics: the contract is [`COMPAT.md`](COMPAT.md) |

Errors: `{error, type}` with 400 (a bad call, a refused signature), 404 (unknown source, manga, package or endpoint), 502
(the extension or its site failed).

### `/compat` as built

`about`, `settings` (`{version, downloadAsCbz: true, downloadsPath, flareSolverr, flareSolverrUrl, webView}`),
`sources` (`{id, name, displayName, lang, extension, supportsLatest, isConfigurable, baseUrl}`; `displayName` is
Suwayomi's `HttpSource.toString()`, `MangaDex (EN)`), `search`, `manga/{id}/chapters`, `manga/{id}/library`,
`downloads` (+ `enqueue` / `dequeue`), `chapters`, `chapters/delete-downloaded`, as `COMPAT.md` says. As Suwayomi
v2.4.2366 does it (its code was read for each):

- **Ids**: a manga id is made on first sight of (source, url) in a search, a chapter id on first sight of (manga, url)
  in a chapter list; SQLite `AUTOINCREMENT`, so an id is never reused after a delete.
- **Search** refreshes a known manga's title and details, except for a manga in the library (`MangaList.insertOrUpdate`).
- **Chapters** (`Chapter.updateChapterListDatabase`): the list is fetched from the source now; duplicates by URL are
  dropped; numbers are read by `ChapterRecognition` (the source's own when it gives one); names are sanitized (the
  manga's title cut off the front); a blank scanlator is none; `sourceOrder` is 1 for the oldest; a chapter with no
  date gets now (or the newest date seen); chapters the source no longer lists are deleted and dequeued; a downloaded
  chapter whose name or group changed has its CBZ renamed (a dropped downloaded chapter passes its CBZ to a new one with
  the same number and group). Answered newest first. A source that lists no chapters leaves the stored list alone and
  answers none (Suwayomi answers the error "No chapters found"; MangaList reads both as none).
- **Details** are fetched with the chapter list (one `getMangaUpdate` call) when never fetched or `fetchDetails: true`;
  a new title renames the manga's download folder (kept when the rename fails).
- **Downloads**: one at a time per source, up to `--download-parallel` sources at once; pages fetched with the source's
  client and headers (`HttpSource.getImage`) into `cache/downloads/<chapter>/` (kept between tries), named `001.<ext>`
  (the extension from the image's bytes, as Suwayomi's `ImageResponse`); a `ComicInfo.xml` (Suwayomi's
  `getComicInfo`: Title, Series, Number, Web = the chapter's URL, Summary, Writer, Penciller, Translator, Genre,
  PublishingStatus, Day / Month / Year); zipped in name order to `<cbz>.tmp`, renamed to
  `<downloads>/mangas/<displayName>/<title>/<scanlator>_<name>.cbz` (each part `SafePath.buildValidFilename`, the code
  MangaList's `suwayomi_name` mirrors). A finished chapter leaves the queue and reads `downloaded`; a failure is tried
  again at once, up to 3 tries, then `ERROR` (a chapter with no pages: `ERROR` at once); enqueueing an `ERROR` chapter
  gives it one more try; an already downloaded chapter (or a whole CBZ already there) finishes without fetching.
- **The queue** is in `host.db`: after a restart a chapter that was downloading is queued again, and an `ERROR` one
  stays `ERROR` with its tries (Suwayomi re-adds its saved queue fresh, so its `ERROR` chapters get 3 new tries after a
  restart; MangaList's automatic retry covers them either way).
- **Delete downloaded**: the CBZ (or a folder of images) and the folders left empty under `mangas/`; `downloaded` false;
  the same answer when the file is already gone.

## Extensions: signature check, updates, preferences

An extension is installed only when it is signed with its repository's key: the SHA-256 of its signing certificate must
equal `index.json`'s `signingKey` (keiyoushi's `9add655a...`; legacy repositories: `repo.json`'s
`signingKeyFingerprint`). keiyoushi's jars are signed as jars (checked with `java.util.jar`, every entry); its APKs carry
APK signature scheme v2 only (checked with Android's apksig). The fingerprint is kept in `installed.json`
(`signatureHash`), with the repository (`repo`) an update comes from. A refused package is never loaded (HTTP 400).

Preferences are Suwayomi's `getSourcePreferences` / `setSourcePreference`: the source builds its screen on its
SharedPreferences (`source_<id>`), a change is saved, the source's change listener is told, and the extension's sources
are made again. MangaDex (EN) shows 12 (thumbnail quality, data saver, content ratings, blocked groups, ...).

## WebView

Suwayomi's KCEF provider is back (`vendor/suwayomi/AndroidCompat/.../webkit/Kcef*`, `CefHelper`, Suwayomi's
`CEFManager`): JetBrains' JCEF jar plus the Chromium libraries of a JetBrains Runtime release (`jbr-release-25.0.4b508.27`,
243.6 MB download, ~530 MB unpacked), downloaded into `<data>/bin/kcef` **the first time an extension opens a WebView**
(or on `POST /webview/start`), never in the repository or an image. Chromium needs an X display (`DISPLAY`). The
host removes Chromium's profile lock left by an earlier container (a recreated container has a new host name, and
Chromium then refuses the profile). `--webview off`: CEF is never started; an extension that opens a WebView fails with
"CEF is disabled".

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

## Parity with Suwayomi (lane H, 2026-10-22): downloads through `/compat`

Every extension installed from keiyoushi's repository with the signature check (all `9add655a...`), one chapter each
downloaded through `/compat` (search -> chapters -> enqueue -> CBZ), in a 2 GB / 4 CPU container:

| Source | Search | Chapters | Download | CBZ |
| --- | --- | --- | --- | --- |
| MangaDex (EN) | 0.6 s | 1.2 s, 1 chapter (no. 127.5) | 6.7 s | 10 pages, 18.2 MB, ComicInfo Number 127.5, Translator, Web |
| Weeb Central (EN) | 0.5 s | 4.0 s, 124 chapters | 20.8 s | Episode 1: 231 pages, 22.9 MB (Episode 3: 143 pages in 14.3 s) |
| MangaFire (EN) | 0.5 s | 0.9 s, 50 chapters | 18.7 s | 29 pages, 16.8 MB; the title's trailing `.` cut from its folder name |
| ManhwaRead (EN), Cloudflare | 13.5 s (FlareSolverr) | 0.3 s, 102 chapters | 5.6 s | 41 WebP pages, 9.5 MB, no group: `Chapter 01.cbz` |
| Comix (EN), WebView | 19 s the first time (Chromium downloaded 6.5 s, unpacked 10.4 s, started 0.8 s), then 1-3 s | **fails**: "Could not find the site's chapters API" | - | - |

Comix: the WebView works (Comix's search runs in it: Chromium navigation, request interception, the JavaScript bridge),
but its chapter list and pages fail - **the same error in Suwayomi v2.4.2366** (checked in a throwaway Suwayomi
container). The cause is the site, not the browser: comix.to's current build (`35595e3de3c99889c1aa70`) wraps its API
client in a `Proxy` that hides its keys and binds its functions, so the extension's script cannot find the method it
looks for (read with `/webview/debug-eval`). Comix 1.6.54 needs an update from keiyoushi.

Memory (`docker stats`, the same container): idle with no WebView 117 MiB (174 MiB after a download), downloading
187 MiB at most; with the WebView started (Chromium's processes) 353-413 MiB, idle 409 MiB, downloading 414 MiB.
`--webview off` and a WebView never opened cost nothing.

## Tests

`./gradlew :host:test`: 42 tests - 33 unit (CBZ layout and names against MangaList's `suwayomi_name`, ComicInfo, ids
across a restart, the chapter merge, the queue: success, 3 failures -> ERROR, one more try, restart, dequeue, two
sources at once; signatures of jars and v2 APKs; preferences; the repository index), 8 HTTP (`/compat` and extension
management against an in-process server with a fake source and a local image / repository server), 1 process (the real
host program started, stopped and started again: ids and queue kept).

## Not in the prototype

- Comix's chapters (the extension, not the host: see above).
- APK installs convert with dex2jar, which fails for keiyoushi's minified APKs at run time (the signature check passes);
  the jar is the format to use.
- Packaging into MangaList's image (a JRE plus ~152 MB, and the WebView's libraries) - lane M.

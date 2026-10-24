# The host's Suwayomi-compatible API (contract between the host and MangaList)

Integrator-owned (2026-10-22). The extension host (lane H) implements this; MangaList's `ExtensionsChapterClient` (lane M)
consumes it. A lane that needs a change proposes it in its own vault note / report - it does not edit this file.

Goal: everything MangaList asks Suwayomi today (`mangalist.downloads.contracts.ChapterClient` plus `search_manga`,
`search_series`, `search_series_page`, `server_settings`) answered by the host with the **same semantics**, so MangaList's
chapter features (chapters panel, Check for new chapters, the scheduled chapter check, the find pass, the chapter
arrivals pass, Retry / Cancel) work unchanged on top of a thin client.

All under `/compat`, JSON, errors `{error, type}` with 400 / 404 / 502 as the rest of the API. Source ids are strings
(64-bit); manga and chapter ids are host-assigned **integers, stable across restarts** (persisted in the host's data
folder), like Suwayomi's.

| Call | Answer | Suwayomi equivalent |
| --- | --- | --- |
| `GET /compat/about` | `{name: "MangaList extension host", version, instance}` - `instance`: the id of the host's database (see the end) | `aboutServer` |
| `GET /compat/settings` | `{version, downloadAsCbz: true, downloadsPath, flareSolverr: bool, flareSolverrUrl}` | `settings` |
| `GET /compat/sources` | `[{id, name, displayName, lang, extension}]` - `displayName` exactly as Suwayomi forms it (`MangaDex (EN)`) | `sources` |
| `POST /compat/search {sourceId, query, page}` | `{mangas: [{id, title, url, sourceId, inLibrary}], hasNextPage}` - a manga id is created on first sight of (sourceId, url) | `fetchSourceManga(type: SEARCH)` |
| `POST /compat/manga/{id}/chapters {fetchDetails?: bool}` | `{manga: {id, title, url, sourceId, inLibrary}, sourceName: displayName, chapters: [{id, mangaId, name, chapterNumber (float, -1 unknown; read from the name as Suwayomi does), scanlator, url, realUrl, uploadDate (epoch ms as a string), downloaded, sourceOrder}]}` - fetched from the source now; chapter ids are created on first sight of (mangaId, url); details are fetched only when never fetched or asked | `fetchMangaAndChapters` |
| `POST /compat/manga/{id}/library` | `{id, inLibrary: true}` | `updateManga(inLibrary)` |
| `POST /compat/downloads/enqueue {ids}` / `POST /compat/downloads/dequeue {ids}` | the queue (below) | `enqueueChapterDownloads` / `dequeueChapterDownloads` |
| `GET /compat/downloads` | `{state: STARTED\|STOPPED, queue: [{chapterId, state: QUEUED\|DOWNLOADING\|ERROR, progress (0-1), tries}]}` | `downloadStatus` |
| `POST /compat/chapters {ids}` | the chapters (as above) by id, with `downloaded` | `chapters(filter: id in)` |
| `POST /compat/chapters/delete-downloaded {ids}` | `{ids}` - the CBZ (and folders left empty) deleted, `downloaded` false | `deleteDownloadedChapters` |

## Download semantics (as Suwayomi v2.4.2366)

- A queued chapter is downloaded page by page with the source's own client and headers (`/sources/{id}/image` path),
  into **one CBZ**: `<downloadsPath>/mangas/<source displayName>/<manga title>/<scanlator>_<chapter name>.cbz` (no
  scanlator: `<chapter name>.cbz`), each path part cleaned exactly as MangaList's `chapter_arrivals.suwayomi_name` expects
  (Mihon's `DiskUtil.buildValidFilename`: invalid characters, 240-byte cut). Pages `001.<ext>`, `002.<ext>`, ... in order.
- The CBZ holds a `ComicInfo.xml` with at least `Title` (chapter name), `Series` (manga title), `Number` (the chapter
  number as MangaList reads it), `Translator` (scanlator), `Web` (the chapter's real URL, else its URL on the source: never empty) - what
  `chapter_arrivals.read_comicinfo` reads. On Windows a reserved device name (`CON`, `NUL`, `COM1`, ...) in a path part
  gets a leading `_` (MangaList's `suwayomi_name(..., windows=True)` does the same).
- The file is written to a temporary name and renamed when complete: a CBZ that exists is whole.
- A finished chapter **leaves the queue** and reads `downloaded: true`. A failing chapter is retried up to 3 times, then
  stays in the queue as `ERROR` with its `tries`; enqueueing an `ERROR` chapter again gives it another try (MangaList's
  chapter_retry relies on this).
- Downloads run one or two at a time; the queue survives a host restart (persisted).

## Host start options MangaList relies on

`--data DIR`, `--bind`, `--port`, `--downloads DIR` (default `<data>/downloads`), `--flaresolverr URL`, `--repo URL`
(default keiyoushi's `index.json`), `--webview on|off`.

## Additions as built - 2026-10-22 (integrator, after merging lanes H and M)

Agreed from the lanes' reports; MangaList's client relies on these.

- Extension management (outside `/compat`, used by MangaList's Extensions page): `GET /extensions` (with `repoVersion`,
  `updateAvailable`, `signatureHash`), `POST /extensions/update {pkg, repo?}`, `POST /extensions/uninstall {pkg}` (answers
  the extension), `GET /sources/{id}/preferences` (each with `position`), `POST /sources/{id}/preferences {key, value}`
  (answers the whole list). An extension whose signing key differs from the repository's `signingKey` is refused.
- `GET /webview`, `POST /webview/start`; `POST /webview/debug-eval` is a diagnostics call (local API only; candidate to put
  behind a flag).
- Host option `--download-parallel N` (sources downloading at once, default 2; one chapter at a time per source).
- A manga whose source lists no chapters answers `200` with `chapters: []` (not an error).
- `chapterNumber` is written in its shortest float form (`1.1`).
- Difference from Suwayomi, deliberate: an `ERROR` chapter stays `ERROR` across a host restart (Suwayomi gives it 3 fresh
  tries); MangaList's chapter_retry re-enqueues it on its schedule. Except on the host's first start on a new version
  (2026-10-24, MangaList's transition audit E3): then every `ERROR` chapter gets fresh tries - the failure may be what the
  new version fixes. A try past the limit is logged as "extra try N (after 3 tries)".

## Repositories and updates - 2026-10-22 (integrator, after merging lane U)

- `GET /repos` (url, name, signing-key fingerprint, extension count, last fetch / error), `POST /repos {url}` (the index is
  fetched and checked first; a non-index URL is refused), `DELETE /repos {url}` (also `POST /repos/remove`). Kept in the
  host's `repos.json`; `--repo` may be given more than once (keiyoushi's `index.json` stays the default).
- `GET /repo` lists every repository's extensions; each entry carries `repo` (its index URL) and `repoName`; installed
  extensions carry the repository they came from and `repoListed` (false once that repository is removed: no updates).
- Each repository's own signing key is enforced; a key that changes later makes that repository untrusted. An extension
  updates only from its own repository (`400` otherwise); the same package from another repository needs an uninstall
  first (`409`).
- `POST /extensions/update {pkg, repo?, waitSeconds?}`: while a source of that extension is downloading, the host holds
  that source's queue and waits up to `waitSeconds`, else answers `409` (MangaList's `extension-updates` job waits 60 s and
  otherwise tries again at its next run).

## Registering a manga - 2026-10-24 (integrator, after merging lane C)

- `POST /compat/manga {sourceId, url, title}` -> `{id, title, url, sourceId, inLibrary}`: get-or-create the host's manga id
  for (sourceId, url) (`404` when the source is not installed, `400` without sourceId or url). Used once, by MangaList's
  carry-over from Suwayomi (`mangalist/services/extensions/carry_over.py`), to give each series' chosen manga a host id.

## The database's id, a manga by id, logs - 2026-10-24 (lane F, MangaList's transition audit)

- `GET /compat/about` -> `instance`: a random id of `host.db`, made with it (schema `user_version` 2: the `host_info`
  table) and never changed. MangaList remembers it and compares it before it uses the host ids it stored
  (`mangalist/downloads/host_identity.py`): another id means the database was reset or restored, and those ids name other
  mangas and chapters now.
- `GET /compat/manga/{id}` -> `{id, title, url, sourceId, inLibrary, initialized, thumbnailUrl}` as stored (no request to
  the source; `404` for an unknown id): MangaList's carry-back to Suwayomi reads a host manga's url with it.
- At its start the host logs an ERROR when file names are not UTF-8 (`sun.jnu.encoding`; an ASCII locale fails every
  non-ASCII chapter, group or manga name), and its log lines are `yyyy-MM-dd HH:mm:ss  LEVEL  logger  [thread] text`
  (MangaList's own format, `WARN` written `WARNING`).

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
| `GET /compat/about` | `{name: "MangaList extension host", version}` | `aboutServer` |
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
  number as MangaList reads it), `Translator` (scanlator), `Web` (the chapter's URL) - what `chapter_arrivals.read_comicinfo`
  reads.
- The file is written to a temporary name and renamed when complete: a CBZ that exists is whole.
- A finished chapter **leaves the queue** and reads `downloaded: true`. A failing chapter is retried up to 3 times, then
  stays in the queue as `ERROR` with its `tries`; enqueueing an `ERROR` chapter again gives it another try (MangaList's
  chapter_retry relies on this).
- Downloads run one or two at a time; the queue survives a host restart (persisted).

## Host start options MangaList relies on

`--data DIR`, `--bind`, `--port`, `--downloads DIR` (default `<data>/downloads`), `--flaresolverr URL`, `--repo URL`
(default keiyoushi's `index.json`), `--webview on|off`.

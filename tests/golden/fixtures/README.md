# Matcher golden set fixtures

Recorded responses of the public [MangaUpdates API](https://api.mangaupdates.com) (v1), used by the
stage-2 matcher golden set (`tests/golden`). Series data (c) MangaUpdates - credited here as the
API's acceptable use policy asks. The tests replay these files; no test ever contacts the network.

- Every `search.*.json` / `series.*.json` file is copied unchanged (byte-identical) from MangaPixer
  1.31.1 (release commit `858c7df`, `tests/MangaPixer.Server.Tests/Features/Metadata/Fixtures/GoldenSet/`).
  MangaPixer recorded them with at least 0.75 s between requests, PUBLIC well-known titles only (never
  a name from a real library): the first set on 2026-09-26, one record for 1.26.1, page 2 of four
  searches, live-run lookalikes and `publishers[]` merged into three records for 1.27.0, image URLs in
  three searches for 1.28.0, and the spin-off, author-tagged alias and origin-tie searches for 1.30.0.
  No new requests were made for Manga-List. MangaPixer's `covers.json` (cover hashes) is not copied:
  Manga-List compares no covers.
- `search.<slug>.json`: `POST /v1/series/search` with `{search, page: 1, perpage: 10, filter_types}`;
  `search.<slug>.p2.json` is page 2 (the file carries `"page": 2`). `filter_types` is the fixed
  automatic-search filter (`Novel`, `Doujinshi`, `Artbook`, `Drama CD`); files ending in `.dj.json`
  were recorded with Doujinshi allowed. Each file keeps the query, the filter and the response trimmed
  to `total_hits` and, per result, `record.series_id, title, type, year` plus `hit_title`.
- `series.<id>.json`: `GET /v1/series/{id}`, trimmed to what the matcher reads: `series_id, title,
  associated[].title, type, year, status, latest_chapter, authors[] (name, type)`, the
  `Webtoon/Webcomic` category votes (when present), `related_series[] (relation_type,
  related_series_id)` and, where recorded, `publishers[] (publisher_name, type, notes)`.
- A missing fixture fails the case with the exact request it would need, so the set stays
  reproducible.
- `../mangapixer_report.txt` is MangaPixer's own result for the cases Manga-List runs: MangaPixer's
  golden harness (production detector, planner, retrieval loop, provider mapping and scorer) run at
  `858c7df` over these recordings, printing per case the detector class, the band, chosen id, title /
  adjusted score and reasons, and the searches and GETs it sent, plus the aggregate lines at the
  default, loosest and strictest thresholds. The Manga-List golden test compares its results with it
  line by line.

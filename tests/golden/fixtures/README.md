# Matcher golden set fixtures

Recorded responses of the public [MangaUpdates API](https://api.mangaupdates.com) (v1), used by the
stage-2 matcher golden set (`tests/golden`). Series data (c) MangaUpdates - credited here as the
API's acceptable use policy asks. The tests replay these files; no test ever contacts the network.

- Copied unchanged (byte-identical) from MangaPixer v1.26.0
  (`tests/MangaPixer.Core.Tests/Metadata/Fixtures/GoldenSet/`), where they were captured on
  2026-09-26 with at least 0.75 s between requests, PUBLIC well-known titles only (never a name from a
  real library). No new requests were made for Manga-List.
- 1.26.1 (MangaPixer release commit `1e4132d`) added `series.40032173896.json` (the main record of a
  title already in the set), recorded the same way; copied unchanged.
- `search.<slug>.json`: `POST /v1/series/search` with `{search, page: 1, perpage: 10, filter_types}`.
  `filter_types` is the fixed automatic-search filter (`Novel`, `Doujinshi`, `Artbook`, `Drama CD`);
  files ending in `.dj.json` were recorded with Doujinshi allowed. Each file keeps the query, the
  filter and the response trimmed to `total_hits` and, per result, `record.series_id, title, type,
  year` plus `hit_title`.
- `series.<id>.json`: `GET /v1/series/{id}`, trimmed to what the matcher reads: `series_id, title,
  associated[].title, type, year, status, latest_chapter, authors[] (name, type)`, the
  `Webtoon/Webcomic` category votes (when present) and `related_series[] (relation_type,
  related_series_id)`.
- A missing fixture fails the case with the exact request it would need, so the set stays
  reproducible.
- `../mangapixer_report.txt` is MangaPixer's own golden report for the ported version (its
  `Report_AutoRateAndPrecision` output: per-case band, id, title / adjusted score and reasons, plus
  the aggregate lines). The Manga-List golden test compares its results with it line by line.

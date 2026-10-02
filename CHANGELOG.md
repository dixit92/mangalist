# Changelog

Versions are calendar versions `YEAR.MONTH.N`: the release's year and month plus a counter that
restarts each month (`2026.9.0`, `2026.9.1`, `2026.10.0`). To release: rename `[Unreleased]` to
`[YEAR.MONTH.N] - YYYY-MM-DD`, start a new empty `[Unreleased]`, commit, then tag `vYEAR.MONTH.N` and
push the tag. CI refuses a tag without its section here and uses the section as the release notes.

## [Unreleased]

- MangaUpdates matching follows MangaPixer's matcher up to 1.31.1 (was 1.26.1):
  - The volume / chapter count check compares the highest volume or chapter number in the file names,
    not the number of files. `.5` extras do not count, and neither do volume and chapter files mixed in
    one folder. It also reads the English publisher's totals and the chapter total in the status line,
    so long-running webtoons and English re-releases no longer go to *needs review* for a count
    conflict.
  - A category folder (`Manga`, `Manhwa`, ...) and tall pages only ever add confidence. A manhwa
    under a `Manga` folder is no longer marked *needs review*.
  - A folder subtitle that belongs to a spin-off ranks the spin-off first, but always as *needs
    review* (new reasons: series family, subtitle family).
  - Author names written as `Title by Author` or `Author - Title` help pick the right record.
  - A record listed under another work's name with an author tag (`English Title (AUTHOR Name)`) is
    found and checked.
  - Search reads a second results page when the first one ends in a tie.
- Matches from earlier versions keep their tier, marked as from an older version; **Check MU**
  re-matches them. Confirmed matches are never re-scored.
- A MangaUpdates record that no longer exists is never linked. A failed request now leaves the row
  unchanged instead of scoring it on partial data.

## [2026.9.0] - 2026-09-27

First packaged release.

- MangaUpdates matching uses a port of MangaPixer's matcher (1.26.1): it first decides whether a
  folder is one work, searches several title variants, and sorts results into *auto*, *needs
  review* (orange, with reasons) and *unmatched*. Numbered chapters with chapter subtitles count as
  one series; author names in brackets help pick between same-titled records.
- Matches cached by earlier versions keep their old score, marked as legacy; **Check MU** re-matches
  them. Confirmed matches are never re-scored.
- Settings, cache and logs moved to a per-user folder; the old `data` folder next to the program is
  copied over once on first start. The portable Windows zip keeps its data next to the exe.
- Packages for Windows (installer and portable zip), macOS (Apple silicon and Intel) and Linux
  (AppImage and tar.gz), with `SHA256SUMS`.
- `--version` and `--smoke-test` command-line options.

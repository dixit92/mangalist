# Changelog

Versions are calendar versions `YEAR.MONTH.N`: the release's year and month plus a counter that
restarts each month (`2026.9.0`, `2026.9.1`, `2026.10.0`). To release: rename `[Unreleased]` to
`[YEAR.MONTH.N] - YYYY-MM-DD`, start a new empty `[Unreleased]`, commit, then tag `vYEAR.MONTH.N` and
push the tag. CI refuses a tag without its section here and uses the section as the release notes.

## [Unreleased]

- **MangaPixer 1.34.0:** a folder MangaPixer marks as a **collection about** a series (fan works such as doujinshi) shows as
  "Collection about <series>" and is not treated as that series - no Behind, missing or upgrade numbers, never matched by
  MangaList, and nothing below it inherits the series. A link state MangaList does not know yet is handled the same way
  ("not a series"), so a future MangaPixer never breaks the sync. Folders set to Don't match in MangaPixer show as "Not a
  series" too.
- For a folder MangaPixer knows, the **MU Title, Licensed, Behind and Completed** columns now show MangaPixer's data (they used
  to show MangaList's own older match, if it had one), and stay empty when MangaPixer says it is not a series.

- **MangaPixer as a source** (MangaPixer 1.33.0 or later): toolbar **MangaPixer...** - the server
  address and an API token (MangaPixer Administration > API tokens), a connection test, and each root
  mapped to a MangaPixer library automatically by its folder names (with a manual override). A folder
  MangaPixer knows takes MangaPixer's link, record, volume list and Completion answer, and MangaList
  no longer looks it up on MangaUpdates itself. The token is never logged; certificate checks are on
  unless you turn them off for a self-signed MangaPixer. The headless runner syncs it daily at 03:15
  (`MANGALIST_MANGAPIXER_SYNC_SCHEDULE`), before the rescan.
- **Rescan states:** new **State**, **Gaps** and **Official source** columns and a state filter - Wanted
  (empty folder: official available / awaiting release / scanlation only), Missing volumes, Missing
  chapters, Upgrade available, Up to date, Complete (and "Complete + Upgrade available"). A **Wanted**
  panel lists what is wanted, missing or upgradable with its official links.
- **Official sources** for every series: MangaPixer's official links, AniList's English links, the
  English publisher, and store searches (Amazon, BookWalker Global, Kobo). Only web links are opened.
- **File names read exactly:** FMD2 names take their numbers from the bracket only (a chapter title such
  as "Episode 3" is no longer read as a number), release names (`v05 (+ c041-045)`, `(Digital)`, `(f2)`)
  are understood, and decimals stay exact (`291.999`). Folders of bare-number files (`01.cbz`) ask once
  "Volumes or chapters?" (row menu) and remember the answer.
- A series' files are listed in the same order on every file system.

## [2026.10.2] - 2026-10-03

The first MangaList release (the foundations of the redesign; testing build).

- **Renamed to MangaList** (was Manga List / Manga-List; repository `dixit92/MangaList`). Settings and the
  MangaUpdates cache stay where they are; the Windows installer upgrades the old version in place and
  replaces its "Manga List" shortcuts. `MANGA_LIST_DATA_DIR` still works; the new name is `MANGALIST_DATA_DIR`.
- **Several library roots, with exclusions:** a Roots manager lists every root, and per-root patterns (e.g.
  `@Oneshots`, `*.txt`) are never scanned - with a live preview of what a pattern hides. The configured
  Manga Root becomes the first root. Archives lying directly in a root are reported, not matched.
- **One database** (`mangalist.db`) holds roots, exclusions, series and the MangaUpdates links; the old
  `mu_cache.db` and `config.json` are imported once and kept. A renamed series folder keeps its link.
- **Headless runner and Docker / Unraid image:** `python -m mangalist --headless` rescans the roots on a
  schedule; the Docker image runs the app in the browser over HTTPS (with clipboard sync) next to the runner.
  The image is not published yet - see the README. It is for local use only: there is no login, so never
  expose it to the internet (use a VPN or Tailscale).
- **MangaUpdates matching follows MangaPixer 1.32.0** (was 1.31.1), e.g. `Webtoon` / `Webtoons` folders count
  as webtoon evidence again, and `No. N` titles and `Library Edition` releases are read correctly.
  Older matches are re-checked on the next Check MU.
- Groundwork for later versions, not visible yet: a file-name parser that reads FMD2 names and release names
  exactly, and an undo journal for renames.

## [2026.10.1] - 2026-10-02

- AniList lookups (the Behind column's chapters-per-volume estimate) work again under AniList's current
  limit of 30 requests a minute: requests are spaced to fit, a rate-limit answer is retried once after the
  wait AniList asks for, and the log shows the real HTTP status and reason (it used to say "HTTP 0"). AniList
  states chapter and volume totals only for finished series, so ongoing series still get none.
- Manga-List identifies itself to MangaUpdates and AniList with its own User-Agent
  (`MangaList/<version>`); AniList's front end blocks generic client signatures.

## [2026.10.0] - 2026-10-02

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

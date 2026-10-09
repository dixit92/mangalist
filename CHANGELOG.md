# Changelog

Versions are calendar versions `YEAR.MONTH.N`: the release's year and month plus a counter that
restarts each month (`2026.9.0`, `2026.9.1`, `2026.10.0`). To release: rename `[Unreleased]` to
`[YEAR.MONTH.N] - YYYY-MM-DD`, start a new empty `[Unreleased]`, commit, then tag `vYEAR.MONTH.N` and
push the tag. CI refuses a tag without its section here and uses the section as the release notes.

## [Unreleased]

- **A new root pairs with its MangaPixer library on its first scan:** it used to stay unpaired until the next "Sync now" or
  the nightly sync. Every scan now re-pairs automatically paired roots from the libraries already synced (no network;
  your manual pairings stay), which also keeps the matched counts current.
- **Settings > Library shows each root's MangaPixer library:** e.g. "MangaPixer: Other › M/Manga · 12 of 12 series" for
  a root inside a library, or why it has none ("not paired yet", "no library matched these folders", "not paired (your
  choice)").
- **Sync now on the MangaPixer card** in Settings > Connected services, next to Test and Edit (it was only inside
  Edit).

## [2026.10.7] - 2026-10-09

The sixth MangaList release (testing build): safer duplicate detection, the series' scanlation group kept by default, a busy state while deleting, and excluded folders no longer reported missing.

- **Fewer false duplicates (safety):** files only count as copies when their names differ by tags alone (`[group]`,
  `(Digital)`, a year, a copy marker). Names that differ by another number - chapters written before their volume
  (`009 Vol 01 Title` next to `008 Vol 01 Title`), or `Season 1 v01` next to `Season 2 v01` - are different units and
  no longer listed (they were, with all but one pre-marked Discard).
- **The series' group:** when copies come from different scanlation groups, the default Keep is the copy from the group
  the neighbouring chapters come from (else the folder's most used group), tagged "series' group" - a series keeps
  one translation style, even when it changed groups along the way.
- **The Duplicates view shows it is working:** a moving bar, "Deleting N files...", "Rescanning the library...",
  "Looking for duplicate files...", with the list greyed out until it is up to date again.
- **Excluding a folder no longer asks whether it is missing:** a series folder you add to a library's exclusions is
  let go quietly (its MangaUpdates link is kept, so removing the exclusion brings it back as it was).
- The library editor no longer shows **Origin hint**, **Enforce naming** and **Staging folder**: nothing used them yet
  (finished downloads wait in qBittorrent's download folder, one for every library). They come back with the renamer.

## [2026.10.6] - 2026-10-09

The fifth MangaList release (testing build): duplicates one series at a time, Open in MangaPixer, a Duplicates column that counts files, and links that work in the Unraid container.

- **Duplicates, one series at a time:** each series in the Duplicates view has its own **Apply for this series**, and
  right-clicking a series in the List offers **Review duplicate files**, which opens the view on that series only.
- **Open in MangaPixer:** a series MangaPixer knows has a link to its MangaPixer page (from the Duplicates view and the
  row menu), to compare the copies in MangaPixer's reader.
- The List's **Dupe** column is now **Duplicates**: per series, how many volume and chapter numbers are held twice
  (e.g. "1 vol. · 2 ch."), plus "+1 folder" when the same series is in another folder. The column menu says
  "Examined" for the ✓ column.
- Links in the Unraid container (which has no browser) are now copied to the clipboard, with a note, instead of doing
  nothing.

## [2026.10.5] - 2026-10-08

The fourth MangaList release (testing build): a new look in two tabs (List and Download), one Settings dialog, a Duplicates view, MangaPixer library scans after filing, and a new icon.

- **A new look, in two tabs:** **List** (your library, with state chips - Missing volumes, Missing chapters, Upgrades,
  Duplicates and more - and a details panel you can collapse) and **Download** (the series to get, grouped, with
  nyaa's releases for the one you select and the downloads in progress). "Get the missing volumes" in the List tab
  takes you to that series in the Download tab. The app bundles the IBM Plex fonts.
- **One Settings dialog** replaces the separate dialogs: Library, Connected services (MangaPixer, qBittorrent),
  Download sources (nyaa: English and/or raw, hide light novels, only trusted uploaders), Matching and Automation
  (the container's schedules, Remove Completed).
- **Duplicates:** the Duplicates chip lists series held in more than one folder, and duplicate files (the same number
  twice in one folder, MangaPixer's rule) to keep or discard. Discarded files are deleted only after a confirmation
  that lists every one of them, and one copy of each number always stays.
- **MangaPixer sees new volumes within minutes:** after filing downloads, MangaList asks MangaPixer (1.36.0 or later) to
  rescan each library it filed into - one request per library; when MangaPixer is busy or cooling down, again after the
  time it names. The token needs the **Request library scans** permission (create a new token with it); without it
  MangaList stops asking until a new token is entered. Settings > Automation can switch the requests off.
- After filing, MangaList also records the series' new volumes right away (they no longer show as missing until the
  nightly rescan).
- **A new icon:** MangaPixer's icon turned 90 degrees, in inverted colours - for the app, the installers, the web GUI of the
  container and its Unraid label (which showed MangaPixer's own icon until now).

## [2026.10.4] - 2026-10-07

The third MangaList release (testing build): missing volumes found on nyaa, downloaded with qBittorrent and filed by hard link (Unraid container, opt-in), plus split chapters and GUI fixes.

- **Volumes from nyaa, filed for you (Unraid container, opt-in with `MANGALIST_DOWNLOADS=1`):** for a series
  MangaPixer has matched that is licensed in English, **Find volumes on nyaa** (Wanted panel, row menu) lists
  nyaa's English-translated releases of that series - releases of other series sharing the name are left out -
  ranked by how many of your missing volumes they hold (when MangaList cannot tell which English volumes are out:
  how many volumes you do not have - a release on nyaa is itself proof that a volume is out), Digital before scans,
  trusted uploaders marked, volumes
  you already have labelled, releases without seeders and light novels hidden. You pick one; MangaList adds it to
  qBittorrent in its own category `mangalist`.
- **Arrivals:** once the torrent has finished, MangaList hard-links only the missing volumes into the folder where
  that series already keeps its volumes (asked when the layout is unclear), keeping the release's file names,
  through the undo journal - nothing in the library is ever replaced. Without a possible hard link it copies and
  verifies the file instead and says so.
- **Remove Completed** (Sonarr / Radarr style, on by default): when qBittorrent has stopped the torrent at its seed
  goal and the library files are checked, MangaList asks qBittorrent to delete the torrent and its downloaded copy -
  only ever in the `mangalist` category, never when filing failed, never when the torrent's data lies in a library root.
  A torrent you stop yourself before its seed goal is kept (resume it, or remove it in qBittorrent).
  A torrent you remove in qBittorrent yourself after its volumes were filed is "done", not "failed".
- **qBittorrent** (toolbar): Web UI address, user name, password (stored outside the settings, never shown
  again), download folder (qBittorrent's own copy while it seeds - not the library), Remove Completed, and a connection test. A **Downloads** list shows each download's state.
- When MangaPixer's volume list has no English dates (e.g. its dates source could not be reached), the English
  publishers' volume count decides which English volumes are out - as when there is no list - instead of "Can't tell".
- Table columns can be resized in every dialog (MangaPixer, Missing series, Find volumes, Downloads); buttons no longer end
  in "...", and the **Wanted panel** toggle is a button that stays pressed while the panel is open.
- **Split chapters count as chapters:** files numbered as parts of a chapter (`Ch. 2.1` + `Ch. 2.2`) are chapter 2, as
  MangaPixer reads them - such a folder no longer shows those chapters as missing. A part missing between two others
  (`4.1` and `4.3`) is listed; a lone `10.5` is still an extra.
- **Check now** (Downloads dialog) runs the downloads check on demand - filing finished downloads and Remove Completed -
  besides the hourly schedule.
- `MANGALIST_DOWNLOADS_SCHEDULE` (default `every 1h`) sets how often finished downloads are filed and completed
  torrents removed. The container must see the library and the torrent folder through **one** mount
  (`/mnt/user` -> `/data`, as qBittorrent does) for hard links; see the README.

## [2026.10.3] - 2026-10-05

The second MangaList release (testing build): phase 1 - knowing what is missing - plus series identity and MangaPixer 1.34.0 support.

- **Series keep their identity when folders move:** MangaList now recognises a series folder that was renamed or moved - within
  a root or to another root, also when chapters were added or removed meanwhile - by its archives (MangaPixer's method: each
  archive gets a content signature, and a folder that received at least 80% of a vanished folder's archives is the same series).
  The MangaUpdates link, the Behind override, the "volumes or chapters?" answer and the examined mark move with it; a folder's own
  data is never overwritten. With MangaPixer connected, its own move detection is used too.
- **Missing series:** a series folder that vanished and could not be recognised (an empty folder, or anything ambiguous) is listed
  under **Missing (n)**, where you re-attach it to its new folder or forget it. Nothing is deleted on its own.
- Archive signatures are computed in the background after a scan (at most 128 KiB read per archive, once; afterwards only new or
  changed files). **On the first scan after the upgrade nothing is signed yet:** a folder renamed before then shows under Missing
  instead of being recognised.
- The headless runner now records its rescans like the window does (stored units, your "volumes or chapters?" answers, moves).
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

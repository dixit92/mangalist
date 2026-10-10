# Changelog

Versions are calendar versions `YEAR.MONTH.N`: the release's year and month plus a counter that
restarts each month (`2026.9.0`, `2026.9.1`, `2026.10.0`). To release: rename `[Unreleased]` to
`[YEAR.MONTH.N] - YYYY-MM-DD`, start a new empty `[Unreleased]`, commit, then tag `vYEAR.MONTH.N` and
push the tag. CI refuses a tag without its section here and uses the section as the release notes.

## [Unreleased]

- **Number boxes show their up / down arrows again** (Settings > Download sources > Download budget, Logging): the stylesheet had hidden them. Settings cards have a little more room inside their border.
- **No personal examples in the app**: the Windows server field and its explanation use a neutral example (`MYSERVER`); test data no longer borrows real library or share names.

## [2026.10.14] - 2026-10-10

The thirteenth MangaList release (testing build): MangaList's own naming scheme and a library renamer that brings existing files to it safely (dry run, preview, batches with undo), chapter downloads through Suwayomi, schedules picked from a list, and the holding folder's size.

- **MangaList's own naming scheme** (the names the renamer and chapter downloads will give files): chapters are `Ch. 0102.00 Vol. 012 (Chapter title) [Group].cbz` - the chapter number first with four digits and always two decimals (`Ch. 0010.50`), then the volume, the chapter title and the group, each only when known; a range is `Ch. 0010.00-0012.00`. Volumes are `Series title - Vol. 001 [Group].cbz` (a range `Vol. 001-003`). Every chapter name starts with its number, so any file browser sorts them in reading order, and a volume learned later only changes the end of the name. Characters Windows does not allow are replaced, brackets inside a title become `[ ]` and inside a group `( )`, and the group is capped at 32 characters. No name is longer than 255 bytes; when the Windows server name is set, no Windows path (`\\SERVER\share\...`) is longer than 259 characters - the chapter title is shortened first (ending with `…`), then the group, never the numbers.
- **Every library reads these names exactly**, also a library with its own naming scheme: a chapter title such as "The Vol 2 Begins" or "Chapter 5 of the Ch. 3 Arc" is no longer read as volume 2 or chapters 3-10.
- **More chapter names are understood**: chapters a site labels with its own word ("Contact. 0001", "Episode 35", "report011", "Pact 0015", "Lesson", "Stage", "Round", "Night" and others) are chapters, not extras; an FMD2 name whose bracket starts with a number (`0076 [0076  Arc title (3)]`) is that chapter; a volume release with "Part 5" in its title (`Series - Part 5 - Subtitle v05 (2022) (Digital)`) is volume 5, not "5".
- **A bare number after the series title counts as a chapter** (`Series 07.cbz`, `Series. 035 (2023) (Digital)`, `Series 014.6  Chapter title.cbz`) until you answer the series' "Volumes or chapters?" question, which stays open; answering "volumes" turns them into volumes. A name that is only a number (`01.cbz`) still waits for the answer.
- **Naming fixes from a dry run over a real library**: `<Series> 001 Vol 01 <chapter title>` is chapter 1 of volume 1 (not volume 1); a volume keeps the season or part its name states (`<Series> Season 2 - Vol. 001`), so two seasons' volume 1 no longer collide; a name cut off after "Ch." (`0001 [Vol. 0001 Ch`) is left alone instead of becoming volume 1; MangaDex's `[no group]` (and `Unknown`, `N/A`, `none`) is no group; a chapter title that only repeats the number (`Ch. 0144.00 (Chapter 144)`) is left out; FMD2 brackets that start with any word and a number (`[Hug 0001]`, `[No. 0001]`) are that chapter - but not `[Season 2]`, `[Part 3]`, `[Extra 4]` or a title such as `[Love 2 Hate]`.
- **The download budget says when its usage moves**: one log line when a check takes sizes from qBittorrent or releases a failed download ("now 12.3 GB of 50 GB") - the first live check changed the usage from 36.1 to 12.3 GB without a word at Info.
- **Schedules are picked, not typed.** Settings > Automation offers Weekly (a day and a time), Daily (a time), Every 12 hours, Every 6 hours or Off for each schedule; filing finished downloads also keeps Every hour, its default. A container value outside these (e.g. every 3 hours) is shown as its own choice, never changed silently. The runner understands the new weekly form (`weekly@sun 03:30`).
- **The holding folder says how much space it takes**: "1.2 GB held (4 batches, 32 files)" in Settings, "1.2 GB of replaced chapters in the holding folder (32 chapter files, 4 series)" in the Download tab.
- **Finished downloads leave the In progress list.** A download whose torrent left qBittorrent at its seed goal ("Filed v36 - done") or that was cancelled is hidden; "Show finished (2)" shows it again. Failed downloads stay in view.
- **The library renamer: bring file names to the naming scheme, safely.** Right-click a series > **Rename to the scheme…** (the first step: one series, then check its read marks in MangaPixer), then **Rename library…** above the List (the library picked, or every library). The window looks first and changes nothing: how many files would be renamed, are already named by the scheme, are left alone (unreadable names, extras) or would collide (two files to one name - both keep their names, with "Review duplicates…"), which naming patterns were found, which chapter titles the length rule would shorten and which hold a volume or chapter word; per series, old -> new in colour. **Rename N files…** asks with the counts, then renames in batches through the journal (never over an existing file, the library's lock held); **Undo last batch** puts a batch back. Only names change, never a file's contents; the window says plainly that files MangaPixer has not analysed yet lose their read state. After each batch the library is rescanned and MangaPixer is asked to scan it.
- **Rename pending.** The List flags series whose files are not named by the scheme (State column, a "Rename pending" filter under More, an optional Rename column with the count); a volume MangaPixer learns later makes its chapters pending again. Settings > Library > Edit says per library what happens to them: **Off**, **Ask before renaming** (the default) or **Rename automatically** - then the scheduled rescan renames them (at most 500 files a run, every batch logged and undoable), once you have started that library's conversion in the Renamer window.
- **Settings > Library: File naming** explains the scheme and takes the **Windows server name** (e.g. `MYSERVER`, optional): with it, names are shortened where the whole Windows path (`\\SERVER\share\...`) would pass 259 characters.
- **Missing chapters come from Suwayomi.** Connect a Suwayomi-Server (tested with v2.4.2366) under Settings > Connected services > Suwayomi: its address, an optional basic-auth login (the password is write-only, never shown or logged) and the folder MangaList reads Suwayomi's downloads from. Test connection shows Suwayomi's version and warns when "Download as CBZ" is off, when the folder holds no `mangas` folder, or when FlareSolverr is off - FlareSolverr is set in Suwayomi itself (e.g. `http://<unraid-ip>:8191`).
- **Settings > Download sources > Suwayomi sources** lists the sources Suwayomi has installed (from its extensions): tick the ones MangaList may use and put them in order. MangaDex alone is used until you choose.
- **The chapters panel.** In the Download tab, selecting a series of the Missing chapters group (once Suwayomi is connected) finds it in Suwayomi - on MangaDex by the MangaDex id MangaPixer links, on your other sources (Weeb Central, MangaFire, MangaFox, Flame Comics, ...) by title, and then only after you pick the right series with "Use this series" (remembered; "Not this series?" forgets it). It lists the missing chapters with the scanlation groups that have them: the series' group is the folder's usual group, else the group with the most of these chapters, and you can change it for the series (remembered) or per chapter. Send to Suwayomi asks first, then Suwayomi downloads them.
- **Chapter downloads show like torrents**: "Downloading ch 101-104" / "Filed ch 101-104" chips on the "To get" row and one In progress row per send ("Ch. 101-104 · <group> · MangaDex (EN)"). They are outside the download budget, which counts torrents only (they do not seed).
- **Finished chapters are filed by the hourly check and Check now**: the CBZ is found in Suwayomi's download folder, its ComicInfo (chapter number, title, group) is read, and it is moved into the series folder under MangaList's chapter naming scheme through the journal (a hard link on the same share, else a verified copy; never over an existing file or a chapter already held). Suwayomi then deletes its copy; the library rescans and MangaPixer is asked to scan, as after filing volumes. A chapter that cannot be filed yet says why on its row.
## [2026.10.13] - 2026-10-09

The twelfth MangaList release (testing build): a download budget (at most 50 GB downloading or seeding by default; the rest is queued and handed to qBittorrent as room frees), a Logging page in Settings, and editable schedules.

- **A download budget: MangaList keeps at most 50 GB downloading or seeding** (Settings > Download sources > Download budget; 0 = no limit). Everything MangaList has sent and not yet seen removed counts: downloading, waiting to be filed, and seeding - a partial download only its selected files, and a failed one while its torrent is still in qBittorrent. Sizes start from the release's size on nyaa (or the selected files) and follow qBittorrent's own figure once it reports one. Settings shows the usage next to the cap ("Using 12.3 GB of 50 GB").
- **A send that would go over the budget is queued.** The confirmation says so ("This would go over the download budget: MangaList is using 48 GB of 50 GB, and this adds 5 GB") and offers Queue it (the default) or Send now (past the cap). A release bigger than the whole budget on its own gets an alert naming its size and the cap: send it anyway, or cancel. Queued downloads wait in the In progress list ("Queued - 2nd in line", grey) and as "Queued v36" chips in the "To get" list; a queued release is not sent twice.
- **Queued downloads go to qBittorrent by themselves, oldest first,** in the hourly check and Check qBittorrent now, as soon as Remove Completed has made room. Right-click a queued download for Send now (past the cap), Move to the front of the queue, or Remove from the queue. A queued release that can no longer be sent fails with the reason and does not hold up the rest; lowering the budget removes nothing (new sends then wait). Every hand-over and every wait is logged with the sizes, the cap and the place in the queue.
- **Settings > Logging.** Pick what the log files hold: Error, Warning, Info (the default) or Debug, optionally a level of its own for scanning, matching, MangaPixer, nyaa, qBittorrent, filing, duplicates or upgrades, how big a file may grow and how many old ones are kept. A change applies at once in the app, and within a minute in the container's background runner (no restart). **Open the log folder** and **Copy the log folder path** are on the page. `MANGALIST_LOG_LEVEL` only sets the first-run level.
- **The schedules are editable.** Settings > Automation now lets you type the rescan, MangaPixer sync and "file finished downloads" times (`daily@03:30`, `every 12h` or `off`; a bad entry says what to write). They are stored in the database and the container's runner re-reads them without a restart, logging each new next-run time; the `MANGALIST_*_SCHEDULE` variables only seed them, and "Use the container's value" goes back to the variable. The page says that the container's runner uses them, not the desktop app.
- **Better logs.** One convention (Info for every action and external call, Warning for a problem MangaList got past, Error for a failure, Debug for detail; never a token, password or cookie - and a mask on every log line as a second guard): a scan, a MangaPixer sync and its token or connection changes, nyaa searches, duplicate and replaced-chapter clean-ups, root changes, the filing plans and a failed job now leave a line. The debug level adds MangaPixer and nyaa request lines (the endpoint only) and `urllib3` stays quiet.
- **"Sent" now reads "Downloading"** in the In progress list and the List's detail panel, as the "To get" chips say.
- **Fixed: the Published column cut the date** ("2021-11-...") with a larger table font: its width now comes from the font.

## [2026.10.12] - 2026-10-09

The eleventh MangaList release (testing build): the "To get" list shows which series already have a torrent in qBittorrent, packs without volume numbers show what their file list holds, and the releases table gains a Published column.

- **A series with a torrent in qBittorrent says so in the "To get" list.** Each row now carries coloured chips, the In progress list's badge colours: "Downloading v36" (blue), "Seeding v09-v18" (green, filed and still seeding), "Failed: ..." (red), then the search state ("Releases ready" outlined, "No releases", "Searching..."). A filed torrent used to disappear behind "Releases ready". The chips name units from the download record, so chapter downloads will use the same chips.
- **The releases panel names the series' torrents already in qBittorrent** ("Already in qBittorrent - Seeding v09-v18: ..."), and a release that is one of them cannot be sent again.
- **A pack whose title has no volume numbers shows what it holds.** Once its `.torrent` file list is read, Fills and You have are filled from the file names and the line under it says "file list: v01-v10, 10 files" instead of "contents unknown". The selected release is read first, then up to five other numberless packs one after another in the background.
- **The releases table has a Published column** (the day the release was posted on nyaa), no longer only in the tooltip.
- **Fixed: the status at the right of a "To get" row was cut off** ("Releases rea...", "Se...") on displays with fractional font metrics: the width was rounded down and the text then cut at its own width.
- **Emptying the holding folder early is logged as such.** "Empty now" used to log each deletion as "holding period over"; it now says "emptied early, on the owner's word". The automatic purge after the holding period keeps its wording.

## [2026.10.11] - 2026-10-09

The tenth MangaList release (testing build): the Library picker moves to the top bar (it applies to both tabs), and filing into a series held as volumes only is noted plainly.

- **The Library picker moved to the top bar** ("Library: All libraries ▾"), next to the scan time: it applies to the
  List and the Download tab alike, which the List's filter bar did not make clear. The top bar no longer repeats the
  library names beside it.
- Filing missing volumes into a series held as **volumes only** is now noted plainly ("the series holds no chapter
  files") - it used to give reasons about MangaPixer's chapter lists, as if chapters were involved.

## [2026.10.10] - 2026-10-09

The ninth MangaList release (testing build): the Download tab shows one group at a time (Missing volumes, Upgrades, Missing chapters) with resizable panels, an upgrade says so in the release panel, the holding folder can be emptied early, and a series can be excluded from its library from the List's right-click menu.

- **Download tab, one group at a time:** chips above the "To get" list - **Missing volumes**, **Upgrades**, **Missing
  chapters**, each with its count - show one group (remembered); "Get the volume upgrades" in the List opens the
  Upgrades group. The "To get" panel and the **In progress** panel are resizable (remembered).
- An upgrade's release panel now says **"Upgrade v23-v24"** and what happens to the chapter files they replace (it said
  "Missing ... nothing there is replaced").
- **Empty the holding folder now:** "Empty now..." for a held batch in the replaced-chapters review, and "Empty the
  holding folder now..." in Settings > Automation - after a confirmation, and only for batches whose volumes are still in
  the library.
- **Exclude from its library...** in the List's right-click menu: adds the folder to its library's exclusions (after a
  confirmation; files untouched; Settings > Library undoes it) and rescans that library.

## [2026.10.9] - 2026-10-09

The eighth MangaList release (testing build): upgrades from chapters to volumes, partial pack downloads (only the missing volumes), a Library picker with per-library rescans, Remove now for a download you stopped, and Apply to selected in Duplicates.

- **Upgrades from nyaa: chapters to volumes.** The Download tab's **Upgrades** group is no longer "coming later": a series
  held as chapters whose English volumes are out gets the same nyaa search and send as missing volumes, picked by you.
  The volumes are filed into the series folder (MangaList asks where when the chapters they replace sit in a subfolder).
- **The chapters a filed volume replaces** (only those MangaPixer's volume list puts fully inside it) are handled the way
  Settings > Automation says: **moved to a holding folder** outside every library (default
  `/data/appdata/mangalist/replaced`; restorable; emptied after 30 days - choose the period), or **deleted after a
  confirmation** that lists every file. A line in the Download tab and a Review dialog show what was moved or is waiting
  (Restore, Move, Delete, Try again, Keep).
- **Only the missing volumes of a pack.** Before sending, MangaList reads the release's `.torrent` and shows "Only the
  missing volumes (3 of 23 files, 410 MB of 1.7 GB)" - ticked by default (Settings > Download sources sets the
  default); qBittorrent then downloads only those files. Untick it for the whole pack. A torrent downloaded in part seeds
  only what it downloaded. A release with only a magnet link, or whose files say no volume, is sent whole - and says why.
- **Library picker:** with more than one library, a dropdown in the List's filter bar shows one library or all of them;
  the table, the chip counts, the Duplicates view and the Download tab's list follow it, and it is remembered. An
  optional **Library** column (hidden by default) for "All libraries".
- **Rows appear library by library** while a rescan runs, and **Rescan ▾** rescans one library on its own. A window
  closed mid-scan keeps the libraries already read.
- **A download you stopped yourself says so:** "Filed v06-v07 - stopped before its seed goal (ratio 0.70 of 2)" instead of
  "seeding" (MangaList still never removes it on its own). Right-click it: **Remove now…** removes the torrent and its
  downloaded copy from qBittorrent after a confirmation and the same library check - the volumes stay in the library.
- **Duplicates: apply to a chosen few series:** tick the series cards you want and use **Apply to selected (N series, M
  files)** - next to the per-series Apply and Apply (all).
- Fixed: the "series' group" tag no longer goes to a copy whose group appears nowhere else in the folder (the copy itself
  was counted as evidence for its own group).
- Clearer buttons in the Download tab's In progress list: **Reload list** (shows the latest saved state; asks neither
  qBittorrent nor MangaPixer) and **Check qBittorrent now** (was "Refresh" and "Check now").

## [2026.10.8] - 2026-10-09

The seventh MangaList release (testing build): new roots pair with their MangaPixer library on their first scan, Settings > Library shows each root's pairing, Sync now on the MangaPixer card, and a Windows lock-file fix.

- **A new root pairs with its MangaPixer library on its first scan:** it used to stay unpaired until the next "Sync now" or
  the nightly sync. Every scan now re-pairs automatically paired roots from the libraries already synced (no network;
  your manual pairings stay), which also keeps the matched counts current.
- **Settings > Library shows each root's MangaPixer library:** e.g. "MangaPixer: Other › M/Manga · 12 of 12 series" for
  a root inside a library, or why it has none ("not paired yet", "no library matched these folders", "not paired (your
  choice)").
- **Sync now on the MangaPixer card** in Settings > Connected services, next to Test and Edit (it was only inside
  Edit).
- **Windows:** a library's lock file that is in use for a moment (another reader, or the heartbeat replacing it) is
  read again instead of being taken for "free" - the app holding the lock could think it had lost it mid-operation.

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

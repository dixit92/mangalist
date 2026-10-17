# Changelog

Versions are calendar versions `YEAR.MONTH.N`: the release's year and month plus a counter that
restarts each month (`2026.9.0`, `2026.9.1`, `2026.10.0`). To release: rename `[Unreleased]` to
`[YEAR.MONTH.N] - YYYY-MM-DD`, start a new empty `[Unreleased]`, commit, then tag `vYEAR.MONTH.N` and
push the tag. CI refuses a tag without its section here and uses the section as the release notes.

## [Unreleased]

## [2026.10.43] - 2026-10-17

The forty-second MangaList release (testing build): the Rename window's file menu, faster rescans, MangaPixer kept in step after a rename, and Unbundled chapters after the newest volume out.

- **Right-click a file in the Rename window:** Open folder (also on double-click), Delete this file… (for a stray file such as a hiatus notice - asked first, deleted for good, only inside a library and only if it has not changed), and Review duplicates of this series….
- **One folder rescanned, not the library.** Answering Chapters or Volumes for a series in the Rename window (or deleting a file there) reads only that series' folder again - seconds instead of the whole library.
- **MangaPixer follows a rename.** A scan MangaPixer holds back (its five-minute cooldown) is asked again as soon as it may be, not at the next hourly pass; and once MangaPixer has scanned, MangaList re-reads it, so renamed series stop showing "needs review" a few minutes after a rename instead of after the 12-hourly sync.
- **No freeze after a large rename.** The window no longer stops for a few seconds after renaming thousands of files.
- **Unbundled chapters start after the newest volume out.** The chapters after the volumes start after the newest volume you hold or lack while it is out in English (a volume you lack brings its chapters; a volume you hold the chapters of stays an upgrade). An empty folder with Unbundled on wants them too. Where the volume list does not say where that volume ends, "First chapter to get" asks about that volume.

## [2026.10.42] - 2026-10-17

The forty-first MangaList release (testing build): a finished unlicensed series whose every volume you hold is Complete.

- **No "Vol. 9+" past a finished series' final volume.** When the original edition is finished and you hold every one of its volumes, an unlicensed series is Complete, even where MangaDex's volume list stops short (its translation stopped partway through the last volume). A series still ongoing in the original keeps the open-ended gap, and a volume you lack is still a gap.

## [2026.10.41] - 2026-10-17

The fortieth MangaList release (testing build): chapter 0 the way MangaPixer counts it, and MangaDex's readable chapters as a chapter target.

- **Chapter 0 counts only when you hold it** (as in MangaPixer 1.43.2). MangaDex often lists a prologue as chapter 0; a volume listed as chapters 0-6 now counts as held - and as an upgrade - when you hold chapters 1-6.
- **MangaDex's readable chapters.** Where MangaPixer exports the chapters MangaDex can actually serve (taken-down ones left out), their latest one joins every series' chapter target, so new chapters MangaUpdates has not recorded yet show as missing - never a gap for a chapter MangaDex took down. A finished series' known total caps it, so a Complete series stays Complete. The readable list fills in as MangaPixer re-reads each series.

## [2026.10.40] - 2026-10-16

The thirty-ninth MangaList release (testing build): official English titles in folder names, an unmounted library left alone, Found clearing itself, spinners while things load, and the List's layout defects fixed. Database schema 9.

- **Official English titles.** The English part of a folder name ("<romaji> [<English>]") is the series' official English title from AniList (found through MangaPixer or MangaDex, kept in the database so scans never wait for it), in the form you already use: a repeated title is left out ("Noragami [Stray God]"), punctuation is plain, a subtitle's colon reads " - ", and your own English stays when AniList's is only the romaji. No bracket when the name would repeat itself, or where a file's path would pass 255 characters. The new names wait for your Rename like every folder rename.
- **An unmounted library is left alone.** A library that had series and is suddenly empty (its share not mounted) is treated as offline - "is the share mounted? Nothing was changed" - instead of marking every series missing (one library would have lost 304 series and 8136 files to "missing").
- **Found clears itself.** A finding whose gap was filled another way leaves Found after every scan, and at once when Change source finds nothing missing.
- **Unbundled chapters:** a volume not out in English becomes an upgrade (its chapters are got as chapters, the volume replaces them later); an unlicensed series whose newest volume MangaPixer does not map counts the original edition's volumes.
- **Spinners.** A series whose state is still being worked out shows "Checking…" with a spinner; the Rename pending, Duplicates and To get counts spin while they run.
- **Looks.** The Wanted badges are orchid, apart from the accent; the details panel fits its width (the close button is no longer cut off); the State column shows whole flags ("+2") instead of "Up..."; the matched title shows only when it differs from the folder name; at 1200 pixels every column stays in view; a first-run screen with one button.
- **The clean-up of finished download records** is weekly by default on a new install.

## [2026.10.39] - 2026-10-16

The thirty-eighth MangaList release (testing build): shorter names - volume files without the series title and official short folder names - and two rare freezes fixed.

- **Volume files are named "Vol. 001 [group].cbz".** The series' full "<romaji> [<English>]" name in every volume file made paths of about 330 characters, past what Windows tools take (260). The folder carries the name; volume files carry none, like chapter files: `Vol. 001 [group].cbz`, `Vol. 012.5`, `Vol. 005 + Extras [group].cbz`. Old names are still read. These renames wait for your Rename (marked "new volume name format"), like folder renames; new downloads are named this way from the start.
- **Long titles use an official short name.** A romaji title over 50 characters becomes an official short name the series goes by (an alternative title such as "Kaguya-sama", "RoshiDere", "DanMachi Sword Oratoria"), the way animefetch names folders; without one, the full title stays - nothing is cut.
- **Renames never lose part of a name.** A volume's group and extras note are kept; a name the scheme cannot keep whole (an edition, a volume title, a second group) is left alone, with the reason.
- **Fixed: the window could freeze for good right after a scan** - two rare deadlocks in background jobs (a subprocess count finishing very quickly, and a background worker being cleaned up while the window connected a signal).

## [2026.10.38] - 2026-10-15

The thirty-seventh MangaList release (testing build): Add series finds the English title and gets the first volumes, and unlicensed volume series follow Unbundled chapters.

- **Add series names the folder with the official English title.** A series picked from a title search had no English title; MangaList now finds MangaDex's record of that MangaUpdates series and takes AniList's English title through it - the licensed one ("Mistress Kanan is Devilishly Easy", not a fan title MangaDex also lists). With several English titles and no AniList answer, they are all offered and none is the default.
- **"Find releases now" after adding finds releases.** It searched nothing: the new series' state is worked out in the background after the scan, and the find ran before it. It now waits for it.
- **A series you confirmed is searched on nyaa.** Only series matched in MangaPixer could be searched, and MangaPixer never lists an empty folder - so a series just added could not be searched at all. A MangaUpdates match confirmed in MangaList (Add series, Fix MangaUpdates match) is now enough; an automatic match is not.
- **Unlicensed volume series follow Unbundled chapters.** A series not licensed in English whose folder holds only volumes wanted its later chapters even with Unbundled chapters off. Now it wants them only with the setting on; off, the chapters out past your volumes are missing volumes - the original edition's volumes, and an open-ended "Vol. 26+" after the last known one - never Up to date. On the owner's library 5 series move from Missing chapters to Missing volumes.

## [2026.10.37] - 2026-10-15

The thirty-sixth MangaList release (testing build): a chapter source that is behind is said, not skipped in silence.

- **A source behind your missing chapters waits under Found.** When every missing chapter of a series is newer than its source's newest (the source stopped, or has not caught up), the find pass skipped the series without a word - an Auto series never got its chapters and nothing said why. It now waits under Found with Change source…: "MangaDex (EN) has nothing past ch. 49; ch. 50-62 are not there yet - Change source?". Skip hides it until the source has a newer chapter.

## [2026.10.36] - 2026-10-15

The thirty-fifth MangaList release (testing build): the cycle planned from the owner's answers - folder names `<romaji> [<English>]`, Scheduled tasks, Unbundled chapters and comics extras level with MangaPixer, the Download tab polished, and the window no longer freezes during a scan.

- **Folder names `<romaji> [<English>]`.** Series folders follow animefetch's form (the English part left out when there is none or it repeats the romaji). Add series names a new folder so; the Renamer lists a folder not named so as a rename candidate and renames it as one undoable step, keeping the series. Volume files take the same series title. Folder renames, and file renames that change only the series title, wait for your Rename - the automatic pass never does them - so nothing changes on its own; other renames stay automatic.
- **Add series** offers Find releases now / Open in Download right after adding, names the folder by the scheme, and hides doujinshi and artbooks from title searches (a box shows them).
- **Scheduled tasks.** Settings > Scheduled tasks lists what runs on its own - the MangaPixer sync, the full re-read (every 6 h, 12 h or daily), checking downloads, the find pass and the new clean-up of finished download records (off, daily, weekly or monthly; "Clear finished now" too) - each with its last and next run and Run now. The clean-up clears only removed or cancelled records older than the period; nothing in progress, seeding, failed or still needed is touched, and a cleared release is never sent again.
- **Library Empty.** A library whose folder is now empty says "Library Empty" instead of "Scan failed".
- **Unbundled chapters.** Where MangaPixer's volume list does not map your newest volume, turning it on asks once for the first chapter to get (with a suggestion; change it later from the row menu). With it on, the chapters of a volume not out in English yet are wanted too. A filter under More filters.
- **Comics extras as MangaPixer reads them.** `Annual 2`, `Special #1`, `One-Shot 1`, `FCBD` are numbered extras: they never fill a missing chapter.
- **Found.** A series header's mode chip shows the series' own mode exactly; a series with no gaps left shows its name; Enter sends and Delete skips the selected finding.
- **In progress.** Failed rows (chapter downloads too) have Dismiss.
- **Release panel.** A queued send can be cancelled; a declared edition's volumes can be searched on nyaa by hand.
- **Mark Behind as up to date** takes the series out of To get.
- **Move to library.** The move is tried between shares too (you are told to move it yourself only if the system refuses); choose a folder inside the target library; refused while replaced chapters are in the holding folder.
- **Looks.** Dark's accent is MangaPixer's violet; "Not tracked" has its own colour.
- **A window that does not freeze.** Series states are worked out in the background and arrive in one step: on a library of 692 series the longest freeze during a scan went from 1.1 s to under 0.1 s, a library's first visit from up to 1 s to 0.15 s, sorting from 0.5 s to 0.05 s, and the Rename-pending and duplicate counts run in a separate process so the window stays smooth while they run. In the container a watchdog logs a warning whenever the window is blocked for more than a quarter of a second.

## [2026.10.35] - 2026-10-15

The thirty-fourth MangaList release (testing build): move a series to another library from its menu, and sends from the release panel are queued instead of waited for.

- **Move to library.** The List's row menu (one or several series) and the To get row menu have Move to library ▸ with your other libraries. One confirmation (Cancel is the default) names the series, the folder they go to and what changes there: series on "Use the default" take that library's automatic-download default, and its "Rename automatically" applies. Each series folder moves as a whole - one rename, nothing copied or deleted - and keeps its MangaUpdates link, settings, examined mark and download history; both folders are rescanned at once. Undo last move is in the same menu. A move is refused, with the reason, while a download of the series is not filed yet, while replaced chapters wait for your answer, when the other library already has a folder of that name, or when the two libraries are on different drives or shares (move it with a file manager then; the next scan recognises it).
- **Sends are queued.** Send to qBittorrent in the release panel while another send runs queues the release ("Queued to send (1st)") instead of waiting: one send at a time, in the order clicked, each exactly as chosen at the click. The status line shows the series on screen, not another series' "Sending..."; a failed send does not stop the ones after it.

## [2026.10.34] - 2026-10-14

The thirty-third MangaList release (testing build): a faster window and Found as one list, split chapters you hold are no longer asked for again, and the holding folder empties in one go.

- **Found is one list.** The findings are one list grouped by series instead of a card each: a series' header shows how many wait, their size and its automatic-download mode (a chip that changes it) and folds the series away; each row shows what it fills, the release and source, why it waits, its state (Waiting, Sending..., Queued, Change source?) and its size, with Send, Skip and the "..." menu at the right. A new box filters the series and releases. Everything the cards did is still there; the "..." menu also offers Change source... for a series with nothing to send.
- **A faster window.** Measured on a library of 692 series: a key typed in the List's filter takes about 0.02 s instead of up to 1 s (the filter looks at the title, the other title and the kind, a moment after the last key); Found refreshes in a few milliseconds when nothing changed instead of about 1 s; Light / Dark switches in under 1 s instead of about 7 s; switching back to a library takes 0.2 s instead of 1.5 s; the longest freeze during a scan is about 1 s instead of almost 5 s; the window opens in about 1.6 s instead of 3.6 s.
- **Split chapters and extras you hold are not missing.** A folder holding chapter parts next to extras (`6.1`, `6.2`, `6.5` "Extras", `6.6` "Bonus") read as missing 6.3, 6.4 and 6.5, so the find pass asked the source for chapters the folder holds and the filing refused them ("chapter 6.5 is already in the folder"). Now `.5` and the numbers after it continue a split only after `.4`, and a chapter the folder holds is never missing. Four series of the owner's library go to Up to date.
- **Empty all.** Replaced chapters' Review offers Empty all... when two or more series are in the holding folder: one confirmation, and each series is still emptied only while the volumes that replaced its chapters are in the library.

## [2026.10.33] - 2026-10-14

The thirty-second MangaList release (testing build): the Download tab's In progress list refreshes again after a series is forgotten.

- **In progress kept refreshing after a forgotten series.** Forgetting a series keeps its download records, without a series. The Download tab's In progress list then failed on every refresh (once a minute) and kept showing what it showed before; it now refreshes, and such a record reads "(series no longer in the library)". The downloads themselves were never affected.

## [2026.10.32] - 2026-10-14

The thirty-first MangaList release (testing build): dark mode, the same series actions on every screen, a library's own automatic-download mode, chapter files inside held volumes found however the volumes arrived, and Found clears findings whose gap was filled. Database schema 8.

- **Dark mode.** Settings > Appearance: Light, Dark or Follow the system. Dark takes MangaPixer's dark theme so the apps look like one family; Light is unchanged. The switch applies at once to every open window. In the container, the base image's `DARK_MODE=1` also darkens the parts MangaList does not draw (dialog frames, the file manager, the web page's bar) after a restart.
- **A library's automatic-download mode.** Settings > Library > Edit > Automatic downloads: Off, Notify, Auto or the default. A series without its own mode follows its library's, then the default - new series in that library included; everywhere a mode is inherited, it says whose default it is ("Off (the library's default)"). Schema 8 adds the column; nothing else in the database changes.
- **Series actions everywhere.** The To get list's row menu (also on the checked rows) gets the automatic-download mode, Unbundled chapters, Mark Behind as up to date, Show in List, Open folder, Copy title, the MangaUpdates / MangaPixer pages, Fix MangaUpdates match…, Check for new chapters… and Change source…; a Found card's "⋯" menu: Show in To get (its series' other releases), Show in List, Open folder, the mode, Skip all; the series panels' headers: Show in List, Open folder, the mode; the List's details panel: the mode as a picker and Unbundled as a switch. A change made on one screen shows on the others.
- **Chapter files inside held volumes.** "Set aside chapters its volumes hold…" uses MangaPixer's volume list as well as its estimates, so chapter files beside volumes that arrived outside MangaList's downloads can be set aside; such a series reads "Needs attention: Chapter files inside volumes you hold" until they are. Nothing moves by itself.
- **Found clears filled gaps.** A waiting finding whose numbers are no longer missing (chapters sent by hand, another release) leaves Found after the next filing instead of waiting for the next find pass.
- **The README** no longer shows a screenshot.

## [2026.10.31] - 2026-10-13

The thirtieth MangaList release (testing build): a chapter pack is never taken for volumes, chapters a source splits into parts (62.1 + 62.2) are found and sent, and titled chapter files after the series' English title are read.

- **Bare-number releases are not volumes for certain.** A nyaa release whose numbers carry no volume mark (`<Series> 001-051 (2024) (Digital)`) was read as volumes 1-51: the find pass offered it for a missing volume as certain, and the files were read as volumes too, so chapter 3 (`<Series> 003 (2024) ...cbz`) was downloaded and filed as volume 3. Such a release now ranks below any release that states its volumes, says "its numbers carry no volume mark, so they may be chapters", is never sent by itself, and its files are not read as volumes because of it.
- **Chapter packs are never sent for volumes.** A pack whose files are all chapter files is passed over by the find pass and refused by Send ("its files are chapters, not volumes") before the torrent starts.
- **Split chapters from a source, as MangaPixer counts them.** A source that numbers a chapter's parts `62.1`, `62.2` offered them as extras and called chapter 62 "not on the source", so it stayed missing; the parts now stand in for the missing chapter (counted as missing, picked, sent by the find pass - "part of ch 62"), and the parts of chapters you hold are no longer listed as extras. Real extras (`71.5`) stay extras.
- **Titled chapter files after the series' other title are read.** `<English title> 001 <chapter title>.cbz` in a folder named by the romaji title was left alone, so its "volumes or chapters?" answer could not apply and the Renamer could not rename it; the title every file in the folder starts with (before a number that changes) now counts as the series' title. Across the owner's library this reads 23 files of one series, nothing else.

## [2026.10.30] - 2026-10-13

The twenty-ninth MangaList release (testing build): the cycle "MangaPixer 1.42 parity and MangaDex data" - the parser and matcher level with MangaPixer 1.42.0, MangaDex data and MangaPixer's declared editions / tracking off, Add a series by link, Unbundled Chapters, and selective scanning of changed folders.

- **Parser and matcher level with MangaPixer 1.42.0.** A number the series title ends with is never read as the unit (`<Title 100> v01` is volume 1, not chapter 100), and a number every name in a folder shares belongs to the title (the series head); `[v2]` / `(v3)` is a release revision, not a volume; comics names are read (`Tome`, `Tomo`, `Band`, `Deel`, `Album`, `Livre`, `T03` = volumes; `Annual`, `FCBD`, `Special #1`, `One-Shot` = extras); watermarks are a class, not a list; the matcher takes MangaPixer 1.34.0-1.42.0's name rules (index-prefixed chapters, dated doujin names, an author's bracketed alias, three decimals, placeholder tags). No reading changed across the owner's library.
- **MangaDex data through the MangaPixer sync.** A series whose chapter count nothing else gives ("Can't tell") takes MangaDex's highest whole English chapter from MangaPixer; MangaDex never lowers a target and is not used where MangaUpdates or the English publisher tells (its count includes chapters MangaDex has taken down). MangaPixer's MangaDex series records (series MangaUpdates does not list) are accepted.
- **MangaPixer's declared edition and "tracking off".** A folder whose edition is declared in MangaPixer counts that edition's volumes (no upgrades; "Missing volumes · Master edition"); a series with tracking off reads "Not tracked" and leaves To get and the find pass.
- **Add a series by link.** "Add series…" in the top bar: paste a MangaUpdates or MangaDex link (or type a title), pick the library and the folder name; MangaList makes the empty folder, records the MangaUpdates match and the MangaDex chapter source, and scans it.
- **Unbundled Chapters.** A row-menu tick turns a volume series into volumes + the chapters after its last volume: those chapters are Missing chapters (found and sent like any chapter series) while the volume side stays as it was.
- **Selective scanning.** Actions that know what changed (a filing, replaced chapters, duplicate deletes, Set aside, Exclude, Add a series, the Renamer) scan only those folders instead of whole libraries; unchanged archive rows are no longer rewritten; the container's job records library by library under a scan lock it shares with the window, so "database is locked" no longer happens; Rename pending and the Renamer's automatic pass look only at what changed. A filing's after-step in the container goes from about 95 s to under a second; the periodic rescan from about 105 s to about 30 s. The periodic rescan and Rescan still read everything.
- **Fixed:** the chapter arrivals, image-volume folders and the duplicate finder read names with their folder's title too (a title ending in a number could pass for a chapter).

## [2026.10.29] - 2026-10-13

The twenty-eighth MangaList release (testing build): Empty now works after the automatic rename, and the Found view gets Change source, each series' automatic-download mode, queued sends and reasons that say which case a source problem is.

- **Empty now follows renamed volumes.** Replaced chapters' Empty now refused with "<volume> is no longer in the library" once the automatic rename had renamed the volumes that replaced a batch's chapters: the check looked for them only under the names they had when the chapters were set aside. It now follows each volume through the renames MangaList recorded and checks it is the same file (same size, inside a library root). The same holds before a pending batch is moved or its confirmed delete runs.
- **Change source… in Found.** A chapter finding with nothing to send - its source is gone or lacks the chapters, or it has no source yet - shows Change source… instead of Send: it opens the series in Missing chapters, where your other sources are listed to pick from. Send all leaves such findings out.
- **The series' mode in Found.** Showing one series, the Found view has its Automatic downloads mode (Off / Notify / Auto / the default), changed in place; on Auto, what waits goes at the next scheduled find pass or Find now (a bulk find sends nothing by itself).
- **Queued sends.** Send while another send runs queues the finding ("Queued") instead of waiting; the queue runs in order, the status line counts what is left, and Skip and Change source stay usable (skipping a queued finding takes it off the queue).
- **"Change source?" says which case it is.** Instead of "not on <source> any more": the source cannot open the series; taken down on MangaDex (its chapters there are marked unavailable); MangaDex has never had chapters of the series; the source lists no chapters; or the source lacks the missing chapters (and which it has). MangaDex's cases use MangaPixer's MangaDex chapter count. Waiting findings take the new wording at the next find pass.

## [2026.10.28] - 2026-10-12

The twenty-seventh MangaList release (testing build): Open folder works in the Docker image - it opens a file manager in the web GUI.

- **Open folder in the container.** The List's "Open folder in Explorer" (and double-clicking a row) worked only on Windows; in the Docker image, and on Linux and macOS, it did nothing. The other Open folder buttons (Duplicates, log viewer, Settings > Logging) did nothing in the container either. The image now includes the PCManFM file manager, which opens as a window in the web GUI on the series' folder (File > Close returns to MangaList). Off Windows the menu item reads "Open folder". Where no file manager opens, the folder path is copied and the status bar says so.
- **Deleting in the container's file manager skips the trash**, after a confirmation: a trash for the library would be a `.Trash-99` folder at the top of the `/data` mount, which on Unraid is a new share. PCManFM's Edit > Preferences (kept in `/config`) can turn the trash back on.

## [2026.10.27] - 2026-10-12

The twenty-sixth MangaList release (testing build): set aside the chapter files that held volumes contain, by MangaPixer's estimated volume ranges.

- **Set aside chapters its volumes hold** (List row menu). MangaPixer 1.42.0 estimates the chapter ranges of volumes its volume list does not map. For a series holding such volumes beside their chapter files, MangaList lists the chapter files inside each held volume's estimated range (per volume: chapters, files, size) and, after a yes, moves them to the holding folder as one batch - restorable from the Download tab, never deleted outright; the volumes are not touched.
- **Estimates stay suggestions.** MangaPixer's estimated ranges are kept apart from its volume list: states, the volume in chapter file names and the upgrade after filing act only on mapped ranges, as before.

## [2026.10.26] - 2026-10-12

The twenty-fifth MangaList release (testing build): torrents whose filed volumes were renamed to the scheme are removed at their seed goal again.

- **Remove Completed follows renames.** A torrent stopped at its seed goal stayed in qBittorrent ("not removed: ... is no longer in the library") once the automatic rename had renamed its filed volumes: the check looked for them only under their filed names. It now follows each filed file through the renames MangaList recorded and checks it is the same file (same size; for a hard link, the same file on disk). A filed file that is really gone still keeps the torrent.

## [2026.10.25] - 2026-10-12

The twenty-fourth MangaList release (testing build): libraries kept in the naming scheme get their downloads under the scheme's names, and MangaList offers to keep a library in the scheme after its first rename.

- **Downloads filed under the scheme's names.** In a library set to "Rename automatically" whose conversion was started from the Renamer window, a downloaded volume is filed straight under the scheme's name (`<Series> - Vol. 007 [group].cbz`) instead of its release name - still a hard link, so seeding is unaffected. A release name the scheme cannot read with certainty keeps its own name; other libraries keep release names as before.
- **Asked once after a library's first rename.** When a library-wide rename from the Renamer window finishes, each library it renamed that is not set to "Rename automatically" and was never asked gets the question: Rename automatically / Not now. The answer is remembered either way (Settings > Library changes it later).

## [2026.10.24] - 2026-10-11

The twenty-third MangaList release (testing build): series licensed only as app chapters are compared with the original volume list, the To get list sorts by how much is missing, and Check for new chapters works for volume series and from the Download tab.

- **No English volume list? The original one.** A series licensed in English only as chapters in an app (no English volumes, dates or count) whose folder holds volumes was "Can't tell". It is now compared with the original edition's volume list: a volume counts as out once its last chapter is out in English (the English chapter count or the latest scanlated chapter) - not held, it is a Missing volume (nyaa often has the English chapters compiled into those volumes); its chapters all held, an Upgrade. Volumes beyond the list are fine; volume 0 and series with an English omnibus edition are not compared this way.
- **Sort the To get list**: a Sort box next to Filter - Title, Most missing first, Fewest missing first (volumes, or chapters under Missing chapters); remembered.
- **Check for new chapters for volume series, and from the Download tab.** A series held as volumes counts the last chapter of its newest volume (from the volume list) as held and lists the chapters after it. To get rows have a right-click menu with Check for new chapters….
- **Two rescans at once no longer fail.** After a filing the GUI and the scheduled job rescan the same library; the slower one's recording failed ("UNIQUE constraint failed") and skipped its move detection for that scan. Nothing was lost, and it now completes.

## [2026.10.23] - 2026-10-11

The twenty-second MangaList release (testing build): set a file's chapter or volume in the Renamer, and the List's last column can no longer be squeezed out of sight.

- **Set chapter / volume in the Renamer.** A file whose name cannot be read (cut off after "Ch", a "vch106-110" bundle, an odd number like 17.1.5) was left alone with no way to name it. Right-click it in the Renamer > **Set chapter / volume…**: chapter or volume, a number or range, and for a chapter its volume (blank: the one the name states, else the volume list's). It is then renamed with the rest ("number set by you"), undoable like any batch; its title and group stay. **Forget the number I set** takes the answer back.
- **The List's last column keeps at least its header's width.** It used to get only what was left of the view - a sliver with its header cut and its edge out of reach. The table now scrolls sideways instead, by pixel rather than a column at a time; Last Modified starts at 150 px.

## [2026.10.22] - 2026-10-11

The twenty-first MangaList release (testing build): automatic downloads, a Found list, bulk find, a log viewer, and Check for new chapters.

- **Automatic downloads.** Each series has a mode: **Off** (never searched), **Notify** (searched; what is found waits for you) or **Auto** (searched and sent by itself within the guards). The default is Notify (Settings > Automation); change a series, or a selection, in the List's row menu under **Automatic downloads**. Only a series whose own mode differs from the default shows a small chip after its title.
- **The find pass** searches exactly the Download tab's "To get" list (missing volumes, upgrades, missing chapters), leaving out series on Off and those with a download in flight. It runs on its own schedule (`MANGALIST_DISPATCH_SCHEDULE`, default daily at 04:30) and on **Find now**. Volumes: nyaa, the best release first, then the best for what is still missing. Auto sends a release by itself only when it certainly fills a missing volume and is a single volume or a pack narrowed to the missing volumes; the download budget still applies. Chapters come only from the series' saved Suwayomi source, extras never; a saved source that no longer has the series says "Change source?".
- **Found** in the Download tab: everything the pass found that waits for you, with what it would get, its size and why it waits - **Send**, **Skip** (not offered again for the same release) or **Send all**. The tab's badge counts them.
- **Bulk find**: select series in the List (or a whole To get group) and find releases for all of them in one go; the results go to Found and nothing is sent by itself. The old "Find releases for selected" is now "Search selected on nyaa".
- **Log viewer**: Settings > Logging > **View logs** shows the app's and the scheduled jobs' logs live, with level, area and search filters, Pause / Follow, older lines and copy (read-only).
- **Check for new chapters.** In the List's row menu, for any series (also one that is Up to date): MangaList asks the series' saved Suwayomi source for every chapter after the newest one you have, and opens the chapters panel with them to send. MangaUpdates' latest chapter, which the gaps count up to, can trail the sites by days.
- **A source with no chapters falls through to the others.** Suwayomi reports "No chapters found" as an error (typical for a licensed series whose chapters MangaDex took down); the Download tab showed "Lookup failed" and stopped. MangaList now offers the series on your other sources, and a saved source that lists nothing says so. A lookup that fails for any reason now offers **Change source…** too.
- **An empty folder for a licensed series with no English volume** (a webtoon) lists every chapter up to the latest one under Missing chapters, instead of "awaiting release" with nothing to get; with no volume announced it reads **Wanted**.
- **Settings:** after Sync now, the MangaPixer badge no longer stays on "Syncing...". A text box's clear (x) button is centred.
- Database version 7 (automatic-download modes and findings); upgraded on first start.

## [2026.10.21] - 2026-10-11

The twentieth MangaList release (testing build): a volume starting at part 1 of a chapter covers that chapter, more bare-number names are read, and the Renamer shows when it is waiting for a rescan.

- **A volume whose chapters start at part 1 covers that chapter.** A complete series held as all its volumes showed one missing chapter when the volume list mapped its last volume from `11.1` (chapter 11 released in parts). A start at `N.5` (usually an extra) still does not count.
- **A zero-padded number and a chapter title right after the series' own title** (`<Series> 002 <chapter title>`, one space) is read as a bare number with that title, so the "volumes or chapters?" answer (in the List or the Renamer) applies; such names were unreadable. A short plain number (`<Series> 2 <words>`) is still not read.
- **The Renamer shows its wait.** After an answer or a rename the window read the library again with only a summary line changing ("the page was blank"); it now shows its busy bar with "Rescanning the library to show the new names…" and keeps the buttons off until the rescan is done.

## [2026.10.20] - 2026-10-11

The nineteenth MangaList release (testing build): chapters moved away can be sent again, four file-name readings fixed, "volumes or chapters?" answered in the Renamer, and a finished series held as all its volumes is Complete.

- **Chapters moved away can be sent again.** After 2026.10.19 the chapters panel let them be ticked, but Send still skipped them as "already sent": the send's own check counted every filed download. Both now count a filed chapter only while its file is in the series folder.
- **Chapter 100 onwards before its volume** (`<Series> 100 Vol 11 <title>`) is a chapter: before, only a zero-padded number counted, so chapter 100 onwards read as the volume and collided. Short or year-like numbers (`<Series> 2049 v01`) stay part of the title.
- **A chapter title ending in a number** (`<Series> 014.5  <title>, Part 2`) keeps its chapter number (it read as chapter 2).
- **A padded sub-number** (`Ch.014.005`) is chapter 14.5, as the sites that write it mean (`021.010` is 21.1).
- **Answer "volumes or chapters?" in the Renamer.** A series whose files are bare numbers left alone for that answer shows "are they: Chapters / Volumes" in the Renamer window; the answer is the same one the List asks for.
- **A site's watermark** at the end of a name ("Read Online", "Read Manga Online", "Read Free", "Online Free") is left out when the name is read.
- **Every volume of a finished series held is Complete**, also when the volume list maps only the first volumes and MangaUpdates counts chapters differently (a 4-koma counted in strips showed "Missing chapters 261-717"). With MangaUpdates saying the translation is complete, a series with no English edition is compared with its original volume count.

## [2026.10.19] - 2026-10-11

The eighteenth MangaList release (testing build): season and part subfolders belong to their series, and a filed chapter that left the library can be downloaded again.

- **Season and part subfolders belong to their series.** A series split into `Season 01`, `Season 02`, ... (or `Part 1`, `Volumes`, `Chapters`, `Extras`) is now ONE series holding all their files, named after its folder, as in MangaPixer. Before, each season was a series of its own, named "Season 01", missing the chapters the other seasons hold. Only two or more subfolders that are not units (two separate works) make a franchise of separate series.
- **Numbering that starts again in each season** (Season 2 starting at chapter 1 again) is not compared with one total: no chapters are reported missing that way, and the state says why ("Can't tell"). A chapter repeated at a season boundary is not a restart.
- **New chapters go to the latest season folder** (the one with the highest chapters); the chapters panel shows it and offers the others.
- **The old per-season series** carry over to their series on the next scan (their downloads with them) instead of showing in the Missing series list.
- **A filed chapter that left the library can be downloaded again.** A chapter moved away (for example to replace it with another source's copy) stayed "Filed" in the chapters panel and could not be ticked; it now counts as held only while its file is in the series folder.
- **Failed chapter lookups are logged** with Suwayomi's answer (they showed "Lookup failed" with nothing in the log).

## [2026.10.18] - 2026-10-10

The seventeenth MangaList release (testing build): rescans after duplicates and downloads cover only the libraries that changed, a rescan asked for during another is no longer lost, and the Renamer shows the new names after a rename.

- **Only the libraries that changed are rescanned** after deleting duplicates or after the Download tab files, restores or moves files. Before, discarding duplicates in one library rescanned every library.
- **The Renamer waits for its rescan.** Right after a rename, every renamed file showed under its old name as "left alone: the file is gone since the last scan". The window now says "Rescanning the library to show the new names…" and shows the preview once that rescan is done; Undo last batch stays off meanwhile.
- **A rescan asked for while another scan runs is no longer dropped**: it runs after the current scan. A rename's or a deletion's rescan could be lost this way.

## [2026.10.17] - 2026-10-10

The sixteenth MangaList release (testing build): the chapters panel offers a source's extra chapters and has Select all, MangaFire's "unofficial" label is not used as a group, and the Renamer hides series already in the scheme.

- **Extra chapters the source has are offered.** The chapters panel now also lists the source's decimal chapters (`71.5`) between the folder's first and last chapter that the folder does not hold. They are unticked, marked "extra - not counted as missing", and never count as missing. Chapters before the folder's first loose chapter are left out, since they may be inside a volume you hold.
- **Select all** in the chapters table's header ticks every chapter that can be sent, or none; it shows partly checked when only some are ticked.
- **MangaFire's "unofficial" is not a group.** MangaFire labels each chapter "official" or "unofficial" where other sources name the scanlation group. "unofficial" is now left out of file names, like `[no group]`; "official" stays. Files already named `[unofficial]` show as Rename pending.
- **The Renamer hides series already in the scheme.** After a library's rename, every series no longer shows "in the scheme"; "Show series already in the scheme (N)" lists them again. Series with files left alone or collisions stay listed.

## [2026.10.16] - 2026-10-10

The fifteenth MangaList release (testing build): choose where a series' chapters come from, Rescan follows the library you picked, empty scanlation-only folders can get their chapters, and the Renamer's preview reads as a preview.

- **Change where a series' chapters come from.** The chapters panel's "Change source…" lists the series on MangaDex (by the link MangaPixer has) and on your other Suwayomi sources (by title), leaving out the one in use; pick one with "Use this series" and its chapters can be ticked. The choice is remembered for the series and never falls back to MangaDex on its own; "Back to the chapters" keeps the current source. Before, the other sources were offered only when MangaDex had none of the missing chapters, so a series with a few chapters on MangaDex could not get the rest. "Change source…" replaces "Not this series?", which did not say what it did.
- **"Look up again" is now "Refresh from source"**: it asks Suwayomi for the source's chapter list as it is now.
- **Rescan follows the library picker.** With one library picked, the Rescan button scans only that library; with All libraries, or the arrow's "All libraries", it scans every one. The tooltip says which.
- **An empty folder of a series with no English edition lists its chapters.** "Wanted - scanlation only" now counts every chapter up to the latest known one as missing, so the Download tab's Missing chapters group offers them from Suwayomi.
- **The Renamer's preview reads as a preview**: its status column says "will be renamed" (it said "renamed" before anything had changed) and "keeps its name: name collision", under a "What happens" heading.

## [2026.10.15] - 2026-10-10

The fourteenth MangaList release (testing build): Suwayomi sources default to MangaDex in your languages with a language filter and a reset, the Download tab follows what a check filed, "Check downloads now", clearer Renamer rows and Settings fixes (number-box arrows, card padding, no personal examples).

- **The Download tab follows what was filed.** When a check files volumes or chapters, the library is rescanned, so the To get list and the panel drop what arrived (they kept showing it as missing until the next scan).
- **"Check qBittorrent now" is now "Check downloads now"**: it asks Suwayomi as well (files finished chapters), so the old name was misleading.
- **Suwayomi sources: only your languages, and only MangaDex (English) ticked to start.** Every MangaDex language used to arrive ticked. The list now shows the sources in your nyaa languages (English unless Raw is ticked) plus any you use; "Show all languages" shows the rest. "Reset to default" undoes a list ticked by hand.
- **Number boxes show their up / down arrows again** (Settings > Download sources > Download budget, Logging): the stylesheet had hidden them. Settings cards have a little more room inside their border.
- **Clearer Renamer window**: each series shows its title on its own line, the counts smaller and grey under it, and collisions as a red chip; long titles shorten instead of widening the list. A series with only collisions or files left alone says "nothing to rename".
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

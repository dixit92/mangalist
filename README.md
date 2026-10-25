# MangaList

<img src="packaging/icons/mangalist-512.png" alt="MangaList icon" width="96" align="right">

A PySide6 desktop tool that scans a "Manga Root" folder, classifies each manga subfolder as
**Volume-based**, **Chapter-based** or **Both** from its archive file names (`.cbz`, `.zip`, `.cbr`,
`.rar`, `.7z`, `.cb7`), and matches it to [MangaUpdates](https://www.mangaupdates.com) to show what
is licensed, finished or behind.

## Install

Download from the [Releases](../../releases) page:

| System | File |
| --- | --- |
| Windows x64 | `MangaList-vX.Y.Z-windows-x64-setup.exe` (per-user install, no admin rights; upgrades in place) or the portable `-windows-x64.zip` |
| macOS | `MangaList-vX.Y.Z-macos-arm64.dmg` (Apple silicon) or `-macos-x64.dmg` (Intel): drag MangaList to Applications |
| Linux x64 | `MangaList-vX.Y.Z-linux-x64.AppImage` (`chmod +x`, then run; glibc 2.35+) or the `-linux-x64.tar.gz` folder |

The builds are not code-signed. Check a download against `SHA256SUMS` (`sha256sum -c SHA256SUMS
--ignore-missing`, or `Get-FileHash <file>` on Windows); on first launch:

- **Windows** SmartScreen: **More info > Run anyway**.
- **macOS** Gatekeeper: right-click the app > **Open**, or on macOS 15+ **System Settings > Privacy &
  Security > Open Anyway**.

### MangaPixer as a source

If you run [MangaPixer](https://github.com/dixit92/mangapixer) 1.33.0 or later on the same library,
MangaList can use what MangaPixer already knows instead of looking everything up again: in MangaPixer,
**Administration > API tokens > Create token**, then in MangaList **Settings > Connected services > MangaPixer** enter the server
address and paste the token. With MangaPixer 1.36.0 or later, also tick **Request library scans** when you create the
token: after MangaList files downloaded volumes, it asks MangaPixer to rescan that library, so they show up there within
minutes (Settings > Automation can switch that off). MangaList maps each root to a MangaPixer library by its folder names and
syncs MangaPixer's links, volume lists and Completion answers (read-only - it never changes anything
in MangaPixer). Folders MangaPixer does not know still use MangaList's own matcher.

### Docker / Unraid

The image runs the same desktop app in the browser (on
[jlesage/baseimage-gui](https://github.com/jlesage/docker-baseimage-gui)), plus a headless runner that
rescans the library on a schedule. It is not published to a registry yet; build it from a checkout:

```sh
docker build -f packaging/docker/Dockerfile -t mangalist .
docker run -d --name mangalist -p 5800:5800 \
  -v /mnt/user/appdata/mangalist:/config -v /mnt/user/<library share>:/data mangalist
```

Then open `https://<host>:5800/`. The certificate is self-signed unless you put your own in
`/config/certs`. HTTPS is on because browsers only allow clipboard sync with the host over HTTPS.
`USER_ID` / `GROUP_ID` default to Unraid's `99` / `100`. The scheduled rescan is `MANGALIST_RESCAN_SCHEDULE`
(default `daily@03:30`, `off` to disable). Downloads stay off unless `MANGALIST_DOWNLOADS=1` (below).

The schedules (`MANGALIST_RESCAN_SCHEDULE`, `MANGALIST_MANGAPIXER_SYNC_SCHEDULE`, `MANGALIST_DOWNLOADS_SCHEDULE`,
`MANGALIST_DISPATCH_SCHEDULE`, `MANGALIST_CHAPTER_CHECK_SCHEDULE` for the check for new chapters, default `every 6h`,
`MANGALIST_EXTENSION_UPDATES_SCHEDULE` for the built-in extensions' update check, default `daily@05:15`,
and `MANGALIST_CLEAN_RECORDS_SCHEDULE` for the clean-up of finished download records,
default `weekly@sun 05:30`: records older than a week; `off` to stop) and the log level (`MANGALIST_LOG_LEVEL`: `error`, `warning`, `info` (default) or `debug`) are **first-run
defaults**: you change them in **Settings > Scheduled tasks** (one list: each task with its time, last run, next run
and **Run now**; the full re-read offers every 6 hours, every 12 hours or daily) and **Settings > Logging**, the
container's background runner picks the change
up within a minute (no restart), and a value set in Settings is used instead of the variable until you press "Use the
container's value". The log files are in the data folder's `logs` folder (`mangalist.log` for the window,
`headless.log` for the runner); Settings > Logging opens that folder or copies its path.

#### Volumes from nyaa (Unraid, opt-in)

With `MANGALIST_DOWNLOADS=1`, a series that MangaPixer has matched and that is licensed in English gets
a place in the **Download** tab ("Get the missing volumes" in the List tab's details and row menu takes you
there). MangaList searches nyaa's English-translated
literature, ranks the releases (the most missing volumes covered first - or, when MangaList cannot tell which
English volumes are out, the most volumes you do not have - Digital over scans), and **you pick** one. MangaList adds it to qBittorrent in its own category `mangalist`, and when it has finished,
hard-links the missing volumes into the series folder where that series already keeps its volumes (the
release's file names are kept). Like Sonarr / Radarr's "Remove Completed", once qBittorrent has stopped
the torrent at its seed goal and the library files are checked, MangaList asks qBittorrent to delete the
torrent and its downloaded copy (on by default; only ever in the `mangalist` category).

Set it up:

- **Mount the whole user-share tree at `/data`, the same way qBittorrent does** (e.g. binhex-qbittorrentvpn's
  `/mnt/user` -> `/data`): `-v /mnt/user:/data`, then add your roots as `/data/<share>/...`. A hard link only
  works inside one mount; with separate mounts for the library and the torrent folder every file is
  copied instead (double space until the torrent is removed). Both containers then see identical paths.
- In MangaList, **Settings > Connected services > qBittorrent**: the Web UI address, user name and password, and the download folder
  (default `/data/appdata/torrents/mangalist`, which must lie outside every library root). **Test
  connection** checks the login.
- In qBittorrent, give torrents a seed goal that **stops** them (e.g. ratio 2, action "Stop"); Remove
  Completed waits for that.
- `MANGALIST_DOWNLOADS_SCHEDULE` (default `every 1h`): how often finished downloads are filed and completed
  torrents removed.

A pack is downloaded **only for the volumes you are missing** by default (the release panel shows "Only the missing
volumes (n of N files)" - untick it for the whole pack; Settings > Download sources sets the default): MangaList reads
the release's `.torrent` first and tells qBittorrent to skip the other files. A torrent you stop yourself before its
seed goal is never removed by MangaList; right-click it in the Download tab's In progress list and **Remove now** does
it (after a confirmation).

**Upgrades (chapters to volumes).** A series held as chapters whose English volumes are out is listed under
**Upgrades** in the Download tab, with the same search and send. Once its volumes are filed, the chapter files they
replace are moved to a **holding folder** outside every library (Settings > Automation; default
`/data/appdata/mangalist/replaced`, restorable, emptied after 30 days) or, if you choose, **deleted after a
confirmation** listing every file. On Unraid, a holding folder on the same share as the library avoids moves across
disks (a move the share refuses shows as failed, with Try again; nothing is lost).

**Automatic downloads.** Every series has a mode - **Off**, **Notify** (the default) or **Auto** - set in the List's row
menu (**Automatic downloads**, also for a selection); the default is in Settings > Automation, and a series whose own
mode differs from it shows a small chip after its title. A scheduled **find pass** (`MANGALIST_DISPATCH_SCHEDULE`,
default `daily@04:30`; **Find now** runs it at once) searches the series in the Download tab's "To get" list. On Auto,
a release goes out by itself only when it certainly fills a missing volume and is a single volume or a pack narrowed to
the missing volumes, and chapters only from the series' saved Suwayomi source; everything else waits in the Download
tab's **Found** list with the reason, for you to **Send** or **Skip**. **Bulk find** (select series in the List) fills
the same list without sending anything.

**New chapters before MangaUpdates lists them.** The find pass only looks for the chapters a series is missing up to
MangaUpdates' latest chapter, which can lag the sites by days or weeks. A scheduled **check for new chapters**
(`MANGALIST_CHAPTER_CHECK_SCHEDULE`, default every 6 hours; Settings > Scheduled tasks changes it or turns it off) asks
the saved Suwayomi source of each ongoing chapter series that is not Off for the chapters numbered after the newest one
you have - the same as **Check for new chapters** in the List's row menu. Series on Auto get them sent; for series on
Notify they wait in **Found**. A series without a saved source (or a MangaDex id MangaPixer links), or with a download
on its way, is not asked. To keep the requests down, the check asks a source for the chapter list only, first syncs
with MangaPixer when it is connected, and skips a MangaDex series whose latest upload on MangaDex (from MangaPixer 1.45.0
or later when its own check for new chapters went through that library within the check's interval, or else one request
to MangaDex for every 100 series) has not changed since its last check - at least once a day it is asked anyway. The row menu's Check for new chapters always asks.

**Whole chapters or parts - one form per chapter.** When two groups release a series, one in whole chapters and one in
parts (`38` and `38.1`, `38.2`), and the source lists both, the checks and the find pass take each chapter in the form
the series holds: a series held in parts gets the parts, and the whole chapter only as a stand-in when the source's
parts do not start at `.1` (`38` with `38.2`: the whole stands in for the missing `38.1`); a series held in whole
chapters gets the whole chapter, never its parts. A part that turns up later (`38.1` after `38.2` was filed) is fetched
too - but never `38.1` while the files `38` and `38.2` are held: the file `38` is part 1 there. The chapters panel shows
the other form unticked, with the reason.

**Chapters without Suwayomi: the built-in extensions (since 2026.10.56).** Chapter downloads go through Suwayomi by
default; you can choose MangaList's own extension host instead: a small Java program shipped in the image
(`extension-host/`, about 210 MB with its Java 21 runtime) that runs the same Mihon / Tachiyomi extensions - MangaDex,
Weeb Central, MangaFire, ... - and answers MangaList the way Suwayomi does, so the chapters panel, Check for new
chapters, the find pass, filing and Retry / Cancel work unchanged. Every screen names the program in use (Suwayomi, or
"the extension host"). To use it:

- **Choose it in Settings**, in either of two places - they are one setting: **Connected services > Chapter downloads
  through** or **Download sources > Chapter sources** (the card that lists the sources chapters come from, with the same
  chooser). Suwayomi stays the default. Switching is refused while chapter downloads are still in progress with the
  other program. In the container the host starts by itself within a minute of choosing it (the `extension-host`
  service; its data - the installed extensions, the id database, the download queue and the WebView's Chromium bundle -
  lives in `/config/extension-host`) and stops when you switch back; the card says "Starting..." until it answers.
  `MANGALIST_EXTENSION_HOST=1` keeps it always running, `0` turns the service off.
- **Going back to Suwayomi** is the same chooser: Suwayomi's connection and its per-series choices were never touched,
  so nothing has to be set up again (what you picked on the host in the meantime is not copied back). The list of
  allowed sources is shared by both programs; editing it while one program is chosen keeps the sources only the other
  has installed.
- **The chapter download queue**: **Chapter queue...** under the In progress list shows the chosen program's queue - each
  chapter's state, progress and tries - with Retry and Cancel for the chapters it gave up on (what Suwayomi's web page
  showed; the host has no page of its own).
- **Extensions** (on the Built-in extensions card): every repository's extensions (keiyoushi's by default) with a
  **Repository** column and filter, **Install**, **Update** (and Update all) and **Uninstall** buttons for the selected
  one, and **Source settings** for a source's own settings (languages, image quality, ...). Install MangaDex first;
  which sources MangaList may use is set under Download sources, as before (source ids are the extensions' own, so the
  choice made for Suwayomi carries over). The same extension in two repositories is listed twice; one installed from a
  repository is updated only from that repository (to take it from the other one, uninstall it first).
- **Edit** on the same card: the **extension repositories** (as Suwayomi's "extension repos"): **Add repository** takes
  an `index.json` address - the host reads it first and refuses one that is not a Mihon extension repository or gives no
  signing key - and **Remove** drops the selected one (the extensions installed from it stay installed and get no more
  updates). Each repository's own signing key is enforced: an extension is installed or updated only when signed with
  the key of the repository it comes from, and a repository whose index later names another key is not trusted until
  removed and added again. Changes apply at once and are remembered for the host's next start
  (`MANGALIST_EXTENSION_REPO` - one or more addresses - only seeds the list). Also there: **Update extensions
  automatically** (off by default), FlareSolverr's address for sources behind Cloudflare (or
  `MANGALIST_FLARESOLVERR_URL`), the WebView switch (sources such as Comix need a browser engine) and, on a desktop, the
  address of a host you started yourself.
- **Extension updates**: a scheduled task (**Check for extension updates**, daily at 05:15 by default;
  `MANGALIST_EXTENSION_UPDATES_SCHEDULE`, or Settings > Scheduled tasks: weekly, daily, every 12 or 6 hours, or off)
  asks the host which installed extensions have a newer version in their repository. The Built-in extensions card says
  what it found ("1 extension update: Weeb Central 1.6.20 → 1.6.25", with **Check for updates**) and so does the Download
  tab ("1 extension update" with **Open Extensions**); the window also checks once at start when the last check is older
  than the schedule. With **Update extensions automatically** on, the task installs them one by one (each checked against
  its repository's key, as for Update; one that fails keeps the old version and is reported) and the notices say what
  was updated. An extension is never updated while one of its chapters is downloading: the host holds that source's
  queue, waits up to a minute for the running chapter, and otherwise leaves the update for the next check.

Each series' match on the host is kept apart from its Suwayomi match (the host numbers its manga itself); the scanlation
group you picked carries over.

- **Carry over from Suwayomi** (on the Built-in extensions card, while Suwayomi is set up and series have a source
  picked there): carries those picks over once, so no series needs its source picked again. It first checks and shows
  what it will do - the extensions to install in the host (from its repositories, each signature-checked as usual),
  Suwayomi's repositories the host lacks, how many series it will carry and which it cannot (the source's extension is
  not available, or Suwayomi no longer has the manga) - and does it on **Carry over**, with a progress bar and a report.
  Each series gets the same manga on the host (by its source and address), with the same kind of match (MangaDex id or
  confirmed). Suwayomi is only read and its own picks stay, so switching back still works; a series already set up on
  the host is never changed, and running it again carries only what is still missing. In the container:
  `python -m mangalist.services.extensions carry-over [--dry-run]` does the same with the stored connections and prints
  the report.

Downloads are an Unraid-container feature for now; the desktop builds do not offer them.

> **Local use only - do not expose it to the internet.** MangaList is a personal, single-user desktop
> app, not a server: the web GUI has **no login**, and whoever opens it can rename and move files in
> the library mounted at `/data`. Keep port 5800 on your home network and reach it from outside
> through a VPN or [Tailscale](https://tailscale.com/), never through a port forward or a public
> reverse proxy.

## Your data

Settings, the MangaUpdates cache and logs live per user, so upgrades and uninstalls keep them:
`%LOCALAPPDATA%\MangaList` (Windows), `~/Library/Application Support/MangaList` (macOS),
`~/.local/share/MangaList` (Linux). The portable zip keeps them in `data\` next to `MangaList.exe`.
Older versions kept `data\` next to the program; the first start copies it over once.
`MANGALIST_DATA_DIR` points it elsewhere.

## Run from source

```sh
pip install -r requirements-dev.txt
python -m mangalist          # --version, or --smoke-test for a headless start check
python -m pytest -q
```

The tests never touch the network. A few optional live checks against the real MangaUpdates API (public titles from
the golden set only) run with `MANGALIST_LIVE_MU=1 python -m pytest -q tests/golden/test_live_mangaupdates.py`.

Series folders are named `Romanized Title [English Title]` - MangaUpdates' title, then the official English title in
brackets (AniList's, found through MangaDex's links; else the folder's own English title), just `Romanized Title`
otherwise, like animefetch's show folders. The official titles are looked up in the background when the Renamer opens
and cached, so a scan never asks the network. When the path of a series' longest file would pass 255 characters, its
folder name leaves the English part out. Add series proposes that name; the Renamer lists a folder named differently as
Rename pending and renames it only when you press Rename.

## MangaUpdates matching

**Check MU** uses `mangalist/matcher/`, a Python port of the
[MangaPixer](https://github.com/dixit92/mangapixer) 1.31.1 matcher with the same rules and
thresholds. It first decides whether a folder is one work (a series, a series with `Volumes/` /
`Chapters/` / `Season N/` subfolders, or a one-shot) and matches only those. It searches a few title
variants (and a second results page on a tie), then scores candidates by title similarity (sequel
numbers count) and local evidence: the highest volume / chapter numbers against the record's
totals, years, the category folder, author names in brackets or after `by`, and related records
(a spin-off named only by the folder's subtitle always needs review). MangaPixer's cover comparison
and admin-declared types are not ported: MangaList has neither covers nor declarations.

The **MU Title** column shows the result:

- *auto*: a normal, unconfirmed match.
- *needs review*: orange, with the reasons in the tooltip.
- *unmatched* / *not one work*: nothing is linked. Use **Fix MangaUpdates match…** to pick one.

A confirmed match (✔) is never re-scored. The golden tests in `tests/golden` replay recorded public
MangaUpdates responses and check that the results equal MangaPixer's.

## Finding a series by its other titles or its creator

The List's filter box (and the Download tab's **To get** filter) looks in a series' title, English title and kind, and
also in every other title of its MangaUpdates record (MangaPixer's record when MangaPixer knows the folder, else
MangaList's own match), as MangaPixer's search does: type `delicious` and *Dungeon Meshi* is listed with **aka Delicious
in Dungeon** under its title. Other titles are matched from three letters, upper or lower case; a series whose other
titles are not known is still found by its own.

`creator:Urasawa` or `creator:"Naoki Urasawa"` lists the series of a writer or artist, also by the author's other names
(`creator:浦沢直樹`), and combines with words (`creator:Urasawa pluto`) and with more creators. A typed word counts when it
starts a word of the name, in any order and ignoring accents and punctuation (MangaPixer's name key). For a series
MangaPixer knows, the creators and their other names come from MangaPixer's export (`record.creators`; a MangaPixer
that does not export them yet gives those series none, and MangaList asks MangaUpdates nothing for them - they are
still found by their titles). A series MangaPixer does not know is read from MangaUpdates in the background when a
MangaUpdates lookup ends - the series' record, then each author's page for the other names, a request a second, kept for
180 days (`mangalist/creator_names.py`) - and found by creator once read.

## Several libraries

With more than one library (root), the List's **Library** picker shows one of them or all; the chips, the Duplicates
view and the Download tab follow it. **Rescan ▾** rescans a single library, and a rescan shows each library's series as
soon as it has been read. Settings > Library shows which MangaPixer library each root is paired with.

## Duplicates

The List tab's **Duplicates** chip shows series held in more than one folder (to look at; nothing changes) and
duplicate files: the same chapter or volume number in two files of one folder, by MangaPixer's rule. A section of
its own, **Whole chapters with parts**, with its own count (not in the Duplicates chip), lists a whole chapter held next
to its parts (`38` with `38.1`, `38.2`: the whole may be redundant; `38` with only `38.2` is a plain split - the file
`38` is part 1 - and is not listed). MangaList reads the pages of these files in the background (once per file; an
unchanged file is never read again) and says on each card what they show, with the evidence: the same pages (one form
can go - the form the series does not hold is marked Discard), a copy (`38.1` is `38` again), or the wrong chapter (the
file `14` holds chapter 13's pages). Different chapters that only look like parts (a side story numbered `81.1`,
`81.2`) are hidden, and **Dismiss** hides a card you judged fine until one of its files changes; **Show hidden** lists
them, with **Restore**. RAR / 7z archives are not read: their cards stay as before. Mark each
file **Keep** or **Discard** (by default the newest is kept), then **Apply**: MangaList lists every file it is
about to delete and deletes them only after you confirm. There is no holding folder - the files are gone - and at
least one copy of each number always stays. Each series also has its own **Apply for this series**, and right-clicking
a series in the List offers **Review duplicate files** for that series alone. **Open in MangaPixer** opens a series'
page there, to compare the copies in its reader. (In the container, which has no browser, links are copied to the
clipboard instead.)

## Build and release

CI (`.github/workflows`) runs the tests on Windows, macOS and Linux and builds every package
(PyInstaller via `MangaList.spec`, scripts in `packaging/`) for each push to `main` and each pull
request. Versions are calendar versions `YEAR.MONTH.N` (`2026.9.0`); each release has a section in
[CHANGELOG.md](CHANGELOG.md), which says how to cut one. Pushing a `vYEAR.MONTH.N` tag creates a
draft release with the packages, `SHA256SUMS` and that section as its notes. Build locally with
`python packaging/make_icon.py && pyinstaller MangaList.spec --clean --noconfirm` (`build.bat` on
Windows).

## Data sources & attribution

Not affiliated with, endorsed by, or sponsored by either service.

- **[MangaUpdates](https://www.mangaupdates.com)**: series matching, licensing and progress via the
  [MangaUpdates API](https://api.mangaupdates.com/), rate-limited (`REQUEST_DELAY` in
  `mangalist/mu_client.py`), with matches cached locally. The golden-test fixtures are recorded
  public MangaUpdates data (series data © MangaUpdates).
- **[AniList](https://anilist.co)**: supplementary volume / chapter counts via the
  [AniList GraphQL API](https://docs.anilist.co/).

## License

[MIT](LICENSE) for this project's code. Data from the services above belongs to its owners.

The app bundles the [IBM Plex](https://github.com/IBM/plex) Sans and Mono fonts (`mangalist/assets/fonts`),
Copyright © 2017 IBM Corp., under the [SIL Open Font License 1.1](mangalist/assets/fonts/OFL.txt).

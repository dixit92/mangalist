# MangaList

A PySide6 desktop tool that scans a "Manga Root" folder, classifies each manga subfolder as
**Volume-based**, **Chapter-based** or **Both** from its archive file names (`.cbz`, `.zip`, `.cbr`,
`.rar`, `.7z`, `.cb7`), and matches it to [MangaUpdates](https://www.mangaupdates.com) to show what
is licensed, finished or behind.

![Main window](docs/screenshot.png)

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
**Administration > API tokens > Create token**, then in MangaList **MangaPixer...** enter the server
address and paste the token. MangaList maps each root to a MangaPixer library by its folder names and
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

#### Volumes from nyaa (Unraid, opt-in)

With `MANGALIST_DOWNLOADS=1`, a series that MangaPixer has matched and that misses English volumes gets
**Find volumes on nyaa...** (Wanted panel and the row menu). MangaList searches nyaa's English-translated
literature, ranks the releases (the most missing volumes covered first, Digital over scans), and **you
pick** one. MangaList adds it to qBittorrent in its own category `mangalist`, and when it has finished,
hard-links the missing volumes into the series folder where that series already keeps its volumes (the
release's file names are kept). Like Sonarr / Radarr's "Remove Completed", once qBittorrent has stopped
the torrent at its seed goal and the library files are checked, MangaList asks qBittorrent to delete the
torrent and its downloaded copy (on by default; only ever in the `mangalist` category).

Set it up:

- **Mount the whole user-share tree at `/data`, the same way qBittorrent does** (e.g. binhex-qbittorrentvpn's
  `/mnt/user` -> `/data`): `-v /mnt/user:/data`, then add your roots as `/data/<share>/...`. A hard link only
  works inside one mount; with separate mounts for the library and the torrent folder every file is
  copied instead (double space until the torrent is removed). Both containers then see identical paths.
- In MangaList, **qBittorrent...**: the Web UI address, user name and password, and the save path
  (default `/data/appdata/torrents/mangalist`, which must lie outside every library root). **Test
  connection** checks the login.
- In qBittorrent, give torrents a seed goal that **stops** them (e.g. ratio 2, action "Stop"); Remove
  Completed waits for that.
- `MANGALIST_DOWNLOADS_SCHEDULE` (default `every 1h`): how often finished downloads are filed and completed
  torrents removed.

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

Folder names may be `Title`, `English Title` or `Romanized Title [English Title]`.

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

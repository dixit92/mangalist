# Manga List Classifier

A PySide6 desktop tool that scans a "Manga Root" folder and classifies each
manga subfolder as **Volume-based**, **Chapter-based**, or **Both** using
filename heuristics on `.cbz` / `.zip` / `.cbr` / `.rar` / `.7z` / `.cb7`
archives.

![Main window](docs/screenshot.png)

## Install

Download the package for your system from the [Releases](../../releases) page.
Every file is listed in `SHA256SUMS` next to the downloads (see
[Verify a download](#verify-a-download)).

| System | File | How |
| --- | --- | --- |
| Windows 10/11 x64 | `MangaList-vX.Y.Z-windows-x64-setup.exe` | Run it. Installs for your user only (no administrator rights) to `%LOCALAPPDATA%\Programs\MangaList`, with a Start-menu entry and an uninstaller (Settings > Apps). A newer setup upgrades in place and keeps your settings and cache. |
| Windows, no install | `MangaList-vX.Y.Z-windows-x64.zip` | Unzip anywhere (also a USB stick) and run `MangaList.exe`. The zip is *portable*: settings, cache and logs stay in `data\` and `logs\` next to the exe (delete the `portable` file to use the per-user folder instead). |
| macOS (Apple silicon) | `MangaList-vX.Y.Z-macos-arm64.dmg` | Open the disk image and drag **Manga List** to Applications. |
| macOS (Intel) | `MangaList-vX.Y.Z-macos-x64.dmg` | Same. |
| Linux x64 | `MangaList-vX.Y.Z-linux-x64.AppImage` | `chmod +x` it and run it. Needs glibc 2.35 or newer (Ubuntu 22.04, Debian 12, Fedora 36 and later) and the usual desktop libraries (X11 / Wayland, OpenGL, fontconfig). Without FUSE: `./MangaList-*.AppImage --appimage-extract-and-run`. |
| Linux x64, fallback | `MangaList-vX.Y.Z-linux-x64.tar.gz` | Unpack and run `./MangaList` inside the folder. A `.desktop` file and an icon are included. |

### First launch of an unsigned build

The packages are not code-signed yet, so the systems warn once:

- **Windows** (SmartScreen: "Windows protected your PC"): choose **More info > Run anyway**.
- **macOS** (Gatekeeper: "cannot be opened because the developer cannot be verified" or "is
  damaged"): in Finder, right-click (Control-click) **Manga List** in Applications and choose
  **Open**, then **Open** again. On macOS 15 and later, if there is no Open button: try to open it
  once, then go to **System Settings > Privacy & Security** and click **Open Anyway**. From a
  terminal the same is `xattr -dr com.apple.quarantine "/Applications/Manga List.app"`.
- **Linux**: nothing to confirm; the AppImage only needs the executable bit.

### Where your data lives

Settings (`config.json`), the MangaUpdates cache (`mu_cache.db`) and the logs are kept per user,
outside the program folder, so upgrades and uninstalls never touch them:

| System | Settings and cache | Logs |
| --- | --- | --- |
| Windows | `%LOCALAPPDATA%\MangaList` | `%LOCALAPPDATA%\MangaList\Logs` |
| macOS | `~/Library/Application Support/MangaList` | `~/Library/Logs/MangaList` |
| Linux | `~/.local/share/MangaList` | `~/.local/state/MangaList/log` |

- **Upgrading from an older version** (which kept `data\` next to `MangaList.exe` or in the source
  folder): on first start, the new version copies that `data\` folder into the per-user folder
  once and leaves the old folder as it was (`migrated-from.txt` records where it came from). If
  you install with the setup while your old data sits next to an old downloaded exe elsewhere,
  copy that old `data\` folder's contents to `%LOCALAPPDATA%\MangaList` before the first start.
- The portable Windows zip keeps using `data\` and `logs\` next to the exe.
- To use another folder, set the environment variable `MANGA_LIST_DATA_DIR` (logs go to its `logs`
  subfolder).

### Verify a download

```powershell
# Windows (PowerShell): compare with the line for this file in SHA256SUMS
Get-FileHash MangaList-vX.Y.Z-windows-x64-setup.exe -Algorithm SHA256
```

```sh
# macOS / Linux, in the folder with the downloads and SHA256SUMS
shasum -a 256 -c SHA256SUMS --ignore-missing      # macOS
sha256sum -c SHA256SUMS --ignore-missing          # Linux
```

## Install (from source)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Run

```powershell
python -m manga_list
```

Click **Choose Manga Root…**, pick the folder that contains your per-manga
subfolders, and the table will populate. Click any row to see sample filenames
and the heuristic hits behind the verdict in the right-hand pane.

## Folder name conventions recognized

Manga folder names may be in any of these forms:

- `<Romanized Title>` — e.g. `7-Nin no Nemuri Hime`
- `<English Title>` — e.g. `A Dating Sim of Life or Death`
- `<Romanized Title> [<English Title>]` — e.g. `Jitsu wa Ore, Saikyou deshita [Am I Actually the Strongest]`

Romanized text and raw JP/KR/CN UTF-8 are both accepted.

## Heuristics (summary)

For every archive file inside a manga folder (scanned to a depth of 3):

- `has_volume`  — matches `Vol`, `Vol.`, `Volume`, or bare `v01` style tokens
- `has_chapter` — matches `Ch`, `Ch.`, `Chapter`, `Chp`, or bare `c003` tokens
- A file with **both** tokens (e.g. `Vol. 1 Ch 3`) counts as a **chapter**.
- Files at depth 0 vs. inside a subfolder are tracked separately so the "volumes
  in parent + chapters in subfolder" pattern can be detected as **Both**.
- Median file size and total file count nudge ambiguous cases (chapters tend to
  be many small files; volumes tend to be fewer larger files).

The exact weights live as constants in `manga_list/classifier.py` and are easy
to tune.

## MangaUpdates matching

**Check MU** matches each folder to a MangaUpdates series with the stage-2
matcher in `manga_list/matcher/`, a Python port of the matcher in
[MangaPixer](https://github.com/dixit92/mangapixer) 1.26.0 (same rules and thresholds):

1. **What is the folder?** From names and counts alone, a folder is one work
   (a series, a series with `Volumes/` / `Chapters/` / `Season N/` subfolders,
   a one-shot) or not (a shelf of separate works, an artist folder, a unit
   subfolder). Only folders that are one work are matched automatically.
2. **Search.** Up to four title variants are searched in order: the folder
   name, its `[English Title]`, the part before a ` - Subtitle`, the title
   without a sequel number, and the title most archive names share. Searching
   stops at the first confident hit. Novels, doujinshi, artbooks and drama CDs
   are filtered out.
3. **Score.** Title similarity counts word order, partial words and sequel
   numbers (`Part 3` is not `Part 4`), and a subset is not a perfect match.
   Local evidence can lower a candidate or veto an automatic link: file counts
   vs published volumes / chapters, file years vs the start year, the category
   folder vs the record's origin (`Manhwa` vs a Japanese manga).
4. **Tier.** The **MU Title** column shows the result:
   - *auto*: title score ≥ 0.92, a clear lead over the runner-up and no conflict.
     Shown as a normal, unconfirmed match.
   - *needs review*: score ≥ 0.60 otherwise. Highlighted in orange; the tooltip
     gives the reasons.
   - *unmatched* or *not one work*: nothing is linked; the tooltip says why.
     Use **Fix MangaUpdates match…** to pick one yourself.

A confirmed match (✔) is yours and is never re-scored. Matches cached by
versions before this matcher keep their old score, marked as a legacy score;
**Check MU** re-matches them.

## Phase 2 (planned, not implemented)

- CSV / JSON export of the table
- Right-click "Open folder", "Open in explorer"
- Rename helpers (e.g. normalize `Vol.1` → `Vol. 01`)
- Batch actions

## Tests

```powershell
pip install -r requirements-dev.txt
python -m pytest -q
```

## Build packages

`MangaList.spec` builds a PyInstaller *onedir* app on every platform (a folder with the executable
and its libraries; on macOS also a windowed `MangaList.app`). Details, the package scripts and
where code signing plugs in: [docs/packaging.md](docs/packaging.md).

```sh
pip install -r requirements-dev.txt
python packaging/make_icon.py            # icons for the exe / installer / bundle
pyinstaller MangaList.spec --clean --noconfirm
dist/MangaList/MangaList --smoke-test    # starts, opens the window once, exits 0
```

On Windows, `build.bat` runs the same steps. `python -m manga_list --version` prints the version;
`--smoke-test` (with `QT_QPA_PLATFORM=offscreen` on a headless machine) is what CI runs against
every package.

## GitHub Actions

- **Tests** (`.github/workflows/test.yml`): pytest on Windows, macOS and Linux with Python 3.11 and
  3.12, plus 3.10 on Linux, for every push to `main` and every pull request. A second job runs
  the matcher, golden-set and data-location tests in an environment without PySide6, to keep them
  free of Qt and of any display.
- **Build packages** (`.github/workflows/build.yml`): for every push to `main`, every pull
  request and every `v*` tag, builds the Windows installer and portable zip, the macOS disk images
  (Apple silicon and Intel), and the Linux AppImage and tar.gz. Each package is smoke-tested (the
  Windows installer also through install, upgrade and uninstall) and uploaded as a workflow
  artifact, with a `SHA256SUMS` artifact. Only a `v*` tag also creates a **draft** GitHub
  release with all packages attached; you review and publish it.

### Cutting a release

The version is derived from the git tag, so there is nothing to bump by hand:

```sh
git tag v0.2.0
git push origin v0.2.0
```

CI stamps `manga_list/_version.py` with the tag (minus the leading `v`), builds every package,
and opens the draft release. Builds that are not tags report `0.0.0+<short-sha>`.

## Data sources & attribution

Series metadata is fetched from third-party APIs. This project is not affiliated
with, endorsed by, or sponsored by either service.

- **[MangaUpdates](https://www.mangaupdates.com)** — series matching, licensing
  status, and scanlation/publisher progress, via the
  [MangaUpdates API](https://api.mangaupdates.com/). Used in accordance with their
  Acceptable Use Policy: requests are rate-limited (`REQUEST_DELAY` in
  `manga_list/mu_client.py`) and matches are cached locally in your data folder
  (see [Where your data lives](#where-your-data-lives)).
- **[AniList](https://anilist.co)** — supplementary volume/chapter counts via the
  [AniList GraphQL API](https://docs.anilist.co/).

The matcher's golden-set tests replay recorded public MangaUpdates responses
(`tests/golden/fixtures`, series data © MangaUpdates); the tests never contact
the network.

### Fetching the MangaUpdates API spec

The OpenAPI spec is not vendored in this repository. Fetch the current version if
you need it while working on `manga_list/mu_client.py`:

```powershell
curl -o openapi.yaml https://api.mangaupdates.com/openapi.yaml
```

Only two endpoints are used: `POST /series/search` and `GET /series/{id}`.

## License

[MIT](LICENSE) — applies to this project's own source code. Data retrieved at
runtime from the services above remains the property of its respective owners.

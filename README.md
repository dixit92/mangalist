# Manga List Classifier

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
| macOS | `MangaList-vX.Y.Z-macos-arm64.dmg` (Apple silicon) or `-macos-x64.dmg` (Intel): drag Manga List to Applications |
| Linux x64 | `MangaList-vX.Y.Z-linux-x64.AppImage` (`chmod +x`, then run; glibc 2.35+) or the `-linux-x64.tar.gz` folder |

The builds are not code-signed. Check a download against `SHA256SUMS` (`sha256sum -c SHA256SUMS
--ignore-missing`, or `Get-FileHash <file>` on Windows); on first launch:

- **Windows** SmartScreen: **More info > Run anyway**.
- **macOS** Gatekeeper: right-click the app > **Open**, or on macOS 15+ **System Settings > Privacy &
  Security > Open Anyway**.

## Your data

Settings, the MangaUpdates cache and logs live per user, so upgrades and uninstalls keep them:
`%LOCALAPPDATA%\MangaList` (Windows), `~/Library/Application Support/MangaList` (macOS),
`~/.local/share/MangaList` (Linux). The portable zip keeps them in `data\` next to `MangaList.exe`.
Older versions kept `data\` next to the program; the first start copies it over once.
`MANGA_LIST_DATA_DIR` points it elsewhere.

## Run from source

```sh
pip install -r requirements-dev.txt
python -m manga_list          # --version, or --smoke-test for a headless start check
python -m pytest -q
```

Folder names may be `Title`, `English Title` or `Romanized Title [English Title]`.

## MangaUpdates matching

**Check MU** uses `manga_list/matcher/`, a Python port of the
[MangaPixer](https://github.com/dixit92/mangapixer) 1.26.1 matcher with the same rules and
thresholds. It first decides whether a folder is one work (a series, a series with `Volumes/` /
`Chapters/` / `Season N/` subfolders, or a one-shot) and matches only those. It searches a few title
variants, then scores candidates by title similarity (sequel numbers count) and local evidence: file
counts, years, the category folder, and author names in brackets.

The **MU Title** column shows the result:

- *auto*: a normal, unconfirmed match.
- *needs review*: orange, with the reasons in the tooltip.
- *unmatched* / *not one work*: nothing is linked. Use **Fix MangaUpdates match…** to pick one.

A confirmed match (✔) is never re-scored. The golden tests in `tests/golden` replay recorded public
MangaUpdates responses and check that the results equal MangaPixer's.

## Build and release

CI (`.github/workflows`) runs the tests on Windows, macOS and Linux and builds every package
(PyInstaller via `MangaList.spec`, scripts in `packaging/`) for each push to `main` and each pull
request. Pushing a `v*` tag also creates a draft release with the packages and `SHA256SUMS`; the
version comes from the tag. Build locally with `python packaging/make_icon.py && pyinstaller
MangaList.spec --clean --noconfirm` (`build.bat` on Windows).

## Data sources & attribution

Not affiliated with, endorsed by, or sponsored by either service.

- **[MangaUpdates](https://www.mangaupdates.com)**: series matching, licensing and progress via the
  [MangaUpdates API](https://api.mangaupdates.com/), rate-limited (`REQUEST_DELAY` in
  `manga_list/mu_client.py`), with matches cached locally. The golden-test fixtures are recorded
  public MangaUpdates data (series data © MangaUpdates).
- **[AniList](https://anilist.co)**: supplementary volume / chapter counts via the
  [AniList GraphQL API](https://docs.anilist.co/).

## License

[MIT](LICENSE) for this project's code. Data from the services above belongs to its owners.

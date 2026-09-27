# Packaging

How the desktop packages are built, and where code signing goes once there are certificates.
CI does all of this in `.github/workflows/build.yml`; the same steps work locally.

## Common steps (every platform)

```sh
pip install -r requirements-dev.txt
python packaging/stamp_version.py      # optional locally: writes manga_list/_version.py (CI: from the tag)
python packaging/make_icon.py          # build/icons/MangaList.{png,ico}, MangaList-256.png
pyinstaller MangaList.spec --clean --noconfirm
```

- **Version** (`packaging/stamp_version.py`): a `v1.2.3` tag gives `1.2.3`; any other build gives
  `0.0.0+<short sha>`. It also prints the numeric part (`1.2.3`, for installer and bundle
  metadata) and the package base name `MangaList-v<version>`.
- **Icons** (`packaging/make_icon.py`): the running app paints its icon in code
  (`render_app_icon` in `gui/main_window.py`); this script renders the same painter to files, so
  the exe, installer, bundle and AppImage use the same icon. Needs Pillow for the `.ico`.
- **PyInstaller** (`MangaList.spec`): onedir everywhere - `dist/MangaList/` with the executable
  and `_internal/`; on macOS also `dist/MangaList.app`. Onedir, not one-file: an installed
  one-file exe would unpack itself into a temp folder on every start (slow, and often flagged by
  antivirus software). UPX is off (it breaks Qt plugins and signing).
- **Smoke test**: `MangaList --smoke-test` builds the main window, runs the event loop once and
  exits 0. With `QT_QPA_PLATFORM=offscreen` it needs no display. CI runs it against the raw
  build and against every package. Use `MANGA_LIST_DATA_DIR` to keep test runs out of your real
  data folder.

## Windows x64

- **Installer**: Inno Setup 6 (`packaging/windows/MangaList.iss`), compiled with
  `iscc /DAppVersion=<version> /DAppVersionNumeric=<numeric> packaging\windows\MangaList.iss`
  into `package\MangaList-v<version>-windows-x64-setup.exe`.
  - Per-user (`PrivilegesRequired=lowest`): no administrator rights, installs to
    `%LOCALAPPDATA%\Programs\MangaList`, Start-menu shortcut (optional desktop shortcut),
    uninstaller registered for the current user.
  - Upgrade: a newer setup with the same `AppId` installs over the old version. The old
    `_internal` folder is deleted first (no stale libraries), and a running Manga List is closed
    first. Never change `AppId`.
  - User data (`%LOCALAPPDATA%\MangaList`) is outside the install folder: upgrades and
    uninstalls keep it.
  - Why Inno Setup and not a WiX MSI: a per-user install without elevation is one directive
    here, whereas a per-user MSI needs `ALLUSERS=2` / `MSIINSTALLPERUSER` and ICE validation
    workarounds. Inno also gives a single `setup.exe` with upgrade-in-place and "close the running
    app", and the GitHub Windows images come with it preinstalled.
- **Portable zip**: the onedir folder plus `README.md`, `LICENSE.txt` and a `portable` marker
  file, which keeps `data\` and `logs\` next to the exe (see `manga_list/paths.py`).
- CI also installs the setup silently, runs the installed app, installs again (upgrade), then
  uninstalls, and checks the Start-menu entry, that the app files are removed, and that the user
  data folder is kept.

## macOS

- `packaging/macos/build_dmg.sh <version> <arm64|x64>` checks that the binary's architecture
  matches the name, then makes `package/MangaList-v<version>-macos-<arch>.dmg` (the app plus an
  Applications link).
- Runners: `macos-latest` (Apple silicon) builds arm64; `macos-15-intel` builds x64 natively.
  GitHub has said Intel macOS runners will be retired; when that label disappears, drop the
  x64 row from the matrix, or build a universal2 app on arm64 (that needs universal2 wheels of
  Python and PySide6).
- PyInstaller ad-hoc signs the bundle (required on Apple silicon). That is not a Developer ID
  signature, so Gatekeeper asks once (see the README).

## Linux x64

- `packaging/linux/build_packages.sh <version>` makes both packages from `dist/MangaList`:
  - `MangaList-v<version>-linux-x64.tar.gz`: the onedir folder, README, LICENSE, `.desktop` file and
    icon.
  - `MangaList-v<version>-linux-x64.AppImage`: `AppRun` + the onedir build in `usr/bin`, built
    with appimagetool 1.9.1 (downloaded once and checked against its SHA-256). It runs
    extract-and-run, so building needs no FUSE (containers, CI).
- Built on `ubuntu-22.04`: an AppImage runs only on systems with the build machine's glibc or
  newer, so the oldest supported runner gives the widest reach (glibc 2.35).
- The AppImage bundles Qt but uses the system's X11 / Wayland, OpenGL and fontconfig libraries,
  like most Qt AppImages.

## Checksums and release

The `checksums` job collects every package, checks that all six expected file names exist, and
writes `SHA256SUMS`. On a `v*` tag, the `release` job (the only job with `contents: write`)
attaches everything to a draft GitHub release.

## Code signing (not set up)

Nothing is signed; no certificates are bought or stored. The workflow marks each place with a
`# Signing hook` comment. To add signing later:

- **Windows** (Authenticode): store the certificate (PFX, base64) and its password as repository
  secrets. Add a step that signs `dist\MangaList\MangaList.exe` (before the zip and the installer
  are made) and one that signs `package\*-setup.exe` afterwards, both with
  `signtool sign /fd sha256 /tr <timestamp url> /td sha256`. Alternatively, add a `SignTool=`
  directive to `MangaList.iss` so Inno signs the setup and the uninstaller. Cloud signing
  (e.g. Azure Trusted Signing) works the same way with its own action.
- **macOS** (Developer ID + notarization): store the Developer ID Application certificate (p12,
  base64), its password, and an App Store Connect API key as secrets. Import the certificate into
  a temporary keychain. Run `codesign --deep --force --options runtime --timestamp --sign
  "Developer ID Application: ..." dist/MangaList.app`, then build the dmg, sign it, and run
  `xcrun notarytool submit --wait` and `xcrun stapler staple` on it.
- **Linux**: optional. AppImages can carry an embedded GPG signature (`appimagetool --sign`);
  `SHA256SUMS` can be GPG-signed as well.

Gate these steps on the secrets being present (map a secret to an `env` value at job level and use
`if: env.X != ''`), so forks and pull requests without secrets still build unsigned packages.

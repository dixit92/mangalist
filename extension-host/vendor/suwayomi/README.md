# Vendored from Suwayomi-Server

Source: https://github.com/Suwayomi/Suwayomi-Server, tag **v2.4.2366** (commit `40bc5da57820324ed0ae724cbbc020cc4673b594`), copied 2026-10-21.
License: **MPL-2.0** (`LICENSE` here). Every file under this folder stays under the MPL-2.0, including the changes
listed below; MangaList's own code (`../../host/src/main/kotlin/mangalist`) is not covered by it.

## What is here

| Folder | From | Why |
| --- | --- | --- |
| `AndroidCompat/` (with `Config/`) | `AndroidCompat/` | the Android classes extensions call (preferences, Handler / Looper, Uri, Log, Base64, ...) on a plain JVM |
| `tachiyomi/eu/kanade/tachiyomi/` | `server/src/main/kotlin/eu/kanade/tachiyomi/` | the Tachiyomi source API (Source, HttpSource, models, NetworkHelper, interceptors, ChapterRecognition) |
| `tachiyomi/suwayomi/tachidesk/manga/impl/util/` | same path under `server/src/main/kotlin/` | the extension loader: `PackageTools` (dex2jar, APK metadata, class loading), `BytecodeEditor`, `ChildFirstURLClassLoader`, `AndroidManifestParser`, `lang/RxCoroutineBridge`, `lang/JsonObject` |
| `tachiyomi/eu/kanade/tachiyomi/source/local/metadata/ComicInfo.kt` | same path under `server/src/main/kotlin/` (2026-10-22, lane H) | the `ComicInfo.xml` model the downloader writes into each CBZ, exactly as Suwayomi's (unchanged) |
| `tachiyomi/suwayomi/tachidesk/server/util/CEFManager.kt` | `server/src/main/kotlin/suwayomi/tachidesk/server/util/` (2026-10-22, lane H) | downloads, unpacks and starts Chromium (JCEF) for the WebView |
| `tachiyomi/suwayomi/tachidesk/server/util/Platform.kt` | `server/server-config/src/main/kotlin/suwayomi/tachidesk/server/util/` (2026-10-22, lane H) | the OS / architecture CEFManager picks the Chromium package by |
| `../../gradle/libs.versions.toml`, `../../gradle/wrapper/`, `../../gradlew*` | the same files | the same dependency versions and Gradle |

## Changes (marked "MangaList extension host (prototype)" in the code)

- `AndroidCompat/build.gradle.kts`, `AndroidCompat/Config/build.gradle.kts`: rewritten (no ktlint, no version-catalog
  bundles); `getAndroid.sh` / `getAndroid.ps1` removed (the stubs come from Maven: `com.github.Suwayomi:android-jar`).
  JCEF (`org.jetbrains.intellij.deps.jcef:jcef`, from JetBrains' `intellij-dependencies` Maven repository, as Suwayomi)
  is back in since 2026-10-22 (lane H), for the WebView.
- `AndroidCompat/src/main/resources/font/` removed (~37 MB of Noto fonts; `android.graphics.Typeface` falls back to the
  JVM's fonts).
- `AndroidCompat/.../webkit/{CefHelper,KcefHelper,KcefWebSettings,KcefWebViewProvider}.kt` and
  `AndroidCompatInitializer`'s KCEF provider registration: removed in the first prototype, **restored unchanged**
  2026-10-22 (lane H). The host registers its own factory after it (`host/.../WebViewSetup.kt`) only to start Chromium
  at the first WebView instead of at boot; Suwayomi's ServerSetup cookie hand-over (`InitBrowserHandler`) is in the same
  host file.
- `server/util/CEFManager.kt` (2026-10-22, lane H): the CEF folders from the host's settings (`serverConfig.dataRoot` /
  `cacheRoot` in `host/.../ServerConfig.kt`) instead of Suwayomi's `ApplicationDirs`; started once by `init()` (no live
  `kcefEnabled` subscription; `started` tells whether it was asked for). The download, unpacking and start-up are
  Suwayomi's. Its `BuildConfig.JCEF_VERSION` / `JCEF_JBR_RELEASE` come from `host/.../generated/BuildConfig.kt` (Suwayomi's
  values: the `jcef` version in `gradle/libs.versions.toml`, `jbr-release-25.0.4b508.27`).
- `server/util/Platform.kt` (2026-10-22, lane H): only the OS and the architecture (Suwayomi's JVM / OS details for its
  GraphQL "about" left out).
- `network/interceptor/CloudflareInterceptor.kt`: unchanged (2026-10-21; it first was left out). Its FlareSolverr settings come from
  the host's own `suwayomi.tachidesk.server.serverConfig` (`host/src/main/kotlin/suwayomi/tachidesk/server/ServerConfig.kt`),
  which has only those six keys.
- `network/NetworkHelper.kt`: no source-registry reset on a user-agent change (the host makes its sources again itself,
  `Main.kt`).
- `AppInfo.kt`: a fixed version (v2.4.2366) and image MIME list instead of Suwayomi's BuildConfig / ImageUtil.
- `PackageTools.kt`: the extensions folder from the `mangalist.extensions.dir` system property instead of Suwayomi's ApplicationDirs.
- Not copied: `source/local/**` (but `source/local/metadata/ComicInfo.kt`, above), `util/storage/**` (Suwayomi's local
  source and EPUB reader).
- Rewritten in the host, not copied (Suwayomi's versions sit on its Exposed database): the downloader
  (`DownloadManager` / `Downloader` / `ChaptersFilesProvider` / `ArchiveProvider` -> `host/.../Downloader.kt`,
  `ChapterFiles.kt`), the chapter merge (`Chapter.updateChapterListDatabase` -> `Library.kt`), source preferences
  (`Source.getSourcePreferences` -> `SourcePreferences.kt`), `ImageUtil.findImageType` (`ImageType` in `ChapterFiles.kt`).

## Updating

Copy the same files from a newer Suwayomi-Server tag, re-apply the changes above, and run the host's checks
(`../../README.md`).

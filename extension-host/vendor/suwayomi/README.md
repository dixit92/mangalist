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
| `../../gradle/libs.versions.toml`, `../../gradle/wrapper/`, `../../gradlew*` | the same files | the same dependency versions and Gradle |

## Changes (marked "MangaList extension host (prototype)" in the code)

- `AndroidCompat/build.gradle.kts`, `AndroidCompat/Config/build.gradle.kts`: rewritten (no ktlint, no JCEF / KCEF, no
  version-catalog bundles); `getAndroid.sh` / `getAndroid.ps1` removed (the stubs come from Maven: `com.github.Suwayomi:android-jar`).
- `AndroidCompat/src/main/resources/font/` removed (~37 MB of Noto fonts; `android.graphics.Typeface` falls back to the
  JVM's fonts).
- `AndroidCompat/.../webkit/{CefHelper,KcefHelper,KcefWebSettings,KcefWebViewProvider}.kt` removed and
  `AndroidCompatInitializer` registers no WebView provider: extensions that open a WebView fail ("No factory registered").
- `network/interceptor/CloudflareInterceptor.kt` removed (FlareSolverr); the host has its own pass-through of the same name.
- `network/NetworkHelper.kt`: no source-registry reset on a user-agent change; the pass-through CloudflareInterceptor.
- `AppInfo.kt`: a fixed version (v2.4.2366) and image MIME list instead of Suwayomi's BuildConfig / ImageUtil.
- `PackageTools.kt`: the extensions folder from the `mangalist.extensions.dir` system property instead of Suwayomi's ApplicationDirs.
- Not copied: `source/local/**`, `util/storage/**` (Suwayomi's local source and EPUB reader).

## Updating

Copy the same files from a newer Suwayomi-Server tag, re-apply the changes above, and run the host's checks
(`../../README.md`).

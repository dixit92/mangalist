package suwayomi.tachidesk.server.generated

/**
 * MangaList extension host: the two build constants Suwayomi's CEFManager (vendored) reads from Suwayomi's generated
 * BuildConfig. They are Suwayomi v2.4.2366's: `JCEF_VERSION` is `gradle/libs.versions.toml`'s `jcef` (the JCEF jar the
 * host compiles against) and `JCEF_JBR_RELEASE` is Suwayomi's `buildSrc/.../Constants.kt` `webviewJbrRelease` (the
 * JetBrains Runtime release whose JCEF / Chromium libraries are downloaded at first use). Change them together.
 */
object BuildConfig {
    const val JCEF_VERSION = "144.0.15-g72717cf-chromium-144.0.7559.172-api-1.21-262-b37"
    const val JCEF_JBR_RELEASE = "jbr-release-25.0.4b508.27"
}

package mangalist.extensionhost

import kotlinx.serialization.json.Json
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull
import kotlin.test.assertTrue

/** Both extension index formats (keiyoushi's, 2026-10-21) read into the same entries; made-up values. */
class RepoTest {
    private val current =
        """
        {"name": "Keiyoushi", "signingKey": "ab", "extensionList": {"extensions": [
          {"name": "MangaDex", "packageName": "eu.kanade.tachiyomi.extension.all.mangadex",
           "resources": {"apkUrl": "https://example.org/a.apk", "jarUrl": "https://example.org/a.jar", "iconUrl": "x"},
           "extensionLib": "1.6", "versionCode": "106000", "versionName": "1.6.0", "contentWarning": "CONTENT_WARNING_MIXED",
           "sources": [{"id": "2499283573021220255", "name": "MangaDex", "language": "en", "homeUrl": "https://mangadex.org"},
                       {"id": "1", "name": "MangaDex", "language": "fr", "homeUrl": "https://mangadex.org"}]},
          {"name": "Weeb Central", "packageName": "eu.kanade.tachiyomi.extension.en.weebcentral",
           "resources": {"apkUrl": "https://example.org/w.apk"}, "extensionLib": "1.6", "versionCode": "16025",
           "versionName": "1.6.25", "contentWarning": "CONTENT_WARNING_NONE",
           "sources": [{"id": "7", "name": "Weeb Central", "language": "en", "homeUrl": "https://weebcentral.com"}]}
        ]}}
        """.trimIndent()

    private val legacy =
        """
        [{"name": "Outdated App", "pkg": "eu.kanade.tachiyomi.extension.all.keiyoushi", "apk": "tachiyomi-all.keiyoushi-v1.4.1.apk",
          "lang": "all", "code": 1, "version": "1.4.1", "nsfw": 1,
          "sources": [{"name": "Outdated App", "lang": "all", "id": "1", "baseUrl": "https://keiyoushi.github.io"}]}]
        """.trimIndent()

    @Test
    fun `the current index gives absolute apk and jar urls`() {
        val (md, wc) = Repo.parse(Json.parseToJsonElement(current), "https://example.org/repo/index.json")
        assertEquals("eu.kanade.tachiyomi.extension.all.mangadex", md.pkg)
        assertEquals("https://example.org/a.jar", md.jarUrl)
        assertEquals("all", md.lang)
        assertEquals(106000, md.versionCode)
        assertEquals("1.6", md.libVersion)
        assertTrue(md.nsfw)
        assertEquals("2499283573021220255", md.sources.first().id)
        assertEquals("en", wc.lang)
        assertNull(wc.jarUrl)
        assertEquals(false, wc.nsfw)
    }

    @Test
    fun `the legacy list resolves apk names against the index folder`() {
        val (only) = Repo.parse(Json.parseToJsonElement(legacy), "https://example.org/repo/index.min.json")
        assertEquals("https://example.org/repo/apk/tachiyomi-all.keiyoushi-v1.4.1.apk", only.apkUrl)
        assertEquals("1.4", only.libVersion)
        assertTrue(only.nsfw)
        assertEquals("https://keiyoushi.github.io", only.sources.single().baseUrl)
    }
}

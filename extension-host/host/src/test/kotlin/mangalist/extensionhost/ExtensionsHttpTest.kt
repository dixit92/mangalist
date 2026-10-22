package mangalist.extensionhost

import com.sun.net.httpserver.HttpServer
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.OkHttpClient
import xyz.nulldev.androidcompat.io.sharedprefs.JavaSharedPreferences
import java.net.InetSocketAddress
import java.net.URI
import java.net.http.HttpClient
import java.net.http.HttpRequest
import java.net.http.HttpResponse
import java.nio.file.Files
import kotlin.io.path.createTempDirectory
import kotlin.io.path.listDirectoryEntries
import kotlin.test.AfterTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

/** HTTP: extension management (repository versions, the signature check, uninstall) and source preferences. */
class ExtensionsHttpTest {
    companion object {
        init {
            System.setProperty("suwayomi.tachidesk.config.server.rootDir", createTempDirectory("android").toString())
        }
    }

    private val dir = createTempDirectory("ext")
    private val files = mutableMapOf<String, ByteArray>()
    private val repoServer =
        HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0).apply {
            createContext("/") { ex ->
                val body = files[ex.requestURI.path]
                if (body == null) {
                    ex.sendResponseHeaders(404, -1)
                } else {
                    ex.sendResponseHeaders(200, body.size.toLong())
                    ex.responseBody.use { it.write(body) }
                }
                ex.close()
            }
            start()
        }
    private val repoBase = "http://127.0.0.1:${repoServer.address.port}"
    private val repoUrl = "$repoBase/index.json"
    private val registry = ExtensionRegistry(dir.resolve("extensions"), OkHttpClient())
    private val source = ConfigurableFakeSource("http://127.0.0.1:9", "Configurable ${System.nanoTime()}")
    private val shared = mutableMapOf<Long, android.content.SharedPreferences>()
    private val server =
        ApiServer(
            "127.0.0.1", 0, registry, Repo(OkHttpClient()), null, repoUrl,
            SourcePreferences({ null }, { shared.getOrPut(it.id) { JavaSharedPreferences("source_${it.id}") } }),
        )
    private val http = HttpClient.newHttpClient()

    init {
        val notAnExtension = TestKey.zip(dir.resolve("x.jar"), "AndroidManifest.xml" to ByteArray(8), "a/B.class" to ByteArray(16))
        files["/good.jar"] = Files.readAllBytes(TestKey.A.signJar(notAnExtension))
        files["/foreign.jar"] = Files.readAllBytes(TestKey.B.signJar(notAnExtension))
        files["/index.json"] =
            """
            {"name": "Test repo", "signingKey": "${TestKey.A.fingerprint}", "extensionList": {"extensions": [
              ${entry("eu.kanade.tachiyomi.extension.en.good", "good.jar", 2, "1.4.2")},
              ${entry("eu.kanade.tachiyomi.extension.en.foreign", "foreign.jar", 1, "1.4.1")}
            ]}}
            """.trimIndent().toByteArray()
        registry.sources[source.id] = source
        server.start()
    }

    private fun entry(
        pkg: String,
        jar: String,
        code: Int,
        version: String,
    ) = """{"name": "$pkg", "packageName": "$pkg", "resources": {"apkUrl": "$repoBase/x.apk", "jarUrl": "$repoBase/$jar"},
        "extensionLib": "1.4", "versionCode": "$code", "versionName": "$version", "contentWarning": "CONTENT_WARNING_NONE",
        "sources": []}"""

    @AfterTest
    fun close() {
        server.stop()
        repoServer.stop(0)
    }

    private fun call(
        method: String,
        path: String,
        body: String? = null,
    ): Pair<Int, JsonElement> {
        val req =
            HttpRequest
                .newBuilder(URI("http://127.0.0.1:${server.port}$path"))
                .method(method, if (body == null) HttpRequest.BodyPublishers.noBody() else HttpRequest.BodyPublishers.ofString(body))
                .build()
        val resp = http.send(req, HttpResponse.BodyHandlers.ofString())
        return resp.statusCode() to Json.parseToJsonElement(resp.body())
    }

    @Test
    fun `an extension not signed with the repository's key is refused before it is loaded`() {
        val (status, error) = call("POST", "/extensions/install", """{"pkg": "eu.kanade.tachiyomi.extension.en.foreign"}""")
        assertEquals(400, status, error.toString())
        assertTrue("not by the repository's key" in error.jsonObject["error"]!!.jsonPrimitive.content, error.toString())
        assertEquals(emptyList(), registry.installed.keys.toList())
        assertEquals(emptyList(), dir.resolve("extensions").listDirectoryEntries("*.jar"))
    }

    @Test
    fun `a signed package passes the check and is then read as an extension`() {
        val (status, error) = call("POST", "/extensions/install", """{"pkg": "eu.kanade.tachiyomi.extension.en.good"}""")
        // the test jar is signed with the right key but is no extension: it fails after the check, not at it
        assertEquals(502, status, error.toString())
        assertFalse("signed" in error.jsonObject["error"]!!.jsonPrimitive.content, error.toString())
    }

    @Test
    fun `installed extensions show the repository's version and whether an update is there`() {
        registry.installed["eu.kanade.tachiyomi.extension.en.good"] =
            InstalledExtension(
                "eu.kanade.tachiyomi.extension.en.good", "Good", "1.4.1", 1, "1.4", "x.Good", "good.jar",
                signatureHash = TestKey.A.fingerprint, repo = repoUrl,
            )
        val (status, list) = call("GET", "/extensions")
        assertEquals(200, status, list.toString())
        val ext = list.jsonArray.single().jsonObject
        assertEquals("1.4.2", ext["repoVersion"]!!.jsonPrimitive.content)
        assertTrue(ext["updateAvailable"]!!.jsonPrimitive.boolean)
        assertEquals(TestKey.A.fingerprint, ext["signatureHash"]!!.jsonPrimitive.content)
        assertEquals(404, call("POST", "/extensions/uninstall", """{"pkg": "not.installed"}""").first)
        assertEquals(404, call("POST", "/extensions/update", """{"pkg": "not.installed"}""").first)
        assertEquals(200, call("POST", "/extensions/uninstall", """{"pkg": "eu.kanade.tachiyomi.extension.en.good"}""").first)
        assertEquals(0, call("GET", "/extensions").second.jsonArray.size)
    }

    @Test
    fun `preferences over HTTP`() {
        val (status, list) = call("GET", "/sources/${source.id}/preferences")
        assertEquals(200, status, list.toString())
        assertEquals("lang", list.jsonArray[0].jsonObject["key"]!!.jsonPrimitive.content)
        val (s2, after) = call("POST", "/sources/${source.id}/preferences", """{"key": "lang", "value": "ja"}""")
        assertEquals(200, s2, after.toString())
        assertEquals(JsonPrimitive("ja"), after.jsonArray[0].jsonObject["currentValue"]) // the whole screen again
        assertEquals(JsonPrimitive(4), after.jsonArray[4].jsonObject["position"])
        assertEquals(JsonPrimitive("ja"), call("GET", "/sources/${source.id}/preferences").second.jsonArray[0].jsonObject["currentValue"])
        assertEquals(400, call("POST", "/sources/${source.id}/preferences", """{"key": "lang", "value": "xx"}""").first)
        assertEquals(404, call("GET", "/sources/1/preferences").first)
    }
}

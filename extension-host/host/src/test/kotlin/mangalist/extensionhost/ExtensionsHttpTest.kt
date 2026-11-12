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
import java.net.Socket
import java.net.URI
import java.net.http.HttpClient
import java.net.http.HttpRequest
import java.net.http.HttpResponse
import java.nio.file.Files
import kotlin.io.path.createTempDirectory
import kotlin.io.path.listDirectoryEntries
import kotlin.io.path.name
import kotlin.io.path.readText
import kotlin.test.AfterTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

/** The class the test's extension jars name: what an extension's own source class is to the host. */
class JarSource : FakeSource("http://127.0.0.1:9", "Jar Source")

/**
 * HTTP: extension management (repository versions, the signature check, a failed update, uninstall), source
 * preferences, bad calls and the screening of requests.
 */
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

    private val jarPkg = "eu.kanade.tachiyomi.extension.en.jar"

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
                .apply { if (body != null) header("Content-Type", "application/json") }
                .build()
        val resp = http.send(req, HttpResponse.BodyHandlers.ofString())
        return resp.statusCode() to Json.parseToJsonElement(resp.body())
    }

    /** A jar signed with the repository's key whose manifest reads as an extension's ([className]: its source class). */
    private fun extensionJar(
        name: String,
        version: String,
        className: String,
        feature: String = "tachiyomi.extension",
    ): ByteArray {
        val manifest =
            """
            <manifest xmlns:android="http://schemas.android.com/apk/res/android" package="$jarPkg"
                android:versionCode="1" android:versionName="$version">
              <uses-feature android:name="$feature"/>
              <application android:label="Tachiyomi: Jar">
                <meta-data android:name="tachiyomi.extension.class" android:value="$className"/>
              </application>
            </manifest>
            """.trimIndent()
        val bytes = JarSource::class.java.getResourceAsStream("JarSource.class")!!.readAllBytes()
        val jar = TestKey.zip(dir.resolve(name), "AndroidManifest.xml" to manifest.toByteArray(), "mangalist/extensionhost/JarSource.class" to bytes)
        return Files.readAllBytes(TestKey.A.signJar(jar))
    }

    /** One request written as it is (headers the JDK's client does not let a caller set); the answer's status. */
    private fun raw(
        head: List<String>,
        body: String = "",
    ): Int =
        Socket("127.0.0.1", server.port).use { socket ->
            val length = if (body.isEmpty()) emptyList() else listOf("Content-Length: ${body.toByteArray().size}")
            socket.getOutputStream().write(((head + length + "Connection: close").joinToString("\r\n") + "\r\n\r\n" + body).toByteArray())
            socket.getOutputStream().flush()
            socket.getInputStream().bufferedReader().readLine().split(' ')[1].toInt()
        }

    private val hostHeader get() = "Host: 127.0.0.1:${server.port}"

    @Test
    fun `an update that fails to load leaves the installed version working`() {
        files["/v1.jar"] = extensionJar("v1.jar", "1.4.1", JarSource::class.java.name)
        files["/v2.jar"] = extensionJar("v2.jar", "1.4.2", "mangalist.extensionhost.NoSuchSource")
        files["/index.json"] =
            """{"name": "Test repo", "signingKey": "${TestKey.A.fingerprint}", "extensionList": {"extensions": [${entry(jarPkg, "v2.jar", 2, "1.4.2")}]}}"""
                .toByteArray()
        val (installed, first) = call("POST", "/extensions/install", """{"pkg": "$jarPkg", "apkUrl": "$repoBase/v1.jar"}""")
        assertEquals(200, installed, first.toString())
        val manifest = dir.resolve("extensions/installed.json").readText()

        val (status, error) = call("POST", "/extensions/update", """{"pkg": "$jarPkg"}""")
        assertEquals(502, status, error.toString())
        assertTrue("NoSuchSource" in error.jsonObject["error"]!!.jsonPrimitive.content, error.toString())

        val ext = call("GET", "/extensions").second.jsonArray.single().jsonObject
        assertEquals("1.4.1", ext["version"]!!.jsonPrimitive.content)
        assertTrue(ext["updateAvailable"]!!.jsonPrimitive.boolean)
        val id = ext["sources"]!!.jsonArray.single().jsonObject["id"]!!.jsonPrimitive.content
        val (searched, found) = call("GET", "/sources/$id/search?q=x")
        assertEquals(200, searched, found.toString())
        assertEquals(listOf("$jarPkg-v1.4.1.jar"), dir.resolve("extensions").listDirectoryEntries("*.jar").map { it.name })
        assertEquals(manifest, dir.resolve("extensions/installed.json").readText())
    }

    @Test
    fun `a bad call is answered 400, not as a failed extension`() {
        assertEquals(400, call("POST", "/extensions/install", "{not json").first)
        assertEquals(400, call("POST", "/extensions/uninstall", """{"name": "no pkg"}""").first)
        assertEquals(400, call("POST", "/sources/${source.id}/manga", """["a list"]""").first)
        files["/plain.jar"] = extensionJar("plain.jar", "1.4.1", JarSource::class.java.name, feature = "something.else")
        val (status, error) = call("POST", "/extensions/install", """{"apkUrl": "$repoBase/plain.jar"}""")
        assertEquals(400, status, error.toString())
        assertTrue("is not a Tachiyomi / Mihon extension" in error.jsonObject["error"]!!.jsonPrimitive.content, error.toString())
        assertEquals(emptyList(), registry.installed.keys.toList())
        assertEquals(emptyList(), dir.resolve("extensions").listDirectoryEntries("*.jar"))
    }

    @Test
    fun `a request from a web page is refused`() {
        assertEquals(200, raw(listOf("GET /health HTTP/1.1", hostHeader)))
        assertEquals(403, raw(listOf("GET /health HTTP/1.1", hostHeader, "Origin: https://example.com")))
        val post = listOf("POST /repos HTTP/1.1", hostHeader, "Content-Type: application/json", "Origin: http://127.0.0.1:${server.port}")
        assertEquals(403, raw(post, """{"url": "$repoBase/other.json"}"""))
        assertEquals(listOf(repoUrl), server.repos.urls())
    }

    @Test
    fun `a request that names another host is refused`() {
        assertEquals(403, raw(listOf("GET /extensions HTTP/1.1", "Host: rebound.example.com:${server.port}")))
        assertEquals(403, raw(listOf("GET /extensions HTTP/1.1", "Host: 127.0.0.1.example.com")))
        for (name in listOf("127.0.0.1", "localhost:${server.port}", "LOCALHOST", "[::1]:${server.port}")) {
            assertEquals(200, raw(listOf("GET /extensions HTTP/1.1", "Host: $name")), name)
        }
    }

    @Test
    fun `a body that is not sent as JSON is refused`() {
        val body = """{"url": "$repoUrl"}"""
        assertEquals(415, raw(listOf("POST /repos/remove HTTP/1.1", hostHeader, "Content-Type: text/plain"), body))
        assertEquals(415, raw(listOf("DELETE /repos HTTP/1.1", hostHeader), body))
        assertEquals(listOf(repoUrl), server.repos.urls())
        assertEquals(200, raw(listOf("POST /repos/remove HTTP/1.1", hostHeader, "Content-Type: application/json; charset=utf-8"), body))
        assertEquals(emptyList(), server.repos.urls())
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

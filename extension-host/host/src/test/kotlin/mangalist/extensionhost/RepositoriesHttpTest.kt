package mangalist.extensionhost

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.OkHttpClient
import java.net.URI
import java.net.URLEncoder
import java.net.http.HttpClient
import java.net.http.HttpRequest
import java.net.http.HttpResponse
import java.nio.file.Files
import kotlin.io.path.createTempDirectory
import kotlin.test.AfterTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

/**
 * HTTP: several repositories - `GET/POST/DELETE /repos`, every repository's extensions in `GET /repo`, each
 * repository's own signing key at install, an installed extension updated only from the repository it came from, the
 * same package in two repositories.
 */
class RepositoriesHttpTest {
    companion object {
        init {
            System.setProperty("suwayomi.tachidesk.config.server.rootDir", createTempDirectory("android").toString())
        }

        const val PKG = "eu.kanade.tachiyomi.extension.en.shared"
        const val ONLY_B = "eu.kanade.tachiyomi.extension.en.onlyb"
    }

    private val dir = createTempDirectory("repos-http")
    private val web = StaticServer()
    private val a = "${web.base}/a/index.json"
    private val b = "${web.base}/b/index.json"
    private val registry = ExtensionRegistry(dir.resolve("extensions"), OkHttpClient())
    private val repo = Repo(OkHttpClient(), cacheMillis = 0)
    private val repos = RepoList(dir.resolve("repos.json"), repo, listOf(a))
    private val server = ApiServer("127.0.0.1", 0, registry, repo, null, a, null, repos)
    private val http = HttpClient.newHttpClient()

    init {
        val notAnExtension = TestKey.zip(dir.resolve("x.jar"), "AndroidManifest.xml" to ByteArray(8), "a/B.class" to ByteArray(16))
        web.files["/signed-a.jar"] = Files.readAllBytes(TestKey.A.signJar(notAnExtension))
        web.files["/signed-b.jar"] = Files.readAllBytes(TestKey.B.signJar(notAnExtension))
        web["/a/index.json"] = repoIndex("Repo A", TestKey.A.fingerprint, repoEntry(PKG, "${web.base}/signed-a.jar", 2, "1.4.2"))
        web["/b/index.json"] =
            repoIndex(
                "Repo B", TestKey.B.fingerprint,
                repoEntry(PKG, "${web.base}/signed-b.jar", 9, "1.4.9"),
                // B lists a package signed with A's key: not B's, so refused when installed from B
                repoEntry(ONLY_B, "${web.base}/signed-a.jar", 1, "1.4.1"),
            )
        server.start()
    }

    @AfterTest
    fun close() {
        server.stop()
        web.close()
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

    private fun JsonElement.str(key: String) = jsonObject[key]!!.jsonPrimitive.content

    private fun error(answer: Pair<Int, JsonElement>) = answer.second.str("error")

    private fun installFromA(version: String = "1.4.1") {
        registry.installed[PKG] =
            InstalledExtension(PKG, "Shared", version, version.substringAfterLast('.').toInt(), "1.4", "x.Shared", "shared.jar", signatureHash = TestKey.A.fingerprint, repo = a)
    }

    @Test
    fun `the repository list over HTTP - add checks the index first, delete keeps installed extensions`() {
        val (s0, list0) = call("GET", "/repos")
        assertEquals(200, s0, list0.toString())
        assertEquals(listOf(a), list0.jsonArray.map { it.str("url") })
        assertEquals("Repo A", list0.jsonArray[0].str("name"))
        assertEquals(1, list0.jsonArray[0].jsonObject["extensions"]!!.jsonPrimitive.int)

        val refused = call("POST", "/repos", """{"url": "${web.base}/signed-a.jar"}""")
        assertEquals(400, refused.first, refused.second.toString())
        assertTrue("not an extension repository" in error(refused), refused.second.toString())

        val (s1, added) = call("POST", "/repos", """{"url": "$b"}""")
        assertEquals(200, s1, added.toString())
        assertEquals("Repo B", added.str("name"))
        assertEquals(TestKey.B.fingerprint, added.str("signingKey"))
        assertEquals(2, added.jsonObject["extensions"]!!.jsonPrimitive.int)
        assertEquals(listOf(a, b), call("GET", "/repos").second.jsonArray.map { it.str("url") })

        installFromA()
        val (s2, removed) = call("DELETE", "/repos", """{"url": "$a"}""")
        assertEquals(200, s2, removed.toString())
        assertEquals(listOf(b), call("GET", "/repos").second.jsonArray.map { it.str("url") })
        assertEquals(404, call("DELETE", "/repos?url=" + URLEncoder.encode(a, Charsets.UTF_8)).first)
        // still installed, but no updates from a repository no longer listed (B's newer version is not offered)
        val ext = call("GET", "/extensions").second.jsonArray.single()
        assertEquals(a, ext.str("repo"))
        assertFalse(ext.jsonObject["repoListed"]!!.jsonPrimitive.boolean)
        assertFalse(ext.jsonObject["updateAvailable"]!!.jsonPrimitive.boolean)
        assertEquals(JsonNull, ext.jsonObject["repoVersion"])
        val update = call("POST", "/extensions/update", """{"pkg": "$PKG"}""")
        assertEquals(409, update.first, update.second.toString())
        assertTrue("no longer in the list" in error(update))
        // POST /repos/remove does the same as DELETE (for clients that send no body with DELETE)
        assertEquals(200, call("POST", "/repos/remove", """{"url": "$b"}""").first)
        assertEquals(0, call("GET", "/repos").second.jsonArray.size)
    }

    @Test
    fun `GET repo lists every repository's extensions, the same package once per repository`() {
        call("POST", "/repos", """{"url": "$b"}""")
        val (status, all) = call("GET", "/repo")
        assertEquals(200, status, all.toString())
        assertEquals(
            listOf(Triple(PKG, a, "1.4.2"), Triple(PKG, b, "1.4.9"), Triple(ONLY_B, b, "1.4.1")),
            all.jsonArray.map { Triple(it.str("pkg"), it.str("repo"), it.str("version")) },
        )
        assertEquals(listOf("Repo A", "Repo B", "Repo B"), all.jsonArray.map { it.str("repoName") })
        assertEquals(listOf(a), call("GET", "/repo?url=" + URLEncoder.encode(a, Charsets.UTF_8)).second.jsonArray.map { it.str("repo") })
    }

    @Test
    fun `each repository's own key is enforced at install`() {
        call("POST", "/repos", """{"url": "$b"}""")
        // B's package signed with A's key: refused although A is a trusted repository too
        val foreign = call("POST", "/extensions/install", """{"pkg": "$ONLY_B", "repo": "$b"}""")
        assertEquals(400, foreign.first, foreign.second.toString())
        assertTrue("not by the repository's key ${TestKey.B.fingerprint}" in error(foreign), foreign.second.toString())
        // the shared package from B, signed with B's key: passes the check (then fails as the test jar is no extension)
        val fromB = call("POST", "/extensions/install", """{"pkg": "$PKG", "repo": "$b"}""")
        assertEquals(502, fromB.first, fromB.second.toString())
        assertFalse("signed" in error(fromB), fromB.second.toString())
        // ... and from A, signed with A's key: passes too
        assertEquals(502, call("POST", "/extensions/install", """{"pkg": "$PKG", "repo": "$a"}""").first)
        // a repository not in the list is refused
        val unknown = call("POST", "/extensions/install", """{"pkg": "$PKG", "repo": "${web.base}/c/index.json"}""")
        assertEquals(400, unknown.first, unknown.second.toString())
        assertTrue("add it first" in error(unknown))
        assertEquals(emptyList(), registry.installed.keys.toList())
    }

    @Test
    fun `an installed extension is updated only from the repository it came from`() {
        call("POST", "/repos", """{"url": "$b"}""")
        installFromA()
        // A offers 1.4.2 (B's 1.4.9 is not this extension's repository)
        val ext = call("GET", "/extensions").second.jsonArray.single()
        assertEquals("1.4.2", ext.str("repoVersion"))
        assertEquals("Repo A", ext.str("repoName"))
        assertTrue(ext.jsonObject["updateAvailable"]!!.jsonPrimitive.boolean)
        val other = call("POST", "/extensions/update", """{"pkg": "$PKG", "repo": "$b"}""")
        assertEquals(400, other.first, other.second.toString())
        assertTrue("updates only from there" in error(other))
        // installing it from B while it is installed from A is refused: uninstall first
        val switch = call("POST", "/extensions/install", """{"pkg": "$PKG", "repo": "$b"}""")
        assertEquals(409, switch.first, switch.second.toString())
        assertTrue("uninstall it first" in error(switch))
        // the update itself goes to A: A's jar (A's key) passes the check, then fails as the test jar is no extension
        val update = call("POST", "/extensions/update", """{"pkg": "$PKG"}""")
        assertEquals(502, update.first, update.second.toString())
        // A newer up to date: nothing to do, the installed one answered
        installFromA("1.4.2")
        val same = call("POST", "/extensions/update", """{"pkg": "$PKG"}""")
        assertEquals(200, same.first, same.second.toString())
        assertEquals("1.4.2", same.second.str("version"))
        assertFalse(same.second.jsonObject["updateAvailable"]!!.jsonPrimitive.boolean)
    }

    @Test
    fun `a repository whose index changes its signing key is not trusted for installs or updates`() {
        installFromA()
        assertEquals(200, call("GET", "/repos").first) // the key of the first fetch is kept
        web["/a/index.json"] = repoIndex("Repo A", TestKey.B.fingerprint, repoEntry(PKG, "${web.base}/signed-b.jar", 3, "1.4.3"))
        val update = call("POST", "/extensions/update", """{"pkg": "$PKG"}""")
        assertEquals(400, update.first, update.second.toString())
        assertTrue("signing key changed" in error(update), update.second.toString())
        assertTrue("signing key changed" in call("GET", "/repos").second.jsonArray.single().str("error"))
        assertFalse(call("GET", "/extensions").second.jsonArray.single().jsonObject["updateAvailable"]!!.jsonPrimitive.boolean)
    }
}

package mangalist.extensionhost

import com.sun.net.httpserver.HttpServer
import okhttp3.OkHttpClient
import java.net.InetSocketAddress
import java.util.concurrent.ConcurrentHashMap
import kotlin.io.path.createTempDirectory
import kotlin.io.path.exists
import kotlin.test.AfterTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertNull
import kotlin.test.assertTrue

/** A static file server for repository indexes (`files[path] = body`; a missing path answers 404). */
class StaticServer : AutoCloseable {
    val files = ConcurrentHashMap<String, ByteArray>()
    private val server =
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

    val base: String get() = "http://127.0.0.1:${server.address.port}"

    operator fun set(
        path: String,
        text: String,
    ) {
        files[path] = text.toByteArray()
    }

    override fun close() = server.stop(0)
}

/** An `index.json` with [entries] (made with [repoEntry]). */
fun repoIndex(
    name: String,
    key: String?,
    vararg entries: String,
): String =
    """{"name": "$name"${if (key != null) """, "signingKey": "$key"""" else ""}, "extensionList": {"extensions": [${entries.joinToString(",")}]}}"""

fun repoEntry(
    pkg: String,
    jarUrl: String,
    code: Int,
    version: String,
) = """{"name": "$pkg", "packageName": "$pkg", "resources": {"apkUrl": "$jarUrl.apk", "jarUrl": "$jarUrl"},
    "extensionLib": "1.4", "versionCode": "$code", "versionName": "$version", "contentWarning": "CONTENT_WARNING_NONE",
    "sources": []}"""

/** Unit: the host's repository list - persistence, adding (checked first), removing, the signing key kept per repository. */
class RepoListTest {
    private val dir = createTempDirectory("repos")
    private val file = dir.resolve("repos.json")
    private val web = StaticServer()
    private val repo = Repo(OkHttpClient(), cacheMillis = 0)
    private val one = "${web.base}/one/index.json"
    private val two = "${web.base}/two/index.json"

    init {
        web["/one/index.json"] = repoIndex("One", "AA:BB", repoEntry("p.a", "${web.base}/a.jar", 1, "1.4.1"))
        web["/two/index.json"] = repoIndex("Two", "cc", repoEntry("p.a", "${web.base}/a2.jar", 3, "1.4.3"), repoEntry("p.b", "${web.base}/b.jar", 1, "1.4.1"))
        web["/nokey/index.json"] = repoIndex("No key", null, repoEntry("p.c", "${web.base}/c.jar", 1, "1.4.1"))
        web["/page.html"] = "<html><body>not a repository</body></html>"
        web["/other.json"] = """{"hello": "world"}"""
    }

    @AfterTest
    fun close() = web.close()

    @Test
    fun `the first start seeds the list with --repo, else keiyoushi's, and keeps it in repos json`() {
        val seeded = RepoList(file, repo, listOf(one))
        assertEquals(listOf(one), seeded.urls())
        assertTrue(file.exists())
        // a later start without --repo keeps the list; with a new --repo adds it
        assertEquals(listOf(one), RepoList(file, repo, emptyList()).urls())
        assertEquals(listOf(one, two), RepoList(file, repo, listOf(one, two)).urls())
        // no file and no --repo: keiyoushi's
        assertEquals(listOf(DEFAULT_REPO), RepoList(dir.resolve("fresh.json"), repo, emptyList()).urls())
    }

    @Test
    fun `adding fetches and checks the index first, then keeps its name, key and count`() {
        val list = RepoList(file, repo, listOf(one))
        val added = list.add(" $two ")
        assertEquals(two, added.url)
        assertEquals("Two", added.name)
        assertEquals("cc", added.signingKey)
        assertEquals(2, added.extensions)
        assertNull(added.error)
        assertEquals(listOf(one, two), RepoList(file, repo, emptyList()).urls()) // persisted
        assertEquals(two, list.add(two).url) // already there: answered as it is
        for (bad in listOf("${web.base}/page.html", "${web.base}/other.json", "${web.base}/missing.json", "${web.base}/nokey/index.json")) {
            val e = assertFailsWith<HttpError>(bad) { list.add(bad) }
            assertEquals(400, e.status, e.message)
        }
        assertEquals(400, assertFailsWith<HttpError> { list.add("ftp://example.org/index.json") }.status)
        assertEquals(listOf(one, two), list.urls())
    }

    @Test
    fun `removing forgets it for good, a later start does not bring it back`() {
        val list = RepoList(file, repo, listOf(one, two))
        assertEquals(two, list.remove(two).url)
        assertEquals(listOf(one), RepoList(file, repo, listOf(one)).urls())
        assertEquals(404, assertFailsWith<HttpError> { list.remove(two) }.status)
        assertEquals(404, assertFailsWith<HttpError> { list.index(two) }.status)
    }

    @Test
    fun `a --repo keeps the key of its first fetch, and an index that changes its key is not trusted`() {
        val list = RepoList(file, repo, listOf(one))
        assertNull(list.record(one)!!.signingKey)
        assertEquals("aabb", list.index(one).signingKey) // ':' and case ignored
        assertEquals("aabb", RepoList(file, repo, emptyList()).record(one)!!.signingKey) // kept
        assertEquals("One", list.record(one)!!.name)
        web["/one/index.json"] = repoIndex("One", "dd", repoEntry("p.a", "${web.base}/a.jar", 2, "1.4.2"))
        val e = assertFailsWith<HttpError> { list.index(one) }
        assertEquals(400, e.status)
        assertTrue("signing key changed" in e.message!!, e.message)
        assertTrue("signing key changed" in list.dtos(fetch = false).single().error!!)
        assertEquals(emptyList(), list.allExtensions()) // listed nowhere until trusted again
        list.remove(one)
        list.add(one)
        assertEquals("dd", list.record(one)!!.signingKey)
    }

    @Test
    fun `every repository's extensions, each saying where it is from - the same package in two is listed twice`() {
        val list = RepoList(file, repo, listOf(one, two))
        val all = list.allExtensions()
        assertEquals(listOf("p.a" to one, "p.a" to two, "p.b" to two), all.map { it.pkg to it.repo })
        assertEquals(listOf("One", "Two", "Two"), all.map { it.repoName })
        val dtos = list.dtos(fetch = false)
        assertEquals(listOf(1, 2), dtos.map { it.extensions })
        web.files.remove("/two/index.json")
        assertEquals(listOf("p.a"), list.allExtensions().map { it.pkg }) // one that fails is skipped ...
        assertTrue("404" in list.dtos().last().error!!) // ... and says why
    }
}

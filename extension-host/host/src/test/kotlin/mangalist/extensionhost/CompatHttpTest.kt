package mangalist.extensionhost

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.float
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.OkHttpClient
import java.net.URI
import java.net.http.HttpClient
import java.net.http.HttpRequest
import java.net.http.HttpResponse
import kotlin.io.path.createTempDirectory
import kotlin.io.path.exists
import kotlin.test.AfterTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

/** HTTP: the /compat calls MangaList makes, against an in-process server with a fake source. */
class CompatHttpTest {
    private val dir = createTempDirectory("compat")
    private val images = ImageServer()
    private val source = FakeSource(images.base)
    private val registry = ExtensionRegistry(dir.resolve("extensions"), OkHttpClient())
    private val store = Store(dir.resolve("host.db"))
    private val library = Library(store, { registry.sources[it] }, DownloadsLayout(dir.resolve("downloads")))
    private val downloader = Downloader(store, library, dir.resolve("cache"), retryDelayMs = 0)
    private val server = ApiServer("127.0.0.1", 0, registry, Repo(OkHttpClient()), CompatApi(library, downloader, registry))
    private val http = HttpClient.newHttpClient()

    init {
        source.mangas["/m/1"] = "Fragrant Flower"
        source.chapters["/m/1"] = listOf(chapter("/c/2", "Ch. 2", -1f, "Team"), chapter("/c/1", "Ch. 1.5 - Rain", -1f))
        source.pages["/c/2"] = 2
        source.pages["/c/1"] = 1
        registry.sources[source.id] = source
        server.start()
    }

    @AfterTest
    fun close() {
        server.stop()
        downloader.close()
        store.close()
        images.close()
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

    private fun ok(
        method: String,
        path: String,
        body: String? = null,
    ): JsonElement {
        val (status, json) = call(method, path, body)
        assertEquals(200, status, json.toString())
        return json
    }

    private val JsonElement.o: JsonObject get() = jsonObject

    private fun JsonElement.s(key: String) = jsonObject[key]!!.jsonPrimitive.content

    @Test
    fun `about, settings and sources`() {
        assertEquals("MangaList extension host", ok("GET", "/compat/about").s("name"))
        val settings = ok("GET", "/compat/settings").o
        assertTrue(settings["downloadAsCbz"]!!.jsonPrimitive.boolean)
        assertEquals(dir.resolve("downloads").toString(), settings["downloadsPath"]!!.jsonPrimitive.content)
        assertFalse(settings["flareSolverr"]!!.jsonPrimitive.boolean)
        val src = ok("GET", "/compat/sources").jsonArray.single()
        assertEquals(source.id.toString(), src.s("id"))
        assertEquals("Fake Source (EN)", src.s("displayName"))
        assertEquals("en", src.s("lang"))
    }

    @Test
    fun `search, chapters, library, download, chapters by id, delete`() {
        val search = ok("POST", "/compat/search", """{"sourceId": "${source.id}", "query": "fragrant", "page": 1}""").o
        assertFalse(search["hasNextPage"]!!.jsonPrimitive.boolean)
        val manga = search["mangas"]!!.jsonArray.single()
        val mangaId = manga.o["id"]!!.jsonPrimitive.int
        assertEquals(source.id.toString(), manga.s("sourceId"))
        assertEquals("/m/1", manga.s("url"))
        assertFalse(manga.o["inLibrary"]!!.jsonPrimitive.boolean)
        // the same manga, the same id
        assertEquals(mangaId, ok("POST", "/compat/search", """{"sourceId": "${source.id}", "query": "flower"}""").o["mangas"]!!.jsonArray.single().o["id"]!!.jsonPrimitive.int)

        val got = ok("POST", "/compat/manga/$mangaId/chapters", "").o
        assertEquals("Fake Source (EN)", got["sourceName"]!!.jsonPrimitive.content)
        assertEquals("Fragrant Flower", got["manga"]!!.s("title"))
        val chapters = got["chapters"]!!.jsonArray
        assertEquals(listOf("Ch. 2", "Ch. 1.5 - Rain"), chapters.map { it.s("name") })
        assertEquals(listOf(2f, 1.5f), chapters.map { it.o["chapterNumber"]!!.jsonPrimitive.float })
        assertEquals("Team", chapters[0].s("scanlator"))
        assertEquals("1700000000000", chapters[0].s("uploadDate"))
        assertEquals("${images.base}/c/2", chapters[0].s("realUrl"))
        assertEquals(mangaId, chapters[0].o["mangaId"]!!.jsonPrimitive.int)
        val ch2 = chapters[0].o["id"]!!.jsonPrimitive.int

        val lib = ok("POST", "/compat/manga/$mangaId/library").o
        assertTrue(lib["inLibrary"]!!.jsonPrimitive.boolean)

        val queued = ok("POST", "/compat/downloads/enqueue", """{"ids": [$ch2]}""").o
        assertTrue(queued["queue"]!!.jsonArray.any { it.o["chapterId"]!!.jsonPrimitive.int == ch2 } || queued["queue"]!!.jsonArray.isEmpty())
        waitFor(what = "the download") { ok("GET", "/compat/downloads").o["queue"]!!.jsonArray.isEmpty() }
        val byId = ok("POST", "/compat/chapters", """{"ids": [$ch2]}""").jsonArray.single()
        assertTrue(byId.o["downloaded"]!!.jsonPrimitive.boolean)
        val cbz = dir.resolve("downloads/mangas/Fake Source (EN)/Fragrant Flower/Team_Ch. 2.cbz")
        assertTrue(cbz.exists())

        assertEquals(JsonArray(listOf(Json.parseToJsonElement("$ch2"))), ok("POST", "/compat/chapters/delete-downloaded", """{"ids": [$ch2]}""").o["ids"])
        assertFalse(cbz.exists())
        assertFalse(ok("POST", "/compat/chapters", """{"ids": [$ch2]}""").jsonArray.single().o["downloaded"]!!.jsonPrimitive.boolean)
    }

    @Test
    fun `the queue answers with states and tries, dequeue takes a chapter out`() {
        val mangaId = ok("POST", "/compat/search", """{"sourceId": "${source.id}", "query": "fragrant"}""").o["mangas"]!!.jsonArray.single().o["id"]!!.jsonPrimitive.int
        val ch1 = ok("POST", "/compat/manga/$mangaId/chapters").o["chapters"]!!.jsonArray[1].o["id"]!!.jsonPrimitive.int
        images.failures.set(100)
        ok("POST", "/compat/downloads/enqueue", """{"ids": [$ch1]}""")
        waitFor(what = "ERROR") { ok("GET", "/compat/downloads").o["queue"]!!.jsonArray.singleOrNull()?.s("state") == "ERROR" }
        val entry = ok("GET", "/compat/downloads").o["queue"]!!.jsonArray.single().o
        assertEquals(3, entry["tries"]!!.jsonPrimitive.int)
        assertEquals(ch1, entry["chapterId"]!!.jsonPrimitive.int)
        val after = ok("POST", "/compat/downloads/dequeue", """{"ids": [$ch1]}""").o
        assertEquals(0, after["queue"]!!.jsonArray.size)
        assertEquals("STOPPED", after["state"]!!.jsonPrimitive.content)
    }

    @Test
    fun `errors are JSON with 400 and 404`() {
        val (s1, e1) = call("POST", "/compat/manga/424242/chapters")
        assertEquals(404, s1)
        assertEquals("HttpError", e1.s("type"))
        assertEquals(400, call("POST", "/compat/search", """{"query": "x"}""").first)
        assertEquals(404, call("POST", "/compat/search", """{"sourceId": "1", "query": "x"}""").first)
        assertEquals(400, call("POST", "/compat/downloads/enqueue", """{"ids": "x"}""").first)
        assertEquals(404, call("GET", "/compat/nothing").first)
        assertEquals(emptyList(), ok("POST", "/compat/chapters", """{"ids": [123456]}""").jsonArray.toList())
    }
}

package mangalist.extensionhost

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import java.net.URI
import java.net.http.HttpClient
import java.net.http.HttpRequest
import java.net.http.HttpResponse
import java.nio.file.Path
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import kotlin.io.path.createTempDirectory
import kotlin.test.Test
import kotlin.test.assertEquals

/** Process: the real host program (its boot, options and persistence) run as a separate JVM, stopped and started again. */
class HostProcessTest {
    private val data = createTempDirectory("host-data")
    private val downloads = createTempDirectory("host-downloads")
    private val http = HttpClient.newHttpClient()

    private class Host(
        val process: Process,
        val port: Int,
    )

    private fun start(): Host {
        val java = Path.of(System.getProperty("java.home"), "bin", "java").toString()
        val process =
            ProcessBuilder(
                java, "-cp", System.getProperty("java.class.path"), "mangalist.extensionhost.MainKt",
                "--data", data.toString(), "--downloads", downloads.toString(), "--port", "0", "--webview", "off",
            ).redirectErrorStream(true).start()
        val lines = LinkedBlockingQueue<String>()
        Thread {
            process.inputStream.bufferedReader().forEachLine { lines += it }
        }.apply { isDaemon = true }.start()
        val end = System.currentTimeMillis() + 60_000
        while (System.currentTimeMillis() < end) {
            val line = lines.poll(1, TimeUnit.SECONDS) ?: continue
            Regex("Listening on http://127.0.0.1:(\\d+)").find(line)?.let { return Host(process, it.groupValues[1].toInt()) }
        }
        process.destroyForcibly()
        throw AssertionError("the host did not start")
    }

    private fun Host.call(
        method: String,
        path: String,
        body: String? = null,
    ): JsonElement {
        val req =
            HttpRequest
                .newBuilder(URI("http://127.0.0.1:$port$path"))
                .method(method, if (body == null) HttpRequest.BodyPublishers.noBody() else HttpRequest.BodyPublishers.ofString(body))
                .build()
        return Json.parseToJsonElement(http.send(req, HttpResponse.BodyHandlers.ofString()).body())
    }

    private fun Host.stop() {
        process.destroy()
        if (!process.waitFor(20, TimeUnit.SECONDS)) process.destroyForcibly()
    }

    @Test
    fun `the host keeps ids and its queue across a restart`() {
        // a library the host made before (its source is not installed now)
        val ids =
            Store(data.resolve("host.db")).use { store ->
                val manga = store.insertManga(MangaRow(0, 42, "/m/1", "Kept Title"))
                val c1 = store.insertChapter(ChapterRow(0, manga.id, "/c/1", "Chapter 1", 1f, null, "https://x/c/1", 1, 1))
                val c2 = store.insertChapter(ChapterRow(0, manga.id, "/c/2", "Chapter 2", 2f, "Team", "https://x/c/2", 2, 2))
                store.saveQueue(listOf(QueueRow(c1, Downloader.ERROR, 3)))
                store.startedWith(HOST_VERSION) // made by this version (a new version would retry the ERROR: audit E3)
                listOf(c1, c2)
            }
        val instance = Store(data.resolve("host.db")).use { it.instance() }

        val first = start()
        try {
            val about = first.call("GET", "/compat/about").jsonObject
            assertEquals("MangaList extension host", about["name"]!!.jsonPrimitive.content)
            assertEquals(instance, about["instance"]!!.jsonPrimitive.content) // the database's id, kept
            val settings = first.call("GET", "/compat/settings").jsonObject
            assertEquals(downloads.toString(), settings["downloadsPath"]!!.jsonPrimitive.content)
            assertEquals("off", settings["webView"]!!.jsonPrimitive.content)
            val chapters = first.call("POST", "/compat/chapters", """{"ids": [${ids[0]}, ${ids[1]}]}""").jsonArray
            assertEquals(listOf("Chapter 1", "Chapter 2"), chapters.map { it.jsonObject["name"]!!.jsonPrimitive.content })
            val queue = first.call("GET", "/compat/downloads").jsonObject["queue"]!!.jsonArray.single().jsonObject
            assertEquals(ids[0], queue["chapterId"]!!.jsonPrimitive.int)
            assertEquals("ERROR", queue["state"]!!.jsonPrimitive.content)
            assertEquals(3, queue["tries"]!!.jsonPrimitive.int)
            // a chapter whose source is gone fails its three tries and waits in ERROR
            first.call("POST", "/compat/downloads/enqueue", """{"ids": [${ids[1]}]}""")
            waitFor(30_000, "ERROR") {
                first.call("GET", "/compat/downloads").jsonObject["queue"]!!.jsonArray.count { it.jsonObject["state"]!!.jsonPrimitive.content == "ERROR" } == 2
            }
        } finally {
            first.stop()
        }

        val second = start()
        try {
            val queue = second.call("GET", "/compat/downloads").jsonObject["queue"]!!.jsonArray.map { it.jsonObject }
            assertEquals(ids, queue.map { it["chapterId"]!!.jsonPrimitive.int })
            assertEquals(listOf(3, 3), queue.map { it["tries"]!!.jsonPrimitive.int })
            val again = second.call("POST", "/compat/chapters", """{"ids": [${ids[1]}]}""").jsonArray.single().jsonObject
            assertEquals("Team", again["scanlator"]!!.jsonPrimitive.content)
            assertEquals("STOPPED", second.call("GET", "/compat/downloads").jsonObject["state"]!!.jsonPrimitive.content)
        } finally {
            second.stop()
        }
    }
}

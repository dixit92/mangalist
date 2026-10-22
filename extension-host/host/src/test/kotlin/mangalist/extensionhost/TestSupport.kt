package mangalist.extensionhost

import com.sun.net.httpserver.HttpServer
import eu.kanade.tachiyomi.source.model.FilterList
import eu.kanade.tachiyomi.source.model.MangasPage
import eu.kanade.tachiyomi.source.model.Page
import eu.kanade.tachiyomi.source.model.SChapter
import eu.kanade.tachiyomi.source.model.SManga
import eu.kanade.tachiyomi.source.model.SMangaUpdate
import eu.kanade.tachiyomi.source.online.HttpSource
import kotlinx.coroutines.CompletableDeferred
import okhttp3.Headers
import okhttp3.OkHttpClient
import java.net.InetSocketAddress
import java.util.Base64
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicInteger

/** A 1x1 PNG. */
val PNG: ByteArray = Base64.getDecoder().decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")

/** A local image server: `/img/<anything>` answers [PNG] (as `image/png`), or HTTP 500 while [failures] is above 0. */
class ImageServer : AutoCloseable {
    val failures = AtomicInteger(0)
    val requests = AtomicInteger(0)
    val userAgents = ConcurrentHashMap.newKeySet<String>()
    private val server =
        HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0).apply {
            createContext("/img/") { ex ->
                requests.incrementAndGet()
                ex.requestHeaders.getFirst("User-Agent")?.let { userAgents += it }
                if (failures.get() > 0) {
                    failures.decrementAndGet()
                    ex.sendResponseHeaders(500, -1)
                } else {
                    ex.responseHeaders.add("Content-Type", "image/png")
                    ex.sendResponseHeaders(200, PNG.size.toLong())
                    ex.responseBody.use { it.write(PNG) }
                }
                ex.close()
            }
            start()
        }

    val base: String get() = "http://127.0.0.1:${server.address.port}"

    override fun close() = server.stop(0)
}

/**
 * A source for tests: an [HttpSource] (so the host's real image path is used: `getImage` with the source's client and
 * headers) whose catalogue lives in memory.
 */
open class FakeSource(
    override val baseUrl: String,
    override val name: String = "Fake Source",
    override val lang: String = "en",
) : HttpSource() {
    override val supportsLatest = false
    override val client: OkHttpClient = OkHttpClient()

    override fun headersBuilder(): Headers.Builder = Headers.Builder().add("User-Agent", "fake-source-agent")

    /** url -> title */
    val mangas = linkedMapOf<String, String>()

    /** What the details call answers: url -> title (null: the search's title). */
    val detailsTitles = mutableMapOf<String, String>()

    /** manga url -> chapters, newest first (as sources list them). */
    val chapters = mutableMapOf<String, List<SChapter>>()

    /** chapter url -> page count. */
    val pages = mutableMapOf<String, Int>()

    val detailCalls = AtomicInteger(0)
    val chapterCalls = AtomicInteger(0)

    /** When set, getPageList waits for it (a download that is "running"). */
    @Volatile var gate: CompletableDeferred<Unit>? = null

    override suspend fun getSearchManga(
        page: Int,
        query: String,
        filters: FilterList,
    ): MangasPage =
        MangasPage(
            mangas
                .filter { it.value.contains(query, ignoreCase = true) }
                .map { (u, t) ->
                    SManga.create().apply {
                        url = u
                        title = t
                    }
                },
            false,
        )

    override suspend fun getMangaUpdate(
        manga: SManga,
        chapters: List<SChapter>,
        fetchDetails: Boolean,
        fetchChapters: Boolean,
    ): SMangaUpdate {
        val m =
            if (fetchDetails) {
                detailCalls.incrementAndGet()
                SManga.create().apply {
                    url = manga.url
                    title = detailsTitles[manga.url] ?: manga.title
                    author = "An Author"
                    description = "A description"
                    status = SManga.ONGOING
                    initialized = true
                }
            } else {
                manga
            }
        val c =
            if (fetchChapters) {
                chapterCalls.incrementAndGet()
                this.chapters[manga.url].orEmpty().map { src ->
                    SChapter.create().apply {
                        url = src.url
                        name = src.name
                        chapter_number = src.chapter_number
                        scanlator = src.scanlator
                        date_upload = src.date_upload
                    }
                }
            } else {
                chapters
            }
        return SMangaUpdate(m, c)
    }

    override suspend fun getPageList(chapter: SChapter): List<Page> {
        gate?.await()
        val n = pages[chapter.url] ?: 0
        return (0 until n).map { Page(it, "", "$baseUrl/img/${chapter.url.trim('/').replace('/', '-')}-$it.png") }
    }

    override fun getChapterUrl(chapter: SChapter): String = baseUrl + chapter.url

    override fun getMangaUrl(manga: SManga): String = baseUrl + manga.url
}

fun chapter(
    url: String,
    name: String,
    number: Float = -1f,
    scanlator: String? = null,
    date: Long = 1_700_000_000_000,
): SChapter =
    SChapter.create().apply {
        this.url = url
        this.name = name
        chapter_number = number
        this.scanlator = scanlator
        date_upload = date
    }

/** Wait (up to [timeoutMs]) until [check] holds. */
fun waitFor(
    timeoutMs: Long = 15_000,
    what: String = "condition",
    check: () -> Boolean,
) {
    val end = System.currentTimeMillis() + timeoutMs
    while (System.currentTimeMillis() < end) {
        if (check()) return
        Thread.sleep(25)
    }
    throw AssertionError("timed out waiting for $what")
}

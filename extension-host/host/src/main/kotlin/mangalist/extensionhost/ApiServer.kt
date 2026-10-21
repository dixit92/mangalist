package mangalist.extensionhost

import com.sun.net.httpserver.HttpExchange
import com.sun.net.httpserver.HttpServer
import eu.kanade.tachiyomi.source.CatalogueSource
import eu.kanade.tachiyomi.source.Source
import eu.kanade.tachiyomi.source.model.Page
import eu.kanade.tachiyomi.source.model.SChapter
import eu.kanade.tachiyomi.source.model.SManga
import eu.kanade.tachiyomi.source.online.HttpSource
import eu.kanade.tachiyomi.util.chapter.ChapterRecognition
import io.github.oshai.kotlinlogging.KotlinLogging
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.encodeToJsonElement
import java.net.InetSocketAddress
import java.net.URLDecoder
import java.util.concurrent.Executors

private val logger = KotlinLogging.logger {}

// --- what MangaList reads (JSON; source ids are strings: they are 64-bit) ------------------------------------------

@Serializable
data class SourceDto(
    val id: String,
    val name: String,
    val lang: String,
    val baseUrl: String?,
    val supportsLatest: Boolean,
    val extension: String?,
)

@Serializable
data class ExtensionDto(
    val pkg: String,
    val name: String,
    val version: String,
    val libVersion: String,
    val nsfw: Int,
    val sources: List<SourceDto>,
)

@Serializable
data class MangaDto(
    val url: String,
    val title: String,
    val thumbnailUrl: String? = null,
    val author: String? = null,
    val artist: String? = null,
    val description: String? = null,
    val genre: String? = null,
    val status: Int = 0,
    val webUrl: String? = null,
)

@Serializable
data class MangasPageDto(
    val mangas: List<MangaDto>,
    val hasNextPage: Boolean,
)

@Serializable
data class ChapterDto(
    val url: String,
    val name: String,
    val chapterNumber: Float,
    val scanlator: String?,
    val dateUpload: Long,
    val webUrl: String? = null,
)

@Serializable
data class PageDto(
    val index: Int,
    val url: String,
    val imageUrl: String?,
)

@Serializable
data class InstallRequest(
    val pkg: String? = null,
    val repo: String = DEFAULT_REPO,
    val apkUrl: String? = null,
    /** "jar" (default: keiyoushi's ready jar when the index has one) or "apk" (convert the APK with dex2jar). */
    val format: String = "jar",
)

@Serializable
data class UrlRequest(
    val url: String,
    val title: String = "",
)

@Serializable
data class ImageRequest(
    val index: Int = 0,
    val url: String = "",
    val imageUrl: String? = null,
)

@Serializable
data class ErrorDto(
    val error: String,
    val type: String,
)

class HttpError(
    val status: Int,
    message: String,
) : Exception(message)

/**
 * The host's local API (JDK HttpServer; binds 127.0.0.1 by default - it has no login):
 *
 * - `GET  /health`
 * - `GET  /repo?url=<index url>` - the repository's extensions (default: keiyoushi's)
 * - `GET  /extensions`, `POST /extensions/install {pkg[, repo][, format: jar|apk] | apkUrl}`
 * - `GET  /sources`
 * - `GET  /sources/{id}/popular?page=1`, `GET /sources/{id}/latest?page=1`, `GET /sources/{id}/search?q=..&page=1`
 * - `POST /sources/{id}/manga {url}` (details), `POST /sources/{id}/chapters {url}`
 * - `POST /sources/{id}/pages {url}[?resolve=true]` (resolve: ask the source for every page's image URL)
 * - `POST /sources/{id}/image {index, url, imageUrl}` - the image's bytes, with the source's headers
 */
class ApiServer(
    private val bind: String,
    private val requestedPort: Int,
    private val registry: ExtensionRegistry,
    private val repo: Repo,
) {
    private val json =
        Json {
            ignoreUnknownKeys = true
            encodeDefaults = true
            explicitNulls = true
        }
    private lateinit var server: HttpServer

    val port: Int
        get() = server.address.port

    fun start() {
        server = HttpServer.create(InetSocketAddress(bind, requestedPort), 0)
        server.executor = Executors.newFixedThreadPool(8)
        server.createContext("/") { exchange -> handle(exchange) }
        server.start()
    }

    fun stop() = server.stop(0)

    private fun handle(ex: HttpExchange) {
        val method = ex.requestMethod
        val path = ex.requestURI.path.trimEnd('/')
        val query = parseQuery(ex.requestURI.rawQuery)
        try {
            val parts = path.split('/').filter { it.isNotEmpty() }
            when {
                method == "GET" && path == "/health" ->
                    reply(ex, json.encodeToJsonElement(mapOf("ok" to "true", "version" to HOST_VERSION, "sources" to registry.sources.size.toString())))
                method == "GET" && path == "/repo" ->
                    reply(ex, json.encodeToJsonElement(repo.index(query["url"] ?: DEFAULT_REPO)))
                method == "GET" && path == "/extensions" ->
                    reply(ex, json.encodeToJsonElement(registry.installed.values.sortedBy { it.pkg }.map { extensionDto(it) }))
                method == "POST" && path == "/extensions/install" -> {
                    val req = json.decodeFromString<InstallRequest>(body(ex))
                    val url =
                        req.apkUrl ?: run {
                            val pkg = req.pkg ?: throw HttpError(400, "give pkg (with repo) or apkUrl")
                            val entry =
                                repo.index(req.repo).firstOrNull { it.pkg == pkg }
                                    ?: throw HttpError(404, "$pkg is not in ${req.repo}")
                            if (req.format == "apk") entry.apkUrl else entry.jarUrl ?: entry.apkUrl
                        }
                    val (ext, _) = registry.install(url)
                    reply(ex, json.encodeToJsonElement(extensionDto(ext)))
                }
                method == "GET" && path == "/sources" ->
                    reply(ex, json.encodeToJsonElement(registry.sources.values.sortedBy { it.name }.map { sourceDto(it) }))
                parts.size == 3 && parts[0] == "sources" -> sourceCall(ex, method, source(parts[1]), parts[2], query)
                else -> throw HttpError(404, "no such endpoint: $method $path")
            }
        } catch (e: HttpError) {
            replyError(ex, e.status, e)
        } catch (e: Throwable) {
            logger.warn(e) { "$method $path failed" }
            replyError(ex, 502, e)
        } finally {
            ex.close()
        }
    }

    private fun sourceCall(
        ex: HttpExchange,
        method: String,
        source: Source,
        action: String,
        query: Map<String, String>,
    ) = runBlocking {
        val page = query["page"]?.toIntOrNull() ?: 1
        when {
            method == "GET" && action == "popular" -> reply(ex, json.encodeToJsonElement(mangasPage(catalogue(source).getPopularManga(page), source)))
            method == "GET" && action == "latest" -> reply(ex, json.encodeToJsonElement(mangasPage(catalogue(source).getLatestUpdates(page), source)))
            method == "GET" && action == "search" -> {
                val q = query["q"] ?: throw HttpError(400, "give q")
                val found = catalogue(source).getSearchManga(page, q, source.getFilterList())
                reply(ex, json.encodeToJsonElement(mangasPage(found, source)))
            }
            method == "POST" && action == "manga" -> {
                val req = json.decodeFromString<UrlRequest>(body(ex))
                val update = source.getMangaUpdate(manga(req), emptyList(), fetchDetails = true, fetchChapters = false)
                reply(ex, json.encodeToJsonElement(mangaDto(update.manga.also { if (it.url.isEmpty()) it.url = req.url }, source)))
            }
            method == "POST" && action == "chapters" -> {
                val req = json.decodeFromString<UrlRequest>(body(ex))
                val update = source.getMangaUpdate(manga(req), emptyList(), fetchDetails = false, fetchChapters = true)
                reply(ex, json.encodeToJsonElement(update.chapters.map { chapterDto(it, source, req.title) }))
            }
            method == "POST" && action == "pages" -> {
                val req = json.decodeFromString<UrlRequest>(body(ex))
                val chapter = SChapter.create().apply { url = req.url; name = req.title }
                val pages = source.getPageList(chapter)
                if (query["resolve"] == "true" && source is HttpSource) {
                    pages.filter { it.imageUrl.isNullOrEmpty() }.forEach { it.imageUrl = source.getImageUrl(it) }
                }
                reply(ex, json.encodeToJsonElement(pages.map { PageDto(it.index, it.url, it.imageUrl) }))
            }
            method == "POST" && action == "image" -> {
                val http = source as? HttpSource ?: throw HttpError(400, "${source.name} serves no images over HTTP")
                val req = json.decodeFromString<ImageRequest>(body(ex))
                val page = Page(req.index, req.url, req.imageUrl)
                if (page.imageUrl.isNullOrEmpty()) page.imageUrl = http.getImageUrl(page)
                http.getImage(page).use { resp ->
                    val bytes = resp.body.bytes()
                    ex.responseHeaders.add("Content-Type", resp.header("Content-Type") ?: "application/octet-stream")
                    ex.sendResponseHeaders(200, bytes.size.toLong())
                    ex.responseBody.use { it.write(bytes) }
                }
            }
            else -> throw HttpError(404, "no such source call: $method $action")
        }
    }

    // --- helpers ---------------------------------------------------------------------------------------------------

    private fun source(id: String): Source {
        val key = id.toLongOrNull() ?: throw HttpError(400, "source id must be a number: $id")
        return registry.sources[key] ?: throw HttpError(404, "no installed source $id")
    }

    private fun catalogue(source: Source): CatalogueSource =
        source as? CatalogueSource ?: throw HttpError(400, "${source.name} cannot be searched")

    private fun manga(req: UrlRequest): SManga =
        SManga.create().apply {
            url = req.url
            title = req.title
        }

    private fun sourceDto(source: Source) =
        SourceDto(
            id = source.id.toString(),
            name = source.name,
            lang = source.lang,
            baseUrl = (source as? HttpSource)?.baseUrl,
            supportsLatest = (source as? CatalogueSource)?.supportsLatest ?: false,
            extension = registry.extensionOf(source.id)?.pkg,
        )

    private fun extensionDto(ext: InstalledExtension) =
        ExtensionDto(
            pkg = ext.pkg,
            name = ext.name,
            version = ext.version,
            libVersion = ext.libVersion,
            nsfw = ext.nsfw,
            sources = registry.sources.values.filter { registry.extensionOf(it.id)?.pkg == ext.pkg }.map { sourceDto(it) },
        )

    private fun mangasPage(
        page: eu.kanade.tachiyomi.source.model.MangasPage,
        source: Source,
    ) = MangasPageDto(page.mangas.map { mangaDto(it, source) }, page.hasNextPage)

    private fun mangaDto(
        m: SManga,
        source: Source,
    ) = MangaDto(
        url = m.url,
        title = runCatching { m.title }.getOrDefault(""),
        thumbnailUrl = m.thumbnail_url,
        author = m.author,
        artist = m.artist,
        description = m.description,
        genre = m.genre,
        status = m.status,
        webUrl = runCatching { (source as? HttpSource)?.getMangaUrl(m) }.getOrNull(),
    )

    /** As Suwayomi: a chapter number the source leaves unknown (-1) is read from the chapter's name. */
    private fun chapterDto(
        c: SChapter,
        source: Source,
        mangaTitle: String,
    ) = ChapterDto(
        url = c.url,
        name = c.name,
        chapterNumber = ChapterRecognition.parseChapterNumber(mangaTitle, c.name, c.chapter_number.toDouble()).toFloat(),
        scanlator = c.scanlator,
        dateUpload = c.date_upload,
        webUrl = runCatching { (source as? HttpSource)?.getChapterUrl(c) }.getOrNull(),
    )

    private fun body(ex: HttpExchange): String = ex.requestBody.use { String(it.readBytes(), Charsets.UTF_8) }

    private fun parseQuery(raw: String?): Map<String, String> =
        raw
            ?.split('&')
            ?.filter { it.isNotEmpty() }
            ?.associate {
                val k = it.substringBefore('=')
                val v = it.substringAfter('=', "")
                URLDecoder.decode(k, Charsets.UTF_8) to URLDecoder.decode(v, Charsets.UTF_8)
            }.orEmpty()

    private fun reply(
        ex: HttpExchange,
        element: JsonElement,
        status: Int = 200,
    ) {
        val bytes = json.encodeToString(JsonElement.serializer(), element).toByteArray(Charsets.UTF_8)
        ex.responseHeaders.add("Content-Type", "application/json; charset=utf-8")
        ex.sendResponseHeaders(status, bytes.size.toLong())
        ex.responseBody.use { it.write(bytes) }
    }

    private fun replyError(
        ex: HttpExchange,
        status: Int,
        e: Throwable,
    ) {
        runCatching {
            reply(ex, json.encodeToJsonElement(ErrorDto(e.message ?: e.javaClass.simpleName, e.javaClass.simpleName)), status)
        }
    }
}

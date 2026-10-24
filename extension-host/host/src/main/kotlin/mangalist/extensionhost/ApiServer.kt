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
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import suwayomi.tachidesk.server.serverConfig
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
    val versionCode: Int,
    val libVersion: String,
    val nsfw: Int,
    /** SHA-256 of its signing certificate, checked against the repository's key at install (null: installed before). */
    val signatureHash: String?,
    /** The repository's current version (null: not in the repository, or the repository did not answer). */
    val repoVersion: String?,
    val repoVersionCode: Int?,
    val updateAvailable: Boolean,
    val sources: List<SourceDto>,
    /** The repository it was installed from (where its updates come from; it gets none once that one is removed). */
    val repo: String? = null,
    val repoName: String? = null,
    /** False when [repo] is no longer in the host's repository list (no updates). */
    val repoListed: Boolean = true,
)

@Serializable
data class PkgRequest(
    val pkg: String,
    /** update: only the repository it was installed from is accepted (default: that one). */
    val repo: String? = null,
    /** update / uninstall: how long to wait for a chapter of its sources that is downloading (else HTTP 409). */
    val waitSeconds: Int = 0,
)

@Serializable
data class PreferenceChange(
    val key: String,
    val value: JsonElement,
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
    /** The repository's index, one of the host's list (default: the first one listing [pkg]). */
    val repo: String? = null,
    val apkUrl: String? = null,
    /** "jar" (default: keiyoushi's ready jar when the index has one) or "apk" (convert the APK with dex2jar). */
    val format: String = "jar",
    /** How long to wait for a chapter of its sources that is downloading, when it is installed (else HTTP 409). */
    val waitSeconds: Int = 0,
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
 * - `GET  /repos`, `POST /repos {url}`, `DELETE /repos {url}` - the repository list ([RepoList])
 * - `GET  /repo[?url=<index url>]` - every listed repository's extensions (or one repository's), each with its `repo`
 * - `GET  /extensions`, `POST /extensions/install {pkg[, repo][, format: jar|apk] | apkUrl}`,
 *   `POST /extensions/update {pkg[, waitSeconds]}`, `POST /extensions/uninstall {pkg[, waitSeconds]}`
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
    private val compat: CompatApi? = null,
    private val defaultRepo: String = DEFAULT_REPO,
    private val preferences: SourcePreferences? = null,
    repoList: RepoList? = null,
    /** The download queue: an extension is updated or removed only while none of its sources downloads. */
    private val downloader: Downloader? = null,
) {
    val repos: RepoList = repoList ?: RepoList(null, repo, listOf(defaultRepo))

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
                    reply(
                        ex,
                        json.encodeToJsonElement(
                            mapOf(
                                "ok" to "true",
                                "version" to HOST_VERSION,
                                "sources" to registry.sources.size.toString(),
                                "flareSolverr" to if (serverConfig.flareSolverrEnabled.value) serverConfig.flareSolverrUrl.value else "off",
                                "webView" to WebViewSetup.state(),
                            ),
                        ),
                    )
                method == "GET" && path == "/webview" -> reply(ex, json.encodeToJsonElement(mapOf("state" to WebViewSetup.state())))
                method == "POST" && path == "/webview/start" -> {
                    if (!serverConfig.kcefEnabled.value) throw HttpError(400, "the WebView is off (--webview off)")
                    WebViewSetup.start()
                    reply(ex, json.encodeToJsonElement(mapOf("state" to WebViewSetup.state())))
                }
                method == "POST" && path == "/webview/debug-eval" -> {
                    val req = json.parseToJsonElement(body(ex)).jsonObject
                    val url = req["url"]?.jsonPrimitive?.content ?: throw HttpError(400, "give url")
                    val script = req["script"]?.jsonPrimitive?.content ?: throw HttpError(400, "give script")
                    val wait = req["waitMs"]?.jsonPrimitive?.content?.toLongOrNull() ?: 5000
                    reply(ex, json.encodeToJsonElement(mapOf("result" to WebViewSetup.debugEval(url, script, wait))))
                }
                method == "GET" && path == "/repos" -> reply(ex, json.encodeToJsonElement(repos.dtos()))
                method == "POST" && path == "/repos" -> reply(ex, json.encodeToJsonElement(repos.add(repoUrl(ex, query))))
                (method == "DELETE" && path == "/repos") || (method == "POST" && path == "/repos/remove") ->
                    reply(ex, json.encodeToJsonElement(repos.remove(repoUrl(ex, query))))
                method == "GET" && path == "/repo" -> {
                    val url = query["url"]
                    val list =
                        when {
                            url == null -> repos.allExtensions()
                            url in repos -> repos.index(url, fresh = true).extensions
                            else -> repo.fetch(url, fresh = true).extensions // a look at a repository not listed
                        }
                    reply(ex, json.encodeToJsonElement(list))
                }
                method == "GET" && path == "/extensions" -> reply(ex, json.encodeToJsonElement(extensions()))
                method == "POST" && path == "/extensions/install" -> {
                    val req = json.decodeFromString<InstallRequest>(body(ex))
                    val repoUrl =
                        req.repo?.trim()?.takeIf { it.isNotEmpty() }
                            ?: req.pkg?.let { pkg -> repos.urls().firstOrNull { u -> runCatching { repos.index(u) }.getOrNull()?.extensions?.any { it.pkg == pkg } == true } }
                            ?: repos.first()
                            ?: throw HttpError(400, "the host has no repository: add one first")
                    if (repoUrl !in repos) throw HttpError(400, "$repoUrl is not in the repository list: add it first")
                    val index = repos.index(repoUrl, fresh = true)
                    val url =
                        req.apkUrl ?: run {
                            val pkg = req.pkg ?: throw HttpError(400, "give pkg (with repo) or apkUrl")
                            val entry =
                                index.extensions.firstOrNull { it.pkg == pkg }
                                    ?: throw HttpError(404, "$pkg is not in ${index.name} ($repoUrl)")
                            if (req.format == "apk") entry.apkUrl else entry.jarUrl ?: entry.apkUrl
                        }
                    val current = req.pkg?.let { registry.installed[it] }
                    if (current != null) {
                        val from = current.repo ?: defaultRepo
                        if (from != repoUrl) {
                            throw HttpError(
                                409,
                                "${current.name} is installed from ${repoName(from)}; it updates only from there - uninstall it first to take it from ${index.name}",
                            )
                        }
                    }
                    val ext =
                        guarded(current?.pkg, current?.name, req.waitSeconds) {
                            install(url, index.signingKey, repoUrl).first
                        }
                    reply(ex, json.encodeToJsonElement(extensionDto(ext, index)))
                }
                method == "POST" && path == "/extensions/update" -> {
                    val req = json.decodeFromString<PkgRequest>(body(ex))
                    val pkg = req.pkg
                    val current = registry.installed[pkg] ?: throw HttpError(404, "$pkg is not installed")
                    val repoUrl = current.repo ?: defaultRepo
                    if (req.repo != null && req.repo.trim() != repoUrl) {
                        throw HttpError(400, "${current.name} was installed from ${repoName(repoUrl)}: it updates only from there")
                    }
                    if (repoUrl !in repos) {
                        throw HttpError(409, "${current.name}'s repository ($repoUrl) is no longer in the list: it gets no updates")
                    }
                    val index = repos.index(repoUrl, fresh = true)
                    val entry = index.extensions.firstOrNull { it.pkg == pkg } ?: throw HttpError(404, "$pkg is not in ${index.name} ($repoUrl)")
                    val ext =
                        if (entry.versionCode > current.versionCode) {
                            guarded(pkg, current.name, req.waitSeconds) {
                                install(entry.jarUrl ?: entry.apkUrl, index.signingKey, repoUrl).first
                            }.also { logger.info { "Updated ${it.name} ${current.version} -> ${it.version} from ${index.name}" } }
                        } else {
                            current
                        }
                    reply(ex, json.encodeToJsonElement(extensionDto(ext, index)))
                }
                method == "POST" && path == "/extensions/uninstall" -> {
                    val req = json.decodeFromString<PkgRequest>(body(ex))
                    val current = registry.installed[req.pkg] ?: throw HttpError(404, "${req.pkg} is not installed")
                    val ext = guarded(req.pkg, current.name, req.waitSeconds) { registry.uninstall(req.pkg) }
                    reply(ex, json.encodeToJsonElement(extensionDto(ext, null)))
                }
                method == "GET" && path == "/sources" ->
                    reply(ex, json.encodeToJsonElement(registry.sources.values.sortedBy { it.name }.map { sourceDto(it) }))
                parts.firstOrNull() == "compat" && compat != null ->
                    reply(ex, compat.handle(method, parts.drop(1)) { body(ex) } ?: throw HttpError(404, "no such endpoint: $method $path"))
                parts.size == 3 && parts[0] == "sources" && parts[2] == "preferences" -> {
                    val prefs = preferences ?: throw HttpError(404, "no preferences here")
                    val src = source(parts[1])
                    when (method) {
                        "GET" -> reply(ex, json.encodeToJsonElement(prefs.list(src)))
                        "POST" -> {
                            val change = json.decodeFromString<PreferenceChange>(body(ex))
                            prefs.set(src, change.key, change.value)
                            // as Suwayomi (unregisterSource): the extension's sources are made again with the new value
                            val pkg = registry.extensionOf(src.id)?.pkg
                            if (pkg != null) registry.reloadPackage(pkg)
                            // the whole screen again, from the new source (a change can show / hide others)
                            reply(ex, json.encodeToJsonElement(prefs.list(registry.sources[src.id] ?: src)))
                        }
                        else -> throw HttpError(404, "no such endpoint: $method $path")
                    }
                }
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

    private fun install(
        url: String,
        signingKey: String?,
        repoUrl: String,
    ) = try {
        registry.install(url, signingKey, repoUrl)
    } catch (e: SignatureException) {
        throw HttpError(400, e.message ?: "signature check failed")
    }

    /** Run [block] while none of [pkg]'s sources downloads a chapter (see [Downloader.whileIdle]); HTTP 409 when one still does. */
    private fun <T> guarded(
        pkg: String?,
        name: String?,
        waitSeconds: Int,
        block: () -> T,
    ): T {
        val queue = downloader ?: return block()
        if (pkg == null) return block()
        return try {
            queue.whileIdle(registry.sourceIds(pkg), name ?: pkg, waitSeconds.coerceIn(0, 600) * 1000L, block)
        } catch (e: Downloader.ExtensionBusyException) {
            throw HttpError(409, e.message ?: "busy")
        }
    }

    private fun repoUrl(
        ex: HttpExchange,
        query: Map<String, String>,
    ): String =
        query["url"] ?: body(ex).takeIf { it.isNotBlank() }?.let { json.decodeFromString<RepoUrlRequest>(it).url }
            ?: throw HttpError(400, "give url")

    private fun repoName(url: String): String = repos.record(url)?.name?.takeIf { it.isNotBlank() } ?: Repo.fallbackName(url)

    /**
     * Installed extensions with the versions of the repository each was installed from (a repository that does not
     * answer, or that is no longer listed: no update shown).
     */
    private fun extensions(): List<ExtensionDto> {
        val indexes =
            registry.installed.values
                .map { it.repo ?: defaultRepo }
                .distinct()
                .filter { it in repos }
                .associateWith { url -> runCatching { repos.index(url) }.onFailure { logger.warn { "Repository $url: ${it.message}" } }.getOrNull() }
        return registry.installed.values.sortedBy { it.pkg }.map { extensionDto(it, indexes[it.repo ?: defaultRepo]) }
    }

    private fun extensionDto(
        ext: InstalledExtension,
        index: RepoIndex?,
    ): ExtensionDto {
        val from = ext.repo ?: defaultRepo
        val entry = index?.takeIf { it.url == from }?.extensions?.firstOrNull { it.pkg == ext.pkg }
        return ExtensionDto(
            pkg = ext.pkg,
            name = ext.name,
            version = ext.version,
            versionCode = ext.versionCode,
            libVersion = ext.libVersion,
            nsfw = ext.nsfw,
            signatureHash = ext.signatureHash,
            repoVersion = entry?.version,
            repoVersionCode = entry?.versionCode,
            updateAvailable = entry != null && entry.versionCode > ext.versionCode,
            sources = registry.sources.values.filter { registry.extensionOf(it.id)?.pkg == ext.pkg }.map { sourceDto(it) },
            repo = from,
            repoName = repoName(from),
            repoListed = from in repos,
        )
    }

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

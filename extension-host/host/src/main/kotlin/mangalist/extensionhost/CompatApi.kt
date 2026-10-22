package mangalist.extensionhost

import eu.kanade.tachiyomi.source.CatalogueSource
import eu.kanade.tachiyomi.source.ConfigurableSource
import eu.kanade.tachiyomi.source.online.HttpSource
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.encodeToJsonElement
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import suwayomi.tachidesk.server.serverConfig

// --- what /compat answers (COMPAT.md; Suwayomi v2.4.2366's GraphQL answers MangaList reads, as plain JSON) ---------

@Serializable
data class CompatSource(
    val id: String,
    val name: String,
    val displayName: String,
    val lang: String,
    val extension: String?,
    val supportsLatest: Boolean,
    val isConfigurable: Boolean,
    val baseUrl: String?,
)

@Serializable
data class CompatManga(
    val id: Int,
    val title: String,
    val url: String,
    val sourceId: String,
    val inLibrary: Boolean,
    val initialized: Boolean,
    val thumbnailUrl: String?,
)

@Serializable
data class CompatChapter(
    val id: Int,
    val mangaId: Int,
    val name: String,
    val chapterNumber: Float,
    val scanlator: String?,
    val url: String,
    val realUrl: String?,
    val uploadDate: String,
    val downloaded: Boolean,
    val sourceOrder: Int,
    val pageCount: Int,
)

@Serializable
data class CompatSearch(
    val mangas: List<CompatManga>,
    val hasNextPage: Boolean,
)

@Serializable
data class CompatMangaChapters(
    val manga: CompatManga,
    val sourceName: String,
    val chapters: List<CompatChapter>,
)

@Serializable
data class CompatQueueEntry(
    val chapterId: Int,
    val state: String,
    val progress: Float,
    val tries: Int,
)

@Serializable
data class CompatQueue(
    val state: String,
    val queue: List<CompatQueueEntry>,
)

@Serializable
data class CompatAbout(
    val name: String,
    val version: String,
)

@Serializable
data class CompatSettings(
    val version: String,
    val downloadAsCbz: Boolean,
    val downloadsPath: String,
    val flareSolverr: Boolean,
    val flareSolverrUrl: String,
    val webView: String,
)

@Serializable
data class CompatLibrary(
    val id: Int,
    val inLibrary: Boolean,
)

@Serializable
data class CompatIds(
    val ids: List<Int>,
)

/**
 * `/compat/...`: the calls MangaList made to Suwayomi, answered by the host (contract: `extension-host/COMPAT.md`).
 * [webView]: what `/compat/settings` says about the browser engine (`on`, `off`, or `failed: ...`).
 */
class CompatApi(
    private val library: Library,
    private val downloader: Downloader,
    private val registry: ExtensionRegistry,
    private val webView: () -> String = { "off" },
) {
    private val json =
        Json {
            ignoreUnknownKeys = true
            encodeDefaults = true
            explicitNulls = true
        }

    fun sources(): List<CompatSource> =
        registry.sources.values.sortedBy { it.toString() }.map { s ->
            CompatSource(
                id = s.id.toString(),
                name = s.name,
                displayName = s.toString(),
                lang = s.lang,
                extension = registry.extensionOf(s.id)?.pkg,
                supportsLatest = (s as? CatalogueSource)?.supportsLatest ?: false,
                isConfigurable = s is ConfigurableSource,
                baseUrl = runCatching { (s as? HttpSource)?.baseUrl }.getOrNull(),
            )
        }

    private fun manga(m: MangaRow) =
        CompatManga(m.id, m.title, m.url, m.sourceId.toString(), m.inLibrary, m.initialized, m.thumbnailUrl)

    private fun chapter(c: ChapterRow) =
        CompatChapter(
            id = c.id,
            mangaId = c.mangaId,
            name = c.name,
            chapterNumber = c.chapterNumber,
            scanlator = c.scanlator,
            url = c.url,
            realUrl = c.realUrl,
            uploadDate = c.uploadDate.toString(),
            downloaded = c.downloaded,
            sourceOrder = c.sourceOrder,
            pageCount = c.pageCount,
        )

    private fun queue(s: QueueStatus) = CompatQueue(s.state, s.queue.map { CompatQueueEntry(it.chapterId, it.state, it.progress, it.tries) })

    private fun obj(body: String): JsonObject =
        if (body.isBlank()) {
            JsonObject(emptyMap())
        } else {
            runCatching { json.parseToJsonElement(body).jsonObject }.getOrElse { throw HttpError(400, "the body must be a JSON object") }
        }

    private fun ids(body: String): List<Int> {
        val arr = obj(body)["ids"] ?: throw HttpError(400, "give ids")
        return runCatching { arr.jsonArray.map { it.jsonPrimitive.intOrNull ?: it.jsonPrimitive.content.toInt() } }
            .getOrElse { throw HttpError(400, "ids must be a list of integers") }
    }

    private fun id(text: String): Int = text.toIntOrNull() ?: throw HttpError(400, "not an id: $text")

    /** Answer `/compat/<parts...>`; null when no such call. */
    fun handle(
        method: String,
        parts: List<String>,
        body: () -> String,
    ): JsonElement? =
        runBlocking {
            when {
                method == "GET" && parts == listOf("about") -> json.encodeToJsonElement(CompatAbout("MangaList extension host", HOST_VERSION))
                method == "GET" && parts == listOf("settings") ->
                    json.encodeToJsonElement(
                        CompatSettings(
                            version = HOST_VERSION,
                            downloadAsCbz = true,
                            downloadsPath = library.layout.root.toAbsolutePath().toString(),
                            flareSolverr = serverConfig.flareSolverrEnabled.value,
                            flareSolverrUrl = serverConfig.flareSolverrUrl.value,
                            webView = webView(),
                        ),
                    )
                method == "GET" && parts == listOf("sources") -> json.encodeToJsonElement(sources())
                method == "POST" && parts == listOf("search") -> {
                    val req = obj(body())
                    val sourceId =
                        (req["sourceId"] as? JsonPrimitive)?.contentOrNull?.toLongOrNull()
                            ?: throw HttpError(400, "give sourceId (the source's id, a string)")
                    val query = (req["query"] as? JsonPrimitive)?.contentOrNull ?: ""
                    val page = (req["page"] as? JsonPrimitive)?.intOrNull ?: 1
                    val (mangas, hasNext) = library.search(sourceId, query, page)
                    json.encodeToJsonElement(CompatSearch(mangas.map { manga(it) }, hasNext))
                }
                method == "POST" && parts.size == 3 && parts[0] == "manga" && parts[2] == "chapters" -> {
                    val fetchDetails = (obj(body())["fetchDetails"] as? JsonPrimitive)?.booleanOrNull ?: false
                    val got = library.chapters(id(parts[1]), fetchDetails)
                    json.encodeToJsonElement(CompatMangaChapters(manga(got.manga), got.sourceName, got.chapters.map { chapter(it) }))
                }
                method == "POST" && parts.size == 3 && parts[0] == "manga" && parts[2] == "library" -> {
                    val m = library.addToLibrary(id(parts[1]))
                    json.encodeToJsonElement(CompatLibrary(m.id, m.inLibrary))
                }
                method == "GET" && parts == listOf("downloads") -> json.encodeToJsonElement(queue(downloader.status()))
                method == "POST" && parts == listOf("downloads", "enqueue") -> json.encodeToJsonElement(queue(downloader.enqueue(ids(body()))))
                method == "POST" && parts == listOf("downloads", "dequeue") -> json.encodeToJsonElement(queue(downloader.dequeue(ids(body()))))
                method == "POST" && parts == listOf("chapters") ->
                    json.encodeToJsonElement(library.chaptersById(ids(body())).map { chapter(it) })
                method == "POST" && parts == listOf("chapters", "delete-downloaded") ->
                    json.encodeToJsonElement(CompatIds(library.deleteDownloaded(ids(body()))))
                else -> null
            }
        }
}

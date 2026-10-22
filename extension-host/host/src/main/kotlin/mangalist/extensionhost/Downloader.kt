package mangalist.extensionhost

import eu.kanade.tachiyomi.source.model.SChapter
import eu.kanade.tachiyomi.source.online.HttpSource
import io.github.oshai.kotlinlogging.KotlinLogging
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.async
import kotlinx.coroutines.cancel
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import java.nio.file.Path
import kotlin.io.path.exists

private val logger = KotlinLogging.logger {}

/** One entry of the download queue as `/compat/downloads` shows it. */
data class QueueEntry(
    val chapterId: Int,
    val state: String,
    val progress: Float,
    val tries: Int,
)

data class QueueStatus(
    val state: String,
    val queue: List<QueueEntry>,
)

/**
 * The chapter downloader, with Suwayomi v2.4.2366's semantics (`DownloadManager` + `Downloader`):
 *
 * - a queue of chapters, persisted in the [Store] (it survives a restart; a chapter that was downloading is queued
 *   again; one in `ERROR` stays so, with its tries);
 * - one download at a time per source and at most [parallel] sources at once (Suwayomi's `maxSourcesInParallel`);
 * - a chapter is fetched page by page with its source's own client and headers (`HttpSource.getImage`), the pages kept
 *   in a cache folder between tries, then written as one CBZ ([ChapterArchive]); a finished chapter leaves the queue
 *   and reads downloaded;
 * - a failing chapter is tried again until it has failed [maxTries] times (3), then stays in the queue as `ERROR`; a
 *   chapter with no pages goes to `ERROR` at once; enqueueing an `ERROR` chapter again gives it one more try.
 */
class Downloader(
    private val store: Store,
    private val library: Library,
    private val cacheRoot: Path,
    private val parallel: Int = 2,
    private val maxTries: Int = 3,
    private val retryDelayMs: Long = 2_000,
) : AutoCloseable {
    companion object {
        const val QUEUED = "QUEUED"
        const val DOWNLOADING = "DOWNLOADING"
        const val ERROR = "ERROR"
        const val STARTED = "STARTED"
        const val STOPPED = "STOPPED"
    }

    class EmptyChapterException : Exception("Chapter does not have any pages to download")

    private class Item(
        val chapterId: Int,
        val sourceId: Long,
        @Volatile var state: String,
        @Volatile var tries: Int,
    ) {
        @Volatile var progress: Float = 0f

        @Volatile var job: Job? = null
    }

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val lock = Any()
    private val queue = mutableListOf<Item>()
    private val runners = mutableMapOf<Long, Job>()

    init {
        library.onChaptersRemoved = { dequeue(it) }
    }

    /** Load the persisted queue and start downloading. */
    fun restore() {
        val rows = store.loadQueue()
        synchronized(lock) {
            for (row in rows) {
                val chapter = store.chapter(row.chapterId) ?: continue
                val manga = store.manga(chapter.mangaId) ?: continue
                val state = if (row.state == ERROR) ERROR else QUEUED
                queue += Item(row.chapterId, manga.sourceId, state, row.tries)
            }
            persist()
        }
        if (queue.isNotEmpty()) logger.info { "Download queue restored: ${queue.size} chapter(s)" }
        kick()
    }

    fun status(): QueueStatus =
        synchronized(lock) {
            QueueStatus(
                if (runners.values.any { it.isActive }) STARTED else STOPPED,
                queue.map { QueueEntry(it.chapterId, it.state, it.progress, it.tries) },
            )
        }

    /**
     * Add chapters to the queue (by manga, then source order, as Suwayomi); unknown ids are skipped, a chapter already
     * queued stays as it is, except one in `ERROR`, which is queued again for one more try.
     */
    fun enqueue(ids: Collection<Int>): QueueStatus {
        val chapters =
            store
                .chapters(ids.toSet())
                .mapNotNull { c -> store.manga(c.mangaId)?.let { m -> c to m } }
                .sortedWith(compareBy({ it.first.mangaId }, { it.first.sourceOrder }))
        var added = false
        synchronized(lock) {
            for ((chapter, manga) in chapters) {
                val existing = queue.firstOrNull { it.chapterId == chapter.id }
                if (existing == null) {
                    queue += Item(chapter.id, manga.sourceId, QUEUED, 0)
                    added = true
                } else if (existing.state == ERROR) {
                    logger.info { "Chapter ${chapter.id} failed before (${existing.tries} tries): one more try" }
                    existing.state = QUEUED
                    existing.progress = 0f
                    added = true
                }
            }
            if (added) persist()
        }
        if (added) kick()
        return status()
    }

    /** Take chapters out of the queue (a running download of one is stopped and its pages dropped). */
    fun dequeue(ids: Collection<Int>): QueueStatus {
        val removed =
            synchronized(lock) {
                val gone = queue.filter { it.chapterId in ids }
                queue.removeAll(gone)
                if (gone.isNotEmpty()) persist()
                gone
            }
        for (item in removed) {
            item.job?.cancel(CancellationException("dequeued"))
            cacheDir(item.chapterId).toFile().deleteRecursively()
        }
        return status()
    }

    override fun close() {
        scope.cancel()
    }

    private fun cacheDir(chapterId: Int): Path = cacheRoot.resolve(chapterId.toString())

    /** Callers hold [lock]. */
    private fun persist() {
        store.saveQueue(queue.map { QueueRow(it.chapterId, if (it.state == DOWNLOADING) QUEUED else it.state, it.tries) })
    }

    /** Start a runner for each source with queued chapters, up to [parallel] at once, in queue order. */
    private fun kick() {
        synchronized(lock) {
            val waiting = queue.filter { it.state == QUEUED }.map { it.sourceId }.distinct()
            for (sourceId in waiting) {
                if (runners.size >= parallel) break
                if (runners.containsKey(sourceId)) continue
                // the runner needs [lock] before it does anything, so it is registered before it can finish
                runners[sourceId] = scope.launch { run(sourceId) }
            }
        }
    }

    private suspend fun run(sourceId: Long) {
        try {
            while (currentCoroutineContext().isActive) {
                val item =
                    synchronized(lock) {
                        queue.firstOrNull { it.sourceId == sourceId && it.state == QUEUED }?.also {
                            it.state = DOWNLOADING
                            it.progress = 0f
                        }
                    } ?: break
                val attempt = scope.async { download(item) }
                item.job = attempt
                try {
                    val pages = attempt.await()
                    store.setDownloaded(item.chapterId, true, pages)
                    synchronized(lock) {
                        queue.remove(item)
                        persist()
                    }
                    logger.info { "Chapter ${item.chapterId} downloaded ($pages pages)" }
                } catch (e: CancellationException) {
                    currentCoroutineContext().ensureActive() // the downloader itself stopped: leave
                    logger.info { "Chapter ${item.chapterId}: download stopped (dequeued)" }
                } catch (e: EmptyChapterException) {
                    logger.warn { "Chapter ${item.chapterId} failed: ${e.message}" }
                    fail(item, maxTries)
                } catch (e: Throwable) {
                    logger.warn(e) { "Chapter ${item.chapterId} failed (try ${item.tries + 1} of $maxTries)" }
                    val queuedAgain = fail(item, item.tries + 1)
                    if (queuedAgain && retryDelayMs > 0) delay(retryDelayMs)
                } finally {
                    item.job = null
                }
            }
        } finally {
            synchronized(lock) {
                queue.filter { it.sourceId == sourceId && it.state == DOWNLOADING }.forEach { it.state = QUEUED }
                runners.remove(sourceId)
            }
            if (scope.isActive) kick()
        }
    }

    /** Count a failed try; true when the chapter is queued again, false when it is now in `ERROR`. */
    private fun fail(
        item: Item,
        tries: Int,
    ): Boolean =
        synchronized(lock) {
            item.tries = tries
            item.state = if (tries >= maxTries) ERROR else QUEUED
            if (item in queue) persist()
            item.state == QUEUED
        }

    /** Download one chapter into its CBZ; the number of pages. */
    private suspend fun download(item: Item): Int {
        val chapter = store.chapter(item.chapterId) ?: error("chapter ${item.chapterId} no longer exists")
        val manga = store.manga(chapter.mangaId) ?: error("manga ${chapter.mangaId} no longer exists")
        val source = library.source(manga.sourceId)
        val http = source as? HttpSource ?: error("${source.name} serves no pages over HTTP")
        val cbz = library.layout.cbz(source.toString(), manga.title, chapter.name, chapter.scanlator)
        if (chapter.downloaded && cbz.exists()) return ChapterArchive.imageCount(cbz).takeIf { it > 0 } ?: chapter.pageCount

        val pages =
            source.getPageList(
                SChapter.create().apply {
                    url = chapter.url
                    name = chapter.name
                    chapter_number = chapter.chapterNumber
                    scanlator = chapter.scanlator
                    date_upload = chapter.uploadDate
                },
            )
        if (pages.isEmpty()) throw EmptyChapterException()
        // a whole CBZ already there (Suwayomi: "unrecognized download"): take it
        if (ChapterArchive.imageCount(cbz) == pages.size) return pages.size

        val dir = cacheDir(item.chapterId)
        pages.forEachIndexed { index, page ->
            currentCoroutineContext().ensureActive()
            val name = DownloadsLayout.pageName(index, pages.size)
            if (ChapterArchive.existingPage(dir, name) == null) {
                if (page.imageUrl.isNullOrEmpty()) page.imageUrl = http.getImageUrl(page)
                http.getImage(page).use { resp ->
                    if (resp.code != 200) throw IllegalStateException("request error! ${resp.code}")
                    ChapterArchive.savePage(dir, name, resp.body.byteStream(), resp.header("Content-Type"))
                }
            }
            item.progress = (index + 1).toFloat() / pages.size
        }
        currentCoroutineContext().ensureActive()
        ChapterArchive.writeComicInfo(dir, manga, chapter)
        ChapterArchive.writeCbz(dir, cbz)
        dir.toFile().deleteRecursively()
        return pages.size
    }
}

package mangalist.extensionhost

import eu.kanade.tachiyomi.source.CatalogueSource
import eu.kanade.tachiyomi.source.Source
import eu.kanade.tachiyomi.source.model.SChapter
import eu.kanade.tachiyomi.source.model.SManga
import eu.kanade.tachiyomi.source.online.HttpSource
import eu.kanade.tachiyomi.util.chapter.ChapterRecognition
import eu.kanade.tachiyomi.util.chapter.ChapterSanitizer.sanitize
import io.github.oshai.kotlinlogging.KotlinLogging
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.StandardCopyOption
import java.util.concurrent.ConcurrentHashMap
import kotlin.io.path.deleteIfExists
import kotlin.io.path.exists
import kotlin.io.path.isDirectory
import kotlin.math.max

private val logger = KotlinLogging.logger {}

/** The loaded sources by id (the extension registry; a test gives its own). */
fun interface SourceLookup {
    fun source(id: Long): Source?
}

/** A manga's chapters as `/compat/manga/{id}/chapters` answers them. */
data class MangaChapters(
    val manga: MangaRow,
    val sourceName: String,
    val chapters: List<ChapterRow>,
)

/**
 * The part of Suwayomi v2.4.2366 MangaList relies on, on the host's own [Store]: mangas found by a search get stable
 * ids (`MangaList.insertOrUpdate`), a manga's chapter list is fetched from the source and merged into the stored one
 * (`Chapter.updateChapterListDatabase`: numbers read from the names by ChapterRecognition, names sanitized, chapters the
 * source dropped deleted and dequeued, a downloaded chapter whose name changed has its CBZ renamed), its details are
 * fetched when never fetched or asked (`Manga.updateMangaDatabase`, the downloads folder renamed with the title), and
 * downloaded chapters are deleted (`ArchiveProvider.delete`).
 */
class Library(
    val store: Store,
    private val sources: SourceLookup,
    val layout: DownloadsLayout,
) {
    /** Set by the downloader: dequeue chapters the source no longer lists. */
    var onChaptersRemoved: (List<Int>) -> Unit = {}

    private val mangaLocks = ConcurrentHashMap<Int, Any>()

    fun source(id: Long): Source = sources.source(id) ?: throw HttpError(404, "no installed source $id")

    /** Suwayomi's source display name (`HttpSource.toString()`: `MangaDex (EN)`) - also its download folder. */
    fun displayName(sourceId: Long): String = source(sourceId).toString()

    fun manga(id: Int): MangaRow = store.manga(id) ?: throw HttpError(404, "no manga $id")

    // --- search -----------------------------------------------------------------------------------------------------

    suspend fun search(
        sourceId: Long,
        query: String,
        page: Int,
    ): Pair<List<MangaRow>, Boolean> {
        val source = source(sourceId)
        val catalogue = source as? CatalogueSource ?: throw HttpError(400, "${source.name} cannot be searched")
        val found = catalogue.getSearchManga(page, query, catalogue.getFilterList())
        val rows =
            store.transaction {
                found.mangas.map { m ->
                    val title = runCatching { m.title }.getOrDefault("")
                    val existing = mangaByUrl(sourceId, m.url)
                    when {
                        existing == null ->
                            insertManga(
                                MangaRow(
                                    id = 0, sourceId = sourceId, url = m.url, title = title, thumbnailUrl = m.thumbnail_url,
                                    author = m.author, artist = m.artist, description = m.description, genre = m.genre,
                                    status = m.status,
                                ),
                            )
                        // Suwayomi refreshes what a browse shows, except for a manga in the library
                        existing.inLibrary -> existing
                        else ->
                            existing
                                .copy(
                                    title = title.ifEmpty { existing.title },
                                    thumbnailUrl = m.thumbnail_url ?: existing.thumbnailUrl,
                                    author = m.author ?: existing.author,
                                    artist = m.artist ?: existing.artist,
                                    description = m.description ?: existing.description,
                                    genre = m.genre ?: existing.genre,
                                    status = m.status,
                                ).also { updateManga(it) }
                    }
                }
            }
        return rows to found.hasNextPage
    }

    /**
     * The manga of [sourceId] at [url], made now when the host never saw it (MangaList's carry-over from Suwayomi: the
     * same source, the same url, the host's own id). An existing manga is answered as it is; a new one keeps [title]
     * until its details are fetched (at its first chapter list, as for a manga a search found). 404 when the source is
     * not installed.
     */
    fun register(
        sourceId: Long,
        url: String,
        title: String,
    ): MangaRow {
        source(sourceId)
        return store.transaction {
            mangaByUrl(sourceId, url)
                ?: insertManga(MangaRow(id = 0, sourceId = sourceId, url = url, title = title))
        }
    }

    fun addToLibrary(mangaId: Int): MangaRow {
        manga(mangaId)
        store.setInLibrary(mangaId, true)
        return manga(mangaId)
    }

    // --- chapters ---------------------------------------------------------------------------------------------------

    private fun MangaRow.toSManga(): SManga {
        val row = this
        return SManga.create().apply {
            url = row.url
            title = row.title
            thumbnail_url = row.thumbnailUrl
            artist = row.artist
            author = row.author
            description = row.description
            genre = row.genre
            status = row.status
            initialized = row.initialized
        }
    }

    private fun ChapterRow.toSChapter(): SChapter {
        val row = this
        return SChapter.create().apply {
            url = row.url
            name = row.name
            chapter_number = row.chapterNumber
            scanlator = row.scanlator
            date_upload = row.uploadDate
        }
    }

    /**
     * Fetch the manga's chapter list from the source now (and its details when never fetched, or [fetchDetails]) and
     * merge it into the stored one. A source that lists no chapters leaves the stored list as it is and answers none
     * (Suwayomi answers "No chapters found").
     */
    suspend fun chapters(
        mangaId: Int,
        fetchDetails: Boolean = false,
    ): MangaChapters {
        val lock = mangaLocks.computeIfAbsent(mangaId) { Any() }
        val start = manga(mangaId)
        val source = source(start.sourceId)
        val details = fetchDetails || !start.initialized
        val stored = store.chaptersOf(mangaId)
        val update = source.getMangaUpdate(start.toSManga(), stored.map { it.toSChapter() }, fetchDetails = details, fetchChapters = true)
        synchronized(lock) {
            var manga = manga(mangaId)
            if (details) manga = applyDetails(manga, source, update.manga)
            if (update.chapters.distinctBy { it.url }.isEmpty()) {
                return MangaChapters(manga, source.toString(), emptyList())
            }
            mergeChapters(manga, source, update.chapters)
            return MangaChapters(manga, source.toString(), store.chaptersOf(mangaId))
        }
    }

    /** Suwayomi's `updateMangaDatabase`: a changed title renames the manga's download folder (kept when that fails). */
    private fun applyDetails(
        manga: MangaRow,
        source: Source,
        remote: SManga,
    ): MangaRow {
        val remoteTitle = runCatching { remote.title }.getOrDefault("")
        val changed = remoteTitle.isNotEmpty() && remoteTitle != manga.title
        val titleOk = changed && renameMangaDir(source.toString(), manga.title, remoteTitle)
        val title = if (titleOk) remoteTitle else manga.title
        val updated =
            manga.copy(
                title = title,
                initialized = true,
                artist = remote.artist ?: manga.artist,
                author = remote.author ?: manga.author,
                description = remote.description ?: manga.description,
                genre = remote.genre ?: manga.genre,
                status = remote.status,
                thumbnailUrl = remote.thumbnail_url?.takeIf { it.isNotEmpty() } ?: manga.thumbnailUrl,
            )
        val realUrl = runCatching { (source as? HttpSource)?.getMangaUrl(updated.toSManga()) }.getOrNull()
        val withUrl = updated.copy(realUrl = realUrl)
        store.updateManga(withUrl)
        return withUrl
    }

    private fun renameMangaDir(
        displayName: String,
        oldTitle: String,
        newTitle: String,
    ): Boolean {
        val from = layout.mangaDir(displayName, oldTitle)
        val to = layout.mangaDir(displayName, newTitle)
        if (!from.exists() || from == to) return true
        return try {
            Files.move(from, to)
            to.exists()
        } catch (e: Exception) {
            logger.error(e) { "Could not rename the download folder \"$from\" to \"$to\"" }
            false
        }
    }

    /** Suwayomi's `Chapter.updateChapterListDatabase`, without read / bookmark state (MangaList uses none). */
    private fun mergeChapters(
        manga: MangaRow,
        source: Source,
        fetched: List<SChapter>,
    ) {
        val unique = fetched.distinctBy { it.url }
        val sManga = manga.toSManga()
        unique.forEach { chapter ->
            @Suppress("DEPRECATION")
            (source as? HttpSource)?.prepareNewChapter(chapter, sManga)
            chapter.chapter_number =
                ChapterRecognition.parseChapterNumber(manga.title, chapter.name, chapter.chapter_number.toDouble()).toFloat()
            chapter.name = chapter.name.sanitize(manga.title)
            chapter.scanlator = chapter.scanlator?.ifBlank { null }?.trim()
        }
        val now = System.currentTimeMillis()
        var maxSeenUploadDate = 0L
        val inDb = store.chaptersOf(manga.id)
        val byUrl = inDb.associateBy { it.url }
        val displayName = source.toString()

        val toInsert = mutableListOf<ChapterRow>()
        val toUpdate = mutableListOf<ChapterRow>()
        unique.reversed().forEachIndexed { index, c ->
            val existing = byUrl[c.url]
            val realUrl = runCatching { (source as? HttpSource)?.getChapterUrl(c) }.getOrNull()
            val row =
                ChapterRow(
                    id = existing?.id ?: 0,
                    mangaId = manga.id,
                    url = c.url,
                    name = c.name,
                    chapterNumber = c.chapter_number,
                    scanlator = c.scanlator,
                    realUrl = realUrl,
                    uploadDate = c.date_upload,
                    sourceOrder = index + 1,
                    downloaded = existing?.downloaded ?: false,
                    pageCount = existing?.pageCount ?: -1,
                )
            if (existing == null) {
                toInsert +=
                    if (row.uploadDate == 0L) {
                        row.copy(uploadDate = if (maxSeenUploadDate == 0L) now else maxSeenUploadDate)
                    } else {
                        maxSeenUploadDate = max(maxSeenUploadDate, row.uploadDate)
                        row
                    }
            } else {
                toUpdate += if (row.uploadDate == 0L) row.copy(uploadDate = existing.uploadDate) else row
            }
        }

        val urls = unique.map { it.url }.toSet()
        val removed = inDb.filter { it.url !in urls }
        // a dropped chapter that was downloaded: its download may pass to a new chapter with the same number and group
        val removedDownloads = removed.filter { it.downloaded && it.chapterNumber >= 0f }.associateBy { it.chapterNumber }

        // dequeued first, outside the store's transaction (the downloader takes its own lock, then the store's)
        if (removed.isNotEmpty()) onChaptersRemoved(removed.map { it.id })
        store.transaction {
            if (removed.isNotEmpty()) deleteChapters(removed.map { it.id })
            for (row in toInsert) {
                val old = removedDownloads[row.chapterNumber]
                val keep = old != null && old.scanlator == row.scanlator && moveChapterFile(displayName, manga.title, old, row)
                insertChapter(if (keep) row.copy(downloaded = true, pageCount = old!!.pageCount) else row)
            }
            for (row in toUpdate) {
                val current = inDb.first { it.id == row.id }
                if (current.downloaded && !moveChapterFile(displayName, manga.title, current, row)) {
                    updateChapter(row.copy(downloaded = false, pageCount = -1))
                } else {
                    updateChapter(row)
                }
            }
        }
    }

    /** Suwayomi's `updateChapterDownloadDir`: a renamed chapter keeps its CBZ under the new name. */
    private fun moveChapterFile(
        displayName: String,
        mangaTitle: String,
        old: ChapterRow,
        new: ChapterRow,
    ): Boolean {
        val from = layout.cbz(displayName, mangaTitle, old.name, old.scanlator)
        val to = layout.cbz(displayName, mangaTitle, new.name, new.scanlator)
        if (from == to) return true
        if (!from.exists()) return true
        return try {
            Files.move(from, to, StandardCopyOption.ATOMIC_MOVE)
            to.exists()
        } catch (e: Exception) {
            logger.error(e) { "Could not rename the chapter download \"$from\" to \"$to\"" }
            false
        }
    }

    fun chaptersById(ids: Collection<Int>): List<ChapterRow> = store.chapters(ids.toSet())

    /** Where a chapter's CBZ is (or will be). */
    fun cbzOf(chapter: ChapterRow): Path {
        val manga = manga(chapter.mangaId)
        return layout.cbz(displayName(manga.sourceId), manga.title, chapter.name, chapter.scanlator)
    }

    /**
     * Delete the chapters' downloads (the CBZ, or a folder of images, and the folders left empty) and mark them not
     * downloaded. A chapter whose file is already gone is marked the same; an unknown id is skipped.
     */
    fun deleteDownloaded(ids: Collection<Int>): List<Int> {
        val done = mutableListOf<Int>()
        for (chapter in store.chapters(ids.toSet())) {
            val manga = manga(chapter.mangaId)
            val display = sources.source(manga.sourceId)?.toString()
            if (display != null) {
                val cbz = layout.cbz(display, manga.title, chapter.name, chapter.scanlator)
                if (cbz.deleteIfExists()) ChapterArchive.cleanupParents(cbz, layout.mangasRoot)
                val folder = layout.folder(display, manga.title, chapter.name, chapter.scanlator)
                if (folder.isDirectory()) {
                    folder.toFile().deleteRecursively()
                    ChapterArchive.cleanupParents(folder, layout.mangasRoot)
                }
            } else {
                logger.warn { "Chapter ${chapter.id}: source ${manga.sourceId} is not installed; only marked not downloaded" }
            }
            store.setDownloaded(chapter.id, false)
            done += chapter.id
        }
        return done
    }
}

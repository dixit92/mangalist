package mangalist.extensionhost

import kotlinx.coroutines.runBlocking
import java.nio.file.Files
import kotlin.io.path.createDirectories
import kotlin.io.path.createTempDirectory
import kotlin.io.path.exists
import kotlin.test.AfterTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNotEquals
import kotlin.test.assertTrue

/** Unit: ids (persisted, stable across a restart) and the chapter list merge, on a fake source. */
class LibraryTest {
    private val dir = createTempDirectory("lib")
    private val source = FakeSource("http://127.0.0.1:9")
    private var store = Store(dir.resolve("host.db"))
    private val layout = DownloadsLayout(dir.resolve("downloads"))

    private fun library() = Library(store, { if (it == source.id) source else null }, layout)

    @AfterTest
    fun close() = store.close()

    init {
        source.mangas["/m/1"] = "Absolute Regression"
        source.mangas["/m/2"] = "Absolute Sword Sense"
        source.chapters["/m/1"] =
            listOf(
                chapter("/c/3", "Absolute Regression Chapter 3", scanlator = " Team "),
                chapter("/c/2", "Chapter 2.5", scanlator = ""),
                chapter("/c/1", "Vol.1 Ch.1 - Start", number = 1f, date = 0),
            )
    }

    @Test
    fun `manga and chapter ids are stable across a restart`() =
        runBlocking {
            val (found, _) = library().search(source.id, "absolute", 1)
            assertEquals(listOf("Absolute Regression", "Absolute Sword Sense"), found.map { it.title })
            val chapters = library().chapters(found[0].id).chapters
            store.close()
            store = Store(dir.resolve("host.db"))
            val again = library().search(source.id, "absolute", 1).first
            assertEquals(found.map { it.id }, again.map { it.id })
            assertEquals(chapters.map { it.id }, library().chapters(found[0].id).chapters.map { it.id })
            assertEquals(chapters.map { it.id }.toSet(), store.chapters(chapters.map { it.id }).map { it.id }.toSet())
        }

    @Test
    fun `chapters get numbers from their names, sanitized names, trimmed groups and source order`() =
        runBlocking {
            val id = library().search(source.id, "regression", 1).first.single().id
            val got = library().chapters(id)
            assertEquals("Fake Source (EN)", got.sourceName)
            val byUrl = got.chapters.associateBy { it.url }
            assertEquals(listOf("/c/3", "/c/2", "/c/1"), got.chapters.map { it.url }) // newest first
            assertEquals(3f, byUrl["/c/3"]!!.chapterNumber)
            assertEquals("Chapter 3", byUrl["/c/3"]!!.name) // the manga's title is cut off the front, as Suwayomi
            assertEquals("Team", byUrl["/c/3"]!!.scanlator)
            assertEquals(2.5f, byUrl["/c/2"]!!.chapterNumber)
            assertEquals(null, byUrl["/c/2"]!!.scanlator)
            assertEquals(1f, byUrl["/c/1"]!!.chapterNumber)
            assertEquals(listOf(3, 2, 1), got.chapters.map { it.sourceOrder })
            assertEquals("http://127.0.0.1:9/c/3", byUrl["/c/3"]!!.realUrl)
            assertNotEquals(0L, byUrl["/c/1"]!!.uploadDate) // no date from the source: now
        }

    @Test
    fun `details are fetched once, then only when asked`() =
        runBlocking {
            val id = library().search(source.id, "regression", 1).first.single().id
            library().chapters(id)
            library().chapters(id)
            assertEquals(1, source.detailCalls.get())
            assertEquals(2, source.chapterCalls.get())
            library().chapters(id, fetchDetails = true)
            assertEquals(2, source.detailCalls.get())
            assertTrue(store.manga(id)!!.initialized)
            assertEquals("An Author", store.manga(id)!!.author)
        }

    @Test
    fun `a chapter the source dropped is deleted and dequeued, a new one gets a new id`() =
        runBlocking {
            val lib = library()
            val removed = mutableListOf<Int>()
            lib.onChaptersRemoved = { removed += it }
            val id = lib.search(source.id, "regression", 1).first.single().id
            val first = lib.chapters(id).chapters.associateBy { it.url }
            source.chapters["/m/1"] = listOf(chapter("/c/4", "Chapter 4")) + source.chapters["/m/1"]!!.filter { it.url != "/c/2" }
            val second = lib.chapters(id).chapters.associateBy { it.url }
            assertEquals(listOf(first["/c/2"]!!.id), removed)
            assertEquals(setOf("/c/4", "/c/3", "/c/1"), second.keys)
            assertEquals(first["/c/3"]!!.id, second["/c/3"]!!.id)
            assertTrue(second["/c/4"]!!.id > first.values.maxOf { it.id }) // never a reused id
        }

    @Test
    fun `no chapters from the source leaves the stored list alone`() =
        runBlocking {
            val id = library().search(source.id, "regression", 1).first.single().id
            val stored = library().chapters(id).chapters
            source.chapters["/m/1"] = emptyList()
            assertEquals(emptyList(), library().chapters(id).chapters)
            assertEquals(stored.map { it.id }, store.chaptersOf(id).map { it.id })
        }

    @Test
    fun `a new title renames the manga's download folder`() =
        runBlocking {
            val id = library().search(source.id, "regression", 1).first.single().id
            library().chapters(id)
            val old = layout.mangaDir("Fake Source (EN)", "Absolute Regression")
            old.createDirectories()
            Files.writeString(old.resolve("Team_Chapter 3.cbz"), "x")
            source.detailsTitles["/m/1"] = "Absolute Regression: Remake"
            val got = library().chapters(id, fetchDetails = true)
            assertEquals("Absolute Regression: Remake", got.manga.title)
            assertFalse(old.exists())
            assertTrue(layout.mangaDir("Fake Source (EN)", "Absolute Regression: Remake").resolve("Team_Chapter 3.cbz").exists())
        }

    @Test
    fun `a renamed downloaded chapter keeps its file`() =
        runBlocking {
            val id = library().search(source.id, "regression", 1).first.single().id
            val c3 = library().chapters(id).chapters.first { it.url == "/c/3" }
            val cbz = layout.cbz("Fake Source (EN)", "Absolute Regression", c3.name, c3.scanlator)
            cbz.parent.createDirectories()
            Files.writeString(cbz, "x")
            store.setDownloaded(c3.id, true, 5)
            source.chapters["/m/1"] = listOf(chapter("/c/3", "Chapter 3 - Renamed", scanlator = "Team")) + source.chapters["/m/1"]!!.drop(1)
            val renamed = library().chapters(id).chapters.first { it.url == "/c/3" }
            assertTrue(renamed.downloaded)
            assertFalse(cbz.exists())
            assertTrue(layout.cbz("Fake Source (EN)", "Absolute Regression", "Chapter 3 - Renamed", "Team").exists())
        }

    @Test
    fun `the library flag is kept, and a library manga's title is not changed by a search`() =
        runBlocking {
            val id = library().search(source.id, "regression", 1).first.single().id
            assertTrue(library().addToLibrary(id).inLibrary)
            source.mangas["/m/1"] = "Absolute Regression (new title)"
            val again = library().search(source.id, "regression", 1).first.single()
            assertEquals("Absolute Regression", again.title)
            assertTrue(again.inLibrary)
        }
}

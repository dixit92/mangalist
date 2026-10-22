package mangalist.extensionhost

import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.runBlocking
import java.util.zip.ZipFile
import kotlin.io.path.createTempDirectory
import kotlin.io.path.exists
import kotlin.io.path.listDirectoryEntries
import kotlin.io.path.name
import kotlin.test.AfterTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

/** Unit: the queue's semantics (Suwayomi's), against a fake source whose images come from a local HTTP server. */
class DownloaderTest {
    private val dir = createTempDirectory("dl")
    private val images = ImageServer()
    private val source = FakeSource(images.base)
    private var store = Store(dir.resolve("host.db"))
    private val layout = DownloadsLayout(dir.resolve("downloads"))
    private var library = Library(store, { if (it == source.id) source else null }, layout)
    private var downloader = Downloader(store, library, dir.resolve("cache"), parallel = 2, retryDelayMs = 0)

    init {
        source.mangas["/m/1"] = "Weeb: Title?"
        source.chapters["/m/1"] = listOf(chapter("/c/2", "Chapter 2", 2f, "Group/A"), chapter("/c/1", "Chapter 1", 1f))
        source.pages["/c/2"] = 3
        source.pages["/c/1"] = 2
    }

    @AfterTest
    fun close() {
        downloader.close()
        store.close()
        images.close()
    }

    private fun chapters(): Map<String, ChapterRow> =
        runBlocking {
            val id = library.search(source.id, "weeb", 1).first.single().id
            library.chapters(id).chapters.associateBy { it.url }
        }

    private fun restart() {
        downloader.close()
        store.close()
        store = Store(dir.resolve("host.db"))
        library = Library(store, { if (it == source.id) source else null }, layout)
        downloader = Downloader(store, library, dir.resolve("cache"), parallel = 2, retryDelayMs = 0)
        downloader.restore()
    }

    @Test
    fun `a downloaded chapter is one CBZ where MangaList looks, and leaves the queue`() {
        val c2 = chapters().getValue("/c/2")
        downloader.enqueue(listOf(c2.id))
        waitFor(what = "the download") { downloader.status().queue.isEmpty() }
        val cbz = layout.cbz("Fake Source (EN)", "Weeb: Title?", "Chapter 2", "Group/A")
        assertEquals("Group_A_Chapter 2.cbz", cbz.name)
        assertEquals("Weeb_ Title_", cbz.parent.name)
        assertTrue(cbz.exists())
        ZipFile(cbz.toFile()).use { zip ->
            assertEquals(listOf("001.png", "002.png", "003.png", "ComicInfo.xml"), zip.entries().asSequence().map { it.name }.toList())
            val info = String(zip.getInputStream(zip.getEntry("ComicInfo.xml")).readBytes())
            assertTrue("<Number>2</Number>" in info && "<Translator>Group/A</Translator>" in info && "<Series>Weeb: Title?</Series>" in info, info)
            assertTrue("<Web>${images.base}/c/2</Web>" in info, info)
        }
        assertEquals(listOf("Group_A_Chapter 2.cbz"), cbz.parent.listDirectoryEntries().map { it.name }) // no temporary file left
        val row = store.chapter(c2.id)!!
        assertTrue(row.downloaded)
        assertEquals(3, row.pageCount)
        assertEquals(setOf("fake-source-agent"), images.userAgents) // the source's own headers
        assertFalse(dir.resolve("cache").resolve(c2.id.toString()).exists())
    }

    @Test
    fun `three failures leave the chapter in ERROR, enqueueing it again gives one more try`() {
        val c1 = chapters().getValue("/c/1")
        images.failures.set(3)
        downloader.enqueue(listOf(c1.id))
        waitFor(what = "ERROR") { downloader.status().queue.singleOrNull()?.state == Downloader.ERROR }
        val stuck = downloader.status().queue.single()
        assertEquals(3, stuck.tries)
        Thread.sleep(200)
        assertEquals(Downloader.ERROR, downloader.status().queue.single().state) // never tried again by itself
        assertEquals(3, images.requests.get())
        assertFalse(store.chapter(c1.id)!!.downloaded)

        images.failures.set(1)
        downloader.enqueue(listOf(c1.id))
        waitFor(what = "ERROR again") { downloader.status().queue.singleOrNull()?.state == Downloader.ERROR }
        assertEquals(4, downloader.status().queue.single().tries) // exactly one more try

        downloader.enqueue(listOf(c1.id))
        waitFor(what = "the download") { downloader.status().queue.isEmpty() }
        assertTrue(store.chapter(c1.id)!!.downloaded)
    }

    @Test
    fun `a chapter without pages goes to ERROR at once`() {
        val c1 = chapters().getValue("/c/1")
        source.pages["/c/1"] = 0
        downloader.enqueue(listOf(c1.id))
        waitFor(what = "ERROR") { downloader.status().queue.singleOrNull()?.state == Downloader.ERROR }
        assertEquals(3, downloader.status().queue.single().tries)
        assertEquals(0, images.requests.get())
    }

    @Test
    fun `the queue survives a restart, a running download is queued again, an ERROR stays`() {
        val all = chapters()
        val c1 = all.getValue("/c/1")
        val c2 = all.getValue("/c/2")
        images.failures.set(3)
        downloader.enqueue(listOf(c1.id))
        waitFor(what = "ERROR") { downloader.status().queue.singleOrNull()?.state == Downloader.ERROR }
        val gate = CompletableDeferred<Unit>()
        source.gate = gate
        downloader.enqueue(listOf(c2.id))
        waitFor(what = "DOWNLOADING") { downloader.status().queue.any { it.chapterId == c2.id && it.state == Downloader.DOWNLOADING } }

        restart()
        val after = downloader.status().queue.associateBy { it.chapterId }
        assertEquals(listOf(c1.id, c2.id), downloader.status().queue.map { it.chapterId })
        assertEquals(Downloader.ERROR, after.getValue(c1.id).state)
        assertEquals(3, after.getValue(c1.id).tries)
        gate.complete(Unit)
        waitFor(what = "the restored download") { downloader.status().queue.none { it.chapterId == c2.id } }
        assertTrue(store.chapter(c2.id)!!.downloaded)
        assertEquals(Downloader.ERROR, downloader.status().queue.single().state)
    }

    @Test
    fun `dequeue stops a running download`() {
        val c2 = chapters().getValue("/c/2")
        val gate = CompletableDeferred<Unit>()
        source.gate = gate
        downloader.enqueue(listOf(c2.id))
        waitFor(what = "DOWNLOADING") { downloader.status().queue.singleOrNull()?.state == Downloader.DOWNLOADING }
        downloader.dequeue(listOf(c2.id))
        assertEquals(emptyList(), downloader.status().queue)
        gate.complete(Unit)
        Thread.sleep(300)
        assertFalse(store.chapter(c2.id)!!.downloaded)
        assertEquals(0, images.requests.get())
    }

    @Test
    fun `an already downloaded chapter finishes without fetching, and delete-downloaded removes the file and empty folders`() {
        val c2 = chapters().getValue("/c/2")
        downloader.enqueue(listOf(c2.id))
        waitFor(what = "the download") { downloader.status().queue.isEmpty() }
        val requests = images.requests.get()
        downloader.enqueue(listOf(c2.id))
        waitFor(what = "the second download") { downloader.status().queue.isEmpty() }
        assertEquals(requests, images.requests.get())

        val cbz = library.cbzOf(store.chapter(c2.id)!!)
        assertEquals(listOf(c2.id), library.deleteDownloaded(listOf(c2.id, 99999)))
        assertFalse(cbz.exists())
        assertFalse(cbz.parent.exists())
        assertFalse(cbz.parent.parent.exists())
        assertTrue(layout.mangasRoot.exists())
        assertFalse(store.chapter(c2.id)!!.downloaded)
        assertEquals(listOf(c2.id), library.deleteDownloaded(listOf(c2.id))) // already gone: the same answer
    }

    @Test
    fun `two sources download at once, one chapter at a time per source`() {
        val other = FakeSource(images.base, name = "Other")
        other.mangas["/m/9"] = "Weeb Other"
        other.chapters["/m/9"] = listOf(chapter("/o/1", "Chapter 1", 1f))
        other.pages["/o/1"] = 1
        library = Library(store, { listOf(source, other).firstOrNull { s -> s.id == it } }, layout)
        downloader.close()
        downloader = Downloader(store, library, dir.resolve("cache"), parallel = 2, retryDelayMs = 0)
        val mine = chapters()
        val theirs =
            runBlocking {
                val id = library.search(other.id, "other", 1).first.single().id
                library.chapters(id).chapters.single()
            }
        val gate = CompletableDeferred<Unit>()
        source.gate = gate
        other.gate = gate
        downloader.enqueue(listOf(mine.getValue("/c/1").id, mine.getValue("/c/2").id, theirs.id))
        waitFor(what = "two running") { downloader.status().queue.count { it.state == Downloader.DOWNLOADING } == 2 }
        Thread.sleep(200)
        val running = downloader.status().queue.filter { it.state == Downloader.DOWNLOADING }.map { it.chapterId }.toSet()
        assertEquals(setOf(mine.getValue("/c/1").id, theirs.id), running)
        assertEquals(Downloader.STARTED, downloader.status().state)
        gate.complete(Unit)
        waitFor(what = "all downloads") { downloader.status().queue.isEmpty() }
        waitFor(what = "STOPPED") { downloader.status().state == Downloader.STOPPED }
    }
}

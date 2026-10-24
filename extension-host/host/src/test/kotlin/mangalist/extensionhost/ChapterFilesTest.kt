package mangalist.extensionhost

import java.nio.file.Files
import java.util.zip.ZipFile
import kotlin.io.path.Path
import kotlin.io.path.createDirectories
import kotlin.io.path.createTempDirectory
import kotlin.io.path.exists
import kotlin.io.path.listDirectoryEntries
import kotlin.io.path.name
import kotlin.io.path.writeBytes
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNull
import kotlin.test.assertTrue

/** Unit: the download layout MangaList's chapter_arrivals expects (values checked against its `suwayomi_name`). */
class ChapterFilesTest {
    @Test
    fun `on Windows a reserved device name gets a leading underscore, elsewhere not (audit B10)`() {
        val before = DownloadsLayout.windows
        try {
            DownloadsLayout.windows = true
            assertEquals("_CON", DownloadsLayout.validName("CON"))
            assertEquals("_nul.txt", DownloadsLayout.validName("nul.txt"))
            assertEquals("_COM1", DownloadsLayout.validName("COM1"))
            assertEquals("CONAN", DownloadsLayout.validName("CONAN"))
            assertEquals("Les Poroïniens & Fap It_Ch.58", DownloadsLayout.chapterFileName("Ch.58", "Les Poroïniens & Fap It"))
            DownloadsLayout.windows = false
            assertEquals("CON", DownloadsLayout.validName("CON"))
        } finally {
            DownloadsLayout.windows = before
        }
    }

    @Test
    fun `names are cleaned as MangaList's suwayomi_name expects`() {
        assertEquals("Vol.1 Ch.2_ The _End__", DownloadsLayout.validName("  ..Vol.1 Ch.2: The *End*?  "))
        assertEquals("a_b_c_d_e_f_g_h", DownloadsLayout.validName("a\u0001b\u007fc\"d<e>f|g\\h"))
        assertEquals("(invalid)", DownloadsLayout.validName(" . "))
        val cut = DownloadsLayout.validName("日本".repeat(100))
        assertEquals(80, cut.length)
        assertEquals(240, cut.toByteArray().size)
    }

    @Test
    fun `the CBZ path is downloads, mangas, source, title, scanlator_name`() {
        val layout = DownloadsLayout(Path("/dl"))
        assertEquals(
            Path("/dl/mangas/MangaDex (EN)/Title_ Sub_Part/Group A_Ch. 1.cbz"),
            layout.cbz("MangaDex (EN)", "Title: Sub/Part", "Ch. 1", "Group A"),
        )
        assertEquals(Path("/dl/mangas/MangaDex (EN)/Title/Ch. 1.cbz"), layout.cbz("MangaDex (EN)", "Title", "Ch. 1", null))
    }

    @Test
    fun `page names have at least three digits`() {
        assertEquals("001", DownloadsLayout.pageName(0, 20))
        assertEquals("120", DownloadsLayout.pageName(119, 120))
        assertEquals("0001", DownloadsLayout.pageName(0, 1000))
    }

    @Test
    fun `image types come from the bytes, then the content type`() {
        assertEquals("png", ImageType.extensionFor(PNG.copyOf(12), "image/jpeg"))
        assertEquals("jpg", ImageType.extensionFor(byteArrayOf(0xFF.toByte(), 0xD8.toByte(), 0xFF.toByte(), 0), null))
        assertEquals("webp", ImageType.extensionFor("RIFF....WEBP".toByteArray(), null))
        assertEquals("gif", ImageType.extensionFor(ByteArray(4), "image/gif"))
        assertEquals("jpg", ImageType.extensionFor(ByteArray(4), "text/html"))
    }

    private val manga = MangaRow(id = 1, sourceId = 7, url = "/m/1", title = "Fragrant Flower", author = "Saka", status = 1)

    private fun chapter(
        number: Float,
        scanlator: String? = "Team",
    ) = ChapterRow(1, 1, "/c/1", "Ch. 10.5 - Rain", number, scanlator, "https://example.org/c/1", 1_700_000_000_000, 3)

    @Test
    fun `ComicInfo carries title, series, number, translator and web`() {
        val xml = ChapterArchive.comicInfoXml(manga, chapter(10.5f))
        assertTrue(xml.startsWith("<?xml"), xml)
        assertTrue("<Title>Ch. 10.5 - Rain</Title>" in xml, xml)
        assertTrue("<Series>Fragrant Flower</Series>" in xml, xml)
        assertTrue("<Number>10.5</Number>" in xml, xml)
        assertTrue("<Translator>Team</Translator>" in xml, xml)
        assertTrue("<Web>https://example.org/c/1</Web>" in xml, xml)
        assertTrue("<Writer>Saka</Writer>" in xml, xml)
        assertTrue("<ty:PublishingStatusTachiyomi" in xml || "PublishingStatusTachiyomi" in xml, xml)
        assertTrue("<Number>12</Number>" in ChapterArchive.comicInfoXml(manga, chapter(12f)))
        val unknown = ChapterArchive.comicInfoXml(manga, chapter(-1f, scanlator = null))
        assertFalse("<Number>" in unknown, unknown)
        assertFalse("<Translator>" in unknown, unknown)
        // no real url from the source: the chapter's own url, so MangaList finds a renamed CBZ by it (audit B10)
        val bare = ChapterArchive.comicInfoXml(manga, chapter(1f).copy(realUrl = null))
        assertTrue("<Web>/c/1</Web>" in bare, bare)
    }

    @Test
    fun `a CBZ is written whole under a temporary name, its entries sorted`() {
        val dir = createTempDirectory("pages")
        val out = createTempDirectory("out").resolve("a/b/Team_Ch. 1.cbz")
        dir.resolve("002.png").writeBytes(PNG)
        dir.resolve("001.png").writeBytes(PNG)
        dir.resolve("003.tmp").writeBytes(PNG) // a broken page is never archived
        ChapterArchive.writeComicInfo(dir, manga, chapter(1f))
        ChapterArchive.writeCbz(dir, out)
        assertTrue(out.exists())
        assertEquals(listOf("Team_Ch. 1.cbz"), out.parent.listDirectoryEntries().map { it.name })
        ZipFile(out.toFile()).use { zip ->
            assertEquals(listOf("001.png", "002.png", "ComicInfo.xml"), zip.entries().asSequence().map { it.name }.toList())
        }
        assertEquals(2, ChapterArchive.imageCount(out))
    }

    @Test
    fun `saving a page names it by its bytes and leaves no temporary file`() {
        val dir = createTempDirectory("pages")
        val saved = ChapterArchive.savePage(dir, "001", PNG.inputStream(), "image/jpeg")
        assertEquals("001.png", saved.name)
        assertEquals(listOf("001.png"), dir.listDirectoryEntries().map { it.name })
        assertEquals(saved, ChapterArchive.existingPage(dir, "001"))
        assertNull(ChapterArchive.existingPage(dir, "002"))
    }

    @Test
    fun `cleaning up removes empty folders up to the root only`() {
        val root = createTempDirectory("dl").resolve("mangas")
        val file = root.resolve("Src (EN)/Title/x.cbz")
        file.parent.createDirectories()
        Files.writeString(file, "x")
        Files.delete(file)
        ChapterArchive.cleanupParents(file, root)
        assertTrue(root.exists())
        assertFalse(root.resolve("Src (EN)").exists())
    }
}

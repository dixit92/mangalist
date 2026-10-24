package mangalist.extensionhost

import eu.kanade.tachiyomi.source.local.metadata.COMIC_INFO_FILE
import eu.kanade.tachiyomi.source.local.metadata.ComicInfo
import eu.kanade.tachiyomi.source.local.metadata.ComicInfoPublishingStatus
import nl.adaptivity.xmlutil.XmlDeclMode
import nl.adaptivity.xmlutil.core.XmlVersion
import nl.adaptivity.xmlutil.serialization.XML
import xyz.nulldev.androidcompat.util.SafePath
import java.io.InputStream
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.StandardCopyOption
import java.time.Instant
import java.time.ZoneId
import java.util.zip.Deflater
import java.util.zip.ZipEntry
import java.util.zip.ZipFile
import java.util.zip.ZipOutputStream
import kotlin.io.path.createDirectories
import kotlin.io.path.deleteIfExists
import kotlin.io.path.exists
import kotlin.io.path.isDirectory
import kotlin.io.path.listDirectoryEntries
import kotlin.io.path.name
import kotlin.io.path.outputStream
import kotlin.io.path.writeText

/**
 * Where and how a downloaded chapter is written - the same files Suwayomi v2.4.2366 writes with "Download as CBZ"
 * (its `DirName.kt`, `ArchiveProvider`, `ChaptersFilesProvider`, `ImageResponse`, `GetComicInfo.kt`), which is what
 * MangaList's `chapter_arrivals` (`suwayomi_name`, `expected_path`, `read_comicinfo`) reads:
 *
 * `<downloads>/mangas/<source displayName>/<manga title>/<scanlator>_<chapter name>.cbz` (no scanlator: `<chapter
 * name>.cbz`), each part cleaned by Suwayomi's `SafePath.buildValidFilename` (Mihon's `DiskUtil.buildValidFilename`);
 * pages `001.<ext>` ... (at least 3 digits, more for 1000+ pages), the extension from the image's own bytes; a
 * `ComicInfo.xml`.
 */
class DownloadsLayout(
    val root: Path,
) {
    val mangasRoot: Path = root.resolve("mangas")

    fun mangaDir(
        sourceDisplayName: String,
        mangaTitle: String,
    ): Path = mangasRoot.resolve(validName(sourceDisplayName)).resolve(validName(mangaTitle))

    fun cbz(
        sourceDisplayName: String,
        mangaTitle: String,
        chapterName: String,
        scanlator: String?,
    ): Path = mangaDir(sourceDisplayName, mangaTitle).resolve(chapterFileName(chapterName, scanlator) + ".cbz")

    /** A chapter's folder (Suwayomi without "Download as CBZ"); only ever deleted, never written. */
    fun folder(
        sourceDisplayName: String,
        mangaTitle: String,
        chapterName: String,
        scanlator: String?,
    ): Path = mangaDir(sourceDisplayName, mangaTitle).resolve(chapterFileName(chapterName, scanlator))

    companion object {
        /** On Windows a few names are devices, not files (`CON`, `NUL`, `COM1`, ... - also with an extension). */
        private val RESERVED = setOf("CON", "PRN", "AUX", "NUL") + (1..9).flatMap { listOf("COM$it", "LPT$it") }

        /** Whether names are cleaned for Windows too (the host runs there: a desktop). */
        var windows: Boolean = System.getProperty("os.name").orEmpty().startsWith("Windows")

        /**
         * Suwayomi's rule (`SafePath.buildValidFilename`), and on Windows a reserved device name gets a leading `_`
         * (MangaList's `chapter_arrivals.suwayomi_name` does the same there; transition audit B10).
         */
        fun validName(name: String): String = SafePath.buildValidFilename(name).let { if (windows) windowsSafe(it) else it }

        fun windowsSafe(name: String): String = if (name.substringBefore('.').trimEnd().uppercase() in RESERVED) "_$name" else name

        /** Suwayomi's `getChapterDir`: `<scanlator>_<name>` or the name alone, cleaned as one file name. */
        fun chapterFileName(
            chapterName: String,
            scanlator: String?,
        ): String = validName(if (scanlator != null) "${scanlator}_$chapterName" else chapterName)

        /** Suwayomi's `Page.getPageName`: 0 -> `001` (more digits when the chapter has 1000 pages or more). */
        fun pageName(
            index: Int,
            pageCount: Int,
        ): String = String.format("%0${pageCount.toString().length.coerceAtLeast(3)}d", index + 1)
    }
}

/** Suwayomi's `ImageUtil.ImageType`, found from the first bytes of the image. */
enum class ImageType(
    val mime: String,
    val extension: String,
) {
    AVIF("image/avif", "avif"),
    GIF("image/gif", "gif"),
    HEIF("image/heif", "heif"),
    JPEG("image/jpeg", "jpg"),
    JXL("image/jxl", "jxl"),
    PNG("image/png", "png"),
    WEBP("image/webp", "webp"),
    ;

    companion object {
        private fun ByteArray.startsWith(prefix: ByteArray) = size >= prefix.size && prefix.indices.all { this[it] == prefix[it] }

        private fun bytes(vararg v: Int) = ByteArray(v.size) { v[it].toByte() }

        fun of(head: ByteArray): ImageType? {
            if (head.isEmpty()) return null
            return when {
                head.startsWith(bytes(0xFF, 0xD8, 0xFF)) -> JPEG
                head.startsWith(bytes(0x89, 0x50, 0x4E, 0x47)) -> PNG
                head.startsWith("GIF8".toByteArray()) -> GIF
                head.startsWith("RIFF".toByteArray()) -> WEBP
                head.size >= 12 && head.copyOfRange(4, 12).contentEquals("ftypavif".toByteArray()) -> AVIF
                head.size >= 12 && head.copyOfRange(4, 8).contentEquals("ftyp".toByteArray()) &&
                    String(head.copyOfRange(8, 12)) in setOf("heic", "heix", "hevc", "hevx", "mif1", "msf1") -> HEIF
                head.startsWith(bytes(0xFF, 0x0A)) -> JXL
                head.startsWith(bytes(0x00, 0x00, 0x00, 0x0C, 0x4A, 0x58, 0x4C, 0x20, 0x0D, 0x0A, 0x87, 0x0A)) -> JXL
                else -> null
            }
        }

        private val byMime = entries.associateBy { it.mime }

        /** Suwayomi's `ImageResponse.saveImage`: the bytes' type, else the Content-Type's, else `jpg`. */
        fun extensionFor(
            head: ByteArray,
            contentType: String?,
        ): String {
            of(head)?.let { return it.extension }
            val mime = contentType?.substringBefore(';')?.trim()?.lowercase()?.takeIf { it.startsWith("image/") }
            mime?.let { m -> byMime[m]?.let { return it.extension } }
            return mime?.substringAfter('/')?.takeIf { it.isNotBlank() && it.all { c -> c.isLetterOrDigit() } } ?: "jpg"
        }
    }
}

object ChapterArchive {
    /** The XML Suwayomi encodes ComicInfo with (its AppModule's `XML` single). */
    private val xml =
        XML.v1 {
            policy {
                ignoreUnknownChildren()
                autoPolymorphic = true
            }
            xmlDeclMode = XmlDeclMode.Charset
            xmlVersion = XmlVersion.XML10
            setIndent(2)
        }

    /** Suwayomi's `getComicInfo` (no categories: the host has none). */
    fun comicInfo(
        manga: MangaRow,
        chapter: ChapterRow,
    ): ComicInfo {
        val localDate = Instant.ofEpochMilli(chapter.uploadDate).atZone(ZoneId.systemDefault()).toLocalDate()
        return ComicInfo(
            title = ComicInfo.Title(chapter.name),
            series = ComicInfo.Series(manga.title),
            number =
                chapter.chapterNumber.takeIf { it >= 0 }?.let {
                    if (it.rem(1) == 0.0f) ComicInfo.Number(it.toInt().toString()) else ComicInfo.Number(it.toString())
                },
            // the chapter's url when the source gives no real one: MangaList finds a renamed CBZ by it (audit B10)
            web = ComicInfo.Web(chapter.realUrl?.takeIf { it.isNotBlank() } ?: chapter.url),
            summary = manga.description?.let { ComicInfo.Summary(it) },
            writer = manga.author?.let { ComicInfo.Writer(it) },
            penciller = manga.artist?.let { ComicInfo.Penciller(it) },
            translator = chapter.scanlator?.let { ComicInfo.Translator(it) },
            genre = manga.genre?.let { ComicInfo.Genre(it) },
            publishingStatus =
                ComicInfo.PublishingStatusTachiyomi(ComicInfoPublishingStatus.toComicInfoValue(manga.status.toLong())),
            categories = null,
            inker = null,
            colorist = null,
            letterer = null,
            coverArtist = null,
            tags = null,
            day = ComicInfo.Day(localDate.dayOfMonth),
            month = ComicInfo.Month(localDate.monthValue),
            year = ComicInfo.Year(localDate.year),
        )
    }

    fun comicInfoXml(
        manga: MangaRow,
        chapter: ChapterRow,
    ): String = xml.encodeToString(ComicInfo.serializer(), comicInfo(manga, chapter))

    fun writeComicInfo(
        dir: Path,
        manga: MangaRow,
        chapter: ChapterRow,
    ) {
        dir.resolve(COMIC_INFO_FILE).writeText(comicInfoXml(manga, chapter))
    }

    /** The pages already saved in [dir] under [pageName] (`001.jpg`), or null. A `.tmp` file is a broken one. */
    fun existingPage(
        dir: Path,
        pageName: String,
    ): Path? =
        if (!dir.isDirectory()) {
            null
        } else {
            dir.listDirectoryEntries("$pageName.*").firstOrNull { !it.name.endsWith(".tmp") }
        }

    /** Save one page as `<dir>/<pageName>.<ext>` (written to `.tmp` first; the extension from its bytes). */
    fun savePage(
        dir: Path,
        pageName: String,
        body: InputStream,
        contentType: String?,
    ): Path {
        dir.createDirectories()
        val tmp = dir.resolve("$pageName.tmp")
        try {
            body.use { input -> tmp.outputStream().use { input.copyTo(it) } }
            val head = Files.newInputStream(tmp).use { it.readNBytes(12) }
            val target = dir.resolve("$pageName.${ImageType.extensionFor(head, contentType)}")
            Files.move(tmp, target, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE)
            return target
        } finally {
            tmp.deleteIfExists()
        }
    }

    /**
     * Zip [dir]'s files (sorted by name, as Suwayomi's ArchiveProvider) into [cbz]: written to `<cbz>.tmp` and
     * renamed when whole, so a CBZ that exists is complete.
     */
    fun writeCbz(
        dir: Path,
        cbz: Path,
    ) {
        cbz.parent.createDirectories()
        val tmp = cbz.resolveSibling(cbz.name + ".tmp")
        try {
            ZipOutputStream(tmp.outputStream()).use { zip ->
                zip.setMethod(ZipOutputStream.DEFLATED)
                zip.setLevel(Deflater.DEFAULT_COMPRESSION)
                dir.listDirectoryEntries().filter { Files.isRegularFile(it) && !it.name.endsWith(".tmp") }.sortedBy { it.name }.forEach { f ->
                    val entry = ZipEntry(f.name)
                    entry.time = 0L
                    zip.putNextEntry(entry)
                    Files.copy(f, zip)
                    zip.closeEntry()
                }
            }
            Files.move(tmp, cbz, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE)
        } finally {
            tmp.deleteIfExists()
        }
    }

    /** The number of images in a CBZ (ComicInfo.xml left out); 0 when it cannot be read. */
    fun imageCount(cbz: Path): Int =
        if (!cbz.exists()) {
            0
        } else {
            runCatching {
                ZipFile(cbz.toFile()).use { z -> z.entries().asSequence().count { !it.isDirectory && it.name != COMIC_INFO_FILE } }
            }.getOrDefault(0)
        }

    /** Suwayomi's `FileDeletionHelper.cleanupParentFoldersFor`: remove the folders left empty up to [root]. */
    fun cleanupParents(
        file: Path,
        root: Path,
    ) {
        var folder = file.toAbsolutePath().parent ?: return
        val stop = root.toAbsolutePath().normalize()
        while (folder.isDirectory() && folder.normalize() != stop && folder.startsWith(stop)) {
            if (folder.listDirectoryEntries().isNotEmpty()) return
            Files.delete(folder)
            folder = folder.parent ?: return
        }
    }
}

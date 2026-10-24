package mangalist.extensionhost

import java.nio.file.Files
import java.nio.file.Path
import kotlin.io.path.deleteIfExists

/**
 * The start-up check of how the JVM names files (MangaList's transition audit D1, 2026-10-24). Java takes the encoding of
 * file *names* (`sun.jnu.encoding`) from the C library's locale at start, and nothing can change it later: in an ASCII
 * locale (`LANG` naming a locale the system does not have, or `C`) every chapter, group or manga name with a character
 * like "ï" or a Japanese title fails ("InvalidPathException: Malformed input or input contains unmappable characters").
 * The container and the supervisor give the host `C.UTF-8`; a host started any other way is told loudly at its start
 * ([problem], logged as an ERROR) instead of failing chapter by chapter.
 */
object FileNames {
    /** The JVM's file-name encoding. */
    fun encoding(): String = System.getProperty("sun.jnu.encoding") ?: "unknown"

    /** What is wrong (an ERROR line for the log), or null when file names are UTF-8 and one can be written in [dir]. */
    fun problem(
        dir: Path? = null,
        encoding: String = encoding(),
    ): String? {
        val utf8 = encoding.replace("-", "").equals("UTF8", ignoreCase = true)
        if (!utf8) {
            return "File names are encoded as $encoding, not UTF-8 (the locale: LANG=${System.getenv("LANG") ?: ""}, " +
                "LC_ALL=${System.getenv("LC_ALL") ?: ""}): chapters, groups and mangas whose names are not plain ASCII " +
                "cannot be downloaded. Start the host with LANG=C.UTF-8 (the container and its supervisor do)."
        }
        if (dir == null) return null
        return probe(dir)?.let { "A file with a non-ASCII name cannot be written in $dir: $it" }
    }

    /** Write and delete a file named with non-ASCII characters in [dir]: null when that works, else why not. */
    fun probe(dir: Path): String? {
        var file: Path? = null
        return try {
            Files.createDirectories(dir)
            file = dir.resolve(".mangalist-name-check-ï-日本.tmp")
            Files.write(file, byteArrayOf(1))
            null
        } catch (e: Exception) {
            "${e.javaClass.simpleName}: ${e.message}"
        } finally {
            runCatching { file?.deleteIfExists() }
        }
    }

    /** `java -cp ... mangalist.extensionhost.FileNames <dir>`: prints the encoding and the check's result (tests). */
    @JvmStatic
    fun main(args: Array<String>) {
        println("encoding=${encoding()}")
        println("problem=${problem(args.firstOrNull()?.let { Path.of(it) }) ?: "none"}")
    }
}

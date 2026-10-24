package mangalist.extensionhost

import java.nio.file.Path
import java.util.concurrent.TimeUnit
import kotlin.io.path.createTempDirectory
import kotlin.io.path.listDirectoryEntries
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNotNull
import kotlin.test.assertNull
import kotlin.test.assertTrue

/**
 * The file-name encoding check (MangaList's transition audit D1): a JVM started in an ASCII locale is told at once, and
 * one in C.UTF-8 writes a non-ASCII name. Both run as real JVMs with the locale in their environment - the way the
 * owner's container failed (LANG naming a locale the system does not have) and the way the supervisor starts the host.
 */
class FileNamesTest {
    private fun run(vararg locale: Pair<String, String>): Map<String, String> {
        val dir = createTempDirectory("names")
        val java = Path.of(System.getProperty("java.home"), "bin", "java").toString()
        val pb = ProcessBuilder(java, "-cp", System.getProperty("java.class.path"), "mangalist.extensionhost.FileNames", dir.toString())
        pb.environment().keys.removeAll { it == "LANG" || it.startsWith("LC_") }
        locale.forEach { (k, v) -> pb.environment()[k] = v }
        val process = pb.redirectErrorStream(true).start()
        val out = process.inputStream.bufferedReader().readText()
        assertTrue(process.waitFor(60, TimeUnit.SECONDS))
        assertTrue(dir.listDirectoryEntries().isEmpty(), "the check leaves nothing behind")
        return out.lines().filter { "=" in it }.associate { it.substringBefore("=") to it.substringAfter("=") }
    }

    @Test
    fun `an ASCII locale is reported at once`() {
        val got = run("LANG" to "C")
        assertTrue(got.getValue("encoding").replace("-", "").uppercase() != "UTF8", got.toString())
        assertTrue(got.getValue("problem").startsWith("File names are encoded as"), got.toString())
        // the owner's container: LANG names a locale the system does not have
        val missing = run("LANG" to "xx_NOPE.UTF-8")
        assertTrue(missing.getValue("problem") != "none", missing.toString())
    }

    @Test
    fun `C UTF-8 writes a non-ASCII name`() {
        val got = run("LANG" to "C.UTF-8", "LC_ALL" to "C.UTF-8")
        assertEquals("none", got["problem"], got.toString())
    }

    @Test
    fun `the check itself`() {
        assertNotNull(FileNames.problem(encoding = "ANSI_X3.4-1968"))
        assertNull(FileNames.problem(encoding = "UTF-8"))
    }
}

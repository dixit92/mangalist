package mangalist.extensionhost

import okhttp3.OkHttpClient
import kotlin.io.path.createDirectories
import kotlin.io.path.createTempDirectory
import kotlin.io.path.exists
import kotlin.io.path.readText
import kotlin.io.path.writeText
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse

/** The record of what is installed (`installed.json`). */
class ExtensionRegistryTest {
    private val root = createTempDirectory("registry").resolve("extensions")

    @Test
    fun `a record cut off mid-write is moved aside and the host starts with no extensions`() {
        val cut = """[{"pkg": "eu.kanade.tachiyomi.extension.en.good", "name": "Go"""
        root.createDirectories().resolve("installed.json").writeText(cut)
        val registry = ExtensionRegistry(root, OkHttpClient())
        registry.loadInstalled()
        assertEquals(emptyList(), registry.installed.keys.toList())
        assertEquals(emptyList(), registry.sources.keys.toList())
        assertFalse(root.resolve("installed.json").exists())
        assertEquals(cut, root.resolve("installed.json.bad").readText())
    }
}

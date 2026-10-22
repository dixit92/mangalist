package mangalist.extensionhost

import androidx.preference.CheckBoxPreference
import androidx.preference.EditTextPreference
import androidx.preference.ListPreference
import androidx.preference.MultiSelectListPreference
import androidx.preference.SwitchPreferenceCompat
import eu.kanade.tachiyomi.source.ConfigurableSource
import eu.kanade.tachiyomi.source.PreferenceScreen
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonPrimitive
import xyz.nulldev.androidcompat.io.sharedprefs.JavaSharedPreferences
import kotlin.io.path.createTempDirectory
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue

/** A configurable source like MangaDex (a language list, a data saver switch, ...). */
class ConfigurableFakeSource(
    baseUrl: String,
    name: String = "Configurable",
) : FakeSource(baseUrl, name),
    ConfigurableSource {
    val changes = mutableListOf<Pair<String, Any?>>()

    override fun setupPreferenceScreen(screen: PreferenceScreen) {
        screen.addPreference(
            ListPreference(screen.context).apply {
                key = "lang"
                title = "Language"
                summary = "%s"
                entries = arrayOf("English", "Japanese")
                entryValues = arrayOf("en", "ja")
                setDefaultValue("en")
                setOnPreferenceChangeListener { p, v -> changes += p.key to v; true }
            },
        )
        screen.addPreference(
            SwitchPreferenceCompat(screen.context).apply {
                key = "dataSaver"
                title = "Data saver"
                setDefaultValue(false)
            },
        )
        screen.addPreference(
            CheckBoxPreference(screen.context).apply {
                key = "nsfw"
                title = "Show NSFW"
                setDefaultValue(true)
            },
        )
        screen.addPreference(
            EditTextPreference(screen.context).apply {
                key = "blocked"
                title = "Blocked groups"
                dialogTitle = "Groups to hide"
                setDefaultValue("")
            },
        )
        screen.addPreference(
            MultiSelectListPreference(screen.context).apply {
                key = "tags"
                title = "Tags"
                entries = arrayOf("Action", "Romance")
                entryValues = arrayOf("a", "r")
                setDefaultValue(setOf("a"))
            },
        )
        screen.addPreference(
            EditTextPreference(screen.context).apply {
                key = "locked"
                title = "Locked"
                setDefaultValue("x")
                isEnabled = false
            },
        )
    }
}

/** Unit: source preferences (Suwayomi's getSourcePreferences / setSourcePreference). */
class PreferencesTest {
    companion object {
        init {
            // AndroidCompat's SharedPreferences live under this folder (the host's data folder in production)
            System.setProperty("suwayomi.tachidesk.config.server.rootDir", createTempDirectory("android").toString())
        }
    }

    private val source = ConfigurableFakeSource("http://127.0.0.1:9", "Configurable ${System.nanoTime()}")

    // one SharedPreferences per source, as AndroidCompat's CustomContext keeps them
    private val shared = mutableMapOf<Long, android.content.SharedPreferences>()

    private fun prefs() = SourcePreferences({ null }, { shared.getOrPut(it.id) { JavaSharedPreferences("source_${it.id}") } })

    @Test
    fun `a source's preferences with their kinds, entries and current values`() {
        val list = prefs().list(source).associateBy { it.key }
        assertEquals(listOf("lang", "dataSaver", "nsfw", "blocked", "tags", "locked"), prefs().list(source).map { it.key })
        assertEquals("list", list["lang"]!!.type)
        assertEquals(listOf("English", "Japanese"), list["lang"]!!.entries)
        assertEquals(listOf("en", "ja"), list["lang"]!!.entryValues)
        assertEquals(JsonPrimitive("en"), list["lang"]!!.currentValue)
        assertEquals("switch", list["dataSaver"]!!.type)
        assertEquals(JsonPrimitive(false), list["dataSaver"]!!.currentValue)
        assertEquals("checkbox", list["nsfw"]!!.type)
        assertEquals("edittext", list["blocked"]!!.type)
        assertEquals("Groups to hide", list["blocked"]!!.dialogTitle)
        assertEquals("multiselect", list["tags"]!!.type)
        assertEquals(JsonArray(listOf(JsonPrimitive("a"))), list["tags"]!!.currentValue)
        assertEquals(false, list["locked"]!!.enabled)
    }

    @Test
    fun `a change is saved, read back by a new screen, and told to the source`() {
        assertEquals(JsonPrimitive("ja"), prefs().set(source, "lang", JsonPrimitive("ja")).currentValue)
        prefs().set(source, "dataSaver", JsonPrimitive(true))
        prefs().set(source, "blocked", JsonPrimitive("Group A, Group B"))
        prefs().set(source, "tags", JsonArray(listOf(JsonPrimitive("a"), JsonPrimitive("r"))))
        val again = prefs().list(source).associateBy { it.key }
        assertEquals(JsonPrimitive("ja"), again["lang"]!!.currentValue)
        assertEquals(JsonPrimitive(true), again["dataSaver"]!!.currentValue)
        assertEquals(JsonPrimitive("Group A, Group B"), again["blocked"]!!.currentValue)
        assertEquals(setOf("a", "r"), (again["tags"]!!.currentValue as JsonArray).map { (it as JsonPrimitive).content }.toSet())
        assertTrue(("lang" to "ja") in source.changes)
    }

    @Test
    fun `wrong values, unknown keys and disabled preferences are refused`() {
        assertEquals(400, assertFailsWith<HttpError> { prefs().set(source, "lang", JsonPrimitive("fr")) }.status)
        assertEquals(400, assertFailsWith<HttpError> { prefs().set(source, "dataSaver", JsonPrimitive("yes")) }.status)
        assertEquals(400, assertFailsWith<HttpError> { prefs().set(source, "tags", JsonPrimitive("a")) }.status)
        assertEquals(404, assertFailsWith<HttpError> { prefs().set(source, "nothing", JsonPrimitive("a")) }.status)
        assertEquals(400, assertFailsWith<HttpError> { prefs().set(source, "locked", JsonPrimitive("y")) }.status)
        assertEquals(400, assertFailsWith<HttpError> { prefs().set(FakeSource("http://x"), "lang", JsonPrimitive("en")) }.status)
        assertEquals(emptyList(), prefs().list(FakeSource("http://x")))
    }
}

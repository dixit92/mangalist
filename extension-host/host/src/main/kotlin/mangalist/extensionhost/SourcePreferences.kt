package mangalist.extensionhost

import android.content.Context
import android.content.SharedPreferences
import androidx.preference.CheckBoxPreference
import androidx.preference.DialogPreference
import androidx.preference.EditTextPreference
import androidx.preference.ListPreference
import androidx.preference.MultiSelectListPreference
import androidx.preference.Preference
import androidx.preference.PreferenceScreen
import androidx.preference.SwitchPreferenceCompat
import eu.kanade.tachiyomi.source.ConfigurableSource
import eu.kanade.tachiyomi.source.Source
import eu.kanade.tachiyomi.source.sourcePreferences
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonPrimitive

/** One preference of a source, as `GET /sources/{id}/preferences` answers it. */
@Serializable
data class PreferenceDto(
    val key: String?,
    /** `list`, `multiselect`, `edittext`, `checkbox`, `switch` (or the class name of another kind). */
    val type: String,
    val title: String?,
    val summary: String?,
    val entries: List<String>? = null,
    val entryValues: List<String>? = null,
    val dialogTitle: String? = null,
    val dialogMessage: String? = null,
    val currentValue: JsonElement,
    val defaultValue: JsonElement,
    val enabled: Boolean,
    val visible: Boolean,
)

/**
 * The preferences of a [ConfigurableSource] (MangaDex's languages and image quality, ...), as Suwayomi's
 * `Source.getSourcePreferencesRaw` / `setSourcePreference` do it: the source builds its preference screen on the
 * SharedPreferences it reads (`source_<id>`, kept by AndroidCompat in the data folder), a change is saved there and
 * announced to the source's change listener, and the extension's sources are then made again (Suwayomi's
 * `unregisterSource`) so they read the new value.
 *
 * [context]: what the screen hands to the preferences the source makes (AndroidCompat's CustomContext);
 * [prefsFor]: the source's SharedPreferences.
 */
class SourcePreferences(
    private val context: () -> Context?,
    private val prefsFor: (ConfigurableSource) -> SharedPreferences = { it.sourcePreferences() },
) {
    fun screen(source: Source): List<Preference> {
        val configurable = source as? ConfigurableSource ?: return emptyList()
        val screen = PreferenceScreen(context())
        screen.sharedPreferences = prefsFor(configurable)
        configurable.setupPreferenceScreen(screen)
        return screen.preferences
    }

    fun list(source: Source): List<PreferenceDto> = screen(source).map { dto(it) }

    /**
     * Set [key] to [value] (a string for list / edittext, a boolean for checkbox / switch, a list of strings for
     * multiselect; a list preference only takes one of its entry values). A disabled preference is refused.
     */
    fun set(
        source: Source,
        key: String,
        value: JsonElement,
    ): PreferenceDto {
        if (source !is ConfigurableSource) throw HttpError(400, "${source.name} has no preferences")
        val pref = screen(source).firstOrNull { it.key == key } ?: throw HttpError(404, "${source.name} has no preference $key")
        if (!pref.isEnabled) throw HttpError(400, "$key is disabled")
        val newValue: Any =
            when (runCatching { pref.defaultValueType }.getOrNull()) {
                "String" -> (value as? JsonPrimitive)?.takeIf { it.isString || it.contentOrNull != null }?.content
                    ?: throw HttpError(400, "$key takes a string")
                "Boolean" -> (value as? JsonPrimitive)?.booleanOrNull ?: throw HttpError(400, "$key takes true or false")
                "Set<String>" ->
                    (value as? JsonArray)?.map { it.jsonPrimitive.content }?.toSet()
                        ?: throw HttpError(400, "$key takes a list of strings")
                else -> throw HttpError(400, "$key: unsupported preference type ${pref.defaultValueType}")
            }
        if (pref is ListPreference && pref.entryValues != null && newValue !in pref.entryValues.map { it.toString() }) {
            throw HttpError(400, "$key takes one of ${pref.entryValues.joinToString()}")
        }
        // as Suwayomi: saved, then the source's listener told (what it answers is not asked)
        pref.saveNewValue(newValue)
        pref.callChangeListener(newValue)
        return dto(pref)
    }

    companion object {
        fun type(p: Preference): String =
            when (p) {
                is ListPreference -> "list"
                is MultiSelectListPreference -> "multiselect"
                is EditTextPreference -> "edittext"
                is SwitchPreferenceCompat -> "switch"
                is CheckBoxPreference -> "checkbox"
                else -> p.javaClass.simpleName
            }

        private fun json(v: Any?): JsonElement =
            when (v) {
                null -> JsonNull
                is Boolean -> JsonPrimitive(v)
                is Number -> JsonPrimitive(v)
                is Collection<*> -> JsonArray(v.map { JsonPrimitive(it?.toString()) })
                else -> JsonPrimitive(v.toString())
            }

        fun dto(p: Preference): PreferenceDto {
            val (entries, values) =
                when (p) {
                    is ListPreference -> p.entries to p.entryValues
                    is MultiSelectListPreference -> p.entries to p.entryValues
                    else -> null to null
                }
            val dialog = p as? DialogPreference
            return PreferenceDto(
                key = p.key,
                type = type(p),
                title = p.title?.toString(),
                summary = runCatching { p.summary?.toString() }.getOrNull(),
                entries = entries?.map { it.toString() },
                entryValues = values?.map { it.toString() },
                dialogTitle = dialog?.dialogTitle?.toString(),
                dialogMessage = dialog?.dialogMessage?.toString(),
                currentValue = json(runCatching { p.currentValue }.getOrNull()),
                defaultValue = json(p.defaultValue),
                enabled = p.isEnabled,
                visible = p.visible,
            )
        }
    }
}

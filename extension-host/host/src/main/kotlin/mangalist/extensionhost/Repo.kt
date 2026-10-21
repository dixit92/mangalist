package mangalist.extensionhost

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.int
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.OkHttpClient
import okhttp3.Request

/**
 * An extension repository index, in either format Mihon-style apps read (checked 2026-10-21 on keiyoushi's):
 *
 * - `index.json` (current): `{name, signingKey, extensionList: {extensions: [{name, packageName, resources: {apkUrl,
 *   jarUrl, iconUrl}, extensionLib, versionCode, versionName, contentWarning, sources: [{id, name, language, homeUrl}]}]}}`
 * - `index.min.json` (legacy list): `[{name, pkg, apk, lang, code, version, nsfw, sources: [{id, name, lang, baseUrl}]}]`
 *   - keiyoushi now serves only an "update your app" entry there.
 *
 * Both are read into [RepoExtension] with an absolute [RepoExtension.apkUrl] (and [RepoExtension.jarUrl] when given). MangaList's Suwayomi used keiyoushi's
 * `index.json` ([DEFAULT_REPO]).
 */
@Serializable
data class RepoSource(
    val id: String,
    val name: String,
    val lang: String,
    val baseUrl: String = "",
)

@Serializable
data class RepoExtension(
    val name: String,
    val pkg: String,
    val apkUrl: String,
    /** A ready JVM jar of the extension (keiyoushi publishes one; Suwayomi prefers it to converting the APK). */
    val jarUrl: String? = null,
    val lang: String,
    val versionCode: Int,
    val version: String,
    val libVersion: String,
    val nsfw: Boolean,
    val sources: List<RepoSource>,
)

const val DEFAULT_REPO = "https://raw.githubusercontent.com/keiyoushi/extensions/repo/index.json"

class Repo(
    private val client: OkHttpClient,
) {
    private val json = Json { ignoreUnknownKeys = true }

    /** The repository's extensions ([indexUrl]: the index file's URL). */
    fun index(indexUrl: String = DEFAULT_REPO): List<RepoExtension> {
        val body =
            client.newCall(Request.Builder().url(indexUrl).build()).execute().use { resp ->
                check(resp.isSuccessful) { "the repository answered HTTP ${resp.code} for $indexUrl" }
                resp.body.string()
            }
        return parse(json.parseToJsonElement(body), indexUrl)
    }

    companion object {
        fun parse(
            root: JsonElement,
            indexUrl: String,
        ): List<RepoExtension> =
            when (root) {
                is JsonArray -> root.map { legacy(it.jsonObject, indexUrl.substringBeforeLast('/')) }
                is JsonObject -> root["extensionList"]!!.jsonObject["extensions"]!!.jsonArray.map { current(it.jsonObject) }
                else -> error("not an extension index")
            }

        private fun JsonObject.str(key: String): String? = this[key]?.jsonPrimitive?.contentOrNull

        private fun current(e: JsonObject): RepoExtension {
            val sources =
                e["sources"]?.jsonArray.orEmpty().map {
                    val s = it.jsonObject
                    RepoSource(s.str("id")!!, s.str("name")!!, s.str("language") ?: "", s.str("homeUrl") ?: "")
                }
            val langs = sources.map { it.lang }.toSet()
            return RepoExtension(
                name = e.str("name")!!,
                pkg = e.str("packageName")!!,
                apkUrl = e["resources"]!!.jsonObject.str("apkUrl")!!,
                jarUrl = e["resources"]!!.jsonObject.str("jarUrl"),
                lang = if (langs.size == 1) langs.first() else "all",
                versionCode = e.str("versionCode")?.toIntOrNull() ?: 0,
                version = e.str("versionName") ?: "",
                libVersion = e.str("extensionLib") ?: "",
                nsfw = (e.str("contentWarning") ?: "").let { it.isNotEmpty() && it != "CONTENT_WARNING_NONE" && it != "CONTENT_WARNING_SAFE" },
                sources = sources,
            )
        }

        private fun legacy(
            e: JsonObject,
            base: String,
        ): RepoExtension {
            val version = e.str("version") ?: ""
            return RepoExtension(
                name = e.str("name")!!,
                pkg = e.str("pkg")!!,
                apkUrl = "$base/apk/${e.str("apk")}",
                lang = e.str("lang") ?: "",
                versionCode = e["code"]?.jsonPrimitive?.intOrNull ?: 0,
                version = version,
                libVersion = version.substringBeforeLast('.'),
                nsfw = (e["nsfw"]?.jsonPrimitive?.int ?: 0) == 1,
                sources =
                    e["sources"]?.jsonArray.orEmpty().map {
                        val s = it.jsonObject
                        RepoSource(s.str("id")!!, s.str("name")!!, s.str("lang") ?: "", s.str("baseUrl") ?: "")
                    },
            )
        }
    }
}

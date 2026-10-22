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

/** A repository: its extensions and the SHA-256 of the certificate they must be signed with (null: none given). */
data class RepoIndex(
    val url: String,
    val signingKey: String?,
    val extensions: List<RepoExtension>,
)

class Repo(
    private val client: OkHttpClient,
    /** How long a fetched index is reused (the extensions list asks for the repository's versions). */
    private val cacheMillis: Long = 10 * 60 * 1000,
) {
    private val json = Json { ignoreUnknownKeys = true }
    private val cache = java.util.concurrent.ConcurrentHashMap<String, Pair<Long, RepoIndex>>()

    /** The repository's extensions ([indexUrl]: the index file's URL). */
    fun index(indexUrl: String = DEFAULT_REPO): List<RepoExtension> = fetch(indexUrl, fresh = true).extensions

    /** The repository's index with its signing key ([fresh]: not from the cache). */
    fun fetch(
        indexUrl: String = DEFAULT_REPO,
        fresh: Boolean = false,
    ): RepoIndex {
        val now = System.currentTimeMillis()
        if (!fresh) cache[indexUrl]?.let { (at, index) -> if (now - at < cacheMillis) return index }
        val root = json.parseToJsonElement(get(indexUrl) ?: error("the repository answered nothing for $indexUrl"))
        val key =
            signingKey(root)
                ?: if (root is JsonArray) {
                    // the legacy format keeps its key next to it, in repo.json
                    runCatching {
                        get(indexUrl.substringBeforeLast('/') + "/repo.json")?.let { body ->
                            json.parseToJsonElement(body).jsonObject["meta"]?.jsonObject?.str("signingKeyFingerprint")
                        }
                    }.getOrNull()
                } else {
                    null
                }
        val index = RepoIndex(indexUrl, key, parse(root, indexUrl))
        cache[indexUrl] = now to index
        return index
    }

    private fun get(url: String): String? =
        client.newCall(Request.Builder().url(url).build()).execute().use { resp ->
            check(resp.isSuccessful) { "the repository answered HTTP ${resp.code} for $url" }
            resp.body.string()
        }

    companion object {
        /** `index.json`'s `signingKey` (null in the legacy format). */
        fun signingKey(root: JsonElement): String? = (root as? JsonObject)?.str("signingKey")

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

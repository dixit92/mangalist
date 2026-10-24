package mangalist.extensionhost

import io.github.oshai.kotlinlogging.KotlinLogging
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import java.nio.file.Path
import java.util.concurrent.ConcurrentHashMap
import kotlin.io.path.createDirectories
import kotlin.io.path.exists
import kotlin.io.path.readText
import kotlin.io.path.writeText

private val logger = KotlinLogging.logger {}

/** One repository of the host's list, as kept in `repos.json`. */
@Serializable
data class RepoRecord(
    val url: String,
    val name: String = "",
    /**
     * The SHA-256 its extensions must be signed with, as its index gave it when it was added (or, for a repository
     * given with `--repo`, at its first fetch). An index that later names another key is not trusted: its extensions
     * are neither installed nor updated until the repository is removed and added again.
     */
    val signingKey: String? = null,
    val addedAt: Long = 0,
)

/** What `GET /repos` answers for one repository. */
@Serializable
data class RepoDto(
    val url: String,
    val name: String,
    val signingKey: String?,
    /** Its extensions at the last fetch (null: never fetched). */
    val extensions: Int?,
    /** When it was last fetched (ms since the epoch; null: never). */
    val lastFetch: Long?,
    /** Why the last fetch failed (or the signing key changed); null when it worked. */
    val error: String?,
)

@Serializable
data class RepoUrlRequest(
    val url: String,
)

/**
 * The host's extension repositories (Suwayomi's `extensionRepos` / Mihon's repository list), kept in
 * `<data>/repos.json` ([file]; null: in memory only, for tests). The first start seeds the list with [initial] (the
 * `--repo` options, else keiyoushi's `index.json`); a later start adds an `--repo` that is not in the list yet, and
 * never removes one: removing is `DELETE /repos`.
 *
 * Each repository's own signing key is kept with it ([RepoRecord.signingKey]): an extension is installed or updated
 * only when signed with the key of the repository it comes from.
 */
class RepoList(
    private val file: Path?,
    private val repo: Repo,
    initial: List<String> = listOf(DEFAULT_REPO),
    private val clock: () -> Long = System::currentTimeMillis,
) {
    private val json =
        Json {
            ignoreUnknownKeys = true
            prettyPrint = true
            encodeDefaults = true
        }
    private val records = LinkedHashMap<String, RepoRecord>()
    private val fetched = ConcurrentHashMap<String, Pair<Long, Int>>() // url -> (when, extensions)
    private val errors = ConcurrentHashMap<String, String>()

    init {
        val existing = file?.takeIf { it.exists() }?.let { json.decodeFromString<List<RepoRecord>>(it.readText()) }
        existing?.forEach { records[it.url] = it }
        val seed = initial.map { it.trim() }.filter { it.isNotEmpty() }.distinct()
        var added = false
        for (url in if (existing == null && seed.isEmpty()) listOf(DEFAULT_REPO) else seed) {
            if (url !in records) {
                records[url] = RepoRecord(url, Repo.fallbackName(url), null, clock())
                added = true
            }
        }
        if (added || existing == null) save()
        logger.info { "Extension repositories: ${records.keys.joinToString()}" }
    }

    /** The repositories' addresses, in the list's order. */
    @Synchronized
    fun urls(): List<String> = records.keys.toList()

    @Synchronized
    fun record(url: String): RepoRecord? = records[url]

    operator fun contains(url: String): Boolean = record(url) != null

    /** The first repository (where an extension installed without a recorded repository is updated from). */
    fun first(): String? = urls().firstOrNull()

    /**
     * Add a repository: its index is fetched and checked first - a URL that is not an extension index, or one that
     * gives no signing key, is refused ([HttpError] 400). Adding one already listed answers it as it is.
     */
    fun add(rawUrl: String): RepoDto {
        val url = rawUrl.trim()
        if (!Regex("^https?://", RegexOption.IGNORE_CASE).containsMatchIn(url)) throw HttpError(400, "a repository address starts with http:// or https://")
        record(url)?.let { return dto(it) }
        val index =
            try {
                repo.fetch(url, fresh = true)
            } catch (e: kotlinx.serialization.SerializationException) {
                throw HttpError(400, "$url is not an extension repository: it does not answer JSON (give the address of its index.json)")
            } catch (e: Exception) {
                throw HttpError(400, "$url is not an extension repository: ${e.message ?: e.javaClass.simpleName}")
            }
        val key = normalized(index.signingKey) ?: throw HttpError(400, "$url gives no signing key: its extensions could not be checked")
        val record = RepoRecord(url, index.name, key, clock())
        synchronized(this) {
            records[url] = record
            save()
        }
        fetched[url] = clock() to index.extensions.size
        errors.remove(url)
        logger.info { "Repository added: ${index.name} ($url), ${index.extensions.size} extension(s), key $key" }
        return dto(record)
    }

    /** Remove a repository. Extensions installed from it stay installed and keep working; they get no updates. */
    fun remove(rawUrl: String): RepoDto {
        val url = rawUrl.trim()
        val gone =
            synchronized(this) {
                records.remove(url)?.also { save() }
            } ?: throw HttpError(404, "$url is not in the repository list")
        fetched.remove(url)
        errors.remove(url)
        logger.info { "Repository removed: ${gone.name} ($url)" }
        return dto(gone)
    }

    /**
     * A listed repository's index, its signing key checked against the one kept for it (the first fetch of a
     * repository given with `--repo` keeps the key it finds). [HttpError] 404 for a repository not in the list, 400
     * when its key changed, 502 when it cannot be fetched.
     */
    fun index(
        url: String,
        fresh: Boolean = false,
    ): RepoIndex {
        val record = record(url) ?: throw HttpError(404, "$url is not in the repository list: add it first")
        val index =
            try {
                repo.fetch(url, fresh)
            } catch (e: Exception) {
                val message = e.message ?: e.javaClass.simpleName
                errors[url] = message
                throw HttpError(502, "the repository $url could not be read: $message")
            }
        fetched[url] = clock() to index.extensions.size
        val key = normalized(index.signingKey)
        val kept = normalized(record.signingKey)
        when {
            key == null -> {
                errors[url] = "it gives no signing key"
                throw HttpError(400, "${record.name} gives no signing key: its extensions cannot be checked")
            }
            kept == null -> {
                synchronized(this) {
                    records[url] = record.copy(signingKey = key, name = index.name)
                    save()
                }
                logger.info { "Repository ${index.name}: signing key $key kept" }
            }
            key != kept -> {
                val message = "its signing key changed from $kept to $key: not trusted (remove it and add it again to trust the new key)"
                errors[url] = message
                throw HttpError(400, "${record.name}: $message")
            }
            index.name != record.name ->
                synchronized(this) {
                    records[url] = record.copy(name = index.name)
                    save()
                }
        }
        errors.remove(url)
        return index.copy(signingKey = key)
    }

    /** Every listed repository's extensions, each saying which repository it is from (a repository that fails is skipped). */
    fun allExtensions(): List<RepoExtension> =
        urls().flatMap { url ->
            runCatching { index(url).extensions }
                .onFailure { logger.warn { "Repository $url: ${it.message}" } }
                .getOrDefault(emptyList())
        }

    /** The list with each repository's state (each is read from the cache, else fetched). */
    fun dtos(fetch: Boolean = true): List<RepoDto> =
        urls().mapNotNull { url ->
            if (fetch) runCatching { index(url) }
            record(url)?.let { dto(it) }
        }

    private fun dto(r: RepoRecord): RepoDto {
        val seen = fetched[r.url]
        return RepoDto(r.url, r.name.ifBlank { Repo.fallbackName(r.url) }, r.signingKey, seen?.second, seen?.first, errors[r.url])
    }

    private fun save() {
        val target = file ?: return
        target.parent?.createDirectories()
        val tmp = target.resolveSibling(target.fileName.toString() + ".tmp")
        tmp.writeText(json.encodeToString(records.values.toList()))
        java.nio.file.Files.move(tmp, target, java.nio.file.StandardCopyOption.REPLACE_EXISTING, java.nio.file.StandardCopyOption.ATOMIC_MOVE)
    }

    private fun normalized(key: String?): String? = key?.replace(":", "")?.trim()?.lowercase()?.takeIf { it.isNotEmpty() }
}

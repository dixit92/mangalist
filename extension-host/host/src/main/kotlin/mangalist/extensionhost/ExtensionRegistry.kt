package mangalist.extensionhost

import eu.kanade.tachiyomi.source.Source
import eu.kanade.tachiyomi.source.SourceFactory
import io.github.oshai.kotlinlogging.KotlinLogging
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import okhttp3.OkHttpClient
import okhttp3.Request
import suwayomi.tachidesk.manga.impl.util.AndroidManifestParser
import suwayomi.tachidesk.manga.impl.util.PackageTools
import suwayomi.tachidesk.manga.impl.util.PackageTools.EXTENSION_FEATURE
import suwayomi.tachidesk.manga.impl.util.PackageTools.LIB_VERSION_MAX
import suwayomi.tachidesk.manga.impl.util.PackageTools.LIB_VERSION_MIN
import suwayomi.tachidesk.manga.impl.util.PackageTools.METADATA_EXTENSION_LIB
import suwayomi.tachidesk.manga.impl.util.PackageTools.METADATA_NAME
import suwayomi.tachidesk.manga.impl.util.PackageTools.METADATA_NSFW
import suwayomi.tachidesk.manga.impl.util.PackageTools.METADATA_SOURCE_CLASS
import java.net.URLClassLoader
import java.nio.file.FileSystems
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.StandardCopyOption
import java.util.concurrent.ConcurrentHashMap
import java.util.zip.ZipFile
import java.util.zip.ZipInputStream
import kotlin.io.path.createDirectories
import kotlin.io.path.deleteIfExists
import kotlin.io.path.exists
import kotlin.io.path.inputStream
import kotlin.io.path.readText
import kotlin.io.path.writeText

private val logger = KotlinLogging.logger {}

/** One installed extension, as kept in `installed.json` (enough to load it again without the APK). */
@Serializable
data class InstalledExtension(
    val pkg: String,
    val name: String,
    val version: String,
    val versionCode: Int,
    val libVersion: String,
    val className: String,
    val jar: String,
    val nsfw: Int = 0,
    /** SHA-256 of the certificate the package was signed with, checked against its repository's key at install. */
    val signatureHash: String? = null,
    /** The repository index it was installed from (where an update comes from). */
    val repo: String? = null,
)

/**
 * The extensions this host has installed and the sources they provide. Installing follows Suwayomi's
 * `Extension.installAPK` (same checks, same dex2jar conversion and class loading, through the vendored
 * [PackageTools]), without its database: what is installed is a small JSON file in the extensions folder.
 */
class ExtensionRegistry(
    private val root: Path,
    private val client: OkHttpClient,
) {
    private val json =
        Json {
            ignoreUnknownKeys = true
            prettyPrint = true
        }
    private val manifest = root.resolve("installed.json")

    val installed = ConcurrentHashMap<String, InstalledExtension>()
    val sources = ConcurrentHashMap<Long, Source>()
    private val sourcesByPkg = ConcurrentHashMap<String, List<Long>>()

    init {
        root.createDirectories()
    }

    /** Load every extension recorded in `installed.json` (one that fails to load is logged and skipped). */
    fun loadInstalled() {
        if (!manifest.exists()) return
        val recorded = json.decodeFromString<List<InstalledExtension>>(manifest.readText())
        for (ext in recorded) {
            try {
                register(ext, instantiate(root.resolve(ext.jar), ext.className))
            } catch (e: Throwable) {
                logger.error(e) { "Could not load ${ext.pkg} (${ext.jar})" }
            }
        }
        logger.info { "Loaded ${installed.size} extension(s), ${sources.size} source(s)" }
    }

    /** Make every installed extension's sources again (new instances; their headers pick up the current User-Agent). */
    @Synchronized
    fun reloadSources() {
        for (ext in installed.values.toList()) {
            try {
                register(ext, instantiate(root.resolve(ext.jar), ext.className))
            } catch (e: Throwable) {
                logger.error(e) { "Could not make ${ext.pkg}'s sources again" }
            }
        }
    }

    /**
     * Download the extension at [url] and install it (replacing an installed version of the same package): a `.jar`
     * (keiyoushi's ready JVM jar, as Suwayomi prefers) or an `.apk` (converted with dex2jar). It must be signed with
     * [signingKey] (its repository's key, [Signatures.check]), else it is refused before anything is loaded.
     */
    @Synchronized
    fun install(
        url: String,
        signingKey: String?,
        repo: String? = null,
    ): Pair<InstalledExtension, List<Source>> {
        val dir = root.resolve("download").createDirectories()
        val file = dir.resolve(url.substringAfterLast('/').substringBefore('?'))
        client.newCall(Request.Builder().url(url).build()).execute().use { resp ->
            check(resp.isSuccessful) { "the extension download answered HTTP ${resp.code} for $url" }
            Files.copy(resp.body.byteStream(), file, StandardCopyOption.REPLACE_EXISTING)
        }
        try {
            return installFile(file, signingKey, repo)
        } finally {
            file.deleteIfExists()
        }
    }

    /** Check and install a downloaded package file (see [install]). */
    @Synchronized
    fun installFile(
        file: Path,
        signingKey: String?,
        repo: String? = null,
    ): Pair<InstalledExtension, List<Source>> {
        val fingerprint = Signatures.check(file, signingKey)
        val (ext, made) = if (file.toString().endsWith(".jar")) installJar(file) else installApk(file)
        val recorded = ext.copy(signatureHash = fingerprint, repo = repo)
        installed[ext.pkg] = recorded
        saveManifest()
        return recorded to made
    }

    /** Remove an installed extension: its sources, its jar and its record. Its sources' preferences are kept. */
    @Synchronized
    fun uninstall(pkg: String): InstalledExtension {
        val ext = installed[pkg] ?: throw HttpError(404, "$pkg is not installed")
        unload(pkg)
        saveManifest()
        logger.info { "Uninstalled ${ext.name} ${ext.version} ($pkg)" }
        return ext
    }

    /** Make one extension's sources again (after a preference change, as Suwayomi's `unregisterSource`). */
    @Synchronized
    fun reloadPackage(pkg: String) {
        val ext = installed[pkg] ?: return
        register(ext, instantiate(root.resolve(ext.jar), ext.className))
    }

    /** What both package kinds say about themselves (Suwayomi's `Extension.PackageMetadata`). */
    private class PackageMeta(
        val pkg: String,
        val versionName: String,
        val versionCode: Int,
        val features: List<String>,
        val label: String?,
        val meta: (String) -> String?,
    )

    private fun installJar(file: Path): Pair<InstalledExtension, List<Source>> {
        val manifest =
            ZipFile(file.toFile()).use { zip ->
                val entry = zip.getEntry("AndroidManifest.xml") ?: error("the jar has no AndroidManifest.xml")
                zip.getInputStream(entry).use { AndroidManifestParser.parse(it) }
            }
        val app = manifest.application
        val meta =
            PackageMeta(
                pkg = manifest.packageName,
                versionName = requireNotNull(manifest.versionName) { "the jar's manifest has no version" },
                versionCode = manifest.versionCode ?: 0,
                features = manifest.usesFeatures.mapNotNull { it.name },
                label = app?.label,
                meta = { key -> app?.metaData?.find { it.name == key }?.value },
            )
        return finish(meta) { jar -> Files.copy(file, jar, StandardCopyOption.REPLACE_EXISTING) }
    }

    private fun installApk(file: Path): Pair<InstalledExtension, List<Source>> {
        val info = PackageTools.getPackageInfo(file)
        val bundle = info.applicationInfo.metaData
        val meta =
            PackageMeta(
                pkg = info.packageName,
                versionName = info.versionName,
                versionCode = info.versionCode,
                features = info.reqFeatures.orEmpty().mapNotNull { it.name },
                label = info.applicationInfo.nonLocalizedLabel?.toString(),
                meta = { key -> bundle.getString(key) },
            )
        return finish(meta) { jar ->
            PackageTools.dex2jar(file, jar)
            copyAssets(file, jar)
        }
    }

    /** Suwayomi's `installExtension` checks, then make the jar ([prepare]), load it and record it. */
    private fun finish(
        m: PackageMeta,
        prepare: (Path) -> Unit,
    ): Pair<InstalledExtension, List<Source>> {
        val pkg = m.pkg
        require(EXTENSION_FEATURE in m.features) { "$pkg is not a Tachiyomi / Mihon extension" }
        val libVersion = m.versionName.substringBeforeLast('.').toDouble()
        require(libVersion in LIB_VERSION_MIN..LIB_VERSION_MAX) {
            "$pkg uses extension lib $libVersion; only $LIB_VERSION_MIN to $LIB_VERSION_MAX can run"
        }
        val sourceClass = requireNotNull(m.meta(METADATA_SOURCE_CLASS)) { "$pkg names no source class" }.trim()
        val className = if (sourceClass.startsWith(".")) pkg + sourceClass else sourceClass
        val ext =
            InstalledExtension(
                pkg = pkg,
                name = m.meta(METADATA_NAME) ?: m.label?.substringAfter("Tachiyomi: ") ?: pkg,
                version = m.versionName,
                versionCode = m.versionCode,
                libVersion = m.meta(METADATA_EXTENSION_LIB)?.takeUnless { it == "0" || it.startsWith("{") } ?: libVersion.toString(),
                className = className,
                jar = "$pkg-v${m.versionName}.jar",
                nsfw = m.meta(METADATA_NSFW)?.toIntOrNull() ?: 0,
            )
        val jar = root.resolve(ext.jar)
        unload(pkg)
        jar.deleteIfExists()
        prepare(jar)
        val made = register(ext, instantiate(jar, className))
        saveManifest()
        logger.info { "Installed ${ext.name} ${ext.version} ($pkg): ${made.size} source(s)" }
        return ext to made
    }

    private fun instantiate(
        jar: Path,
        className: String,
    ): Any = runBlocking { PackageTools.loadExtensionSources(jar, className) }

    private fun register(
        ext: InstalledExtension,
        instance: Any,
    ): List<Source> {
        val made =
            when (instance) {
                is Source -> listOf(instance)
                is SourceFactory -> instance.createSources()
                else -> error("unknown source class type ${instance.javaClass}")
            }
        installed[ext.pkg] = ext
        made.forEach { sources[it.id] = it }
        sourcesByPkg[ext.pkg] = made.map { it.id }
        return made
    }

    /** Forget a package's sources and close its class loader (before an update). */
    private fun unload(pkg: String) {
        sourcesByPkg.remove(pkg)?.forEach { sources.remove(it) }
        val old = installed.remove(pkg) ?: return
        val jarPath = root.resolve(old.jar).toAbsolutePath().toString()
        (PackageTools.jarLoaderMap.remove(jarPath) as? URLClassLoader)?.close()
        root.resolve(old.jar).deleteIfExists()
    }

    fun extensionOf(sourceId: Long): InstalledExtension? =
        sourcesByPkg.entries.firstOrNull { sourceId in it.value }?.let { installed[it.key] }

    private fun saveManifest() {
        manifest.writeText(json.encodeToString(installed.values.sortedBy { it.pkg }))
    }

    /** Some extensions read files from their APK's `assets/` (as Suwayomi's `extractAssetsFromApk`): put them in the jar. */
    private fun copyAssets(
        apk: Path,
        jar: Path,
    ) {
        FileSystems.newFileSystem(jar).use { fs ->
            ZipInputStream(apk.inputStream()).use { zip ->
                var entry = zip.nextEntry
                while (entry != null) {
                    if (entry.name.startsWith("assets/") && !entry.isDirectory) {
                        val target = fs.getPath(entry.name)
                        target.parent?.let { Files.createDirectories(it) }
                        Files.copy(zip, target, StandardCopyOption.REPLACE_EXISTING)
                    }
                    entry = zip.nextEntry
                }
            }
        }
    }
}

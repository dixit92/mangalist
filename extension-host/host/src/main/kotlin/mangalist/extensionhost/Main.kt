package mangalist.extensionhost

import android.os.Looper
import eu.kanade.tachiyomi.App
import eu.kanade.tachiyomi.createAppModule
import eu.kanade.tachiyomi.network.NetworkHelper
import io.github.oshai.kotlinlogging.KotlinLogging
import org.koin.core.context.startKoin
import uy.kohesive.injekt.Injekt
import uy.kohesive.injekt.api.get
import xyz.nulldev.androidcompat.AndroidCompat
import xyz.nulldev.androidcompat.AndroidCompatInitializer
import xyz.nulldev.androidcompat.androidCompatModule
import xyz.nulldev.ts.config.configManagerModule
import kotlinx.coroutines.DelicateCoroutinesApi
import kotlinx.coroutines.GlobalScope
import kotlinx.coroutines.flow.drop
import kotlinx.coroutines.flow.launchIn
import kotlinx.coroutines.flow.onEach
import suwayomi.tachidesk.server.serverConfig
import java.nio.file.Path
import kotlin.io.path.Path
import kotlin.io.path.absolute
import kotlin.io.path.createDirectories

/**
 * MangaList's extension host (prototype): runs Mihon / Tachiyomi extensions (keiyoushi's repository) on a plain JVM
 * and answers MangaList over a small local JSON API ([ApiServer]). No Suwayomi server, database or web UI: only
 * Suwayomi's Android compatibility layer and Tachiyomi source API, vendored (vendor/suwayomi, MPL-2.0).
 *
 * Usage: `mangalist-extension-host [--data DIR] [--bind 127.0.0.1] [--port 4570] [--flaresolverr URL]
 * [--flaresolverr-timeout 60] [--flaresolverr-session mangalist] [--flaresolverr-session-ttl 15]`
 *
 * Cloudflare: with `--flaresolverr` (or `MANGALIST_FLARESOLVERR_URL`) a request Cloudflare challenges is solved by the
 * owner's FlareSolverr container through Suwayomi's CloudflareInterceptor (vendored unchanged); without it such a
 * request fails ("Cloudflare bypass currently disabled").
 */

const val HOST_VERSION = "0.1.0-prototype"

private val logger = KotlinLogging.logger {}

data class Options(
    val dataDir: Path,
    val bind: String,
    val port: Int,
    val flareSolverrUrl: String? = null,
    val flareSolverrTimeout: Int = 60,
    val flareSolverrSession: String = "mangalist",
    val flareSolverrSessionTtl: Int = 15,
    /** Where chapters are downloaded (`<downloads>/mangas/...`); default `<data>/downloads`. */
    val downloadsDir: Path = dataDir.resolve("downloads"),
    /** The extension repository's index (`index.json`). */
    val repo: String = DEFAULT_REPO,
    /** A browser engine (Chromium through JCEF, downloaded at first use) for extensions that open a WebView. */
    val webView: Boolean = true,
    /** Chapters downloaded at once (from different sources; one at a time per source). */
    val downloadParallel: Int = 2,
) {
    companion object {
        const val USAGE =
            "mangalist-extension-host [--data DIR] [--bind 127.0.0.1] [--port 4570] [--downloads DIR] [--repo URL] " +
                "[--webview on|off] [--download-parallel 2] [--flaresolverr URL] [--flaresolverr-timeout 60] " +
                "[--flaresolverr-session mangalist] [--flaresolverr-session-ttl 15]"

        fun parse(args: Array<String>): Options {
            var data = System.getenv("MANGALIST_EXTENSIONS_DATA") ?: "extension-host-data"
            var bind = "127.0.0.1"
            var port = 4570
            var flare = System.getenv("MANGALIST_FLARESOLVERR_URL")?.takeIf { it.isNotBlank() }
            var flareTimeout = 60
            var flareSession = "mangalist"
            var flareTtl = 15
            var downloads: String? = null
            var repo = DEFAULT_REPO
            var webView = true
            var parallel = 2
            var i = 0
            while (i < args.size) {
                when (args[i]) {
                    "--data" -> data = args[++i]
                    "--bind" -> bind = args[++i]
                    "--port" -> port = args[++i].toInt()
                    "--downloads" -> downloads = args[++i]
                    "--repo" -> repo = args[++i]
                    "--webview" ->
                        webView =
                            when (args[++i].lowercase()) {
                                "on", "true", "yes" -> true
                                "off", "false", "no" -> false
                                else -> throw IllegalArgumentException("--webview takes on or off")
                            }
                    "--download-parallel" -> parallel = args[++i].toInt().coerceIn(1, 4)
                    "--flaresolverr" -> flare = args[++i].takeIf { it.isNotBlank() }
                    "--flaresolverr-timeout" -> flareTimeout = args[++i].toInt()
                    "--flaresolverr-session" -> flareSession = args[++i]
                    "--flaresolverr-session-ttl" -> flareTtl = args[++i].toInt()
                    "--help", "-h" -> {
                        println(USAGE)
                        kotlin.system.exitProcess(0)
                    }
                    else -> throw IllegalArgumentException("unknown argument ${args[i]}")
                }
                i++
            }
            val dataDir = Path(data).absolute()
            return Options(
                dataDir, bind, port, flare, flareTimeout, flareSession, flareTtl,
                downloadsDir = downloads?.let { Path(it).absolute() } ?: dataDir.resolve("downloads"),
                repo = repo,
                webView = webView,
                downloadParallel = parallel,
            )
        }
    }
}

/** Start the Android compatibility layer the way Suwayomi does, minus its server (ServerSetup.applicationSetup). */
fun boot(dataDir: Path) {
    dataDir.createDirectories()
    val extensions = dataDir.resolve("extensions").createDirectories()
    // AndroidCompat keeps its files (SharedPreferences of the extensions, caches) under this root
    System.setProperty("suwayomi.tachidesk.config.server.rootDir", dataDir.resolve("android").toString())
    System.setProperty("mangalist.extensions.dir", extensions.toString())
    // Android's main thread (Suwayomi's ServerSetup does the same): Handler posts and AndroidCompat's
    // Dispatchers.Main (kotlinx.coroutines.android.AndroidDispatcherFactory) run on it; Comix 1.6.54 needs it.
    val looperReady = java.util.concurrent.CountDownLatch(1)
    Thread({
        Looper.prepareMainLooper()
        looperReady.countDown()
        Looper.loop()
    }, "android-main").apply { isDaemon = true }.start()
    looperReady.await()
    val app = App()
    startKoin { modules(createAppModule(app), androidCompatModule(), configManagerModule()) }
    AndroidCompatInitializer().init()
    AndroidCompat().startApp(app)
    // Injekt (what extensions use) answers from Koin through injekt-koin
    val network = Injekt.get<NetworkHelper>()
    System.setProperty("http.agent", network.defaultUserAgentProvider())
}

fun main(args: Array<String>) {
    val options = Options.parse(args)
    logger.info { "MangaList extension host $HOST_VERSION; data folder ${options.dataDir}" }
    serverConfig.set(options.flareSolverrUrl, options.flareSolverrTimeout, options.flareSolverrSession, options.flareSolverrSessionTtl)
    logger.info {
        if (options.flareSolverrUrl != null) {
            "Cloudflare: FlareSolverr at ${serverConfig.flareSolverrUrl.value} (session ${options.flareSolverrSession})"
        } else {
            "Cloudflare: no FlareSolverr (--flaresolverr URL); challenged requests fail"
        }
    }
    boot(options.dataDir)
    val network = Injekt.get<NetworkHelper>()
    val registry = ExtensionRegistry(options.dataDir.resolve("extensions"), network.client)
    registry.loadInstalled()
    // FlareSolverr's answer brings its browser's User-Agent, which Cloudflare's clearance cookie is bound to. Sources
    // build their headers (with the User-Agent) once, so make them again - as Suwayomi's NetworkHelper does with
    // GetSource.unregisterAllSources().
    @OptIn(DelicateCoroutinesApi::class)
    network.userAgentFlow
        .drop(1)
        .onEach {
            logger.info { "User-Agent changed (FlareSolverr): making the sources again" }
            registry.reloadSources()
        }.launchIn(GlobalScope)
    val store = Store(options.dataDir.resolve("host.db"))
    val library = Library(store, { registry.sources[it] }, DownloadsLayout(options.downloadsDir))
    val downloader = Downloader(store, library, options.dataDir.resolve("cache").resolve("downloads"), options.downloadParallel)
    downloader.restore()
    val compat = CompatApi(library, downloader, registry)
    val server = ApiServer(options.bind, options.port, registry, Repo(network.client), compat)
    server.start()
    logger.info { "Listening on http://${options.bind}:${server.port}" }
}

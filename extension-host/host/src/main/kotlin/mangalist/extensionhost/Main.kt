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
import java.nio.file.Path
import kotlin.io.path.Path
import kotlin.io.path.absolute
import kotlin.io.path.createDirectories

/**
 * MangaList's extension host (prototype): runs Mihon / Tachiyomi extensions (keiyoushi's repository) on a plain JVM
 * and answers MangaList over a small local JSON API ([ApiServer]). No Suwayomi server, database or web UI: only
 * Suwayomi's Android compatibility layer and Tachiyomi source API, vendored (vendor/suwayomi, MPL-2.0).
 *
 * Usage: `mangalist-extension-host [--data DIR] [--bind 127.0.0.1] [--port 4570]`
 */

const val HOST_VERSION = "0.1.0-prototype"

private val logger = KotlinLogging.logger {}

data class Options(
    val dataDir: Path,
    val bind: String,
    val port: Int,
) {
    companion object {
        fun parse(args: Array<String>): Options {
            var data = System.getenv("MANGALIST_EXTENSIONS_DATA") ?: "extension-host-data"
            var bind = "127.0.0.1"
            var port = 4570
            var i = 0
            while (i < args.size) {
                when (args[i]) {
                    "--data" -> data = args[++i]
                    "--bind" -> bind = args[++i]
                    "--port" -> port = args[++i].toInt()
                    "--help", "-h" -> {
                        println("mangalist-extension-host [--data DIR] [--bind 127.0.0.1] [--port 4570]")
                        kotlin.system.exitProcess(0)
                    }
                    else -> throw IllegalArgumentException("unknown argument ${args[i]}")
                }
                i++
            }
            return Options(Path(data).absolute(), bind, port)
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
    boot(options.dataDir)
    val network = Injekt.get<NetworkHelper>()
    val registry = ExtensionRegistry(options.dataDir.resolve("extensions"), network.client)
    registry.loadInstalled()
    val server = ApiServer(options.bind, options.port, registry, Repo(network.client))
    server.start()
    logger.info { "Listening on http://${options.bind}:${server.port}" }
}

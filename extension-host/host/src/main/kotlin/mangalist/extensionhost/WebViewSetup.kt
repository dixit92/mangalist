package mangalist.extensionhost

import android.webkit.WebView
import eu.kanade.tachiyomi.network.NetworkHelper
import io.github.oshai.kotlinlogging.KotlinLogging
import okhttp3.Cookie
import org.cef.network.CefCookie
import org.cef.network.CefCookieManager
import org.koin.core.module.Module
import org.koin.dsl.module
import suwayomi.tachidesk.server.serverConfig
import suwayomi.tachidesk.server.util.CEFManager
import uy.kohesive.injekt.Injekt
import uy.kohesive.injekt.api.get
import xyz.nulldev.androidcompat.webkit.CefHelper
import xyz.nulldev.androidcompat.webkit.KcefWebViewProvider
import java.nio.file.Path
import java.util.Date

private val log = KotlinLogging.logger {}

/**
 * The WebView extensions open (Comix and other JavaScript-heavy sites): Suwayomi's KCEF provider (vendored
 * `AndroidCompat/.../webkit/Kcef*`), on JetBrains' JCEF with the Chromium libraries of a JetBrains Runtime release
 * (Suwayomi's CEFManager, vendored), downloaded into `<data>/bin/kcef` the first time a WebView is needed - never part of
 * the repository or an image. Chromium needs an X display (`DISPLAY`; Suwayomi's image runs Xvfb, MangaList's container
 * has one).
 *
 * With `--webview off` CEF is never started: an extension that opens a WebView fails with "CEF is disabled".
 */
object WebViewSetup {
    /** Koin: Suwayomi's ServerSetup gives every new browser the cookies the host's HTTP client holds. */
    fun koinModule(): Module =
        module {
            single<KcefWebViewProvider.InitBrowserHandler> {
                object : KcefWebViewProvider.InitBrowserHandler {
                    override fun init(provider: KcefWebViewProvider) {
                        val networkHelper = Injekt.get<NetworkHelper>()
                        log.info { "Start loading cookies" }
                        CefCookieManager.getGlobalManager().apply {
                            for (cookie in networkHelper.cookieStore.getStoredCookies()) {
                                try {
                                    if (!setCookie("https://" + cookie.domain, cookie.toCefCookie())) throw Exception()
                                } catch (e: Exception) {
                                    log.warn(e) { "Loading cookie ${cookie.name} failed" }
                                }
                            }
                        }
                    }
                }
            }
        }

    /** Suwayomi's `KcefWebView.toCefCookie`. */
    fun Cookie.toCefCookie(): CefCookie =
        CefCookie(
            name,
            value,
            if (hostOnly) domain else ".$domain",
            path,
            secure,
            httpOnly,
            Date(),
            null,
            expiresAt < 253402300799999L, // okhttp3.internal.http.MAX_DATE
            Date(expiresAt),
        )

    /** Set CEF's folders and switch; the provider starts CEF (download, then init) when a WebView is first made. */
    fun configure(
        enabled: Boolean,
        dataDir: Path,
    ) {
        serverConfig.kcefEnabled.value = enabled
        serverConfig.dataRoot.value = dataDir.toAbsolutePath().toString()
        serverConfig.cacheRoot.value = dataDir.resolve("cache").toAbsolutePath().toString()
        WebView.setProviderFactory { view: WebView ->
            CEFManager.init()
            KcefWebViewProvider(view)
        }
    }

    /** Start CEF now (download Chromium if needed) instead of at the first WebView. */
    fun start() = CEFManager.init()

    /** `off`, `not started`, `starting`, `ready`, or `failed: <why>`. */
    fun state(): String =
        when {
            !serverConfig.kcefEnabled.value -> "off"
            !CEFManager.started -> "not started"
            else -> {
                val app = CefHelper.cefApp.value
                when {
                    app.isFailure -> "failed: ${app.exceptionOrNull()?.message}"
                    app.getOrNull() == null -> "starting"
                    else -> "ready"
                }
            }
        }
}

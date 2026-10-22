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
        clearStaleProfileLock(dataDir.resolve("cache").resolve("kcef"))
        WebView.setProviderFactory { view: WebView ->
            CEFManager.init()
            KcefWebViewProvider(view)
        }
    }

    /**
     * Diagnostics: open [url] in a WebView, run [script] once the page has loaded (and [waitMs] more), then answer
     * `JSON.stringify(window.__mlDebug)` read [waitMs] later - what a page script left there. Local API only.
     */
    fun debugEval(
        url: String,
        script: String,
        waitMs: Long,
    ): String? {
        val result = java.util.concurrent.CompletableFuture<String?>()
        val main = android.os.Handler(android.os.Looper.getMainLooper())
        var view: WebView? = null
        main.post {
            try {
                val wv = WebView(Injekt.get<android.content.Context>())
                view = wv
                wv.settings.javaScriptEnabled = true
                wv.webViewClient =
                    object : android.webkit.WebViewClient() {
                        private var done = false

                        override fun onPageFinished(
                            v: WebView,
                            u: String?,
                        ) {
                            if (done) return
                            done = true
                            main.postDelayed({
                                v.evaluateJavascript(script, null)
                                main.postDelayed({ v.evaluateJavascript("JSON.stringify(window.__mlDebug)") { result.complete(it) } }, waitMs)
                            }, waitMs)
                        }
                    }
                wv.loadUrl(url)
            } catch (e: Throwable) {
                result.completeExceptionally(e)
            }
        }
        return try {
            result.get(waitMs * 2 + 120_000, java.util.concurrent.TimeUnit.MILLISECONDS)
        } finally {
            main.post { view?.destroy() }
        }
    }

    /**
     * Chromium locks its profile (the CEF cache folder) with `Singleton*` links naming the machine; a container made
     * again (a new host name) then finds the old lock and Chromium refuses to start ("The profile appears to be in use
     * by another Chromium process ... on another computer", exit code 21). One host owns its data folder, so a lock
     * left there is stale: remove it before CEF starts.
     */
    fun clearStaleProfileLock(profile: Path) {
        for (name in listOf("SingletonLock", "SingletonSocket", "SingletonCookie")) {
            try {
                if (java.nio.file.Files.deleteIfExists(profile.resolve(name))) log.info { "Removed Chromium's stale $name" }
            } catch (e: Exception) {
                log.warn(e) { "Could not remove Chromium's $name" }
            }
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
                    else ->
                        when (val cef = org.cef.CefApp.getState()) {
                            org.cef.CefApp.CefAppState.INITIALIZED -> "ready"
                            org.cef.CefApp.CefAppState.SHUTTING_DOWN, org.cef.CefApp.CefAppState.TERMINATED -> "failed: Chromium stopped ($cef)"
                            else -> "starting"
                        }
                }
            }
        }
}

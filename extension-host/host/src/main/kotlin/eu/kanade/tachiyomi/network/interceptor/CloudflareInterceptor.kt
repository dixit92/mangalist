package eu.kanade.tachiyomi.network.interceptor

import okhttp3.Interceptor
import okhttp3.Response

/**
 * MangaList extension host (prototype): a pass-through in place of Suwayomi's FlareSolverr-backed interceptor.
 *
 * keiyoushi's shared extension code requires the default client to hold an interceptor of this simple name
 * ("CloudflareInterceptor must be present in default client", checked 2026-10-21 in MangaDex 1.6.0), so it has to
 * exist; solving Cloudflare challenges is not part of the prototype.
 */
class CloudflareInterceptor(
    @Suppress("unused") private val setUserAgent: (String) -> Unit = {},
) : Interceptor {
    override fun intercept(chain: Interceptor.Chain): Response = chain.proceed(chain.request())
}

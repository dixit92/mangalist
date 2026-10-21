package suwayomi.tachidesk.server

import kotlinx.coroutines.flow.MutableStateFlow

/**
 * MangaList extension host (prototype): the FlareSolverr settings Suwayomi's CloudflareInterceptor reads (vendored
 * unchanged; it imports `suwayomi.tachidesk.server.serverConfig`). Only these keys exist: the rest of Suwayomi's
 * server config is not part of the host. Set from the command line / environment at start ([HostFlareSolverr.set]).
 *
 * FlareSolverr is the owner's own container (owner, 2026-10-21: "we already have a Flaresolverr docker, so it doesn't
 * need to be a part of MangaList"); Suwayomi uses it at http://192.168.2.50:8191.
 */
class HostFlareSolverr {
    val flareSolverrEnabled = MutableStateFlow(false)
    val flareSolverrUrl = MutableStateFlow("http://localhost:8191")
    val flareSolverrTimeout = MutableStateFlow(60) // seconds
    val flareSolverrSessionName = MutableStateFlow("mangalist")
    val flareSolverrSessionTtl = MutableStateFlow(15) // minutes
    val flareSolverrAsResponseFallback = MutableStateFlow(false)

    /** Turn FlareSolverr on at [url] (null / blank: off). */
    fun set(
        url: String?,
        timeoutSeconds: Int = 60,
        sessionName: String = "mangalist",
        sessionTtlMinutes: Int = 15,
        asResponseFallback: Boolean = false,
    ) {
        flareSolverrEnabled.value = !url.isNullOrBlank()
        if (!url.isNullOrBlank()) flareSolverrUrl.value = url.trim().removeSuffix("/")
        flareSolverrTimeout.value = timeoutSeconds
        flareSolverrSessionName.value = sessionName
        flareSolverrSessionTtl.value = sessionTtlMinutes
        flareSolverrAsResponseFallback.value = asResponseFallback
    }
}

val serverConfig = HostFlareSolverr()

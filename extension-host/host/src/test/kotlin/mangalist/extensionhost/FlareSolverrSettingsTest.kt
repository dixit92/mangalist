package mangalist.extensionhost

import suwayomi.tachidesk.server.HostFlareSolverr
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

/** The FlareSolverr switch Suwayomi's CloudflareInterceptor reads: a URL turns it on, none (or blank) leaves it off. */
class FlareSolverrSettingsTest {
    @Test
    fun `a url turns flaresolverr on with the given session`() {
        val config = HostFlareSolverr()
        config.set(" http://192.168.2.50:8191/ ", timeoutSeconds = 90, sessionName = "mangalist", sessionTtlMinutes = 20)
        assertTrue(config.flareSolverrEnabled.value)
        assertEquals("http://192.168.2.50:8191", config.flareSolverrUrl.value)
        assertEquals(90, config.flareSolverrTimeout.value)
        assertEquals("mangalist", config.flareSolverrSessionName.value)
        assertEquals(20, config.flareSolverrSessionTtl.value)
        assertFalse(config.flareSolverrAsResponseFallback.value)
    }

    @Test
    fun `no url leaves it off`() {
        val config = HostFlareSolverr()
        config.set(null)
        assertFalse(config.flareSolverrEnabled.value)
        config.set("   ")
        assertFalse(config.flareSolverrEnabled.value)
    }
}

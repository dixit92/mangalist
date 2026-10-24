package eu.kanade.tachiyomi


/**
 * Used by extensions.
 *
 * @since extension-lib 1.3
 */
object AppInfo {
    /**
     *
     * should be something like 74
     *
     * @since extension-lib 1.3
     */
    // MangaList extension host (prototype): a fixed app version (Suwayomi's BuildConfig is not part of the host)
    const val VERSION = "v2.4.2366"

    fun getVersionCode() =
        VERSION
            .replace("v", "")
            .split('.')
            .joinToString("")
            .toInt()

    /**
     * should be something like "0.13.1"
     *
     * @since extension-lib 1.3
     */
    fun getVersionName() = VERSION.substring(1)

    /**
     * A list of supported image MIME types by the reader.
     * e.g. ["image/jpeg", "image/png", ...]
     *
     * @since extension-lib 1.5
     */
    fun getSupportedImageMimeTypes(): List<String> =
        listOf("image/jpeg", "image/png", "image/gif", "image/webp", "image/avif", "image/heif", "image/jxl")
}

package mangalist.extensionhost

import com.android.apksig.ApkVerifier
import java.nio.file.Path
import java.security.MessageDigest
import java.security.cert.Certificate
import java.util.jar.JarFile

class SignatureException(
    message: String,
) : Exception(message)

/**
 * The signature check of an extension before it is installed: the SHA-256 of its signing certificate must be the
 * repository's `signingKey` (keiyoushi's `index.json`: `9add655a...`), as Mihon checks an extension against its
 * repository's key. keiyoushi's JVM jars are signed as jars (`META-INF/CERT.RSA`, the same certificate as the APKs);
 * its APKs carry APK signature scheme v2 / v3 signatures, checked with Android's apksig.
 */
object Signatures {
    fun sha256(cert: Certificate): String = MessageDigest.getInstance("SHA-256").digest(cert.encoded).joinToString("") { "%02x".format(it) }

    /**
     * The certificates (SHA-256, lowercase hex) every class and resource of the jar is signed with; empty when an
     * entry is unsigned or the signature does not verify (a tampered entry throws while it is read).
     */
    fun jarSigners(jar: Path): Set<String> =
        try {
            JarFile(jar.toFile(), true).use { jf ->
                var common: Set<String>? = null
                for (entry in jf.entries().asSequence()) {
                    if (entry.isDirectory) continue
                    val name = entry.name.uppercase()
                    if (name.startsWith("META-INF/")) continue
                    jf.getInputStream(entry).use { it.readAllBytes() } // the signature is checked as the entry is read
                    val signers = entry.codeSigners?.map { sha256(it.signerCertPath.certificates.first()) }?.toSet().orEmpty()
                    common = common?.intersect(signers) ?: signers
                    if (common.isEmpty()) return emptySet()
                }
                common.orEmpty()
            }
        } catch (_: SecurityException) {
            emptySet()
        }

    /** The certificates (SHA-256) an APK is signed with (v1 / v2 / v3); empty when it does not verify. */
    fun apkSigners(apk: Path): Set<String> {
        val result = ApkVerifier.Builder(apk.toFile()).build().verify()
        if (!result.isVerified) return emptySet()
        return result.signerCertificates.map { sha256(it) }.toSet()
    }

    /**
     * Check that [file] (a `.jar` or an `.apk`) is signed with [signingKey] (the repository's, SHA-256 hex, `:` and
     * case ignored); its fingerprint, or a [SignatureException].
     */
    fun check(
        file: Path,
        signingKey: String?,
    ): String {
        val expected = signingKey?.replace(":", "")?.trim()?.lowercase()?.takeIf { it.isNotEmpty() }
            ?: throw SignatureException("the repository gives no signing key: the extension cannot be checked, so it is not installed")
        val signers = if (file.toString().endsWith(".jar")) jarSigners(file) else apkSigners(file)
        if (signers.isEmpty()) throw SignatureException("${file.fileName} is not signed (or its signature does not verify): not installed")
        if (expected !in signers) {
            throw SignatureException(
                "${file.fileName} is signed by ${signers.joinToString()}, not by the repository's key $expected: not installed",
            )
        }
        return expected
    }
}

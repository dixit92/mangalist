package mangalist.extensionhost

import com.android.apksig.ApkSigner
import jdk.security.jarsigner.JarSigner
import java.nio.file.Files
import java.nio.file.Path
import java.security.KeyStore
import java.security.PrivateKey
import java.security.cert.CertificateFactory
import java.security.cert.X509Certificate
import java.util.zip.ZipEntry
import java.util.zip.ZipFile
import java.util.zip.ZipOutputStream
import kotlin.io.path.createTempDirectory
import kotlin.io.path.outputStream
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue

/** A signing key made with the JDK's keytool (once per test run). */
class TestKey(
    alias: String,
) {
    val key: PrivateKey
    val cert: X509Certificate

    init {
        val dir = createTempDirectory("key")
        val store = dir.resolve("k.p12")
        val keytool = Path.of(System.getProperty("java.home"), "bin", "keytool").toString()
        val p =
            ProcessBuilder(
                keytool, "-genkeypair", "-alias", alias, "-keyalg", "RSA", "-keysize", "2048", "-validity", "3650",
                "-dname", "CN=$alias", "-storetype", "PKCS12", "-keystore", store.toString(), "-storepass", "secret",
                "-keypass", "secret",
            ).redirectErrorStream(true).start()
        val out = p.inputStream.readAllBytes().decodeToString()
        check(p.waitFor() == 0) { "keytool failed: $out" }
        val ks = KeyStore.getInstance("PKCS12")
        Files.newInputStream(store).use { ks.load(it, "secret".toCharArray()) }
        key = ks.getKey(alias, "secret".toCharArray()) as PrivateKey
        cert = ks.getCertificate(alias) as X509Certificate
    }

    val fingerprint: String get() = Signatures.sha256(cert)

    /** Sign [jar] as a jar (as keiyoushi's JVM jars are); the signed copy. */
    fun signJar(jar: Path): Path {
        val out = jar.resolveSibling("signed-" + jar.fileName)
        val path = CertificateFactory.getInstance("X.509").generateCertPath(listOf(cert))
        val signer = JarSigner.Builder(key, path).build()
        ZipFile(jar.toFile()).use { zf -> out.outputStream().use { signer.sign(zf, it) } }
        return out
    }

    /** Sign [apk] with APK signature scheme v2 only (as keiyoushi's APKs). */
    fun signApk(apk: Path): Path {
        val out = apk.resolveSibling("signed-" + apk.fileName)
        val config = ApkSigner.SignerConfig.Builder("test", key, listOf(cert)).build()
        ApkSigner
            .Builder(listOf(config))
            .setInputApk(apk.toFile())
            .setOutputApk(out.toFile())
            .setMinSdkVersion(26)
            .setV1SigningEnabled(false)
            .setV2SigningEnabled(true)
            .setV3SigningEnabled(false)
            .build()
            .sign()
        return out
    }

    companion object {
        val A by lazy { TestKey("repo-a") }
        val B by lazy { TestKey("repo-b") }

        fun zip(
            file: Path,
            vararg entries: Pair<String, ByteArray>,
        ): Path {
            ZipOutputStream(file.outputStream()).use { z ->
                for ((name, bytes) in entries) {
                    z.putNextEntry(ZipEntry(name))
                    z.write(bytes)
                    z.closeEntry()
                }
            }
            return file
        }
    }
}

/** Unit: an extension is installed only when signed with its repository's key. */
class SignaturesTest {
    private val dir = createTempDirectory("sig")

    private fun jar() = TestKey.zip(dir.resolve("ext.jar"), "AndroidManifest.xml" to ByteArray(8), "a/B.class" to ByteArray(32) { it.toByte() })

    @Test
    fun `a jar signed with the repository's key passes and gives its fingerprint`() {
        val signed = TestKey.A.signJar(jar())
        assertEquals(setOf(TestKey.A.fingerprint), Signatures.jarSigners(signed))
        assertEquals(TestKey.A.fingerprint, Signatures.check(signed, TestKey.A.fingerprint.uppercase().chunked(2).joinToString(":")))
    }

    @Test
    fun `a jar signed with another key, unsigned, or tampered with is refused`() {
        val signed = TestKey.B.signJar(jar())
        val e = assertFailsWith<SignatureException> { Signatures.check(signed, TestKey.A.fingerprint) }
        assertTrue(TestKey.B.fingerprint in e.message!!, e.message)
        assertFailsWith<SignatureException> { Signatures.check(jar(), TestKey.A.fingerprint) }
        assertFailsWith<SignatureException> { Signatures.check(signed, null) }

        // change a class after signing: the entry's digest no longer matches
        val good = TestKey.A.signJar(jar())
        val bad = dir.resolve("bad.jar")
        ZipFile(good.toFile()).use { zf ->
            ZipOutputStream(bad.outputStream()).use { out ->
                for (entry in zf.entries()) {
                    out.putNextEntry(ZipEntry(entry.name))
                    val bytes = zf.getInputStream(entry).readAllBytes()
                    if (entry.name == "a/B.class") bytes[0] = 99
                    out.write(bytes)
                    out.closeEntry()
                }
            }
        }
        assertEquals(emptySet(), Signatures.jarSigners(bad))
        assertFailsWith<SignatureException> { Signatures.check(bad, TestKey.A.fingerprint) }
    }

    @Test
    fun `an APK with a v2 signature is checked with apksig`() {
        val apk = TestKey.zip(dir.resolve("ext.apk"), "AndroidManifest.xml" to ByteArray(8), "classes.dex" to ByteArray(64))
        val signed = TestKey.A.signApk(apk)
        assertEquals(setOf(TestKey.A.fingerprint), Signatures.apkSigners(signed))
        assertEquals(TestKey.A.fingerprint, Signatures.check(signed, TestKey.A.fingerprint))
        assertFailsWith<SignatureException> { Signatures.check(signed, TestKey.B.fingerprint) }
        assertFailsWith<SignatureException> { Signatures.check(apk, TestKey.A.fingerprint) }
    }
}

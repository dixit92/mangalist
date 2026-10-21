// MangaList extension host (prototype): the program MangaList starts. Our own code is in src/main/kotlin; the
// Tachiyomi source API and Suwayomi's extension loader are compiled in from vendor/suwayomi/tachiyomi (MPL-2.0).
plugins {
    alias(libs.plugins.kotlin.jvm)
    alias(libs.plugins.kotlin.serialization)
    application
}

dependencies {
    implementation(project(":android-compat"))
    implementation(project(":android-compat-config"))
    implementation(libs.logback)
    implementation(libs.okhttp.core)
    implementation(libs.okhttp.logging)
    implementation(libs.okhttp.dnsoverhttps)
    implementation(libs.okhttp.brotli)
    implementation(libs.okhttp.zstd)
    implementation(libs.okio)
    implementation(libs.injekt)
    implementation(libs.asm)
    implementation(libs.cache4k)
    implementation(libs.sort)
    implementation(libs.serialization.xml.core)
    implementation(libs.serialization.xml)
    testImplementation(libs.kotlin.test.junit5)
    testRuntimeOnly("org.junit.platform:junit-platform-launcher")
}

sourceSets {
    main {
        kotlin.srcDirs("src/main/kotlin", "../vendor/suwayomi/tachiyomi")
    }
}

application {
    mainClass.set("mangalist.extensionhost.MainKt")
    applicationName = "mangalist-extension-host"
}

tasks.test {
    useJUnitPlatform()
}

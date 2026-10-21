// MangaList extension host (prototype): simplified from Suwayomi-Server's AndroidCompat/Config/build.gradle.kts
plugins {
    alias(libs.plugins.kotlin.jvm)
    alias(libs.plugins.kotlin.serialization)
}

dependencies {
    implementation(libs.kotlin.stdlib.jdk8)
    implementation(libs.kotlin.reflect)
    implementation(libs.coroutines.core)
    implementation(libs.serialization.json)
    implementation(libs.koin.core)
    implementation(libs.slf4japi)
    implementation(libs.kotlinlogging)
    implementation(libs.logback)
    implementation(libs.appdirs)
    implementation(libs.config)
    implementation(libs.config4k)
}

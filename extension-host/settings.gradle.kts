// MangaList extension host (prototype): runs Mihon / Tachiyomi extensions on a plain JVM, without Suwayomi.
// The Android compatibility layer and the Tachiyomi source API are vendored from Suwayomi-Server (MPL-2.0); see
// vendor/suwayomi/README.md.
pluginManagement {
    repositories {
        gradlePluginPortal()
        mavenCentral()
    }
}

dependencyResolutionManagement {
    repositories {
        mavenCentral()
        google()
        maven("https://github.com/Suwayomi/Suwayomi-Server/raw/android-jar/")
        maven("https://jitpack.io")
        // JetBrains' JCEF (the WebView), as Suwayomi
        maven("https://packages.jetbrains.team/maven/p/ij/intellij-dependencies")
    }
}

rootProject.name = "mangalist-extension-host"

include(":android-compat-config")
include(":android-compat")
include(":host")
project(":android-compat-config").projectDir = file("vendor/suwayomi/AndroidCompat/Config")
project(":android-compat").projectDir = file("vendor/suwayomi/AndroidCompat")

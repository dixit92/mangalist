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
        // Suwayomi's Android stubs, pinned to a commit: the branch republishes the same version 1.0.0 whenever its
        // AndroidCompat takes over a class (2026-11-01: android.net.Network* left the jar), and the vendored
        // AndroidCompat here is v2.4.2366's, which still needs those stubs.
        maven("https://github.com/Suwayomi/Suwayomi-Server/raw/3983289d9481a8ba99c0b16350b27f41364906a2/")
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

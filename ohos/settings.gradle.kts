pluginManagement {
    repositories {
        maven("https://maven.eazytec-cloud.com/nexus/repository/maven-develop/")
        maven("https://maven.eazytec-cloud.com/nexus/repository/maven-public/")
        gradlePluginPortal()
        mavenCentral()
    }
    plugins {
        id("org.jetbrains.kotlin.multiplatform") version "2.4.20-0.1.0-08"
        id("org.jetbrains.kotlin.plugin.compose") version "2.4.20-0.1.0-08"
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        exclusiveContent {
            forRepository { maven("https://maven.eazytec-cloud.com/nexus/repository/maven-inner/") }
            filter { includeGroup("co.touchlab") }
        }
        google {
            // 仅放行已核 common metadata 别名；Native 仍由 CPF pins 固定。
            content {
                includeModule("androidx.annotation", "annotation")
                includeModule("androidx.arch.core", "core-common")
                includeModule("androidx.collection", "collection")
                includeModule("androidx.compose.runtime", "runtime")
                includeModule("androidx.compose.runtime", "runtime-annotation")
                includeModule("androidx.compose.runtime", "runtime-saveable")
                includeModule("androidx.lifecycle", "lifecycle-common")
                includeModule("androidx.lifecycle", "lifecycle-runtime")
                includeModule("androidx.lifecycle", "lifecycle-runtime-compose")
                includeModule("androidx.lifecycle", "lifecycle-viewmodel")
                includeModule("androidx.lifecycle", "lifecycle-viewmodel-compose")
                includeModule("androidx.lifecycle", "lifecycle-viewmodel-savedstate")
                includeModule("androidx.savedstate", "savedstate")
                includeModule("androidx.savedstate", "savedstate-compose")
            }
        }
        // Native 闭包在 dependency-pins.json 逐坐标固定；没有 Google AndroidX Native 回退。
        maven("https://maven.eazytec-cloud.com/nexus/repository/maven-develop/")
        maven("https://maven.eazytec-cloud.com/nexus/repository/maven-public/")
        mavenCentral()
    }
}

rootProject.name = "koin-ga-port"
include(":koin-core")
project(":koin-core").projectDir = file("core/koin-core")
include(":koin-core-viewmodel")
project(":koin-core-viewmodel").projectDir = file("core/koin-core-viewmodel")
include(":koin-compose")
project(":koin-compose").projectDir = file("compose/koin-compose")
include(":koin-compose-viewmodel")
project(":koin-compose-viewmodel").projectDir = file("compose/koin-compose-viewmodel")

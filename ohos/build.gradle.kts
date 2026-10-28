import groovy.json.JsonOutput
import groovy.json.JsonSlurper
import java.security.MessageDigest
import org.gradle.api.publish.PublishingExtension
import org.gradle.api.publish.maven.MavenPublication
import org.gradle.jvm.tasks.Jar
import org.jetbrains.kotlin.gradle.dsl.KotlinMultiplatformExtension
import org.jetbrains.kotlin.gradle.tasks.KotlinNativeCompile
import org.gradle.api.attributes.Usage
import org.gradle.api.attributes.Category
import org.gradle.api.artifacts.component.ModuleComponentIdentifier
import org.gradle.api.artifacts.result.ResolvedDependencyResult

plugins {
    kotlin("multiplatform") apply false
    kotlin("plugin.compose") apply false
}

val portWork = file(providers.gradleProperty("portWork").get()).canonicalFile
val privateVersion = providers.gradleProperty("portVersion").get()
val portRepository = file(providers.gradleProperty("portRepository").get()).canonicalFile
val toolRoot = file(providers.gradleProperty("portTooling").get()).resolve("toolchains/kotlin-native/2.4.20-0.1.0-08")
fun objectMap(value: Any): Map<String, Any> = (value as Map<*, *>).entries.associate { it.key.toString() to requireNotNull(it.value) }
fun jsonMap(path: String) = objectMap(JsonSlurper().parse(file(path)))
val portConfig = jsonMap("port-config.json")
val moduleConfig = objectMap(portConfig.getValue("modules"))
val moduleNames = moduleConfig.keys
val projectPins = moduleNames.flatMap { module ->
    listOf(module, "$module-ohosx64", "$module-ohosarm64").map { "io.github.qsxs.cpf.koin:$it" to privateVersion }
}.toMap()
val pins = jsonMap("dependency-pins.json").filterKeys { !it.startsWith("io.insert-koin:") } + projectPins
val metadataPins = jsonMap("metadata-pins.json")
val metadataTools = objectMap(JsonSlurper().parse(toolRoot.resolve("metadata-toolchain.json")))
val launcherPins = objectMap(metadataTools.getValue("launcherPins"))
val launcherArtifacts = objectMap(metadataTools.getValue("launcherArtifacts"))
val commonizerTools = objectMap(JsonSlurper().parse(toolRoot.resolve("commonizer-toolchain.json")))
val nativeTools = objectMap(JsonSlurper().parse(toolRoot.resolve("native-compiler-toolchain.json")))
val commonizerPins = launcherPins + objectMap(commonizerTools.getValue("pins"))
val commonizerArtifacts = launcherArtifacts + objectMap(commonizerTools.getValue("artifacts"))
val nativePins = objectMap(nativeTools.getValue("pins"))
val nativeArtifacts = objectMap(nativeTools.getValue("artifacts"))
fun pinned(key: String) = "$key:${pins.getValue(key)}"
fun digest(file: File) = MessageDigest.getInstance("SHA-256").digest(file.readBytes()).joinToString("") { "%02x".format(it) }
fun filesOf(file: File) = if (file.isDirectory) file.walkTopDown().filter(File::isFile).toList() else listOf(file)
fun fileRecords(files: Iterable<File>) = files.filter(File::isFile).distinct().sortedBy { it.path }.map {
    mapOf("path" to it.absolutePath, "sha256" to digest(it), "size" to it.length())
}
fun upstreamSources(module: String, sourceSet: String): File {
    val modulePath = objectMap(moduleConfig.getValue(module)).getValue("projectDirectory").toString()
    return rootDir.parentFile.resolve("projects/$modulePath/src/$sourceSet/kotlin").canonicalFile
}
fun metadataSources(child: Project) = listOf("commonMain", "nativeMain", "ohosMain").filter {
    upstreamSources(child.name, it).walkTopDown().any { file -> file.isFile && file.extension == "kt" }
}
fun metadataCompile(source: String) = if (source == "commonMain") "compile${source.replaceFirstChar { it.uppercase() }}WithMetadataCli"
    else "compile${source.replaceFirstChar { it.uppercase() }}KotlinMetadata"
fun effectiveDependencies(task: Task): List<String> {
    val visited = mutableSetOf<Task>()
    fun visit(current: Task) { gradle.taskGraph.getDependencies(current).forEach { if (visited.add(it)) visit(it) } }
    visit(task)
    return visited.map { it.path }.sorted()
}
fun parentCompilationOutputs(task: Task): List<Map<String, Any>> {
    if (task !is KotlinNativeCompile || !task.name.endsWith("KotlinMetadata")) return emptyList()
    val child = task.project
    val source = task.name.removePrefix("compile").removeSuffix("KotlinMetadata").replaceFirstChar { it.lowercase() }
    val kmp = child.extensions.getByType<KotlinMultiplatformExtension>()
    return kmp.sourceSets.getByName(source).dependsOn.map { parent ->
        val output = kmp.targets.getByName("metadata").compilations.getByName(parent.name).output.classesDirs
        mapOf("sourceSet" to parent.name, "roots" to output.files.map { it.absolutePath },
            "files" to fileRecords(output.files.flatMap(::filesOf)),
            "builtBy" to output.buildDependencies.getDependencies(task).map { it.path })
    }
}
fun producerRecord(task: Task): Map<String, Any?> {
    val state = task.state
    check(gradle.taskGraph.hasTask(task) && state.executed && task.enabled && state.failure == null && !state.noSource &&
        state.skipMessage in listOf(null, "UP-TO-DATE", "FROM-CACHE")) { "Invalid producer state: ${task.path}" }
    val inputs = task.inputs.files.files.flatMap(::filesOf)
    val roots = task.outputs.files.files.filter { it.exists() }
    val outputs = fileRecords(roots.flatMap(::filesOf))
    check(outputs.isNotEmpty()) { "Producer has no output: ${task.path}" }
    return mapOf("task" to task.path, "executed" to state.executed, "upToDate" to state.upToDate,
        "state" to mapOf("inTaskGraph" to true, "executed" to state.executed, "enabled" to task.enabled,
            "noSource" to state.noSource, "failure" to null, "skipMessage" to state.skipMessage),
        "inputs" to fileRecords(inputs), "sourceInputs" to fileRecords(inputs.filter { it.extension == "kt" }),
        "outputs" to outputs, "outputRoots" to roots.map { it.absolutePath },
        "dependencies" to effectiveDependencies(task),
        "compilerPlugins" to if (task is KotlinNativeCompile) fileRecords(task.compilerPluginClasspath?.files.orEmpty()) else emptyList<Map<String, Any>>(),
        "compilerPluginOptions" to if (task is KotlinNativeCompile) task.compilerPluginCommandLine else emptyList<String>(),
        "parentCompilationOutputs" to parentCompilationOutputs(task),
        "refinesPaths" to if (task.name.endsWith("WithMetadataCli")) emptyList<String>() else null,
        "parentEvidenceKind" to "PUBLIC_COMPILATION_OUTPUT_BUILTBY_AND_ACTUAL_INPUTS_NOT_NATIVE_ARGV")
}

// 每阶段保留实际任务及输出证据；依赖已执行或缓存复用都必须有对应产物。
fun registerProducerRecord(taskName: String, target: String?) {
    tasks.register(taskName) {
        val producers = subprojects.flatMap { child ->
            if (target == null) listOf("${child.path}:allMetadataJar") + metadataSources(child).flatMap {
                listOf("${child.path}:transform${it.replaceFirstChar { ch -> ch.uppercase() }}DependenciesMetadata",
                    "${child.path}:${metadataCompile(it)}")
            } else buildList {
                add("${child.path}:compileKotlin${target.replaceFirstChar { it.uppercase() }}")
            }
        }
        dependsOn(producers)
        outputs.upToDateWhen { false }
        doLast {
            val dependencies = subprojects.flatMap { child ->
                val configuration = child.configurations.getByName(if (target == null)
                    "commonMainResolvableDependenciesMetadata" else "${target}CompileKlibraries")
                configuration.incoming.artifacts.artifacts.map { artifact ->
                    val id = artifact.id.componentIdentifier
                    if (id is ModuleComponentIdentifier) {
                        val key = "${id.group}:${id.module}"
                        val expected = if (target == null) metadataPins[key] ?: pins[key] else pins[key]
                        check(expected != null && id.version == expected.toString()) { "Unpinned resolved dependency: $id" }
                    }
                    mapOf("module" to child.name, "configuration" to configuration.name, "component" to id.displayName,
                        "attributes" to artifact.variant.attributes.keySet().associate {
                            it.name to artifact.variant.attributes.getAttribute(it).toString()
                        }, "files" to fileRecords(filesOf(artifact.file)))
                }
            }
            val records = producers.map { producerRecord(project.tasks.getByPath(it)) }
            val stage = providers.gradleProperty("portStage").get()
            val hostTools = subprojects.map { child ->
                val task = child.tasks.getByName("verifyKoinHost")
                val state = task.state
                check(gradle.taskGraph.hasTask(task) && state.executed && task.enabled && !state.noSource &&
                    state.failure == null && state.skipMessage == null) { "Koin host verification did not execute: ${task.path}" }
                val report = portWork.resolve("evidence/host-${child.name}-$stage.json")
                mapOf("module" to child.name, "task" to task.path,
                    "state" to mapOf("inTaskGraph" to true, "executed" to state.executed, "enabled" to task.enabled,
                        "noSource" to state.noSource, "failure" to null, "skipMessage" to state.skipMessage),
                    "report" to fileRecords(listOf(report)).single())
            }
            val destination = portWork.resolve("evidence/$taskName.json")
            destination.parentFile.mkdirs()
            destination.writeText(JsonOutput.toJson(mapOf("stage" to taskName, "target" to target,
                "sourceManifestSha256" to digest(portWork.resolve("source-manifest.json")), "producers" to records,
                "hostTools" to hostTools, "dependencies" to dependencies)))
        }
    }
}
registerProducerRecord("recordMetadataProducers", null)
registerProducerRecord("recordX64Producers", "ohosX64")
registerProducerRecord("recordArm64Producers", "ohosArm64")

tasks.register("resolveKoin") {
    doLast {
        check(gradle.gradleVersion == "9.5.0" && System.getProperty("java.specification.version") == "17")
        val configurations = subprojects.flatMap { child ->
            listOf("commonMainResolvableDependenciesMetadata", "ohosX64CompileKlibraries", "ohosArm64CompileKlibraries").map { name ->
                val configuration = child.configurations.getByName(name)
                val metadata = name == "commonMainResolvableDependenciesMetadata"
                val edges = configuration.incoming.resolutionResult.allDependencies.map { edge ->
                    check(edge is ResolvedDependencyResult) { "Unresolved Koin dependency: $edge" }
                    val id = edge.selected.id
                    if (id is ModuleComponentIdentifier) {
                        val ga = "${id.group}:${id.module}"
                        check((if (metadata) metadataPins[ga] ?: pins[ga] else pins[ga]) == id.version) { "Unpinned Koin component: $id" }
                    }
                    mapOf("from" to edge.from.id.displayName, "requested" to edge.requested.displayName, "selected" to id.displayName)
                }
                mapOf("module" to child.name, "configuration" to name, "edges" to edges)
            }
        }
        val target = portWork.resolve("evidence/resolve.json")
        target.parentFile.mkdirs()
        target.writeText(JsonOutput.toJson(mapOf("fingerprint" to providers.gradleProperty("portFingerprint").get(), "configurations" to configurations)))
    }
}

tasks.register("publishAllPublicationsToPortRepository") {
    dependsOn(subprojects.map { "${it.path}:publishAllPublicationsToPortRepository" })
}
layout.buildDirectory.set(portWork.resolve("build/root"))

subprojects {
    val moduleName = name
    group = "io.github.qsxs.cpf.koin"
    version = privateVersion
    layout.buildDirectory.set(portWork.resolve("build/$moduleName"))
    plugins.withId("org.jetbrains.kotlin.multiplatform") {
        extensions.configure<KotlinMultiplatformExtension> {
            ohosX64()
            ohosArm64()
            sourceSets {
                val commonMain by getting {
                    kotlin.setSrcDirs(listOf(upstreamSources(moduleName, "commonMain")))
                    dependencies {
                        when (moduleName) {
                            "koin-core" -> {
                                implementation(pinned("co.touchlab:stately-concurrency"))
                                implementation(pinned("co.touchlab:stately-concurrent-collections"))
                            }
                            "koin-core-viewmodel" -> {
                                api(project(":koin-core"))
                                api(pinned("org.jetbrains.androidx.lifecycle:lifecycle-viewmodel"))
                                api(pinned("org.jetbrains.androidx.lifecycle:lifecycle-viewmodel-savedstate"))
                            }
                            "koin-compose" -> {
                                api(project(":koin-core"))
                                api(pinned("org.jetbrains.compose.runtime:runtime"))
                                api(pinned("org.jetbrains.compose.foundation:foundation"))
                            }
                            "koin-compose-viewmodel" -> {
                                api(project(":koin-compose"))
                                api(project(":koin-core-viewmodel"))
                                api(pinned("org.jetbrains.androidx.lifecycle:lifecycle-viewmodel-compose"))
                            }
                        }
                    }
                }
                val nativeMain = create("nativeMain") {
                    dependsOn(commonMain)
                    kotlin.setSrcDirs(listOf(upstreamSources(moduleName, "nativeMain")))
                }
                val leafParent = create("ohosMain") {
                    dependsOn(nativeMain)
                    kotlin.setSrcDirs(emptyList<String>())
                }
                listOf("ohosX64Main", "ohosArm64Main").forEach { target ->
                    getByName(target).dependsOn(leafParent)
                    getByName(target).kotlin.setSrcDirs(emptyList<String>())
                }
            }
        }
    }
    configurations.configureEach {
        if (name == nativeTools["configuration"] || name == commonizerTools["configuration"]) {
            val hostPins = if (name == nativeTools["configuration"]) nativePins else commonizerPins
            resolutionStrategy.eachDependency {
                val ga = "${requested.group}:${requested.name}"
                val version = hostPins[ga]?.toString() ?: error("Unpinned Koin compiler host: $ga")
                if (requested.version != version) useVersion(version)
            }
        }
        val metadata = name.endsWith("ResolvableDependenciesMetadata") || name == "allSourceSetsCompileDependenciesMetadata"
        val native = name in setOf("ohosX64CompileKlibraries", "ohosArm64CompileKlibraries",
            "ohosX64CompilationDependenciesMetadata", "ohosArm64CompilationDependenciesMetadata")
        if ((metadata || native) && !name.contains("Test", ignoreCase = true)) resolutionStrategy.eachDependency {
            val key = "${requested.group}:${requested.name}"
            val version = (if (metadata) metadataPins[key] ?: pins[key] else pins[key])
                ?: error("Unreviewed Koin dependency: $name $key:${requested.version}")
            if (requested.version != version.toString()) useVersion(version.toString())
        }
    }
    fun checkedHost(name: String, fixed: Map<String, Any>, expected: Map<String, Any>): Map<String, Any> {
        val configuration = configurations.getByName(name)
        val artifacts = configuration.resolvedConfiguration.resolvedArtifacts.map { artifact ->
            val id = artifact.moduleVersion.id
            val ga = "${id.group}:${id.name}"
            val bytes = objectMap(expected.getValue(ga))
            check(fixed[ga] == id.version && digest(artifact.file) == bytes["sha256"] &&
                artifact.file.length() == (bytes.getValue("size") as Number).toLong()) { "Koin host tool differs: $id" }
            mapOf("gav" to id.toString(), "files" to fileRecords(listOf(artifact.file)))
        }
        check(artifacts.size == fixed.size && artifacts.map { it.getValue("gav") }.toSet() ==
            fixed.map { "${it.key}:${it.value}" }.toSet()) { "Koin host tool set differs: $name" }
        val edges = configuration.incoming.resolutionResult.allDependencies.map { edge ->
            check(edge is ResolvedDependencyResult) { "Unresolved Koin host tool: $edge" }
            mapOf("from" to edge.from.id.displayName, "requested" to edge.requested.displayName, "selected" to edge.selected.id.displayName)
        }
        return mapOf("configuration" to name, "edges" to edges, "artifacts" to artifacts.sortedBy { it.getValue("gav").toString() })
    }
    val verifyHost = tasks.register("verifyKoinHost") {
        outputs.upToDateWhen { false }
        doLast {
            val stage = providers.gradleProperty("portStage").get()
            val report = portWork.resolve("evidence/host-$moduleName-$stage.json")
            report.parentFile.mkdirs()
            report.writeText(JsonOutput.toJson(mapOf("fingerprint" to providers.gradleProperty("portFingerprint").get(),
                "stage" to stage, "module" to moduleName, "task" to path,
                "native" to checkedHost(nativeTools.getValue("configuration").toString(), nativePins, nativeArtifacts),
                "commonizer" to checkedHost(commonizerTools.getValue("configuration").toString(), commonizerPins, commonizerArtifacts))))
        }
    }
    tasks.withType<KotlinNativeCompile>().configureEach {
        if (name in listOf("compileCommonMainKotlinMetadata", "compileNativeMainKotlinMetadata",
                "compileKotlinOhosX64", "compileKotlinOhosArm64")) dependsOn(verifyHost)
    }
    plugins.withId("maven-publish") {
        extensions.configure<PublishingExtension> {
            repositories { maven { name = "Port"; url = uri(portRepository) } }
            publications.withType<MavenPublication>().configureEach {
                artifact(portWork.resolve("provenance.json")) { classifier = "provenance"; extension = "json" }
                pom {
                    name.set(moduleName)
                    description.set("Koin 4.2.2 exact GA common/Native sources; private OHOS publication")
                    licenses { license { name.set("Apache License 2.0"); url.set("https://www.apache.org/licenses/LICENSE-2.0") } }
                }
            }
        }
    }
    tasks.withType<Jar>().configureEach {
        from(portWork.resolve("legal/$moduleName"))
        from(portWork.resolve("source-manifest.json")) { into("META-INF/upstream") }
    }
}

// KGP完成元数据源集/任务注册，并实现旧任务的懒配置后，再替换公开输出集合。
gradle.projectsEvaluated {
    subprojects.forEach { child ->
        val sourceSet = "commonMain"
        val suffix = sourceSet.replaceFirstChar { it.uppercase() }
        val sourceRoot = upstreamSources(child.name, sourceSet)
        if (!sourceRoot.walkTopDown().any { it.isFile && it.extension == "kt" }) return@forEach
        val kmp = child.extensions.getByType<KotlinMultiplatformExtension>()
        val compilation = kmp.targets.getByName("metadata").compilations.getByName(sourceSet)
        val original = child.tasks.named<KotlinNativeCompile>("compile${suffix}KotlinMetadata").get()
        child.tasks.getByName("allMetadataJar")
        // 在CLI任务的惰性依赖解析上下文求值，不能在projectsEvaluated跨项目主动解析。
        val originalDependencies = child.provider { original.taskDependencies.getDependencies(original).toList() }
        val launcher = child.configurations.create("${sourceSet}MetadataCliLauncher") {
            isCanBeConsumed = false
            isCanBeResolved = true
            isVisible = false
            attributes {
                attribute(Usage.USAGE_ATTRIBUTE, child.objects.named(Usage.JAVA_RUNTIME))
                attribute(Category.CATEGORY_ATTRIBUTE, child.objects.named(Category.LIBRARY))
            }
            resolutionStrategy.eachDependency {
                val ga = "${requested.group}:${requested.name}"
                val fixed = launcherPins[ga] ?: error("Unreviewed metadata launcher dependency: $ga")
                if (requested.version != fixed.toString()) useVersion(fixed.toString())
            }
        }
        child.dependencies.add(launcher.name, "org.jetbrains.kotlin:kotlin-compiler-embeddable:${metadataTools.getValue("compilerVersion")}")
        val output = child.layout.buildDirectory.dir("metadata-cli/$sourceSet/klib")
        val argumentsFile = child.layout.buildDirectory.file("metadata-cli/$sourceSet/arguments.txt")
        val evidenceFile = child.layout.buildDirectory.file("metadata-cli/$sourceSet/producer.json")
        // 从本模块原任务的公开输入模型读取，不使用另一个模块或历史运行的library列表。
        val libraries = child.files(child.provider { original.libraries })
        val plugins = child.files(child.provider { original.compilerPluginClasspath })
        val sourceFiles = child.fileTree(upstreamSources(child.name, sourceSet)) { include("**/*.kt") }
        val compilerArguments = child.provider {
            val options = original.compilerOptions
            buildList {
                // refined metadata 的解析顺序有语义，保留原 KGP FileCollection 次序。
                addAll(listOf("-classpath", libraries.files.joinToString(File.pathSeparator) { it.invariantSeparatorsPath },
                    "-d", output.get().asFile.invariantSeparatorsPath, "-module-name", "${child.group}:${child.name}_$sourceSet",
                    "-Xtarget-platform=Native", "-Xmulti-platform"))
                options.languageVersion.orNull?.let { addAll(listOf("-language-version", it.version)) }
                options.apiVersion.orNull?.let { addAll(listOf("-api-version", it.version)) }
                options.optIn.get().sorted().forEach { add("-opt-in=$it") }
                if (!plugins.isEmpty) add("-Xplugin=" + plugins.files.sortedBy { it.path }.joinToString(",") { it.invariantSeparatorsPath })
                original.compilerPluginCommandLine.forEach { addAll(listOf("-P", it)) }
                addAll(sourceFiles.files.sortedBy { it.path }.map { it.invariantSeparatorsPath })
            }
        }
        val cli = child.tasks.register<JavaExec>("compile${suffix}WithMetadataCli") {
            group = "build"
            description = "用同版本公共metadata编译器编译未修改的commonMain；无refines父模块。"
            dependsOn(originalDependencies)
            dependsOn("transform${suffix}DependenciesMetadata")
            dependsOn("verifyKoinHost")
            classpath = launcher
            mainClass.set(metadataTools.getValue("mainClass") as String)
            setExecutable(File(System.getProperty("java.home"), "bin/" + if (System.getProperty("os.name").startsWith("Windows")) "java.exe" else "java").absolutePath)
            inputs.files(sourceFiles, libraries, plugins)
            inputs.file(portWork.resolve("source-manifest.json"))
            inputs.file(toolRoot.resolve("metadata-toolchain.json"))
            inputs.property("compilerArguments", compilerArguments)
            outputs.dir(output)
            outputs.file(argumentsFile)
            outputs.file(evidenceFile)
            args("@" + argumentsFile.get().asFile.absolutePath)
            doFirst {
                val files = compilation.output.classesDirs
                check(files.files == setOf(output.get().asFile) &&
                    files.buildDependencies.getDependencies(this).map { it.path }.toSet() == setOf(path) && !original.enabled) {
                    "KGP $sourceSet metadata output/builtBy was not replaced exclusively: ${child.path}"
                }
                val actualArguments = compilerArguments.get()
                val argFile = argumentsFile.get().asFile
                argFile.parentFile.mkdirs()
                argFile.writeText(actualArguments.joinToString("\n", postfix = "\n") {
                    "\"" + it.replace("\\", "\\\\").replace("\"", "\\\"") + "\""
                })
            }
            doLast {
                check(executionResult.get().exitValue == 0)
                val artifactRecords = launcher.resolvedConfiguration.resolvedArtifacts.map {
                    mapOf("gav" to it.moduleVersion.id.toString(), "path" to it.file.absolutePath,
                        "sha256" to digest(it.file), "size" to it.file.length())
                }
                check(artifactRecords.map { it.getValue("gav") }.toSet() == launcherPins.map { "${it.key}:${it.value}" }.toSet())
                artifactRecords.forEach { row ->
                    val ga = row.getValue("gav").toString().substringBeforeLast(":")
                    val expected = objectMap(launcherArtifacts.getValue(ga))
                    check(row["sha256"] == expected["sha256"] && row["size"] == (expected.getValue("size") as Number).toLong())
                }
                val pluginFiles = plugins.files.sortedBy { it.path }
                val sourceManifest = portWork.resolve("source-manifest.json")
                val data = mapOf("kind" to "COMMON_METADATA_CLI_COMPILE",
                    "sourceSet" to sourceSet, "refinesPaths" to emptyList<String>(),
                    "publicRefinesPaths" to emptyList<String>(), "module" to child.name,
                    "task" to path, "exitCode" to executionResult.get().exitValue,
                    "mainClass" to mainClass.get(), "compilerVersion" to metadataTools.getValue("compilerVersion"),
                    "sourceManifestSha256" to digest(sourceManifest), "arguments" to compilerArguments.get(),
                    "argumentsFile" to argumentsFile.get().asFile.absolutePath, "argumentsSha256" to digest(argumentsFile.get().asFile),
                    "launcher" to artifactRecords, "plugins" to fileRecords(pluginFiles),
                    "languageOptions" to mapOf("languageVersion" to original.compilerOptions.languageVersion.orNull?.version,
                        "apiVersion" to original.compilerOptions.apiVersion.orNull?.version, "optIns" to original.compilerOptions.optIn.get().sorted()),
                    "pluginOptions" to original.compilerPluginCommandLine,
                    "sources" to fileRecords(sourceFiles.files), "libraries" to libraries.files.map {
                        mapOf("path" to it.absolutePath, "files" to fileRecords(filesOf(it)))
                    }, "output" to output.get().asFile.absolutePath,
                    "outputs" to fileRecords(filesOf(output.get().asFile)),
                    "retiredTask" to mapOf("path" to original.path, "enabled" to original.enabled))
                evidenceFile.get().asFile.writeText(JsonOutput.prettyPrint(JsonOutput.toJson(data)))
            }
        }
        original.enabled = false
        compilation.output.classesDirs.setFrom(output)
        compilation.output.classesDirs.setBuiltBy(listOf(cli))
        // 图就绪后再核对，防止后续懒配置把旧输出/旧builtBy追加回来。
        gradle.taskGraph.whenReady {
            check(compilation.output.classesDirs.files == setOf(output.get().asFile)) { "$sourceSet metadata output changed after binding" }
            check(compilation.output.classesDirs.buildDependencies.getDependencies(cli.get()).map { it.path }.toSet() == setOf(cli.get().path)) {
                "$sourceSet metadata builtBy contains retired producer"
            }
        }
    }
}

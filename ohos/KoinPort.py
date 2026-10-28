"""Koin 4.2.2 四模块独立 OHOS producer；不部署、不读取凭据。"""
from __future__ import annotations
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tarfile
from urllib.request import ProxyHandler, build_opener
from urllib.parse import urlparse
import zipfile
import xml.etree.ElementTree as ET

sys.dont_write_bytecode = True
PORT = Path(__file__).resolve().parent
HERE = PORT
ROOT = PORT.parent
TOOL_REVISION = "d0cff88fd1ea2e4f08bb4409f1c802cca0c7f285"
TOOL_DIR = "toolchains/kotlin-native/2.4.20-0.1.0-08"
GROUP = "io.github.qsxs.cpf.koin"
MODULES = ("koin-core", "koin-core-viewmodel", "koin-compose", "koin-compose-viewmodel")
TASKS = {"Tasks": ["tasks", "--all"], "Resolve": ["resolveKoin"], "Metadata": ["recordMetadataProducers"],
         "NativeX64": ["recordX64Producers"], "NativeArm64": ["recordArm64Producers"],
         "Publish": ["publishAllPublicationsToPortRepository"]}
REQUIRED = {"resolveKoin", "recordMetadataProducers", "recordX64Producers", "recordArm64Producers", "publishAllPublicationsToPortRepository"}
ARGS = None
TOOLS = None


def io_path(path):
    """仅在 Windows 文件系统边界使用扩展路径；身份与范围仍用逻辑路径。"""
    if os.name != "nt":
        return Path(path)
    absolute = os.path.abspath(path)
    if absolute.startswith("\\\\?\\"):
        return Path(absolute)
    if absolute.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + absolute[2:])
    return Path("\\\\?\\" + absolute)


def logical_path(path):
    text = str(path)
    if os.name == "nt" and text.startswith("\\\\?\\UNC\\"):
        return Path("\\\\" + text[8:])
    if os.name == "nt" and text.startswith("\\\\?\\"):
        return Path(text[4:])
    return Path(path)


def logical_paths(paths):
    return (logical_path(path) for path in paths)


def require(value, message):
    if not value:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def load(path):
    return json.loads(io_path(path).read_text(encoding="utf-8"))


def record(path):
    path = Path(path)
    data = io_path(path).read_bytes()
    return {"path": str(path.resolve()), "size": len(data), "sha256": digest(data)}


def config():
    value = load(PORT / "port-config.json")
    if ARGS is not None:
        value["version"] = "4.2.2-cpf11-preview-" + ARGS.source_sha[:12] + "-" + ARGS.run_id + "-" + ARGS.run_attempt
    return value


def safe(path):
    path = Path(path).resolve()
    require(ARGS is not None, "Explicit output directories are required")
    roots = (ARGS.work.resolve(), ARGS.repository.resolve())
    require(any(path.is_relative_to(root) for root in roots), f"Output outside isolated work/staging: {path}")
    return path


def write(path, data):
    path = safe(path)
    io_path(path.parent).mkdir(parents=True, exist_ok=True)
    if io_path(path).exists():
        require(io_path(path).read_bytes() == data, f"Immutable preparation file differs: {path}")
    else:
        with io_path(path).open("xb") as target:
            target.write(data)


def checked_tools(work, explicit):
    lock = load(HERE / "tooling-lock.json")
    if lock["revision"] != TOOL_REVISION or lock["repository"] != "https://github.com/qsxs/cpf-port-tooling":
        raise RuntimeError("Immutable shared tooling revision differs")
    if lock["archiveUrl"] != lock["repository"] + "/archive/" + TOOL_REVISION + ".tar.gz":
        raise RuntimeError("Shared tooling archive URL differs")
    if len(lock["files"]) != 20 or len({row["path"] for row in lock["files"]}) != 20:
        raise RuntimeError("Shared tooling file lock differs")
    directory = Path(explicit).resolve() if explicit else (work / "tooling" / TOOL_REVISION).resolve()
    for row in lock["files"]:
        relative = PurePosixPath(row["path"])
        if relative.is_absolute() or ".." in relative.parts or "\\" in row["path"] or not row["path"].startswith(TOOL_DIR + "/"):
            raise RuntimeError("Unsafe shared tooling file path")
    if not explicit and not all((directory / row["path"]).is_file() for row in lock["files"]):
        # 仅读取锁定文件；归档压缩容器本身不作为内容身份。
        with build_opener(ProxyHandler({})).open(lock["archiveUrl"], timeout=60) as response:
            data = response.read(8 * 1024 * 1024 + 1)
        if len(data) > 8 * 1024 * 1024:
            raise RuntimeError("Shared tooling archive is too large")
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            members = archive.getmembers()
            for row in lock["files"]:
                matches = [member for member in members if "/" in member.name and member.name.split("/", 1)[1] == row["path"]]
                if len(matches) != 1 or not matches[0].isfile():
                    raise RuntimeError("Shared tooling archive member differs: " + row["path"])
                stream = archive.extractfile(matches[0])
                if stream is None:
                    raise RuntimeError("Shared tooling archive member is unreadable")
                content = stream.read(row["size"] + 1)
                if len(content) != row["size"] or digest(content) != row["sha256"]:
                    raise RuntimeError("Shared tooling archive bytes differ: " + row["path"])
                target = directory / row["path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    if target.read_bytes() != content:
                        raise RuntimeError("Existing shared tooling bytes differ")
                else:
                    with target.open("xb") as out:
                        out.write(content)
    for row in lock["files"]:
        actual = record(directory / row["path"])
        if (actual["size"], actual["sha256"]) != (row["size"], row["sha256"]):
            raise RuntimeError("Shared tooling identity differs: " + row["path"])
    return directory


def module_source_root(module):
    item = config()["modules"][module]
    return (ROOT / "projects" / item["projectDirectory"] / "src").resolve()


def source_from_logical(name):
    parts = PurePosixPath(name).parts
    require(len(parts) >= 4 and parts[0] == "sources" and parts[1] in MODULES and parts[2] in ("commonMain", "nativeMain"),
            "Unsafe Koin source member")
    path = module_source_root(parts[1]) / parts[2] / "kotlin" / Path(*parts[3:])
    require(path.resolve().is_relative_to(module_source_root(parts[1])), "Koin source member escaped checkout")
    return path.resolve()


def source_facts():
    cfg = config()
    records = []
    license_data = io_path(ROOT / "LICENSE").read_bytes()
    require(digest(license_data) == cfg["license"]["sha256"], "Official Koin license differs")
    for module in MODULES:
        item = cfg["modules"][module]
        expected = set()
        for member, info in item["sourceEntries"].items():
            logical = "sources/" + module + "/" + member
            path = source_from_logical(logical)
            data = io_path(path).read_bytes()
            require(len(data) == info["size"] and digest(data) == info["sha256"], "Original Koin source differs: " + logical)
            expected.add(path)
            records.append({"path": logical, "sha256": info["sha256"], "size": info["size"],
                "origin": cfg["archive"]["url"], "commit": cfg["archive"]["commit"], "archiveEntry": info["entry"]})
        actual = set()
        root = module_source_root(module)
        for source_set in ("commonMain", "nativeMain", "ohosMain"):
            folder = root / source_set
            actual.update(path.resolve() for path in logical_paths(io_path(folder).rglob("*.kt")) if io_path(path).is_file())
        require(len(expected) == item["sourceFileCount"] and actual == expected, "Koin source inventory differs: " + module)
    require(len(records) == 114, "Incomplete Koin source closure")
    return cfg, records, license_data


def plan():
    require(re.fullmatch(r"[0-9a-f]{40}", ARGS.source_sha) is not None and
            re.fullmatch(r"[1-9][0-9]*", ARGS.run_id) is not None and
            re.fullmatch(r"[1-9][0-9]*", ARGS.run_attempt) is not None, "Immutable source SHA and positive run identifiers required")
    actual = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], env=environment(), check=True, capture_output=True, text=True).stdout.strip()
    require(actual == ARGS.source_sha, "Koin checkout differs from requested source SHA")
    cfg, records, _ = source_facts()
    require(cfg["group"] == GROUP and cfg["kotlin"] == "2.4.20-0.1.0-08" and cfg["archive"]["commit"] == "dc86ef8dd8fbe8564fb7453c03f5b738da3450bb", "Koin identity differs")
    recipe = []
    for path in sorted(PORT.rglob("*")):
        relative = path.relative_to(PORT)
        if path.is_file() and not any(part in (".local", ".gradle", ".kotlin", "build", "__pycache__") for part in relative.parts):
            data = io_path(path).read_bytes()
            recipe.append({"path": relative.as_posix(), "size": len(data), "sha256": digest(data)})
    identity = {"sourceCommit": ARGS.source_sha, "version": cfg["version"], "group": GROUP,
                "toolingCommit": TOOL_REVISION, "sources": records, "recipe": recipe}
    fingerprint = digest(json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
    return {**identity, "fingerprint": fingerprint, "work": str(ARGS.work.resolve()), "repository": str(ARGS.repository.resolve()),
            "toolingHome": str(TOOLS), "runtimeBoundary": cfg["runtimeBoundary"],
            "tasks": ["recordMetadataProducers", "recordX64Producers", "recordArm64Producers", "publishAllPublicationsToPortRepository"],
            "consumerLink": "NOT_RUN", "hapRuntime": "NOT_RUN"}


def environment():
    return {key: os.environ[key] for key in ("PATH", "HOME", "JAVA_HOME", "LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR", "LD_LIBRARY_PATH", "GRADLE_USER_HOME", "KONAN_DATA_DIR", "TMPDIR", "TEMP", "TMP") if key in os.environ}


def members(path):
    with zipfile.ZipFile(io_path(path)) as archive:
        require(len(archive.namelist()) == len(set(archive.namelist())), "Duplicate archive entry")
        require(sum(i.file_size for i in archive.infolist()) < 64 * 1024 * 1024, "Expanded archive too large")
        for entry in archive.infolist():
            name = PurePosixPath(entry.filename)
            require(not name.is_absolute() and ".." not in name.parts and "\\" not in entry.filename
                    and ":" not in entry.filename, f"Unsafe archive entry: {entry.filename}")
            require((entry.external_attr >> 16) & 0o170000 != 0o120000, "Archive symlink forbidden")
            if not entry.is_dir():
                yield name, archive.read(entry)


def within_repository(directory, url, repository):
    require(not urlparse(url).scheme and not url.startswith(("/", "\\")), "Non-relative publication URL")
    path = Path(os.path.abspath(directory / url))
    safe(path)
    require(path.is_relative_to(repository) and io_path(path).is_file(), "Missing/outside linked publication")
    return path


def verify_native_producer(published, records):
    """对齐 packed 原字节或 unpacked 全 member 集，不重写 Native manifest。"""
    published_members = {str(name): {"sha256": digest(data), "size": len(data)}
                         for name, data in members(published)}
    packed = [item for item in records if item["path"].endswith(".klib")]
    if packed:
        require(any(item["sha256"] == digest(io_path(published).read_bytes()) for item in packed),
                "Published packed Native Klib differs from producer")
        return
    manifests = [Path(item["path"]) for item in records
                 if Path(item["path"]).name == "manifest" and Path(item["path"]).parent.name == "default"]
    require(len(manifests) == 1, "Expected one unpacked Native default/manifest")
    root = manifests[0].parent.parent
    actual_files = {path for path in logical_paths(io_path(root).rglob("*")) if io_path(path).is_file()}
    recorded = {Path(item["path"]): item for item in records if Path(item["path"]).is_relative_to(root)}
    require(set(recorded) == actual_files, "Unpacked Native evidence does not cover every member")
    for path in actual_files:
        safe(path)
    produced_members = {path.relative_to(root).as_posix(): {"sha256": item["sha256"], "size": item["size"]}
                        for path, item in recorded.items()}
    require(produced_members == published_members, "Published Native member SHA/size set differs from producer")


def verify_files(records):
    require(len({item["path"] for item in records}) == len(records), "Duplicate file record")
    for item in records:
        path = io_path(item["path"])
        require(path.is_file() and path.stat().st_size == item["size"] and digest(path.read_bytes()) == item["sha256"],
                f"Recorded input/tool changed: {item['path']}")
    return {Path(item["path"]) for item in records}


def verify_state(record):
    state = record["state"]
    require(all(state[key] is True for key in ("inTaskGraph", "executed", "enabled")) and
            state["noSource"] is False and state["failure"] is None and
            state["skipMessage"] in (None, "UP-TO-DATE", "FROM-CACHE"), "Invalid effective producer state")


def verify_plugins(module, plugins, input_paths):
    paths = verify_files(plugins)
    require(paths <= input_paths, "Compiler plugin is detached from actual task inputs")
    compose = [path for path in paths if "compose-compiler" in path.name]
    require(bool(compose) == (module in ("koin-compose", "koin-compose-viewmodel")), "Compose plugin module scope mismatch")
    require(all(config()["kotlin"] in path.name for path in compose), "Compose compiler plugin version mismatch")
    if compose:
        fixed = load(PORT / "compiler-plugin-toolchain.json")["artifacts"]["org.jetbrains.kotlin:kotlin-compose-compiler-plugin-embeddable"]
        require(len(compose) == 1 and record(compose[0])["size"] == fixed["size"] and
                record(compose[0])["sha256"] == fixed["sha256"], "Compose compiler plugin bytes differ")


def verify_host_evidence(current, stage, evidence):
    work = Path(current["work"])
    metadata = load(TOOLS / TOOL_DIR / "metadata-toolchain.json")
    commonizer = load(TOOLS / TOOL_DIR / "commonizer-toolchain.json")
    native = load(TOOLS / TOOL_DIR / "native-compiler-toolchain.json")
    common_pins = {**metadata["launcherPins"], **commonizer["pins"]}
    common_artifacts = {**metadata["launcherArtifacts"], **commonizer["artifacts"]}
    native_pins, native_artifacts = native["pins"], native["artifacts"]
    require(native["configuration"] == "kotlinCompilerClasspath" and len(native_pins) == 14 and
            set(native_pins) == set(native_artifacts), "Koin Native host lock differs")
    require(commonizer["configuration"] == "kotlinKlibCommonizerClasspath" and len(common_pins) == 9 and
            set(common_pins) == set(common_artifacts), "Koin commonizer host lock differs")
    labels = {"recordMetadataProducers": "Metadata", "recordX64Producers": "NativeX64", "recordArm64Producers": "NativeArm64"}
    label = labels[stage]
    hosts = evidence["hostTools"]
    require(len(hosts) == len(MODULES) and {item["module"] for item in hosts} == set(MODULES),
            "Koin host verification module set differs")
    for host in hosts:
        module = host["module"]
        task = f":{module}:verifyKoinHost"
        require(host["task"] == task, "Koin host task identity differs")
        verify_state(host)
        report_record = host["report"]
        expected_path = (work / "evidence" / ("host-" + module + "-" + label + ".json")).resolve()
        require(Path(report_record["path"]).resolve() == expected_path, "Koin host report path differs")
        verify_files([report_record])
        report = load(expected_path)
        require(report["fingerprint"] == current["fingerprint"] and report["stage"] == label and
                report["module"] == module and report["task"] == task, "Koin host report identity differs")
        for kind, configuration, pins, artifacts in (
                ("native", native["configuration"], native_pins, native_artifacts),
                ("commonizer", commonizer["configuration"], common_pins, common_artifacts)):
            actual = report[kind]
            records = actual["artifacts"]
            require(actual["configuration"] == configuration and actual["edges"] and len(records) == len(pins) and
                    {item["gav"] for item in records} == {ga + ":" + version for ga, version in pins.items()},
                    "Koin host resolved GAV set differs: " + module + ":" + kind)
            for item in records:
                ga = item["gav"].rsplit(":", 1)[0]
                files = item["files"]
                require(len(files) == 1 and files[0]["size"] == artifacts[ga]["size"] and
                        files[0]["sha256"] == artifacts[ga]["sha256"], "Koin host artifact identity differs: " + ga)
                verify_files(files)
    for producer in evidence["producers"]:
        task = producer["task"]
        if task.endswith("WithMetadataCli") or task.endswith("KotlinMetadata") or ":compileKotlin" in task:
            module = task.split(":")[1]
            require(f":{module}:verifyKoinHost" in producer["dependencies"], "Koin compiler task lacks host verification dependency")


def metadata_compile_name(source):
    suffix = source[0].upper() + source[1:]
    return f"compile{suffix}WithMetadataCli" if source in ("commonMain",) else f"compile{suffix}KotlinMetadata"


def metadata_task_names(module, source_sets):
    names = {f":{module}:allMetadataJar"}
    for source in source_sets:
        suffix = source[0].upper() + source[1:]
        compile_name = metadata_compile_name(source)
        names |= {f":{module}:transform{suffix}DependenciesMetadata", f":{module}:{compile_name}"}
    return names


def verify_direct_project_metadata(work, module, source, compile_record, transform, records):
    cfg = config()
    upstream_modules = cfg["projectDependencies"][module]
    require(upstream_modules and source in cfg["modules"][module]["sourceSets"], "No declared direct project metadata edge")
    index = work / "build" / module / "kotlinTransformedMetadataLibraries" / source / (source + ".json")
    require({Path(item["path"]) for item in transform["outputs"]} == {index} and
            json.loads(io_path(index).read_text(encoding="utf-8")) == [], "Unreviewed transform output layout")
    manifest = json.loads(io_path(work / "source-manifest.json").read_text(encoding="utf-8"))
    inputs = {Path(item["path"]): item for item in compile_record["inputs"]}
    bindings = []
    for upstream in upstream_modules:
        locked = {source_from_logical(item["path"]): item for item in manifest["files"]
                  if item["path"].startswith(f"sources/{upstream}/") and item["path"].endswith(".kt")}
        for upstream_source in cfg["modules"][upstream]["sourceSets"]:
            producer = records[f":{upstream}:{metadata_compile_name(upstream_source)}"]
            verify_state(producer)
            require(producer["task"] in compile_record["dependencies"], "Direct metadata producer missing from actual task graph")
            source_records = {Path(item["path"]): item for item in producer["sourceInputs"]}
            own = {p for p in locked if p.is_relative_to(module_source_root(upstream) / upstream_source)}
            require(own and own <= set(source_records) <= set(locked), "Direct metadata source-set mismatch")
            require(all(item["sha256"] == locked[path]["sha256"] and item["size"] == locked[path]["size"]
                        for path, item in source_records.items()), "Direct metadata source differs from canonical GA bytes")
            manifests = [Path(item["path"]) for item in producer["outputs"] if Path(item["path"]).name == "manifest"
                         and Path(item["path"]).parent.name == "default"]
            require(len(manifests) == 1, "Ambiguous direct project Klib")
            root = manifests[0].parent.parent
            require(root.is_relative_to(work / "build" / upstream), "Direct metadata producer belongs to wrong module")
            produced = {Path(item["path"]): item for item in producer["outputs"] if Path(item["path"]).is_relative_to(root)}
            require(produced and set(produced) <= set(inputs) and any("linkdata" in path.parts for path in produced),
                    "Direct project metadata is not completely consumed")
            require(all(item["sha256"] == inputs[path]["sha256"] and item["size"] == inputs[path]["size"]
                        for path, item in produced.items()), "Direct project metadata bytes differ from producer")
            bindings.append({"module": upstream, "sourceSet": upstream_source, "task": producer["task"], "output": str(root)})
    return {"kind": "DIRECT_PROJECT_METADATA", "bindings": bindings}


def verify_metadata_chain(work, module, sources, records):
    source_sets = {p.relative_to(module_source_root(module)).parts[0] for p in sources}
    jar_record = records[f":{module}:allMetadataJar"]
    jar_inputs = {Path(item["path"]) for item in jar_record["inputs"]}
    common_output = work / "build" / module / "metadata-cli/commonMain/klib"
    input_bindings = []
    for source in source_sets:
        suffix = source[0].upper() + source[1:]
        name = metadata_compile_name(source)
        compile_record = records[f":{module}:{name}"]
        transform = records[f":{module}:transform{suffix}DependenciesMetadata"]
        require(transform["task"] in compile_record["dependencies"] and compile_record["task"] in jar_record["dependencies"],
                "Disconnected transform/compile/metadata JAR chain")
        actual_sources = {Path(item["path"]) for item in compile_record["sourceInputs"]}
        own_sources = {p for p in sources if p.is_relative_to(module_source_root(module) / source)}
        require(own_sources <= actual_sources <= sources, "Metadata compiler source set differs from prepared sources")
        compile_inputs = {Path(item["path"]) for item in compile_record["inputs"]}
        transformed = {Path(item["path"]) for task in records.values() if
                       task["task"].startswith(f":{module}:transform") and task["task"] in compile_record["dependencies"]
                       for item in task["outputs"]}
        if compile_inputs & transformed:
            binding = {"kind": "TRANSFORMED_METADATA", "inputs": [str(path) for path in sorted(compile_inputs & transformed)]}
        else:
            binding = verify_direct_project_metadata(work, module, source, compile_record, transform, records)
        input_bindings.append({"module": module, "sourceSet": source, "task": compile_record["task"], **binding})
        require(jar_inputs & {Path(item["path"]) for item in compile_record["outputs"]}, "Metadata JAR misses compile output")
        if source in ("commonMain",):
            cli_output = work / "build" / module / "metadata-cli" / source / "klib"
            expected_kind = "COMMON_METADATA_CLI_COMPILE" if source == "commonMain" else "SOURCE_SET_METADATA_CLI_COMPILE"
            producer = json.loads(io_path(work / "build" / module / "metadata-cli" / source / "producer.json").read_text(encoding="utf-8"))
            require(producer["task"] == compile_record["task"] and producer["kind"] == expected_kind and producer["sourceSet"] == source and
                    producer["exitCode"] == 0 and producer["compilerVersion"] == config()["kotlin"] and
                    producer["mainClass"] == "org.jetbrains.kotlin.cli.metadata.KotlinMetadataCompiler" and
                    producer["retiredTask"]["enabled"] is False and
                    producer["retiredTask"]["path"] == f":{module}:compile{suffix}KotlinMetadata" and
                    Path(producer["output"]) == cli_output,
                    "Invalid common CLI producer")
            require(verify_files(producer["sources"]) == own_sources, "Common CLI source set mismatch")
            verify_files(producer["launcher"])
            verify_files(producer["outputs"])
            verify_plugins(module, producer["plugins"], compile_inputs)
            launcher_pins = load(TOOLS / TOOL_DIR / "metadata-toolchain.json")["launcherPins"]
            require(all(launcher_pins.get(item["gav"].rsplit(":", 1)[0]) == item["gav"].rsplit(":", 1)[1]
                        for item in producer["launcher"]) and any(item["gav"] ==
                        "org.jetbrains.kotlin:kotlin-compiler-embeddable:" + config()["kotlin"] for item in producer["launcher"]),
                    "Common CLI launcher does not match audited compiler closure")
            require(producer["sourceManifestSha256"] == digest(io_path(work / "source-manifest.json").read_bytes()) and
                    producer["argumentsSha256"] == digest(io_path(producer["argumentsFile"]).read_bytes()),
                    "Common CLI source/arguments evidence changed")
            require({Path(item["path"]) for item in producer["outputs"]} <=
                    {Path(item["path"]) for item in compile_record["outputs"]}, "Common CLI outputs detached from effective task")
            arguments = producer["arguments"]
            plugin_args = [arg.removeprefix("-Xplugin=") for arg in arguments if arg.startswith("-Xplugin=")]
            require(([Path(p) for p in plugin_args[0].split(",")] if len(plugin_args) == 1 else []) ==
                    [Path(item["path"]) for item in producer["plugins"]] and len(plugin_args) <= 1,
                    "Metadata CLI plugin arguments differ from actual plugin records")
            require([arguments[i + 1] for i, value in enumerate(arguments[:-1]) if value == "-P"] == producer["pluginOptions"],
                    "Metadata CLI plugin options differ from task options")
            actual_refines = [Path(path) for path in producer["refinesPaths"]]
            require(actual_refines == [Path(path) for path in producer["publicRefinesPaths"]],
                    "Execution-time refines model differs from public parent compilation output")
            require(actual_refines == [Path(path) for path in compile_record["refinesPaths"]], "CLI refines model differs from recorded task")
            refine_arguments = [arg.removeprefix("-Xrefines-paths=") for arg in arguments if arg.startswith("-Xrefines-paths=")]
            require(not refine_arguments and not actual_refines, "Common CLI must not claim a refined parent")
            require(arguments[arguments.index("-module-name") + 1] == f"{GROUP}:{module}_{source}" and
                    "-Xtarget-platform=Native" in arguments and
                    {Path(arg) for arg in arguments if arg.endswith(".kt")} == own_sources,
                    "Common CLI arguments do not compile the prepared source set")
            require(Path(arguments[arguments.index("-d") + 1]) == cli_output,
                    "Common CLI arguments target a different output")
            library_roots = [Path(item["path"]) for item in producer["libraries"]]
            require([Path(value) for value in arguments[arguments.index("-classpath") + 1].split(os.pathsep)] == library_roots,
                    "Metadata CLI classpath order does not match actual KGP library records")
            for library in producer["libraries"]:
                require(verify_files(library["files"]) <= compile_inputs, "Common CLI library missing from actual task inputs")
        if source != "commonMain":
            parents = compile_record["parentCompilationOutputs"]
            require({p["sourceSet"] for p in parents} == set(config()["modules"][module]["sourceSetParents"][source]),
                    "Public metadata parent DAG differs from prepared source model")
            for parent in parents:
                parent_record = records[f":{module}:{metadata_compile_name(parent['sourceSet'])}"]
                require(parent_record["task"] in compile_record["dependencies"] and
                        parent_record["task"] in parent["builtBy"], "Parent output is detached from effective producer")
                parent_files = verify_files(parent["files"])
                require(parent_files and parent_files <= compile_inputs and parent_files <=
                        {Path(item["path"]) for item in parent_record["outputs"]},
                        "Intermediate metadata did not consume exact public parent output")
                require(all(any(path.is_relative_to(Path(root)) for root in parent["roots"]) for path in parent_files),
                        "Parent member outside public compilation output")
    jar_files = [Path(item["path"]) for item in jar_record["outputs"] if item["path"].endswith(".jar")]
    require(len(jar_files) == 1 and any("linkdata/" in str(name) and data for name, data in members(jar_files[0])),
            "Metadata JAR lacks compiled metadata")
    return input_bindings


def prepare():
    cfg, records, license_data = source_facts()
    current = plan()
    work = Path(current["work"])
    for module in MODULES:
        destination = work / "legal" / module / "META-INF/LICENSE"
        write(destination, license_data)
        records.append({"path": destination.relative_to(work).as_posix(), "sha256": digest(license_data),
                        "size": len(license_data), "origin": cfg["archive"]["url"], "archiveEntry": cfg["license"]["entry"]})
    write(work / "source-manifest.json", encoded({"files": records, "sources": cfg,
          "fingerprint": current["fingerprint"], "status": "PREPARED_NOT_COMPILED"}))
    provenance = {"fingerprint": current["fingerprint"], "sourceCommit": current["sourceCommit"], "group": GROUP,
                  "version": current["version"], "modules": list(MODULES), "toolingCommit": TOOL_REVISION,
                  "upstream": cfg["archive"], "sourceEntries": {module: cfg["modules"][module]["sourceEntries"] for module in MODULES},
                  "license": cfg["license"], "statelyPinsSha256": digest(io_path(PORT / "stately-pins.json").read_bytes())}
    write(work / "provenance.json", encoded(provenance))
    write(work / "plan.json", encoded(current))
    return {**current, "status": "PREPARED_NOT_COMPILED", "files": len(records)}


def verify():
    current = plan()
    work = Path(current["work"])
    invocation = json.loads((io_path(work / "build-invocation.json")).read_text())
    require(invocation["fingerprint"] == current["fingerprint"] and invocation["status"] == "BUILD_EXIT_0",
            "No successful build invocation for this recipe")
    cfg = config()
    steps = invocation["steps"]
    require([s["name"] for s in steps] == ["metadata", "native-x64", "native-arm64", "publish"] and
            all(s["exitCode"] == 0 for s in steps), "Incomplete build stages")
    source_manifest = work / "source-manifest.json"
    sources = json.loads(io_path(source_manifest).read_text(encoding="utf-8"))
    require(sources["fingerprint"] == current["fingerprint"], "Source fingerprint differs")
    _, source_rows, license_data = source_facts()
    original = {cfg["license"]["entry"]: license_data}
    for module in MODULES:
        require(io_path(work / "legal" / module / "META-INF/LICENSE").read_bytes() == license_data,
                "Prepared original license changed")
    require({item["path"]: (item["size"], item["sha256"]) for item in sources["files"] if item["path"].startswith("sources/")} ==
            {item["path"]: (item["size"], item["sha256"]) for item in source_rows}, "Source manifest differs from checkout")
    def check_record(record):
        path = Path(record["path"])
        safe(path)
        require(path.is_relative_to(work) and io_path(path).is_file() and
                io_path(path).stat().st_size == record["size"] and digest(io_path(path).read_bytes()) == record["sha256"],
                f"Producer output differs: {path}")

    for source in sources["files"]:
        if source["path"].startswith("sources/"):
            actual = record(source_from_logical(source["path"]))
            require(actual["size"] == source["size"] and actual["sha256"] == source["sha256"], "Original source bytes changed")
        else:
            check_record({**source, "path": str(work / source["path"])})
    producer_records = {}
    all_task_records = {}
    prepared_sources = {source_from_logical(item["path"]) for item in sources["files"] if item["path"].startswith("sources/") and item["path"].endswith(".kt")}
    for stage in current["tasks"][:3]:
        evidence = json.loads((io_path(work / "evidence" / (stage + ".json"))).read_text(encoding="utf-8"))
        require(evidence["sourceManifestSha256"] == digest(io_path(source_manifest).read_bytes()), "Producer input mismatch")
        target = None if stage == "recordMetadataProducers" else ("ohosX64" if stage == "recordX64Producers" else "ohosArm64")
        expected_tasks = set()
        for module in cfg["modules"]:
            module_sources = {p for p in prepared_sources if p.is_relative_to(module_source_root(module))}
            if target is None:
                expected_tasks |= metadata_task_names(module, {p.relative_to(module_source_root(module)).parts[0] for p in module_sources})
            else:
                suffix = target[0].upper() + target[1:]
                expected_tasks.add(f":{module}:compileKotlin{suffix}")
        require(len(evidence["producers"]) == len(expected_tasks) and
                {p["task"] for p in evidence["producers"]} == expected_tasks, "Wrong module/ABI producer task matrix")
        require(evidence["stage"] == stage and evidence["target"] == target, "Wrong producer stage/ABI")
        verify_host_evidence(current, stage, evidence)
        for producer in evidence["producers"]:
            require(producer["executed"] and producer["outputs"], "Producer did not execute")
            verify_state(producer)
            verify_files(producer["inputs"])
            verify_files(producer["sourceInputs"])
            require({item["path"] for item in producer["sourceInputs"]} ==
                    {item["path"] for item in producer["inputs"] if item["path"].endswith(".kt")},
                    "Source snapshot is detached from actual task inputs")
            all_task_records[producer["task"]] = producer
            producer_records[producer["task"]] = producer["outputs"]
            for output in producer["outputs"]:
                check_record(output)
            if target and ":compileKotlin" in producer["task"]:
                module = producer["task"].split(":")[1]
                require({Path(item["path"]) for item in producer["sourceInputs"]} ==
                        {p for p in prepared_sources if p.is_relative_to(module_source_root(module))},
                        "Native compiler did not consume exactly its prepared module source set")
            if ":compileKotlin" in producer["task"] or producer["task"].endswith("KotlinMetadata"):
                verify_plugins(producer["task"].split(":")[1], producer["compilerPlugins"],
                               {Path(item["path"]) for item in producer["inputs"]})
        require(evidence["dependencies"], "Missing resolved dependency evidence")
        for dependency in evidence["dependencies"]:
            for item in dependency["files"]:
                path = Path(item["path"])
                require(io_path(path).is_file() and digest(io_path(path).read_bytes()) == item["sha256"], "Resolved dependency bytes changed")
            if target and dependency["component"].startswith("co.touchlab:"):
                name = dependency["component"].split(":")[1]
                expected = [item for item in load(PORT / "stately-pins.json")["entries"] if item["kind"] == "klib" and
                            item["artifact"] in (name, name + "-" + target.lower())]
                require(len(expected) == 1 and any(item["sha256"] == expected[0]["sha256"] and item["size"] == expected[0]["bytes"]
                        for item in dependency["files"]), "Resolved Stately Native artifact differs from the original pin")
    metadata_input_bindings = []
    for module in cfg["modules"]:
        metadata_input_bindings.extend(verify_metadata_chain(work, module,
            {p for p in prepared_sources if p.is_relative_to(module_source_root(module))}, all_task_records))
    repository = Path(current["repository"])
    pins = {key: value for key, value in load(PORT / "dependency-pins.json").items() if not key.startswith("io.insert-koin:")}
    for module in MODULES:
        for suffix in ("", "-ohosx64", "-ohosarm64"):
            pins[GROUP + ":" + module + suffix] = cfg["version"]
    metadata_pins = json.loads((io_path(PORT / "metadata-pins.json")).read_text(encoding="utf-8"))
    direct = {
        "koin-core": ["co.touchlab:stately-concurrency", "co.touchlab:stately-concurrent-collections"],
        "koin-core-viewmodel": [GROUP + ":koin-core", "org.jetbrains.androidx.lifecycle:lifecycle-viewmodel", "org.jetbrains.androidx.lifecycle:lifecycle-viewmodel-savedstate"],
        "koin-compose": [GROUP + ":koin-core", "org.jetbrains.compose.runtime:runtime", "org.jetbrains.compose.foundation:foundation"],
        "koin-compose-viewmodel": [GROUP + ":koin-compose", GROUP + ":koin-core-viewmodel", "org.jetbrains.androidx.lifecycle:lifecycle-viewmodel-compose"],
    }
    publications = []
    for module in cfg["modules"]:
        for target in (None, *cfg["targets"]):
            artifact = module if target is None else module + "-" + target.lower()
            directory = repository / Path(*GROUP.split(".")) / artifact / cfg["version"]
            prefix = artifact + "-" + cfg["version"]
            gmm = json.loads((io_path(directory / (prefix + ".module"))).read_text(encoding="utf-8"))
            require(gmm["component"] == {**gmm["component"], "group": cfg["group"], "module": module,
                                         "version": cfg["version"]}, "Wrong published GMM identity")
            root_directory = repository / Path(*GROUP.split(".")) / module / cfg["version"]
            root_module_path = root_directory / (module + "-" + cfg["version"] + ".module")
            if target:
                owner = within_repository(directory, gmm["component"]["url"], repository)
                require(owner == root_module_path, "Native leaf has wrong root owner URL")
                root_gmm = json.loads(io_path(owner).read_text(encoding="utf-8"))
                root_api_edges = [variant for variant in root_gmm["variants"]
                                  if variant.get("attributes", {}).get("org.gradle.usage") == "kotlin-api" and
                                  variant.get("attributes", {}).get("org.gradle.category") == "library" and
                                  variant.get("attributes", {}).get("org.jetbrains.kotlin.platform.type") == "native" and
                                  variant.get("attributes", {}).get("org.jetbrains.kotlin.native.target") == cfg["targets"][target]]
                require(len(root_api_edges) == 1, "Missing or ambiguous root Native API edge")
                edge = root_api_edges[0]["available-at"]
                require((edge["group"], edge["module"], edge["version"]) == (cfg["group"], artifact, cfg["version"]) and
                        within_repository(root_directory, edge["url"], repository) == directory / (prefix + ".module"),
                        "Root Native API edge does not return to physical leaf GAV")
                leaf_api = [variant for variant in gmm["variants"] if
                            variant.get("attributes", {}).get("org.gradle.usage") == "kotlin-api" and
                            variant.get("attributes", {}).get("org.gradle.category") == "library" and
                            variant.get("attributes", {}).get("org.jetbrains.kotlin.native.target") == cfg["targets"][target] and
                            variant.get("attributes", {}).get("org.jetbrains.kotlin.platform.type") == "native"]
                require(len(leaf_api) == 1, "Missing or ambiguous Native leaf API variant")
                api_klibs = [item for item in leaf_api[0].get("files", []) if
                             within_repository(directory, item["url"], repository) == directory / (prefix + ".klib")]
                require(len(api_klibs) == 1, "Native leaf API URL does not expose its physical Klib")
            pom = ET.parse(io_path(directory / (prefix + ".pom"))).getroot()
            namespace = {"m": "http://maven.apache.org/POM/4.0.0"}
            require([pom.findtext("m:" + key, namespaces=namespace) for key in ("groupId", "artifactId", "version")] ==
                    [cfg["group"], artifact, cfg["version"]], "Wrong POM identity")
            allowed = pins if target else {**pins, **metadata_pins}
            expected = {key: pins[key] for key in ["org.jetbrains.kotlin:kotlin-stdlib", *direct[module]]}
            api = [variant for variant in gmm["variants"] if
                   (not target and variant.get("attributes", {}).get("org.gradle.usage") == "kotlin-metadata" and
                    variant.get("attributes", {}).get("org.jetbrains.kotlin.platform.type") == "common") or
                   (target and variant.get("attributes", {}).get("org.gradle.usage") == "kotlin-api" and
                    variant.get("attributes", {}).get("org.jetbrains.kotlin.native.target") == cfg["targets"][target])]
            require(len(api) == 1 and {dep["group"] + ":" + dep["module"]: dep.get("version", {}).get("requires")
                    for dep in api[0].get("dependencies", [])} == expected, "Koin published API dependency set differs")
            found = {}
            suffix = "-" + target.lower() if target else ""
            for dependency in pom.findall("m:dependencies/m:dependency", namespace):
                group = dependency.findtext("m:groupId", namespaces=namespace)
                artifact_name = dependency.findtext("m:artifactId", namespaces=namespace)
                key = group + ":" + artifact_name
                require(dependency.findtext("m:version", namespaces=namespace) == allowed.get(key), "Unpinned POM dependency")
                logical = group + ":" + (artifact_name.removesuffix(suffix) if suffix else artifact_name)
                require(logical not in found and logical in expected and
                        dependency.findtext("m:version", namespaces=namespace) == expected[logical] and
                        dependency.findtext("m:scope", namespaces=namespace) == ("compile" if target else "runtime"),
                        "Koin POM dependency or scope differs")
                if target and group == GROUP:
                    require(artifact_name.endswith(suffix), "Koin POM project dependency target differs")
                found[logical] = expected[logical]
            require(found == expected, "Koin POM dependency set differs")
            for variant in gmm["variants"]:
                for dependency in variant.get("dependencies", []):
                    key = dependency["group"] + ":" + dependency["module"]
                    require(dependency.get("version", {}).get("requires") == allowed.get(key) and key in allowed,
                            f"Unpinned GMM dependency: {key}")
                for item in variant.get("files", []):
                    path = within_repository(directory, item["url"], repository)
                    require(item["sha256"] == digest(io_path(path).read_bytes()) and item["size"] == io_path(path).stat().st_size,
                            "Published GMM checksum/size mismatch")
                link = variant.get("available-at")
                if link:
                    path = within_repository(directory, link["url"], repository)
                    expected_artifact = module + "-" + next((key.lower() for key, value in cfg["targets"].items()
                        if value == variant.get("attributes", {}).get("org.jetbrains.kotlin.native.target")), "INVALID")
                    require(target is None and (link["group"], link["module"], link["version"]) ==
                            (cfg["group"], expected_artifact, cfg["version"]), "Unexpected publication redirect identity")
                    require(path == repository / Path(*GROUP.split(".")) / expected_artifact / cfg["version"] /
                            (expected_artifact + "-" + cfg["version"] + ".module"), "Wrong physical redirect path")
            if target:
                klib = directory / (prefix + ".klib")
                task_name = f":{module}:compileKotlin{target[0].upper() + target[1:]}"
                produced = producer_records[task_name]
                verify_native_producer(klib, produced)
                with zipfile.ZipFile(io_path(klib)) as archive:
                    manifests = [archive.read(n).decode() for n in archive.namelist() if n == "manifest" or n.endswith("/manifest")]
                    require(len(manifests) == 1, "Ambiguous Klib manifest")
                    props = {key: value.replace("\\:", ":").replace("\\=", "=").replace("\\ ", " ")
                             for key, value in (line.split("=", 1) for line in manifests[0].splitlines() if "=" in line)}
                    require(props.get("unique_name") == cfg["group"] + ":" + module, "Klib identity rewritten")
                    require(props.get("native_targets") == cfg["targets"][target], "Wrong Native target")
                    require("2.4.20" in props.get("compiler_version", ""), "Wrong Native compiler")
                    require(any("ir/" in n for n in archive.namelist()) and
                            any("linkdata/" in n for n in archive.namelist()), "Missing Native IR/metadata")
            else:
                root_jar = directory / (prefix + ".jar")
                require(any(item["sha256"] == digest(io_path(root_jar).read_bytes())
                            for item in producer_records[f":{module}:allMetadataJar"]), "Published metadata differs from producer")
            require(io_path(directory / (prefix + "-provenance.json")).read_bytes() == io_path(work / "provenance.json").read_bytes(),
                    "Published provenance differs")
            for published_jar in logical_paths(io_path(directory).glob("*.jar")):
                published_entries = {str(name): data for name, data in members(published_jar)}
                require(published_entries.get("META-INF/LICENSE") == original[cfg["license"]["entry"]],
                        "Published JAR lacks original GA license bytes")
            publications.append({"artifact": artifact, "files": [
                {"path": str(p), "sha256": digest(io_path(p).read_bytes()), "size": io_path(p).stat().st_size}
                for p in sorted(logical_paths(io_path(directory).iterdir())) if io_path(p).is_file()]})
    result = {**current, "status": "PUBLICATIONS_VERIFIED_CONSUMER_NOT_RUN", "publications": publications,
              "metadataInputBindings": metadata_input_bindings, "nativeParentProof": "PUBLIC_COMPILATION_OUTPUT_BUILTBY_AND_ACTUAL_INPUTS_NOT_FULL_COMPILER_ARGV"}
    # 候选验证不写 accepted ledger；必须等真实 consumer 与主控验收。
    write(work / "verification.json", encoded(result))
    return result


def main():
    global ARGS, TOOLS
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("Prepare", *TASKS, "Verify"))
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--tooling", type=Path)
    parser.add_argument("--native-home", type=Path)
    ARGS = parser.parse_args()
    work = ARGS.work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    TOOLS = checked_tools(work, ARGS.tooling)
    current = plan()
    if ARGS.stage == "Prepare":
        print(json.dumps(prepare(), ensure_ascii=False))
        return
    require((work / "plan.json").is_file() and load(work / "plan.json") == current, "Prepared Koin identity differs")
    require(ARGS.native_home is not None, "Explicit Native home required")
    props = dict(line.split("=", 1) for line in (ARGS.native_home / "konan/konan.properties").read_text(encoding="utf-8").splitlines() if "=" in line)
    require(props.get("compilerVersion", "").strip() == "2.4.20-0.1.0-08", "Native compiler version differs")
    if ARGS.stage == "Verify":
        print(json.dumps(verify(), ensure_ascii=False))
        return
    previous = ["Tasks", "Resolve", "Metadata", "NativeX64", "NativeArm64", "Publish"]
    for stage in previous[:previous.index(ARGS.stage)]:
        row = load(work / "invocations" / (stage + ".json"))
        require(row["fingerprint"] == current["fingerprint"] and row["exitCode"] == 0, "Prior stage did not succeed: " + stage)
    if ARGS.stage == "Publish":
        repo = ARGS.repository.resolve() / Path(*GROUP.split("."))
        for module in MODULES:
            for suffix in ("", "-ohosx64", "-ohosarm64"):
                require(not (repo / (module + suffix) / current["version"]).exists(), "Publication version already exists")
    receipt = work / "invocations" / (ARGS.stage + ".json")
    require(not receipt.exists(), "Existing stage invocation; use a fresh run")
    argv = ["sh", str(PORT / "gradlew"), "-p", str(PORT), "--project-cache-dir", str(work / "project-cache"),
            "-PportWork=" + str(work), "-PportTooling=" + str(TOOLS), "-PportStage=" + ARGS.stage,
            "-PportFingerprint=" + current["fingerprint"],
            "-PportVersion=" + current["version"], "-PportRepository=" + current["repository"],
            "-Pkotlin.project.persistent.dir=" + str(work / "kotlin-cache"), "-Pkotlin.native.home=" + str(ARGS.native_home.resolve()),
            "--console=plain", "--stacktrace", *TASKS[ARGS.stage]]
    logs = work / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    stdout, stderr = logs / (ARGS.stage + ".stdout.log"), logs / (ARGS.stage + ".stderr.log")
    with stdout.open("wb") as out, stderr.open("wb") as err:
        run = subprocess.run(argv, env=environment(), stdout=out, stderr=err)
    write(receipt, encoded({"fingerprint": current["fingerprint"], "stage": ARGS.stage, "exitCode": run.returncode,
                            "stdout": record(stdout), "stderr": record(stderr)}))
    if run.returncode:
        print(stderr.read_text(encoding="utf-8", errors="replace")[-16384:], file=sys.stderr)
        raise RuntimeError("Gradle " + ARGS.stage + " failed with exit " + str(run.returncode))
    if ARGS.stage == "Tasks":
        names = {name.lstrip(":") for name in re.findall(r"^\s*([\w:-]+)(?:\s+-|\s*$)", stdout.read_text(encoding="utf-8", errors="replace"), re.MULTILINE)}
        require(REQUIRED <= names, "Required Koin tasks absent from discovery")
        write(work / "task-discovery.json", encoded({"fingerprint": current["fingerprint"], "required": sorted(REQUIRED), "stdout": record(stdout)}))
    if ARGS.stage == "Resolve":
        resolution = load(work / "evidence/resolve.json")
        require(resolution["fingerprint"] == current["fingerprint"] and len(resolution["configurations"]) == 12,
                "Koin four-module resolution evidence differs")
    if ARGS.stage == "Publish":
        labels = (("Metadata", "metadata"), ("NativeX64", "native-x64"), ("NativeArm64", "native-arm64"), ("Publish", "publish"))
        steps = [{"name": name, "exitCode": load(work / "invocations" / (stage + ".json"))["exitCode"]} for stage, name in labels]
        write(work / "build-invocation.json", encoded({"fingerprint": current["fingerprint"], "status": "BUILD_EXIT_0", "steps": steps}))
    print(json.dumps({"stage": ARGS.stage, "fingerprint": current["fingerprint"], "exitCode": run.returncode, "stdout": record(stdout)}))


if __name__ == "__main__":
    main()

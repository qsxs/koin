# Koin 4.2.2 OHOS 独立发行入口

四模块直接编译同一 Fork 中的 114 份官方 commonMain/Native 原源，OHOS 源集保持为空，不复制或改写生产源码。上游为 [4.2.2 固定提交](https://github.com/InsertKoinIO/koin/tree/dc86ef8dd8fbe8564fb7453c03f5b738da3450bb)，四份发布许可证均保留原 Apache-2.0 字节。Koin 模块间依赖继续使用项目依赖，原有 API 与 implementation 范围不变；Stately 继续使用原已适配的 `2.1.0-1.1.0` 坐标和固定制品。

临时根坐标为 `io.github.qsxs.cpf.koin:<原模块名>:4.2.2-cpf11-preview-<sourceSHA12>-<runID>-<attempt>`。Kotlin/Native `2.4.20-0.1.0-08`、CMP/Lifecycle `-0.1.0-11`、Gradle 9.5、JDK 17 和共享工具 A 固定；四个 commonMain 延续同版 metadata CLI，Native metadata 与 OHOS 双 ABI 延续标准 KGP。metadata 专用补充依赖不进入 Native runtime。

## producer、Pages 与公开 consumer 已验

[Fork source `4165003410d767a5281441312f70d21dea4d6aab`](https://github.com/qsxs/koin/commit/4165003410d767a5281441312f70d21dea4d6aab) 经 [producer 40684515113/2](https://github.com/qsxs/cpf-port-tooling/actions/runs/40684515113/attempts/2) 完成生产与 staging Verify；生产工作流提交 B 为 `4d01825448f83e87619888dade7e919562e8ecd4`，与共享工具内容锁 A 分别记录。该工作流上传通过 Verify 的 staging，并另上传证据，Pages 由维护者核对后手动发布。

四个根模块为 `koin-core`、`koin-core-viewmodel`、`koin-compose`、`koin-compose-viewmodel`，公共 group 为 `io.github.qsxs.cpf.koin`，本次版本为 `4.2.2-cpf11-preview-4165003410d7-40684515113-2`。临时 Maven 根 `https://qsxs.github.io/cpf-maven/`（[koin-core 根 POM](https://qsxs.github.io/cpf-maven/io/github/qsxs/cpf/koin/koin-core/4.2.2-cpf11-preview-4165003410d7-40684515113-2/koin-core-4.2.2-cpf11-preview-4165003410d7-40684515113-2.pom)、[GitHub 制品仓库](https://github.com/qsxs/cpf-maven)）。[Pages 制品提交 `4a430bb48508`](https://github.com/qsxs/cpf-maven/commit/4a430bb48508ba7ac948be3f052499262ce40957) 的 380 个文件、3,455,187 字节与 staging 经匿名 HTTP 逐字节对齐。

生产记录中 15 条项目边（7 条 metadata、8 条 Native）的输入与输出记录匹配。Producer 在 runner 内验证 metadata CLI 参数，但原始 `producer.json`/`arguments.txt` 未上传，无法独立复核各参数；Native 任务输入记录不是完整 compiler argv。上传记录之间的大小/哈希匹配不等于重新对所有已删除的 runner 输入文件计算哈希。

项目依赖保持：`koin-core` 无 Koin 项目依赖；`koin-core-viewmodel` 和 `koin-compose` 各以 `api` 依赖 `koin-core`；`koin-compose-viewmodel` 以 `api` 依赖 `koin-compose` 与 `koin-core-viewmodel`。Stately 沿用匿名 CPF `2.1.0-1.1.0` 既有制品，15 个对象身份匹配，不表示重新构建或普遍 ABI 兼容。

[consumer 40703356336/1](https://github.com/qsxs/cpf-port-tooling/actions/runs/40703356336/attempts/1) 在 metadata 阶段下载 sysroot 超时；正常重试的 [/2](https://github.com/qsxs/cpf-port-tooling/actions/runs/40703356336/attempts/2) 使用工作流 `ed0e29a75c6f2a8a31af4a1c0ae68b8e170ffb0d`、Gradle 9.5.0，完成 Scope/ViewModel 探针的 common metadata、OHOS x64 和 arm64 三项真实编译。该次使用 K/N 标准按需下载，手工预填候选未应用。四模块 48 个公开对象与 staging 对齐，四模块依赖边和三个解析配置已独立核对。转换后的四个 metadata Klib 未上传本体，仅有 runner 记录的路径、大小、SHA 和 metadataTrace，无法在本机独立重算内部成员；Native Klib 完整文件 SHA 可与 staging 对齐。Gradle 8 消费、link、runtime、设备及 Android/iOS 回归未验证，App 引用尚未切换；历史 x64 HAP 证据不能替代本次验收。

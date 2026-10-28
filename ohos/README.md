# Koin 4.2.2 OHOS 独立发行入口

四模块直接编译同一 Fork 中的 114 份官方 commonMain/Native 原源，OHOS 源集保持为空，不复制或改写生产源码。上游为 [4.2.2 固定提交](https://github.com/InsertKoinIO/koin/tree/dc86ef8dd8fbe8564fb7453c03f5b738da3450bb)，四份发布许可证均保留原 Apache-2.0 字节。Koin 模块间依赖继续使用项目依赖，原有 API 与 implementation 范围不变；Stately 继续使用原已适配的 `2.1.0-1.1.0` 坐标和固定制品。

临时根坐标为 `io.github.qsxs.cpf.koin:<原模块名>:4.2.2-cpf11-preview-<sourceSHA12>-<runID>-<attempt>`。Kotlin/Native `2.4.20-0.1.0-08`、CMP/Lifecycle `-0.1.0-11`、Gradle 9.5、JDK 17 和共享工具 A 固定；四个 commonMain 延续同版 metadata CLI，Native metadata 与 OHOS 双 ABI 延续标准 KGP。metadata 专用补充依赖不进入 Native runtime。

producer 仅向隔离 staging 发布并验收四根和八个 Native leaf；Pages 发布须独立受控完成。独立 consumer 仅通过公开 Pages 根坐标与原 Stately 仓库解析，编译 scope/ViewModel 探针。其他平台继续上游坐标。当前新仓尚未运行云端 producer、公开 consumer、link 或 runtime；历史 x64 HAP 证据不能替代新仓验收，ARM runtime 未验证。

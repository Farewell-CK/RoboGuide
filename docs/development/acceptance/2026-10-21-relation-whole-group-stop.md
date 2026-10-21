# Relation violation whole-Group stop 验收

ADR-0137 复核确认，原安全停止 `admit` 已覆盖同 Group 的所有活跃 attempts；早期把
入口选择误述为单 attempt 覆盖是不准确的。relation violation/Unknown 必须额外保留其
exact target，即使它已本地 Completed。当前通过此入口建立独立 durable stop purpose。原 deadline、
safe-state、worker exit 和 release gate 保持不变；Group 中任意未确认 attempt 都继续持有
ownership。

新增回归覆盖活跃 source/终态 source 两种情况下的 Completed target、原 Group ownership、
Mission hold、原终态不改写，以及全组 stop replay 不续期。合成 Node facts 仅用于单元
合同测试，不建立部署设备事实。实际设备停止、hard-realtime hazard 和异构 regrasp 仍待证据。

已注册原 relation 的同一回归在原 `b4e2544` 产品入口下失败，日志保留于
`/tmp/rg-relation-terminal-valid-counterexample-20261021.log`；修正后应用 271 项通过，
日志 `/tmp/rg-relation-app-tests-20261021-b.log`。完整 workspace Clippy、私有 Rustdoc、
fmt 与严格站点构建通过。此改动不引入持久 schema 或协议变化。

Rust/应用编译回归覆盖现有 integration-server safety-stop 与 relation tests；该项只验证
软件停止协调，不证明设备物理停止、hard-realtime hazard 或异构 regrasp。

# Control tenant quota policy 实际进程验收

日期：2026-10-21。范围是可配置、可审计的 Control tenant/category 逻辑 quota；不创建 Node、设备、能力、资源 inventory 或 workload。

## 行为闭环

- recovery-admin 通过 `POST /v1/resource-quotas` 提交完整 expected-revision policy；请求绑定 stable authenticated principal、command ID、输入 digest、registry digest 和原始回执，并与 Control checkpoint/authority journal 同事务保存。
- 只有 recovery-admin 可改全局 policy；tenant-scoped operator 即使伪造 recovery-admin role header 也不能获得权限。`GET /v1/resource-quotas` v0.2 列出所有当前有 claim 或已配置 limit 的 tenant/category；`max_units` 与 `remaining_units` 无 policy 时明确为 `null`，现有承诺不会被隐藏。
- 同一 command/主体的跨证书轮换重试返回原回执；相同 command ID 的不同内容拒绝；下一 revision 更新成功。旧 reservation demand 未知时不补估单位。
- 重启恢复保留当前 policy、history 与回执。旧 checkpoint 没有 typed `resource_id` 字段时，即使 opaque claim ID 与 ResourceId 文本相同，也不会把它提升成 reservation 证明。真实过程 gate 使用空 Node inventory；checkpoint 的 Node identity registry 为 `null`。

## 独立实际进程证据

`tools/quality/check_resource_quota_processes.py` 启动真实 integration-server binary，以离线生成的 operator/server 软件证书通过 mTLS 发送真实 HTTP。脚本直接从原 SQLite `controller_checkpoint` 和 `control_command_journal` 读取 revision、主体和两条 quota command，再重启同一 binary/DB 验证 GET 与原 command 重试。证书仅代表本地测试 operator 身份，不是 Node 或硬件身份。

执行命令：

```text
uv run --frozen python tools/quality/check_resource_quota_processes.py --output /tmp/rg-control-quota-process-20261021-view-v02
```

结果：PASS。binary SHA-256 `0ad90f773893b1166f0a491b57a40b7f14b08b7c979ee168b882c6c9fdcee9ca`；Controller wrapper `roboguide.controller-checkpoint/v47`；quota view `roboguide.resource-quota-view/v0.2`；policy revision `2`；原 SQLite journal 有 `2` 条 quota commands；稳定 admin principal 为 `quota-admin`；restart 前后 registered Nodes 为 `0`；Node identity registry 为 `null`。本次输出和过程日志：`/tmp/rg-control-quota-process-20261021-view-v02/result.json`、`/tmp/rg-control-quota-process-20261021-view-v02/controller.log`。

同一源码版本的 workspace 回归中，Control 为 166 项（164 pass、2 个显式 ignored）；quota-specific Control 回归、无 policy 用量可见性和空 Node identity/旧 reservation 拒绝回归均通过。Rust workspace 测试、Clippy、Rustdoc、Python ruff/mypy 和 strict MkDocs 也通过。

## 解释边界

该 gate 证明当前软件/SQLite/mTLS 路径和恢复行为，不证明跨主机副本、真实 CPU/GPU/交通成本、可分资源 allocation、OS enforcement、设备连续性、物理安全或硬实时。ResourceId 仍由 Control 独占预留；quota 只按原 Role demand 记逻辑 tenant usage。divisible quota、完整时空承诺、成本模型及 F6 总验收仍开放。

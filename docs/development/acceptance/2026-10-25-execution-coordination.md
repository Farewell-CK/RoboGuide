# 运行协调模块软件验收（2026-10-25）

[ADR-0210](../../decisions/0210-reviewed-execution-coordination.md)与
[运行协调合同](../execution-coordination-v0.1.md)的本地软件组合通过。开发仍只在
`codex/nonrelease-remediation-closure`；未运行设备、模拟器、外部模型或实验。
这不是设备停止、物理 regrasp/交接、跨主机时钟、完整 HA 或 SoftwareComplete 证明。

最终维护门禁：`/tmp/rgd59-5/result.json`，3/3 独立案例通过，原实例重启与完整声明数据
backup/verify/新 root restore/原命令 retry 均通过；恢复 CLI 启动服务数为 0。

| 案例 | 原实际证据与结果 |
| --- | --- |
| 运行中抢占 | 1 个实际软件 Node，3 个实际 CPU 子计算；原源 Cancel 回执到达时 parent/child 仍活着，提前 Commit 拒绝。原 parent 真正回收 child 后退出 -15；同事务正常 Control 先给受益 Mission 原冲突资源，再恢复原 Mission。独立文件 verifier 与独立原目标通过 |
| 耦合恢复 | 2 个共址实际 Node 服务共享一个 Local EAIOS；原 parent 真正拥有 3 个 CPU children，其中已完成 prefix 只读第一个文件，两个耦合 Role 读取另一个文件。新 Group 只启动 2 个新 children，未重复 prefix；requires-active 真实状态、原完整 Group wait、独立 Review/原目标均通过 |
| 持续 hazard | 实际文件哈希改变形成原 Node 的 Observed State；原 gate 触发整组停止、撤权与 hold。Cancel receipt 到达时原 CPU 仍活着，资源未释放；Controller 原实例重启保留原 watch、Unknown/hold 与 deadline，没有重复工作 |
| 设备证据拒绝 | 软件 assurance 的 regrasp 请求被实际管理 HTTP 入口拒绝；设备类协调另要求原 reviewed Memory/context。没有为了验收而登记设备、伪造设备 observations 或执行物理动作 |
| 冷恢复与旧 writer | 三个案例分别独立比较完整 SQL、原协调/Review/beneficiary admission、hazard、Node journal、CAS 与实际 CPU 文件。genuine v58 程序拒绝 v59，原 SQL authority 行不变；新 root 不启动服务、不续预算 |
| 原实例恢复 | 已完成抢占与耦合 Mission 的原 Controller 重新启动，完整 runtime revision 权威原样保留；原 CPU invocations 未增加。hazard 的独立 Unknown 重启保留原未确认结果 |

实际 Controller SHA-256：
`caf8d4d7d748c3154671bc6a5b0d2ed14f4a6920cd5393cc7cbc7b4783bd70e4`。
实际 Node SHA-256：
`d4036e5e42607dfa7aa68d9b8027ae8b3621dcd48dec0229f14930e2fdd8c30e`。
genuine v58 Controller SHA-256：
`36634ce81808f8977da93f8df1a9705b726142d42ad4b72b1e9a0e4d0904c197`。

抢占 manifest：`sha256:de3da7d1c572802b63e097b4ac73b8893fe1e318b8fc20fdd0899bb9e256833c`。
耦合 manifest：`sha256:c98783eded32ba75e0325335f350b863ee2c54d242753c188773908676629fa3`。
hazard manifest：`sha256:c3784244c2f941d6eb8a64316aa4a3ab077663337a6a0dec50d2ef06b5f9f96c`。

Rust 最终其余 workspace 1028 项通过、2 项忽略，Replica 168 项在前一完整批次通过，
共 1196 项通过。恢复工具 265 项通过；all-targets/all-features Clippy 通过。
原整批唯一失败来自未启用 hazard 时多读一次时钟；已恢复未配置路径的原时钟读取行为，
最终原投递期限回归通过。私有 Rust 文档以 warnings denied 构建通过。
附随的 v59 实际资源日历与全部十个 Artifact SQL/GC 故障案例通过；日历冷恢复 manifest
为 `sha256:305bc780e7b3e9124e3c5fbeea6476297d04b925f19ce9ee971ad70a48e99a26`，
原 v58 writer 在恢复数据上实际拒绝。严格类型检查 399 个源文件通过，165 个 Python 文件
格式与 Ruff、函数文档检查通过；站点另固定到本模块功能提交后核对。

失败历史单独保留：`/tmp/rgd59-preempt-1` 和 `/tmp/rgd59-2` 的实际抢占流程通过，
冷门禁分别正确拒绝源目录内的 bundle、发现新 profile 在 content 集合中的遗漏；
它们不计为通过。`/tmp/rgd59-3` 完整三案例通过；进一步的 `/tmp/rgd59-4` 原实例恢复
暴露通用回执 validator 未接纳 v0.6，已补齐后以最终第五轮验证。没有修改旧结果或重标旧 schema。
第五轮重启过程中保留了一次 provider 只读 GET 回复的 BrokenPipe；未新增执行，原
native wait、已提交权威与恢复比较通过。这段短观察不替代长期网络/SLO 验收。

复现：

```text
uv run python tools/quality/check_execution_coordination_processes.py --output <fresh-root> --previous-controller <genuine-v58-controller>
```

Node/Plan/Memory/事件合同保持原义；新权威为 Controller v59 / recovery v0.28。
B 的 typed Memory/自动 MI 证据继续保留，完整全局/HA/跨域组合仍由 A1/A2/E/F 完成。
软件验收不删除任何原非发布完成条件，硬件证据保持独立待验证。

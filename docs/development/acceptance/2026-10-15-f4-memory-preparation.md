# F4 执行前 Memory 准备切片

状态：当前切片软件验收通过；F4 整体仍在实施。未接设备、未跑实验、模型或模拟器。
实现决定见 [ADR-0105](../../decisions/0105-memory-reconciliation-authorization-and-spatial-provider.md)。
合同见 [Memory preparation](../../../contracts/memory/preparation-v0.1/README.md) 与
[Node catalog v0.12](../../../contracts/node/v0.12/README.md)。

## 当前调用链

MissionPlan v0.9 Role 只声明原 selector、kind、payload schema 和 media。Control 在实际
binding 后选择同 operation LocalSystem 的已注册 provider，复查原始/当前内容策略、实际
Task/Role/attempt/resources、source session 与成员版本。模型不能选择 receiver 或路径。

Application 在 checkpoint/authority journal 中冻结原准备上下文，再发送 Prepare。Node journal
持久原配置与 boot/monotonic 预算后返回 ACK；此后只允许原输入 import。Unknown 只能查询
原 provider，不能换 ID、provider 或 attempt 重做。查询有原期限、次数、发送数与退避。

导入 Completed 后，原 backend 重新校验当前完整 bytes、manifest/configuration digest 与实际
instance。Probe 每个 ID 只读一次，重复回执不续时；Controller 的新鲜度从首次 issue 起算。
整组 pending prerequisite 通过后才发送带精确准备上下文的 Execute；Node 再读当前内容并在
Local How 前复查。执行成功仍须独立 verifier 才满足 Task。

Revoke 先写 fence 再 ACK，先到也留 tombstone。下载前及每个有界写块都核对接收侧原预算与
撤销；CAS 服务同时按当前内容权限逐块检查。精确原 import 的 manifest/副本历史 GET 使用
其 Read 权限，不授予全目录发现。Controller restart 先持久撤销未结束的准备授权，保留
原始时限、导入与回执。当前 120 秒为准备/导入/Execute 接纳窗口，不是运行中可续约 Group 租约。

## 实际软件进程证据

当前记录：`/tmp/rg-memory-prepare-20261015-c/result.json`，`passed=true`。
同目录保留代码/interpreter/config identity、完整 Mission、原 source manifest、两份计算结果、
独立验证、准备与 import 历史、10 个自有进程的退出记录。两软件 Node 在同一主机，均由实际
Node Service 注册，UUID 每次生成；这不建立两个物理设备或独立硬件故障域。

| 实际操作 | 结果 |
| --- | --- |
| 原文件 | `core/domain/src/memory.rs` 当次内容，26204 bytes，SHA-256 `967d3c9ddb39478dddaf580c485c8b8c58b29cf2e251d832890e5ec0057f825f` |
| 传输与身份 | 实际 mTLS、两个独立证书、公开 enrollment API、正式 Node Protocol |
| 原 source 与 consumer 工作 | 各一次真实 CPU 文件哈希，分别由独立进程重读原文件/导入 bytes 校验 |
| Group 未获内容授权 | consumer 后端 jobs 为 0，Execute 保持 fenced |
| 导入最终 SQL 失败 | 实际 fsync/copy 后的 UPDATE 被 trigger 中断，backend 原行仍 `pending` |
| 原 provider 对账 | 同一 import ID 的一次只读 query 证明实际文件效果；无第二次复制 |
| 当前可用性 | Prepare、Available、Revoke 为独立原始回执，完整 bytes/instance/config/source 仍相符 |
| 重启 | 实际重启 Controller、两个 Node 和两个 backend；新注册 receive time/可达性恢复，backend UUID、原 receiver budget 与完成行不变 |
| 清理 | 10 个本次自有进程均已 reap；未操作其它服务 |

当前 Controller SHA-256 为 `bfd5ea1577fb237ca37932ff4a40c3f65899e5fd5016d0136077d0f94059b740`；
Node 为 `b63e8aad44e68c2ebfc42efa629eedffa1a3b09a1f549fcb2039f1ee08bdff76`。
实际 interpreter 是 Python 3.13.5；其 binary digest、OpenSSL/SQLite 版本及六份部署配置
digest 均在结果中。声明不扩展为全部动态库闭包证明。

```sh
cargo build --locked -p integration-server -p roboguide-node
uv run python tools/quality/check_memory_preparation_processes.py \
  --output /tmp/rg-memory-prepare-20261015-c
uv run python tools/quality/check_memory_preparation_upgrade.py \
  --directory /tmp/rg-memory-prepare-20261015-c \
  --old-node /tmp/roboguide-node-f4-policy-journal-v8-reference \
  --old-controller /tmp/roboguide-controller-f4-wrapper-v34-reference
uv run python tools/quality/check_memory_preparation_schemas.py \
  --directory /tmp/rg-memory-prepare-20261015-c
```

首次运行使用尚不存在的输出路径，原记录不覆盖。旧 binary 为上一已验证切片冻结的真实
文件。`upgrade-gates.json` 保留 source/consumer journal v9 对旧 v8 binary 的双重拒绝，
包括回退 user_version 后仍由持久 marker 拒绝；旧 Controller v34 拒绝 wrapper v35，
包括零 Node/零事件的策略快照。各实际 authority table 的 row digest 保持不变。

跨语言校验使用固定 jsonschema 4.26.0、纯本地引用 registry，验证真实计划、两个 TOML、
准备、命令、回执与 availability。13 类负例包含 Node/provider/path/inventory 注入、错误
probe generation、probe 引入新 context、过期/缺配置/缺输入/负 availability 和有副作用的
availability workflow。Rust 仍额外核对完整 metadata digest、精确 cross-field authority 和时限。

## 验证与边界

完整 Rust workspace：810 passed、2 个既有扩展测试 ignored；单独扩充的 vendor-neutral
边界检查通过。全 workspace all-target/all-feature Clippy `-D warnings`、private Rustdoc
`-D warnings` 与 fmt 通过。完整 Python 离线测试 2501 passed；变更模块/工具的 Ruff、函数
文档和 17 个相关 Python 文件 strict mypy 通过。全仓 typing 总验收和两项扩展测试仍由 F10 关闭。

初次实际记录 `...-a` 暴露原导入的 manifest GET 错用 Discover，已修为原 Read 权限；其
失败日志保留。`...-b` 验证修复，`...-c` 增加真实 Node/backend 重启与配置/interpreter pin。
回归还发现空 Memory outbox 多读 clock 影响原恢复 deadline 检查，已修复并重新通过全 Rust。

当前正向输入为通用实际文件；完整 raw typed-map 消费仍须独立业务验收。完整 Group lifetime、
handoff/parent lineage、active-map、GC/全部引用闭环仍待实施，F5–F10 同样必需。后端目前为
协作式 CPU 时间/worker 上限，F6 仍须完成实际 OS quota enforcement。软件回执和文件结果
不证明机器人已停、硬件身份、硬实时、定位或物理连续性；这些保持 HardwareEvidencePending。

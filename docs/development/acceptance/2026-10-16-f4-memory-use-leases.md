# F4 当前 Memory 使用许可切片

本切片接通 Controller→正式 Node Protocol→Node journal→真实 Local Memory backend 的
当前使用租约。F4 仍进行中；active-map、Group handoff、GC 与 F5–F10 均未关闭。
所有记录为同主机实际软件进程与文件证据：0 设备、0 模型、0 仿真器，未运行实验或 E6。

原 preparation/import/首次 Execute 的不可续期预算保持 120 秒；大文件路径通过分块、断点进度和不重复哈希完成。新使用命令绑定完整原 context、
tenant、Task/Role/attempt、Node/session、配置、provider/backend、内容策略及成员版本，
最多 30 秒，generation 连续且重复命令不续期。当前与最近三代记录保留，水位拒绝更旧重放。
撤销不可逆，重启先持久 fence；所有所选后端确认最新 generation 前，整组 pending Execute
不能放行。续约等待期间的上一代有效许可仅维持已有执行，不能授权新 Execute。
原副本 Imported、当前 bytes Probe、使用许可、local completion、独立 Task 满足分别归约。

Control 的初始 Import+Read 和持续 Read 分开。命令先随应用检查点提交再发送；接纳回执
再次核对当前授权及原实例/配置。Node 的原预算包括真实 boot 与 time namespace；后端使用
同 Linux kernel/namespace、同用户私有 Unix socket，在每个有界读取/计算阶段检查许可。
后端启动 fence 旧许可并保留原回执和预算。并不比较跨主机时钟。

版本为 Node Contract v0.10、config v0.11、catalog v0.13、preparation v0.2、Node journal v10、
Controller wrapper v37。旧 preparation v0.1 的 schema 不变，缺失 use authority 不从新注册补齐。
实际 byte 上传、原 import、源码和配置 digest 均保留；不换 import/attempt ID 绕过 Unknown。

参考文件后端先持久接纳 invocation 和原计算预算，再返回 handle，由真实后台 worker 读取、
哈希并产生结果。默认计算上限仍为 5 秒、256 轮；显式部署可分别设置上限。重放读取原结果，
不重新启动 worker。合作式预算不构成 OS quota、硬实时或设备 safe-state。

实际发现并修正三处问题：Persisted 后仍重发而过早耗尽次数；发现同一完整 manifest 时重复
上传全部 bytes；新协议 Cancel 先于 Execute 时构造终止 snapshot。现分别等待原完成、
校验 bytes 后避免重复上传、只回 durable receipt。原接纳期限和独立停止证明要求不变。

| 实际路径 | 产物 | 结果 |
| --- | --- | --- |
| 当前授权→实际计算→连续续约→独立验证→重启 | `/tmp/rg-memory-use-head-race-20261016-a/result.json` | 最终二进制通过，最高 generation 5，保留 4 代 grant 回执；原预算不变 |
| 计算中撤除 Read | `/tmp/rg-memory-use-revoke-20261015-b/result.json` | 通过，真实 worker Cancelled，无结果；全部 provider 撤销确认 |
| 杀掉 Controller，等待原租约到期 | `/tmp/rg-memory-use-expire-20261015-a/result.json` | 通过，由实际许可到期退出；未续期、未重算 |
| SIGKILL 后端再启动 | `/tmp/rg-memory-use-backend-crash-20261015-a/result.json` | 通过，原计算 Unknown，原预算/回执保留；不重执行 |
| SIGKILL Controller 再启动 | `/tmp/rg-memory-use-controller-crash-20261015-a/result.json` | 通过，恢复 fence 与撤销确认；真实 worker 退出 |
| native 点云生成→第二软件 Node 导入并计算 | `/tmp/rg-spatial-use-20261015-b/result.json` | 通过，两个独立 verifier；重启无第二 native worker |

每个产物含实际 binary/module/config identity，`owned-process-exits.json` 均确认本次自有进程
全部 reap。独立 verifier 重读实际源文件/CAS/导入 bytes；本地 Completed 不替代语义满足。
普通文件检查还注入真正的导入完成 SQL 失败，原 provider 只读对账，不再复制或换 ID。

2026-10-18 补充了同一 readonly query 的单调恢复规则：无结果的 `Unknown` 可在原
provider instance、identity digest 和 contract digest 完全一致时晋升为确定的 `Completed` /
`Rejected`；确定结果仍不可改写，provider 身份变化仍拒绝。此前大文件失败记录中，Controller
已保存空结果 `Unknown`，同一 Node journal 则保存了原 provider 的确定结果，重试因此被拒绝。
新增 `original_provider_query_resolves_only_current_authorized_import_and_fences_old_wrapper`
回归覆盖 Unknown→Completed、确定结果重复幂等及 Completed→Rejected 拒绝；Integration Server
全套 220 项、Clippy、fmt 通过。标准大小实际进程检查
`/tmp/rg-memory-prep-20261018-a/result.json` 通过真实 SQL 故障注入、两 Node/CAS、原 provider
对账、独立复算与重启，`devices/models/simulators=0`。

历史大文件失败记录 `/tmp/rg-memory-large-final-20261018-a/failure.json` 保留为
`TEST GAP`：旧实现的 90 秒外层等待在源 Node→CAS 发布阶段结束，565,360,640 字节只传到
137,609,216 字节。该记录不与修复后的二进制混报。

修复后的大文件闭环 `/tmp/rg-memory-large-final-20261018-q/result.json` 已通过：
569,559,040 字节真实源文件在原 120 秒准备窗口内完成分块上传、CAS 发布、第二 Node 导入、原始 provider 复核、
重启和撤销；`actual_node_restarts=2`、`actual_backend_restarts=2`、
`actual_import_completion_sql_failure=true`、`readonly_reconciliations=1`、
`independent_verifiers=2`、`original_receiver_budget_unchanged=true`，且
`devices/models/simulators=0`。同一 upload identity 会返回
`received_bytes` 并从已确认前缀继续；CAS/fsync 通过阻塞边界执行且不占用 Tokio worker，发布复用已持久化的完成证明，
不会再次哈希整份大文件。原始 query identity 只发送一次，后续 generation 才能在窗口后对账；
撤销允许有界重试并保持不可逆。

`rg-spatial-use-20261015-b/schema-validation.json` 验证实际 v0.2 context、两种 Node 配置、
完整 raw map、使用命令/回执及非法变体。`upgrade-gates-final.json` 记录 6 项实际旧二进制拒绝：
source 和已有 use head/commands/effects 的 consumer 均拒绝旧 Node v9，回退 header 后仍拒绝
durable marker；旧 Controller v36 拒绝 v37（含 0 Node / 0 event）。原 authority 行 digest 均不变。

2026-10-19 增加了应用层的 provider-complete release gate。撤销命令提交后，
`safety_stop::blocks_release` 还会检查原 preparation 的 provider 集合；只有同一 revoke
command 收到 `revoked` receipt、provider ID 集合精确相等且没有重复项，Mission/Group 才能
继续 terminal release。缺 provider、重复 provider、foreign provider、Unknown 或尚未收到
回执都会 fail-closed。Node engine 仍先逐 provider 校验原始 backend response，Node journal
仍校验每个 provider effect 的 durable receipt；该门禁只是把这组证据真正接入 release，
不把聚合状态单独当作整组撤销完成。它不等价于多 Node 动态 Group 授权、物理 stop、handoff
或硬实时安全证明。

全工作区 Rust 检查在本次最新代并发修正前通过 824 项、忽略 2 项；Clippy、private Rustdoc、
fmt 与 Python 全量离线检查通过。Python 变更路径 Ruff、strict mypy（17 文件）和函数文档检查
通过；全量类型检查的既有问题仍归 F10，不由该局部检查声明通过。最新代并发修正的 4 项
应用回归、fmt、Clippy、private Rustdoc 均通过，最终二进制续约/重启进程验证通过。
质量日志分别保留在 `/tmp/roboguide-f4-memory-use-*-head-race-20261016.log` 与
`/tmp/roboguide-f4-memory-use-head-race-test-20261016.log`；修改前的全量日志保留为
`/tmp/roboguide-f4-memory-use-*-final-20261016.log`，两者不混为同一 binary identity。

失败原记录全部保留：普通文件 `rg-memory-use-20261015-a/c` 分别暴露检查器未等撤销回执和
后端 preparation 版本接线问题；长任务 `renew-a/b/c/d` 暴露大文件超时、重发和写锁/新鲜度
问题；`renew-e` 观察到 12 代续约，最终触及实际计算预算，未报告成功。它们是整改前的
历史证据，不代表当前修复后二进制的结果。

本轮已关闭 Integration Server 重启后的 staging 恢复：启动先重放 upload event，再恢复
有权威 Started/Prepared 记录的 partial 文件并重建增量 SHA-256；Prepared 已完成 CAS 的
记录可直接补写 Completed；没有 durable 记录的 partial 文件会被清理。回归覆盖半截 Started
跨重启续传，以及 SQL 完成提交失败后 Prepared/CAS 跨重启对账。active-map 选择、Group
handoff、引用感知 GC、物理停止/硬件连续性以及 F5–F10 仍需独立实现和验收。

这些检查不提供当前设备定位、物理停止、硬件连续性、跨主机时钟同步或完整 F4 完成证据。

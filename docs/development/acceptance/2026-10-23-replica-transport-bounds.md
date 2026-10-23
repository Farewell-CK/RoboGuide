# 2026-10-23 副本 TCP/TLS 容量验收

实施分支 `codex/nonrelease-remediation-closure`，前置 `7221fb1`，适用
[ADR-0163](../../decisions/0163-bounded-replica-transport-admission.md)与
[部署/失败语义](../replica-transport-bounds-v0.1.md)。所有文件、命令、真实配置及退出结果
保留于 `/data/workspace/roboguide-evidence/nonrelease-closure/p7/`。

## 行为与实际进程

旧版本 `legacy-replica-transport-probe-7221fb1` 的 SHA-256 为
`b8489a33a275f107b1ac9d6038ef585377b3066c8ed7f0d618ec723680ba8bd3`。
它只组合原库 listener，没有注入 unbounded 模式。`replica-transport-native-verified/legacy`
使用真实 mTLS、三个 infrastructure members，目标实际 FD 从 15 增至 206，192 个真实
客户端同时等待 TLS。三个原进程的实际 wait 均为 -15。最早 raw loopback 基线 01 把 FD
增量写成必须精确 192，遇到短暂 Raft 连接 turnover 判失败；原记录保留，不计为通过。
当前基线使用至少 180 个额外实际 FD，仍明显超过默认 128，且未依赖一个精确瞬时差值。

正式新探针 `replica-transport-probe-adr0163-final` 的 SHA-256 为
`c62f28764a6fe06b1d48de12b44f6a7e78d87aa7a572233d278852949f17e367`。
`replica-transport-native-verified/result.json` 为正式通过结果；实际四个进程 incarnation
均 wait -15，包含原 SQL 重启。每个进程使用一个 async worker，入站/出站容量 16/8，
small/large pool 为 1/8 MiB。范围是三台本机 infrastructure processes，未创建
Controller、RoboGuide Node、设备、模型或模拟器。

| 实际触发 | 核对结果 |
| --- | --- |
| 48 个真实未完成 TLS 连接 | 同时持有 16 个 permit，真实拒绝 32 个；客户端关闭后回收 |
| 两个真实 TLS 连接只发各 4 MiB 长度头 | large pool 实际占满 8 MiB；下一 large frame 分配前拒绝 |
| large pool 满时发送 5 次小帧 Fence | 均完成实际多数派检查；该短窗口约 17–27 ms，不作为 SLO |
| 长度 0、超过原 256 MiB、非法 JSON | 拒绝，原容量释放；wire cap 不变 |
| 小帧只发头后 EOF | 原 32 KiB 许可释放 |
| 错 profile、证书与 claimed sender 不符 | 原权限检查拒绝 |
| 真实 TLS 慢连接等待十五秒 | 原 deadline 触发实际 timeout 计数，permit 回收 |
| 两个 pending large frames 与未完成 TLS 时 drop listener | 实际 member 仍运行，所有 inbound/large permits 回收 |
| 原 SQL 重启 | 再次完成实际 quorum Fence；没有重建应用/Node 或操作历史 |

首轮 `replica-transport-native-01` 也通过。随后 `replica-transport-native-final` 的网络
检查全部完成，但并行 Cargo 构建更新了其探针文件，原二进制指纹检查拒绝发布通过；
完整失败记录保留。正式 verified 使用独立冻结探针并重跑全部旧/新实际进程检查。
CI 使用顺序 build/gate，不依赖本地冻结旧文件，缺旧 binary 明确 unavailable。

## 代码与验证

`transport_budget.rs` 管理原始编码长度与实际 semaphore；入站在 TLS/task 前准入，
Node 所有异步网络客户端、内部证书/transfer 查询共享原 budget；独立 admin client
有自己的默认有界容量。listener 通过 JoinSet 持有实际 handler，取消网络等待不否认
已提交的 Commit/transfer，独立后台 worker 的原生命周期保持原义。

frame 编码在分配前有界计数、占用确切容量，第二遍也无法越过原长度。流式认证有原
JSON/摘要兼容检查。资源测试覆盖实际 permit 生命周期、small reserve、容量用尽前拒绝、
编码超限/serializer 增长和配置边界；不把这些单元测试当作设备或部署事实。

全库 Rust 1028 通过、2 忽略；fmt、全 workspace Clippy/all-targets/all-features、
私有 Rustdoc 通过。严格 mypy 原维护组合 355 files 通过，新增 checker Ruff/format/函数
文档通过；网站 9 tests 与 strict MkDocs 的中文/英文构建通过。只运行 stdlib/file/TLS
软件检查，没有开展实验、模型推断或仿真。

`7c3219a` 的远端 Documentation 成功、CI 失败；[原 Rust job annotation](https://github.com/Farewell-CK/RoboGuide/actions/runs/39882422452/job/120182672131)
显示失败步骤为 `Verify original child lineage through stopped Group handoff`。多次获取
完整 job/log 遇到 GitHub API TLS timeout，步骤内部原因尚未取回；相同实际 gate 已重新
启动核对。`7221fb1` 的 Documentation 成功，
不能据此写为该提交的完整 CI 成功。后续继续获取原日志、核对当前分支的实际 CI。

## 保留门禁

本地容量不是 RSS/OS 强制配额，也没有解决所有 CPU/同步 I/O、listener DoS、跨主机
SLO 或 online HA。同步 child fence 仍有自己的 writer/socket/frame 约束，本包没有
把它加入 member 异步池。supervisor 原 SQL inode/materialization、旧 child journal
迁移、同一 current/joint voters 的实际 bytes+metadata ACK、Node/Mission failover、
完整 P1–P7 和固定 deployment matrix 继续必需，尚未宣告 SoftwareComplete。

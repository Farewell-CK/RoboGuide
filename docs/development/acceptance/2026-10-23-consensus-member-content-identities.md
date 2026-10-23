# 2026-10-23 共识成员内容身份与旧编码兼容验收

唯一分支 `codex/nonrelease-remediation-closure`，前置 `67d9be0`。
[ADR-0167](../../decisions/0167-consensus-bound-content-member-identities.md)先固定 owner
与 membership 的绑定语义，再替换 upstream address-only member 类型。证据根目录
为 `/data/workspace/roboguide-evidence/nonrelease-closure/p7/`。

## 行为

`ReplicaMember` 保留原 `addr`；缺 `content_store_owner` 的原记录仍缺内容绑定，序列化
不增加 null/默认新字段。完整 content target 要求每个 voter 在确切 committed
membership 中具有完整 endpoint/owner，且与 target 的 member→store map 一致。
current/joint 两组仍分别要求同一批完整 metadata+bytes members 的严格多数。

旧 profile 和现有 direct storage v4/v5/v6 拒绝含新 owner 的 log、apply、Snapshot
和启动 membership；合法旧缺省编码继续支持。新增 DTO、纯数据构造器和声明的 owner
都不产生真实 byte ACK。生产 v0.4 尚未启用，实际传输/接线仍须完成。

## 实际软件证据

`content-member-mixed-binaries-01/result.json` 通过。三个真实 mTLS infrastructure
members 先运行冻结旧程序，再逐个切换新程序，最后逐个退回旧程序。**9 次实际进程
wait 均为 -15**，六个滚动步骤每次均有实际剩余两成员 quorum 及重启后的 quorum。
三个原 SQL inode 分别保持 `56694758 / 56694764 / 56694770`；每次的原 identity 和
membership raw rows 逐字节相同，实际 endpoints 与原声明相同，未出现新隐藏字段。
每次都重新打开原 SQLite，未复制或替换库文件。

旧程序 `replica-transport-probe-adr0163-final` 为
`c62f28764a6fe06b1d48de12b44f6a7e78d87aa7a572233d278852949f17e367`；
新 `replica-transport-probe-adr0167` 为
`37c2c2071ce567eba50519b21748fe9b483d6e2fc281dc97159431d9ea7483cb`。
这是原 address-only profile 的兼容/回退，不是完整新内容 profile 或应用 failover。
CI 缺历史 binary 时明确 `historical_binary_available=false`，仅检查当前 profile 重启。

全冻结的 `content-member-archive-frozen/result.json` 与其 Snapshot、original producer、
parent adoption、complete State 子结果均通过。实际旧 Snapshot 程序产生原 v4/Root2，
新程序保留旧 member/Snapshot digest，通过原 SQL/CAS/reader/process-end 与 49 步
parent 接管故障组合，再完成原 Archive 的显式 cold gate。

该次冻结 `content-snapshot-probe-adr0167` 为
`32c42d2bb033ecbb11734dba03546dadef051d4f2e38f8ba409f2f38ac03ba0b`，
`controller-replica-member-adr0167` 为
`93051040d8c2e757909ae8fe23e26124dc88bdc051c99c31abf331ca722a2161`，
`content-store-probe-adr0167` 为
`3389d3bb9e76894d639862fa7855f2514a80fcb465da1bc79432a77c5c88f059`。
Controller、State 与 projection 也使用各自冻结文件；完整 source/binary identities
保留于原证据。checker 增加显式 `--controller-binary` / `--replica-content-probe`，
不改变原指纹、源匹配或成功条件。

早先 `content-member-archive-01` 把支持 v6 的 ADR-0166 探针误用于“旧 v5 必须拒绝”
用例，按原断言失败；后续使用实际 ADR-0165 v5 binary。`content-member-archive-verified`
则因并行 Cargo 改写未冻结的默认 content probe 而拒绝发布通过。两份失败均保留且不计
通过；最后 frozen 运行显式锁定所有实际二进制。没有修改拒绝条件或忽略指纹变化。

`67d9be0` 的 [远端 CI](https://github.com/Farewell-CK/RoboGuide/actions/runs/39908819760)
在 parent 的 committed-killed 注入窗口失败，Documentation 成功。原 Rust job log
保留为 `p7/ci-67d9be0-rust.log`。原 checker 持有 parent 写锁、轮询 producer intents，
在较慢 runner 上可能错过 100 ms native busy 窗口而先退出；不属于产品假成功。
现在只在 owned fixture 的实际 CAS 文件上持有 Linux write lease，以实际 SIGIO
证明读打开被阻断，核对全部原 intents/原 v4 后取得实际 SIGSTOP wait。原 mode 在启动
产品进程前恢复，真实阻塞至少 250 ms；再持有 root SQL 写锁、解除 file lease，让真实
hash/parent v6 Commit 通过，原回执仍被 SQL 锁阻断时取得原 SIGKILL wait。没有增加产品
预算、测试模式或放宽 receipt/Unknown 条件。CI 也保留 runner.temp 的原失败结果和请求。

`content-member-parent-kernel-barrier-01/result.json` 的 49 步通过；完整重跑的
`content-member-archive-kernel-barrier/result.json` 及全部子 gate 通过。后者实际 SIGIO
为 29、原 stop wait 信号为 19、阻塞约 0.254975 秒，原只读 mode 0400 保持。之后原
wait -9，原 parent/producer 的 Unknown 均保留。早先远端失败继续保留，不计为通过。

## 代码验证与限制

新增旧 Node/membership/SnapshotMeta JSON 的逐字节比较、无绑定/错 owner/独立 map
冲突、实际 legacy SQL 的 log/apply/Snapshot 无效果拒绝。全库 Rust **1045 passed、
2 ignored**，全 workspace Clippy/all-targets/all-features、私有 Rustdoc、fmt 通过；
维护 Python 质量目录 Ruff/format/函数文档与组合 strict mypy **358 files** 通过。
网站 9 tests 与 strict MkDocs 中文/英文构建通过。

全部检查为实际基础设施软件或明确标注的 DTO 拒绝测试，0 embodied Nodes、0 devices、
0 models、0 simulators。它不证明新内容网络 ACK、Root3/v6 的完整冷恢复、旧生产 v3
升级、Node/Mission 路由、物理连续性或长期 SLO。其余 N1–N7 继续实施，不据此宣布
SoftwareComplete。

## 后续 CI 原端口冲突

`f632efb` 的 CI 39914437998 在 Catalog 文件组合的 Controller startup 失败；
原 artifact 12484686351 的 `controller.log` 明确报告 Operator listener
`Address already in use (os error 98)`，并非新的内容流或 parent 门禁通过。原日志、
ZIP 和解包文件保留于 `p7/ci-f632efb-*`。

维护 checker 的 `address()` 先读实际 Linux `ip_local_port_range`，在其外随机选择
实际可绑定的非特权 loopback 端口，并在同一检查进程内锁定不可重选集合。这样其后
真实健康请求的内核临时 source port 不会占用预选 listener。仍不承诺排除外部服务
之后抢占的竞态；产品 bind 失败继续如实失败。Controller 提前退出现在立即报告
实际 returncode 和原日志路径，不重启子进程，不重试 mutation 或放宽启动期限。

`p7/ci-f632efb-endpoints-01.json` 四线程选择的 256 个端口均在实际范围
32768–60999 外，独立同时 bind 全部成功。原相同两软件 Node、单 async worker 的
`/tmp/rg-ci-endpoints-20261023-01/result.json` 通过：真实两个 CPU hash、一个 import、
两个独立 verifier、原 SQL 完成故障/readonly 对账/重启/精确 Catalog 退休保持。
完整 strict mypy 358 files 与两维护模块 Ruff/format/函数文档通过。

首轮本地长目录 `ci-f632efb-catalog-non-ephemeral-01` 因实际 Unix socket path too
long 失败，原日志保留且不计为通过；正式重跑采用原短路径规范。两软件 Nodes 是明确
配置的真实文件进程，设备/外部模型/模拟器为零。本项只修检查器端口选择和失败报告。

# 私有接收 Archive 原退休与 GC 验收

唯一开发分支仍为 `codex/nonrelease-remediation-closure`，接续 `6c5a099`，实现
[ADR-0155](../../decisions/0155-private-archive-recipient-retirement-and-reclamation.md)
及显式 [recovery v0.14](../../../contracts/recovery/bundle-v0.14/README.md)。
此项只处理完整声明的私有接收 owner，不把源 Artifact 全引用 GC 或 N1–N7 宣布完成。

## 实际 authority

原 OS administrator 使用 CLI `retire-archive` / `gc-archive`，显式指定完整 v0.14
profile、原 Controller component、原 copy ID/digest、原命令 ID 和保留原因。
所有实际原 Controller、source CAS、Archive writers 先持有，再取得实际接收 CAS
writer；独立再次打开无法获得这些租约，文件 descriptor 的 device/inode 与当时
PID/start ticks 进入原 intent。当前内核/时间 namespace/UID 与非续期 budget 也
固化；这只是软件与 OS 管理边界，不能冒充远程用户认证或物理身份。

原源 compact receipt 必须与完整原 publication 一致，原 events/Catalog/checkpoint
和 receiving bytes 均校验。所有 source 已知 Controller consumers 必须声明；目标
必须有空的完整 consumer inventory、唯一原 Archive reference、无其它 Snapshot/
Archive producer 或 staging。已知 consumer、其它 producer、pending、缺原内容
或缺原表均拒绝；不接受调用者写一个“无引用”布尔值。

retire 先持久原 tombstone，再逐个密封同一 owner/header/fence/inventory 为终态
storage v4。旧 v3 binary/CLI 因实际格式拒绝，不能重开或重新登记 consumer；v4
不是普通可写 CAS。retire 不删除内容。独立 GC intent/budget/current writer proof
先于 unlink，完整原 seal/receipt/tombstone 均必须存在。原命令重试只观察，原
Unknown 不重新执行；新的显式 GC 可处理仍存在的原 bytes，独立记录其实际 unlink
和先前已缺失观察，不能倒推先前进程完成。原 intent/head/receipt 历史保留。

recovery v0.14/bundle v0.4 核对完整终态元数据与原 source history；只有已完整
退休的接收 history 不要求已删 bytes。缺原 tombstone、GC head/known command、
owner seal 或 mandatory 目录仍拒绝。restore receipt v0.14 不启动服务，不续任何
原 copy/cleanup/Memory/GC budget。普通 source Artifact storage v3、wrapper v50、
Node 和 Local Memory 合同保持各自原含义。

## 原实际证据

证据根为 `/data/workspace/roboguide-evidence/nonrelease-closure/p1/`。

| 记录 | 实际结果 |
| --- | --- |
| `arc-gc-03` | 新零 Node producer 先产生真实 SQL/CAS/完整 immutable bundle；活跃接收 writer 拒绝退休；retire 后两份 bytes 保留，GC 实际 unlink 两份；原命令 retry unlink 为零 |
| `arc-gc-fault` | 实际 create-new 文件故障发生在原 GC intent 持久之后；旧 ID 查询保持 Unknown，另一个新 GC 对账完整 head 后处理原 bytes |
| `arc-gc-partial` | 实际目录权限让后续 unlink 失败，前一份已缺失但无 receipt；新 GC 仅删剩余文件，原失败命令保持 Unknown |
| `arc-gc-final` | 当前完整实际 gate 同时覆盖上述两种故障、独立当前 writer/process proof、密封旧 binary 拒绝、CLI backup/verify/restore/幂等 retry，以及删除原 tombstone 后重新 pin manifest 仍拒绝 |

`arc-gc-final` 实际新 GC receipt 只记录 **1** 次 unlink；先前失败进程之后独立
观察到 **1** 份原文件已缺失，未伪造该进程的完成回执。独立 GC intent 保存当时
两个原输入的 present/inode 与新 budget，原退休 tombstone 始终保持原字节。
旧命令重复 unlink 数均为 **0**。旧 v3 reader Controller 的实际退出码为 **1**。

所有实际门禁的 Controller binary SHA-256 为
`1c36eb8b167e5439593cb8152cfbc9a5299e725e8b2c0443bca367ff3eeef49a`，
shared wrapper 为 v50。实际源码、argv、stdout/stderr、原 SQL/CAS 和命令历史均在
各自目录；新 producer 的原完整 pinned bundle 保留。没有修改此前独立验收目录。
Node、设备、外部模型和模拟器均为 **0**。

`arc-gc-02` 实际旧 binary 正确拒绝终态 owner，但检查器未接纳旧层返回的
`original maintenance journal requires explicit Artifact storage v2`，因此整体失败；
修正检查器后新目录重跑，未放宽产品版本 fence。原失败日志和状态保留。

上一包 `6c5a099` 的文档 CI 成功；其完整 CI 首轮在原 MI 自动 hold 等待处超时，
实际 Rust 单测/Clippy/Python 及此前 native gate 成功，后续 native 因该失败未运行。
日志保存在 `abort-remote-rust-39796756462.log`，不将失败轮记作通过。后续重跑的
原结果分别保留，[相同原提交完整 CI 的第二轮](https://github.com/Farewell-CK/RoboGuide/actions/runs/39796756462)
已成功。此重跑未改变产品 budget，也未证明该首次超时的原因。本包增加失败时保留
actual gate logs/原 SQL 的 CI 路径，避免仅
剩一个超时字符串；不上传证书私钥或 provider 凭证。

## 工程检查与剩余项

新增 parser 反例明确使用合成 owner/lease，不建立实际 OS authority；它们验证
原输入改变、旧 profile、缺 tombstone/seal、其它 producer/known consumer、丢失
GC command/head 和 Unknown 与完成 receipt 的严格区别。实际 OS/文件证明来自上表。
本包没有修改 Rust；上一包完整 Rust 975 passed、2 ignored 的范围保持不变。
本包完整 Python **3078 passed、0 skipped、480.97 秒**；恢复工具单独 **102 passed**。
521 文件格式/Ruff、331 源文件 strict mypy、函数文档、MkDocs strict 和 609 HTML 页
链接检查通过，网站 8 tests OK。远程 CI 在同一原分支提交后继续执行。

下一步继续源 Artifact 所有已知消费者和全部引用的 GC、未归属旧 Catalog 的显式
迁移，以及 Raft Snapshot/在线 Replica 与完整 N2–N7。私有接收 owner 的终态不证明
全局引用覆盖、在线多数派、跨主机恢复、设备停止或 SoftwareComplete。

# 时空资源模块软件验收（2026-10-25）

ADR-0209 的 Control 时空合同、运行入口和原数据恢复通过本次软件验收。
所有工作仍在 `codex/nonrelease-remediation-closure`；没有设备、模拟器、外部模型或实验运行。
这些记录不建立物理排他、设备停止、跨主机时钟可比性、全局 HA 或整体 SoftwareComplete。

| 证据 | 实际结果 |
| --- | --- |
| Core / Application 合同 | 跨 ID 空间重叠、clearance/参考/溢出拒绝、共享 time domain 别名、半开边界、完整批次原子拒绝、future policy fence、原截止时间恢复、当前 replacement 与不可变原 decision、未确认 overrun 持有通过 |
| 授权与 SQL | 非 admin GET/POST 拒绝；真实 SQLite exclusive writer 阻塞时 registry 更新回滚且无 command/authority 变化；释放锁后同原命令成功并只读复用原回执 |
| 实际 CPU / Node / Controller | `/tmp/rgt58-3/result.json` 通过；1 个实际软件 Node，2 个完成的 CPU 文件计算及 2 个独立 verifier，另 1 个 overrun CPU child；原 PID/预订和 Node journal 经同 root 重启保留，没有重复计算 |
| 活动授权冷恢复 | `/tmp/rgt58-3-active-recovery/result.json` 通过 backup/verify/新 root restore/原命令 retry；独立 SQL 比较，旧 v57 writer 拒绝且不改原 SQL；CLI 启动服务数 0 |
| 未确认停止冷恢复 | `/tmp/rgt58-3-final-recovery/result.json` 通过，保留原 deadline、ManualEscalation、资源与原策略；真实 OS wait 是 -15，Controller 没有独立 safe-state 证明，仍明确未确认停止 |
| 原 Archive 组合 | `/tmp/rgt58-archive-3/result.json` 通过真实 mTLS、原 SQL 故障、publication/retry/restart、完整 CAS/Archive 新 root 恢复和缺失字节拒绝；注册 Node/设备均为 0 |
| 原 Controller 存储门禁 | `/tmp/rgt58-controller-storage-1/result.json` 通过 15 次原生进程检查；实际 v57 原 writer、同 inode/硬链接 fencing、原 SQL quota/完整 Archive 冷恢复均保留，没有注册 Node |
| Rust | 最终其余 workspace 1023 通过、2 忽略；Replica 原批次其余 167 项和更新后的有限 reader 门禁分别通过，总计 1191 项通过；all-targets/all-features Clippy 与私有 Rustdoc warnings-denied 通过 |
| Python | 恢复工具完整回归 255 项通过；394 个维护 source 的 strict mypy、Ruff/format 与 function-doc 检查通过 |

实际 Controller SHA-256：
`36634ce81808f8977da93f8df1a9705b726142d42ad4b72b1e9a0e4d0904c197`。
实际 Node SHA-256：
`7d84a62b352200491de6af2dbb2dc743379238825666cbc9c1de87fe728f7cd0`。
真实旧 v57 Controller：
`326b9eeddadfdb125e9783d924bb646f5c76a6c7f4075120b963f646053ed20e`。

活动恢复 manifest：
`sha256:e0f52709f9b5b9caeacdd6dc4eaeb75a00fbac65e013a28f8686e88311efb8f7`。
未确认停止恢复 manifest：
`sha256:bb52a862589bde8be5654c7ef025054e989bc30d932aad7c606513d3a74aeff8`。
Archive 恢复 manifest：
`sha256:efd0a595dfc5e6b362c2903211a96288509a2ee3995c84f556b4763bf9a8336c`。

进程证明与冷恢复证明分开：实际 CPU 在原外部 Local EAIOS 进程内继续持有原 read permit，
重启的是原 Controller/Node root；新恢复 root 只核对原 SQL/CAS，未声称在新 root 启动外部
CPU 或恢复物理执行。外部临时 compute provider/input files 不在该 bundle 内。

首轮 `/tmp/rgt58-1` 暴露新 wrapper 在 Catalog/Archive 校验中的旧白名单遗漏；第二轮
`/tmp/rgt58-2` 的 backup/verify/restore 已执行，但 restore receipt 错误回落为 v0.19，未通过
验收。这些原结果保留。现在 receipt 使用有限的精确 profile 对应，旧格式没有重标版本；
最终第三轮完整通过。原 CI 还暴露 Archive process checker 仅接纳 v55，已扩展实际支持的
v56–v58，并使用实际 header 选择完整恢复 profile。没有放宽原字节、来源或 writer 检查。

本项闭合 C 的本地软件合同；E 的跨域时钟/事务、A1/A2 的全局内容组合和 F 的最终固定 profile
仍是原计划的必需项。设备、跨主机与长期 SLO 证据独立保留待验证。

后续完整 GC/SQL 兼容门禁 `/tmp/rgt58-artifact-sql-3/result.json` 通过全部 10 类实际进程案例：
正常修复、边界拒绝、SQL/copy/receipt 故障、原预算过期、原 source 改变、SIGKILL、缺失
历史和 WAL 拒绝。修复入口显式支持新的独立 Controller profile，仍拒绝 Replica 和
Replica-content；不把新版恢复包降级。255 项恢复回归及完整严格类型/风格检查继续通过。
双语站点严格构建通过，839 个 HTML/46 条维护路由，归档未解决锚点为 0。

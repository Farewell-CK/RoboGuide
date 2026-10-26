# 双独立 Control 域、在线归档与完整恢复验收（2026-10-26）

状态：HA16 完整实际门禁通过。正常执行、分区、原未知结果、停止确认、错误证书拒绝、
在线归档/换主和完整静止恢复均已观察；总体 SoftwareComplete 交付同时要求总矩阵的
当前完整 CI 与 main 同一受检 SHA。
唯一开发分支为 `codex/nonrelease-remediation-closure`。

## 权威与固定输入

本项连接 ADR-0211、0214–0220 的原持久协调与真实复制部署。两个独立的 Control 域各有
三个真实 mTLS voter，刻意使用相同数字 member IDs，但保持各自原 profile、Root owner、
Log、epoch、租约、决策和预算。端口、成员和软件 Node 标识由本次显式部署生成，Core
没有内置设备清单。两条配置声明的 CPU/file Node 执行真实 owned OS children；设备、
外部模型、模拟器、E6 和 benchmark 均为零。

Controller 保持 wrapper v60；显式 process v0.9 选择原 v0.6 base/parent11/Root8。
应用版本不隐式更换旧布局。peer v0.3 首次 admission 冻结 request 30 秒、正常事务
300 秒、另一份故障输入 120 秒、clock uncertainty 10 秒及停止只读 scope 300 秒。
原 cleanup 仍为 30 秒，只读观察最多十六代；新输入不能改写原事务或 Unknown 的预算。

| 冻结可执行文件 | SHA-256 |
| --- | --- |
| Controller | `9325ce34f294d349b9deac8c64e0258862aeda9846b9db3b1e8e086f3725621a` |
| Supervisor | `d2fd475d815c47541feea632e41b67ac121004836dfaa3abd3a4b20a9c16ad8f` |
| Node | `846f4afdd8285483a36e45d1df1cbe067f10226d1b41317a6c7b3212bef21a8e` |
| 实际 Root process probe | `6d7739f3fb4644b6ed9c3c15373b37e8d25a118309e17e18cfebe06f436fa606` |
| genuine 原 peer v0.2 Supervisor | `52e18bbb390acef65a22ca068bad470ffe73a0b9ff3176f8b8b69f6aa51418e0` |

当前实现提交为 `f7f0b1f0`。HA16 的 `source-identity.json` 另外固定实际 gate、恢复工具和
依赖脚本字节；验收运行时不修改这些文件或可执行文件。

## 实际链路

| 门禁 | 必须保留的事实 |
| --- | --- |
| 原 v0.2 程序拒绝 v0.3 | 实际旧二进制非零退出，所有原 SQL/CAS digest 不变，尚未创建 Node |
| 正常全局执行 | held Commit reply 时 CPU 数为零；完整所有域 Commit/release 后，普通 Control/Runtime 才允许 CPU 执行；实际 Completed 以后独立读取原文件、提交原目标证明 |
| 在线内容与换主 | 正常完成后两域正式 private Archive；两次真实原 V3 transfer 均核对原 source child PID/原 wait、目标实际更新 epoch 和不重复执行 |
| 分区与证书轮换 | 对端真实 Prepare 后断网；原 response-loss 保留 Unknown，不发送第二次 Prepare；分区期间真实 Stage/Retire 原 member leaf 并完成 Root transfer |
| 原停止对账 | 原 abort 失败；cleanup 到期后首次 GET 也在真实分区中失败；恢复网络后，仅独立 GET 对账原 stopped/resources_released，原 abort error/nonce/预算不变，未启动新 CPU |
| 实际 leaf pin 拒绝 | 新连接使用同 CA 的另一真实 leaf，实际 coordinator 在 HTTP 之前拒绝；不从转发计数或配置文字推断 TLS 成功/失败 |
| 完整静止恢复 | 所有原进程排空后，profile v0.31 同包冻结两个域的全部原 parent/Root/实际 child SQL、Node journal、CAS、声明文件与输入；verify/new-root restore/只读重复/缺一 Root 拒绝后，零 Node 重启各域并逐个验证三个实际 leader |

完整入口：

```bash
uv run --frozen python tools/quality/check_federation_ha_processes.py \
  --output /tmp/rg-federation-ha-16 \
  --supervisor /tmp/rg-federation-ha-bin-release-6/supervisor \
  --controller /tmp/rg-federation-ha-bin-release-6/controller \
  --node /tmp/rg-federation-ha-bin-release-6/node \
  --storage-probe /tmp/rg-federation-ha-bin-release-6/storage \
  --previous-supervisor /tmp/rg-federation-ha-bin-release-3/supervisor
```

HA16 结果：`/tmp/rg-federation-ha-16/result.json` 的 `passed=true`、`online_cases_passed=true`、
`complete_recovery.passed=true`，两个域各有 3 个恢复后实际 leader 读取；`cleanup_errors=[]`，
`devices=0`、`models=0`、`simulators=0`。完整 backup/verify/restore 和缺一 Root 拒绝记录在
`/tmp/rg-federation-ha-16/complete-recovery/`；恢复启动使用 0700 canonical child-socket
目录，所有恢复 supervisor 的实际 wait 为 0。
原完整 bundle manifest 为 `sha256:27dc08b521bf216fe1422aa6596c0bc9cb394bc44ef24df74351bae357320010`。

## 原反例与修复边界

早期 3 秒/v0.1 10 秒请求的真实超时保留；显式新预算只作用于新声明。原 lease issue-time
下界假设已被独立单元检查否定，未当作已证实根因。后续实际 diagnostics 明确报告
`status_stale`：健康事实在队列中变陈旧，而单纯 reader liveness 没有健康权限。
ADR-0220 在原 writer 内单独接纳同一 session 中实际始终不变的健康值，使用真实 receive
时间和当前身份/lease/sequence；健康变化关闭提前归约。原事实仍经 durable Commit 再 ACK。

远端 `3fbbab6d` 已越过分区/停止对账，随后 Python 3.13 在无 `_sslobj` 的监听 socket
上设置 `context` 抛错。修复后的工具在每个新连接 accept 时读取明确 SSLContext，实际三次
TLS 握手分别核对原、替换和恢复 leaf 的 DER digest；不改变产品授权。

`/tmp/rg-federation-ha-13/result.json` 保留 `passed=false`：正常目标、两域归档/换主、分区
与停止确认通过后，负向检查的外层 30 秒等待先到期；同一原 final checkpoint 已记录
`actual Federation peer leaf pin refused`。后续 gate 将外层观察改为 90 秒，原协议 request、
事务与 kernel budgets 均不修改。不得将原失败文件改写为成功。

当前共享模块整批验证为 Rust 1217 通过、0 失败、2 项既有忽略；恢复工具模块为 331 项通过；应用模块在最后入口整理后
另有 299 通过。全 Clippy、433 个源文件 strict mypy、Ruff 与函数文档检查通过。当前完整
CI、完整 native 结果和总签署仍分别核对，不从局部测试推断完成。

后续完整 CI 40267061646 已通过本 HA gate、升级后完整恢复及先前所有检查，在
initialized Controller 跨 owner 读取检查发现工具观察竞态：HTTP Content-Length 已收齐，
原 Root worker 尚未持久 EOF/close，`receipt=null` 不能直接下标为完成。维护工具现仅只读
观察同一原 reader/immutable intent 的真实 SQL close，固定 30 秒外层观察，不重发 HTTP、
重开 reader 或续原权限。原内容/digest/字节数及 complete 必须由实际持久 receipt 证明。
`/tmp/rg-current-initialized-1/result.json` 已通过完整初始化/TLS/原 child 故障重启、
跨 owner 原 reader close、Prepared 恢复/release/Completed retirement；五个实际
supervisor wait 均为 0。`/tmp/rg-current-initialized-cold-1/result.json` 完整恢复及三个
leader 实际读取通过。原 CI 失败保留，修复不改变产品合同。

## 证据限制

同一主机真实独立进程、文件、SQLite、CAS、mTLS、kernel clocks 与 wait 仅证明对应软件
合同。完整恢复不会自动启动服务；恢复后的零 Node 检查不会重新执行 CPU 或证明设备
连续运行。真实设备停止、硬件来源、物理交接、硬实时、跨主机连续性、长期 SLO 和人工
响应均需各自部署证据。

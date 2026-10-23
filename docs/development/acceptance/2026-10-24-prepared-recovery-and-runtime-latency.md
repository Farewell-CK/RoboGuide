# Prepared 恢复、故障观察与重复校验优化验收

日期：2026-10-24。对应 ADR-0177–0180，接续 N3/P3 与 N7/P7；不关闭完整
SoftwareComplete。全部开发保持在 `codex/nonrelease-remediation-closure`。

## 行为与边界

原上传在 Prepared 后发生真实 Completed SQL 故障时，原 record、owner 和未知结果
保留。新 leader 上，原 stable uploader/tenant 可显式创建有原预算、唯一 reader ID
和实际目的 writer 的一次性恢复意图。实际 Root 完整 reader close、独立新上传的
正常 Prepared/CAS/Completed 和原 SQL 回执匹配先于恢复完成。两个 owner 不合并，
原 Prepared 不改写；重试不重复复制，不续预算。事件 codec v32 / wrapper v53
阻止旧 writer 接受新 authority。

SQL 写入禁用后，精确 GET recovery observation 可返回原 Unknown。当前 TLS、
grant、tenant、epoch/quorum 仍要求通过；副本/restart/Catalog 不确定性还须取得
新的完整多数派视图，精确匹配本地 committed checkpoint。响应标记
`audit_persisted: false`，不清故障、不恢复 write/use，不因实际 CAS 已存在补造完成。

优化限于可证明不变的重复工作：Root 同连接的完整结构扫描有实际 SQL/header
witness；单行真实审计 INSERT 仅在完全符合 witness 条件时不触发无变化的权威
提交。自身/外部写入、DDL、trigger、rollback、重启和失败均不能借用旧证明。
当前权限、字节哈希、预算、PID 和实际 reader 生命周期仍逐次检查。

## 实际进程证据

证据根目录：`/data/workspace/roboguide-evidence/nonrelease-closure/p7/`。
最终结果 `prepared-recovery-native-07/result.json` 为通过；独立只读 SQL/文件复核
保存在同目录 `independent-recovery-verification.json`。三个实际 supervisor
PID 4084981/4084982/4084983 均实际 wait 0；新 leader 为 member 337 / term 2，
原 Controller child 为 PID 4086946。这些 member 是基础设施副本，不是具身 Node。

冻结 binary（`audit-recovery-binaries-02.json`）：

| binary | SHA-256 |
| --- | --- |
| controller-replica-adr0180-02 | `260e528c43025ea602130b044e16432f753cba1157d4623802ab4b940dd84d6b` |
| integration-server-adr0180-02 | `972cf1351b5589481d732962c8796c969d0cb4dcf50aeada73dad3cd2e1cf362` |

实际检查包含：原 HTTP 上传、5 秒 idle 无权威增长、真实 child SIGKILL/wait 与
原 inode 重启、原 supervisor 退出/新 epoch、跨 owner 原 Root 读取、Prepared
恢复、参数冲突、原命令只读重试、第二次独立恢复的完成 SQL 故障，以及故障后的
POST 拒绝和 GET Unknown。恢复内容 196655 bytes。独立复核直接读取 Controller
SQL、实际 Root reader receipt/authority 和目的 CAS 哈希，确认原 Prepared 未变、
第二次新上传虽然 Completed，但其 recovery completion 仍缺失。

最终样本 HTTP 原始耗时见 `member-337/recovery-http-observations.json`：

| 请求 | 结果 | 实际秒数 |
| --- | --- | ---: |
| 完整首次恢复 | 200 | 76.963 |
| 已恢复内容 GET | 200 | 1.565 |
| 原成功命令重试 | 200 | 0.455 |
| 修改原预算 | 409 | 0.250 |
| 恢复完成 SQL 故障 | 500 | 110.542 |
| write-disabled 后原 POST | 503 | 0.002 |
| fresh quorum 保护的 Unknown GET | 200 | 3.684 |

这些是短窗口观察，不是长期 SLO 或受控因果基准；完整恢复延迟仍需优化。前序
native-01–05 失败记录保留，不能计为通过：分别暴露检查器写错 readonly SQL view、
原 5 秒 callback 截断、客户端等待小于声明预算，以及重复结构/审计工作。native-06
通过但早于最终 fresh-checkpoint 限制，不能代替 native-07。产品原 120 秒恢复预算
没有因性能失败续期；Commit 等待使用首次 kernel budget 的实际剩余量。

## 相关检查

- 相关三个 Rust crate 回归：514 通过；最终权限限制另有针对性回归通过。
- 相关 all-targets/all-features Clippy、Rustdoc 通过。
- recovery-tool 143 项通过；完整维护范围 mypy 365 source files 通过。
- 修改的 Python Ruff、format、function-doc 检查与双语站点 strict build/9 项测试通过。
- `prepared-recovery-cold-v18-01/result.json`：独立 standalone storage v1 的
  wrapper v53 Archive/CAS/SQL 故障/冷恢复通过，使用该档案所冻结的 ADR-0177-01
  binary。保留目录说明复制后的 inode 不是原运行身份。

显式 recovery v0.18 保留 v0.17 合同，仅接纳无新增 recovery 事件的 wrapper v53。
它拒绝新 codec v32 recovery 历史、parent8/Root4；这项冷恢复结果不能冒充完整
Replica content recovery。证据索引 `p1/evidence-index.json` 现有 145 项，不以条数
推断总体完成度。

## 接续工作

跨 owner 原上传的显式释放/退休、Unknown 的只读对账、parent8/Root4 完整恢复与
升级、在线 readiness/Archive/Snapshot/GC，及原计划其余 N1–N7/P1–P7/F0–F10/
R01–R20 继续必需。下一包先处理原 owner 上传引用的释放，避免恢复后永久保留。

本包实际创建零具身 Nodes、设备、模型或模拟器。文件、SQL、TLS、CPU process 和
基础设施 wait 只证明软件事实，不证明设备停止、物理连续性或整体 HA 完成。

# 原内容主库显式接管 v0.1

本地 OS 管理入口归属 `apps/controller-replica/src/content_maintenance.rs`，原存储操作
归属 `core/controller-replication/src/store_adoption.rs`，权威合同见
[ADR-0166](../decisions/0166-explicit-original-content-parent-adoption.md)。本入口不启动
Replica listener、Controller 子进程或 Node，不选择设备，也不建立网络 quorum。

## 适用存储与步骤

原始输入是同一 profile/member 的实际 content Root2 与 Raft parent storage v4。
先关闭原实际 managed writers、处理原 Controller 子进程 wait/Unknown，再保存原
部署配置和存储证据。升级必须在原路径执行；复制数据库或改变 root/inode 不能借用
原身份。不存在的 root、SQL、mandatory head 或原内容均拒绝，不按配置初始化替代品。

两条独立命令均使用实际程序：

```bash
controller-replica --content-maintenance original-root-upgrade.json
controller-replica --content-maintenance original-parent-adoption.json
```

请求采用 closed JSON，未知字段拒绝。`member` 包含以下原部署值，不提供设备清单：

| 字段 | 原输入 |
| --- | --- |
| `content_root` | 原实际 content root 的绝对路径 |
| `profile_identity` | 原 profile 的 64 位十六进制摘要 |
| `member` | 原 infrastructure member 整数 ID |

root 请求包含 `operation: "upgrade-root"` 和 `member`。它取得原实际 root 与 SQL inode
writers，将 Root2 显式升级为 Root3，原 identity、copy/reader/pin/history 原样保留；
parent 仍是 v4。此原有升级不补造 legacy producer 的进程、时钟或完成证据。

parent 请求包含 `operation: "adopt-parent"`、同一 `member`，以及：

| 字段 | 语义 |
| --- | --- |
| `parent_sql` | 原现存 Raft SQL 的绝对路径 |
| `operation_id` | 新的唯一管理操作 ID，1–128 字符，不含控制字符 |
| `budget_ms` | 原 effect budget，100–300000 毫秒；在取得 root/SQL writers 前采样 |

实际 sibling/inode writer、native DELETE/exclusive drain 和原 child wait precede
admission。第一次意图、完整 first-intent 目录和 v4 adoption-pending marker 同事务。
之后建立当前原 Log/View/Snapshot 的新保留责任、验证每份 qualified CAS bytes，再将
本地 heads、确切接管 Commit 和 parent v6 格式同事务提交。原 root producer 的实际
回执及 parent 回执都须在原预算内；任一 SQL/文件/进程错误保留 Unknown。

原业务行逐项保留，不重新 apply 原 Control/Task/Execute，也不把相同 hash 的其它
owner 当作原 owner。旧 legacy pins 保持原样；新 producer 的预算/进程仅属于本次接管。
v5 已有原 journal 保持 v5 的独立合同，不通过此入口再次接管。没有可用真实原 epoch
的 Snapshot 拒绝接管，不制造 epoch。原部署必须保留该拒绝及相应待处理状态。

## 中断与观察

使用完全相同请求重试只返回原 `published` 或 `unknown` 记录，不重做 hash、publication
或续预算。改变原 duration、source inode 或完整 source 身份拒绝。原进程退出、时钟到期、
当前 source 存在和原 within-budget completion 是不同事实；观察不补原 completion。

若原 v6 尚未 Commit，新的显式操作可用新 ID/预算修复当前责任，旧 Unknown 和原
producer pins 留存。若 v6 已 Commit 但原回执丢失，新命令拒绝再次接管；相同 ID
保持 Unknown。正常原存储可验证新 heads/原 bytes，并依原 producer 合同独立对账，
不能用“重试成功”覆盖先前不确定性。缺 pending/v6 原日志、first-intent 目录、Commit、
root producer 或原身份时均拒绝，不手工将缺失内容写成空历史。

`content_parent_adoption_history()` 是只读存储视图。CLI 输出只表达本地管理结果，
不授予 Memory read/use、GC、Control、Node 或设备权限。除该命令自行持有的实际 writer
外，运维应独立保存二进制/配置摘要、原 SQL/CAS 和实际 OS wait 结果。

## 验证范围

`tools/quality/replica_parent_adoption_processes.py` 使用实际产品 CLI、原软件 Controller
上传/CAS、原 storage profile、hardlink/native SQL locks、SIGSTOP/SIGKILL 与独立 SQL
读取。通过 Archive gate 显式启用：

```bash
uv run python tools/quality/check_content_archive_processes.py \
  --skip-build --abort-unpublished \
  --replica-snapshot-probe /absolute/content_snapshot_probe \
  --original-producer-journal --adopt-original-parents \
  --content-maintenance-binary /absolute/controller-replica \
  --output /absolute/new-evidence-directory
```

旧 Snapshot/producers 二进制可分别通过 `--old-replica-snapshot-probe` 与
`--old-parent-snapshot-probe` 显式冻结；缺旧 binary 保持 unavailable。证据目录只创建
一次，失败不覆写为通过。此入口不退休 legacy pins，不证明全局 consumer 覆盖。
生产 Replica v0.4、same-voter bytes+metadata ACK、完整在线/跨故障域恢复、旧 storage
v3 部署升级与 Node/Mission 路由仍须实施。recovery v0.17 不自动获得 Root3/v6 权限。

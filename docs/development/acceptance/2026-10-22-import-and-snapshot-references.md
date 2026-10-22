# 原 import 与 provider Snapshot 引用验收

本包是完整非发布计划 P1/N1 的实际生产者交付。基线为
`45a82410867e01241394d1b9116aa53634ce2a06`，唯一实施分支为
`codex/nonrelease-remediation-closure`。验证的源码以每次运行保留的实际文件 SHA-256
为准；Controller/Node 使用该基线构建的实际二进制，进程门禁另记录其 bytes digest。
原工作树、实验目录和 main 保留。

## 实际调用链

原接收 provider 的 MemoryStore 在同一 SQL 事务保存 imports intent 与 Replica pin，
然后才打开输入文件和复制。新 producer 不能由公共引用命令代替或释放。只读 query、
进程重启和迟到原命令均不重新导入；原不可逆 revoke 之后才可退休 pin，worker、map
及其它引用独立阻止回收。完成历史、当前 availability、tombstone 与 unlink 分开。

显式 snapshot 配置先升级原 provider v3→v4，保留原 instance/全部历史；旧 v3 writer
实际拒绝 v4。恢复工具 v0.8 在全部原 root/inode 租约内持久化唯一原 command、内容
集合与 kernel 预算、snapshot pins，然后实际复制完整 SQL/CAS。发布后的原 manifest
和完整 bytes/SQL 验证通过才退休 source pins。最终 SQL 中断后只读核对原发布结果，
不重新 copy、不替换原意图、不续预算。冷恢复保留原 active intent，显式 reconcile
检查实际原 bundle 后才退休。未发布 copy 的显式 abort 以全部原租约永久封住原 command；
实际 SIGKILL/wait 后的 partial 输出保留，旧 command 不得再次复制。

## 本地进程与故障证据

证据根为 `/data/workspace/roboguide-evidence/nonrelease-closure/p1/`；
`evidence-index.json` 保存原运行路径、保留副本和结果文件 hash。Unix socket 使用独立
短目录，证据中的原路径保持原样。以下命令均为实际执行，不含模拟器或模型调用：

```bash
uv run python tools/quality/check_import_replica_processes.py \
  --output /data/workspace/roboguide-evidence/nonrelease-closure/p1/import-replica-final
uv run python tools/quality/check_snapshot_reference_processes.py \
  --output /data/workspace/roboguide-evidence/nonrelease-closure/p1/snapshot-process-final \
  --legacy-ref 45a82410867e01241394d1b9116aa53634ce2a06
uv run python tools/quality/check_memory_preparation_processes.py \
  --output /tmp/rg-p1-file-final-20261022 --content-reclamation --process-workers --async-workers 1
```

| 检查 | 实际结果与边界 |
| --- | --- |
| 原 import 生产者 | 3 次实际 provider 启动；SQL INSERT 故障回滚、导入响应丢失后的原只读查询、重启保留 pin、原 revoke/tombstone/真实 unlink、迟到命令不激活；0 注册 Node |
| Snapshot producer | 实际 v3→v4、完整 CLI backup/verify/restore/reconcile、最终 SQL fault 后无 recopy；实际活跃 copier 阻止 abort；原 copier wait 为 `-9` 后保留 intent，显式 abort 后原 command 不再 copy；0 注册 Node |
| 旧 writer/profile | 实际从基线提交提取的 provider 代码对 v4 退出 1；独立 SQL authority 观察不变；旧 v0.7 profile 拒绝新格式 |
| 正常 Node 文件组合 | 2 个实际注册的软件 Node、mTLS、1 次 import、2 次真实 CPU hash、2 个独立 verifier；3 次 backend restart、2 次 Node restart；实际最终 SQL fault、原只读对账、tombstone/unlink；原预算不刷新 |
| 定向回归 | 139 项 Memory/恢复测试通过，含原命令更换目标/profile 拒绝、原 SQL 与 CAS 读取、mandatory snapshot columns 缺失拒绝 |
| 静态门禁 | 479 个 Python 文件格式检查与 Ruff 通过；309 个主维护源文件 strict mypy 通过；函数文档检查通过 |
| 全量回归 | 指定实际 Python 3.9 部署解释器后，3031 passed，0 skipped，481.70 秒；Memory/恢复定向 139 passed；MkDocs strict 通过；v0.8 JSON Schema 与实际进程 profile 验证通过 |

新 provider 对本地 SQLite 故障返回明确 503，保留原不确定状态。旧验收器只接受断开连接
的假设已更新；仍独立检查实际注入的 SQL trigger、已发生的 unlink、原 tombstone 和
重启后原命令显式对账，不把状态码本身当作副作用或恢复证明。

## 原范围中的剩余项

本包不关闭完整 P1/F4/F5 或 SoftwareComplete。下面的 owner 不能按同一 digest 合并：

| 生产者 / owner | 当前证据 | 必需后续 |
| --- | --- | --- |
| 原 provider import / Replica | 本包实际完成本地原 import 生命周期 | 在线跨 owner 副本传输、独立远端原命令/ACK 对账 |
| 原 provider Snapshot | 本包显式 v4/v0.8 实际生产、验证、退休与故障恢复 | Controller Artifact 与 Raft Snapshot 的内容集合、跨 owner 恢复组合 |
| Catalog 与 immutable child / lineage | 原 publication/Group handoff 历史已存在 | 实际内容 owner 引用意图、不可逆退休及跨服务精确对账 |
| Event Archive | 原非 Catalog segment 可归档；catalog history 仍被保护 | Archive 内容引用、完整 catalog snapshot/replay、截断前传递闭包 |
| 当前 use / FileJobs / Active Map | 原实现继续通过本包回归 | 与上述所有生产者及 typed-map/MI 的完整组合矩阵 |

后续按原 P1→P2→P3→P4→P5→P6→P7 队列继续，不新建开发分支。
所有 Node 标识只属于明确的软件协议配置；没有创建设备、运动、物理停止、硬实时、
跨独立主机连续性或长期 SLO 的证据。

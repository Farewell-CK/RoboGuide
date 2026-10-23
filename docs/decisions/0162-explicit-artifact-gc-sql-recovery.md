# ADR-0162：Artifact GC journal 的显式原生恢复

- 状态：Accepted，实施中
- 日期：2026-10-23
- 前置：[ADR-0157](0157-source-artifact-retention-release-and-reclamation.md)、[ADR-0161](0161-controller-inode-writer-and-storage-generation.md)

## 原问题与决策

实际 v5 Artifact 的原 SQLite 写入进程在未提交事务及真实 cache spill 后 SIGKILL /
native wait -9，留下 hot rollback journal。当前 Controller 与 recovery 只读入口
正确拒绝，错误为 SQLite `READONLY_ROLLBACK`；原 SQL/journal bytes 保留。单纯拒绝
缺少恢复入口，不能把任何不可读状态解释为空 GC 历史。

新增只在本机 OS 管理边界使用的 `recover-artifact-journal`，使用完整显式 recovery
v0.17 profile、目标 Artifact、原 SQL/journal digest、独立 evidence directory 和原因。
不增加 HTTP/Node/Control 权限，不改变 Artifact v5、GC SQL v0.1、wrapper 或 Node 合同。
操作只交给 SQLite 恢复原未提交事务，绝不重建缺失表、marker、GC receipt 或 tombstone。

先取得完整 profile 的实际 writer 描述符，包括 Controller SQL inode/sibling、Artifact
与其它声明 writer，并验证所有当前已知 Controller reference authorities 均在其中。
已有低层租约获取与语义验证分开；只有确切持有的 actual lease set 可以暂缓目标 GC
SQL 的语义读取。Root owner/fence/完整引用 inventory/原 journal identity、所有其它
组件仍完整验证。不能由 caller bool、JSON 的 passed、任意 Arc 或一个路径授予暂缓权。

只接受完整真实 DELETE-mode SQL 与 regular 原 journal，在源 descriptor/path identity、
exact digests 与原 budget 下拒绝 WAL、symlink、缺失/改变的 source、活跃原 writer、
不完整消费者和其它损坏。只读打开确实遇到 `READONLY_ROLLBACK` 才准入实际恢复，
其它异常不取得隐含修复权限。原生 SQLite 的 exclusive/完整检查后，所有原语义 marker、
heads、commands、tombstones 与完整 profile 重新验证；任何一项未知就不发布完成。

## 意图、结果与重试

每个明确的新管理调用生成新的原随机 operation ID。实际源 SQL/journal 的 descriptor、
size/hash、完整 profile、原 owner/消费者、真实 OS caller/process/boot/namespace 与
不可续 deadline，先持久化为 immutable intent。原 SQL/journal 的 bounded forensic
copy 与 fsync 先于 writable SQLite；copy 完成再次核对实际原源，才调用原生恢复。
超时、copy/SQL/receipt 失败保留原 intent/partial/Unknown，不补造原完成时间。

evidence directory 必须在所有声明 source roots 外。同 directory 的重试只观察其原
intent/receipt，不打开 writable SQL、不恢复事务、不续预算。操作不提供接受旧 operation
ID 再执行的接口；新的显式 repair 有新的原随机 ID，早先 Unknown 仍独立保留。observe
不依赖当前服务可达，也不把当前 SQL 可读补写成原 within-budget Completed。

这里的外部 forensic 文件是管理审计证据，不是应用 checkpoint、内容保留授权、GC
许可、备份发布或可恢复权威；缺失证据显示 unavailable，不构造空历史。正常 v5 reader
仍只依赖原 GC SQL 的完整 authority；恢复审计不改变原 GC/上传/Catalog/租约语义。
需要随 cold bundle 保留时，明确声明为已有 `files` component，依赖原 Artifact writer。

## 验收

保留真实 SQL writer/PID/cache spill/hot journal/SIGKILL/wait；初始 readonly/startup/
backup 拒绝且原 bytes 不动。原 writer/Controller hardlink 占用、缺 known consumer、
wrong digest、symlink、WAL/损坏、copy/receipt 故障与原 budget 到期必须拒绝或 Unknown。
正常恢复后独立 reader、实际 Controller/mTLS/原内容与 cold profile 完整核对；原 active
上传、Catalog、tombstone/Unknown 及原 GC receipt 不变。同 directory 重试零 writable
effects，明确新 repair 与旧 Unknown 并存。实际 checker 使用零 Nodes/devices/models/
simulators，不把 SQLite rollback 当作执行停止、quorum 或物理连续性。

完整 online 内容、多数 ACK、全 producer closure、supervisor 原替换/迁移与其它 P1–P7
仍全部必需。不得仅凭管理维护通过宣布 SoftwareComplete。

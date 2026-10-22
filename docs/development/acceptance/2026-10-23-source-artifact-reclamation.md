# 源 Artifact 保留释放与回收验收

唯一开发分支为 `codex/nonrelease-remediation-closure`，接续 `51af590` 实现
[ADR-0157](../../decisions/0157-source-artifact-retention-release-and-reclamation.md)和
[recovery v0.16](../../../contracts/recovery/bundle-v0.16/README.md)。前一提交的
[完整 CI](https://github.com/Farewell-CK/RoboGuide/actions/runs/39812477755)与
[文档 CI](https://github.com/Farewell-CK/RoboGuide/actions/runs/39812491716)成功。

## 责任和格式

Completed 上传与 Catalog/child lineage 各自保留内容。当前原上传主体或同 tenant
实际 Artifact admin 用闭合原 command/digest 显式退休上传，固定完整原 receipt、
当前 stable principal/TLS/grant 与首次时间；不将原 Completed 改写为 Abandoned。
退休不删除内容，不释放其它生产者。shared wrapper v52/event codec v31 fence 旧
writer，原 v51 及全部原历史、预算和 authority 不变。

显式普通 CAS storage v5 保留原 owner/inode、已知 Controller inventory 和维护日志，
新增 mandatory GC identity/SQL/head。v3 升级 intent 先持久，v4 仍是终结的私有
Archive。恢复 v0.16 为每个 Artifact component 显式声明实际 v3/v5；旧 profile 不
能默认忽略新格式。GC SQL 使用 DELETE journal，先检查原实际 header，readonly
查询不能先产生 WAL/SHM 副作用；缺必需命令/tombstone/heads 拒绝而非空库重建。

离线 GC 持有 profile 全部原 writer/inode，包含 CAS 已知的每个原 Controller。
先检查 Catalog/child、Prepared/未退休 Completed 上传、原 preparation/use/import/
handoff、MI policy/goal、verifier、Snapshot 和 Archive；不支持 Replica/online
authority 时拒绝。完整 source SQL 由实际 SQLite backup API 在原 writer 内冻结，
不遗漏 WAL。原 hash/size/inode、writer/PID/start ticks/kernel/非续期预算和完整
closure 的 intent 与不可逆 tombstone 同一原 SQL 事务提交，然后才 unlink/fsync。

运行期 MI 声明要求实际 policy 显式 revoked、Mission 终态、全部 switch Applied/
Cancelled。Completed 还核对 Control 原 goal contract、独立 receipt、原 kernel
接收和完成预算。该完整 evidence 进入 Catalog retirement proof digest。原历史仍
保留 digest 不表示永久持有 bytes；use/revoke 与 child lineage 仍独立核对。

unlink 或 receipt 失败保持 Unknown，原 ID 只读重试；新显式 ID 可以处理剩余 bytes，
不改先前结果。实际 read/verify/finalize 都拒绝同 owner tombstone；把相同 bytes
重新放回目录也不能复活。已释放历史恢复不要求已删除内容，活动引用与 tombstone
冲突仍拒绝。

## 实际进程与故障证据

证据根为 `/data/workspace/roboguide-evidence/nonrelease-closure/p1/`。原输入是
实际完成且已 reap 的软件过程，另存一致副本；历史源不改写。

| 原记录 | 实际通过范围 |
| --- | --- |
| `gc-adoption-v52-03` | 真实旧 v50 producer 的原 publication，经当前 v52 mTLS/admin/tenant/adoption、SQL rollback、完整原 Archive、原重启及冷恢复；真实旧 v51 使用原 CAS owner 后拒绝 v52，退出记录由实际 wait 产生 |
| `source-gc-adoption-release-02` | 独立原 Node 上传和当前真实重复上传各自退休；原 Snapshot 保留拒绝，取得原/当前实际 writer 后显式退休；Catalog 独立保留，完整 GC、文件/SQL 故障、旧拒绝与冷恢复 |
| `source-gc-mi-final` | 实际原 Request/SQL/owned CPU children/独立 Review、两个 Task 自己的 import/use/verifier 和独立原目标；两个正式软件 Node，0 设备/外部模型/模拟器 |
| `source-gc-mi-release-final` | 使用上述完整原 SQL/CAS：有效 policy 拒绝 Catalog 退休，实际 recovery-admin 显式 revoke，原 parent/child 先后退休；parent 退休后仍因 child 拒绝 GC；全部原撤权/goal/kernel 证明后 GC 和冷恢复通过 |
| `source-gc-adoption-archive-final` / `source-gc-mi-archive-final` | 最终同一源码分别重跑上述两条释放/GC/冷恢复，并在源 bytes 已真实删除后，由实际 Controller 归档完整退休 history；不要求找回已释放 bytes，原 Archive retry 不重复制，新 Archive 也纳入完整 bundle |
| `source-gc-catalog-cold-02` | 未退休 MI child lineage 的完整原 CAS 冷备份/验证/恢复；原 v51 确实因 v52/新 authority 拒绝，未以缺 owner/语法错误充当格式 fence |
| `source-gc-archive-upgrade-02` | 原 v49 binary 真实产生 Archive，当前 v52 保留原 intent/checkpoint/budget，abort/tombstone/SQL 故障/只读原重试/冷恢复，以及实际旧 CLI 原闭合 profile 拒绝 |
| `source-gc-private-archive-01` | 当前原 producer 与独立私有接收 owner 的全部 writer、retirement/GC intent 故障、partial unlink、原 Unknown 不重做及已删内容的冷恢复；0 Nodes |

两个 source-GC 门禁均通过真实同 UID 目录权限 `0555`：SQL intent/tombstone 已提交，
unlink 实际 PermissionError，剩余文件仍在。旧 ID 仅观察 Unknown；另一个显式 GC
删除余量，旧 Unknown 不变。实际 Rust readonly probe 独立证明仍在磁盘的 tombstoned
bytes 不可读。重新放回同内容后旧 completed ID 不重删，HTTP 内容入口仍拒绝，新的
实际上传也不能 finalize；另一个新 GC 才删余量。HTTP 403 同时受退休 Catalog 影响，
不把该状态码单独当作 CAS tombstone 证明。

旧 profile Snapshot 由实际未发布的失败备份留下。升级后不能用当前 profile digest
重造其原命令；新的显式退休核对完整原 profile/intent，实际原 source writer 被占用时
拒绝，取得原/当前所有 writer 后保存 v0.2 abort receipt，复制/unlink 为零。receipt
保留两份完整 profile，伪换 writer 不能通过重算 digest 绕过原 coverage；pending
receipt 精确对账只发布原 metadata，不续期或复制。历史源 intent 未变。

实际当前 Controller SHA-256：
`2d14edd6619be91bcec675bb0e53a5fcb4590a662f1a0548e8eb009edcb8f867`。
独立 readonly probe：`ca4a44de4f3f2d186ccfa7225eb8ec613857994224c6d28c1feaa766559d02d0`。
真实旧 v51：`5446d048a89a29ad4e5476df2849f3f1e885c795bbfd7254630f0e85030412f2`；
原 v49 Archive producer：`099e3f8a559be67b8c2cebdc4296e404920c4af837e7a38dc7a7c7814a363f89`。
Node binary 仍为 `7103a3ba52c4ae9829b5f67e214d936cb4826e0a5e515d78249570a33c168c1e`。
各轮完整 source/config/binary、argv/SQL/CAS/stdout/stderr 与实际 wait 分别保留；
不同构建结果不合并身份。

## 失败与验证边界

最初 `source-gc-native-01` 正确因旧 profile Snapshot 保留而拒绝，暴露实际升级后的
原 pin 需要独立退休入口；后续增加该原/当前双 writer 合同。早期门禁又遇到原 Node
凭据已经撤销、wire content 实际为 202、退休 Catalog 实际返回 403；检查器保留原
结果，使用实际当前主体重复上传并加入原 CAS readonly probe，未放宽产品权限。

`gc-adoption-v52` 的旧程序因缺原 CAS owner 先拒绝，不能计为 v52 fence；改用
canonical restored CAS 后才通过。`source-gc-archive-upgrade` 和第一轮 Catalog
cold 的旧 profile 仍带新字段，仅语法拒绝，不能计为旧 authority fence；改为保持
真正原闭合 profile 后重跑通过。首次 MI GC 的外主体没有 Artifact grant，连接正确
拒绝，但未形成所需 tenant 检查；新工作副本显式配置实际另一 leaf 的外租户 admin
后，以真实 HTTP 403 验证 tenant，未改历史 source/receipt。

全量 Rust 为 **978 passed、0 failed、2 ignored**；Clippy/private rustdoc 通过。
新增 parser counterexamples 覆盖缺 journal/head/tombstone、替换原 receipt、独立
provider revoke 缺失/错 instance、原 MI policy/switch/goal/receipt/kernel 及过期证明。
它们是 synthetic contract evidence，不进入部署。最终全量 Python 为 **3110 passed、
0 failures、0 skipped**，含实际 Python 3.9 标准库桥接，未启动 Habitat/模型；
recovery 定向 **133 passed**。strict mypy **341 files**、Ruff/format、函数文档和
website 独立 typing 通过，严格构建检查 **619 HTML pages/42 maintained routes**、
8 项 site 单元检查通过。原始日志与后续同 SHA CI 分别保留。

此项关闭已支持 standalone 原引用的离线源 Artifact 回收。Raft Snapshot/online
Replica producer、provider 旧 profile Snapshot 的完整闭环、N2 typed-map 整链和
N3–N7 原所有条件仍必需。GC hot rollback journal 目前在 readonly 启动/备份拒绝，
显式 native rollback 维护仍须补齐；不把安全拒绝写成可用性闭环。没有建立跨主机
多数派内容 ACK、设备停止、硬实时或 SoftwareComplete。

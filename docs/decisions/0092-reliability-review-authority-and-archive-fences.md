# ADR-0092：可靠性复查后的身份、恢复与归档闸门

- 状态：Implemented for the reviewed software slice
- 日期：2026-10-13
- 修订：ADR-0089、ADR-0090、ADR-0091

## 问题

对 `dev@dbb44f6` 的反例复查发现：checkpoint 覆盖事件序号不代表 catalog 可恢复；
共同 CA/DNS 不代表 exact replica 身份；建立 mTLS 连接不代表允许修改 Mission。
breaker 的恢复字段、滚动窗口、归档失败回滚以及统一恢复视图也缺少必要闸门。

## 决策

1. Catalog/Memory/localization 当前仍从 SQLite events 重建。完整 catalog snapshot 或
   archive replay 合同建立前，包含这些发布、replica transition 或 localization 证据的
   cutoff 一律拒绝。应用维护还拒绝任何非终态 Mission 或当前 Unknown attempt；带
   replica writer 的本地 GC 继续拒绝。保护由软件执行，不依赖 operator 手工识别事件类型。
2. 本地 segment 每次最多 8192 条、16MiB，读取前还有 source-byte 上限。文件和独立
   manifest sidecar 通过 staging/fsync/create-new publication 发布，目录也 fsync；
   已存在的内容必须精确相同。SQLite 用 managed transaction 原子删除 prefix、推进
   history floor 并追加 manifest index；COMMIT 失败回滚，未登记事务不能暴露未提交 rows。
   归档事件的 receive-time 高水位在同一事务保存，删空事件表也不能降低重启时钟下界。
   旧 GC 数据若缺少该下界，拒绝以默认零恢复，需要可信迁移；底层 I/O outcome 无法确认
   的维护返回 `maintenance_outcome=not_confirmed`，不能宣称一定没有删除 rows。
   `GET /v1/maintenance/archive?after=<sequence>&limit=<1..128>` 提供有界 manifest 页。
   已有版本只保留的 last manifest 不被伪造为完整历史 index。
3. Replica async RPC 和 child 的同步 fence client 在 TLS handshake 后、application frame
   写出前，核对目标成员的 leaf DER SHA-256；listener 保留 sender-to-leaf 校验。启动核对
   本地证书所属 member，成员 digest 必须是互不重复的 canonical lower-case SHA-256。
   profile identity 继续绑定配置，静态集群升级/证书变化仍需要可信迁移 gate。
4. 所有 operator mutation 路由先统一鉴权。viewer 只能读取与只读 assessment；operator
   可提交/取消 Mission、修改 future scheduling、普通 resume 和取消 execution；
   recovery-admin 才可 review/acknowledge quarantine、execution/group recovery 与 archive。
   header 仅可与已认证角色一致，不能提升角色。原始 loopback 默认 operator；
   `ROBOGUIDE_OPERATOR_LOCAL_RECOVERY_ADMIN=1` 只允许明确配置的、无 mTLS 的 loopback 开发
   listener，且管理员身份在启动时冻结。mTLS listener 的角色只来自 leaf digest map。
   显式 quarantine acknowledgement 必须携带 bounded nonempty reason。Review 的持久化
   失败返回 503，contract/generation/current-evidence 拒绝返回 409，候选均在 Commit 后发布。
5. Controller wrapper 升级为 `roboguide.controller-checkpoint/v26`，新 failure-window 事件
   使用 codec v19。Current wrapper 必须携带 failure authority 与 window 字段；降级 marker、
   缺失 authority、hold/state 不一致与无 hold 的非终态 quarantine 都拒绝。真实 v25 历史
   可迁入，缺少的 rolling samples 保持 `null`/unavailable；既有 quarantine 不自动解除。
   新失败只保留同 class/fingerprint 在最近十分钟内的至多三条 receive-time samples。
   quarantine 不因时间推移或 fingerprint 改变而解除，必须 generation-bound 显式审核。
6. 同一不可变 terminal outcome 的重放、被拒绝的 resume 或重复点击不计作新失败。
   生产 completion-deadline violation 的三次真实 HTTP resume 均保持 count=1/generation=1；
   这证明幂等与 hold，不证明生产中三次独立失败可达。Independent-failure 累计与窗口/restart
   闸门在 Orchestration/应用夹具验证，不宣称任意程序 panic 的 crash-loop 防护。
7. `roboguide.controller-recovery-view/v0.2` 的 Unknown 条目使用 exact current attempt ID，
   correlation ID 单列。Active AwaitingStop/StopConfirmed/ContinuationPrepared 属于待恢复；
   active BudgetExpired/OriginalFailed 属于人工介入。Completed/Continued/Aborted 与 released
   Group 留作历史，terminal Mission 的残留 failure evidence 不阻塞全局恢复状态。
   恢复顺序明确是操作指引，不代表一个自动执行的恢复状态机。

## 验证与边界

正式回归使用 SQLite deferred-FK COMMIT failure、实际篡改后的 archive 文件、完整 catalog
重放、实际 rustls sockets、CA-valid wrong-member leaf、mapped viewer/unmapped client、
production HTTP resume/review 与 checkpoint downgrade/omission 反例。对应函数与软件进程
证据见 [fault matrix](../development/runtime-reliability-fault-matrix.md)。PKI 仅在测试临时目录
离线生成，测试依赖 OpenSSL；没有替代生产 trust source。

这批修复不提供 catalog 历史 GC、远端 archive 共识、跨两个独立主机的 partition/SIGSTOP
证明、长期容量曲线、物理 stop/continuity 或 E6。归档条数与 bytes 有界，不承诺磁盘 I/O
硬时限。完整恢复仍由 Runtime/Control/Orchestration 和已有 stop/repeat/handoff gates 承担。

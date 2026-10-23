# ADR-0176：原 Controller 的 qualified 内容读取

- 状态：Accepted，本包已实现，完整组合矩阵继续验收
- 日期：2026-10-23
- 前置：[ADR-0175](0175-exact-committed-content-routing.md)

接管后的 Controller 保留自己的 Artifact owner，不将另一个原 owner 的 bytes 或
标记复制成本地 owner。显式 parent8 child channel 增加只读 `ReadContentV1`，仅
实际原 PID/instance/boot/start-ticks、mTLS、current epoch/quorum 和原预算允许。
原 Root managed reader 在 payload 读取前持久 intent/pin，authority digest 绑定完整
已提交 target、实际原 process/leaf、qualified input、用途及实际请求上下文 digest。
首个读取核对精确 target；之后每个有界 chunk 核对原权限/预算和原 epoch/membership，
并从实际当前 SQL 核对相同 qualified input 仍保留、同一公开来源仍有效。无关新提交
不使原读取自动失败；更早 revision、丢失/退休/含糊来源拒绝，原 head/预算不刷新。
异步取消不释放尚存活 worker/FD/
Root leases/容量，丢失尾回执保留 Unknown。重复原 reader ID 不重新打开。

用途显式区分：`Published` 只读取当前已提交、未退休的 Completed upload、已发布
Catalog/lineage 或 verifier 内容；纯 Prepared input 即使已有三个副本也不能用于
公开读取。相同 digest 若有多个原 owner 则拒绝含糊选择。`RetainedIntegrity` 仅供
Controller 启动核对精确仍被原完整 target 引用的 qualified bytes，不授予 HTTP/
Node/Memory use，不写本地 Artifact CAS 或创建 availability/placement 记录。

应用 Artifact HTTP 仍执行现有真实调用者 ACL/scope/import/current-use 检查，并在
每个返回 chunk 前复核。仅本地 finalized CAS 缺失时，从同一个实际 replica client
读取原 Root；源身份和完整 byte receipt 保持独立。Catalog restore 的跨 owner
依赖通过上述 integrity scope 核对，原 retained dependency/owner/history 不改写。
任何本地冲突/损坏不得由 fallback 掩盖。legacy/standalone 没有该读取能力，旧 peer
拒绝新 command。原 Domain/Node/Memory/storage schema 保持，读取不能 Commit 或
把 Prepared 自动完成。Prepared finalization 继续另有明确原操作/授权/证据合同。

验收以真实三个 supervisor/Controller/mTLS/SQL/Root 先上传，再实际终止原 member、
由新 leader 的真实 child 下载原 owner 内容，保持原 owner 和新本地 CAS 空缺，
只读依赖核对不创建新的 placement/use。覆盖未发布 Prepared、含糊 owner、权限/
原 process/epoch/预算变化拒绝和 reader cancellation/原 Unknown。不创建设备、
模型或模拟器，不从该软件读取推断物理连续性或整体 SoftwareComplete。
当前实际结果与证据限制见[验收记录](../development/acceptance/2026-10-23-owned-controller-content-reads.md)。

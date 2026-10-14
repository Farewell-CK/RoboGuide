# ADR-0096：服务入口安全与 Authority Journal 初始实现

- 状态：Implemented on the current branch
- 日期：2026-10-14
- 衔接：ADR-0086、ADR-0091、ADR-0092、ADR-0095

## 决策

1. Mission Service、Artifact HTTP 和 Controller HTTP 的默认部署继续使用 loopback；任何非 loopback listener 都必须在启动前配置完整的 mTLS server certificate、private key、client CA 和 certificate digest role map。
2. Mission Service 的 mTLS role map 支持 `viewer`、`submitter`、`approver`、`operator` 和 `admin`，并可把证书主体绑定到 tenant。缺失、空值、未知 role、非法 digest 或缺少证书文件都会 fail closed。
3. Artifact HTTP 的 mTLS role map 支持 `viewer`、`writer` 和 `admin`。Viewer 只能读取健康状态、catalog 和 CAS bytes；写入、上传、发布、replica 和 localization evidence 需要 writer/admin。
4. Loopback 开发模式不伪造远程身份；Mission HTTP mutation 仍写入 principal、tenant、role、route、correlation 和 idempotency audit receipt。
5. State SQLite 在每个 checkpoint batch 中写入 checkpoint-bound `ControlCommandJournalEntry`。旧调用自动生成 digest-bound compatibility entry；fact batch/timer 已通过 `save_checkpoint_with_command` 提交 input-digest-bound command identity；HTTP admission/catalog 等 authority mutation 仍需继续接入真实 epoch、plan 和 ownership digest。
6. Journal command identity 重用时必须携带完全相同的 authority evidence；不同 evidence 的重用会拒绝并回滚外层 batch。
7. Mission HTTP mutation 支持 durable `Idempotency-Key` reservation/replay；同一主体、tenant、method、path
   和 key 绑定到 canonical request digest，成功响应持久化后重放原状态码和 body，冲突复用或并发 pending
   请求 fail closed。Mission Request 首次成功写入时绑定 tenant，后续读写必须通过 tenant fence；admin 才能
   跨 tenant 进行运维访问。
8. Mission、Controller 和 Artifact 暴露只读 `/metrics` 与 `/readyz`。指标是聚合观察数据，不改变任何
   Control、Runtime、State、Memory 或 ownership authority。Artifact 客户端访问远程 endpoint 时必须提供
   `ROBOGUIDE_ARTIFACT_CLIENT_CERT`、`ROBOGUIDE_ARTIFACT_CLIENT_KEY` 和 `ROBOGUIDE_ARTIFACT_CLIENT_CA`。

## 兼容与边界

- 默认 loopback 配置不需要凭据，保持现有本地开发和合成测试路径。
- 本 ADR 不声称已经完成动态 tenant 生命周期、动态 HA membership、真实 Control mutation metadata 全量接入或物理安全证明；这些由整改计划后续阶段继续实现。
- Command Journal 是 checkpoint-bound authority evidence，不等同于完整 Event Sourcing；EventLog、checkpoint、archive 和 Controller authority 的职责保持不变。
- Memory selective import 的独立 wire/journal 边界见 ADR-0098；本 ADR 的 HTTP/Artifact journal 不把 Memory
  import receipt 误记为完整 Controller authority。

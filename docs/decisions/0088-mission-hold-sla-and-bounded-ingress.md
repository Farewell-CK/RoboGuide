# ADR-0088：Mission Hold SLA 与有界 Node ingress

- 状态：Implemented for local hold evidence and session ingress
- 日期：2026-10-12
- 范围：Orchestration Mission hold 与 Node Protocol reader queue

## Decision

Mission-local Hold 持久化 `isolated_at`、generation、source、五分钟 review deadline 和
operator escalation level。deadline 到期只产生可观察的升级状态，不自动重试、释放资源、
重新绑定 Node 或改变 stop-proof。恢复必须由现有 generation-bound resume 和 Control 当前
eligibility 重新授权。

每个 gRPC Node session 的 validated reader-to-application ingress 使用固定有界容量。队列
填满时 reader 背压 transport，不把未经应用接受的 Node facts 无限累积在进程内；应用 ACK
仍只在 durable acceptance 后发送。控制、租约和 route-loss 事实继续沿同一 session 安全
路径处理，观测丢失不能改变 authority。

## Boundaries

- Hold SLA 不是物理 deadline、停止证明或自动恢复授权。
- 有界 ingress 不改变事实分类；共享 Control、Runtime、SQL、复制和 Unknown 错误仍保持
  fail-closed。
- 当前实现没有为 Hold 自动释放或租户级资源配额增加新 authority。

## Validation

离线测试覆盖 review deadline、升级状态保持 Hold、队列容量背压、应用 acceptance timeout
和三 replica Controller 故障恢复路径。E6、模型、Habitat 和真实机器人均未运行。

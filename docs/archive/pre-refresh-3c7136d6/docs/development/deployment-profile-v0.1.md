# RoboGuide Production Candidate Deployment Profile v0.1

状态：整改基线；需要部署负责人在进入真实生产前冻结具体地址、证书、目录和阈值。

软件输入现有 [闭合可执行合同](../../contracts/deployment/profile-v0.1/README.md)与
[inspector](../../tools/deployment/README.md)，由调用者提供实际值并按 digest 保存原声明/
只读观察。实际零 Node 组合见[软件验收](acceptance/2026-10-21-deployment-profile.md)。
下表仍是人类可读的候选部署约束；RTO/RPO 目标不因配置冻结而成为已达 SLO。

这个 profile 把组件拓扑、信任边界、持久化位置、故障域和恢复目标写成可审阅的部署输入。它不把
checkpoint、replica history 或 metrics 当成物理连续性证明。

## 拓扑

| 平面 | 组件 | 推荐共址关系 | 远程边界 | 唯一写入者 |
| --- | --- | --- | --- | --- |
| 请求面 | Mission Service | 与 Mission Intelligence 同主机或受保护内网 | Mission client → Mission mTLS | Mission Service Request Store |
| 控制面 | Controller HTTP、gRPC Integration、Control、Runtime、State EventLog | 一个 Controller composition root；HA 时每个 replica 一份 | Node/Operator/Replica mTLS | Control/Runtime/Orchestration 分域写入 |
| 节点面 | Node Service、Local Integration Engine | 与 Local EAIOS 同主机 | Controller → Node mTLS/Node identity | Node journal、Local EAIOS |
| 数据面 | Artifact HTTP、CAS、Memory catalog | 可独立部署；生产远程访问必须 mTLS | Controller/Node → Artifact mTLS | Artifact Store / Memory provider |
| 复制面 | Controller Replica、archive、snapshot | 三成员静态基线；动态 membership 仍是 S6 | Replica mTLS + leaf pinning | Quorum/leader authority |

## 持久化和故障域

- Mission Request Store、Controller EventLog/Checkpoint/Command Journal、Node execution/import journal、
  Artifact CAS 和 Replica store 必须使用互不覆盖的持久化目录。
- Controller checkpoint、archive segment、catalog replay evidence 和 Memory manifest evidence 不能在
  没有完整 replay contract 前执行删除或 GC。
- 单主机故障不得同时丢失 Controller authority store 与 Artifact CAS；跨站点副本需要独立批准。
- Node journal 是本地执行连续性证据，不能由 Controller checkpoint 代替。

## 信任边界

1. Mission、Artifact、Controller 和 Replica 的远程 listener 缺少完整 mTLS profile 时必须启动失败。
2. 证书主体、tenant、role、Node identity、controller epoch 和 provider identity 必须在边界内核对；
   header、metrics、diagnostics 和状态页不能授予权限。
3. Local EAIOS 是即时物理安全权威。RoboGuide 只能保存 `StopRequested`、`SafeStateObserved`、
   `StopConfirmed` 等证据，不能伪造 safe-state。
4. Memory discovery 不授权复制；导入必须走 ADR-0098 command、provider admission 和 durable Node journal。

## 初始恢复目标

| 组件/故障 | RTO 目标 | RPO 目标 | 恢复前门禁 |
| --- | ---: | ---: | --- |
| Mission Service process restart | 5 min | 0 committed requests | idempotency/audit replay；tenant fence |
| Controller process restart | 2 min | 0 checkpoint-committed mutations | checkpoint → journal high-water → reconciliation |
| Controller quorum loss | 10 min | 0 quorum-accepted batch | no new physical command; current epoch fence |
| Node process restart | 2 min | 0 Node journal identity | local dispatch/Memory import Unknown reconciliation |
| Artifact process restart | 5 min | 0 finalized CAS bytes | upload fence、catalog replay evidence |
| Memory import interruption | 5 min | 0 manifest identity | same import/command/authorization digest retry |

这些目标是需要演练验证的候选值，不是当前系统已经达到的 SLO。

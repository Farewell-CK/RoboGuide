# ADR-0095：生产候选版整改边界与实施门禁

- 状态：Accepted for implementation
- 日期：2026-10-14
- 衔接：ADR-0083、ADR-0086、ADR-0091、ADR-0092、ADR-0094

## 决策

1. 生产候选版整改采用分阶段 `ADR → implementation → acceptance → deployment drill` 流程。
2. Mission、Artifact 和 Controller 的可远程 listener 必须有服务内 mTLS、证书主体、角色和审计；默认 loopback 是开发 profile，不是远程认证。
3. Control durability 采用 Command Journal + versioned Checkpoint，保留当前 EventLog 作为证据存储，不在本阶段改造成完整 Event Sourcing。
4. 物理安全采用通用 Stop/SafeState/StopConfirmed 合同。取消、lease expiry、heartbeat、journal receipt 或 checkpoint replay 都不等同于物理停止。
5. Memory 平台支持完整的 manifest、provider、权限、selective import、ACK、retry fence、retention 和 audit。不同设备、不同高度和不同 frame 的地图保持独立，不自动融合成一张地图。
6. HA 验收分为 AuthorityContinuity、EvidenceContinuity 和 PhysicalContinuity；前两项通过不能推出第三项。

## 门禁

- 未完成 authority matrix 和 DeploymentProfile，不进入跨模块实现。
- 可远程 listener 没有完整 mTLS 配置时必须 fail closed。
- 没有 StopConfirmed，不得释放 physical ownership、自动重放或 Actor takeover。
- Discovery 不得隐式触发 Memory replication。
- 软件测试、合成进程证据和物理证据必须分开报告。
- E6、实验结果和模型效果不构成整改验收证据。

## 结果边界

本 ADR 不承诺自动跨设备地图融合、任意异构设备物理交接、运行中未经确认的抢占、模型语义正确性或物理连续性。上述能力需要独立的物理和部署 gate。

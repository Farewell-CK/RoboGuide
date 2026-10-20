# Runtime plan revision v0.2

设计与运行限制见 [ADR-0121](../../../docs/decisions/0121-durable-runtime-mission-intelligence-work.md)
与[运行期 MI 工作](../../../docs/development/runtime-mission-intelligence-v0.2.md)。
本版保留 v0.1 的全部原 goal/Review/stop/revoke/Control 门禁，增加显式生成授权、原 SQL
worker claim 与两次不可重复的 provider phase 许可。v0.1 不会隐式得到这些新增权限。

| 新 action | 当前权限与效果 |
| --- | --- |
| authorize-generation | 实际 recovery-admin 为原不可替换 policy 授权一次；不增加触发源、次数或期限，不授予 Review |
| claim-runtime-work | 原 proposer 冻结实际 SQL instance、work/identity digest 和原 MI Request；换空库不能重新领取 |
| admit-provider-call | 原 proposer 在原 stop hold 下固定 phase、SQL nonce 与 request digest；每 phase 仅一个 nonce |

Schema 校验不授予权限，不替代实际 TLS、原 current-policy/time/source 检查或语义验证。
provider permission 是工作许可，不证明模型调用完成。实际原始回包与调用结果由 MI SQL
保留；Unknown/中断不会被重放变成新调用。旧数据迁移保留原摘要和预算，wrapper v44 拒绝
旧 writer 与旧 wrapper 中的新增 authority。

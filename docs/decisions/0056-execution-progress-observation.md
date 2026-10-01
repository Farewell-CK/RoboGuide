# ADR-0056: 当前 physical attempt 的只读操作进度

- Status: Implemented
- Date: 2026-10-01

进度复用 Node Protocol v0.4 的注册 State export 与 bounded observation batches，不新增
传输 authority。Local EAIOS 明确配置只读 observer 才发布
`roboguide.execution-progress/v0.1`：每条 sample 包含 execution_id、canonical operation、
stage_epoch、completed_units（可为空）和 working/waiting/blocked/unavailable。
单位与 stage 含义由操作契约定义，心跳、位移和 Habitat step 不能通用地替代进展。

export 必须属于相应 operation 的已注册 LocalSystem；每个 owner 只有一个明确 progress
channel。固定 Node object、schema、run-local physical attempt 与 operation 精确匹配；
超过 128 条、重复 attempt、无法解析的 payload 不更新进度。原始 State evidence 仍独立
保留。没有配置 observer 或信息未知时返回 Unknown，不补造进度。

Runtime 保存当前 sample 的 source epoch/sequence、原 received_at/TTL 与最近 measured
advance 时间。重复、乱序、旧 attempt、错 owner/operation 不续期；注册变化 fence，restart
保留原时间并等待非 Unknown 的真实执行确认。重连建立新的观测基线，不能把断档计为停滞。
Runtime 只保存单条 sample 与源 key/epoch/sequence/时间/TTL，不为每个 execution 复制
整个 State batch payload；原始 batch 留在 State authority。HTTP evidence 暴露原 attribution
和最近 advance 时间，读查询不续期。进度记录随已有 attempt registry 管理，不保存每步轨迹。

只读 `GET /v1/executions/{id}/progress` 返回观测分类。只有明确指定合法
`stall_after_ms` 且有 operation-specific counter 的 working observation 才能标记 Stalled。
waiting 从不因 counter 不变变成 Stalled，Unavailable/过期返回 Unknown。
接口不修改 Node health、Task/Mission、资源、技能或 cancellation。没有显式配置
本契约的节点仍为 Unknown。Habitat 的可选本地导航 observer 已实现，见
[ADR-0058](0058-local-navigation-progress-observer.md)；这不宣称所有 vendor 已支持进度。

inner checkpoint v16、Controller wrapper v20 显式保存该事实，旧数据兼容为空。旧 binary
拒绝新 checkpoint，不会忽略新的证据字段继续恢复执行。

离线测试覆盖等待/阻塞/无量度、无进展/进展、source clock divergence、乱序、跨 owner、
跨 operation、跨 attempt、TTL、restart 和注册变化。真实停滞的准确率仍需独立受控验证。

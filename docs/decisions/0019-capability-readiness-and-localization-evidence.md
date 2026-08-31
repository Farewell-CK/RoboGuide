# ADR-0019：Capability Readiness 与 Localization Verification Evidence

- 状态：Accepted for Phase 1 Hardware Readiness
- 日期：2026-08-30

## Context

双机器狗实验出现了“进程和 WebUI 在线，但 Zenoh Router/ROS service 不可发现”的状态。
当前 Node Service 在启动注册时把全部 capability 固定为 `available=true`，Heartbeat 只报告
聚合 Node health；因此 Matching 可能选择实际无法执行某个 canonical contract 的节点。

当前 `spatial.localization.verify@v0` 在 Robonix `/api/load` 成功后只检查
`has_map=true`，随后 Catalog 记录 `Verified`。该证据没有证明 active map identity、
localization mode、pose quality 或 coordinate-frame 关系，不能作为稳定真机成功条件。

## Decision

### Capability readiness

- Process/local-system health 与 exact canonical capability readiness 是两种独立 observation。
- Node configuration v0.4 为每个 capability 声明固定、无 invocation 输入的 readiness
  workflow 和状态映射；任意超时、未知值或 probe failure 均 fail-closed 为 unavailable。
- Node Service 在首次 Register 前执行 readiness observation，并在事实变化时通过 Node
  Protocol v0.2 已有的完整 `RegistrationUpdate` 发送新 snapshot。gRPC wire 不新增 vendor
  字段，也不修改 Node Protocol 版本。
- Integration 只转换和持久化 observation；State 保存最新 registration/readiness projection；
  Control 继续通过唯一 eligibility predicate 检查 health、liveness、capability 和 contract。
- Integration projection checkpoint 升级为 v7，外层 Controller checkpoint 升级为 v8；旧二进制
  不得忽略 exact readiness 后以 legacy static-ready 语义恢复新 checkpoint。
- v0.2/v0.3 Node config 保持兼容，但其静态 `available=true` 只能用于开发，不满足 RT-G7。
  Phase 1 真机配置必须使用带显式 readiness 的新版本。

### Localization verification evidence

- Local adapter 必须产生结构化、可持久化的 verification result；自由文本 detail 和
  `has_map=true` 不构成强证据。
- 强证据至少关联 artifact selector/digest、Mission/Task/Group/Role、Node、execution/attempt、
  active local map identity、`localization` mode、pose-quality metric/value/threshold、map/odom/base
  frames、manifest anchor，以及 source-local observation time。
- Node Service 根据 deployment mapping 提取证据，并在任何远端 Catalog write 之前持久化；
  restart/finalization retry 只能重发完全相同的 evidence，不重新执行 Local How。
- Artifact data plane 接受结构化 evidence，使用 RoboGuide-local receive time 排序。State &
  Memory 保存和投影 evidence，但不选择 active map、不推进 Task，也不比较独立 source clocks。
- 强 evidence event 将 durable payload codec 升级为 `domain.EventPayload.json/v4`；读取保留
  v2/v3 兼容，但新 variant 不得伪装成旧 schema marker。
- 现有 `MapLocalizationVerified`/`has_map=true` 记录保留为 legacy smoke evidence；它不能满足
  RT-G8。强 Verified 状态只能由新版本化 evidence transition 产生。
- Runtime 只关联 execution identity 并等待 Node terminal fact；Control/Orchestration 不解析
  vendor quality 指标。Adapter 负责把 Local EAIOS evidence 映射为版本化 canonical shape。

## Consequences

- RT-G7 可以复用现有 RegistrationUpdate、Shared Node State 和 Matching 逻辑，不建立第二套
  readiness authority，也不把 probe 放入 Controller。Robonix deployment adapter 现用固定、
  只读的 ROS service discovery command 精确检查 mapping/localization mode service；Router
  缺失会表现为相关 contract unavailable，但仍需用全新真机故障注入完成 Gate。
- RT-G8 需要 Spatial evidence contract、Node journal 和 Catalog projection 的版本化演进；
  当前已加入严格 evidence v0.1 合同、Node journal 持久化接口、Artifact HTTP transition 与
  Catalog strong-evidence projection。真实 Robonix active-map、pose-quality 和 frame 来源仍
  未取得时，不伪造 vendor mapping 或宣布真机通过；Node completion extraction 与 Robonix
  mapping 留待硬件验证。
- Readiness 变化只影响后续 Control decisions；它不自动取消 Active execution，也不替代
  Runtime ambiguity detection 和 Recovery Decision。

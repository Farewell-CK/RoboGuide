# Two-dog Spatial Memory field report

本目录记录 2026-08-28 完成的两台 Lite3/Robonix 机器狗现场实验结论。可执行配置仍以
[`../distributed-spatial-memory-v0.1/`](../distributed-spatial-memory-v0.1/) 为唯一 source of
truth；这里不复制 Node Config，不保存 SSH 凭据、固定局域网地址或破坏性运维命令。

## 已验证

- Controller、Artifact HTTP、两台 `roboguide-node` 和两份本地 Robonix map adapter 能形成
  `MissionPlan -> Group -> TaskExecution -> Node execution -> Spatial evidence` 完整链路。
- A 构建/打包并发布不可变 map revision 后，B 能下载、校验 digest、导入本地 Robonix map
  目录并调用 localization load；A→B 正式 Mission 完整结束。
- B→A 的 publish、artifact transfer、Node staging 和本地导入路径均被执行。正式 Mission
  重试时目标地图已被先前的直接 adapter 调用导入，旧实现将同名目录一律判为冲突，因此
  没有把该次重试误记为正式端到端成功。
- 两台 mapping 容器中的 `/rtabmap/set_mode_mapping` 与
  `/rtabmap/set_mode_localization` 服务最终均实测可调用。

## 现场故障与边界

- 一台机器的 RTAB-Map 进程存在，但 Zenoh Router 未运行，导致 ROS 2 service discovery
  失败。手工启动 `rmw_zenohd` 后恢复。这证明进程/WebUI 在线不等于 capability ready。
- Adapter `/v1/health` 当时只探测 `/api/state`，会在 ROS mapping/localization service
  不可用时仍报告 `ONLINE`。per-capability readiness 尚未进入 Node Observation contract。
- 原实现无法区分“相同 artifact 已导入”和“同名不同地图”；正式 adapter 现以本地持久化
  artifact digest 处理可证明的幂等导入，缺少 provenance 时仍 fail-closed。
- localization verification 只检查 `has_map=true`，尚未证明 active map identity、模式、
  定位质量或两台狗的坐标系对齐。
- 实验使用既有地图 snapshot 验证传输与加载；没有验证自主探索、在线地图同步或地图融合。
- 现场观察到 Node session 断开和重新注册，当前只增加进程边界诊断，尚未形成 attempt/retry
  与自动 recovery 闭环。

## 架构结论

- Map bytes 属于独立 Artifact data plane；State 只保存 manifest、provenance 与 replica
  evidence，Node Protocol 和 Runtime checkpoint 不承载地图 bytes。
- Robonix 文件布局、`/api/save`、`/api/load` 和 ROS/Zenoh 排障属于 deployment integration，
  不能进入 Control、Runtime 或 canonical Node Protocol。
- 双向共享可以跨 Mission 发生；Memory revision 的生命周期不依赖某个 Task 或 Group 存活。
- SSH tunnel 只是现场网络绕行方式，不是 RoboGuide 架构要求。部署网络可达时 Node 和
  Artifact client 应直接访问 Controller listener。

## 未通过本实验验证

- capability-level readiness 与 Zenoh Router 的自动启动/监督；
- MissionAttempt/ExecutionAttempt、自动 retry、failure classification 与 fencing；
- active-map/pose-quality evidence、坐标标定与跨地图融合；
- 动态 MapId/Revision output binding、实时同步、删除和 GC；
- 认证、授权和传输安全。

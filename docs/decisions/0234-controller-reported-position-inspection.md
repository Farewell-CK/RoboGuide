# ADR-0234 Controller 只读报告位置检查

状态：实现，并通过[软件与现场验收](../development/acceptance/2026-11-06-dog2-controller-position.md)。

## 背景

狗2已有统一 Connector 的 robot-state，经正式 Node State export 进入 Controller。
之前的返程由外部操作端查询 Robonix 并手填起点，不是 RG 自己保存和召回起点。
本轮首先验证从 RG 查询位置。原 `/v1/state` 的传感器具有独立年龄，但 Mapping
缓存没有年龄，持续收到 Node 外层记录不能证明这个缓存仍新鲜。

## 决策

同一个 Connector 在既有 `/v1/state` 响应中补充 Mapping HTTP 响应的接收年龄、可用性、
解释和 snapshot_read_at_ns。原 received_at_ns 和原值不变，重复读取不刷新它们；
缺失、超时、时钟回拨或超过十秒保持 unavailable/stale。没有创建新服务或新动作接口。
原 `lite3.robot-state/v0.1` 的既有字段保持；这些新增字段只限定报告数据的缓存年龄。

`robonix_task.position` 提供独立诊断 CLI，通过现有 mTLS Controller GET State 和
inventory。它不调用 Robonix、不发送 Mission、Execute、Cancel、地图加载或保存请求。
指定节点、LocalSystem、channel、地图是显式输入；来源冲突、不可达、外层 TTL 过期、
内层响应过期、地图不匹配或非有限坐标均不返回可用位置。

输出采用 `roboguide.reported-position-inspection/v0.1`，状态仅为 reported 或 unavailable。
保留原查询响应、source epoch、sequence 和两个层次的年龄。只把同一 Controller 的
保留时间加到发布端报告的年龄，不比较不同主机的绝对时钟，也不伪称已测网络传输年龄。

reported 不等于定位质量已通过、地图 revision/共享 frame 已确认、World grounding 已
接纳或已获得运动许可。Mapping WebUI 响应未提供原位姿测量时间，不能以 HTTP 新响应
证明实际定位测量新鲜；这些缺口单独保留，不用推断或默认值补齐。

## 边界与验证

本变更不改 State/Control/Runtime、Node wire、持久化格式或权限，不改变 Task 完成、
资源释放及未知任务处理。诊断结果不写入 World State，不保存起点，不接入 MI。
起点记忆与 MI grounding 需另行明确来源合同和实体/地图关联，不能只改 object class。

软件覆盖双层过期、持续外层发布、地图错配、来源冲突、节点不可达、坐标无效、时钟
回拨、HTTPS GET 和有界响应。现场检查实际 Connector → Node → State → 查询工具，
保持零新任务，并验证 Node 停止后过期拒绝及恢复后的新鲜记录。

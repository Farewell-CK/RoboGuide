# ADR-0246：分区巡检的接入、物理状态与独立到达门禁

状态：实施与无运动验收，运动和故障注入另行授权。日期：2026-11-10。

## 决定

双狗主线依次验证分区巡检、共享通道、停止后接替及仅通信失联、真实结果交接、相邻
路线与持续相对位置协作。原照片数据接力保留，作为辅助数据面证据；资源竞争和恢复
是独立维度，不借 coupling_mode 名称宣称完成。

首轮分区固定狗1去茶水间门口、狗2去王老师办公室门口。使用同一个 Mission、两个
Actor、一个 independent Context 和硬性的 distinct-physical-entities。原物理实体 registry 显式
给出 `entity-lite3-dog1/2` 与已认证 Node 的路由，Actor 绑定到该原实体；声明不构成
硬件信任、定位验证或跨实体接替许可。导航仍只通过 `task.delegate@v1` 给各自 Robonix。

两狗目标必须各有本机原地图/Scene 来源。保存地图摘要相同是有用的来源证据，但不能
单凭同名地图复制实时定位、区域绑定或规划结果。缺失 Scene、原目标或新鲜姿态保持
阻塞，不填旧坐标，不静默加载/切换地图。

## 物理 World 报告

旧 robot-state Node 通道继续承担部署/健康/运行事实。现有 Connector 可显式启用
`--physical-entity`，单独投影 `roboguide.world.robot-physical-observation/v0.1`：只含
本机报告的姿态、电量、地图标签/报告位置及明确的未知字段。MI 显式接纳这个 schema，
不放宽 World-only 过滤，不把整个 Node payload 改名为 World。

该报告保留 chassis 聚合 age、原接收标识及较短 stale_ms。新外层 State 不刷新旧测量；
消费者还必须扣除 Controller 缓存和规划耗时。Mapping HTTP 接收时间与 pose measurement
time 不同，后者、质量和不可变地图版本仍未知。报告不授予 arrival、device stop、
shared-frame 或运动权限。

`roboguide.world.physical-entity-reference/v0.1` 沿既有精确实体引用合同单独发布，仅含
配置授权的 entity_id，且部署须先保存并接纳原物理路由。身份引用与物理测量不是一回事。
每个节点继续使用同一 Node、Connector 和原认证会话，无新增守护进程或协议版本。

## 到达、拍照与汇总

`patrol.py` 生成只读、尚未提交的导航模板并检查真实准备缺口。两个导航 Task 都声明
verifier-evidence，原本地执行完成进入 AwaitingSatisfaction；首轮采用明确独立的
现场到达及停稳确认，按原 plan digest、完整当前 attempt、实体和目标来源绑定。
Pilot done、Nav2 SUCCEEDED 或超时不单独满足此谓词。后续自动源必须另验收原时间、
定位质量和任务归属，不能把人工确认重命名为自动定位验证。

各自拍照必须依赖自己的到达 TaskSatisfied，汇总必须依赖两份原照片及其真实
Artifact/来源回执。当前 Robonix 拍照回执/汇总接线未被证明时，不生成虚构的可执行
photo/summary Task。既有直接 ROS 只读相机能力只算辅助接入，不能取代本轮要求的
MI→RG→各自Robonix任务链路。

严格模板验证默认 motion_authorized=false，禁止提交；当前新鲜度和本轮现场授权
只在实际执行前检查，不能用一份早前预检代替。模板不是生产通用自然语言语义证明。

## 共享通道和接替边界

门口互斥必须有真实几何冲突区域、门外等待点、通行许可及任务归属的离区证据。
固定延时、接口返回、静止、导航完成都不等于清空。缺离区证据保留占用。

已部署 task.delegate 只声明 no-effects，不能证明运动副作用停止。现有 Robonix
取消受理/活动计划消失和内存 run 查询不足以给出整机停止证明，原 run 关联、持久
查询、终止与设备停止证据仍需补齐。仅断 RG 通信保持 Unknown 和占用，不能自动
派第二只重复执行。RG 核心 Actor takeover 的应用入口、授权及适用 Actor 限制另行
接线验收；首轮固定物理 Actor 不能隐式变成可接替 Actor。

持续保持相对位置还需要原共享坐标/状态、关系、直接 peer channel 和 Robonix 本机
协调支持；RG 不承担高频运动控制。相邻路线并发导航不能替代这一验收。

# 双狗协作准备清单：仅 Thor 上电

日期：2026-11-11。现场前提：用户报告两底盘电池充电，仅 Thor 有电。
本轮只有中央 Controller GET、MI readiness GET；没有机器人任务、证据发布或配置变更。
以下分别表示当前已核验的部署、软件代码支持，以及未取得的物理事实。

## 现场只读核验

VERIFIED：Controller、MI ready；unknown_attempts=0、allocations=[]；lite3-dog1、
lite3-dog2 均 Reachable。两Node当前可用合同均不含 task.delegate，不代表原登记消失，
也不据此诊断底盘故障。主机在线、操作支持声明和操作实时就绪分别判断。

VERIFIED：两狗独立 Node 身份均 active、租户 local；physical registry revision1 将
entity-lite3-dog1/2 分别路由到 lite3-dog1/2。软件身份不构成硬件认证。

VERIFIED：onsite-patrol-arrival revision1 enabled；独立 principal rg-patrol-verifier/local，
只覆盖狗1茶水间门口、狗2王老师办公室门口的原精确目标谓词，属于人工现场到达确认。
当前无姿态目标源、共享门口离区源或设备停止源被登记在该 verifier registry。

VERIFIED：resource-topology registry=null，physical_occupancy=unavailable，无reservations。
现有 dog1/dog2-pilot-task-slot 是本机执行槽，不是共享门口资源。

VERIFIED：狗2已声明 photo-captures 和 reported-origins Memory provider，狗1 provider列表
为空。现有 Artifact 权限包含狗2有限selector的写入和辅助照片消费者；这不能推导狗1
可拍照/上传或通用动态巡检照片队列。memory-authorizations=null 不否定已经验收的受限
Global Artifact读取路径，也不意味着 prepared-import/use-lease 已经具备。

## 什么需要部署登记，什么随 Mission 声明

|层次|准备内容|时机|
|---|---|---|
|部署身份|独立Node证书/租户、物理实体路由、独立服务发布身份|接入/变更时登记，非每次任务|
|可执行能力|原Robonix任务入口、支持profile、实时readiness、结果及副作用/恢复合同|Node正常声明与更新，不逐个注册站立/趴下动作|
|地图与数据|各狗原Scene目标来源、地图修订/摘要、需要时的共享坐标证据；照片provider及受限内容权限|来源接入和部署授权；不能用同名地图补跨机frame|
|共享资源|已部署资源、共享冲突区域/坐标锚/安全裕量、通行资源绑定|实物定义和软件绑定验收后发布topology|
|验证来源|准确的到达、reported姿态或离区谓词、发布主体、租户、时效、原attempt绑定|对应生产者真实实现和验收后登记|
|任务分工|Actor、Context/coupling_mode、DAG、目标、依赖、资源要求、验收声明|每个Mission的原计划，不是独立注册模式|
|运行期权威|资源选择、Commit/Bind、attempt、恢复/接替授权、执行和证据回执|原正常事务产生，不手工伪造或预登记未来成功|

## 实验与真实缺口

### 1. 分区巡检：优先

一个Mission，两个固定物理Actor，一个independent Context及distinct-physical-entities。
各自导航→各自到达TaskSatisfied→各自拍照→依赖两份原照片的汇总。

已具备：原Node/实体路由、Scene World接入、固定目标人工到达源、正常Commit/Bind。
未完成：两狗均经各自Robonix执行拍照、可持久查回本次图片结果、原Artifact finalize/
Memory manifest、受限汇总执行者和汇总结果接线。旧狗2 ROS采图→狗1摘要检查是辅助
证据，不能直接替代要求的 MI→RG→各自Robonix 巡检链路。

底盘断电时可补原本机接口/结果合同、producer/consumer/汇总DAG和权限草稿，使用
独立软件Node验错图/错attempt/缺图/重复/Unknown/重启，不需要先登记虚构photo能力。
需要相机/雷达/本机原服务是否供电，仍须按真实readiness，不能从Thor上电推导。

### 2. 共享门口：需要资源与离区接线

准备门的物理ID、冲突区域、各狗门外等待点、通行Task边界、离区条件和证据来源。
坐标、共同frame/anchor revision及安全裕量未经确认均留 UNKNOWN，不造数。

已有 /v1/resource-topology 原管理员事务；topology引用已有资源和Node owner，不创建
资源，也不证明真实占用。必须补准确的通行阶段资源关联，不能让Space units=1
错误选到pilot-slot；当前resource_class只用于Divisible Compute，不能直接套到门口。
也不能给所有task.delegate永久附加门口资源，导致无关站立/导航都占门。

离区证据缺失时保持原占用。超时、延迟发指令、Pilot done、到目标或停止都不能单独
证明门已清空。共同物理区域映射与真实通行效果需后续现场验收。

### 3. 故障接替：需要实现，非仅登记

当前task.delegate部署assurance=no-effects，只证明确未发送副作用的原任务，不证明
运动停止。须接通Robonix对原attempt的持久终态/设备停止证据、适用的恢复/重复授权。

Control已具备ActorTakeoverAuthorization，但当前apps没有实际调用入口；要补应用
命令、证据校验、持久化及隔离验收。每次接替仍重新匹配→Commit→Rebind；已完成
结果保持。原固定physical Actor不可隐式换狗，需另设计允许接替的Mission Actor。
只失联保持Unknown和占用，不立即派第二只重复执行；到达源不充当设备停止证明。

### 4. 顺序巡检交接：无需注册协作模式

每个Mission使用真实结果依赖及sequential-handoff；冻结原Task/attempt、Artifact/
Memory引用，按租户授予消费权限。既有照片接力是软件/数据平面先例。
要验证下游消费本次上游产物；只是先后启动或固定sleep不构成交接。

### 5. 持续相对位置协作：当前实现仍缺

相邻路线同时导航可先用独立Task，不能称保持队形。
真正协作还需要同一原坐标依据、共享pose/velocity来源、执行关系、双端peer readiness
及Robonix本机协调合同。当前SupportedMechanismProfile只支持RequiresActive和
SharedSpatialReference，RelativePose/RelativeDistance尚在执行前拒绝；注册名称不会
补出执行能力。RG承担关系、资源和生命周期，Robonix承担高频本机协调与即时安全。

## 只开 Thor 时的准备顺序

1. 优先补齐分区巡检完整DAG及本机照片/Artifact/汇总合同，保留旧辅助接力。
2. 完善状态不可用、错实体/目标/attempt、图片缺失和重启的无运动软件验收。
3. 给姿态目标观察器做独立身份和生产接线准备；只验证reported standing/down，
   不把姿态或静止解释成设备停止/接替授权。
4. 准备门口资源与离区合同；区域、等待点、frame及物理停止源留明确待验项。
5. 故障接替先补应用入口和停止证据；真正编队在本机协调能力具备后推进。

既有Controller、Node、统一Connector和MI工作器继续复用；不新增每条链路一个守护
进程，不降低TTL，不修改历史或以软件成功替代现场。真正运动、故障注入另确认当轮
现场条件及范围。地图/几何变更需新修订，不改写原已接纳任务。

## 可核验来源

- ADR-0246 分区巡检接入、固定Actor和停止/恢复边界。
- ADR-0248 独立人工到达源及生产注册/验收。
- ADR-0245 既有无运动照片handoff范围。
- ADR-0209 原资源拓扑/几何/日历权威。
- ADR-0070 Control Actor takeover与应用入口缺口。
- core/domain/src/resource.rs：Space选择不能套用Compute resource_class。
- core/orchestration/src/mechanism_profile.rs：当前执行关系白名单。

中央只读证据：deployment-readonly-1794388233227352110.json
Windows：C:/workspace/DEAIOS/lab/rg-collaboration-preparation-20261111
ThinkPad：~/rg-lab/collaboration-preparation-20261111
原文件SHA256：7a545c2d426b43b5749888d3eb6fe01d31f8e884af048909476bf98fc0d6ff48

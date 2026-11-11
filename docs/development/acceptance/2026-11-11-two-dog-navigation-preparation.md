# 2026-11-11 双狗规划预演与 Scene/位置模拟

本轮底盘电池充电、仅Thor上电。用户授权准备、真实MI规划软件验证及模拟Scene和
报告位置。真实流程停在规划/待审批；没有批准导航或发布到达正证据。模拟JSON独立
保存，不写真狗State、不调用Robonix/底盘、不声称已验收真实定位、行走或避障。

## 两次真实 MI 结果

1. Request `request-356b31c1e0b547d4ab03f270ce1194aa`，候选Mission
   `mission-54b2f619967140cd828c64fc78fb13c5`：固定两Actor、independent及distinct约束
   正确，现场前置条件完整；精确谓词却是REQUIRED_BUT_NOT_SUPPLIED，占用要求为空。
   Reviewer因缺到达合同及当前Scene来源拒绝，Failed，原请求保留、零Execute。
2. 从实际认证registry/config准备显式部署能力目录，带精确谓词和来源摘要；根据两狗
   实际Node required_resources，声明本机Space1/task执行槽。第二次Request
   `request-98a39944f7e24c509c5419ab87372ca0`，候选Mission
   `mission-31f0e6065b5e4c60b149cb5d8fd12ddd`：精确谓词、执行槽、两狗分工及现场条件
   正确，真实provenance冻结新目录；Reviewer approved，AwaitingApproval，未批准。

独立复核仍拒绝把第二份草稿用于真机：expected_effect被扩展为两狗最终同时共存停稳，
比单路独立到达谓词更强；当前人工publisher要求expected_effect与本路predicate字面
相同。没有修改原草稿、替换其验收依据或放宽publisher。部署快照v0.2补充完整的
supported_task_satisfactions及独立目标边界，实际publisher guard回归覆盖正确描述
通过、强加共同终态拒绝。最新目录已发布，旧待审批草稿只保留作软件证据；底盘恢复
后须重新捕获真实状态并生成/Review新Request，不能直接批准旧草稿。

目录是受信部署声明，不是自动实时registry发现，也不是World或物理测量。helper原
目录/中间输出摘要、追加槽规则后的文件摘要及MI规范化摘要分别保留。审批、模型、
MI数据库、Controller/Node身份未改。当前MI目录规范摘要为
`sha256:e3f14af12b4aae542287122560ddc2cca2456c18b1688f14c9b5e758f509276b`。

## 原计划导出：真实失败保留并修复

初版arrival_plan把Core内部source_plan_digest与公共MI JSON规范摘要比较，单测误建
同算法夹具，真实旧Request只读复验失败。Core哈希内部domain::MissionPlan；二者
并非同一表示。修复为严格验证原admission wire SHA，核对同Mission受信tenant投影，
将Core token作为opaque qualified token保留，不伪称离线认证或重算Core权威。

复验真实旧Request `request-a9503f375cde49e3b38eccd6a908bb42` 导出4039字节，wire SHA
`d8e0e5ad70371c7f56b21a9887fa93b2f1d524da87a84b655ff7502cfa4e1456`
与原admission完全一致。没有重发旧动作或证据。导出不证明当前revision；publisher
仍核对实际当前revision、AwaitingSatisfaction及全部attempt。新文件0600，不覆盖。

## Scene/报告位置软件模拟

真实SceneTargetObserver、物理投影、位置/姿态读取、MI grounding reader及准备转换器
处理隔离夹具；仅替换原源IO、时钟和Controller JSON传输。没有socket、真实Controller/
Node过程或mTLS，本轮不是传输或鉴权验收，既有进程验收另行保留。

九种情况：正常、错地图、旧报告位置、采样中切图、摘要损坏、缓存过期、来源故障、
重启未采样、缺epoch。正常有两个独立目标、六条World证据、零MI/source gap及两份
reported位置；异常不借用另一狗数据。Mapping响应年龄不是原定位测量时间，质量和
测量时间仍未知，localization_verified=false。

首轮模拟因夹具不符合真实MI envelope失败：缺confidence_millionths、物理通道/TTL
不符。原失败保留，只修夹具，生产检查未降低。source-r3冻结后九例通过。

模拟发现旧patrol准备器未把Scene和Mapping地图一致性统一成导航门槛。新增纯函数
navigation_preflight，在各自原目标/地图、报告位置、姿态、来源注册及原引用之间
联合检查，扣Controller留存和本地总读取预算。姿态较短native窗口独立扣费，不能
被较长位置/Scene预算盖过。只判导航准备，照片/汇总缺口另列，不阻塞本轮导航。

新预检是现有集成模块内的执行前检查，不新增守护进程或通用Core准入权威，不证明
定位质量或授予运动。现场审批前需由中央工具用认证Controller最新输入调用；不能
宣称MI/Controller所有入口都已自动接线。模拟registry/config由正常目标仅冻结一次，
故障后不重算授权。最终正常应ReadyForOnsiteAuthorization且motion_authorized=false；
八种故障应Blocked。实际最后运行结果及摘要见证据索引。

## 原始证据

Windows：`C:/workspace/DEAIOS/lab/rg-two-dog-navigation-preparation-20261111`。
ThinkPad：`~/rg-lab/two-dog-navigation-preparation-20261111`。
各版source、两份真实Request、原失败、发布与九例原JSON均保留，私有凭据不导出。
原MI源未改，真实模型通过不替代硬件就绪或人工publisher的精确合同。电池装回后须
用新鲜Scene、reported位置、本机能力和当轮现场条件重新预检与授权。


最终source-r4：220项集成测试通过；统一预检的九种模拟案例全部通过，正常为
ReadyForOnsiteAuthorization且motion_authorized=false，其余八例Blocked。
再用相同新入口读取真实RG，返回Blocked：两狗无当前Scene/报告位置/姿态，
task.delegate不可用。源登记仍在；当前目标来源不可核验不等于登记被删除。
55个原execution attempts逐值不变、allocations=[]、unknown=0、verifier receipts=[]。
无新增动作或到达正证据。v0.2部署声明之后尚未生成第三个真实模型草稿，不能宣称
新的最终现场计划已经通过模型/审批；必须重新规划/核对精确goal再实际批准。

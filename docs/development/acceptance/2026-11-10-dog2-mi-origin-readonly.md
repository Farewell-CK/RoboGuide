# 狗2真实 MI 起点记录、中央 Memory 规划与原请求重传

日期：2026-11-10，Asia/Shanghai。状态：本轮无动作实验通过；返程草稿未审批或提交。
未验证底盘定位质量、姿态动作、导航效果、多节点协作或 24 小时稳定性。

## 实验运行位置与恢复

用户要求继续推进现场实验。为串行使用 ThinkPad，本轮结束独立软件双 Node 长跑，
保留 1685.039 秒、337 次采样的原进度与中断记录，不将其记为 24 小时通过。
实验使用已验收的 R7 预构建 Controller 与 R9 MI 源码，通过原 Controller/MI 单元
和原 Node endpoint 运行。原 Controller 拉起 MI、MI 等待 Controller readiness 的关系保留。

运行数据库和 Artifact 是完整备份恢复出的副本：

- Controller：`~/rg-lab/data-governance-20261110/field-r8-restore-gate/restored/stores/controller/owned.sqlite3`
- MI：同一 restored 下 `stores/mission/mission-requests.sqlite3`
- Artifact：同一 restored 下 `stores/artifact`

原现场 SQL、MI SQL 与 Artifact 根未覆盖。原 1465461 事件与 651747 命令记录随副本
保留；该实例是有界实验运行，不宣称已完成长期发布门槛。狗1未被操作或修复；它原有
Node 自行重连不构成本轮任务。唯一执行者为狗2。

实验只调整配置：MI 使用已有 v0.10 Mission 合同与 origin capability catalog，保留
origin capture / task delegation 两条无条件审批规则。只增加本次 Memory ID 的
Artifact writer 授权，原授权文件保留。未改产品代码，也未升级或重启狗2的接入服务。

初次新授权未生效：原 EnvironmentFile 覆盖了 systemd Environment 中的新路径。
本地起点 Task 已完成，但中央尚未发布，未把两者混为成功。随后用末层实验 envfile
纠正并核对实际进程环境；重启实验 Controller/MI 后原 Task 没有重跑，中央发布成功。
该次全历史完整 Controller readiness 测量为 0.144796 秒，MI 为 1.501815 秒，均包含
systemctl 调用及探测时间。只有一个样本，不能代替 30 次 P95。此前运行快照中
Controller RSS 74736 KiB、MI RSS 34168 KiB，不能代替 24 小时内存验收。

## 真实 MI 起点任务

- Request：`request-1b217690638241e8ae5ebce6f75046c8`
- Mission：`mission-b2863227e36b463e97ca0b1080272a18`
- Task / Role：`record-origin` / `capture-role`
- 原稿 revision：1；digest：`sha256:cdd4d038fca3463b3ae216431414dbee37d3e1a8c4ff83ddc12eef2a2ea0e15e`

入口是 MI 的 `POST /v1/mission-requests`，原文不含位置坐标。真实模型执行解释、
规划与 Review，然后进入 AwaitingApproval。操作者核对原 revision/digest 后，仅批准
一个 `spatial.origin.capture@v1` Task。能力、参数、一个 divisible compute 单位及
`spatial.origin-slots/v1` 资源类均按原稿核验；没有 `task.delegate`、Pilot 或地图加载。
Task / Context 为 independent；单 Task 单 Role，不是跨节点协作。

Controller 原事件保留 CandidatesMatched、TaskSchedulingSelected、ProposalCreated、
PlanCommitted、ExecutionGroupBound、MissionActorBound、TaskExecutionCompleted、
TaskSatisfied 及资源释放。原事件序号和原时间不重排；该 Task 最终 Completed。

唯一 attempt：
`attempt-46:group-mission-b2863227e36b463e97ca0b1080272a18-13:record-origin-12:capture-role-1`

## 中央发布与实际字节

- Memory：`dog2-origin-mi-readonly-20261110-01/r1`
- Provider：`reported-origins`；schema：`roboguide.reported-origin/v0.1`
- Global / exchangeable；1310 bytes
- SHA-256：`0cb0e781c129538feca9d37f7e672ce7618b54e9a09dc40066a2414664a75be8`

中央原 bytes 的来源 Mission/Task/attempt、Node、manifest、digest 和长度一致。
内容固定 `motion_authorized=false`、`localization_verified=false`；地图为报告标签
`1101_visual_dog2`，map revision 与 pose measurement time 均未知。Mapping HTTP
响应较新不等于物理定位经验证。底盘姿态和电量为过期报告，未用它们声称当前状态。

## MI 从新 Memory 规划返程，停在审批前

- Request：`request-3529cdd10c604a2ba36309ca82f14198`
- 待审批草稿 Mission：`mission-52553da2bc01460f8880ca8123e24354`
- revision：1；digest：`sha256:66f52cdd586864099437997a442c80ce59f170b6a8c9a2aa245346ff4fec3a73`

第二个自然语言请求仍不含坐标，仅引用新 Memory。MI 的原 grounding-context/v0.4
保存该 selector、原 1310 bytes、digest 和 `VerifiedReportedOrigin`。原草稿的目标
坐标来自该新内容，而非旧起点或操作者手填；Role 的 Memory requirement 也引用新 r1。
当前 World State grounding 数量为 0，草稿保留实时姿态、地图兼容性和定位质量待核验。

请求停在 AwaitingApproval。Controller 查询该草稿 Mission 为 404、attempts 为空；
导航没有审批或提交。记录只验证中央 Memory 驱动真实规划，不证明物理返程能力。

两个 Request 各有 3 次实际 provider 调用，requested_model/observed_model 均为
`gpt-6.1-sol`、error_type 均为空。provider 耗时分别为 93465 / 130570 ms；模型耗时
与人工审批等待、Controller 调度和本机观察耗时分别保存。

## 重传与最终结算

按原身份、原正文重传起点 create 和 approve 请求，分别返回原 201/200。
原 Request 的 provenance 仍为 11 行，原 Mission 仍只有同一个 Completed attempt，
没有新增模型调用或记录任务。重启实验服务也未重复该 Task。

最终 Controller ready=true、degraded=false、Unknown=0、allocations=[]。
狗2 Connector Pilot journal 仍为 36 runs / 641 events，完整原行摘要保持一致。
本轮没有向 Robonix Pilot 下发站立、趴下、导航或其他任务。

## 证据

ThinkPad：`~/rg-lab/data-governance-20261110/field-no-motion-experiment-20261110`。
Windows：`C:/workspace/DEAIOS/lab/rg-data-governance-20261110/field-no-motion-evidence/`。
23 份公开证据逐文件核验；压缩包 16190 bytes，SHA-256：
`f57bb99fdfd521fc11d5edaf5bea222f636ae3677a9b8ac2eb21307c69d8c4f2`。
证据包含原请求/原稿、审批、模型调用元数据、事件、Memory 原 bytes、实际环境路径、
原请求重传及最终结算，不包含密钥或私有环境文件。

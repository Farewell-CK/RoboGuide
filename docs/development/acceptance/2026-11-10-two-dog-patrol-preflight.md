# 2026-11-10 双狗巡检接入与无运动预检

本轮权限：准备、接入、无运动软件验证。未批准或下发导航、姿态、拍照、地图切换或现场
故障注入。用户中途因低电关闭底盘，随后重新给两台 Thor 上电；这些变化不构成运动授权。

本轮完成 RG 接入与拒绝路径验证，**双狗分区巡检尚未通过**。拍照接力的原提交、目录、
Mission、失败与成功证据全部保留；它是辅助数据面验收，不代替分区巡检或故障恢复。

## 代码与部署

分支 `codex/two-dog-patrol-preflight`，基于 `a699ab8e`。没有覆盖手机工作区或旧照片接力分支。
Controller 继续使用既有 R7 程序与原 restored SQL/Artifact；没有启动第二个现场 Controller。

- 狗1在既有 Node/Connector 上补入 `task.delegate@v1` / `local-task-v1`，保留图片消费者。
  使用新 Pilot journal 以保留原 disabled-profile journal；未调用 Pilot Task。
- 两狗各增加物理实体引用与过滤后的物理 World 报告；原 Node robot-state 通道保留。
  显式实体为 `entity-lite3-dog1`、`entity-lite3-dog2`。没有增加常驻服务。
- ThinkPad 经原管理入口发布物理 registry revision 1，命令
  `physical-two-dog-patrol-20261110-01`。原回执及冷恢复文件已保存，Controller 重启后原 registry
  一致、readiness=true、Unknown=0、allocations=[]。
- MI 使用冻结 `source-r2` 的新读取器，显式接纳两个 World schema，保留 Memory admission、
  原数据库、模型配置及所有既有审批规则。所有当前目录操作在本轮准备请求前均受审批约束。
- MI 扣除 Controller 留存、GET、本地读取以及后续 Memory/语义/规划证据读取耗时；过期的
  姿态、电量或 Mapping 响应拒绝进入冻结上下文并产生 gap。未知仍为未知，不改原值或 TTL。
  电量时效仍来自 chassis 聚合报告，不证明单独电量采集时间；动作前还必须重新核验。

部署目录：`~/rg-lab/two-dog-patrol-20261110`（ThinkPad 与各 Thor）。既有服务增加
`97-patrol-preparation.conf`；旧目录及原 drop-in 均保留。ThinkPad `before/` 保存本次配置和
原 SQL 备份。Controller 数据库备份约 2.75 GB，复制耗时超过 SSH 客户端等待上限；后续
  只读检查确认备份文件、发布回执、切换结果已落盘，没有因等待超时创建新命令或覆盖现有任务数据。

部署中曾漏打包 vendor/provenance，狗1新 Connector 因此启动失败；补齐原协议 vendor 后，
实际 Node 配置验证与两狗服务启动均通过。原目录未丢弃。该故障不计作设备故障接替实验。

## 实测与证据时序

1. 初始两狗在线。狗1仅暴露 host/image 能力；狗2已有任务委派。停止合同仍为 no-effects。
2. 原生只读预检确认两狗保存的 `1101_visual_dog2` 文件摘要一致。rtabmap.db SHA256 为
   `d5e1e95e69e1442cbcc555bc898f728ab033b6b402ac0c97598af103ea98d9b1`。
   同名或同摘要不能证明当前定位、共享坐标或当前地标绑定。
3. 狗1实时 Scene 能返回七个区域和目标；茶水间 `anno.2cb4c8ba`，王老师办公室
   `anno.4c5ac08a`。狗2 Scene 无进程、HTTP 拒绝连接、三个只读合同 provider_count=0。
   其先前退出码137不足以认定 OOM。没有自动切图或拿狗1目标替代狗2目标。
4. RG 接入切换后两狗 Node/Connector 均 active，原生委派 readiness=READY，实体引用分别
   正确。该次两狗均报告 down、电量15%，Mapping unavailable；只算当次原报告，不是
   后续开关机后的当前状态，也不是停机、遥控器或阻尼证明。
5. 用户关闭底盘并给 Thor 重新上电后，中央能读到四条物理 World 记录。当前缺失的姿态
   或尚未恢复的原生委派保持 unavailable；中央接入存在不等于本机执行器已经就绪。
6. 实际部署的 MI reader 捕获四条 World evidence、两条 Memory metadata、零 acquisition gap。
   这证明读取接线，不能把其中 unknown 或 unavailable 项解释成定位/姿态通过。
7. 两导航准备模板经真实 Controller `/v1/missions/validate-plan` 返回 HTTP200、decision=blocked，
   `verifier.source_unavailable`。本地模板拒绝无本轮授权的 submission 检查。
   模板函数不是生产动作门禁；不能据纯函数测试宣称所有执行入口均已强制本轮条件。
8. Thor 重新上电后的最终只读核查：两狗 Node/Connector 当前 active，但两狗委派均
   UNAVAILABLE。狗1 Atlas可连、Query成功但没有 Liaison submit provider；狗2 Atlas端口
   50051拒绝连接。当前不发布 `task.delegate` 与这些本机未就绪事实一致。两狗姿态/电量
   unknown、Mapping unavailable，旧15%报告已失效。Connector自动重启计数分别9、11；
   严格过滤本次boot的RG日志没有可读条目，原因未查明，不能宣布RG不存在缺陷，也没有
   根据猜测修改或启动Robonix。Liaison构造函数本身没有RPC，不支持“构造连接失败”的推断。

自动审批审查拒绝了通过官方 `rbnx start` 启动狗2 Scene 的操作，返回 `blocked by policy`，
未提供更具体理由。该调用没有执行，也没有换入口重试或绕开；Scene 恢复仍为阻塞项。

原生预检读取进程/启动信息时，模型与语音服务凭据进入了工具日志。交付证据已脱敏，
不在此保存其值；相关凭据需要轮换。后续只读采集改为字段白名单，不读取整段原生 argv/config。

## 各阶段缺口

| 项目 | 已有支持 | 尚缺的真实证据或接线 |
| --- | --- | --- |
| 分区导航 | 同一 Mission、两个固定物理 Actor、一个 independent Context、distinct-physical-entities；两狗原生任务委派接入 | 开机后的当前 Scene/地图/目标、可用定位及底盘状态；登记并接线独立到达及停稳源 |
| 各自拍照与汇总 | 原生 snapshot 合同已读，旧数据接力保留 | 原生图片采集时间/帧龄、到达后新拍、原 attempt/call/provider/result 与 Artifact 关联、真实汇总操作 |
| 共享通道 | 现有资源预留和保守占用语义可复用 | 冲突几何、门外等待点、通行权、全机离区证据；不能靠延迟或 API 返回释放 |
| 故障接替 | 原任务/Unknown/占用、核心恢复语义 | 适用原任务的持久停止终局、恢复授权、Actor takeover 应用入口与 Actor 约束；不能将 no-effects 当运动停止 |
| 顺序巡检交接 | 保留已有真实照片结果接力 | 巡检结果成为后续真实 Task 的输入与前置条件；先后启动不能替代它 |
| 并排行走 | 已区分相邻路线并发与持续相对位置 | 后者尚无已验收的共享 frame、关系、peer channel 与 Robonix 本机协同能力 |

原生相机当前在调用时生成 header.stamp，只缓存最新帧，不返回采集时间、帧龄或持久图片
查询。因此仅有相机接口不等于“到达后新拍”。现有 Executor 对 MCP 非 Text/structuredContent
的处理也需验证。Pilot journal 每原事件256 KiB上限须明确处理较大 JPEG，不能截断后称原图。

## 验收结果

- ThinkPad 独立测试目录、冻结源 `source-r2`：125 项接入测试及 670 项 Mission 测试通过。
  其中物理读取目标文件40项覆盖过期/时钟回退、外层续鲜、电量独立预算及冻结前 Memory/
  最后规划读取延迟。严格类型、Ruff、格式、函数文档及 diff 检查通过。
- 两狗真实旧 ARM Node 二进制分别 `--validate` 新配置通过；服务实际启动验证完成。
- 真实中央物理 registry 发布、重启恢复、World/Memory 读取与计划拒绝路径通过。
- 此轮没有巡检 Mission 接纳、分配、Commit/Bind、attempt、本机执行、到达、照片或资源释放
  证据。`two-dog-patrol-navigation-preparation-20261110` 只是未提交模板 ID。
- 真实 MI 请求 `request-052b65931483485da6ba850978ae415d` 识别了两实体分工与当前物理未知，
  但 Planner 生成占位任务并把准备阶段禁令写进未来执行目标；Reviewer 拒绝该稿及当前
  地标/到达/照片缺口，终态为 **Failed**，不是合格规划或 AwaitingApproval。原失败保留。
  其候选 `mission-ccbd50419e7b47049765615107e92a90` 在 Controller 查询为404，未接纳。
  语义已明确而部署证据缺失时，不应让用户回答系统技术问题，也不能为生成计划补造任务。
  后续须补合同，再验证真实 MI 产出；没有把只读模板充作模型结果。
- 原 MI 请求及物理 registry 命令重传逐字段返回原回执，未重调模型、创建新 Mission 或委派。
  原命令/结果分别在 `mi-patrol-command.json` / `mi-patrol-result.json`，重传结果在
  `receipt-replay-verification.json`。未发送批准；回读 Unknown=0、allocations=[]。

本轮执行流水（不得把未创建的阶段记成通过）：

| 阶段 | 实际结果 |
| --- | --- |
| Request | `request-052b65931483485da6ba850978ae415d`，原指令和响应已保存 |
| Plan | 原模型草稿摘要 `sha256:c89c830359a41219235341108315a6dcb504b5e6c75f2311c583fa49d423985c`，Review拒绝 |
| 分工意图 | 狗1→茶水间门口、狗2→王老师办公室门口；两个物理Actor、一个independent Context |
| RG分配／Commit／Bind／attempt | 均未创建，Controller未接纳该候选Mission |
| 本机执行与物理证据 | 未开始；没有到达、停稳或拍照证据 |
| 资源 | 没有本轮预留或释放事件；最终空占用不等于“巡检完成后释放” |

证据根目录：`C:/workspace/DEAIOS/lab/rg-two-dog-patrol-20261110`，ThinkPad 同名 lab 目录。
关键文件：`tests-r2.json`、`source-r2-manifest.json`、`dog1-switch-result.json`、
`dog2-switch-result.json`、`central-after.json`、`software-preflight-result.json`、
`camera-contract-summary.json`、`scene-mcp-dog1.json`、`scene-mcp-dog2.json`、
`stop-preflight.json`、`thor-only-final-dog1.json`、`thor-only-final-dog2.json`。
旧 stop-preflight 中狗1缺 task.delegate 已由后续切换证据更新，但开机后的原生委派当前仍未就绪；
其余停止/恢复缺口仍成立。各轮记录须保留原请求/计划/证据，不将模板充作执行记录。

## 只有 Thor 上电时继续的实验

| 顺序 | 实验 | 成功标准与边界 |
| --- | --- | --- |
| 1 | 双节点开机登记、能力与 State 时效 | 原身份/epoch正确，Thor在线与底盘不可用分别显示；不把旧姿态续鲜 |
| 2 | 真实 MI 的双物理 Actor 分工草稿 | 固定分工与两个独立执行者约束正确；证据不足明确澄清，保持未批准 |
| 3 | 相同请求重传与中央恢复 | 返回原请求/计划/回执，无新的运动委派；服务重启另按当轮范围确认 |
| 4 | 相机数据接力辅助回归 | 沿既有接线核验原摘要字节、来源与结果依赖；不冒称 Robonix 巡检拍照链路 |
| 5 | 隔离软件环境的占用、Unknown、停止/恢复反例 | 缺适用停止证据不释放或重派，已完成结果保留；不注入现场故障 |

先补第1项所需的原生 Scene、到达和原生拍照回执合同，再申请当轮底盘运动验证。
只验证规划/读取或纯软件进程，不能宣布真机巡检、门口清空或故障接替通过。

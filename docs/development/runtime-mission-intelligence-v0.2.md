# 运行期 Mission Intelligence 工作 v0.2

设计：[ADR-0121](../decisions/0121-durable-runtime-mission-intelligence-work.md)、
[ADR-0123](../decisions/0123-runtime-mi-authorized-work-discovery.md)。
此切片补齐原 fault → durable MI work → candidate → 独立实际 Review → 原安全切换。
原 Group-scoped Memory 通过[显式 v0.3 许可](runtime-plan-revision-v0.1.md)
接入新组；[非空许可的实际组合验收](acceptance/2026-10-21-runtime-mi-scoped-memory.md)
已验证通用文件路径。完整 F8/跨阶段矩阵继续验收，没有总体完成声明。

## Controller 的原工作许可

先按 [v0.1](runtime-plan-revision-v0.1.md) 安装实际 Mission 的有界 policy 和独立原目标
gate，再用 v0.2 `authorize-generation` 显式授权一次。原 policy、触发源、次数与总期限不变。
缺预审替代时，真实获准故障先固定 source revision/Group/全部原 attempts，持久 hold、
typed stop 与原 Memory revoke，并消耗一个 candidate ticket 和 switch budget。
完整原停止/撤销齐备后进入 Generating；没有候选或 Review 时保持 hold，超时进入 Manual。
原执行、普通 resume/recovery、新绑定或新 Execute 均不能绕过这个 pending fence。

生成前，实际 proposer 用 `claim-runtime-work` 冻结原 MI Request、SQL instance UUID、work
digest 与完整实现/配置 identity。更换空库不能领取旧 ticket。每次实际 provider I/O 前，
SQL 先保存原 request 和独立 nonce，再经 `admit-provider-call` 消耗唯一的
`provider-generate` 或 `provider-review` 许可。Controller 只接受该 phase 的原 nonce；
恢复较早 SQLite 副本不能开始第二次调用。许可与实际调用完成是不同证据。

实际候选仍保持原 goal/Actors/Context 安全合同和已满足 Task，未完成 Task 必须使用此前
未使用的 ID。独立实际 recovery-admin 必须对 exact digest Review；配置中的 reviewer
credential 是部署者的明确授权，模型 advice 不会授予它。原独立 goal producer 仍保留
Mission 满足权，不能由新 DAG 完成或模型批准替代。

## MI worker 与原始 SQL

`mission-runtime-replanner --config <profile> --once` 执行一次原 Request 轮询；默认轮询
周期 1 秒，范围 250ms–60s。v0.1 保留至多 128 个显式 Request IDs；v0.2 必须显式
配置 discovery，从实际 Controller 只读队列按证书主体和 tenant 找当前 Generating ticket，
再经 Mission Service 的唯一 Mission 索引/tenant JOIN 定位原 Request。每页至多 32 项，
每轮至多四页；较小扫描预算保留无权威的内存游标，下一轮继续。授权过期、源版本变化或
已退出票据阶段的记录不进入队列；消费仍核对全部原权限和预算。
服务、proposer 与 reviewer
使用固定 mTLS origin/证书，精确单 tenant，拒绝 redirect；reviewer 可以省略，此时仅
保留语义 advice 并等待实际人工 Review。worker 不创建 Mission，不读取 Node inventory，
不授予自己权限，不接触 Execute。配置没有任何设备、Actor 到 Node 的固定替身。

worker 从真实 Request API 和 Controller 的原 admission receipt 校对 exact sent bytes、
原 Mission/Group，再读取已提交的 generation ticket。原 user/grounding、原 current export、
原权限和余量、代码/配置/Catalog/Prompt/依赖摘要在 provider 前持久化。传给模型的新增
runtime context 包含逻辑计划版本、fault、真实 completed/used Task IDs 和原 goal context。
存在显式 scoped Memory 声明时使用 context v0.2，保留完整原 child 许可与目标组；这些声明
不证明 import、当前可用或物理事实。SQL 原 context 与当前 Controller 声明不一致时拒绝新
工作；不得修改未知 provider 输入、追加许可后覆盖原输出，或据此选择 Node。
计划含 Memory requirements 时，显式配置 MissionPlan v0.9 及其 canonical schema。
[ADR-0128](../decisions/0128-explicit-memory-plan-provider-dto.md)保留全部 requirements，
不允许旧版本输出删除它们；默认 v0.8 保持。satisfaction freshness 必须来自部署配置，
模型不能省略该 policy 或自行选择 max age。
完整原 stop/attempt/owner/session 证据保留在应用审计，不能变成模型的 Node 选择权。

Controller 的历史 normalized export 对 execution-report 省略 nullable `verifier`。
worker 的明确兼容读取只补 `verifier: null`，原导出与 authority digest 原样保留，另记
published source digest；不改变其他缺失字段、目标、证据 freshness 或 verifier 权限。
原 grounding 是历史输入；新绑定、State、Memory 与独立原目标证据仍须当前 Control 检查。

SQLite 自带原文件/inode 唯一 writer、原 Linux boot/time namespace 与 CLOCK_BOOTTIME
预算。更换时间域、倒退、SQL 等待、网络排队、重启与重复命令都不能续期。无认证 headers
进入 journal；实际 provider 原 response bytes 在解析前保存，至多 1MiB，超限明确标记
truncated 并拒绝。无效 JSON、HTTP 拒绝和完整原回包均保留；网络不确定结果不重发。

SQL 上界为 1024 个原 work、每 work 16 个 effect、单 document 2MiB、累计证据 64MiB。
耗尽先拒绝新 I/O，不静默删除历史。该 SQL 是独立恢复组件，必须与 Controller 的 claim/
phase receipts 一起保留；丢失或较早副本不授权重做调用，保持 hold 并暴露原不确定性。
完整在线备份/跨主机恢复仍按原 F4/F5 门禁推进。
显式 [Recovery profile v0.3](../../contracts/recovery/bundle-v0.3/README.md)已补实际 worker/
inode 双锁和原格式检查；[联合静止恢复验收](acceptance/2026-10-20-runtime-mi-recovery.md)
逐项核对原 Controller、Node、Request 和 MI work SQL。其恢复不会自动启动或续期。

`--observe-work <work digest>` 只查询原 ticket 下的实际 claim/provider permission/candidate/
Review，查询意图也先记录。独立匹配的观察另存，原 HTTP outcome 保持 uncertain；不重发
原 command、不换 ID、不调用模型。每个原不确定阶段至多两次原查询，并受原 work 期限
与 effect 总数约束。已完整保存的真实 provider response 可以只读复用。

## 配置与证据边界

TOML 使用 `schema_version = "roboguide.runtime-mi-worker/v0.1"`，显式 `state_db`、
`mission_config`、`request_ids` 和可选 `poll_ms`。新 `roboguide.runtime-mi-worker/v0.2`
不填写静态 Request IDs，改为显式 `[discovery] page_size = 32, max_pages = 4`；旧版本
夹带 discovery 会拒绝。两种输入均不改写已有 work 的原 identity 或预算。
`service`、`proposer`、可选 `reviewer`
各自包含 endpoint、tenant、principal、certificate、key、ca。原证书与源配置由部署者
提供；实际服务按 TLS 决定权限，JSON principal 或 tenant header 不能自授 authority。
`mission_config` 使用现有版本化 Catalog/Prompt/Responses 配置并明确启用 Review。

共享 wrapper v44 保留真实 v43 的原 policy/plan/goal/attempt/history 和预算；旧 wrapper
夹带生成权限、claim 或 provider receipt 必须拒绝。Bridge inner v23、codec v28、Node
Contract v0.12/config v0.13/journal v13 保持原义。

进程验收使用实际 mTLS Request/Controller、SQLite、Node 和 owned CPU children；本地
provider 是明确标记的协议 fixture，验证真实 wire、原 I/O journal 和中断恢复，不能被
写成模型效果、设备能力、物理停止或总体 SoftwareComplete 的证据。

自动发现、实际主体/tenant 拒绝与原回包恢复的组合见
[运行期 MI 自动发现验收](acceptance/2026-10-20-runtime-mi-discovery.md)。

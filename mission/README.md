# Mission Intelligence

通过 `[mission] provider_plan_profile = "host-objective-v1"` 显式启用宿主目标组装；
模型草案省略目标字段，MI 从本轮冻结输入带入并完整校验。旧回显模式保持默认。
详见[接入指南](../docs/development/mi-host-objective-v0.1.md)和[ADR-0255](../docs/decisions/0255-host-owned-mission-objective.md)。

`mission/` 负责把用户文本 instruction 经解释、澄清和风险审批转换为版本化 Task Graph，
并生成内部 Mission/Task/Context/Role identity；不负责 Node Assignment、Resource Commit、
Execution Group 或本地设备控制。

已增加可选的姿态目标观察器，运行在现有 `mission-service` 的后台线程中。
它只读取 Controller State、原 Mission 接纳回执和当前执行记录，再向通用 verifier
入口发布证据；不会调用机器人、Pilot、动作重试或任务取消接口。历史
`execution-report` 任务不进入这个队列，原记录保持不变。

开启前必须用单独的 mTLS 叶证书，在 Controller 的
`roboguide.operator-authorization/v0.3` 中授予 `verifier` 角色和明确的租户；
该身份不能提交 Mission、取消任务或更新注册表。Node 必须已经有明确的同租户身份。
选择新的 `integrations/robonix-task/deploy/posture-capability-catalog.json` 作为 MI
能力目录；它在保留 origin/delegate 操作的基础上显式加入目标参数与独立 verifier
合约，现有目录不会静默改变。现有 Mission satisfaction policy 还须显式配置
`policy_ref` 和 `max_evidence_age_ms=5000`，不能由模型自己选择数值。
由管理员预先登记匹配的 `observation.posture.verify-reported@v1` verifier source，
`producer_principals` 精确绑定该服务身份，并允许下述完整谓词。观察器不自授权限。
可从 `integrations/robonix-task/deploy/posture-verifier-registry.template.json`
填入本次唯一 command ID、明确租户和真实 verifier principal 后，由管理员走现有
`POST /v1/verifier-sources` 发布；模板中的占位标识不是现场配置。
服务配置在现有 `[service]` 中增加：

```toml
posture_verifier_enabled = true
posture_verifier_source_id = "mi-reported-posture"
posture_verifier_source_revision = 1
posture_verifier_tenant_id = "local"
verifier_client_cert = "/private/mi-verifier/client.crt"
verifier_client_key = "/private/mi-verifier/client.key"
verifier_client_ca = "/private/mi-verifier/ca.crt"
```

Controller 必须使用 HTTPS；这些路径不能复用现有 MI operator 叶证书。
Worker 的 Controller 请求使用独立的 200 ms 网络超时；配置和队列保存证书摘要，
不保存密钥内容。未启用时无需安装 Robonix 集成包；启用时需要安装该模块或在现有
服务的 `PYTHONPATH` 加入 `integrations/robonix-task`。

新 Task 必须在接纳前声明一个角色的 `execution_intent.parameters.expected_posture`，
取值为 `standing` 或 `down`，并保留部署原有的 `profile` 参数。Task satisfaction
须为 `verifier-evidence`，合约为 `observation.posture.verify-reported@v1`，
`predicate` 与 `expected_effect` 均精确等于
`fresh post-completion Robonix aggregate reports standing` 或对应的 `down`，
最大证据年龄不超过 5000 ms。观察器只检查既有计划，拒绝不一致的声明，不在审批后
改写计划。模型生成的普通执行回执任务不会自动升级为目标验收任务。

队列在唯一一次 Mission POST 前持久化原计划摘要和实际请求正文摘要。
观察器核对 Controller 原接纳正文、原 Group、计划修订号和同租户身份；仅在 Task
处于 `AwaitingSatisfaction` 且完整、无歧义的当前角色 attempt 均已 Completed 后，
记录基线并启动固定 **10 秒**窗口。当前版本仅接纳单角色目标；发现历史 attempt
导致当前集合有歧义，或计划已修订时，保留 Unknown 并转人工。

窗口内需要两条不同 sequence、不同原生接收身份、同来源且同 epoch 的新鲜报告。
两条都达到目标时发布正证据；窗口结束且最后两条仍新鲜、均与目标矛盾时发布负证据。
缺失、过期、来源变化或时钟域变化保留 Unknown。缓存重复查询不能计数或续期。
网络读取、队列等待和发布预算会消耗原始有效期；SQL 等待过久时拒绝发布。
丢失发布回复后保留原 evidence ID、完整请求和不确定状态，转人工只读对账，
不会重发旧测量来刷新 Controller 的接收时间，也不会重试动作。

观察工作、原基线、两条报告、原 attempt 集合、递增发布序号和原回执保存在同一个
MI `state_db` 的版本化 `mission_posture_*` 表中，并校验输入与进度摘要。
服务重启沿原 boot/time namespace 的单调时间继续；重启不能重置窗口。
`GET /v1/mission-requests/{request_id}/posture-verification` 提供同请求所有者可读的
观察记录。`complete` 表示证据已发布并记录回执，真实 Task 的结案仍由 Controller
原状态机决定；Robonix 报告姿态满足也不等于独立物理效果或停止证明。

软件进程验收入口为
`integrations/robonix-task/tests/posture_verifier_process_gate.py`，使用私有 mTLS
Controller/Node 和合成状态源，保留正、负验收及权限拒绝证据。负证据下 Task 为
`SatisfactionRejected`，Mission 为 `Failed`。该检查不连接真机，
也不代替 MI 模型规划或现场动作验收。

## 目录

- `src/mission/`：Mission Request 状态机、resolved GroundedIntent handoff、SQLite store、
  Controller client、合同值、Responses Interpreter/Planner、HTTP API 和 CLI；
- `prompts/v0/`：独立版本化的 Interpreter、Planner 与 Reviewer Prompt；
- `tests/`：合同、配置、安全边界和 Fake Responses 的离线测试；
- `../contracts/mission/v0.6/`：Python 与 Rust 共同遵守、含 Context/ContextRole、role
  execution intent、Task/Context resource scope、typed execution relations、coupling mode、
  selective Group shared view、peer channel descriptor、quantitative resource sizing 和
  relative Task timing，以及显式 Task satisfaction evidence policy 的当前 MissionPlan Schema；
  v0.3-v0.5 继续作为兼容输入；
- `../contracts/mission/request-v0.1/` 与 `inventory-v0.1/`：文本请求状态投影（可恢复嵌入
  v0.3-v0.6、当前输出 v0.6）和只读
  规划预检快照；旧 `v0/` 继续保留，避免静默改写已版本化合同。

模型配置位于 `config/mission.toml`，服务/risk policy 位于 `config/mission-service.toml`，
凭据只从 `OPENAI_API_KEY` 读取。远程明文 HTTP
默认拒绝；持续联调应使用 HTTPS 或通过 SSH 映射到 localhost。

服务可用 `service.execution_profile_path` 选择启动时冻结的
`roboguide.execution-profile/v0.1` 部署配置。它按 canonical operation 声明 resource
kind/units 最低需求，作为同一份 planning input 传给 Planner、Reviewer 和 Repairer；
Planner/Repairer 输出在进入草稿、审批和提交链之前，由 MI 确定性补齐最低资源需求，
保留更高需求、Task DAG、Actor、Context 和 resource scope。未配置时行为不变。
配置不包含 Node、ResourceId 或 PhysicalEntityId，也不读取 live inventory。

服务也可用 `service.planning_profile_path` 选择启动时冻结的
`roboguide.deployment-planning-profile/v0.2` 部署能力摘要。它只包含抽象 capability class、
Catalog 已定义的 typed facts，以及可选的中性 operation state-effect 声明，供 Planner、Reviewer
和 Repairer 了解部署能力和同一 Actor 任务组合的潜在状态交互；
class id 不是 Actor、Node、Resource 或 PhysicalEntity，profile 也不包含 health、lease、
reservation 或当前可用性。缺少任务/世界证据时，MI 不得仅凭 profile 选择 capability constraint；
effect 声明只用于 Review 发现潜在交互，不证明终态谓词会失效，也不选择物理实体。缺少或
partial 声明是未知，不是无副作用。v0.1 仍按原义兼容读取，不会被补造 effect 信息。profile
不能替代 environment-authoritative 的 episode floor 或 start-state evidence。

Habitat 部署还可以通过 `service.grounding_planning_world_evidence_path` 提供
`roboguide.authoritative-planning-world-evidence/v0.1` 静态场景证据。共享世界在执行已有的
唯一一次 reset 后发布 v0.2：除静态事实外，仅在 Habitat 官方几何谓词的当前实体位置和
阈值均可读、且保守毫米舍入仍成立时，记录一个终态谓词的单位置 witness。该 witness 只
证明 reset 快照中一个参与者**精确位于**具名目标中心时多个谓词可以同时成立；它不证明
导航路径、后续物体位置、Task completion 或官方终态成功。两版均不选择 Node 或物理机器人，
v0.2 中的起点仍保留在 Control 部署证据而不进入 MI。文件缺失或损坏时，Grounding
Context 保留 acquisition gap，不把未知事实转换成猜测的楼层、可达性或执行器约束；文件存在
时会选择 grounding-context v0.3，并原样传给 Planner、Reviewer 和 Repairer。关系事实只允许
当前版本声明的 `same_floor`/`different_floor`，且端点必须来自精确环境映射。对象实例
若只有模板路径而没有与 episode label 精确一致的静态句柄，就只能记录 gap；不会把模板变成
目标实例。正式 B1 provenance 同时校验 v0.3 Context、adapter 文件和 frozen workload identity；
历史 v0.2 Context 仍按原协议验证。

[shared-world profile](../scenarios/e1-shared-world-episode-51/execution-profile.json)
把 `mobility.move@v1` / `mobility.navigate@v1` 的 endpoint 独占需求表示为现有 `space:1`。
两份 Node 配置分别发布容量为 1 的 `habitat-navigation-slot-a/b`，并为两个 operation
声明对应 `required_resources`。Control 仍独占 Match/Commit/Bind 和资源释放权限；
Node local lock 仅保留为本地防线。这里的 units 是所选独占资源的最低容量，不能解释成
可分割配额。部署者必须保持 profile 与 Node 配置一致；该 profile 仅适用于此执行环境。
它不推导 benchmark goal、不增加 distinct-executor 约束，也不要求所有任务并发：
顺序任务仍可在资源释放后复用同一个 endpoint。当前 shared-world coordinator 要等待两个
endpoint assignment 才启动，是 Local EAIOS 的执行方式，不是通用单机器人任务语义。

Planner 与 Repairer 的每次 Provider 请求把当前 Mission Request 的 `mission.id` 限定在
请求专属的 strict output schema 中；独立的本地身份校验仍会拒绝不匹配的返回值。若模型
越过 schema 返回了错误身份，MI 不自动改写 ID、不触发草案再生、也不提交 Control，
而是将有界的原始/归一化草案及完整内容 digest 绑定在内部 rejected-draft v0.3 观测
证据中。传输和鉴权错误没有模型草案，仍只记录失败信息；既有 v0.1/v0.2 证据可继续恢复。

```bash
uv sync --dev
uv run mission-service
uv run mission validate \
  --input scenarios/mvp-slice-v0.1/mission-plan.json
uv run pytest -q
```

真实模型解释/规划会产生费用并访问外部网络，必须显式运行 `mission-service` 或
`mission plan`。正常单元测试
只使用 Fixture 和 Fake Transport，不访问模型服务。

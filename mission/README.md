# Mission Intelligence

`mission/` 负责把用户文本 instruction 经解释、澄清和风险审批转换为版本化 Task Graph，
并生成内部 Mission/Task/Context/Role identity；不负责 Node Assignment、Resource Commit、
Execution Group 或本地设备控制。

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
`roboguide.deployment-planning-profile/v0.1` 部署能力摘要。它只包含抽象 capability class
及 Catalog 已定义的 typed facts，供 Planner、Reviewer 和 Repairer 了解部署能够提供的能力类别；
class id 不是 Actor、Node、Resource 或 PhysicalEntity，profile 也不包含 health、lease、
reservation 或当前可用性。缺少任务/世界证据时，MI 不得仅凭 profile 选择 capability constraint；
profile 不能替代 environment-authoritative 的 episode floor 或 start-state evidence。

Habitat 部署还可以通过 `service.grounding_planning_world_evidence_path` 提供
`roboguide.authoritative-planning-world-evidence/v0.1`。该文件只能包含 reset 前由部署环境
读取的静态场景事实及其 dataset identity；它不调用 reset、不采样机器人起点、不选择 Node，
缺失的起点会保留为 `agent_start_state_pending_reset` gap。文件缺失或损坏时，Grounding
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

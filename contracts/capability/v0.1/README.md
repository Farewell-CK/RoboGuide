# Canonical Capability Catalog v0.1

`catalog.json` 是 Mission Intelligence 使用的版本化 RoboGuide 语义词汇表。它列出当前可由
Planner 引用的 exact canonical contracts，并为每个 contract 定义非厂商语义说明与 scalar
parameter schema。

该 Catalog 与 live Node Inventory 严格分离：

- Catalog 回答“这是不是 RoboGuide 当前认识的语义合同”；
- Inventory/Capability Matching 回答“当前部署中谁可以执行”；
- Catalog 中存在某个 contract 不证明当前存在 provider；
- Node 当前上报某个 contract 也不会动态改写 Planner 的系统语言。

v0.1 只覆盖当前 MissionPlan 已使用的 canonical contract identity 和 scalar parameters。它不
定义 embodiment taxonomy、多 capability requirements、feasibility envelope、Node operation
discovery、Local Skill/API 名称或结构化 semantic objective。现有 MissionPlan 字段仍称
`capability_contract`；Capability Requirement 与 Operation/ExecutionIntent 的进一步分层需要
新的 MissionPlan contract slice。

`catalog.schema.json` 定义 artifact shape；Mission Service 还会执行 canonical identity、重复项、
required/unknown parameter 和 scalar type 的确定性校验。

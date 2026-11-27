# 从源码核对合同版本

Code review: `3c7136d6` · 2026-11-26.

| Contract | Source value |
| --- | --- |
| Federation (explicit resource-bearing execution) | `roboguide.federation-execution/v0.1` |
| Runtime revision (explicit coordination) | `roboguide.runtime-plan-revision/v0.6` |
| Decision policy (explicit active hazards) | `roboguide.decision-policy-registry/v0.3` |
| Resource topology (explicit Control deployment) | `roboguide.resource-topology/v0.1` |
| MissionPlan (configured default) | `roboguide.mission-plan/v0.8` |
| MissionPlan (explicit Memory) | `roboguide.mission-plan/v0.9` |
| MissionPlan (explicit quantitative resources) | `roboguide.mission-plan/v0.10` |
| MissionPlan (explicit scoped typed Memory) | `roboguide.mission-plan/v0.11` |
| Node journal (storage fence) | `v15` |
| Node wire catalog | `v0.17` |
| Node Protocol | `roboguide.node-protocol/v0.4` |
| Node Contract (explicit safety/Memory profile) | `roboguide.node.v0.12` |
| Node Contract (explicit quantitative profile) | `roboguide.node.v0.13` |
| Node Contract (explicit scoped typed Memory profile) | `roboguide.node.v0.14` |
| Node config (latest explicit profile) | `roboguide.node-config/v0.15` |
| Controller checkpoint | `roboguide.controller-checkpoint/v60` |
| Capability Catalog | `roboguide.capability-catalog/v0.3` |

这些是分别协商的合同，不是同一个产品版本。旧 profile 保留原义。发布站点还记录实际构建 SHA、工作区状态和正文核对 SHA。

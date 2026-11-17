# 私有 Node 声明预检反馈 v0.3

`roboguide.plan-validation/v0.3` 是
[v0.2](../execution-planning-v0.2/README.md) 的显式只读扩展，
由 [ADR-0255](../../../docs/decisions/0255-private-node-plan-preflight.md) 定义。
它不改变 execution-planning evidence v0.2、MissionPlan 或 Node 注册合同。

仅当 Controller 显式配置 `ROBOGUIDE_NODE_PREFLIGHT_CONFIGS` JSON 路径数组时返回 v0.3。
v0.2 的全部必需字段、摘要、32 条诊断上限及 `issues_truncated` 保留；新增一个必需字段：

```json
{
  "node_preflight": {
    "scope": "declared-node-configs",
    "source_digest": "sha256:<64 lowercase hex digits>"
  }
}
```

新增 issue code：

| Code | 计划路径 | 含义 |
|---|---|---|
| `node_preflight.operation_unsupported` | `/tasks/i/roles/j/execution_intent/operation` | 所提供的配置没有 exact operation |
| `node_preflight.parameters_incompatible` | `/tasks/i/roles/j/execution_intent/parameters` | 没有 workflow 可读取全部实际参数源 |
| `node_preflight.resources_incompatible` | `/tasks/i/roles/j/requirements/resources` | 同一个参数兼容的 operation 无法覆盖完整资源需求及其私有必需 ID |

私有配置的来源摘要参与反馈 provenance，不替代原 deployment/verifier/plan digest。
回复不含私有身份、候选列表、文件路径或 endpoint。无私有配置时保留原 v0.2，
不添加 null 字段、不推断私有检查已完成。仅 v0.3 可携带新的来源和 code；未知代际、
字段或非法来源仍是系统/部署错误。MI 将合法负反馈保留到现有有界草案恢复中。

`not_blocked` 仅表示所检查的声明未发现冲突。它不保证未来选中相同 Node、当前资源可用、
满足全部能力/placement 限制或物理可执行。真实提交/运行门禁独立保留，查询不写任何权威状态。

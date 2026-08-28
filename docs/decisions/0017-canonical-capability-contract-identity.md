# ADR-0017：Canonical Capability Contract Identity

- 状态：Accepted
- 日期：2026-08-28

## Context

Node Config 和 Node Protocol 使用 `namespace.name@version` 字符串，MissionPlan 使用
`namespace`、`name`、`version` 三个结构化字段。真实双节点实验暴露出两种显示相同但结构
不相等的表示：`("spatial.map", "build", "v0")` 与
`("spatial", "map.build", "v0")` 都会格式化为 `spatial.map.build@v0`，但 Matching 将其
视为不同 capability。

## Decision

Canonical identity 使用最后一个 `.` 分隔 namespace 与 name：namespace 可以包含多个
非空 dot-separated segment，name 必须是单 segment，version 不得包含 `@`。三个部分均不得
包含空白，namespace/name 不得包含 `@`。所有 Rust/Python 构造、serde 恢复、MissionPlan
Schema、Node Config 和 Node Protocol 注册校验共同执行该规则。

`spatial.map.build@v0` 的唯一结构化表示是：

```json
{"namespace":"spatial.map","name":"build","version":"v0"}
```

## Consequences

- 字符串与结构化合同可以无歧义双向转换，Matching 不再依赖生产者如何放置 dot。
- 旧的 dotted `name` MissionPlan 必须修正后重新提交，不做静默迁移。
- 该规则只定义 canonical identity，不冻结 capability taxonomy 或本地 adapter skill 名称。

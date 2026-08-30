# Mission Inventory Contract v0.1

`GET /v1/inventory` 向 Mission Intelligence 提供 Shared Node State 的只读、允许滞后的
规划预检快照。它包含节点 reported health、RoboGuide-observed liveness、当前 capability
availability、canonical contracts 与已注册 resources。

该合同不包含 reservation、allocation、assignment 或 commitment，也不授权 Mission
Intelligence 选择 Node。Control 在 Match/Commit 时仍执行唯一权威资格判断。

[`inventory.schema.json`](inventory.schema.json) 定义 Rust Integration Server 与 Python
Mission Service 共同遵守的 wire shape。

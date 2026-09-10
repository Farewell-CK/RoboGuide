# Mission Inventory Contract v0.1

`GET /v1/inventory` 提供 Shared Node State 的只读、允许滞后的部署视图。它包含节点
reported health、RoboGuide-observed liveness、当前 capability availability、canonical
contracts 与已注册 resources，可用于运维诊断以及未来显式的 deployability preview。

该合同不包含 reservation、allocation、assignment 或 commitment，也不授权 Mission
Intelligence 接纳/拒绝 Mission 或选择 Node。Interpreter 不消费该合同；即使未来提供
deployability preview，其结论也只能是 advisory。Control 在 Match/Commit 时仍执行唯一权威
资格判断。

[`inventory.schema.json`](inventory.schema.json) 定义 Rust Integration Server 与 Python
Mission Service 共同遵守的 wire shape。

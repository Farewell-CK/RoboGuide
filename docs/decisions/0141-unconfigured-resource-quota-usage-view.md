# ADR-0141：显示尚未配置限额的资源承诺

状态：Accepted。

## 背景

ADR-0140 允许在没有 quota limit 的情况下保留 ResourceId 的实际 Control demand，以便以后装入
revisioned policy 时按原 claim 检查。然而 v0.1 运维 view 只遍历已配置 limits；因此真实承诺在
policy 安装前不出现在查询结果中。

## 决定

`QuotaLedger::usage()` 按配置 limits 与现存 claims 的 tenant/category 并集生成稳定只读列表。
没有 claim 的配置行报告 `used_units=0`；仅有 claim 的行仍报告用量，而 `max_units` 和
`remaining_units` 明确为 `null`。查询不修改账本，也不把旧 opaque claim 转成 reservation 证明。

`GET /v1/resource-quotas` 的 view schema 提升为 v0.2。该全局运维路由继续只允许 recovery-admin；
返回的聚合用量不向 tenant operator 开放。物理 reservation、tenant policy 和执行权限边界均不变。

## 验收

Ledger 回归创建一个带 typed ResourceId 证据、但没有任何 limit 的 claim，确认用量可见且上限为空；
零 Node 实际 mTLS Controller 回归核对 v0.2 response、SQLite journal 与重启后同一 policy。
检查不注册 Node、设备或 resource inventory。

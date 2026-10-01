# Mission Service

该 composition root 接收自然语言 Mission Request，持久化澄清、草案与结构化 Review history，
并在无开放问题、确定性校验及语义 Review 通过且满足风险策略后，把完整 MissionPlan 提交给
Integration Server。可修复 Review issue 进入最多两次自动 Repair；需要新用户事实的问题返回
`NeedsClarification`。它不选择 Node、不创建 Group，也不维护权威执行生命周期。

恢复决定在独立 observations v0.2 中按类型、draft/context digest 持久化。提交超时、
非法 receipt 或重启中断后，原 Request 的 `/retry` 只查询原 Mission 一次，保留原错误，
不重新 POST 或调用模型。查询没有 accepted-plan digest，Request 不自动变成 Accepted；
`found`、`not_found` 和 `unavailable` 可从 `/observations` 查看。
若重启前已原子保存完整、身份一致的真实接纳 receipt，只归约原 receipt 恢复 Accepted，
不重新 POST。只读 status observation 与权威执行生命周期分开。
只有明确 Controller 400/409/422 拒绝允许显式重交同一原稿。结果不明的请求禁止通过
Dialogue 改写计划或本地 cancel 假装停止；实际 Mission 使用 Controller 的 cancel API。
认证/配置错误提示 `check_configuration`，identity/review 拒绝提示 `review_input`，不会
进入自动草案修复。见 [ADR-0054](../../docs/decisions/0054-mission-recovery-boundary.md)。

```bash
uv run python apps/mission-service/main.py \
  --mission-config config/mission.toml \
  --service-config config/mission-service.toml
```

默认监听 `127.0.0.1:8070`。真实模型调用需要按 `config/mission.toml` 明确启用安全 endpoint
并通过环境变量提供凭据；离线测试不访问模型或真机。

# Mission Service

该 composition root 接收自然语言 Mission Request，持久化澄清/草案状态，并在无开放问题且
满足风险策略后把完整 MissionPlan 提交给 Integration Server。它不选择 Node、不创建 Group，
也不保存 Running/Completed execution state。

```bash
uv run python apps/mission-service/main.py \
  --mission-config config/mission.toml \
  --service-config config/mission-service.toml
```

默认监听 `127.0.0.1:8070`。真实模型调用需要按 `config/mission.toml` 明确启用安全 endpoint
并通过环境变量提供凭据；离线测试不访问模型或真机。

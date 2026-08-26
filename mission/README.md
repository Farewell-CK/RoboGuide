# Mission Intelligence

`mission/` 负责把 Mission ID 与用户 Objective 转换为版本化 Task Graph，不负责
Node Assignment、Resource Commit、Execution Group 或本地设备控制。

## 目录

- `src/mission/`：合同值、配置加载、Fixture Planner、Responses Adapter 和 CLI；
- `prompts/v0/`：独立版本化的 Planner 与 Reviewer Prompt；
- `tests/`：合同、配置、安全边界和 Fake Responses 的离线测试；
- `../contracts/mission/v0.2/`：Python 与 Rust 共同遵守、含 Context/ContextRole、role
  execution intent 和 Task/Context resource scope 的
  MissionPlan v0.1 Schema；旧 `v0/` 继续保留，避免静默改写已版本化合同。

配置位于 `config/mission.toml`，凭据只从 `OPENAI_API_KEY` 读取。远程明文 HTTP
默认拒绝；持续联调应使用 HTTPS 或通过 SSH 映射到 localhost。

```bash
uv sync --dev
uv run mission validate \
  --input scenarios/mvp-slice-v0.1/mission-plan.json
uv run pytest -q
```

真实模型规划会产生费用并访问外部网络，必须显式运行 `mission plan`。正常单元测试
只使用 Fixture 和 Fake Transport，不访问模型服务。

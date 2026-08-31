# Mission Intelligence

`mission/` 负责把用户文本 instruction 经解释、澄清和风险审批转换为版本化 Task Graph，
并生成内部 Mission/Task/Context/Role identity；不负责 Node Assignment、Resource Commit、
Execution Group 或本地设备控制。

## 目录

- `src/mission/`：Mission Request 状态机、resolved GroundedIntent handoff、SQLite store、
  Controller client、合同值、Responses Interpreter/Planner、HTTP API 和 CLI；
- `prompts/v0/`：独立版本化的 Interpreter、Planner 与 Reviewer Prompt；
- `tests/`：合同、配置、安全边界和 Fake Responses 的离线测试；
- `../contracts/mission/v0.2/`：Python 与 Rust 共同遵守、含 Context/ContextRole、role
  execution intent 和 Task/Context resource scope 的 MissionPlan v0.2 Schema；
- `../contracts/mission/request-v0.1/` 与 `inventory-v0.1/`：文本请求状态投影和只读
  规划预检快照；旧 `v0/` 继续保留，避免静默改写已版本化合同。

模型配置位于 `config/mission.toml`，服务/risk policy 位于 `config/mission-service.toml`，
凭据只从 `OPENAI_API_KEY` 读取。远程明文 HTTP
默认拒绝；持续联调应使用 HTTPS 或通过 SSH 映射到 localhost。

```bash
uv sync --dev
uv run mission-service
uv run mission validate \
  --input scenarios/mvp-slice-v0.1/mission-plan.json
uv run pytest -q
```

真实模型解释/规划会产生费用并访问外部网络，必须显式运行 `mission-service` 或
`mission plan`。正常单元测试
只使用 Fixture 和 Fake Transport，不访问模型服务。

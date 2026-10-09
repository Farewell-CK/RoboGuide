# 快速开始

本页给出在本地构建、测试 RoboGuide 并跑通三条演示路径的最短路径。命令均以仓库根目录为工作目录。

## 前置条件

| 工具 | 版本 | 说明 |
| --- | --- | --- |
| Rust toolchain | 由 `rust-toolchain.toml` 固定 | `cargo` 自动选用 |
| Python | 3.13 | 由 `uv` 管理（`pyproject.toml` 固定 `>=3.13,<3.14`） |
| [uv](https://docs.astral.sh/uv/) | 任意近代版本 | Python 环境与依赖管理 |
| git | —— | 克隆仓库 |

??? note "可选：Misson Intelligence 的模型访问"

    Mission Intelligence 默认配置使用 OpenAI Responses API。API Key 只从环境变量
    `OPENAI_API_KEY` 读取，绝不写入配置文件；远程明文 HTTP 会被安全边界拒绝，
    生产联调应使用 HTTPS 或 localhost 隧道。没有模型 Key 时，下文的
    Rust 演示路径不受影响。

## 构建与测试

```bash
# Rust 核心（11 个 crate + 5 个应用）
cargo build --workspace
cargo test --workspace          # 全部离线确定性测试，无需任何外部服务

# Python（mission / evaluation / 工具）
uv sync --dev
uv run pytest -q                # 同样全部离线
```

质量门禁（与 CI 相同）：

```bash
cargo fmt --all -- --check
cargo clippy --workspace --all-targets --all-features --locked -- -D warnings
uv run ruff format --check mission apps/mission-service tools/quality evaluation
uv run ruff check mission apps/mission-service tools/quality evaluation
uv run mypy --strict mission/src mission/tests apps/mission-service tools/quality
uv run python tools/quality/check_python_function_docs.py mission apps/mission-service
```

## 演示路径 1：控制平面纵向切片（无网络）

`apps/controller` 是最小可执行证据：注册 3 个节点、创建 Mission Group、
匹配 → 调度 → 提案 → 提交 → 绑定、驱动 FakeNode（含故障注入与恢复 rebind）、
直至 Group 进入 `Completed → Released`。

```bash
cargo run -p controller
```

预期输出包含 `DEAIOS control slice produced N events`。全过程使用
`VirtualClock` 与内存 State，不需要任何网络或外部服务。

## 演示路径 2：正式 Node Protocol v0.4 握手

启动 Controller 组合根（gRPC `:50051`、Control HTTP `:8080`、Artifact HTTP `:8090`）：

```bash
cargo run -p integration-server
```

另开终端，用 smoke 探针执行真实的协议握手、注册与心跳：

```bash
cargo run -p real-node-smoke -- --endpoint 127.0.0.1:50051
```

预期输出 `registered node=... session=... lease=... protocol=v0.4 ...`。

`--simulate-execute` 模式额外提交一个单角色合成 Mission 走完整
Controller 派发回路（使用会话唯一能力契约，不会选中已有 Node，不做任何硬件 I/O）：

```bash
cargo run -p real-node-smoke -- --endpoint 127.0.0.1:50051 \
  --control-endpoint http://127.0.0.1:8080 --simulate-execute
```

## 演示路径 3：Mission Intelligence 服务

```bash
export OPENAI_API_KEY=sk-...        # 可选，见上文说明
uv run mission-service              # 默认监听 127.0.0.1:8070
```

提交一条文本指令并观察澄清/草案/审批闭环：

```bash
curl --fail-with-body -X POST http://127.0.0.1:8070/v1/mission-requests \
  -H 'Content-Type: application/json' \
  -d '{"instruction": "让机器人把客厅的杯子拿到厨房"}'
```

返回的 request id 可用于 `GET /v1/mission-requests/{id}` 轮询、
`POST .../{id}/messages` 回答阻塞澄清、`.../approve` 审批草案。
配置位于 `config/mission.toml` 与 `config/mission-service.toml`。

## Eval Harness

评估框架独立于核心代码，先做环境自检再跑实验：

```bash
uv run roboguide-eval doctor --spec evaluation/specs/e1/<spec>.yaml --config evaluation/local.yaml
```

机器相关的 Conda 环境、工作目录、凭据保存在 Git 忽略的 `evaluation/local.yaml`
（模板见 `local.yaml.example`）或 `ROBOGUIDE_EVAL_*` 环境变量。

## 本文档站本地预览

```bash
cd website
uv run python sync_docs.py
uvx --from mkdocs --with-requirements requirements.txt mkdocs serve
```

浏览器打开 <http://localhost:8000>。构建是严格模式：任何内部坏链都会导致构建失败。

## 下一步

- 读[架构导览](architecture-tour.md)理解各层职责与关键语义边界
- 按[模块地图](modules/domain-ports.md)逐个 crate 深入（每页列出关键类型、主流程 API 与测试主题）
- 需要理解某条语义"为什么这样设计"时，查 [ADR 索引](docs/decisions/index.md)

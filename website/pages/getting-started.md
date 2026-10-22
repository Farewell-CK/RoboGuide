# 快速开始

从不需要网络、模型或设备的控制平面演示开始。再验证正式协议，最后按需接入 MI。

## 前置条件

仓库根目录运行命令。Rust 版本由 `rust-toolchain.toml` 固定，Python 由 `pyproject.toml` 固定为 3.13 系列；Python 环境使用 uv。具体锁定版本以仓库文件为准。

```bash
git clone https://github.com/Farewell-CK/RoboGuide.git
cd RoboGuide
cargo build --locked -p controller -p integration-server -p real-node-smoke
```

## 演示路径 1：控制平面纵向切片（无网络）

```bash
cargo run --locked -p controller
```

在本页核对基线中输出 `DEAIOS control slice produced 21 events`。演示使用 FakeNode、VirtualClock 和内存 State，包含注册、承诺、执行和恢复。它证明控制链，不创建真实设备。

## 演示路径 2：正式 Node Protocol v0.4 握手

为本次运行选择未占用的 loopback 端口和新数据目录。以下端口仅用于本地示例；若已占用，修改两端参数。

```bash
mkdir -p /tmp/roboguide-quickstart
cargo run --locked -p integration-server --   127.0.0.1:39151 /tmp/roboguide-quickstart/controller.sqlite3   127.0.0.1:39152 127.0.0.1:39153 /tmp/roboguide-quickstart/artifacts
```

另开终端执行：

```bash
cargo run --locked -p real-node-smoke -- --endpoint http://127.0.0.1:39151   --node-id docs-protocol-smoke
```

成功输出包含 `registered node=docs-protocol-smoke`、session 与 `protocol=v0.4`。这会在独立测试 Controller 上注册一个明确的合成软件 Node，不运行 Local EAIOS、模型或设备。结束后在服务终端按 Ctrl+C；保留该目录作为本次日志，不复用到生产。

**常见失败：**端口占用时换端口；数据库 writer busy 时检查是否仍有自己的旧进程持有，使用新目录；握手拒绝时检查 exact contract/TLS/config，不删除原 SQL history。

## 演示路径 3：Mission Intelligence 服务

这条路径需要有效的模型配置和自己的 Controller；本轮只验证无模型示例，不将它标为已执行的模型验收。

```bash
uv sync --dev --frozen
uv run mission-service
```

检查 `config/mission.toml` 与 `config/mission-service.toml`，按配置通过环境变量提供凭证。在你自己的隔离部署中提交文本请求：

```bash
curl --fail-with-body http://127.0.0.1:8070/v1/mission-requests   -H 'Content-Type: application/json'   -d '{"instruction":"生成一份文件并独立验证内容"}'
```

结果依赖已发布 Catalog、grounding、模型和部署，可能需要澄清或审批，不保证示例语句一定可执行。Request ID 用于原进度查询和回答，不把 uncertain outcome 当成可安全重复提交。

## 质量检查与下一步

按仓库 AGENTS 的维护范围执行 fmt、Clippy、pytest、Ruff、mypy 和函数文档检查。首次接入另读[开发与接入](integration-guide.md)；实验编排见[Eval Harness](modules/evaluation.md)。

## 本文档站本地预览

```bash
cd website
python sync_docs.py
uvx --from mkdocs --with-requirements requirements.txt mkdocs serve -a 127.0.0.1:8001
```

完整构建与检查见[网站维护说明](https://github.com/Farewell-CK/RoboGuide/blob/main/website/README.md)。

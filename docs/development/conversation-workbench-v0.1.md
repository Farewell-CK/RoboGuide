# RG 对话工作台 v0.1

## 实施计划

范围：终端对话入口，支持当前 Controller、当前身份可见的集群概览、节点详情、能力检索、
指定 Mission 及其 Task 状态。复用现有查询接口，不创建 Mission，不改变 Planner 输入、
Control 承诺、Node、存储或恢复合同。决策见 [ADR-0231](../decisions/0231-readonly-conversation-workbench.md)。

1. 冻结会话层的职责、工具范围及证据解释。
2. 实现固定 origin 的 GET 客户端，校验完整响应，保留租户与 mTLS、超时和字节限制。
3. 实现 Responses 自然语言查询路由和有界会话指代；模型只选择闭合查询，事实由程序呈现。
4. 提供 `roboguide chat`、`--ask`、直接查询及会话内帮助、原始结果查看和退出。
5. 完成离线真实 HTTP/CLI 与假模型测试、类型/格式检查和命令安装检查，记录实际验证范围。

入口使用 `roboguide`，避免覆盖 ripgrep 的 `rg`。会话层采用独立 Python namespace，
与现有 Mission 工具共用安装包和 Provider 配置，不进入 MissionRequestEngine。

## 验收标准

- 中文自然语言支持“接入了几个节点”“各节点有什么功能”“第二个节点呢”“哪些支持建图”
  “任务 mission-x 到哪一步了”。明确显示当前查询 scope，不把一个 Controller 的视图称为全 Federation。
- 概览按实际响应计算数量；只保留实际展示的 ID 顺序，指代解析后重新 GET。
- 区分注册能力、可用合同、最近健康、最近可达观测和最终执行资格。时间差只使用 Controller
  同一接收时钟；没有新鲜度策略时不推导当前在线或可调度。能力检索无命中不等于不具备能力。
- 未知对象、403、404、断线、响应过大或格式错误显式返回；不补成空集群或成功。
- 模型无法选择任意 URL、method、shell、文件或写操作；拒绝错误结构和非 completed 响应。
- 闭合 GET 工具尊重原服务端权限；远程连接要求完整 mTLS，拒绝跳转。
- 会话和原始结果只保存在当前进程，有界；无模型直接命令仍可用。
- 不调用真实付费模型、不启动机器人或仿真完成自动测试；模型真实性与真实部署接入单独说明。

## 使用

在仓库根目录安装并进入对话：

```bash
uv sync --dev --frozen
uv run roboguide chat --controller http://127.0.0.1:8080
```

可连续输入“集群接入了几个节点？”、“第二个节点有什么功能？”、“哪些支持建图？”。
查询结果会列出原节点 ID、已声明能力、可用合同、注册资源与最近健康/可达观测。
自然语言由 `config/mission.toml` 中的 Responses 模型解析；`--config` 可指定另一份同格式配置。
凭据使用该配置的 `api_key_env`，不写入会话。沿用既有 Provider HTTPS/显式明文配置规则，
工作台不自动放宽。解析失败可直接使用 `/` 命令，不会换模型或自动重试。

```bash
# 单次自然语言提问
uv run roboguide chat --ask '集群接入了几个节点？'

# 无需模型或模型凭据的直接查询
uv run roboguide nodes
uv run roboguide node node-id
uv run roboguide capabilities 建图
uv run roboguide mission mission-id
uv run roboguide chat --ask '/nodes'
```

交互中支持 `/nodes`、`/node ID`、`/node #2`、`/capabilities 关键词`、`/mission ID`、
`/help`、`/raw`、`/reset`、`/quit`。`/raw` 明确显示最后一次成功查询的**历史**原始响应；
正常追问重新请求服务端。没有找到对象或查询失败时保留上次成功引用，但不将旧状态当作新结果。
模型只收到当前问题、上一问题和有界对象 ID 引用；原始集群响应不发送给模型。

每个子命令都支持连接选项。远程 Controller 要求完整 mTLS：

```bash
uv run roboguide nodes --controller https://controller.example:8443 \
  --ca /path/to/ca.pem --client-cert /path/to/operator.pem \
  --client-key /path/to/operator.key --tenant team-a
```

租户 header 仍由服务端依据客户端身份验证，不授予额外可见性。默认 loopback 入口沿用
原部署的信任模式。工作台只查询一个 Controller；跨域汇总和 Mission 执行不在本版范围。

Controller 默认 socket 超时为 5 秒，`--timeout` 可选择大于 0 且不超过 60 秒；自然语言解析
至多使用配置超时与 60 秒中较小者。读取循环使用原始 monotonic deadline；DNS 仍由宿主
resolver 管理，不宣称硬实时。响应最多 2 MiB，超限拒绝部分统计；最多展示 128 个节点/Task，
计数来自完整有效响应。单个问题最多 4096 字符，序号只解析实际展示过的对象。

直接查询失败和单次提问失败退出码为 2；Ctrl-C 退出码为 130；交互中普通查询失败后可以继续。
会话不跨进程恢复，不自动启动 Controller、Node、Local EAIOS 或模型服务。

## 实施与验证记录 · 2026-11-09

- 已完成独立 Python package、安装入口、四类查询、会话指代、原始响应查看和连接配置。
- 已通过 `uv sync --dev --frozen --offline` 和安装后的 `roboguide --help`。
- 实际本地 HTTP/独立 CLI 验证包括 scope、拒绝跳转、响应上限、截断、超时、断线和禁止写操作；
  Responses 使用明确的假 Provider，不能据此宣称真实模型理解准确率。
- 实际本地 mTLS 验证使用独立 CA/客户端证书：带证书成功，匿名连接拒绝，tenant 原样传递。
- 隔离零节点 Controller 的实际查询通过；证据在 `/tmp/rg-workbench-smoke-373qhm45/`。
  使用现有二进制 SHA-256 `92ee67405675edcbc48de3256bbb29e113382d2de40c467cd5f279e7f334152b`，
  新工作台进程实际返回 0 节点，CLI exit=0。原 Controller 通过 terminate/wait 收尾，exit=-15；
  未重编 Rust，也不将此查询冒充当前生产部署、带节点任务或物理验收。
- `uv run --frozen pytest -o addopts='' -q mission/tests evaluation/tests/test_eval_cli.py
  evaluation/tests/test_eval_config.py --tb=short`：698 passed，92.90 秒。
  之后仅对工作台 JSON/连接收尾/对象引用收紧，最终专项检查另记；不把两组重叠测试相加。
- 最终 `uv run --frozen pytest -o addopts='' -q mission/tests/test_workbench.py
  mission/tests/test_workbench_tls.py --tb=short`：53 passed，3.48 秒，包含安装后的自然语言 CLI
  → 假 Provider HTTP POST → Controller HTTP GET，以及无模型交互会话。
- `mypy --strict mission/src mission/tests`：104 个文件通过；工作台 Ruff format/check、
  全 Mission 函数文档检查和 `git diff --check` 通过。原有 CI 的 Mission 路径自动包含新 package/测试。
- 未跑完整 Rust、全仓 Python 或网站构建/发布；本次未改 Core/Node/恢复合同，验证扩至
  Mission 包、受安装入口影响的 Evaluation CLI/config 和真实安装/HTTP/TLS 边界。
- 真实 Provider 的语言理解与实际带节点部署尚未验收；没有模型费用、机器人或仿真实验。

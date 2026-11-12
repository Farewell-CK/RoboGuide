# RG 对话工作台 v0.2

## 实施计划

1. 增加 Mission Request v0.5 的显式提交意图、等待执行状态和精确版本 execute；旧 v0.4 行为保持。
2. 接入 `/plan`、`/run`、`/execute`、澄清回答与原审批；每条命令先保留原 idempotency key，丢响应只观察。
3. 实现私有会话存储与恢复，绑定 Controller/Mission endpoint、tenant 与证书身份；历史状态不成为当前证据。
4. 实现当前任务列表、有限多步查询与比较、任务跟踪、终端历史/补全和自然语言操作建议。
5. 运行针对性、完整 Mission 及受合同/安装影响的回归；验收旧解码器拒绝与原请求不重复提交。

用户交互：`/plan 目标` 只规划，`/run 目标` 授权按现有策略推进，`/execute` 绑定已展示版本。
自由文本建议展示两个明确选项；选择只规划或开始执行流程后才发出创建请求。
风险审批与执行意图分开；Ctrl-C 停止当前观察，不发送 Mission cancel。

## 使用

Controller 和新版 Mission Service 使用实际部署配置分别运行；工作台不自动启动它们。

```bash
uv run roboguide chat --profile config/workbench.example.toml --session warehouse
```

示例配置为本机默认地址。自行配置 HTTPS/mTLS 时，Controller 与 Mission Service 分别提供
CA/客户端证书/私钥，租户选择仍由服务端授权。CLI 参数优先于 profile，相对路径相对于配置文件。
模型使用 profile 的 `config` 路径（同现有 Mission TOML 格式）；真实调用需要实际 Provider 配置和凭据。

```text
你 > /plan 检查仓库是否有障碍物
RG > 展示澄清问题或完整计划；状态 AwaitingExecution，尚未提交。
你 > /answer 先检查东侧，再检查西侧
RG > 展示新的计划版本。
你 > /execute
RG > 仅授权这个版本；按原策略等待风险审批或提交 Controller。
```

`/run 目标` 明确授权按现有流程推进，无需再确认相同执行意图；风险审批若命中仍独立等待。
自由文本任务先显示建议，`/choose plan` 或 `/choose run` 后才发出创建请求。修改计划的自然语言
同样先建议 `/choose answer`；`/answer 内容` 可直接回答。多个澄清问题用 `/answer-to 问题ID 内容`。
`/approve` 绑定展示版本且需要原 approver 权限；`/execute` 只需要 submitter 权限，不替代审批。

恢复会话：

```bash
uv run roboguide chat --profile config/workbench.example.toml --resume warehouse
```

恢复只加载历史和对象引用；执行选择不跨进程继承，先 `/request` 重新展示计划后才能 `/execute`。
执行前还会核对服务端版本；有变化就展示新版本并停止这次提交。服务端再次原子检查 revision/digest，
客户端核对不能替代服务端检查。仅规划的原模式、版本授权和历史均在 Mission SQLite 保存。

## 查询和跟踪

| 命令 | 行为 |
| --- | --- |
| `/nodes`、`/node ID`、`/node #2` | 新读取节点与能力，序号绑定实际展示过的节点 |
| `/capabilities 建图` | 根据已返回的能力/合同检索 |
| `/compare` | 新读取上轮 1–4 个节点，比较共同合同和各自其它合同 |
| `/missions`、`/mission #2` | 查询当前身份可见任务，再按实际展示顺序选定 |
| `/explain ID` | 最多三次只读请求：任务状态、当前 Group 绑定、原恢复评估 |
| `/watch [ID]` | 交互中每 2 秒查询选定任务，最多 150 次；终态或 Ctrl-C 停止本地观察 |
| `/request [ID]` | 新读取已知 Request，包括当前问题、计划和提交意图 |
| `/raw`、`/history` | 查看本进程最近原始结果或最近会话历史；历史不代表当前状态 |
| `/reset` | 清除当前选择，保留命令回执和历史 |

终端支持 Tab 补全与当前进程的方向键历史。自然语言仍通过结构化路由选定有限工具；
组合诊断展示可核对的记录，不通过模型推断缺失因果，也不把多接口响应称为同一原子快照。
本版没有任意 agent 工具循环、运行任务取消、机器人直接命令或跨域自动汇总。

## 会话与响应丢失

默认会话目录 `var/workbench/sessions/`，目录 0700、文件 0600；用 `--session-dir` 可显式选择。
只允许一个进程持有同一会话，恢复要求 endpoint、tenant、CA 与客户端证书摘要一致。
证书轮换后不自动迁移旧命令 scope，应按原身份记录对账。私钥与模型凭据不进入会话文件。

本地最多保留最近 40 条消息和 8 MiB 会话文件；所有已记录命令不自动裁剪。达到上限时拒绝新命令。
每个写操作在 HTTP 前 fsync 原 body、digest、key 和 pending 状态；模型/网络超时不代表服务端未执行。
`/observe` 观察同一原命令；若服务端保留 pending 且执行已经结束，`/reconcile` 仅归约原命令日志。
两者都不重新调用原操作。仍在执行、记录缺失或权限不可用时保留 pending，不换 ID 重试。
已接纳 Mission 的执行问题仍走 Controller 原恢复规则，不由工作台替换为新的任务。

## 验证记录 · 2026-11-09

- 新版 HTTP/SQLite 专项覆盖只规划、编辑/重启、版本拒绝、独立审批、回包丢失、发送前持久化故障、
  scope/文件权限、真实 CLI 恢复及任务查询/跟踪。使用假模型，不提供真实语言理解准确率。
- 从固定前驱 `d68efc080a3c35a74e6acc70adcd12470c13f0e2` 载入真实旧 API/record decoder，验证
  新 create 字段和 v0.5 存量被拒绝；浅克隆缺前驱时这两项明确 skip，不能替代实际旧版证据。
- 真实零节点组合证据：`/tmp/rg-workbench-v2-native-q1b1vhgd/`。运行安装后的 CLI、真实 Mission
  HTTP/SQLite 和原 Controller 二进制（SHA-256 `92ee67405675edcbc48de3256bbb29e113382d2de40c467cd5f279e7f334152b`）。
  使用确定性 Planner fixture；仅规划后 Controller 为 0 Mission，恢复会话并 execute 后为 1 Mission，
  始终 0 Node。Controller terminate/wait exit=-15；不宣称机器人执行、物理停止或当前源码重编验收。
- 组合实现的全仓 Python：`uv run --frozen pytest -o addopts='' -q --tb=short`，
  **3710 passed / 578.75 秒**，日志 `/tmp/rg-workbench-v2-pytest.log`。
- 之后完成精确授权恢复校验、会话字段校验及安装 CLI/旧 API 测试补强，最终
  `uv run --frozen pytest -o addopts='' -q mission/tests --tb=short`：
  **693 passed / 103.82 秒**，日志 `/tmp/rg-workbench-v2-mission-final.log`。
  两组测试有重叠，不相加；最终小改没有重新运行不受影响的整仓检查。
- strict mypy：111 个 Mission 源码/测试文件通过；17 个受影响 Python 文件的 Ruff format/check
  通过，全 Mission 函数文档检查、v0.5 JSON Schema 语法与 `git diff --check` 通过。
- 未改变 Rust Core/Node 合同，未重跑 Rust 全套或部署服务；本次没有网站构建/发布。
  真实 Provider 语言理解、实际带节点执行和物理结果未验收，不能由上述软件测试替代。

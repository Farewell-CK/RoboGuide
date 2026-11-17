# Chat 任务上下文与工具协作验收 · 2026-11-17

基线：`13fa1df84920848326b4d46e150962224f7e5bbb`。
实现分支：`feat/chat-task-context-20261117`。
责任与语义：ADR-0256；改动限于 Chat 上下文、工具交接与验收工具。

## 已实现的行为

- 任务原文先于模型 I/O 持久化，已显示的助手追问才可成为上下文。
- `clarify_task` 自然追问；普通历史截短或会话恢复不丢失待补充任务与限制。
- 模型的原文起点提示不能裁掉宿主保留的目标；否定、条件、疑问或转述执行词组
  不解除原有只规划限制。明确新任务使用独立上下文；原文不完整时拒绝任务副作用。
- 本轮真实且完整读取的 Mission 身份可用于取消；用户明确指定的 Request 可先读后改。
  隐式目标漂移与未展示版本的执行/审批继续拒绝，原权限与取消预览照常检查。
- 同一任务的错误模式纠正后解除对应未完成状态，独立失败仍保留。同一任务重复 start
  复用首个实际回执，不更换原命令或覆盖 Unknown。

## 软件回归

最终相关测试：676 passed，0 failures/errors/skips，106.301 秒。其中：

- 新增连续任务回归：29 项。
- 门禁时间判断：8 项。
- 其余为原 Workbench 与提交意图回归。

Ruff、format、严格 mypy（67 个文件）、函数文档检查与 git diff --check 通过。
新 wheel 独立安装后，31 个 Workbench 模块与受测源码逐字节一致。

## 真实模型任务对话

使用用户明确提供的 Provider 与 `gpt-6.1-sol`，推理配置为 low。对话工具调用来自真实模型，
未脚本化替换。MI HTTP 路由、Request 引擎和 SQLite 是本地实际软件实现；Interpreter、
Planner 和 Controller Submitter 为明确的软件夹具。

| 轮次 | 用户目标 | 实际结果 | 累计 MI 命令 / 软件提交 |
| --- | --- | --- | --- |
| 1 | 先只规划交付测试，缺参数先问 | 自然追问，未创建占位 Request | 0 / 0 |
| 2 | 补充合同与交接路线，暂时不要开始执行 | 一个 draft-only Request，原始限制完整保留 | 1 / 0 |
| 3 | 保持原方案、补充请求原文，并查运行任务 | 原 Request 更新，运行 Mission 查询也有回答 | 2 / 0 |
| 4 | 明确执行已完整展示的计划 | 原版本正常提交，软件 Submitter 收到一次计划 | 3 / 1 |
| 5 | 只查询当前 Request 状态 | 返回实际 Accepted，无重复提交 | 3 / 1 |

五轮均 complete，共 12 次实际模型调用。该结果证明此软件场景中的连续对话与工具协作，
不证明夹具 Planner 的语义能力、真实设备执行、物理目标满足或长期可靠性。

## 安装后的真实终端

独立 wheel、实际 PTY、真实 Provider 推理与合成 Controller/MI facade 的门禁通过：

- 22 次真实模型调用，17 次合成 Controller 读取、0 次 Controller 写入。
- 普通聊天不读 Controller；节点追问、运行任务、设备 State 和缺失数据处理通过。
- 自然追问巡检区域后，再用完整原文创建一个仅规划的 fixture Request。
- 待发消息查看/撤回、恰好一次处理、恢复、反馈导出与 CLI 的 SSE 接收显示通过。

此门禁通过本地记录代理向 CLI 输出 SSE；其上游实际 Provider 请求为非流式 JSON。
不得仅据该项声称已测得上游原生 SSE 的首字延迟或完整真实规划/执行。

另做原生 API 探针，直接发送 `stream=true`，两次真实调用均返回 `text/event-stream`。
Agent 完成一次实际软件 Mission 查询，0 个任务命令、0 次软件提交，最终回复 complete。
没有观测到公开文本增量，因此只验证了完整 SSE 协议兼容，尚未验证原生增量与首字延迟收益。
首次要求必须出现文本增量的探针失败原件保留在 `native-stream/`；后续诊断单独保存在
`native-stream-diagnostic/`，其 `native_incremental_verified=false`，没有改写首次结论。

## 保留的失败与时间边界

首次真实终端门禁停在 State 查询：原数据有效期为 5 秒，实际模型轮次耗时约 12 秒，
产品按原始有效期正确显示已过期；门禁错误要求仍展示原值。失败原件保留。

修正后的门禁按实际 State GET 时钟判断：及时消费必须显示完整当前值，模型延迟超过
有效期必须拒绝旧值。8 项判断测试覆盖过早拒绝、把过期值当当前值、缺失字段等反例。
没有延长产品 TTL、重新标记旧数据或伪造设备上报。后续独立门禁通过，实测该 State
在显示时已超过 6 秒，正确拒绝为当前值；直接 State 命令仍验证有效新数据的完整展示。

启动前另有两次参数/HTTP 显式授权校验失败，发生在 Provider 调用前；日志单独保留，
不计入成功的真实模型任务轮次。初始软件验收与最终验收分开保存，不合并为成功率。

## 证据与交付边界

本机原始证据目录：`/tmp/rg-chat-task-context-20261117-final/`。

- `combined-regressions.xml` / `.log`：最终 676 项回归。
- `task-flow-final.xml`：29 项连续任务回归。
- `gate-checks-final.xml`：8 项门禁判断。
- `pty-gate-checked/summary.json`：最终安装后软件夹具验收。
- `real-model-gate/`：首次真实终端失败原件。
- `real-model-gate-checked/summary.json`：修正判断后的独立真实终端验收。
- `real-task-flow-attempt2/summary.json` 与 `provider-public.json`：五轮真实模型任务对话。
- `native-stream-diagnostic/summary.json`：原生 SSE 兼容与未测得文本增量的边界。
- `verification.json`：最终提交、测试统计、受测包/模块哈希及证据引用。

密钥经测试进程输入与环境传递，不写入配置、仓库或报告。未连接现场 Controller、Node
或机器人。本轮没有授权或证明现场部署与实机验收，后续应作为独立阶段保留证据。

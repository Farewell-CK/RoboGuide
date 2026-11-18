# 2026-11-04 取消结案软件验收

本次在独立 worktree 与分支 `codex/task-cancellation-reconciliation` 实现
[ADR-0232](../../decisions/0232-task-cancellation-settlement.md)。基线为 `7894bd6f`。
没有修改主对话工作区，没有连接、重启或操作现场 ThinkPad、Thor 与机器狗。

## 已实现的范围

- 原 Task 的持久身份、正向未发送依据，或原 turn interrupted 且完整事件中没有计划，
  可以产生显式 no-effects State v0.4。已发出计划、未知、来源不符或缺 generation 不报告。
- 原 Controller session、generation、取消回执、Cancelled 终态与新鲜 State 联合校验后，
  仅正常 Mission 取消路径可以消费结案结果并释放占用。恢复、续跑和物理交接仍要求
  原有停止证明。结案不写入 StopConfirmed 或 confirmed_at。
- 新管理员接口要求 recovery-admin 和实际认证 subject，校验原 stop record 摘要与代次，
  保存核验内容及原始证据，只结束指定 Mission 的取消。请求体不能提供认证身份。
- 同一原命令和同一主体重传只返回原回执，不保存新 checkpoint、不刷新时间或触发释放。
  冲突请求、越权、跨租户和缺证据均拒绝。SQLite 保存失败回滚 SQL，内存权威保持原值。
- 完整保留原物理停止记录、原 deadline、失败和人工处理历史。管理员结案创建独立记录。
  新嵌套合同由闭合解码器拒绝旧 writer，外层 v60 保持不变，历史 wrapper 降级拒绝新权威。

## 隔离进程闭环

检查入口为
[real_process_gate.py](../../../integrations/robonix-task/tests/real_process_gate.py)。
它启动真实 Controller 和通用 Node，使用隔离的 Fake Atlas/Pilot 与真实 loopback gRPC/HTTP。
夹具没有设备 provider，所有提交都停在软件夹具内。管理员场景使用实际临时 CA、客户端
证书和 recovery-admin 授权。证书和密钥仅在私有测试目录，不加入仓库或验收报告。

| 场景 | 原生接纳 / 取消 | Mission 与资源 | 重启 | 旧 writer |
| --- | --- | --- | --- | --- |
| 声明 no-effects 的自动取消 | 各 1 次 | Cancelled，0 项占用 | Controller 冷重启保持原结果，无重放 | 原基线可执行文件拒绝，SQL dump 不变 |
| 缺声明进入人工状态后正式结案 | 各 1 次 | 人工前保留占用，结案后 Cancelled、0 项占用 | 原证据及结案保持，无重放 | 原基线可执行文件拒绝，SQL dump 不变 |

两项均保留 `physical_stop_confirmed=false`。旧 Controller 由 `7894bd6f` 的原始源码独立
编译，实际启动读取新 SQL 时报告 unknown field settlement，未启动新的 Node。
它的拒绝与数据库不变均由实际进程和 SQL dump 核对，未用新解码器模拟旧 writer。

正常 Mission cancel 是本次整体收尾入口。单独 execution cancel 只停止该次执行，
不会隐式取消包含它的整个 Mission；管理员结案也要求 Mission 已显式进入 Cancelling。

## 检查结果

Windows 的 31 项接入协议测试通过，包含实际 gRPC/HTTP、进程中断、派发前取消、
原事件与身份保持，以及 32 KiB State 导出容量。
WSL Rust 1.98 的 Domain 62 项、Integration Server 311 项、Node Service 140 项测试通过。
Workspace all-targets/all-features Clippy、Ruff、格式、函数文档及生产模块 strict mypy 通过。
仓库工具链声明仍为 Rust 1.99.1，未为通过检查改写。

隔离进程二进制摘要、实际 HTTP、冷重启及旧 writer 拒绝日志由实验目录保存。
开发期的失败记录另行保留：首轮误用 execution cancel 等待整个 Mission Cancelled，
随后改用明确的 Mission cancel；管理员开发开关没有认证 subject，随后改用真实 mTLS
授权测试。没有为这些失败放松产品权限或更改 execution cancel 的原语义。

## 尚未验证与部署条件

本次完成的是原任务未产生执行副作用的取消和人工结案的软件闭环。
运动任务仍需要 Robonix 的原导航实例最终终止、设备状态及本机交接判断。
已有 Executor root Canceled 和取消 ACK 不能替代该依据，未知场景继续保留占用。

本次未对现场旧任务执行结案，也未重新查询现场现状。部署前需要在同一受检版本上更新 Controller、
Node、Connector 与声明，并准备有实际身份的管理员认证。新增声明只作用于后续取消，
不能补写原任务；原任务应按原身份、摘要和现场证据进行正式对账。
随后先进行无动作取消验证，再进入低速导航和实际运动取消验收。

## 独立审阅后的迟到终态修复

`2a2fe596` 的独立本地审阅发现一个边界缺陷。原 no-effects State 在停止期限内到达，
但 Cancelled 终态恰好在截止时间或之后到达时，tick 直接调用结案校验并返回错误。
生产 timer/Node fact driver 会把它作为共享权威故障，关闭 Controller 写入并要求重启。
三项新增回归在未修复代码上复现该错误，包含启用实际 write-failure fence 的生产 timer
和两个真实 gRPC 参与者的同批事实处理。

修复在 no-effects 自动结案入口检查终态的原 receive time，只接受
`requested_at <= terminal_received_at < deadline`。迟到终态继续保存在 Runtime，
原停止目的沿既有 expire 路径转为 ManualEscalation，保留原期限与资源占用。
窗口内到达的证据即使稍后归约，仍按原接收时间判断。严格结案校验保持原义。

新增三项回归分别覆盖原截止时间边界、生产 timer 写入可用性，以及两个真实 gRPC
参与者的同批事实处理。后者核对迟到取消的原接收时间保留、另一任务完成且收到 ACK、
仅迟到任务保留占用，以及全局写入仍可用。夹具明确协商 Node safety contract。
本次修改没有连接或部署现场设备。

修复后在独立构建目录通过 Domain 62 项、Integration Server 314 项、Node Service
140 项，共 516 项 Rust 测试。Windows 的 31 项协议测试及生产模块 strict mypy 通过。
更新后的 Controller 二进制再次通过自动取消、管理员结案、冷重启和旧 writer 拒绝的
实际软件进程门禁。原始结果与源码摘要保存在工作区实验目录
`lab/rg-task-cancellation-late-terminal-fix-20261104/`。

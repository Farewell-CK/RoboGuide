# 2026-11-09 实验分支整合验收

目标是把已审阅的 RG 实验增量汇入 `codex/rg-hot-enrollment-maintenance`。不纳入用户
暂缓处理的手机探针，不修改独立 guide-aios 仓库，不更新 main 或任何现场部署。

## 后续增量 d247e03c

实验线在前次合并后新增 `d247e03c`：纯 reported-posture 目标评估器、原证据剩余预算
以及 Task satisfaction 的隔离进程验收。维护线以 `c0f34db8` 为父合并该提交，保留原历史。
解决 AGENTS/V2 索引冲突，并将目标评估的重复 ADR-0240 调整为 ADR-0241；已有姿态
State ADR-0240、在线身份采用 ADR-0239 保持原编号。

两侧都修复了原生时效窗口，合并后统一为原来源合同的整数 `stale_ms`（1–60000），
继续检查 Controller 保留时间和 RG 上限，新增 remaining_valid_for_ms 使用同一最短预算。
维护线只创建新证据文件、unavailable 退出 2 的修复完整保留。

独立重跑验证：86 项集成测试及 54 项 subtests 通过；完整集成目录 Ruff/format、
严格包 mypy（12 个源文件）和函数文档检查通过；posture_goal_process_gate.py 使用
当前热采用版本 Controller/Node，17 项实际软件检查通过，恰好 4 个唯一 Execute。
覆盖目标屏障、负证据拒绝、缓存/离线拒绝、原任务及新 epoch 冷恢复、错误计划/attempt
拒绝、正证据及重复原回执、依赖只执行一次、最终占用归零。

原始输出为 `lab/rg-maintenance-audit-20261109/merge-posture-goal-tests.log` 和
`merge-posture-goal-process.log`；完整私有软件过程在 WSL
`/tmp/rg-merge-posture-goal-20261109/`。Controller/Node 二进制仍为上一轮已受检版本，
本次无 Rust 核心更改。真实 gate 的结果不代表生产姿态 producer 已部署或物理目标已验证。
没有连接 ThinkPad、机器狗或改写历史现场 execution-report 任务。

## 合并范围及冲突

合并父版本为维护线 `07f4586b` 和实验线 `1cec0b46`，保留原提交历史。后者的新增
提交为 `09192dd3`（RG reported chassis posture）和 `1cec0b46`（后续动作实验记录）。
保留热采用、原手机历史文档，以及前后失败/成功的全部实验边界。

- AGENTS.md 合并两侧职责及验收要求，保留热采用已经软件验收的实际状态。
- V2 索引保留两项独立职责，不以一项替换另一项。
- 重复 ADR 编号：在线身份采用继续为 0239；姿态 State 观察调整为 0240，更新所有引用。
- 原姿态记录中的 RG Completed 和未实现物理趴下目标分别保留；本次未重跑物理实验。

## 合并审阅发现及修复

姿态来源有自己的 stale_ms。原读取器只判断缓存时 availability 和 5 秒上限，
Controller 缓存可能跨过更短的数据源窗口仍返回 reported。回归测试先复现该问题，
随后让原 source window 继续消耗 Controller retention，缺失/错误窗口也拒绝。
不增加本机动作、Pilot 任务或 Controller 的通用物理终态校验。

原姿态 CLI 的 --output 会覆盖旧证据文件，unavailable 也正常退出。回归先复现，
再统一到现有 position 读取器规则：只创建新文件，reported 退出 0，unavailable 退出 2。

MI 三项断言原用 read_text 隐式将 CRLF 规范化为 LF，与生产按原字节冻结/读取的
Prompt 不符。在 Windows checkout + WSL 运行时实际失败。本次断言改为核对原 UTF-8
字节解码内容，生产请求、Prompt、摘要和冻结校验均未修改，相关回归及 provenance 通过。

## 验证

- robonix-task：64 项 unittest 通过，含真实 loopback MCP 的固定 state 调用和清理，
  短 source window 过期、未知/外源拒绝，以及原证据保护和 unavailable 退出。
- robonix-task Ruff、format、严格包 mypy 和函数文档检查通过；MI 修改的测试文件同样通过。
- Catalog、Planner、Review、Request Review、Requests、Grounding、Origin Grounding 及
  provenance 的 124 项所选 MI 回归通过。
- Node Service 的 142 项 Rust 库回归通过。
- 两真实 Node 的 host_process_gate.py 通过，两角色正常任务、占用和释放均走原流程。
- check_hot_node_enrollment_processes.py 通过：旧 Node 原连接/任务保留、新 Node 授权后
  入群完成任务、冷恢复，以及真实旧 Controller 对 adopted registry 的拒绝。
- 所有进程验收只使用自有 loopback 进程、软件证书、独立 SQL 和只读夹具。
  当前 Controller/Node 的二进制与上一轮热采用验收 SHA 相同；本次没有 Rust 实现改动。

完整输出保留于工作区 `lab/rg-maintenance-audit-20261109/`：
merge-posture-before-fix.log、merge-integration-tests.log、merge-mi-regressions.log、
merge-mi-regressions-fixed.log、merge-node-regressions.log、merge-host-process.log、
merge-hot-process.log。
前两轮失败输出保留，未用最终通过覆盖原始失败。

## 其他分支处理

取消对账和空间 State 分支已是当前整合线祖先；原 local-eaios-task-delegation 分支
已由 `07f4586b` 合入。因此无需再 cherry-pick 或重复 merge 这些本地分支。

远端 codex/stage2-execution-contract 的两个提交 `72df27c1` / `c80d2cfb` 在
`git cherry origin/main` 中都为 `-`，已有等价补丁，无需为消除分支列表而再次合入。
其他旧实验/应用分支的非祖先状态不证明代码缺失；部分职责、旧 ADR 编号与当前实现
重叠。未逐项验收的独立方向维持原样，不批量导入，也不删除原分支。

本次维护线的真实合并不等同于 main 的交付验收。main 的全量差异与检查仍须责任人
审阅；现场版本与主分支整合分别管理。

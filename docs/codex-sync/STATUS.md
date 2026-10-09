# Codex 实验状态交接

更新时间：2026-10-09（Asia/Shanghai）

本文档面向通过 GitHub 接续 RoboGuide 实验工作的协作者。只记录可由代码、Git 状态或实验产物验证的公开信息；不包含凭据、API 地址、私有服务器路径或聊天原文。

## 当前实验目标

当前主目标是验证 RoboGuide 的故障处理能力，具体限定为：执行中的已分配 Node 丢失后，系统能否保守地标记物理执行歧义，阻塞受影响的任务，保留未受影响的上下文，经由 `Match -> Schedule -> Propose -> Commit -> Rebind` 选择备用 Node，并继续执行至官方 COHERENT 目标成立。

当前阶段只研究单个 Node 的执行前故障。它不证明任意物理机器人故障、动作 exactly-once、持物状态迁移、同时多 Role 故障或跨全部 COHERENT 任务泛化。

## Git 状态

- 当前命名实验分支：`codex/e2-coherent-gpt6-sol`
- 本次同步文档提交前的实验代码基线 HEAD：`fda825d33cf8882f0018b7b7003f3ce150d7000c`
- HEAD 主题：Recovery pilot 先执行 F1 preflight
- 远程分支 `origin/codex/e2-coherent-gpt6-sol` 已核对为同一个 `fda825d`。
- 服务器本地分支原先位于 `95420574050608180e17d8394f875440a45c5873`；在显式 fetch 并验证本地分支为远程分支严格祖先后，已通过 `git merge --ff-only` 安全同步到 `fda825d`，没有 force push 或覆盖提交。
- 当前分支原有未跟踪文件：
  - `tools/e2-full-task17/mission-gpt-5.6-luna.toml`
  - `tools/e2-full-task17/mission-luna.toml`
  - `tools/e2-full-task17/mission-terra.toml`
- 本次获准提交的文件仅限：
  - `docs/codex-sync/STATUS.md`
  - `docs/codex-sync/DECISIONS.md`

本次仅提交和推送上述两份同步文档的操作已经获得人工确认。不得上传会话原始记录，也不得把三个无关 TOML 纳入提交。

## 已完成工作

### 当前命名分支已经包含

- E2-S0 COHERENT 物理受控接入。
- E2-S1 `env4/task17` 图环境受控接入。
- `gpt-6-sol` 的 task17 E2-Full 闭环工具。
- 通用 rolling-horizon runner：根据当前官方局部观察和合法动作列表，让 Mission Intelligence 生成一个动作 MissionPlan，经 Controller、正式 Node workflow 和原始 `Get_env_info.step` 执行。
- 固定的 10 任务抽样清单 `tools/e2-generic/sample-10-seed-20260929.json`。
- 多任务 DAG runner 和串行 DAG policy；
- 目标完成后的动作防护与证据闭环；
- fair/informed prompt ablation pilot；
- E2 Node-loss 故障注入 runtime；
- F0 clean / F1 pre-effect Node-loss 六次 pilot runner、manifest 和自动 verdict；
- 故障时间线、图快照、Controller attempts、standby Node 日志和校验和证据。

恢复 harness 不修改 RoboGuide Core、Controller、Scheduler、Node 或 COHERENT adapter 的恢复语义。它在第 7 个 primitive 请求进入 COHERENT 前阻断请求，终止当前绑定的 Dog Node，并注册声明相同 operation 的 standby Node。

## 最新实验事实

最新已完成批次：`task17-pilot6-fda825d-20261009T103000Z`

- 模型：`gpt-6.1-sol`
- 任务：官方 `env4/task17`
- prompt profile：`fair`
- DAG profile：`serial`
- 故障位置：完成 6 个 primitive 后，在第 7 个请求进入 COHERENT 前注入 Node loss
- 运行顺序：F1 / F0 / F0 / F1 / F1 / F0
- F0 clean：3 次中 2 次任务成功，成功率 `2/3`
- F1 Node-loss：3 次中 0 次任务成功，成功率 `0/3`

F1 的逐次事实：

1. 第一次在到达故障注入点前发生规划失败，`injection_valid=false`。
2. 第二、三次成功在预定边界终止主 Dog Node，并成功注册 standby Node；旧执行 Attempt 变为 `Unknown`，但 Controller 保持 `Running`，没有形成可验证的 Candidate Match、Commit 和 Rebind，任务停在 6 个 primitive。

因此当前证据只证明故障注入器能够稳定杀死主 Node、保存故障前图状态并注册 standby；尚未证明 RoboGuide 的端到端恢复链路已经成功。

## 本次核验和测试

在 commit `fda825d` 上执行了以下选定离线测试；该提交现已是当前命名分支 HEAD：

```text
PYTHONPATH=integrations/coherent-local-eaios:tools/e2-generic \
.venv/bin/python -m pytest -q \
  tools/e2-recovery/test_fault_runtime.py \
  tools/e2-generic/test_dag_policy.py \
  tools/e2-generic/test_goal_guard.py \
  tools/e2-generic/test_prompt_ablation.py \
  tools/e2-generic/test_run_dag_evidence.py
```

当前命名分支同步到 `fda825d` 后使用以上命令复测，实际结果：22 项选定测试通过。

已保留的环境失败：此前未设置 `PYTHONPATH` 时，两个模块在收集阶段无法导入 `coherent_local_eaios`。本次同步后首次复测时，非交互 SSH 环境没有 `python` 命令；随后使用系统 `python3`（3.10）又因缺少 `tomllib` 导致两个模块收集失败。改用项目 `.venv/bin/python` 并补齐明确的 `PYTHONPATH` 后，22 项测试全部通过。这些失败属于解释器/环境选择问题，不是 Recovery 测试失败，也不能从记录中省略。

本次同步文件为文档变更。提交前已执行 `git diff --cached --check`、`git status --short --branch`、完整 staged diff 审查和敏感字段扫描：空白检查通过，暂存区只有两份同步文档，敏感字段扫描没有命中；三个无关 TOML 仍保持未跟踪状态。

## 当前阻塞与已知问题

1. 两次有效 F1 注入中，主 Node 丢失和 standby 注册均成功，但没有观察到完整 Recovery candidate、Proposal、Commit、Rebind 事件链。
2. clean 对照只有 `2/3` 成功；一次规划失败说明在线模型波动会干扰 Recovery 因果判断。
3. F1 pilot 的 standby 仍声明同一个 COHERENT agent identity `24`。当前结果最多研究 Node failover，不能表述为不同物理机器狗的接替。
4. 当前正式证据只有 `env4/task17`，不能外推到不同环境和任务。
5. 当前恢复实现只覆盖单个不可用 Role，不是多 Role 联合恢复。
6. 当前分支的三个未跟踪模型配置文件来源和保留策略尚未确认，不能擅自纳入或删除。

## 下一步计划

1. 从两次有效 F1 证据中定位 Controller 保持 `Running` 的原因，检查 route loss、lease/liveness、`RecoveryRequired`、Blocked 状态和应用 timer 是否按预期推进。
2. 增加自动 verifier，要求完整观察到 `RecoveryRequired -> Match -> Proposal -> Commit -> Rebind -> new attempt`，仅最终 goal pass 不足以判定 Recovery PASS。
3. 先使用冻结的受控计划或稳定的规划产物完成 F0/F1 机制验证，避免在线 LLM 规划波动掩盖恢复结果。
4. task17 Pilot 通过后，扩展到 5 个跨环境任务；只有 Pilot 稳定后再冻结 15--20 个任务、每个任务重复 3 次的正式集合。
5. 单独决定后续研究的是“备用 Node 控制同一物理 Dog”还是“不同 PhysicalEntity Dog-B 接替”。后者需要独立实体状态、Actor/PhysicalEntity 迁移和不能继承持物状态的实验规则。

## 后续维护规则

每次完成有意义的代码修改或实验阶段后，更新本文档并至少记录：

- 更新时所在分支和已检查 HEAD SHA；
- 修改文件及实际实现内容；
- 实际执行的测试命令和结果；
- 失败、无效运行和已知问题；
- 尚未提交的工作区变更；
- 下一步计划。

结果必须来自 Git、自动 verdict 或原始实验日志。不得根据聊天记忆推测完成状态，也不得把凭据、私有路径、API 地址或完整聊天记录写入仓库。

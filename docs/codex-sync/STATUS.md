# Codex 实验状态交接

更新时间：2026-10-09（Asia/Shanghai）

本文档面向通过 GitHub 接续 RoboGuide 实验工作的协作者。只记录可由代码、Git 状态或实验产物验证的公开信息；不包含凭据、API 地址、私有服务器路径或聊天原文。

## 当前实验目标

当前主目标是验证 RoboGuide 的故障处理能力，具体限定为：执行中的已分配 Node 丢失后，系统能否保守地标记物理执行歧义，等待可信停止事实，保留未受影响的上下文，经由 `Match -> Schedule -> Propose -> Commit -> Rebind` 形成新 Attempt，并继续执行至官方 COHERENT 目标成立。

当前阶段只研究单个 Node 的执行前故障。它不证明任意物理机器人故障、动作 exactly-once、持物状态迁移、同时多 Role 故障或跨全部 COHERENT 任务泛化。

## Git 状态

- 当前命名实验分支：`codex/e2-coherent-gpt6-sol`
- 本次核验的实验代码基线 HEAD：`1d01ef75840484278b8abf2b56577af25f1c489a`
- HEAD 主题：`feat: complete confirmed-stop E2 recovery`
- 远程分支 `origin/codex/e2-coherent-gpt6-sol` 已核对为同一个 `1d01ef7`，ahead/behind 为 `0/0`。
- 同步前本地 HEAD 为文档提交 `7f5a0bd398843868871a21c78d90e8f498983485`。显式 fetch 后确认它是远程 HEAD 的祖先，再通过 `git merge --ff-only` 快进到 `1d01ef7`；没有 force push，也没有创建新 Merge Commit。
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
- 故障时间线、图快照、Controller attempts、重启 Node 日志和校验和证据。
- 显式 `/recover` 授权、可信 `Cancelled` 停止事实、恢复预算、same-owner Matching、Commit、Rebind 和新 physical Attempt。
- COHERENT generic bridge 的执行前可取消窗口，以及以 Controller Attempt identity 区分本地幂等句柄。

当前恢复 harness 在第 7 个 primitive 已被 Local EAIOS 接收、但尚未产生图效果时终止绑定 Node，显式提交有界恢复授权，重启同一个 Node identity，并等待旧 Attempt 的真实 `Cancelled` 后验证新 Attempt。它不直接执行或编辑 primitive，但当前实现已经依赖 `1d01ef7` 新增的 Core、Controller、Node workflow 与 COHERENT adapter confirmed-stop 语义。

## 最新实验事实

历史批次：`task17-pilot6-fda825d-20261009T103000Z`

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

同步后发现一份基于 `1d01ef7` 的新单次真实运行：`dog-a-recovery-1d01ef7-20261009T171500Z`。证据清单校验全部通过，实际结果如下：

- `injection_valid=true`，`infrastructure_ok=true`；故障发生在完成 6 个 primitive 后。
- 实际故障目标是 `agent 25` 四旋翼的 `[land_on]`，不是运行目录和部分说明文字所称的 Dog-A。
- 原 Controller Attempt 先为 `Unknown`；恢复授权返回 HTTP 202 时仍为 `AwaitingStop`，没有把回执当停止证明。
- 重启同一 Node identity 后，旧本地执行在产生图效果前报告 `Cancelled`；Controller 随后形成新 Attempt `r07-2`，完成 Rebind，并成功执行原 `[land_on]` primitive。
- 该次运行因此证明了一次 COHERENT pre-effect、same-owner confirmed-stop 恢复链路实际跑通。
- 整体任务仍失败：第二规划段的 Provider 请求在约 600 秒后超时，官方目标只满足 2/3，`task_success=false`、`full_fault_run_success=false`。

这不能写成“RoboGuide 已成功完成故障任务”，也不能证明备用 Node、不同物理 Dog 接替、post-effect 歧义或跨任务泛化。

## 本次核验和测试

在 commit `1d01ef7` 上执行了以下针对性检查：

```text
PYTHONPATH=integrations/coherent-local-eaios:tools/e2-generic \
.venv/bin/python -m pytest -q \
  tools/e2-generic/test_recovery_boundary.py \
  tools/e2-recovery/test_fault_runtime.py

cargo test -p runtime recovery
cargo test -p integration-server recovery

.venv/bin/python tools/quality/check_group_continuation_processes.py \
  --output <fresh-temporary-directory>
```

- Python：5 项通过，0 失败。
- Runtime：17 项恢复相关测试通过，0 失败；另有 27 项被过滤。
- Integration Server：35 项恢复相关测试通过，0 失败；另有 76 项被过滤。
- 进程级检查：2 个 case 通过，使用真实 Controller 与两个真实 Node、合成 Local EAIOS；0 次 Provider 调用、0 次 simulator reset。该结果不是 COHERENT 物理实验成绩。

最初通过非交互 SSH 调用 `uv`、`cargo` 时因工具路径不在 `PATH` 而未进入测试；改用项目 `.venv/bin/python` 和服务器 Rust 工具链的显式路径后获得以上有效结果。两个额外的 Rust 名称过滤命令各匹配 0 项测试，不计入通过数。

## 当前阻塞与已知问题

1. 新代码已经解决旧 Pilot 中“只有 `Unknown`、Controller 保持 `Running`”的代码与协议阻塞，但只有 1 次真实 COHERENT 链路观测，尚无重复稳定性。
2. 新运行的恢复链路成功，整体任务却因后续 Provider 超时失败；在线模型波动仍会干扰 Recovery 因果判断。
3. 当前实际验证的是 same-owner Node restart，不是 standby Node，更不是不同 PhysicalEntity 的机器人替换。
4. 运行目录、verdict interpretation、README 和一个测试名仍含 `Dog-A`/`standby` 旧表述；实际故障对象由第 7 个 primitive 决定，本次是 `agent 25` 四旋翼。
5. 当前真实证据仍只有 `env4/task17`，不能外推到不同环境和任务；post-effect 歧义也未验证。
6. 当前分支的三个未跟踪模型配置文件来源和保留策略尚未确认，不能擅自纳入或删除。

## 下一步计划

1. 修正 harness、README、运行命名和 verifier 中硬编码的 `Dog-A`/`standby` 表述，按实际 fault target 生成证据标签。
2. 使用冻结的受控计划完成至少 3 次 same-owner F1 重复，并把“恢复协议链成功”与“官方任务成功”作为两个独立 verdict，避免在线 LLM 超时掩盖机制结果。
3. 在 same-owner Pilot 稳定后，再单独设计备用 Node 或不同 PhysicalEntity 接替；后者需要独立实体状态、Actor/PhysicalEntity 迁移和不能继承持物状态的实验规则。
4. task17 Pilot 通过后扩展到 5 个跨环境任务；只有 Pilot 稳定后再冻结 15--20 个任务、每个任务重复 3 次的正式集合。

## 后续维护规则

每次完成有意义的代码修改或实验阶段后，更新本文档并至少记录：

- 更新时所在分支和已检查 HEAD SHA；
- 修改文件及实际实现内容；
- 实际执行的测试命令和结果；
- 失败、无效运行和已知问题；
- 尚未提交的工作区变更；
- 下一步计划。

结果必须来自 Git、自动 verdict 或原始实验日志。不得根据聊天记忆推测完成状态，也不得把凭据、私有路径、API 地址或完整聊天记录写入仓库。

## 2026-10-09 第一阶段同步实现

第一阶段实现位于独立工作树的 `codex/e2-live-status-sync` 分支，基于远程实验分支 `f44e84857867665d5daabb59598f9e407f1a4e12`。它没有修改 RoboGuide Core、Recovery harness、实验配置或既有结果，也没有触碰主实验工作树中的三个未跟踪 TOML。

新增内容：

- `docs/codex-sync/LIVE_STATUS.md`：从机器证据生成的公开实时摘要；
- `tools/codex-sync/render_live_status.py`：只读取 verdict、timeline 和 provenance 的白名单字段；
- `tools/codex-sync/record_active_run.py`：在仓库外原子记录外部 supervisor 声明的运行状态；
- `tools/codex-sync/publish-live-status.sh`：在独立工作树中执行双 fetch、目标 SHA 比较和普通非强制 push；
- `tools/codex-sync/test_render_live_status.py`：覆盖 Recovery/Task 结果分离、无效注入和 active-run 状态校验；
- `tools/codex-sync/README.md`：说明工作树、调用时机和安全边界。

首次真实汇总读取 7 份完成的 `fault-verdict.json`：Task Success 为 `2`，Task Failure 为 `5`；故障运行的 Recovery Protocol 为 `PASS=1`、`FAIL=2`、`NOT_EVALUABLE=1`。最新运行的实际故障目标仍是 `agent 25` 四旋翼，Recovery Protocol 为 `PASS`，Task Success 为 `FAIL`，官方目标为 `2/3`。历史 F1 `0/3` 没有被改写。

针对性验证结果：

```text
python -m pytest -q tools/codex-sync/test_render_live_status.py
bash -n tools/codex-sync/publish-live-status.sh
python -m py_compile tools/codex-sync/render_live_status.py
```

- Python 测试：4 项通过，0 失败；
- Bash 语法检查：通过；
- Python 编译检查：通过；
- 真实结果渲染：成功生成 7 次运行的汇总，未复制原始日志或 Provider 响应。

尚未完成：自动 watcher/timer 尚未安装，active-run 状态也尚未接入正在修改的 Recovery harness。这样做是为了避免在正式计时区间引入额外进程或 Git/network 操作。待当前 harness 修改稳定后，只能由外层 supervisor 在启动前、关键状态边界或 verdict/checksum 完成后调用，不得在动作或 LLM 计时区间内调用。
